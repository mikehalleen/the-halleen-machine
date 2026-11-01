"""thm_server: a live session the server has not heard an agent on is ``agent_gone``, so the
bar can say so and the next agent's watch takes it over."""

from thm_server import agent_activity
from thm_server_fixtures import seeded_world

AGENT = {"X-THM-Actor": "agent"}
USER_HEADERS = {"X-THM-Actor": "user"}


def _age(act, seconds):
    """Make a project's agent read as not heard for ``seconds``: the last call, and every session it named."""
    act.at -= seconds
    for k in act.sessions:
        act.sessions[k] -= seconds


def _base(world) -> str:
    return f"/api/projects/{world.slug}"


def test_live_session_with_no_heartbeat_is_gone_until_an_agent_calls(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    card = lambda: next(p for p in world.client.get("/api/projects").json() if p["slug"] == world.slug)
    # no session: nothing to be gone from
    assert card()["agent_gone"] is False and world.client.get(base).json()["agent_gone"] is False
    world.client.post(f"{base}/commands/start_session", json={}, headers=USER_HEADERS)
    assert card()["agent_gone"] is False, "waiting is not gone: nobody was ever heard"
    world.client.post(f"{base}/commands/connect_session", json={"harness": "claude-code"}, headers=AGENT)
    assert card()["agent_gone"] is False and world.client.get(base).json()["agent_gone"] is False
    # while that agent is still heard, nobody else may connect: one agent per project
    r = world.client.post(f"{base}/commands/connect_session", json={"harness": "codex", "takeover": True}, headers=AGENT)
    assert r.status_code == 409 and r.json()["code"] == "session_active"
    # the heartbeat ages past the window: the harness closed without end
    _age(world.app.state.thm.agent_activity[world.slug], agent_activity.GONE_S + 1)
    assert card()["agent_gone"] is True and world.client.get(base).json()["agent_gone"] is True
    # now a connect takes the session over, and is itself the sign of life
    r = world.client.post(f"{base}/commands/connect_session", json={"harness": "codex"}, headers=AGENT)
    assert r.status_code == 200 and "reconnected via codex" in r.json()["change"]["description"]
    assert card()["agent_gone"] is False and card()["agent_session"]["harness"] == "codex"
    _age(world.app.state.thm.agent_activity[world.slug], agent_activity.GONE_S + 1)
    # a call naming the session is its agent's sign of life again
    sid = card()["agent_session"]["id"]
    world.client.get(f"{base}/inbox", headers=AGENT)
    assert card()["agent_gone"] is True, "an agent looking around without the session id is not its owner"
    world.client.get(f"{base}/inbox", headers={**AGENT, "X-THM-Session": sid})
    assert card()["agent_gone"] is False
    # a server restart forgets every heartbeat: live reads gone until the agent calls
    world.app.state.thm.agent_activity.clear()
    assert card()["agent_gone"] is True
    world.client.post(f"{base}/commands/end_session", json={}, headers=USER_HEADERS)
    assert card()["agent_gone"] is False


def test_the_activity_payload_carries_the_live_gone_answer(tmp_path):
    """The project detail's ``agent_gone`` is frozen at fetch time and a heartbeat is a read that
    never refetches it; the activity route (and the ``agent`` event) say it live."""
    world = seeded_world(tmp_path)
    base = _base(world)
    activity = lambda: world.client.get(f"{base}/agent/activity").json()
    assert activity()["gone"] is None, "no live session: nothing to be gone from"
    world.client.post(f"{base}/commands/start_session", json={}, headers=USER_HEADERS)
    assert activity()["gone"] is None
    world.client.post(f"{base}/commands/connect_session", json={"harness": "claude-code"}, headers=AGENT)
    assert activity()["gone"] is False
    _age(world.app.state.thm.agent_activity[world.slug], agent_activity.GONE_S + 1)
    assert activity()["gone"] is True
    sid = world.client.get(base).json()["agent_session"]["id"]
    world.client.get(f"{base}/inbox", headers={**AGENT, "X-THM-Session": sid})
    assert activity()["gone"] is False
