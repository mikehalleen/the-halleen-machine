"""thm_server: what the agent reads and writes beyond commands -- workflow guides, generation
filters, project cards with the session on them, agent uploads and stills, frame sheets, and
the session mode over REST."""

import io

import pytest
from PIL import Image

from thm_core import MediaOrigin, adopt_media
from thm_core_fixtures import live_session
from thm_server_fixtures import make_tiny_mp4, seeded_world

AGENT = {"X-THM-Actor": "agent"}


def _png(color=(120, 60, 200), size=(32, 24)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _base(world) -> str:
    return f"/api/projects/{world.slug}"


def _go_live(world, mode="image_review"):
    project = world.store.load(world.slug)
    sess = live_session(project)
    sess.mode = mode
    world.store.save(project)
    world.registry.invalidate(world.slug)
    return sess.id


def _cursor(world) -> str:
    """The agent's current inbox cursor, for a post: a note that landed unread refuses the post."""
    return str(world.client.get(f"{_base(world)}/inbox", headers=AGENT).json()["cursor"])


def _agent(world, name, body, session, **headers):
    h = {**AGENT, "X-THM-Session": session, **headers}
    return world.client.post(f"{_base(world)}/commands/{name}", json=body, headers=h)


# -- reads -----------------------------------------------------------------------


def test_workflow_detail_carries_its_prompt_guide(tmp_path):
    world = seeded_world(tmp_path)
    listed = world.client.get("/api/workflows").json()
    assert all(w.get("guide") is None for w in listed), "the list stays light"
    one = world.client.get("/api/workflows/vid.json").json()
    assert one["kind"] == "video" and one["guide_source"] == "default"
    assert "one continuous shot" in one["guide"] and "@-tags" in one["guide"]
    assert world.client.get("/api/workflows/nope.json").status_code == 404


def test_generations_filter_by_bin_and_status(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    assert [g["bin"] for g in world.client.get(f"{base}/generations").json()] == [world.image]
    assert world.client.get(f"{base}/generations", params={"bin": world.video}).json() == []
    assert len(world.client.get(f"{base}/generations", params={"bin": world.image, "status": "done"}).json()) == 1
    assert world.client.get(f"{base}/generations", params={"status": "failed"}).json() == []


def test_project_cards_carry_the_session_blocker_and_pending_count(tmp_path):
    world = seeded_world(tmp_path)
    cards = world.client.get("/api/projects").json()
    assert cards[0]["agent_session"] is None and cards[0]["blocker"] is None and cards[0]["pending_changes"] == 0

    session = _go_live(world)
    _agent(world, "post_message", {"text": "need the logo file", "kind": "blocker"}, session, **{"X-THM-Inbox-Cursor": _cursor(world)})
    # a manual session lands the agent's edits; the pending count is the renders (or a card it asked for)
    r = _agent(world, "set_bin_field", {"bin": world.video, "field": "length_s", "value": 8}, session)
    assert r.json()["change"]["decision"] is None
    _agent(world, "set_bin_field", {"bin": world.video, "field": "length_s", "value": 6, "card": True}, session)
    card = world.client.get("/api/projects").json()[0]
    assert card["agent_session"]["status"] == "live" and card["agent_session"]["mode"] == "image_review"
    assert card["blocker"]["text"] == "need the logo file" and card["pending_changes"] == 1


# -- agent uploads and stills ---------------------------------------------------


def test_agent_uploads_into_a_bin_with_provenance(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    session = _go_live(world, mode="auto")
    r = world.client.post(
        f"{base}/uploads",
        files={"file": ("take26_silent.png", _png((9, 9, 9)), "image/png")},
        data={"bin": world.image, "source": world.take, "note": "audio removed, ffmpeg -an"},
        headers={**AGENT, "X-THM-Session": session},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    origin = body["media"]["origin"]
    assert origin["type"] == "upload" and origin["bin"] == world.image
    assert origin["source"] == world.take and origin["note"] == "audio removed, ffmpeg -an"
    # auto mode: it landed, done, no card
    assert body["change"]["actor"] == "agent" and body["change"]["decision"] is None
    detail = world.client.get(f"{base}/bins/{world.image}").json()
    assert detail["selected"] != body["media"]["id"]  # an arrival is new, not the current take

    bad = world.client.post(
        f"{base}/uploads",
        files={"file": ("x.png", _png((1, 1, 1)), "image/png")},
        data={"bin": world.image, "source": "m_nope"},
        headers={**AGENT, "X-THM-Session": session},
    )
    assert bad.status_code == 404 and bad.json()["code"] == "unknown_id"


def test_agent_lifts_a_still_but_does_not_cut(tmp_path):
    world = seeded_world(tmp_path)
    video_path = make_tiny_mp4(world.media_dir / "src.mp4")
    if video_path is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    project = world.registry.get(world.slug)
    media = adopt_media(project, video_path, origin=MediaOrigin(type="upload", bin=world.mara))
    world.store.save(project)
    world.registry.invalidate(world.slug)

    r = world.client.post(f"{_base(world)}/clips", json={"source": media.id, "mode": "still", "at_s": 0.2, "bin": world.mara}, headers=AGENT)
    assert r.status_code == 409 and r.json()["code"] == "no_session"
    session = _go_live(world, mode="auto")
    h = {**AGENT, "X-THM-Session": session}
    r = world.client.post(f"{_base(world)}/clips", json={"source": media.id, "mode": "segment", "start_s": 0.0, "end_s": 0.5, "bin": world.mara}, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "wrong_actor"
    r = world.client.post(f"{_base(world)}/clips", json={"source": media.id, "mode": "still", "at_s": 0.2, "bin": world.mara}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["media"]["origin"]["type"] == "frame" and r.json()["media"]["type"] == "image"


def test_agent_makes_a_gif(tmp_path):
    world = seeded_world(tmp_path)
    video_path = make_tiny_mp4(world.media_dir / "g.mp4")
    if video_path is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    project = world.registry.get(world.slug)
    media = adopt_media(project, video_path, origin=MediaOrigin(type="upload", bin=world.mara))
    world.store.save(project)
    world.registry.invalidate(world.slug)

    r = world.client.post(f"{_base(world)}/clips", json={"source": media.id, "mode": "gif", "bin": world.mara}, headers=AGENT)
    assert r.status_code == 409 and r.json()["code"] == "no_session"
    session = _go_live(world, mode="auto")
    r = world.client.post(f"{_base(world)}/clips", json={"source": media.id, "mode": "gif", "bin": world.mara}, headers={**AGENT, "X-THM-Session": session})
    assert r.status_code == 200, r.text
    assert r.json()["media"]["origin"]["type"] == "gif" and r.json()["media"]["path"].endswith(".gif")

def test_frames_sheet_for_a_video(tmp_path):
    world = seeded_world(tmp_path)
    video_path = make_tiny_mp4(world.media_dir / "sheet.mp4")
    if video_path is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    project = world.registry.get(world.slug)
    media = adopt_media(project, video_path, origin=MediaOrigin(type="upload", bin=world.mara))
    world.store.save(project)
    world.registry.invalidate(world.slug)

    r = world.client.get(f"/media/{world.slug}/{media.id}/frames", params={"n": 4, "size": 128})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/webp"
    sheet = Image.open(io.BytesIO(r.content))
    assert sheet.width == 4 * sheet.height  # four frames of a square clip in one row
    assert sheet.height <= 128
    again = world.client.get(f"/media/{world.slug}/{media.id}/frames", params={"n": 4, "size": 128})
    assert again.content == r.content
    six = Image.open(io.BytesIO(world.client.get(f"/media/{world.slug}/{media.id}/frames", params={"n": 6, "size": 64}).content))
    assert six.width == 4 * six.height / 2  # two rows of four, the second half empty
    assert world.client.get(f"/media/{world.slug}/{world.m1}/frames").status_code == 422
    assert world.client.get(f"/media/{world.slug}/{media.id}/frames", params={"n": 9}).status_code == 422


# -- session mode over REST ------------------------------------------------------


def test_auto_session_owes_nothing_even_when_the_agent_asks(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    r = world.client.post(f"{base}/commands/start_session", json={"notes": "make it pop"})
    assert r.status_code == 200 and r.json()["change"]["description"] == "session started"
    assert world.client.get(base).json()["agent_session"]["mode"] == "auto"
    r = world.client.post(f"{base}/commands/connect_session", json={"harness": "claude-code"}, headers=AGENT)
    session = world.client.get(base).json()["agent_session"]["id"]

    before = world.client.get(f"{base}/feed").json()["cursor"]
    r = _agent(world, "set_bin_field", {"bin": world.video, "field": "length_s", "value": 8, "reason": "the brief says 8 s"}, session)
    assert r.status_code == 200 and r.json()["change"]["decision"] is None
    assert [(m["kind"], m["text"]) for m in r.json()["messages"]] == [("system", "agent: Video 1 - length 5s to 8s -- the brief says 8 s")]
    assert world.client.get(f"{base}/feed").json()["cursor"] == before + 1, "no card, but a feed line"
    assert world.client.get(base).json()["pending_changes"] == 0

    # card is the one ask auto honors: the finished candidate, presented for a verdict
    r = _agent(world, "set_bin_field", {"bin": world.video, "field": "prompt", "value": "@mara.png at the rail, dawn", "card": True}, session)
    assert r.status_code == 200, r.text
    assert r.json()["change"]["decision"]["status"] == "pending"
    assert [m["kind"] for m in r.json()["messages"]] == ["change"]
    assert world.client.get(base).json()["pending_changes"] == 1
    world.client.post(f"{base}/changes/{r.json()['change']['id']}/decision", json={"action": "close"})
    assert world.client.get(base).json()["pending_changes"] == 0

    r = _agent(world, "set_session_mode", {"mode": "video_review", "requested": True}, session)
    assert r.status_code == 200 and r.json()["change"]["decision"] is None
    assert r.json()["messages"][0]["kind"] == "system" and r.json()["messages"][0]["text"].startswith("session mode video cards -- ")
    # a manual session still lands the agent's edits with the reason; only its renders are cards
    r = _agent(world, "set_bin_field", {"bin": world.video, "field": "length_s", "value": 6, "reason": "shorter holds"}, session)
    assert r.json()["change"]["decision"] is None
    assert [(m["kind"], m["text"]) for m in r.json()["messages"]] == [("system", "agent: Video 1 - length 8s to 6s -- shorter holds")]


def test_plan_session_only_proposes_the_brief_and_stays_plan_on_approval(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    world.client.post(f"{base}/commands/start_session", json={"notes": "plan this", "mode": "plan"})
    world.client.post(f"{base}/commands/connect_session", json={"harness": "claude-code"}, headers=AGENT)
    session = world.client.get(base).json()["agent_session"]["id"]

    r = _agent(world, "set_bin_field", {"bin": world.video, "field": "length_s", "value": 8}, session)
    assert r.status_code == 409 and r.json()["code"] == "plan_mode"
    r = _agent(world, "post_message", {"text": "here is the plan", "kind": "agent"}, session)
    assert r.status_code == 409 and r.json()["code"] == "inbox_cursor_required", r.text
    r = _agent(world, "post_message", {"text": "here is the plan", "kind": "agent"}, session, **{"X-THM-Inbox-Cursor": _cursor(world)})
    assert r.status_code == 200, r.text
    r = _agent(world, "set_project_settings", {"brief": "two bins: mara portrait, rail wide"}, session)
    assert r.status_code == 409 and r.json()["code"] == "plan_mode"
    r = _agent(world, "set_project_settings", {"brief": "two bins: mara portrait, rail wide"}, session, **{"X-THM-Propose": "1"})
    assert r.status_code == 200 and r.json()["change"]["decision"]["status"] == "proposed"
    change = r.json()["change"]["id"]

    r = world.client.post(f"{base}/changes/{change}/decision", json={"action": "approve"})
    assert r.status_code == 200, r.text
    detail = world.client.get(base).json()
    # the brief is written and the session is still planning: the panel changes the mode, not the approval
    assert detail["brief"] == "two bins: mara portrait, rail wide" and detail["agent_session"]["mode"] == "plan"
    assert not any("session mode" in (m.get("text") or "") for m in r.json()["messages"])
    r = _agent(world, "set_bin_field", {"bin": world.video, "field": "length_s", "value": 8}, session)
    assert r.status_code == 409 and r.json()["code"] == "plan_mode"
    # a second proposal is a second card; the first stays as it was decided
    r = _agent(world, "set_project_settings", {"brief": "three bins"}, session, **{"X-THM-Propose": "1"})
    assert r.status_code == 200 and r.json()["change"]["decision"]["status"] == "proposed"
    assert world.client.get(f"{base}/changes/{change}").json()["decision"]["status"] == "approved"


def test_set_bin_field_warns_about_unbound_handles(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    r = world.client.post(f"{base}/commands/set_bin_field", json={"bin": world.video, "field": "prompt", "value": "@mara.png and @ghost.png walk"})
    assert r.status_code == 200
    kinds = {(w["kind"], w["handle"]) for w in r.json()["warnings"]}
    assert ("unknown_token", "ghost.png") in kinds


# -- the agent's sign of life ---------------------------------------------------------


def test_agent_calls_record_activity_and_the_status_route(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    assert world.client.get(f"{base}/agent/activity").json() == {"seen": None, "doing": None, "idle_s": None, "gone": None}
    # a user call is not the agent's heartbeat
    world.client.get(f"{base}/inbox")
    assert world.client.get(f"{base}/agent/activity").json()["seen"] is None

    session = _go_live(world, mode="auto")
    world.client.get(f"{base}/inbox", headers={**AGENT, "X-THM-Session": session})
    act = world.client.get(f"{base}/agent/activity").json()
    assert act["seen"] and act["doing"] == "reading your messages" and act["idle_s"] is not None

    world.client.get(f"{base}/bins/{world.video}", headers={**AGENT, "X-THM-Agent-Doing": "checking Video 1 before a render"})
    assert world.client.get(f"{base}/agent/activity").json()["doing"] == "checking Video 1 before a render"

    r = world.client.post(f"{base}/agent/activity", json={"doing": "rewriting the prompt for Video 1"}, headers=AGENT)
    assert r.status_code == 200 and r.json()["doing"] == "rewriting the prompt for Video 1"
    assert world.client.post(f"{base}/agent/activity", json={"doing": "x"}).status_code == 409
    assert world.client.post(f"{base}/agent/activity", json={"doing": "  "}, headers=AGENT).status_code == 422
    assert world.client.get("/api/projects/nope/agent/activity").status_code == 404


def test_server_wide_agent_status_from_any_agent_call(tmp_path):
    """The bar's ``no agent`` / ``start`` choice: any call with the agent header, on any path
    (the project list, which the watch loop polls, has no slug), counts as the agent being there."""
    from thm_server import agent_activity

    world = seeded_world(tmp_path)
    assert world.client.get("/api/agent/status").json() == {"available": False, "seen": None, "idle_s": None}
    world.client.get("/api/projects")  # the browser polling is not an agent
    assert world.client.get("/api/agent/status").json()["available"] is False

    world.client.get("/api/projects", headers=AGENT)
    out = world.client.get("/api/agent/status").json()
    assert out["available"] is True and out["seen"] and out["idle_s"] is not None
    # per-project activity is untouched by a call with no slug
    assert world.client.get(f"{_base(world)}/agent/activity").json()["seen"] is None

    st = world.app.state.thm
    st.agent_seen.at -= agent_activity.AVAILABLE_S + 1
    assert world.client.get("/api/agent/status").json()["available"] is False


def test_jobs_carry_bin_name_and_who_queued_them(tmp_path):
    """The bar names the rendering bin and says ``queued by agent`` / ``queued by you``."""
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")  # generate refuses without one
    st = world.app.state.thm
    st.jobs.launch_fn = lambda cmd, cwd, log: None  # never start a runner here; the job stays queued
    base = _base(world)

    r = world.client.post(f"{base}/commands/generate", json={"bin": world.image})
    assert r.status_code == 200, r.text
    gen_id = r.json()["generation"]["id"]
    job = next(j for j in world.client.get(f"{base}/jobs").json() if j["job"] == gen_id)
    assert job["queued_by"] == "user" and job["bin_name"] == "Image 1"
    card = next(p for p in world.client.get("/api/projects").json() if p["slug"] == world.slug)
    assert card["generating_job"]["bin_name"] == "Image 1" and card["generating_job"]["queued_by"] == "user"

    session = _go_live(world, mode="auto")
    r = _agent(world, "generate", {"bin": world.image}, session, **{"X-THM-Inbox-Cursor": "0"})
    assert r.status_code == 200, r.text
    gen_id = r.json()["generation"]["id"]
    job = next(j for j in world.client.get(f"{base}/jobs").json() if j["job"] == gen_id)
    assert job["queued_by"] == "agent"


def test_project_detail_counts_the_sessions_changes(tmp_path):
    """``31 changes`` in the bar after a session ends: every change made during the session, the
    agent's and the user's own edits alike (both are stamped with the session), pending or not."""
    world = seeded_world(tmp_path)
    base = _base(world)
    assert world.client.get(base).json()["session_changes"] == 0
    session = _go_live(world, mode="auto")
    assert _agent(world, "set_bin_field", {"bin": world.image, "field": "prompt", "value": "one"}, session).status_code == 200
    assert _agent(world, "set_bin_field", {"bin": world.image, "field": "prompt", "value": "two"}, session).status_code == 200
    assert world.client.post(f"{base}/commands/set_bin_field", json={"bin": world.image, "field": "prompt", "value": "three"}).status_code == 200
    assert world.client.get(base).json()["session_changes"] == 3
    assert world.client.post(f"{base}/commands/end_session", json={}).status_code == 200
    detail = world.client.get(base).json()
    # the count survives the end (the transcript link needs it); end_session itself lands after
    # the status flips, so it carries no session and is not counted
    assert detail["agent_session"]["status"] == "ended" and detail["session_changes"] == 3


def test_enhance_request_walks_queued_seen_working(tmp_path, monkeypatch):
    """The agent engine's stages reach the browser as enhance events while the app waits."""
    import asyncio
    import threading

    from thm_server.routes import enhance as enh

    world = seeded_world(tmp_path)
    base = _base(world)
    session = _go_live(world, mode="auto")
    st = world.app.state.thm
    seen: list[dict] = []
    real_publish = st.bus.publish_event

    def spy(slug, event, data, id=None):
        if event == "enhance":
            seen.append(dict(data))
        return real_publish(slug, event, data, id)

    monkeypatch.setattr(st.bus, "publish_event", spy)
    monkeypatch.setattr(enh, "AGENT_TIMEOUT_S", 10.0)
    h = {**AGENT, "X-THM-Session": session}

    def agent_side():
        # the agent's watch lists the request (seen), picks it up (working), answers
        for _ in range(200):
            reqs = world.client.get(f"{base}/enhance/requests", headers=h).json()
            if reqs:
                break
            threading.Event().wait(0.02)
        req = reqs[0]
        assert world.client.post(f"{base}/enhance/requests/{req['id']}/ack", headers=h).status_code == 200
        world.client.post(f"{base}/enhance/requests/{req['id']}/reply", json={"prompt": "@mara.png at the rail, dawn light"}, headers=h)

    t = threading.Thread(target=agent_side)
    t.start()
    r = world.client.post(f"{base}/enhance", json={"kind": "video", "workflow": "vid.json", "prompt": "@mara.png at the rail", "note": "dawn", "engine": "agent", "bin": world.video})
    t.join(5)
    assert r.status_code == 200, r.text
    assert r.json()["engine"] == "agent" and r.json()["prompt"] == "@mara.png at the rail, dawn light"
    stages = [(e["engine"], e["status"]) for e in seen]
    assert stages == [("agent", "queued"), ("agent", "seen"), ("agent", "working"), ("agent", "done")]
    assert seen[2]["task"] == "the agent is rewriting" and seen[0]["request"] == seen[-1]["request"]
    del asyncio


def test_pending_count_is_the_live_sessions_open_cards_only(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    session = _go_live(world, mode="image_review")
    _agent(world, "set_bin_field", {"bin": world.video, "field": "length_s", "value": 8, "card": True}, session)
    _agent(world, "set_bin_field", {"bin": world.video, "field": "prompt", "value": "@mara.png, wide", "card": True}, session)
    assert world.client.get(base).json()["pending_changes"] == 2
    assert world.client.get("/api/projects").json()[0]["pending_changes"] == 2

    # the session ends with both cards unanswered: they stay in the feed, they stop counting
    _agent(world, "end_session", {}, session)
    assert world.client.get(base).json()["pending_changes"] == 0
    assert world.client.get("/api/projects").json()[0]["pending_changes"] == 0
    assert len(world.client.get(f"{base}/changes", params={"status": "pending"}).json()) == 2

    # a new session starts at zero; only its own cards count
    world.client.post(f"{base}/commands/start_session", json={"notes": "again", "mode": "image_review"})
    assert world.client.get(base).json()["pending_changes"] == 0
    world.client.post(f"{base}/commands/connect_session", json={"harness": "claude-code"}, headers=AGENT)
    new_session = world.client.get(base).json()["agent_session"]["id"]
    _agent(world, "set_bin_field", {"bin": world.video, "field": "length_s", "value": 6, "card": True}, new_session)
    assert world.client.get(base).json()["pending_changes"] == 1


def test_a_session_from_another_project_is_refused(tmp_path):
    """Many projects may each have a live session; a command must carry this project's."""
    world = seeded_world(tmp_path)
    base = _base(world)
    session = _go_live(world, mode="auto")
    other = {**AGENT, "X-THM-Session": "as_elsewhere"}
    r = world.client.post(f"{base}/commands/set_bin_field", json={"bin": world.video, "field": "length_s", "value": 8}, headers=other)
    assert r.status_code == 409 and r.json()["code"] == "wrong_session"
    r = world.client.post(f"{base}/uploads", files={"file": ("a.png", _png(), "image/png")}, data={"bin": world.image}, headers=other)
    assert r.status_code == 409 and r.json()["code"] == "wrong_session"
    # the right session, and no session header at all (a fresh process), both pass
    assert _agent(world, "set_bin_field", {"bin": world.video, "field": "length_s", "value": 8}, session).status_code == 200
    r = world.client.post(f"{base}/commands/set_bin_field", json={"bin": world.video, "field": "length_s", "value": 7}, headers=AGENT)
    assert r.status_code == 200
    # talking in the wrong room is the leak: a post under another project's session is refused,
    # a post with no session header (a fresh process) still reaches the user
    r = world.client.post(f"{base}/commands/post_message", json={"text": "which project am I on?", "kind": "agent"}, headers={**other, "X-THM-Inbox-Cursor": _cursor(world)})
    assert r.status_code == 409 and r.json()["code"] == "wrong_session"
    r = world.client.post(f"{base}/commands/post_message", json={"text": "which project am I on?", "kind": "agent"}, headers={**AGENT, "X-THM-Inbox-Cursor": _cursor(world)})
    assert r.status_code == 200


# -- propose_shots: the pre-generation card ---------------------------------------


def _hanging_launcher(cmd, cwd, log_path):
    from test_thm_server_jobs import hanging_launcher

    return hanging_launcher(cmd, cwd, log_path)


def test_propose_shots_round_trip(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    world.app.state.thm.jobs.launch_fn = _hanging_launcher
    base = _base(world)
    session = _go_live(world, mode="video_review")
    items = [
        {"name": "S01 neon", "description": "hood mount, neon rain", "prompt": "@mara.png in the rain", "workflow": "vid.json", "references": [world.m1], "length_s": 4, "quarter": True},
        {"name": "S02 desert", "description": "a rabbit past the hood", "workflow": "vid.json", "references": [world.m1]},
        {"name": "S03 plate", "description": "the empty street", "workflow": "img.json"},
    ]
    # without the ask it is refused; with it, a card and nothing spent
    r = _agent(world, "propose_shots", {"title": "round 3", "items": items}, session, **{"X-THM-Inbox-Cursor": "0"})
    assert r.status_code == 409 and r.json()["code"] == "propose_required", r.text
    r = _agent(world, "propose_shots", {"title": "round 3", "items": items, "reason": "the ideas you asked for"}, session, **{"X-THM-Propose": "1"})
    assert r.status_code == 200, r.text
    out = r.json()
    cards = out["changes"]
    assert out["change"]["id"] == cards[0]["id"] and len(cards) == 3
    assert [c["description"] for c in cards] == ["proposed: shot S01 neon", "proposed: shot S02 desert", "proposed: shot S03 plate"]
    assert all(c["decision"]["status"] == "proposed" for c in cards)
    entries = world.client.get(f"{base}/feed").json()["entries"][-4:]
    assert entries[0]["message"]["kind"] == "system" and entries[0]["message"]["text"] == "agent: 3 shots: round 3 -- the ideas you asked for"
    shots = [e["shots"][0] for e in entries[1:]]
    assert [len(e["shots"]) for e in entries[1:]] == [1, 1, 1]
    assert [s["name"] for s in shots] == ["S01 neon", "S02 desert", "S03 plate"]
    assert shots[0]["kind"] == "video" and shots[0]["quarter"] is True and shots[0]["has_prompt"] is True and shots[0]["length_s"] == 4
    assert shots[0]["references"] == [{"media": world.m1, "type": "image", "label": "Mara"}]
    assert shots[1]["has_prompt"] is False and shots[2]["kind"] == "image" and shots[2]["bin"] is None
    assert world.client.get(base).json()["pending_changes"] == 3
    assert world.client.get(f"{base}/changes/{cards[0]['id']}").json()["command"] == "propose_shots"
    assert world.client.get(f"{base}/changes/c_nope").status_code == 404

    # the verdicts, one card each: replied then kept, rewritten, closed
    r = world.client.post(f"{base}/changes/{cards[0]['id']}/decision", json={"action": "approve", "note": "is the rain in the plate?"})
    assert r.status_code == 200 and r.json()["change"]["decision"]["status"] == "replied" and r.json()["generations"] == [], r.text
    r = world.client.post(f"{base}/changes/{cards[0]['id']}/decision", json={"action": "approve"})
    assert r.status_code == 200, r.text
    kept = r.json()
    assert kept["change"]["decision"]["status"] == "approved" and len(kept["generations"]) == 1 and len(kept["changes"]) == 1
    assert kept["changes"][0]["batch"] == cards[0]["id"] and kept["changes"][0]["decision"]["status"] == "pending"
    r = world.client.post(f"{base}/changes/{cards[1]['id']}/decision", json={"action": "approve", "items": [{"index": 0, "description": "a jackrabbit, both flinch"}]})
    assert r.status_code == 200, r.text
    rewritten = r.json()
    assert len(rewritten["generations"]) == 1
    r = world.client.post(f"{base}/changes/{cards[2]['id']}/decision", json={"action": "close"})
    assert r.status_code == 200 and r.json()["change"]["decision"]["status"] == "closed", r.text
    feed = world.client.get(f"{base}/feed").json()["entries"]
    by_id = {e["change"]["id"]: e for e in feed if e["change"]}
    shots = [by_id[c["id"]]["shots"][0] for c in cards]
    assert [s["dropped"] for s in shots] == [False, False, False] and [s["edited"] for s in shots] == [False, True, False]
    assert shots[0]["bin"]["name"] == "S01 neon" and shots[0]["change"] == kept["changes"][0]["id"]
    assert shots[1]["description"] == "a jackrabbit, both flinch" and shots[2]["bin"] is None
    children = [e for e in feed if e["change"] and e["change"].get("batch") == cards[0]["id"]]
    assert len(children) == 1 and children[0]["message"]["kind"] == "change"
    # the two renders owe a verdict once their takes are in; queued, nothing waits on the user yet
    assert world.client.get(base).json()["pending_changes"] == 0
    detail = world.client.get(base).json()
    assert {b["name"] for b in detail["bins"]} >= {"S01 neon", "S02 desert"} and "S03 plate" not in {b["name"] for b in detail["bins"]}
    assert detail["pending_changes"] == 0  # the two takes count once they land
    assert len(world.client.get(f"{base}/jobs").json()) == 2

    # nothing waits on those verdicts unless it renders from them: an unrelated video goes, as a card
    cursor = world.client.get(f"{base}/inbox").json()["cursor"]
    r = _agent(world, "generate", {"bin": world.video}, session, **{"X-THM-Inbox-Cursor": str(cursor)})
    assert r.status_code == 200 and r.json()["change"]["decision"]["status"] == "pending", r.text
    # three render cards, none with a take yet: none waits on the user (close all still clears them)
    assert world.client.get(base).json()["pending_changes"] == 0

    # close all: nothing owed, one line, the agent reads one line
    r = world.client.post(f"{base}/changes/close-pending")
    assert r.status_code == 200 and r.json()["closed"] == 3 and r.json()["messages"][0]["text"] == "you: closed 3 cards"
    assert world.client.get(base).json()["pending_changes"] == 0
    assert world.client.post(f"{base}/changes/close-pending").json()["closed"] == 0
    r = world.client.post(f"{base}/changes/{kept['changes'][0]['id']}/decision", json={"action": "reopen"})
    assert r.status_code == 200 and r.json()["change"]["decision"]["status"] == "pending"
    r = world.client.post(f"{base}/changes/{kept['changes'][0]['id']}/decision", json={"action": "close"})
    assert r.status_code == 200 and r.json()["change"]["decision"]["status"] == "closed" and r.json()["messages"][-1]["status"] == "closed"


def test_manual_mode_over_rest(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    world.app.state.thm.jobs.launch_fn = _hanging_launcher
    base = _base(world)
    session = _go_live(world, mode="manual")
    cursor = world.client.get(f"{base}/inbox").json()["cursor"]
    r = _agent(world, "generate", {"bin": world.video}, session, **{"X-THM-Inbox-Cursor": str(cursor)})
    assert r.status_code == 409 and r.json()["code"] == "propose_first", r.text
    # the user's own render is never gated
    assert world.client.post(f"{base}/commands/generate", json={"bin": world.image}).status_code == 200


# -- private projects ---------------------------------------------------------------


def _go_private(world, value=True):
    project = world.store.load(world.slug)
    project.private = value
    world.store.save(project)
    world.registry.invalidate(world.slug)


def test_private_project_is_invisible_to_the_agent(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    _go_private(world)

    # the user's list and reads are untouched; the agent's list leaves the project out
    assert [c["slug"] for c in world.client.get("/api/projects").json()] == [world.slug]
    assert world.client.get("/api/projects", headers=AGENT).json() == []
    assert world.client.get(base).json()["private"] is True

    # every agent call addressed to it is refused before any handler runs
    for path in (base, f"{base}/bins", f"{base}/bins/{world.mara}", f"{base}/inbox", f"{base}/feed", f"{base}/generations", f"{base}/enhance/requests", f"{base}/agent/activity", f"/media/{world.slug}/{world.m1}/thumb"):
        r = world.client.get(path, headers=AGENT)
        assert r.status_code == 403 and r.json()["code"] == "private_project", path
    r = world.client.post(f"{base}/commands/post_message", json={"text": "hello"}, headers={**AGENT, "X-THM-Session": "sess"})
    assert r.status_code == 403 and r.json()["code"] == "private_project"
    assert world.app.state.thm.agent_activity.get(world.slug) is None, "no heartbeat lands on a private project"
    assert world.client.get(f"{base}/agent/activity").json()["seen"] is None

    # an unknown project is still the handler's 404, not a private refusal
    assert world.client.get("/api/projects/nope", headers=AGENT).status_code == 404

    _go_private(world, False)
    assert world.client.get(base, headers=AGENT).status_code == 200
    assert [c["slug"] for c in world.client.get("/api/projects", headers=AGENT).json()] == [world.slug]


def test_private_projects_list_last(tmp_path):
    """The user's list keeps newest-first, but every private project sits after the public ones."""
    world = seeded_world(tmp_path)
    _go_private(world)  # the seeded project is now private, and it is the most recently touched
    world.client.post("/api/projects", json={"name": "older public"})
    slugs = [c["slug"] for c in world.client.get("/api/projects").json()]
    assert slugs == ["older-public", world.slug]
    # make the new one private instead: it drops behind the seeded one whatever the recency
    world.client.post("/api/projects/older-public/commands/set_project_settings", json={"private": True})
    _go_private(world, False)
    slugs = [c["slug"] for c in world.client.get("/api/projects").json()]
    assert slugs == [world.slug, "older-public"]


def test_private_project_has_no_agent_session(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    _go_private(world)
    r = world.client.post(f"{base}/commands/start_session", json={})
    assert r.status_code == 403 and r.json()["code"] == "private_project", r.text
    assert world.client.get(base).json()["agent_session"] is None

    # going private mid-session ends it, and the enhance engine no longer offers the agent
    _go_private(world, False)
    _go_live(world, mode="auto")
    assert world.client.get(f"{base}/enhance/status").json()["agent"] is True
    r = world.client.post(f"{base}/commands/set_project_settings", json={"private": True})
    assert r.status_code == 200 and r.json()["change"]["description"] == "project settings - private (session ended)", r.text
    detail = world.client.get(base).json()
    assert detail["private"] is True and detail["agent_session"]["status"] == "ended"
    assert world.client.get(f"{base}/enhance/status").json()["agent"] is False
    r = world.client.post(f"{base}/enhance", json={"kind": "image", "prompt": "a rail at dawn", "engine": "agent"})
    assert r.status_code == 409 and r.json()["code"] == "no_agent_session", r.text


def test_private_project_keeps_its_words_inside(tmp_path, capsys):
    """The project list is read from every screen: a private project's card carries progress,
    never a bin name, a blocker, or a prompt; and the console echo says nothing it said."""
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    st = world.app.state.thm
    st.jobs.launch_fn = lambda cmd, cwd, log: None
    base = _base(world)
    _go_private(world)

    r = world.client.post(f"{base}/commands/generate", json={"bin": world.image})
    assert r.status_code == 200, r.text
    out = capsys.readouterr().out
    assert "private project: render queued" in out and "typed:" not in out and "Image 1" not in out

    card = next(p for p in world.client.get("/api/projects").json() if p["slug"] == world.slug)
    assert card["private"] is True and card["hero"] is None and card["blocker"] is None
    assert card["generating"] == 1 and card["generating_job"]["bin_name"] is None and card["generating_job"]["task"] is None
    # inside the project the bin is still named
    job = next(j for j in world.client.get(f"{base}/jobs").json() if j["job"] == r.json()["generation"]["id"])
    assert job["bin_name"] == "Image 1"


def test_local_enhance_can_be_cancelled_while_the_app_waits(tmp_path, monkeypatch):
    """The cancel route flags the run under the app's request id; the waiting request answers
    ``enhance_cancelled``, the event stream says cancelled (not failed), and the run is forgotten."""
    import threading

    from thm_server.routes import enhance as enh

    world = seeded_world(tmp_path)
    base = _base(world)
    st = world.app.state.thm
    seen: list[dict] = []
    real_publish = st.bus.publish_event

    def spy(slug, event, data, id=None):
        if event == "enhance":
            seen.append(dict(data))
        return real_publish(slug, event, data, id)

    monkeypatch.setattr(st.bus, "publish_event", spy)
    told: list[str] = []

    # stands in for ComfyUI: the run has posted its prompt and waits on its flag, the way
    # run_graph_once does through the tracker's stop hook
    def fake_run_local(project, st_, body, on_progress=None, run=None):
        run.prompt_id = "pid-enh"
        on_progress({"phase": "sampling", "step": 3, "steps": 20, "task": "sampling 3/20"})
        assert run.cancelled.wait(5), "cancel never arrived"
        raise enh._cancelled()

    monkeypatch.setattr(enh, "run_local", fake_run_local)
    monkeypatch.setattr(enh, "drop_prompt", lambda run: told.append(run.prompt_id) or "interrupted in ComfyUI")

    cancel_answer: dict = {}

    def cancel_side():
        for _ in range(200):
            if "enh_test" in st.enhance_local:
                break
            threading.Event().wait(0.02)
        r = world.client.post(f"{base}/enhance/enh_test/cancel")
        cancel_answer["status"], cancel_answer["body"] = r.status_code, r.json()

    t = threading.Thread(target=cancel_side)
    t.start()
    r = world.client.post(f"{base}/enhance", json={"kind": "image", "prompt": "a rail at dawn", "engine": "local", "request": "enh_test"})
    t.join(5)
    assert r.status_code == 409 and r.json()["code"] == "enhance_cancelled", r.text
    assert cancel_answer == {"status": 200, "body": {"request": "enh_test", "detail": "interrupted in ComfyUI"}}
    assert told == ["pid-enh"]  # ComfyUI was told to drop the posted prompt
    assert [(e["status"], e["request"]) for e in seen] == [("running", "enh_test"), ("cancelled", "enh_test")]
    assert "enh_test" not in st.enhance_local
    # nothing left to cancel: the run is gone
    assert world.client.post(f"{base}/enhance/enh_test/cancel").status_code == 404
