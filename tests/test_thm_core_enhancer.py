"""Prompt enhancer: system-prompt lookup, graph assembly, output extraction, and the route's gate."""

from __future__ import annotations

import json
import shutil

import pytest

from thm_core import BackupSettings, CoreSettings
from thm_core.config import REPO_ROOT
from thm_core.enhancer import (
    DEFAULT_SYSTEM_PROMPTS,
    SYSTEM_PROMPT_TITLE,
    USER_PROMPT_TITLE,
    build_enhancer_graph,
    build_user_message,
    clean_output,
    enhancer_available,
    extract_text,
    history_error,
    dropped_tags,
    output_problem,
    system_prompt_for,
)
from thm_server_fixtures import seeded_world

REAL_ENHANCER = REPO_ROOT / "workflows" / "prompt_enhancer.json"
REAL_MINIMAX = REPO_ROOT / "workflows_video" / "minimax_h3_r2v_enhancer.json"


def _enhancer_graph():
    return json.loads(REAL_ENHANCER.read_text(encoding="utf-8"))


def test_system_prompt_from_workflow_else_default(tmp_path):
    if REAL_MINIMAX.is_file():
        text, source = system_prompt_for(REAL_MINIMAX, "video")
        assert source == "workflow" and "MiniMax" in text
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps({"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}, "_meta": {"title": "THM-Prompt"}}}), encoding="utf-8")
    assert system_prompt_for(plain, "image") == (DEFAULT_SYSTEM_PROMPTS["image"], "default")
    assert system_prompt_for(plain, "video") == (DEFAULT_SYSTEM_PROMPTS["video"], "default")
    assert system_prompt_for(None, "video") == (DEFAULT_SYSTEM_PROMPTS["video"], "default")
    # an empty carrier node counts as absent
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "  "}, "_meta": {"title": SYSTEM_PROMPT_TITLE}}}), encoding="utf-8")
    assert system_prompt_for(empty, "image")[1] == "default"


def test_defaults_keep_at_tags_rule():
    for kind in ("image", "video"):
        assert "@band-og.mp4" in DEFAULT_SYSTEM_PROMPTS[kind] and "return only the new prompt" in DEFAULT_SYSTEM_PROMPTS[kind]
        assert "formatter, not a writer" in DEFAULT_SYSTEM_PROMPTS[kind]


def test_user_message_shape():
    msg = build_user_message("a dog @ed.png runs", "focus on the face", [("ed", "image"), ("stage", "video")])
    assert "NOTE\n\nfocus on the face" in msg
    assert "(image)" not in msg  # tags are not restated: the model parrots them back
    assert msg.rstrip().endswith("PROMPT\n\na dog @ed.png runs")
    assert "improve the prompt as it stands" in build_user_message("p", "", [])


def test_output_problem_flags_echo_empty_and_dropped_tags():
    sysp = DEFAULT_SYSTEM_PROMPTS["image"]
    assert output_problem("", sysp, "x") == "empty"
    assert output_problem(sysp[:80], sysp, "x") == "echoed the instructions or refused"
    assert output_problem("Wait - you want me to", sysp, "x") == "echoed the instructions or refused"
    # a dropped tag is a warning on a usable rewrite, not a failure
    assert output_problem("a cat on a mat", sysp, "a cat @tom.png on a mat @stage.mp4.2") is None
    assert dropped_tags("a cat on a mat", "a cat @tom.png on a mat @stage.mp4.2") == ["@stage.mp4.2", "@tom.png"]
    assert dropped_tags("a cat @tom.png on a mat @stage.mp4.2, moody", "a cat @tom.png on a mat @stage.mp4.2") == []
    assert output_problem("a cat @tom.png on a mat @stage.mp4.2, moody", sysp, "a cat @tom.png on a mat @stage.mp4.2") is None
    # a mandated boilerplate line carried verbatim from the instructions is not an echo; three are
    mandated = "no added cuts, no subtitles, no watermarks, no on-screen or burned-in text."
    sysp_fmt = "Compile the prompt.\nClose with the negatives paragraph plus:\n" + mandated + "\nThe second rule of the format is here.\nThe third rule of the format is here too.\n"
    assert output_problem("a cat @tom.png on a mat. " + mandated, sysp_fmt, "a cat @tom.png") is None
    assert output_problem(sysp_fmt.replace("Compile the prompt.\n", "a cat @tom.png\n"), sysp_fmt, "a cat @tom.png") == "echoed the instructions or refused"
    # the narrated-input shape cleans down to the rewrite and passes
    narrated = "**User note: focus**\n\n**Original prompt:** portrait of @ed.png\n\n---\n\n**Rewritten prompt:**\n\nClose-up of @ed.png, moody.\n\n---\n\nNote: x"
    assert clean_output(narrated) == "Close-up of @ed.png, moody."
    assert clean_output("**Final Answer**\n\nportrait of @ed.png") == "portrait of @ed.png"
    assert clean_output("*Rewritten prompt:*\n\nPortrait of @ed.png") == "Portrait of @ed.png"
    assert clean_output("Here is the rewritten prompt based on your instructions:\n\nA bass player @mike-og.png") == "A bass player @mike-og.png"
    assert clean_output("You are a prompt rewriter for models").startswith("You are a prompt rewriter")


@pytest.mark.skipif(not REAL_ENHANCER.is_file(), reason="no enhancer workflow checked in")
def test_graph_sets_texts_forces_switch_and_keeps_seed():
    base = _enhancer_graph()
    g1 = build_enhancer_graph(base, "SYS", "USR")
    g2 = build_enhancer_graph(base, "SYS", "USR", seed=7)
    by_title = {n["_meta"]["title"]: n for n in g1.values()}
    assert by_title[SYSTEM_PROMPT_TITLE]["inputs"]["value"] == "SYS"
    assert by_title[USER_PROMPT_TITLE]["inputs"]["value"] == "USR"
    switches = [n for n in g1.values() if n["class_type"] == "ComfySwitchNode"]
    assert switches and all(n["inputs"]["switch"] is True for n in switches)
    seeds1 = [v for n in g1.values() for k, v in n["inputs"].items() if k.endswith("seed")]
    seeds2 = [v for n in g2.values() for k, v in n["inputs"].items() if k.endswith("seed")]
    saved = [v for n in base.values() for k, v in n["inputs"].items() if k.endswith("seed")]
    assert seeds1 == saved and seeds2 == [7] * len(saved)
    # the source graph is untouched
    assert base[[k for k, n in base.items() if n["_meta"]["title"] == USER_PROMPT_TITLE][0]]["inputs"]["value"] != "USR"


def test_graph_refuses_without_inputs():
    with pytest.raises(ValueError):
        build_enhancer_graph({"1": {"class_type": "PreviewAny", "inputs": {}, "_meta": {"title": "x"}}}, "s", "u")


def test_extract_text_prefers_preview_node_and_reads_errors():
    graph = {"9": {"class_type": "PreviewAny", "inputs": {}}, "3": {"class_type": "TextGenerate", "inputs": {}}}
    entry = {"outputs": {"3": {"text": ["raw"]}, "9": {"text": ["shown"]}}, "status": {"status_str": "success"}}
    assert extract_text(entry, graph) == "shown"
    assert history_error(entry) is None
    assert extract_text({"outputs": {"9": {"text": [""]}}}, graph) is None
    bad = {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "boom"}]]}}
    assert history_error(bad) == "boom"


def test_clean_output_strips_model_wrappers():
    assert clean_output("**Prompt:**  \nA cat") == "A cat"
    assert clean_output("assistant\nA cat") == "A cat"
    assert clean_output('```\n"A cat"\n```') == "A cat"
    assert clean_output("A cat @ed.png sits") == "A cat @ed.png sits"


def test_runner_strips_enhancer_carrier_nodes():
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import workflow_controls as wc

    wf = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "sys"}, "_meta": {"title": SYSTEM_PROMPT_TITLE}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "p", "clip": ["3", 0]}, "_meta": {"title": "THM-Prompt"}},
        "_is_flux2": True,
    }
    wc.strip_prompt_metadata(wf)
    assert set(wf) == {"2"}


def test_enhance_status_and_gate_over_rest(tmp_path):
    """No enhancer workflow -> local off. With one but ComfyUI unreachable -> still off, with a reason.
    The agent engine answers 409 without a live session either way."""
    world = seeded_world(tmp_path)
    slug = world.slug
    r = world.client.get(f"/api/projects/{slug}/enhance/status")
    assert r.status_code == 200
    body = r.json()
    assert body["local"] is False and body["agent"] is False and "prompt_enhancer.json" in body["detail"]

    r = world.client.post(f"/api/projects/{slug}/enhance", json={"kind": "image", "prompt": "x", "engine": "agent"})
    assert r.status_code == 409 and r.json()["code"] == "no_agent_session"
    r = world.client.post(f"/api/projects/{slug}/enhance", json={"kind": "image", "prompt": " ", "note": ""})
    assert r.status_code == 400
    r = world.client.post(f"/api/projects/{slug}/enhance", json={"kind": "image", "prompt": "x"})
    assert r.status_code == 503 and r.json()["code"] == "enhancer_unavailable"


@pytest.mark.skipif(not REAL_ENHANCER.is_file(), reason="no enhancer workflow checked in")
def test_enhance_status_needs_comfy(tmp_path):
    from thm_server import create_app
    from starlette.testclient import TestClient

    wdir = tmp_path / "workflows"
    wdir.mkdir()
    shutil.copy2(REAL_ENHANCER, wdir / "prompt_enhancer.json")
    assert enhancer_available(wdir)
    settings = CoreSettings(projects_dir=tmp_path / "p", workflows_dir=wdir, backups=BackupSettings(throttle_seconds=0))
    settings.comfy.api_base = "http://127.0.0.1:9"  # nothing listens on the discard port
    app = create_app(settings, web_dist=tmp_path / "nodist")
    client = TestClient(app)
    slug = client.post("/api/projects", json={"name": "Enh"}).json()["slug"]
    body = client.get(f"/api/projects/{slug}/enhance/status").json()
    assert body["local"] is False and "not reachable" in body["detail"]


def test_agent_engine_round_trip_over_rest(tmp_path):
    """With a live session the agent engine queues a request, the agent lists and answers it,
    and the app's held request returns the answer. Only the agent may answer, only once."""
    import threading

    from thm_core_fixtures import live_session

    world = seeded_world(tmp_path)
    project = world.store.load(world.slug)
    live_session(project)
    world.store.save(project)
    world.registry.invalidate(world.slug)
    slug = world.slug
    agent = {"X-THM-Actor": "agent"}

    assert world.client.get(f"/api/projects/{slug}/enhance/status").json()["agent"] is True
    assert world.client.get(f"/api/projects/{slug}/enhance/requests", headers=agent).json() == []

    answer: dict = {}
    body = {"kind": "image", "prompt": "a cat @tom.png on a mat", "note": "more fur", "engine": "agent"}
    t = threading.Thread(target=lambda: answer.update(world.client.post(f"/api/projects/{slug}/enhance", json=body).json()))
    t.start()
    for _ in range(100):
        pending = world.client.get(f"/api/projects/{slug}/enhance/requests", headers=agent).json()
        if pending:
            break
        import time as _t

        _t.sleep(0.02)
    assert len(pending) == 1
    req = pending[0]
    assert req["kind"] == "image" and "NOTE\n\nmore fur" in req["message"] and "PROMPT\n\na cat @tom.png on a mat" in req["message"]
    assert "formatter, not a writer" in req["system_prompt"]

    # the user cannot answer; an empty answer is refused
    assert world.client.post(f"/api/projects/{slug}/enhance/requests/{req['id']}/reply", json={"prompt": "x"}).status_code == 403
    assert world.client.post(f"/api/projects/{slug}/enhance/requests/{req['id']}/reply", json={}, headers=agent).status_code == 400
    r = world.client.post(f"/api/projects/{slug}/enhance/requests/{req['id']}/reply", json={"prompt": "  A fluffy cat @tom.png curled on a mat.  "}, headers=agent)
    assert r.status_code == 200
    t.join(5)
    assert answer["engine"] == "agent" and answer["prompt"] == "A fluffy cat @tom.png curled on a mat." and answer["system_source"] == "default"
    # answered requests are gone
    assert world.client.get(f"/api/projects/{slug}/enhance/requests", headers=agent).json() == []
    # both sides of the exchange sit in the feed as ordinary messages
    feed = world.client.get(f"/api/projects/{slug}/feed").json()["entries"]
    asked = [e["message"] for e in feed if e["message"]["kind"] == "user" and e["message"]["text"].startswith("Rewrite ")]
    answered = [e["message"] for e in feed if e["message"]["kind"] == "agent" and e["message"]["text"] == "A fluffy cat @tom.png curled on a mat."]
    assert len(asked) == 1 and "Note: more fur" in asked[0]["text"] and "Prompt: a cat @tom.png on a mat" in asked[0]["text"]
    assert len(answered) == 1
    assert world.client.post(f"/api/projects/{slug}/enhance/requests/{req['id']}/reply", json={"prompt": "again"}, headers=agent).status_code == 404


def test_agent_engine_error_reply_and_tag_check(tmp_path):
    import threading

    from thm_core_fixtures import live_session

    world = seeded_world(tmp_path)
    project = world.store.load(world.slug)
    live_session(project)
    world.store.save(project)
    world.registry.invalidate(world.slug)
    slug = world.slug
    agent = {"X-THM-Actor": "agent"}

    def ask(prompt: str, reply: dict) -> dict:
        out: dict = {}
        t = threading.Thread(target=lambda: out.update(world.client.post(f"/api/projects/{slug}/enhance", json={"kind": "video", "prompt": prompt, "engine": "agent"}).json()))
        t.start()
        import time as _t

        for _ in range(100):
            pending = world.client.get(f"/api/projects/{slug}/enhance/requests", headers=agent).json()
            if pending:
                break
            _t.sleep(0.02)
        world.client.post(f"/api/projects/{slug}/enhance/requests/{pending[0]['id']}/reply", json=reply, headers=agent)
        t.join(5)
        return out

    assert ask("x @ed.png y", {"error": "no session budget"})["code"] == "enhance_failed"
    # a rewrite that drops a tag lands, and says which
    dropped = ask("x @ed.png y", {"prompt": "a rewrite without the tag"})
    assert dropped["prompt"] == "a rewrite without the tag" and dropped["dropped_tags"] == ["@ed.png"]
    # a rejected rewrite rides along on the error, so the sheet can show what the enhancer wrote
    empty = ask("x @ed.png y", {"prompt": "short"})
    assert empty["code"] == "enhance_failed" and empty["extra"]["replies"] == ["short"]
