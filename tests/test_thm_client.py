"""thm-agent: the HTTP client and CLI driven through Starlette's TestClient. No network, no ComfyUI."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thm_server import create_app
from thm_server_fixtures import seeded_world
from test_thm_server_jobs import completing_launcher

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
import cli  # noqa: E402
import thm_client  # noqa: E402
from thm_client import ThmClient, ThmError  # noqa: E402

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


def test_client_drives_session_end_to_end(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    app = create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": completing_launcher(), "poll_interval": 0.01})
    slug = world.slug
    with TestClient(app) as http:
        agent = ThmClient(BASE, transport=_transport(http))
        assert agent.health()["ok"] is True
        with pytest.raises(TimeoutError):
            agent.wait_for_session(poll_s=0, timeout_s=0, sleep=lambda s: None)

        _user_post(http, slug, "start_session", {"notes": "two shots of mara, end on a still", "refs_bin": world.mara, "mode": "image_review"})
        detail = agent.wait_for_session(poll_s=0, timeout_s=5, sleep=lambda s: None)
        assert detail["slug"] == slug and detail["agent_session"]["status"] == "waiting"

        with pytest.raises(ThmError) as refused:
            agent.command(slug, "create_bin", {"workflow": "img.json"})
        assert refused.value.code == "no_session" and refused.value.status == 409

        agent.connect(slug, "claude-code")
        assert agent.session == agent.project(slug)["agent_session"]["id"]
        messages, cursor = agent.inbox(slug)
        assert [m["kind"] for m in messages] == ["user", "system", "system"]
        assert messages[0]["text"].startswith("two shots") and messages[0]["attachments"] == [world.m1, world.m2]
        assert cursor == 3 and agent.cursor == 3

        made = agent.command(slug, "create_bin", {"bin_name": "Dawn", "workflow": "img.json", "card": True})
        assert made["change"]["decision"]["status"] == "pending" and made["change"]["target"]["type"] == "project"
        bin_id = next(b["id"] for b in agent.bins(slug) if b["name"] == "Dawn")
        assert agent.project(slug)["bins"][0]["id"] == bin_id  # an empty new bin is the newest thing in the project
        agent.command(slug, "set_bin_field", {"bin": bin_id, "field": "prompt", "value": "@mara.png in the dawn light"})
        bound = agent.command(slug, "bind_reference", {"bin": bin_id, "media": world.m1})
        assert bound["change"]["description"].endswith("bound @mara.png")
        detail = agent.bin(slug, bin_id)
        assert [r["handle"] for r in detail["references"]] == ["mara.png"]
        assert [m["id"] for m in agent.media(slug, bin=world.mara)["items"]] == [world.m2, world.m1]

        queued = agent.generate(slug, bin_id)
        gen = agent.wait_generation(slug, queued["generation"]["id"], poll_s=0.01, timeout_s=10)
        assert gen["status"] == "done" and len(gen["outputs"]) == 1
        take = gen["outputs"][0]
        assert agent.bin(slug, bin_id)["selected"] == take
        assert [j["status"] for j in agent.jobs(slug) if j["job"] == gen["id"]] == ["done"]
        thumb = agent.thumb(slug, take, size="grid")
        assert thumb[:4] == b"RIFF" and b"WEBP" in thumb[:16]
        assert agent.media_file(slug, take)[:8] == b"\x89PNG\r\n\x1a\n"

        marked = agent.command(slug, "mark_take", {"media": take, "status": "pass", "note": "clean"})
        assert marked["change"]["description"].endswith("marked pass")
        agent.post_message(slug, "first shot landed; moving on")
        feed = agent.feed(slug, since=cursor)
        assert feed["entries"][-1]["message"]["text"] == "first shot landed; moving on"
        ended = agent.end(slug)
        assert ended["messages"][0]["text"] == "session ended"
        assert agent.project(slug)["agent_session"]["status"] == "ended"


def test_generate_refused_when_inbox_behind_surfaces_messages(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    app = create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": completing_launcher(), "poll_interval": 0.01})
    slug = world.slug
    with TestClient(app) as http:
        _user_post(http, slug, "start_session", {"notes": "go"})
        agent = ThmClient(BASE, transport=_transport(http))
        agent.connect(slug, "claude-code")
        with pytest.raises(ThmError) as first:
            agent.generate(slug, world.image)
        assert first.value.code == "inbox_cursor_required"
        agent.inbox(slug)
        _user_post(http, slug, "post_message", {"text": "wait, use the other reference"})
        with pytest.raises(ThmError) as behind:
            agent.generate(slug, world.image)
        assert behind.value.code == "inbox_behind"
        assert [m["text"] for m in behind.value.extra["messages"]] == ["wait, use the other reference"]
        assert behind.value.extra["cursor"] == agent.cursor + 1
        messages, _ = agent.inbox(slug)
        assert messages[-1]["text"] == "wait, use the other reference"
        assert agent.generate(slug, world.image)["generation"]["status"] == "queued"


def test_cli_json_output_and_state(tmp_path, monkeypatch, capsys):
    world = seeded_world(tmp_path)
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    send = _transport(world.client)
    slug = world.slug

    assert cli.main(["--server", BASE, "--json", "projects"], transport=send) == 0
    assert [p["slug"] for p in json.loads(capsys.readouterr().out)] == [slug]

    _user_post(world.client, slug, "start_session", {"notes": "notes text", "mode": "image_review"})
    assert cli.main(["--server", BASE, "wait", "--poll", "0", "--timeout", "1"], transport=send) == 0
    out = capsys.readouterr().out
    assert "session as_" in out and "not configured" in out
    assert cli.main(["--server", BASE, "--json", "workflows"], transport=send) == 0
    wfs = json.loads(capsys.readouterr().out)
    assert {w["name"] for w in wfs} == {"img.json", "vid.json"} and all(w["kind"] in ("image", "video") for w in wfs)
    assert cli.main(["--server", BASE, "--project", slug, "connect", "--harness", "claude-code"], transport=send) == 0
    capsys.readouterr()
    assert cli.main(["--server", BASE, "--project", slug, "--json", "inbox"], transport=send) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cursor"] == 3 and cli.load_state(slug)["cursor"] == 3 and cli.load_state(slug)["session"]

    rc = cli.main(["--server", BASE, "--project", slug, "cmd", "set_bin_field", "--card", "--payload", json.dumps({"bin": world.video, "field": "length_s", "value": 8})], transport=send)
    assert rc == 0 and "Video 1 - length 5s to 8s [pending]" in capsys.readouterr().out
    rc = cli.main(["--server", BASE, "--project", slug, "cmd", "set_bin_field", "--payload", json.dumps({"bin": world.video, "field": "tags", "value": ["style"]})], transport=send)
    assert rc == 0
    capsys.readouterr()
    assert cli.main(["--server", BASE, "--project", slug, "bin", world.video], transport=send) == 0
    out = capsys.readouterr().out
    assert "Video 1" in out and "@mara.png" in out and "length_s=8" in out and "tags: style" in out and out.isascii()
    assert cli.main(["--server", BASE, "--project", slug, "project"], transport=send) == 0
    assert "Video 1" in capsys.readouterr().out

    assert cli.main(["--server", BASE, "--project", slug, "cmd", "set_bin_field", "--payload", "not json"], transport=send) == 2
    assert "not JSON" in capsys.readouterr().err
    rc = cli.main(["--server", BASE, "--project", slug, "cmd", "nope"], transport=send)
    err = capsys.readouterr().err
    assert rc == 1 and "unknown_command" in err

    assert cli.main(["--server", BASE, "--project", slug, "blocker", "need the logo"], transport=send) == 0
    assert world.client.get(f"/api/projects/{slug}").json()["blocker"]["text"] == "need the logo"
    assert cli.main(["--server", BASE, "--project", slug, "end"], transport=send) == 0
    assert cli.load_state(slug).get("session") is None


def test_cli_requires_project_for_bin(capsys):
    assert cli.main(["--server", BASE, "bin", "b_1"], transport=lambda *a: (200, b"{}", {})) == 2
    assert "--project" in capsys.readouterr().err


def test_thm_error_from_non_json_body():
    def send(method, url, body, headers):
        return 502, b"<html>bad gateway</html>", {}

    with pytest.raises(ThmError) as exc:
        ThmClient(BASE, transport=send).projects()
    assert exc.value.status == 502 and exc.value.code == "http_error" and "bad gateway" in exc.value.detail
    assert thm_client.urllib_transport is not None
