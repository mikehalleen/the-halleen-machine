"""thm_server session surface: uploads, the brief, the feed, decisions, blockers, message events."""

import io
from pathlib import Path

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


def _upload(world, name, data, **fields):
    return world.client.post(f"{_base(world)}/uploads", files={"file": (name, data, "image/png")}, data=fields)


# -- uploads --------------------------------------------------------------------


def test_upload_without_a_bin_mints_the_next_bin(tmp_path):
    world = seeded_world(tmp_path)
    r = _upload(world, "ref one.png", _png())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bin"]["name"] == "bin-1" and body["bin"]["workflow"] == ""
    media = body["media"]
    imports = world.store.files_dir(world.slug) / "imports"
    assert media["type"] == "image" and media["width"] == 32 and media["origin"] == {
        "type": "upload", "generation": None, "bin": body["bin"]["id"], "source": None, "start_s": None, "end_s": None, "at_s": None, "note": None,
    }
    assert media["path"].startswith(imports.resolve().as_posix()) and media["path"].endswith("_ref_one.png")
    assert body["change"]["command"] == "add_media_to_bin" and body["change"]["actor"] == "user"
    assert any(p.name.startswith(media["sha256"]) for p in (world.store.files_dir(world.slug) / "cache" / "thumbs").iterdir())

    detail = world.client.get(_base(world)).json()
    refs = next(b for b in detail["bins"] if b["id"] == body["bin"]["id"])
    assert refs["media"] == [media["id"]] and refs["selected"] == media["id"] and detail["version"] == body["project_version"]
    page = world.client.get(f"{_base(world)}/media", params={"bin": refs["id"]}).json()
    assert [m["id"] for m in page["items"]] == [media["id"]]

    # the same bytes under another name, into the same bin: the existing record, no second file, no change
    again = _upload(world, "copy.png", _png(), bin=refs["id"])
    assert again.status_code == 200 and again.json()["media"]["id"] == media["id"] and again.json()["change"] is None
    assert len(list(imports.iterdir())) == 1
    # a name that is not a bin yet mints it; a name that is joins it
    named = _upload(world, "n1.png", _png((1, 1, 1)), bin_name="Refs")
    assert named.json()["bin"]["name"] == "Refs"
    assert _upload(world, "n2.png", _png((2, 2, 2)), bin_name="refs").json()["bin"]["id"] == named.json()["bin"]["id"]
    # nothing given again: the next free number, not the one already taken
    assert _upload(world, "n3.png", _png((3, 3, 3))).json()["bin"]["name"] == "bin-2"

    # an explicit bin: straight into Mara
    into = _upload(world, "mara3.png", _png((9, 9, 9)), bin=world.mara)
    assert into.status_code == 200 and into.json()["bin"]["id"] == world.mara
    assert into.json()["media"]["id"] in world.client.get(f"{_base(world)}/bins/{world.mara}").json()["bin"]["media"]


def test_remove_upload_before_the_brief(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    imports = world.store.files_dir(world.slug) / "imports"
    fresh = _upload(world, "fresh.png", _png((7, 7, 7))).json()
    refs = fresh["bin"]["id"]
    shared = _upload(world, "mara-again.png", Path(world.media_dir / "a.png").read_bytes(), bin=refs).json()  # dedupes to Mara's m1
    assert shared["media"]["id"] == world.m1
    assert len(list(imports.iterdir())) == 1

    # an upload nothing else uses: out of the bin, record and file gone
    r = world.client.delete(f"{base}/uploads/{fresh['media']['id']}", params={"bin": refs})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True and r.json()["change"]["command"] == "remove_media_from_bin"
    assert not any(imports.iterdir())
    assert world.client.get(f"{base}/media/{fresh['media']['id']}").status_code == 404

    # a deduped upload that is also Mara's reference: out of brief-refs, record and file untouched
    r = world.client.delete(f"{base}/uploads/{world.m1}", params={"bin": refs})
    assert r.status_code == 200 and r.json()["deleted"] is False
    assert world.client.get(f"{base}/media/{world.m1}").status_code == 200
    assert world.client.get(f"{base}/bins/{refs}").json()["bin"]["media"] == []
    assert world.m1 in world.client.get(f"{base}/bins/{world.mara}").json()["bin"]["media"]

    r = world.client.delete(f"{base}/uploads/{world.m1}", params={"bin": refs})
    assert r.status_code == 409 and r.json()["code"] == "not_in_bin"
    assert world.client.delete(f"{base}/uploads/{world.m1}").status_code == 422  # the bin is required
    assert world.client.delete(f"{base}/uploads/{world.m1}", params={"bin": "b_nope"}).status_code == 404
    r = world.client.delete(f"{base}/uploads/m_x", params={"bin": refs}, headers=AGENT)
    assert r.status_code == 409 and r.json()["code"] == "wrong_actor"


def test_upload_refusals_keep_the_error_shape_and_leave_no_file(tmp_path):
    world = seeded_world(tmp_path)
    imports = world.store.files_dir(world.slug) / "imports"

    r = _upload(world, "notes.txt", b"hello")
    assert r.status_code == 422 and r.json()["code"] == "bad_value" and set(r.json()) == {"code", "detail", "extra"}

    r = world.client.post(f"{_base(world)}/uploads", data={"bin_name": "x"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_payload"

    r = _upload(world, "empty.png", b"")
    assert r.status_code == 422 and r.json()["code"] == "bad_value"

    # the agent uploads too, but only inside a live session
    r = world.client.post(f"{_base(world)}/uploads", files={"file": ("a.png", _png(), "image/png")}, headers=AGENT)
    assert r.status_code == 409 and r.json()["code"] == "no_session"

    r = _upload(world, "a.png", _png(), bin="b_nope")
    assert r.status_code == 404 and r.json()["code"] == "unknown_id"
    assert not imports.exists() or not any(imports.iterdir())

    r = world.client.post("/api/projects/nope/uploads", files={"file": ("a.png", _png(), "image/png")})
    assert r.status_code == 404 and r.json()["code"] == "unknown_project"

    # unknown routes now carry the same body shape
    r = world.client.get("/api/nothing/here")
    assert r.status_code == 404 and r.json()["code"] == "not_found"


# -- the brief ------------------------------------------------------------------


def test_start_session_over_rest_attaches_brief_media(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    a = _upload(world, "a.png", _png((1, 2, 3))).json()
    refs = a["bin"]["id"]
    b = _upload(world, "b.png", _png((4, 5, 6)), bin=refs).json()
    assert b["bin"]["id"] == refs

    r = world.client.post(f"{base}/commands/start_session", json={"notes": "finish Video 1 at 8s", "refs_bin": refs})
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert [m["kind"] for m in msgs] == ["user", "system"]
    assert msgs[0]["text"] == "finish Video 1 at 8s" and msgs[0]["attachments"] == [a["media"]["id"], b["media"]["id"]]
    assert msgs[1]["text"] == "notes sent with 2 reference files, waiting for an agent to connect"

    detail = world.client.get(base).json()
    assert detail["agent_session"]["status"] == "waiting"
    assert detail["brief"] == "" and detail["configured"] is False
    assert detail["blocker"] is None

    feed = world.client.get(f"{base}/feed").json()
    kinds = [e["message"]["kind"] for e in feed["entries"]]
    assert kinds == ["user", "system"]
    assert feed["entries"][1]["change"]["command"] == "start_session" and feed["entries"][1]["media"] is None
    assert feed["cursor"] == world.client.get(f"{base}/inbox").json()["cursor"] == 2
    assert world.client.get(f"{base}/feed", params={"since": 2}).json() == {"entries": [], "cursor": 2}


# -- agent changes, cards, decisions -------------------------------------------


def test_agent_change_feed_entry_and_decision(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    base = _base(world)
    _go_live(world)

    r = world.client.post(f"{base}/commands/set_bin_field", json={"bin": world.video, "field": "length_s", "value": 8, "card": True}, headers=AGENT)
    assert r.status_code == 200, r.text
    change = r.json()["change"]

    feed = world.client.get(f"{base}/feed").json()
    entry = feed["entries"][-1]
    assert entry["message"]["kind"] == "change" and entry["message"]["change"] == change["id"]
    assert entry["change"]["decision"]["status"] == "pending" and entry["change"]["description"] == "Video 1 - length 5s to 8s"
    assert entry["bin"] == {"id": world.video, "name": "Video 1"} and entry["media"] is None
    assert world.client.get(base).json()["pending_changes"] == 1

    # a bind carries the bound media as the card preview, on the image bin
    r = world.client.post(f"{base}/commands/bind_reference", json={"bin": world.image, "media": world.m1, "card": True}, headers=AGENT)
    assert r.status_code == 200, r.text
    entry = world.client.get(f"{base}/feed").json()["entries"][-1]
    assert entry["media"]["id"] == world.m1 and entry["bin"]["id"] == world.image

    # a render is never proposed: the take is the card, after it lands
    cursor = world.client.get(f"{base}/inbox").json()["cursor"]
    r = world.client.post(
        f"{base}/commands/generate", json={"bin": world.image}, headers={**AGENT, "X-THM-Propose": "1", "X-THM-Inbox-Cursor": str(cursor)}
    )
    assert r.status_code == 409 and r.json()["code"] == "propose_render", r.text
    assert world.client.get(base).json()["pending_changes"] == 2
    assert world.client.get(f"{base}/jobs").json() == []

    # approve the length change with a note: a decision line joins the feed with the updated change
    r = world.client.post(f"{base}/changes/{change['id']}/decision", json={"action": "approve", "note": "good"})
    assert r.status_code == 200, r.text
    entry = world.client.get(f"{base}/feed").json()["entries"][-1]
    assert entry["message"]["kind"] == "decision" and entry["message"]["status"] == "approved" and entry["message"]["note"] == "good"
    assert entry["change"]["id"] == change["id"] and entry["change"]["decision"]["status"] == "approved"
    assert entry["bin"]["name"] == "Video 1"

    # reject the bind: reverted, the card still points at the media it had bound
    bind_change = [e for e in world.client.get(f"{base}/feed").json()["entries"] if e["change"] and e["change"]["command"] == "bind_reference"][0]["change"]
    r = world.client.post(f"{base}/changes/{bind_change['id']}/decision", json={"action": "reject", "note": "not that one"})
    assert r.status_code == 200, r.text
    entry = world.client.get(f"{base}/feed").json()["entries"][-1]
    assert entry["change"]["decision"]["status"] == "rejected" and entry["media"]["id"] == world.m1
    image = world.client.get(f"{base}/bins/{world.image}").json()
    assert image["references"] == []
    assert world.client.get(base).json()["pending_changes"] == 0


def test_blocker_promotes_project_detail_until_answered(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    _go_live(world)
    r = world.client.post(f"{base}/commands/post_message", json={"text": "need the logo file", "kind": "blocker"}, headers=AGENT)
    assert r.status_code == 200, r.text
    blocker_id = r.json()["messages"][0]["id"]
    detail = world.client.get(base).json()
    assert detail["blocker"] and detail["blocker"]["id"] == blocker_id and detail["blocker"]["kind"] == "blocker"

    world.client.post(f"{base}/commands/post_message", json={"text": "uploading it now"})
    assert world.client.get(base).json()["blocker"] is None

    world.client.post(f"{base}/commands/post_message", json={"text": "still stuck", "kind": "blocker"}, headers=AGENT)
    assert world.client.get(base).json()["blocker"]["text"] == "still stuck"
    world.client.post(f"{base}/commands/end_session", json={})
    assert world.client.get(base).json()["blocker"] is None


def test_message_events_precede_the_project_event(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    q = world.app.state.thm.bus.subscribe(world.slug)
    r = world.client.post(f"{base}/commands/post_message", json={"text": "hello agent"})
    assert r.status_code == 200
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert [e[0] for e in events] == ["message", "project"]
    entry = events[0][1]
    assert entry["message"]["kind"] == "user" and entry["message"]["text"] == "hello agent" and entry["change"] is None
    assert events[0][2] is None and events[1][2] == r.json()["project_version"]

    # a decision event carries the decided change so a client can patch its card
    _go_live(world)
    r = world.client.post(f"{base}/commands/set_bin_field", json={"bin": world.video, "field": "length_s", "value": 3, "card": True}, headers=AGENT)
    change_id = r.json()["change"]["id"]
    while not q.empty():
        q.get_nowait()
    world.client.post(f"{base}/changes/{change_id}/decision", json={"action": "reject"})
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert events[0][0] == "message" and events[0][1]["message"]["kind"] == "decision"
    assert events[0][1]["change"]["id"] == change_id and events[0][1]["change"]["decision"]["status"] == "rejected"


def test_end_session_over_rest(tmp_path):
    world = seeded_world(tmp_path)
    base = _base(world)
    _go_live(world)
    r = world.client.post(f"{base}/commands/end_session", json={})
    assert r.status_code == 200 and r.json()["messages"][0]["text"] == "session ended"
    assert world.client.get(base).json()["agent_session"]["status"] == "ended"
    assert world.client.get(f"{base}/feed").json()["entries"][-1]["message"]["kind"] == "system"
    r = world.client.post(f"{base}/commands/end_session", json={})
    assert r.status_code == 409 and r.json()["code"] == "no_session"
    # the feed stays readable; the next session continues the project from its kickoff notes
    r = world.client.post(f"{base}/commands/start_session", json={"notes": "second pass"})
    assert r.status_code == 200 and [m["kind"] for m in r.json()["messages"]] == ["user", "system"]
    assert r.json()["messages"][1]["text"] == "notes sent, waiting for an agent to connect"
    assert world.client.get(base).json()["agent_session"]["status"] == "waiting"


def test_create_project_with_settings_and_edit_over_rest(tmp_path):
    world = seeded_world(tmp_path)
    body = {"name": "Manual Setup", "settings": {"width": 720, "height": 1280, "image_workflow": "img.json", "video_workflow": "vid.json", "image_prompt": "35mm grain"}}
    r = world.client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    detail = r.json()
    assert detail["configured"] is True and detail["defaults"]["image_prompt"] == "35mm grain" and detail["defaults"]["video_length_s"] == 5.0
    slug = detail["slug"]
    card = next(c for c in world.client.get("/api/projects").json() if c["slug"] == slug)
    assert card["canvas"] == "720x1280" and card["video_workflow"] == "vid.json"

    # a bad workflow refuses before anything is written
    r = world.client.post("/api/projects", json={"name": "Broken", "settings": {"image_workflow": "nope.json"}})
    assert r.status_code == 409 and r.json()["code"] == "unknown_workflow"
    assert not any(c["slug"] == "broken" for c in world.client.get("/api/projects").json())

    # a bin without a workflow takes the image default; a settings edit during a live session is reported
    r = world.client.post(f"/api/projects/{slug}/commands/create_bin", json={})
    assert r.status_code == 200 and world.client.get(f"/api/projects/{slug}").json()["bins"][0]["workflow"] == "img.json"
    r = world.client.post(f"/api/projects/{slug}/commands/create_bin", json={"workflow": "vid.json"})
    assert r.status_code == 200 and world.client.get(f"/api/projects/{slug}").json()["bins"][0]["length_s"] == 5.0
    project = world.store.load(slug)
    live_session(project)
    world.store.save(project)
    world.registry.invalidate(slug)
    r = world.client.post(f"/api/projects/{slug}/commands/set_project_settings", json={"video_prompt": "slow dolly", "project_name": "Manual Setup 2"})
    assert r.status_code == 200, r.text
    assert r.json()["change"]["description"] == "project settings - renamed to Manual Setup 2 - global prompts 1"
    assert r.json()["messages"][0]["text"] == "you: project settings - renamed to Manual Setup 2 - global prompts 1"
    detail = world.client.get(f"/api/projects/{slug}").json()
    assert detail["name"] == "Manual Setup 2" and detail["slug"] == slug and detail["defaults"]["video_prompt"] == "slow dolly"


def test_create_project_with_a_brief_from_the_harness(tmp_path):
    world = seeded_world(tmp_path)
    r = world.client.post("/api/projects", json={"name": "Harness First", "brief": "a teaser for the rail line"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["brief"] == "a teaser for the rail line" and body["agent_session"] is None
    feed = world.client.get(f"/api/projects/{body['slug']}/feed").json()
    assert [(e["message"]["kind"], e["message"]["text"]) for e in feed["entries"]] == [("user", "a teaser for the rail line")]
    # the user starts the first session in the app without retyping the brief
    r = world.client.post(f"/api/projects/{body['slug']}/commands/start_session", json={})
    assert r.status_code == 200 and r.json()["messages"][0]["text"] == "session started, waiting for an agent to connect"
