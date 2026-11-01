"""Message files: what a note carries that is not project media -- uploaded for the note alone,
named on the message, read by the agent, kept into a bin only on request."""

import io

from PIL import Image

from thm_core_fixtures import live_session
from thm_server_fixtures import seeded_world

AGENT = {"X-THM-Actor": "agent"}


def _png(color=(120, 60, 200), size=(32, 24)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _base(world) -> str:
    return f"/api/projects/{world.slug}"


def _go_live(world):
    project = world.store.load(world.slug)
    live_session(project)
    world.store.save(project)
    world.registry.invalidate(world.slug)


def _attach(world, name="extra arm.png", data=None, headers=None):
    return world.client.post(f"{_base(world)}/chat-files", files={"file": (name, _png() if data is None else data, "image/png")}, headers=headers or {})


def test_a_note_carries_a_file_that_is_in_no_bin(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    _go_live(world)
    before = world.client.get(base).json()
    media_before = world.client.get(f"{base}/media").json()["total"]

    r = _attach(world)
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["id"].startswith("f_") and f["name"] == "extra_arm.png" and f["type"] == "image"
    # nothing in the project moved: no media, no bin, no version, nothing under imports/
    after = world.client.get(base).json()
    assert after["version"] == before["version"] and [b["id"] for b in after["bins"]] == [b["id"] for b in before["bins"]]
    assert world.client.get(f"{base}/media").json()["total"] == media_before
    assert not (world.store.files_dir(world.slug) / "imports").exists()
    assert [p.name for p in world.store.chat_dir(world.slug).iterdir()] == [f"{f['id']}_extra_arm.png"]

    r = world.client.post(f"{base}/commands/post_message", json={"text": "see the third arm", "files": [f["id"]]})
    assert r.status_code == 200, r.text
    msg = r.json()["messages"][0]
    assert msg["files"] == [f] and msg["attachments"] == []

    # the agent reads it off the inbox line and fetches the bytes
    inbox = world.client.get(f"{base}/inbox", headers=AGENT).json()
    assert inbox["messages"][-1]["files"] == [f]
    got = world.client.get(f"{base}/chat-files/{f['id']}", headers=AGENT)
    assert got.status_code == 200 and got.content == _png() and got.headers["content-type"] == "image/png"
    assert world.client.get(f"{base}/feed").json()["entries"][-1]["message"]["files"] == [f]

    # a sent file stays; an unsent one is taken back
    r = world.client.delete(f"{base}/chat-files/{f['id']}")
    assert r.status_code == 409 and r.json()["code"] == "in_use"
    loose = _attach(world, "loose.png", _png((1, 2, 3))).json()
    assert world.client.delete(f"{base}/chat-files/{loose['id']}", headers=AGENT).json()["code"] == "wrong_actor"
    assert world.client.delete(f"{base}/chat-files/{loose['id']}").json() == {"file": loose["id"], "deleted": True}
    assert world.client.get(f"{base}/chat-files/{loose['id']}").status_code == 404


def test_keep_makes_a_note_file_project_media(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    _go_live(world)
    f = _attach(world, "plate.png", _png((9, 8, 7))).json()

    r = world.client.post(f"{base}/chat-files/{f['id']}/keep", json={"bin_name": "uploads"})
    assert r.status_code == 200, r.text
    kept = r.json()
    assert kept["bin"]["name"] == "uploads" and kept["media"]["origin"]["type"] == "upload"
    assert kept["media"]["path"].endswith("_plate.png") and "/imports/" in kept["media"]["path"]
    assert kept["media"]["id"] in world.client.get(f"{base}/bins/{kept['bin']['id']}").json()["bin"]["media"]
    # the note keeps its own copy
    assert world.client.get(f"{base}/chat-files/{f['id']}").status_code == 200

    # the agent keeps one too, into a bin by id
    g = _attach(world, "other.png", _png((4, 4, 4))).json()
    r = world.client.post(f"{base}/chat-files/{g['id']}/keep", json={"bin": world.mara}, headers=AGENT)
    assert r.status_code == 200 and r.json()["bin"]["id"] == world.mara and r.json()["change"]["actor"] == "agent"


def test_chat_file_refusals(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)

    r = _attach(world, "notes.txt", b"hello")
    assert r.status_code == 422 and r.json()["code"] == "bad_value"
    assert _attach(world, "empty.png", b"").status_code == 422
    # the agent only inside a live session, like an upload
    r = _attach(world, headers=AGENT)
    assert r.status_code == 409 and r.json()["code"] == "no_session"
    assert not world.store.chat_dir(world.slug).exists() or not any(world.store.chat_dir(world.slug).iterdir())

    for bad in ("f_nope", "f_00000000", "..%2F..%2Fx"):
        assert world.client.get(f"{base}/chat-files/{bad}").status_code == 404
    r = world.client.post(f"{base}/chat-files/f_00000000/keep", json={})
    assert r.status_code == 404 and r.json()["code"] == "unknown_id"

    _go_live(world)
    r = world.client.post(f"{base}/commands/post_message", json={"text": "look", "files": ["f_00000000"]})
    assert r.status_code >= 400 and r.json()["code"] == "unknown_id"
    assert all(m["text"] != "look" for m in world.client.get(f"{base}/inbox").json()["messages"])


def test_private_project_chat_files_are_closed_to_the_agent(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    f = _attach(world).json()
    project = world.store.load(world.slug)
    project.private = True
    world.store.save(project)
    world.registry.invalidate(world.slug)

    r = world.client.get(f"{base}/chat-files/{f['id']}", headers=AGENT)
    assert r.status_code == 403 and r.json()["code"] == "private_project"
    assert world.client.get(f"{base}/chat-files/{f['id']}").status_code == 200


def test_a_file_alone_is_a_note_and_a_card_reply(tmp_path):
    from thm_core.models import Change, ChangeTarget, Decision

    world = seeded_world(tmp_path)
    base = _base(world)
    _go_live(world)

    # a note with nothing in it is still refused; a picture alone is a note
    assert world.client.post(f"{base}/commands/post_message", json={"text": "  "}).json()["code"] == "bad_value"
    f = _attach(world).json()
    r = world.client.post(f"{base}/commands/post_message", json={"text": "", "files": [f["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["messages"][0]["files"] == [f]

    # a card answered with a file and no words is a reply that carries the file
    project = world.store.load(world.slug)
    world.store.append_change(project, Change(
        id="c_card", ts="2026-09-06T10:00:00Z", actor="agent", command="set_bin_field",
        target=ChangeTarget(type="project", id=project.id), description="a change", decision=Decision(status="pending"),
    ))
    world.registry.invalidate(world.slug)
    g = _attach(world, "marked.png", _png((5, 5, 5))).json()
    r = world.client.post(f"{base}/changes/c_card/decision", json={"action": "approve", "files": [g["id"]]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["change"]["decision"]["status"] == "replied" and out["change"]["decision"]["files"] == [g]
    assert out["messages"][-1]["files"] == [g]
    # the agent reads it as an open note
    inbox = world.client.get(f"{base}/inbox", headers=AGENT).json()
    assert inbox["messages"][-1]["files"] == [g]
