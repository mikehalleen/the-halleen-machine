"""Open notes: what the user said that the agent has not accounted for. Reading the inbox moves a
cursor and closes nothing; a note leaves the list only when the agent says what it did about it,
and the agent's watch does not block while one is open."""

import sys
from pathlib import Path

from thm_core_fixtures import live_session
from thm_server_fixtures import seeded_world

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))

AGENT = {"X-THM-Actor": "agent"}


def _base(world) -> str:
    return f"/api/projects/{world.slug}"


def _go_live(world, tracked=True):
    project = world.store.load(world.slug)
    live_session(project).notes_tracked = tracked
    world.store.save(project)
    world.registry.invalidate(world.slug)


def _note(world, text):
    r = world.client.post(f"{_base(world)}/commands/post_message", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["messages"][0]["id"]


def _open(world):
    return [n["id"] for n in world.client.get(f"{_base(world)}/notes", headers=AGENT).json()["notes"]]


def test_a_read_note_stays_open_until_the_agent_answers_it(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    _go_live(world)
    a, b, c = _note(world, "fix the extra arm"), _note(world, "car starts fast then slows"), _note(world, "more erratic driving")

    # the agent reads all three: the cursor is at the end, and all three are still owed
    cursor = world.client.get(f"{base}/inbox", headers=AGENT).json()["cursor"]
    assert world.client.get(f"{base}/inbox", params={"cursor": cursor}, headers=AGENT).json()["messages"] == []
    assert _open(world) == [a, b, c]
    assert world.client.get(base).json()["open_notes"] == 3
    assert next(p for p in world.client.get("/api/projects").json() if p["slug"] == world.slug)["open_notes"] == 3
    feed = {e["message"]["id"]: e for e in world.client.get(f"{base}/feed").json()["entries"]}
    assert feed[a]["open"] is True and feed[a]["answer"] is None

    r = world.client.post(f"{base}/commands/answer_notes", json={"notes": [a, c], "did": "quarter re-render queued"}, headers=AGENT)
    assert r.status_code == 200, r.text
    assert r.json()["messages"] == [] and r.json()["change"] is None  # no feed line: the note carries it
    assert _open(world) == [b] and world.client.get(base).json()["open_notes"] == 1
    feed = {e["message"]["id"]: e for e in world.client.get(f"{base}/feed").json()["entries"]}
    assert feed[a]["answer"] == "quarter re-render queued" and feed[a]["open"] is False and feed[b]["open"] is True

    # a reply typed on a card is a note too
    r = world.client.post(f"{base}/commands/set_bin_field", json={"bin": world.video, "field": "length_s", "value": 8, "card": True}, headers=AGENT)
    change = r.json()["change"]["id"]
    world.client.post(f"{base}/changes/{change}/decision", json={"action": "reply", "note": "make it 6"})
    assert len(_open(world)) == 2

    # refusals: the user does not answer for the agent, and the id must be a note of this session
    assert world.client.post(f"{base}/commands/answer_notes", json={"notes": [b], "did": "x"}).json()["code"] == "wrong_actor"
    assert world.client.post(f"{base}/commands/answer_notes", json={"notes": ["msg_nope"], "did": "x"}, headers=AGENT).json()["code"] == "unknown_id"
    assert world.client.post(f"{base}/commands/answer_notes", json={"notes": [b], "did": " "}, headers=AGENT).json()["code"] == "bad_value"


def test_no_session_owes_nothing_and_an_old_session_owes_only_recent_notes(tmp_path):
    world = seeded_world(tmp_path)
    assert _open(world) == [] and world.client.get(_base(world)).json()["open_notes"] == 0

    _go_live(world, tracked=False)
    recent = _note(world, "this one is from just now")
    # a note from long before the newest line of the log is not dug up
    project = world.store.load(world.slug)
    msgs = world.store.read_messages(project)
    old = msgs[-1].model_copy(update={"id": "msg_old00001", "ts": "2020-01-01T00:00:00Z", "text": "ancient"})
    path = world.store.files_dir(world.slug) / "messages.jsonl"
    path.write_text(old.model_dump_json() + "\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    world.registry.invalidate(world.slug)
    assert _open(world) == [recent]


def test_watch_does_not_block_while_notes_are_open(tmp_path):
    import thm_client

    world = seeded_world(tmp_path)
    _go_live(world)
    a = _note(world, "do the thing")

    def transport(method, url, body, headers):
        r = world.client.request(method, url.replace("http://test", ""), content=body, headers=headers)
        return r.status_code, r.content, dict(r.headers)

    client = thm_client.ThmClient("http://test", transport=transport)
    client.session = world.client.get(_base(world)).json()["agent_session"]["id"]  # the session's own agent
    _, cursor = client.inbox(world.slug)  # read: nothing new past the cursor now

    woke = client.wait_for_inbox(world.slug, cursor=cursor, timeout_s=0)
    assert [m["id"] for m in woke] == [a] and woke[0]["open"] is True
    any_ = client.wait_for_any({world.slug: cursor}, timeout_s=0)
    assert any_["slug"] == world.slug and any_["why"] == "1 open note(s)"

    client.session = world.client.get(_base(world)).json()["agent_session"]["id"]
    client.answer_notes(world.slug, [a], "done in take 4")
    try:
        client.wait_for_inbox(world.slug, cursor=client.cursor, timeout_s=0)
        raise AssertionError("watch should have blocked")
    except TimeoutError:
        pass
