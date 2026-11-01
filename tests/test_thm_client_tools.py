"""thm-agent: the tools that give the agent what 1.x read from files -- history, recipe, check,
frames, guide, the command table -- plus modes, sessions, uploads and the wrap-up. Driven
through Starlette's TestClient; no network, no ComfyUI."""

import io
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from thm_core import MediaOrigin, adopt_media
from thm_core.commands import COMMANDS
from thm_core.slug import TOKEN_RE as CORE_TOKEN_RE
from thm_server import create_app
from thm_server_fixtures import make_tiny_mp4, seeded_world
from test_thm_server_jobs import completing_launcher, failing_launcher

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
import cli  # noqa: E402
import thm_client  # noqa: E402
from thm_client import ThmClient, ThmError, check_tokens, prompt_diff, unknown_params  # noqa: E402

BASE = "http://thm.test"


def _transport(client: TestClient):
    def send(method, url, body, headers):
        assert url.startswith(BASE)
        r = client.request(method, url[len(BASE):], content=body, headers=headers)
        return r.status_code, r.content, dict(r.headers)

    return send


def _user_post(client: TestClient, slug: str, name: str, body: dict) -> dict:
    r = client.post(f"/api/projects/{slug}/commands/{name}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _app(world, tmp_path, launch):
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    return create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": launch, "poll_interval": 0.01})


def varying_launcher():
    """A completing launcher whose every render is a different picture, so two takes into one
    bin are two media records rather than one deduplicated by content hash."""
    n = [0]

    def launch(cmd, cwd, log_path):
        n[0] += 1
        return completing_launcher((20 * n[0] % 255, 90, 50))(cmd, cwd, log_path)

    return launch


def _live_agent(http, world, mode="image_review"):
    _user_post(http, world.slug, "start_session", {"notes": "go", "mode": mode})
    agent = ThmClient(BASE, transport=_transport(http))
    agent.connect(world.slug, "claude-code")
    agent.inbox(world.slug)
    return agent


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")


# -- pure helpers -----------------------------------------------------------------


def test_token_regex_matches_the_core():
    assert thm_client.TOKEN_RE.pattern == CORE_TOKEN_RE.pattern


def test_check_tokens_and_params():
    refs = [{"handle": "mara"}, {"handle": "mara.2"}, {"handle": "band-og"}]
    res = check_tokens(".mara waves at .ghost, .band-og behind; end.next is not a token", "..mara .Mara", refs, True)
    assert res == {"unknown": ["ghost"], "unused": ["mara.2"], "used": ["band-og", "mara"]}
    assert check_tokens(".mara", "", refs, False)["unused"] == []
    assert unknown_params("image", {"seed": 1, "fps": 24, "cfg": 3}) == ["fps"]
    assert unknown_params("video", {"quarter": True, "cfg": 3, "step": 4}) == ["cfg", "step"]
    assert unknown_params(None, {"seed": 1}) == ["seed"] and unknown_params("image", None) == []
    assert prompt_diff("a\nb", "a\nb") == ""
    assert "-b" in prompt_diff("a\nb", "a\nc") and "+c" in prompt_diff("a\nb", "a\nc")


def test_command_table_matches_the_registry():
    assert set(cli.COMMAND_TABLE) == set(COMMANDS)
    base = {"reason", "requested", "quiet", "card"}
    for name, cls in COMMANDS.items():
        assert set(cls.model_fields) - base == {k for k, _ in cli.COMMAND_TABLE[name]}, name
    assert set(cli.SETTABLE_FIELDS) == set(COMMANDS["set_bin_field"].model_fields["field"].annotation.__args__)


# -- history, recipe, check -------------------------------------------------------


def test_history_joins_takes_with_qc_and_diffs_prompts(tmp_path, capsys):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, varying_launcher())) as http:
        agent = _live_agent(http, world, mode="auto")  # a review mode stops at the first take for the verdict
        slug = world.slug
        agent.command(slug, "set_bin_field", {"bin": world.video, "field": "prompt", "value": ".mara at the rail\nwide"})
        first = agent.wait_generation(slug, agent.generate(slug, world.video)["generation"]["id"], poll_s=0.01, timeout_s=10)
        agent.command(slug, "mark_take", {"media": first["outputs"][0], "status": "fail", "note": "changed: nothing. broke: mara faces away"})
        agent.command(slug, "set_bin_field", {"bin": world.video, "field": "prompt", "value": ".mara at the rail, facing camera\nwide"})
        second = agent.wait_generation(slug, agent.generate(slug, world.video, params={"seed": 7, "quarter": True})["generation"]["id"], poll_s=0.01, timeout_s=10)
        agent.command(slug, "mark_take", {"media": second["outputs"][0], "status": "pass", "note": "changed: faces camera. fixed: eyeline"})

        rows = agent.history(slug, world.video)
        assert [r["take"] for r in rows] == [2, 1]
        assert rows[0]["qc"]["status"] == "pass" and rows[0]["seed"] == 7 and rows[0]["quarter"] is True and rows[0]["length_s"] == 5.0
        assert "-.mara at the rail" in rows[0]["prompt_diff"] and "+.mara at the rail, facing camera" in rows[0]["prompt_diff"]
        assert rows[1]["prompt_diff"] == "" and rows[1]["qc"]["note"].startswith("changed: nothing")
        assert agent.history(slug, world.video, limit=1)[0]["generation"] == second["id"]
        assert agent.history(slug, world.image)[0]["take"] == 1  # the seeded take

        # the seeded take, deleted, still shows in the log
        _user_post(http, slug, "delete_media", {"media": world.take})
        assert agent.history(slug, world.image)[0]["media_state"] == "deleted"

        rc = cli.main(["--server", BASE, "--project", slug, "history", "--bin", world.video], transport=_transport(http))
        out = capsys.readouterr().out
        assert rc == 0 and "take 2" in out and "qc=pass" in out and "+.mara at the rail, facing camera" in out and "note: changed: faces camera" in out


def test_history_shows_a_failed_render(tmp_path):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, failing_launcher)) as http:
        agent = _live_agent(http, world)
        gen = agent.wait_generation(world.slug, agent.generate(world.slug, world.image)["generation"]["id"], poll_s=0.01, timeout_s=10)
        assert gen["status"] == "failed"
        rows = agent.history(world.slug, world.image)
        assert rows[0]["status"] == "failed" and rows[0]["media_state"] == "no outputs" and rows[0]["take"] is None


def test_recipe_merges_the_generation_params(tmp_path, capsys):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, varying_launcher())) as http:
        agent = _live_agent(http, world)
        gen = agent.wait_generation(world.slug, agent.generate(world.slug, world.image, params={"steps": 4, "seed": 3})["generation"]["id"], poll_s=0.01, timeout_s=10)
        rec = agent.recipe(world.slug, gen["outputs"][0])
        assert rec["found"] and rec["params"]["steps"] == 4 and rec["params"]["seed"] == 3 and rec["status"] == "done"
        assert agent.recipe(world.slug, world.m1)["found"] is False
        rc = cli.main(["--server", BASE, "--project", world.slug, "recipe", world.m1], transport=_transport(http))
        assert rc == 0 and "no recipe" in capsys.readouterr().out
        rc = cli.main(["--server", BASE, "--project", world.slug, "recipe", gen["outputs"][0]], transport=_transport(http))
        assert rc == 0 and '"steps": 4' in capsys.readouterr().out


def test_check_and_preflight_warn_on_stderr_without_refusing(tmp_path, capsys):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, varying_launcher())) as http:
        agent = _live_agent(http, world)
        slug, send = world.slug, _transport(http)
        rc = cli.main(["--server", BASE, "--project", slug, "cmd", "set_bin_field", "--payload", json.dumps({"bin": world.video, "field": "prompt", "value": ".ghost and .mara"})], transport=send)
        out = capsys.readouterr()
        assert rc == 0 and "warning: unknown_token .ghost" in out.out  # the server's own warning, printed by describe_result
        rc = cli.main(["--server", BASE, "--project", slug, "check", "--bin", world.video], transport=send)
        out = capsys.readouterr().out
        # vid.json is positional (start/end), so an unmentioned handle is not "unused" there
        assert rc == 0 and "unknown .ghost" in out and "unused" not in out and ".mara ordinal=1 start" in out and ".mara.2 ordinal=2 end" in out
        cli.main(["--server", BASE, "--project", slug, "inbox"], transport=send)
        capsys.readouterr()
        rc = cli.main(["--server", BASE, "--project", slug, "generate", "--bin", world.video, "--params", json.dumps({"cfg": 3})], transport=send)
        out = capsys.readouterr()
        assert rc == 0 and "generation g_" in out.out
        assert "warning: unknown_token .ghost" in out.err and "warning: unknown param 'cfg'" in out.err and "video runner" in out.err
        assert agent.jobs(slug)


# -- modes, sessions, wrap-up ---------------------------------------------------------


def test_mode_sessions_and_watch_across_projects(tmp_path, capsys):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, varying_launcher())) as http:
        send = _transport(http)
        slug = world.slug
        rc = cli.main(["--server", BASE, "--json", "sessions"], transport=send)
        rows = json.loads(capsys.readouterr().out)
        assert rc == 0 and rows[0]["slug"] == slug and rows[0]["status"] == "none"

        _user_post(http, slug, "start_session", {"notes": "go"})
        rc = cli.main(["--server", BASE, "--json", "watch", "--poll", "0", "--timeout", "1"], transport=send)
        woke = json.loads(capsys.readouterr().out)
        assert rc == 0 and woke == {"slug": slug, "why": "session waiting", "messages": []}

        agent = ThmClient(BASE, transport=send)
        agent.connect(slug, "claude-code")
        cli.save_state(slug, session=agent.session, cursor=agent.inbox(slug)[1])
        with pytest.raises(SystemExit):
            cli.main(["--server", BASE, "--project", slug, "mode", "later"], transport=send)
        rc = cli.main(["--server", BASE, "--project", slug, "mode", "image_review", "--requested"], transport=send)
        assert rc == 0 and "session mode image_review" in capsys.readouterr().out
        rc = cli.main(["--server", BASE, "sessions"], transport=send)
        assert rc == 0 and "mode=image_review" in capsys.readouterr().out

        with pytest.raises(TimeoutError):
            ThmClient(BASE, transport=send).wait_for_any({slug: cli.load_state(slug)["cursor"]}, poll_s=0, timeout_s=0, sleep=lambda s: None)
        _user_post(http, slug, "post_message", {"text": "try a wider shot", "kind": "user"})
        rc = cli.main(["--server", BASE, "watch", "--poll", "0", "--timeout", "1"], transport=send)
        out = capsys.readouterr().out
        assert rc == 0 and f"project {slug}: 1 new item(s)" in out and "try a wider shot" in out and f"--project {slug} inbox" in out


def test_end_with_summary_posts_before_ending(tmp_path, capsys):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, varying_launcher())) as http:
        agent = _live_agent(http, world)
        cli.save_state(world.slug, session=agent.session)
        rc = cli.main(["--server", BASE, "--project", world.slug, "end", "--summary", "take 2 is the pick; the wide is open", "--attach", world.take], transport=_transport(http))
        assert rc == 0 and "summary posted" in capsys.readouterr().out
        feed = agent.feed(world.slug)["entries"]
        assert [e["message"]["kind"] for e in feed[-2:]] == ["agent", "system"]
        assert feed[-2]["message"]["text"].startswith("take 2 is the pick") and feed[-2]["message"]["attachments"] == [world.take]
        assert feed[-1]["message"]["text"] == "session ended" and cli.load_state(world.slug)["session"] is None


def test_auto_session_end_to_end_owes_no_click(tmp_path):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, varying_launcher())) as http:
        agent = _live_agent(http, world, mode="auto")
        slug = world.slug
        before = agent.feed(slug)["cursor"]
        r = agent.command(slug, "set_bin_field", {"bin": world.video, "field": "prompt", "value": ".mara, wide"})
        gen = agent.wait_generation(slug, agent.generate(slug, world.video)["generation"]["id"], poll_s=0.01, timeout_s=10)
        agent.command(slug, "mark_take", {"media": gen["outputs"][0], "status": "pass", "note": "clean"})
        assert r["change"]["decision"] is None and agent.feed(slug)["cursor"] > before  # lines, not cards
        assert all(e["message"]["kind"] == "system" for e in agent.feed(slug, since=before)["entries"] if e["message"].get("change"))
        assert agent.project(slug)["pending_changes"] == 0
        picked = agent.command(slug, "set_bin_field", {"bin": world.video, "field": "selected", "value": gen["outputs"][0], "card": True})
        assert picked["change"] is None or picked["change"]["decision"] is None  # a no-op if it already led; never a card in auto
        agent.post_message(slug, "done: take 1 is the pick", attachments=gen["outputs"])
        assert agent.feed(slug)["entries"][-1]["message"]["text"].startswith("done:")


# -- upload, frames, guide -----------------------------------------------------------


def test_agent_upload_with_provenance(tmp_path, capsys):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, varying_launcher())) as http:
        agent = _live_agent(http, world, mode="auto")
        cli.save_state(world.slug, session=agent.session)
        silent = tmp_path / "take_silent.png"
        Image.new("RGB", (40, 30), (5, 5, 5)).save(silent)
        rc = cli.main(["--server", BASE, "--project", world.slug, "upload", str(silent), "--bin", world.image, "--from", world.take, "--note", "audio removed, ffmpeg -an"], transport=_transport(http))
        out = capsys.readouterr().out
        assert rc == 0 and "uploaded m_" in out and "Image 1" in out
        detail = agent.bin(world.slug, world.image)
        newest = detail["media"][0]
        assert newest["origin"]["source"] == world.take and newest["origin"]["note"] == "audio removed, ffmpeg -an" and detail["selected"] == newest["id"]
        rc = cli.main(["--server", BASE, "--project", world.slug, "bin", world.image], transport=_transport(http))
        assert rc == 0 and "(audio removed, ffmpeg -an)" in capsys.readouterr().out
        with pytest.raises(ThmError) as refused:
            agent.upload(world.slug, silent, bin=world.image, source="m_nope")
        assert refused.value.code == "unknown_id"


def test_frames_and_guide_over_the_cli(tmp_path, capsys):
    world = seeded_world(tmp_path)
    send = _transport(world.client)
    rc = cli.main(["--server", BASE, "guide", "vid.json"], transport=send)
    out = capsys.readouterr().out
    assert rc == 0 and "prompt guide (default)" in out and "one continuous shot" in out
    assert cli.main(["--server", BASE, "guide", "nope.json"], transport=send) == 1

    video_path = make_tiny_mp4(world.media_dir / "sheet.mp4")
    if video_path is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    project = world.registry.get(world.slug)
    media = adopt_media(project, video_path, origin=MediaOrigin(type="upload", bin=world.mara))
    world.store.save(project)
    world.registry.invalidate(world.slug)
    out_path = tmp_path / "sheet.webp"
    rc = cli.main(["--server", BASE, "--project", world.slug, "frames", media.id, "--out", str(out_path), "--n", "3", "--size", "96"], transport=send)
    assert rc == 0 and "3 frames" in capsys.readouterr().out
    sheet = Image.open(io.BytesIO(out_path.read_bytes()))
    assert sheet.width == 3 * sheet.height


def test_every_call_says_what_it_is_doing_and_status_sets_a_phrase(tmp_path, capsys):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, varying_launcher())) as http:
        agent = _live_agent(http, world, mode="auto")
        cli.save_state(world.slug, session=agent.session, cursor=agent.cursor)
        send = _transport(http)
        slug = world.slug
        assert cli.main(["--server", BASE, "--project", slug, "inbox"], transport=send) == 0
        assert agent.activity(slug)["doing"] == "reading your messages"
        assert cli.main(["--server", BASE, "--project", slug, "cmd", "mark_take", "--payload", json.dumps({"media": world.take, "status": "pass"})], transport=send) == 0
        assert agent.activity(slug)["doing"] == "marking a take"
        assert cli.main(["--server", BASE, "--project", slug, "status", "rewriting the prompt for Video 1"], transport=send) == 0
        assert "status: rewriting the prompt for Video 1" in capsys.readouterr().out
        assert agent.activity(slug)["doing"] == "rewriting the prompt for Video 1"
        # a user-actor call never counts as the agent
        http.get(f"/api/projects/{slug}/bins/{world.video}")
        assert agent.activity(slug)["doing"] == "rewriting the prompt for Video 1"


def test_propose_shots_verb_round_trip(tmp_path, capsys):
    world = seeded_world(tmp_path)
    with TestClient(_app(world, tmp_path, varying_launcher())) as http:
        send = _transport(http)
        slug = world.slug
        agent = _live_agent(http, world, mode="video_review")
        cli.save_state(slug, session=agent.session, cursor=agent.cursor)
        shots = tmp_path / "shots.json"
        shots.write_text(json.dumps({"title": "round 3", "items": [
            {"name": "S01 neon", "description": "hood mount, neon rain", "prompt": ".mara in the rain", "workflow": "vid.json", "references": [world.m1], "length_s": 4, "quarter": True},
            {"name": "S02 desert", "description": "a rabbit past the hood", "workflow": "vid.json", "references": [world.m1]},
            {"bin": world.image, "description": "the same still, again", "workflow": "img.json"},
        ]}), encoding="utf-8")

        # the file is checked before anything is sent
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps([{"name": "x", "description": "y", "size": "full"}]), encoding="utf-8")
        assert cli.main(["--server", BASE, "--project", slug, "propose-shots", "--file", str(bad)], transport=send) == 2
        assert "unknown key(s) size" in capsys.readouterr().err
        bad.write_text(json.dumps([{"name": "x"}]), encoding="utf-8")
        assert cli.main(["--server", BASE, "--project", slug, "propose-shots", "--file", str(bad)], transport=send) == 2
        assert "needs a description" in capsys.readouterr().err

        rc = cli.main(["--server", BASE, "--project", slug, "propose-shots", "--file", str(shots), "--reason", "the ideas you asked for"], transport=send)
        out = capsys.readouterr().out
        assert rc == 0 and out.startswith("proposed 3 shots as 3 cards (c_")
        card_ids = out.split("(")[1].split(")")[0].split(", ")
        assert len(card_ids) == 3
        rc = cli.main(["--server", BASE, "--project", slug, "propose-shots", "--show", card_ids[0]], transport=send)
        out = capsys.readouterr().out
        assert rc == 0 and "shot S01 neon [proposed]" in out and "1 shot(s) on the card" in out

        # one card each: kept with a note, rewritten, closed
        r = http.post(f"/api/projects/{slug}/changes/{card_ids[0]}/decision", json={"action": "approve", "note": "go"})
        assert r.status_code == 200, r.text
        r = http.post(f"/api/projects/{slug}/changes/{card_ids[1]}/decision", json={"action": "approve", "items": [{"index": 0, "description": "a jackrabbit"}]})
        assert r.status_code == 200, r.text
        r = http.post(f"/api/projects/{slug}/changes/{card_ids[2]}/decision", json={"action": "close"})
        assert r.status_code == 200, r.text
        outs = []
        for cid in card_ids:
            assert cli.main(["--server", BASE, "--project", slug, "propose-shots", "--show", cid], transport=send) == 0
            outs.append(capsys.readouterr().out)
        assert "[approved]" in outs[0] and "note: go" in outs[0] and "1. S01 neon: kept -- bin b_" in outs[0] and "render c_" in outs[0]
        assert '1. S02 desert: edited to "a jackrabbit" -- bin b_' in outs[1]
        assert "[closed]" in outs[2] and "1. Image 1: closed, nothing made" in outs[2]

        # the inbox says the decision landed; --wait on a card returns as soon as it is decided
        cli.save_state(slug, cursor=agent.inbox(slug)[1])
        rc = cli.main(["--server", BASE, "--project", slug, "--json", "propose-shots", "--file", str(shots), "--wait", "--poll", "0", "--timeout", "0"], transport=send)
        assert rc == 2 and "still proposed" in capsys.readouterr().err

        # the batch the user asked for out loud lands at once, and reads the same way
        rc = cli.main(["--server", BASE, "--project", slug, "propose-shots", "--items", json.dumps([{"name": "S09", "description": "one more", "workflow": "vid.json"}]), "--requested"], transport=send)
        out = capsys.readouterr().out
        assert rc == 0 and "1. S09: kept -- bin b_" in out and "project version" in out
        assert "propose_shots" in cli.COMMAND_TABLE and cli.DOING["propose-shots"] == "proposing shots"


def test_describe_messages_shows_what_a_note_is_about():
    line = cli.describe_messages([{"ts": "t", "kind": "user", "text": "this take is soft", "context": {"bin": "b_1", "bin_name": "Ed", "media": "m_9", "media_label": "take 3"}}])
    assert line.endswith('[about: bin b_1 "Ed" - media m_9 (take 3)]')
    assert "[about:" not in cli.describe_messages([{"ts": "t", "kind": "user", "text": "hi"}])
