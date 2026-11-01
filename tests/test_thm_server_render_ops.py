"""Render operations from the 2026-11-01 bug report: a stalled render raises an alert in the
feed, a render ComfyUI lost in a restart is queued again by itself, ComfyUI unreachable is one
line per project, the agent cancels renders and restarts ComfyUI only in auto or on the user's
word, a card can be withdrawn, and an answer never closes a note that landed unread."""

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from thm_core.models import Message
from thm_server import create_app
from thm_server.jobs import ALERT_PREFIX
from thm_server_fixtures import seeded_world
from test_thm_server_jobs import _complete_on_disk, _manager, _queued_generation, _spec_arg, _status_arg, _wait_for, _write_status, completing_launcher, hanging_launcher

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
from thm_client import ThmClient, ThmError  # noqa: E402

AGENT = {"X-THM-Actor": "agent"}
BASE = "http://thm.test"


def _transport(client: TestClient):
    def send(method, url, body, headers):
        r = client.request(method, url[len(BASE):], content=body, headers=headers)
        return r.status_code, r.content, dict(r.headers)

    return send


async def _until(pred, timeout=5.0):
    """Wait for ``pred`` on the wall clock: ``asyncio.sleep(0.01)`` can return at once on
    Windows (timers inside the clock's 15.6 ms resolution run immediately), so a counted loop
    of short sleeps is no wait at all."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


def _alerts(world) -> list[str]:
    project = world.store.load(world.slug)
    return [m.text for m in world.store.read_messages(project) if m.kind == "system" and m.text.startswith(ALERT_PREFIX)]


# -- stall ---------------------------------------------------------------------------


def test_a_stalled_render_is_flagged_and_alerted_once_until_it_moves(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    async def scenario():
        mgr = _manager(world, hanging_launcher, stall_after=0.05)
        q = world.app.state.thm.bus.subscribe(world.slug)
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await _until(lambda: job.stalled)
        first = (job.stalled, _alerts(world))
        await asyncio.sleep(0.2)
        again = _alerts(world)
        # progress moves: the flag clears and no second line is posted
        _write_status(job.paths.status, "running", task="sampling 4/20", pct=20.0, outputs=[])
        await _until(lambda: not job.stalled)
        cleared = job.stalled
        job.watcher.cancel()
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return first, again, cleared, events

    (stalled, alerts), again, cleared, events = asyncio.run(scenario())
    assert stalled is True and len(alerts) == 1 and "no progress" in alerts[0] and "Image 1" in alerts[0]
    assert again == alerts, "one line per stall, not one per poll"
    assert cleared is False
    assert any(e[0] == "job" and e[1].get("stalled") for e in events)
    assert any(e[0] == "message" and e[1]["message"]["text"].startswith(ALERT_PREFIX) for e in events)


# -- queued again ------------------------------------------------------------------


def dropping_then_completing_launcher():
    """The first runner reports ComfyUI dropped the prompt (a restart); the next completes."""
    seen: list[list[str]] = []

    def launch(cmd, cwd, log_path):
        seen.append(cmd)
        if len(seen) == 1:
            _write_status(_status_arg(cmd), "failed", error="prompt pid-1 dropped by ComfyUI (in neither its queue nor its history for 120s)", outputs=[])
            Path(log_path).write_text("[PID] pid-1\n", encoding="utf-8")
            return SimpleNamespace(pid=4242, poll=lambda: 1, returncode=1)
        spec = _spec_arg(cmd)
        _complete_on_disk(spec, _status_arg(cmd), (7, 7, 7))
        return SimpleNamespace(pid=4243, poll=lambda: 0, returncode=0)

    launch.seen = seen
    return launch


def always_dropping_launcher():
    seen: list[list[str]] = []

    def launch(cmd, cwd, log_path):
        seen.append(cmd)
        _write_status(_status_arg(cmd), "failed", error="prompt pid-1 dropped by ComfyUI (in neither its queue nor its history for 120s)", outputs=[])
        return SimpleNamespace(pid=4242, poll=lambda: 1, returncode=1)

    launch.seen = seen
    return launch


async def _settle(job, timeout=5.0):
    """Wait until the job has no live watcher or collector and is not live."""
    def done():
        tasks = [t for t in (job.watcher, job.collector) if t is not None and not t.done()]
        return not tasks and not job.live

    if not await _until(done, timeout):
        raise AssertionError(f"job did not settle: {job.status} {job.error}")


def test_a_render_comfy_lost_is_queued_again_once(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    async def scenario():
        launch = dropping_then_completing_launcher()
        mgr = _manager(world, launch)  # late lookup: nothing in ComfyUI -> gone -> queued again
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await _settle(job)
        return job, launch.seen, gen

    job, seen, gen = asyncio.run(scenario())
    assert job.status == "done" and job.retried is True and len(seen) == 2 and len(job.takes) == 1
    world.registry.invalidate(world.slug)
    rec = world.store.read_generation(world.registry.get(world.slug), gen.id)
    notes = [w["detail"] for w in rec.warnings if w["kind"] == "runner"]
    assert any("dropped by ComfyUI" in n for n in notes) and any(n.startswith("queued again") for n in notes)
    assert rec.status == "done"

    # a second loss stays failed: once only
    async def twice():
        launch = always_dropping_launcher()
        mgr = _manager(world, launch)
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await _settle(job)
        return job, launch.seen

    job, seen = asyncio.run(twice())
    assert job.status == "failed" and job.retried is True and len(seen) == 2


def test_a_plain_runner_failure_is_not_queued_again(tmp_path):
    from test_thm_server_jobs import crashing_launcher

    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    async def scenario():
        mgr = _manager(world, crashing_launcher)
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await _settle(job)
        return job

    job = asyncio.run(scenario())
    assert job.status == "failed" and job.retried is False


# -- ComfyUI down ----------------------------------------------------------------


def test_comfyui_unreachable_is_one_line_per_project_and_one_when_back(tmp_path, monkeypatch):
    import thm_server.jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "COMFY_WAIT_POLL_S", 0.05)
    up = {"now": False}
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    # two renders, two different pictures (a byte-identical second output would not land as a take)
    launchers = [completing_launcher((10, 20, 30)), completing_launcher((40, 50, 60))]
    launch = lambda cmd, cwd, log: launchers.pop(0)(cmd, cwd, log)  # noqa: E731
    app = create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": launch, "poll_interval": 0.01, "comfy_up": lambda _b: up["now"]})
    base = f"/api/projects/{world.slug}"
    with TestClient(app) as client:
        other = client.post(f"{base}/commands/create_bin", json={"bin_name": "Dusk", "workflow": "img.json"}).json()["bin"]
        a = client.post(f"{base}/commands/generate", json={"bin": world.image}).json()["generation"]["id"]
        b = client.post(f"{base}/commands/generate", json={"bin": other}).json()["generation"]["id"]
        _wait_for(client, f"{base}/jobs", lambda js: all(j["task"] for j in js if j["job"] in (a, b)))
        _wait_for(client, f"{base}/messages", lambda ms: any(m["text"].startswith(ALERT_PREFIX) for m in ms))
        alerts = [m["text"] for m in client.get(f"{base}/messages").json() if m["text"].startswith(ALERT_PREFIX)]
        assert len(alerts) == 1 and "not reachable" in alerts[0]
        up["now"] = True
        _wait_for(client, f"{base}/jobs", lambda js: all(j["status"] == "done" for j in js if j["job"] in (a, b)))
        alerts = [m["text"] for m in client.get(f"{base}/messages").json() if m["text"].startswith(ALERT_PREFIX)]
        assert len(alerts) == 2 and "is back" in alerts[1]


# -- cancel and restart: the agent's narrow levers ---------------------------------


def _session(client, slug, mode="image_review", drafts=False):
    r = client.post(f"/api/projects/{slug}/commands/start_session", json={"notes": "go", "mode": mode, "drafts": drafts})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/{slug}/commands/connect_session", json={"harness": "claude-code"}, headers=AGENT)
    assert r.status_code == 200, r.text
    sid = client.get(f"/api/projects/{slug}").json()["agent_session"]["id"]
    return {**AGENT, "X-THM-Session": sid}


def test_agent_cancels_only_in_auto_or_on_the_users_word(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    st = world.app.state.thm
    st.jobs.launch_fn = hanging_launcher
    st.jobs.comfy_cancel_fn = lambda api_base, slug, gen_id: "interrupted in ComfyUI"
    base = f"/api/projects/{world.slug}"
    with TestClient(world.app) as client:
        # no session: refused
        gen = client.post(f"{base}/commands/generate", json={"bin": world.image}).json()["generation"]["id"]
        r = client.post(f"{base}/jobs/{gen}/cancel", json={}, headers=AGENT)
        assert r.status_code == 409 and r.json()["code"] == "no_session"
        hdr = _session(client, world.slug, "image_review")
        r = client.post(f"{base}/jobs/{gen}/cancel", json={}, headers=hdr)
        assert r.status_code == 409 and r.json()["code"] == "requested_required" and r.json()["extra"]["mode"] == "image_review"
        r = client.post(f"{base}/jobs/{gen}/cancel", json={"requested": True, "reason": "you asked"}, headers=hdr)
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert any("the agent cancelled" in t and "you asked" in t for t in _alerts(world))
        # auto: on its own
        client.post(f"{base}/commands/set_session_mode", json={"mode": "auto"})
        gen2 = client.post(f"{base}/commands/generate", json={"bin": world.image}).json()["generation"]["id"]
        r = client.post(f"{base}/jobs/{gen2}/cancel", headers=hdr)  # no body at all
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        # the user's own click needs nothing
        gen3 = client.post(f"{base}/commands/generate", json={"bin": world.image}).json()["generation"]["id"]
        assert client.post(f"{base}/jobs/{gen3}/cancel").status_code == 200


def test_restart_comfy_requeues_live_renders_and_is_gated_for_the_agent(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    script = tmp_path / "restart.ps1"
    script.write_text("# restart\n", encoding="utf-8")
    runs: list[tuple] = []
    app = create_app(
        world.settings, web_dist=tmp_path / "nodist",
        jobs_kwargs={"launch": hanging_launcher, "poll_interval": 0.01, "comfy_restart": lambda s, cwd, log: runs.append((s, cwd, str(log)))},
    )
    base = f"/api/projects/{world.slug}"
    with TestClient(app) as client:
        # unconfigured: refused with the fix
        r = client.post("/api/comfy/restart", json={})
        assert r.status_code == 409 and r.json()["code"] == "comfy_restart_unconfigured" and "restart_script" in r.json()["detail"]
        world.settings.comfy.restart_script = str(script)
        world.settings.comfy.install_path = str(tmp_path)
        gen = client.post(f"{base}/commands/generate", json={"bin": world.image}).json()["generation"]["id"]
        _wait_for(client, f"{base}/jobs", lambda js: any(j["job"] == gen and j["status"] == "running" for j in js))
        # the agent in a review mode: only on the user's word
        hdr = _session(client, world.slug, "video_review")
        r = client.post("/api/comfy/restart", json={"slug": world.slug}, headers=hdr)
        assert r.status_code == 409 and r.json()["code"] == "requested_required"
        r = client.post("/api/comfy/restart", json={}, headers=hdr)
        assert r.status_code == 422
        r = client.post("/api/comfy/restart", json={"slug": world.slug, "requested": True, "reason": "stuck at 58%"}, headers=hdr)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["requeued"] == [gen] and out["script"] == str(script)
        assert runs == [(str(script), str(tmp_path), out["log"])]
        job = next(j for j in client.get(f"{base}/jobs").json() if j["job"] == gen)
        assert job["retried"] is True and job["status"] in ("queued", "running")
        rec = client.get(f"{base}/generations/{gen}").json()
        assert any("queued again" in w["detail"] and "stuck at 58%" in w["detail"] for w in rec["warnings"])
        alerts = _alerts(world)
        assert any("ComfyUI is restarting" in t and "1 render here queued again" in t for t in alerts)
        # auto: on its own
        client.post(f"{base}/commands/set_session_mode", json={"mode": "auto"})
        r = client.post("/api/comfy/restart", json={"slug": world.slug, "reason": "hung"}, headers=hdr)
        assert r.status_code == 200 and len(runs) == 2
        # the render that was already given its second go is failed, not queued a third time
        job = next(j for j in client.get(f"{base}/jobs").json() if j["job"] == gen)
        assert job["status"] == "failed" and "second time" in job["error"]


# -- withdraw, and answer behind the inbox ----------------------------------------


def test_the_agent_withdraws_its_own_waiting_card(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    base = f"/api/projects/{world.slug}"
    with TestClient(world.app) as client:
        hdr = _session(client, world.slug, "auto")
        cursor = client.get(f"{base}/inbox", headers=hdr).json()["cursor"]
        items = [{"name": "Dawn", "description": "a dawn shot", "workflow": "img.json"}]
        r = client.post(f"{base}/commands/propose_shots", json={"items": items}, headers={**hdr, "X-THM-Propose": "1", "X-THM-Inbox-Cursor": str(cursor)})
        assert r.status_code == 200, r.text
        card = r.json()["change"]["id"]
        # not the user's to withdraw
        r = client.post(f"{base}/commands/withdraw_change", json={"change": card})
        assert r.status_code == 409 and r.json()["code"] == "wrong_actor"
        r = client.post(f"{base}/commands/withdraw_change", json={"change": card, "why": "auto mode needs no card"}, headers=hdr)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["change"]["decision"]["status"] == "closed" and "withdrawn by the agent: auto mode needs no card" in out["change"]["decision"]["note"]
        assert out["messages"][0]["kind"] == "system" and out["messages"][0]["text"].startswith("agent: withdrew the card")
        assert client.get(f"{base}/changes/{card}").json()["decision"]["status"] == "closed"
        r = client.post(f"{base}/commands/withdraw_change", json={"change": card}, headers=hdr)
        assert r.status_code == 409 and r.json()["code"] == "bad_transition"
        # a user's change carries no decision: nothing to withdraw
        ch = client.post(f"{base}/commands/set_bin_field", json={"bin": world.video, "field": "length_s", "value": 8}).json()["change"]["id"]
        r = client.post(f"{base}/commands/withdraw_change", json={"change": ch}, headers=hdr)
        assert r.status_code == 409 and r.json()["code"] == "not_agent_change"


def test_an_answer_behind_the_inbox_is_refused_like_a_post(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    base = f"/api/projects/{world.slug}"
    with TestClient(world.app) as http:
        http.post(f"{base}/commands/start_session", json={"notes": "go", "mode": "auto"})
        agent = ThmClient(BASE, transport=_transport(http))
        agent.connect(world.slug, "claude-code")
        first = http.post(f"{base}/commands/post_message", json={"text": "fix the extra arm"}).json()["messages"][0]["id"]
        agent.inbox(world.slug)
        # a second note lands while the agent works on the first
        http.post(f"{base}/commands/post_message", json={"text": "and make it blue"})
        try:
            agent.post_message(world.slug, "re-rendering without the arm")
        except ThmError as exc:
            assert exc.code == "inbox_behind"
        else:
            raise AssertionError("the post should have been refused")
        try:
            agent.answer_notes(world.slug, [first], "re-render queued")
        except ThmError as exc:
            assert exc.code == "inbox_behind" and [m["text"] for m in exc.extra["messages"]] == ["and make it blue"]
        else:
            raise AssertionError("the answer should have been refused: the note would close with no reply posted")
        assert first in [m["id"] for m in agent.open_notes(world.slug)]
        agent.inbox(world.slug)
        agent.post_message(world.slug, "re-rendering without the arm, in blue")
        agent.answer_notes(world.slug, [first], "re-render queued")
        assert first not in [m["id"] for m in agent.open_notes(world.slug)]
        # a caller with no cursor at all (a harness script) is not held
        r = http.post(f"{base}/commands/answer_notes", json={"notes": [first], "did": "again"}, headers={**AGENT, "X-THM-Session": agent.session})
        assert r.status_code == 200


# -- the agent's watch: alerts wake it, and polling keeps its sessions attended -------


def test_watch_wakes_on_a_server_alert_and_polls_as_the_session_it_holds(tmp_path):
    from thm_core import ids as core_ids
    from thm_server import agent_activity

    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    base = f"/api/projects/{world.slug}"
    with TestClient(world.app) as http:
        http.post(f"{base}/commands/start_session", json={"notes": "go", "mode": "auto"})
        agent = ThmClient(BASE, transport=_transport(http))
        agent.connect(world.slug, "claude-code")
        _, cursor = agent.inbox(world.slug)
        agent.answer_notes(world.slug, [m["id"] for m in agent.open_notes(world.slug)], "read")  # the brief is a note too
        # nothing wakes it
        try:
            agent.wait_for_inbox(world.slug, cursor=cursor, poll_s=0, timeout_s=0, sleep=lambda s: None)
        except TimeoutError:
            pass
        else:
            raise AssertionError("nothing should have woken the agent")
        # a server alert does
        project = world.store.load(world.slug)
        world.store.append_message(project, Message(id=core_ids.new_id(core_ids.MESSAGE), ts="2026-11-01T10:00:00Z", kind="system", text=ALERT_PREFIX + "render of Image 1 has made no progress for 10 min"))
        woke = agent.wait_for_inbox(world.slug, cursor=cursor, poll_s=0, timeout_s=0, sleep=lambda s: None)
        assert [m["text"] for m in woke] == [ALERT_PREFIX + "render of Image 1 has made no progress for 10 min"]
        # the plain system line of the agent's own change does not
        _, cursor = agent.inbox(world.slug)
        # a long quiet spell: the server has not heard the session, so the card reads gone
        act = world.app.state.thm.agent_activity[world.slug]
        act.at -= agent_activity.GONE_S + 1
        for k in act.sessions:
            act.sessions[k] -= agent_activity.GONE_S + 1
        card = lambda: next(p for p in http.get("/api/projects").json() if p["slug"] == world.slug)  # noqa: E731
        assert card()["agent_gone"] is True
        # a watch across projects that holds this session polls as it: heard again, not "agent gone"
        agent.session = None
        try:
            agent.wait_for_any({world.slug: cursor}, sessions={world.slug: card()["agent_session"]["id"]}, poll_s=0, timeout_s=0, sleep=lambda s: None)
        except TimeoutError:
            pass
        else:
            raise AssertionError("its own session is not a wake")
        assert card()["agent_gone"] is False
        # without the session in hand, the same gone card is the wake it always was
        act.at -= agent_activity.GONE_S + 1
        for k in act.sessions:
            act.sessions[k] -= agent_activity.GONE_S + 1
        woke = agent.wait_for_any({world.slug: cursor}, poll_s=0, timeout_s=0, sleep=lambda s: None)
        assert woke["why"] == "agent gone"


def test_cli_file_fetches_a_take_and_cancel_withdraw_restart_verbs_exist(tmp_path, monkeypatch, capsys):
    import cli

    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    send = _transport(world.client)
    slug = world.slug
    http = world.client
    http.post(f"/api/projects/{slug}/commands/start_session", json={"notes": "go", "mode": "auto"})
    assert cli.main(["--server", BASE, "--project", slug, "connect"], transport=send) == 0
    capsys.readouterr()
    take = next(m["id"] for m in http.get(f"/api/projects/{slug}/bins/{world.image}").json()["media"])
    out = tmp_path / "take.png"
    assert cli.main(["--server", BASE, "--project", slug, "file", take, "--out", str(out)], transport=send) == 0
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    for verb in ("cancel", "withdraw", "restart-comfy"):
        assert verb in cli.HANDLERS
    assert "withdraw_change" in cli.COMMAND_TABLE
    # generate prints the job id line, and the drafts hint when drafts are on
    http.post(f"/api/projects/{slug}/commands/set_session_mode", json={"drafts": True})
    world.app.state.thm.jobs.launch_fn = hanging_launcher
    assert cli.main(["--server", BASE, "--project", slug, "inbox"], transport=send) == 0
    assert "MODE CHANGED" in capsys.readouterr().out
    assert cli.main(["--server", BASE, "--project", slug, "generate", "--bin", world.image], transport=send) == 0
    text = capsys.readouterr().out
    assert "job g_" in text and "cancel g_" in text and "drafts first" in text
