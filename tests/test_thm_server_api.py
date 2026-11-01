"""thm_server REST: projects, bins, commands, decisions, inbox, workflows."""

import json
import os
import time
from pathlib import Path

import pytest

from thm_core import BackupSettings, CoreSettings, MediaOrigin, adopt_media
from thm_core.config import REPO_ROOT
from thm_core_fixtures import bind, live_session
from thm_server import create_app
from thm_server_fixtures import seeded_world, write_png


@pytest.fixture
def world(tmp_path):
    return seeded_world(tmp_path)


def _create_bin(world, **body) -> str:
    r = world.client.post(f"/api/projects/{world.slug}/commands/create_bin", json=body)
    assert r.status_code == 200, r.text
    name = body.get("bin_name")
    bins = world.client.get(f"/api/projects/{world.slug}/bins").json()
    return next(b["id"] for b in bins if (b["name"] == name if name else b["name"].startswith("bin-")))


def test_health(world):
    r = world.client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["projects_dir"].endswith("projects")


def test_projects_list_meta_and_thumbs(world):
    r = world.client.get("/api/projects")
    assert r.status_code == 200
    cards = r.json()
    assert len(cards) == 1
    card = cards[0]
    assert (card["slug"], card["name"], card["bin_count"], card["video_count"], card["image_count"]) == (world.slug, "Rail Dawn Promo", 3, 0, 3)
    assert card["hero"] == {"id": world.take, "type": "image"}
    assert card["modified"].endswith("Z") and card["version"] >= 1


def test_project_detail_shape(world):
    r = world.client.get(f"/api/projects/{world.slug}")
    assert r.status_code == 200
    body = r.json()
    assert "media_by_id" not in body and "shots" not in body and "items" not in body
    # newest first by what each bin last gained: the take, then Mara's uploads, then the empty video bin
    assert [b["id"] for b in body["bins"]] == [world.image, world.mara, world.video]
    image, mara, video = body["bins"]
    assert image["selected_media"]["id"] == world.take and image["take_count"] == 1 and image["kind"] == "image"
    assert image["media_types"] == ["image"]
    assert mara["selected_media"]["id"] == world.m2 and mara["take_count"] == 0  # the newest arrival leads
    assert video["selected_media"] is None and video["kind"] == "video" and video["length_s"] == 5.0
    assert body["pending_changes"] == 0
    assert body["media_count"] == 3
    assert body["agent_session"] is None
    # the tag vocabulary: the defaults, then whatever a bin carries
    assert body["tags"] == ["character", "pose", "location", "clip", "keyframe"]
    r = world.client.post(f"/api/projects/{world.slug}/commands/set_bin_field", json={"bin": world.mara, "field": "tags", "value": ["Character", "guitar"]})
    assert r.status_code == 200, r.text
    body = world.client.get(f"/api/projects/{world.slug}").json()
    assert body["tags"] == ["character", "pose", "location", "clip", "keyframe", "guitar"]
    assert next(b for b in body["bins"] if b["id"] == world.mara)["tags"] == ["character", "guitar"]
    assert [b["id"] for b in world.client.get(f"/api/projects/{world.slug}/bins").json()] == [world.image, world.mara, world.video]


def test_bin_detail_references_sub_labels_and_capacity(world):
    r = world.client.get(f"/api/projects/{world.slug}/bins/{world.video}")
    assert r.status_code == 200
    body = r.json()
    refs = body["references"]
    assert [x["sub_label"] for x in refs] == ["start", "end"]
    assert [x["handle"] for x in refs] == ["mara", "mara.2"]
    assert all(x["active"] and not x["missing"] and x["bin_name"] == "Mara" and x["bin_id"] == world.mara for x in refs)
    assert refs[0]["media"]["id"] == world.m1
    assert body["capacity"]["image_slots"] == 2 and body["capacity"]["supports_start_frame"] and body["capacity"]["kind"] == "video"
    assert body["workflow"]["name"] == "vid.json" and body["kind"] == "video"
    assert body["media"] == [] and body["selected"] is None
    assert body["project_name"] == "Rail Dawn Promo"

    r = world.client.get(f"/api/projects/{world.slug}/bins/{world.image}")
    body = r.json()
    assert [t["id"] for t in body["media"]] == [world.take] and body["media"][0]["take"] == 1
    assert body["selected"] == world.take
    assert body["references"] == []
    assert body["capacity"]["slot_node_ids"] == ["10", "11"] and body["kind"] == "image"

    # a bin with no workflow yet: no capacity, no kind
    empty = _create_bin(world, bin_name="Files", workflow="")
    body = world.client.get(f"/api/projects/{world.slug}/bins/{empty}").json()
    assert body["workflow"] is None and body["kind"] is None and body["capacity"]["found"] is False


def test_bin_detail_marks_beyond_capacity_unused(world):
    project = world.store.load(world.slug)
    extra = adopt_media(project, write_png(world.media_dir / "d.png", (300, 300), (9, 9, 9)), origin=MediaOrigin(type="upload", bin=world.mara))
    project.bins_by_id[world.mara].media.append(extra.id)
    bind(project, world.video, extra.id)
    world.store.save(project)
    world.registry.invalidate(world.slug)
    body = world.client.get(f"/api/projects/{world.slug}/bins/{world.video}").json()
    third = body["references"][2]
    assert (third["sub_label"], third["active"], third["ordinal"]) == ("unused", False, 3)


def test_bin_unknown_404(world):
    r = world.client.get(f"/api/projects/{world.slug}/bins/b_nope")
    assert r.status_code == 404 and r.json()["code"] == "unknown_id"
    assert world.client.get(f"/api/projects/{world.slug}/shots/{world.image}").status_code == 404
    assert world.client.get(f"/api/projects/{world.slug}/items").status_code == 404


def test_delete_media_over_rest_removes_orphaned_upload_file(world):
    base = f"/api/projects/{world.slug}/commands"
    # world.m1/m2 are seeded outside imports/ (like an externally-indexed file); a real upload
    # through the upload route is what actually lands under imports/ and is safe to unlink
    png = write_png(world.media_dir / "fresh.png", (64, 64), (5, 5, 5))
    with open(png, "rb") as f:
        up = world.client.post(f"/api/projects/{world.slug}/uploads", files={"file": ("fresh.png", f, "image/png")}, data={"bin": world.mara})
    assert up.status_code == 200, up.text
    media_id = up.json()["media"]["id"]
    upload_path = Path(up.json()["media"]["path"])
    assert upload_path.is_file()

    r = world.client.post(base + "/delete_media", json={"media": media_id})
    assert r.status_code == 200, r.text
    assert not upload_path.is_file()  # an orphaned upload's file is cleaned up
    assert media_id not in world.client.get(f"/api/projects/{world.slug}/bins/{world.mara}").json()["bin"]["media"]
    assert world.client.get(f"/api/projects/{world.slug}/media/{media_id}").status_code == 404

    # a generated take's real file on disk is never touched
    take_path = Path(world.registry.get(world.slug).media_by_id[world.take].path)
    assert take_path.is_file()
    r = world.client.post(base + "/delete_media", json={"media": world.take})
    assert r.status_code == 200, r.text
    assert take_path.is_file()

    r = world.client.post(base + "/delete_media", json={"media": "m_nope"})
    assert r.status_code == 404 and r.json()["code"] == "unknown_id"


def test_delete_bin_over_rest_cascades_and_keeps_referenced_media(world):
    base = f"/api/projects/{world.slug}/commands"
    # an upload into Mara that nothing references: its file goes with the bin
    png = write_png(world.media_dir / "loose.png", (64, 64), (6, 6, 6))
    with open(png, "rb") as f:
        up = world.client.post(f"/api/projects/{world.slug}/uploads", files={"file": ("loose.png", f, "image/png")}, data={"bin": world.mara})
    loose = up.json()["media"]
    r = world.client.post(base + "/delete_bin", json={"bin": world.mara})
    assert r.status_code == 200, r.text
    assert world.client.get(f"/api/projects/{world.slug}/bins/{world.mara}").status_code == 404
    # m1 and m2 are bound on Video 1, so they survive and the references stay
    refs = world.client.get(f"/api/projects/{world.slug}/bins/{world.video}").json()["references"]
    assert [x["media"]["id"] for x in refs] == [world.m1, world.m2] and refs[0]["bin_id"] is None and refs[0]["bin_name"] == "mara"
    assert world.client.get(f"/api/projects/{world.slug}/media/{world.m1}").status_code == 200
    assert world.client.get(f"/api/projects/{world.slug}/media/{loose['id']}").status_code == 404
    assert not Path(loose["path"]).is_file()
    r = world.client.post(base + "/delete_bin", json={"bin": "b_nope"})
    assert r.status_code == 404 and r.json()["code"] == "unknown_id"


def test_delete_bin_over_rest_refuses_while_rendering(world):
    base = f"/api/projects/{world.slug}/commands"
    from thm_server.jobs import Job, JobPaths

    st = world.app.state.thm
    st.jobs._jobs["g_live"] = Job(id="g_live", slug=world.slug, kind="image", paths=JobPaths(Path("a"), Path("b"), Path("c")), bin=world.image, status="running")
    r = world.client.post(base + "/delete_bin", json={"bin": world.image})
    assert r.status_code == 409 and r.json()["code"] == "bin_busy"
    st.jobs._jobs["g_live"].status = "done"
    r = world.client.post(base + "/delete_bin", json={"bin": world.image})
    assert r.status_code == 200, r.text
    assert world.client.get(f"/api/projects/{world.slug}/bins/{world.image}").status_code == 404
    assert world.client.get(f"/api/projects/{world.slug}/media/{world.take}").status_code == 404
    assert [b["id"] for b in world.client.get(f"/api/projects/{world.slug}").json()["bins"]] == [world.mara, world.video]


def test_delete_project_over_rest(world):
    from thm_server.jobs import Job, JobPaths

    st = world.app.state.thm
    project_path = st.store.project_path(world.slug)
    files_dir = st.store.files_dir(world.slug)
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "note.txt").write_text("x", encoding="utf-8")
    assert project_path.is_file()

    st.jobs._jobs["g_live"] = Job(id="g_live", slug=world.slug, kind="image", paths=JobPaths(Path("a"), Path("b"), Path("c")), bin=world.image, status="running")
    r = world.client.delete(f"/api/projects/{world.slug}")
    assert r.status_code == 409 and r.json()["code"] == "project_busy"
    st.jobs._jobs["g_live"].status = "done"

    r = world.client.delete(f"/api/projects/{world.slug}")
    assert r.status_code == 200 and r.json() == {"deleted": world.slug}
    assert not project_path.exists() and not files_dir.exists()
    assert st.jobs.get("g_live") is None
    assert world.client.get(f"/api/projects/{world.slug}").status_code == 404
    assert world.client.delete(f"/api/projects/{world.slug}").status_code == 404
    assert world.slug not in [p["slug"] for p in world.client.get("/api/projects").json()]


def test_create_project_via_api(world):
    r = world.client.post("/api/projects", json={"name": "New One"})
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == "new-one" and body["bins"] == [] and body["comfy"]["api_base"] == world.settings.comfy.api_base
    assert "new-one" in world.store.list_projects()
    assert world.client.post("/api/projects", json={"name": "   "}).status_code == 422
    assert world.client.post("/api/projects", json={"name": "x", "extra": 1}).status_code == 422


def test_unknown_project_404_and_1x_file_409(world):
    r = world.client.get("/api/projects/nope")
    assert r.status_code == 404 and r.json()["code"] == "unknown_project"
    old = Path(world.settings.projects_dir) / "old.json"
    old.write_text(json.dumps({"schema_version": 2, "project": {}, "sequences": {}}), encoding="utf-8")
    r = world.client.get("/api/projects/old")
    assert r.status_code == 409 and r.json()["code"] == "schema_version"
    assert [c["slug"] for c in world.client.get("/api/projects").json()] == [world.slug]


def test_create_bin_command_returns_change_and_bumps_version(world):
    before = world.client.get(f"/api/projects/{world.slug}").json()["version"]
    r = world.client.post(f"/api/projects/{world.slug}/commands/create_bin", json={"bin_name": "Image 2", "workflow": "img.json"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["change"]["description"] == "added Image 2"
    assert body["change"]["actor"] == "user"
    assert body["project_version"] > before
    after = world.client.get(f"/api/projects/{world.slug}").json()
    assert len(after["bins"]) == 4 and after["version"] == body["project_version"]
    # an empty bin is the newest thing in the project, so it leads the list
    assert after["bins"][0]["name"] == "Image 2" and after["bins"][0]["id"].startswith("b_")


def test_bind_reference_with_position_over_rest(world):
    base = f"/api/projects/{world.slug}/commands"
    r = world.client.post(base + "/bind_reference", json={"bin": world.image, "media": world.m1})
    assert r.status_code == 200, r.text
    first_id = world.client.get(f"/api/projects/{world.slug}/bins/{world.image}").json()["references"][0]["id"]
    r = world.client.post(base + "/bind_reference", json={"bin": world.image, "media": world.m2, "position": 0})
    assert r.status_code == 200, r.text
    refs = world.client.get(f"/api/projects/{world.slug}/bins/{world.image}").json()["references"]
    assert [x["media"]["id"] for x in refs] == [world.m2, world.m1]
    assert refs[1]["id"] == first_id
    # img.json has 2 image slots and both are already used; a third bind refuses on capacity,
    # so the bad-position case needs a fresh bin with room to spare
    fresh = _create_bin(world, workflow="img.json")
    world.client.post(base + "/bind_reference", json={"bin": fresh, "media": world.m1})
    r = world.client.post(base + "/bind_reference", json={"bin": fresh, "media": world.m2, "position": 5})
    assert r.status_code == 422 and r.json()["code"] == "bad_value"


def test_bind_reference_takes_another_bins_take_over_rest(world):
    """A take is bindable like any other media; its handle is its bin's name."""
    base = f"/api/projects/{world.slug}/commands"
    fresh = _create_bin(world, workflow="img.json")
    r = world.client.post(base + "/bind_reference", json={"bin": fresh, "media": world.take})
    assert r.status_code == 200, r.text
    ref = world.client.get(f"/api/projects/{world.slug}/bins/{fresh}").json()["references"][0]
    assert ref["media"]["id"] == world.take and ref["bin_id"] == world.image and ref["bin_name"] == "Image 1"
    assert ref["handle"] == "image-1"
    # and the bin's own take
    r = world.client.post(base + "/bind_reference", json={"bin": world.image, "media": world.take})
    assert r.status_code == 200 and r.json()["change"]["description"] == "Image 1 - bound .image-1"


def test_media_list_newest_first(world):
    r = world.client.get(f"/api/projects/{world.slug}/media")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["items"]]
    assert ids.index(world.m2) < ids.index(world.m1)
    r = world.client.get(f"/api/projects/{world.slug}/media", params={"bin": world.mara})
    assert [m["id"] for m in r.json()["items"]] == [world.m2, world.m1]


def test_reorder_references_over_rest(world):
    base = f"/api/projects/{world.slug}/commands"
    before = world.client.get(f"/api/projects/{world.slug}/bins/{world.video}").json()["references"]
    ids = [r["id"] for r in before]
    r = world.client.post(base + "/reorder_references", json={"bin": world.video, "order": list(reversed(ids))})
    assert r.status_code == 200, r.text
    after = world.client.get(f"/api/projects/{world.slug}/bins/{world.video}").json()["references"]
    assert [r["id"] for r in after] == list(reversed(ids))
    r = world.client.post(base + "/reorder_references", json={"bin": world.video, "order": [ids[0]]})
    assert r.status_code == 409 and r.json()["code"] == "bad_order"
    assert world.client.post(base + "/reorder_shots", json={"order": []}).status_code == 404


def test_rebind_reference_over_rest(world):
    base = f"/api/projects/{world.slug}/commands"
    ref = world.client.get(f"/api/projects/{world.slug}/bins/{world.video}").json()["references"][0]
    project = world.registry.get(world.slug)
    other = adopt_media(project, write_png(world.media_dir / "e.png", (64, 64), (7, 7, 7)), origin=MediaOrigin(type="upload", bin=world.mara))
    world.store.save(project)
    world.registry.invalidate(world.slug)
    world.client.post(base + "/add_media_to_bin", json={"bin": world.mara, "media": other.id})
    r = world.client.post(base + "/rebind_reference", json={"bin": world.video, "reference": ref["id"], "media": other.id})
    assert r.status_code == 200, r.text
    updated = world.client.get(f"/api/projects/{world.slug}/bins/{world.video}").json()["references"][0]
    assert updated["media"]["id"] == other.id and updated["handle"] == ref["handle"]


def test_rename_bin_over_rest_rewrites_the_referencing_prompt(world):
    base = f"/api/projects/{world.slug}/commands"
    r = world.client.post(base + "/set_bin_field", json={"bin": world.mara, "field": "name", "value": "Mara Vane"})
    assert r.status_code == 200, r.text
    assert r.json()["change"]["description"] == "Mara renamed to Mara Vane (1 bin references it)"
    video = world.client.get(f"/api/projects/{world.slug}/bins/{world.video}").json()
    assert video["bin"]["prompt"] == ".mara-vane at the rail" and [x["handle"] for x in video["references"]] == ["mara-vane", "mara-vane.2"]


def test_generate_over_rest_refuses_without_comfy_configured(world):
    base = f"/api/projects/{world.slug}/commands"
    before = len(world.client.get(f"/api/projects/{world.slug}/generations").json())
    r = world.client.post(base + "/generate", json={"bin": world.mara})
    assert r.status_code == 409 and r.json()["code"] == "comfy_unconfigured"  # no output_root configured on this seeded world
    gen = world.client.get(f"/api/projects/{world.slug}/generations").json()
    assert len(gen) == before  # refused before the command even executed
    assert world.client.post(base + "/generate_reference", json={"item": world.mara}).status_code == 404


def test_agent_without_session_409_no_session(world):
    r = world.client.post(
        f"/api/projects/{world.slug}/commands/create_bin",
        json={"workflow": "img.json"},
        headers={"X-THM-Actor": "agent"},
    )
    assert r.status_code == 409 and r.json()["code"] == "no_session"


def test_error_mapping(world):
    base = f"/api/projects/{world.slug}/commands"
    r = world.client.post(f"{base}/set_bin_field", json={"bin": "b_nope", "field": "prompt", "value": "x"})
    assert r.status_code == 404 and r.json()["code"] == "unknown_id"
    r = world.client.post(f"{base}/create_bin", json={"length_s": "long"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_payload"
    r = world.client.post(f"{base}/create_bin", json={"workflow": "nope.json"})
    assert r.status_code == 409 and r.json()["code"] == "unknown_workflow"
    r = world.client.post(f"{base}/create_bin", json={"workflow": "img.json", "bogus": 1})
    assert r.status_code == 422 and r.json()["code"] == "invalid_payload"
    r = world.client.post(f"{base}/nope", json={})
    assert r.status_code == 404 and r.json()["code"] == "unknown_command"
    r = world.client.post(f"{base}/create_bin", json=[1, 2])
    assert r.status_code == 422 and r.json()["code"] == "invalid_payload"
    r = world.client.post(f"{base}/create_bin", json={"workflow": "img.json"}, headers={"X-THM-Actor": "robot"})
    assert r.status_code == 400 and r.json()["code"] == "bad_actor"
    r = world.client.post(f"{base}/post_message", json={"text": "hi"}, headers={"X-THM-Inbox-Cursor": "abc"})
    assert r.status_code == 400 and r.json()["code"] == "bad_cursor"


def test_decision_flow_approve(world):
    project = world.store.load(world.slug)
    live_session(project)
    world.store.save(project)
    world.registry.invalidate(world.slug)

    r = world.client.post(
        f"/api/projects/{world.slug}/commands/set_bin_field",
        json={"bin": world.video, "field": "length_s", "value": 8, "card": True},
        headers={"X-THM-Actor": "agent"},
    )
    assert r.status_code == 200, r.text
    change = r.json()["change"]
    assert change["actor"] == "agent" and change["decision"]["status"] == "pending"
    assert change["description"] == "Video 1 - length 5s to 8s"
    assert world.client.get(f"/api/projects/{world.slug}").json()["pending_changes"] == 1
    assert [c["id"] for c in world.client.get(f"/api/projects/{world.slug}/changes", params={"status": "pending"}).json()] == [change["id"]]

    r = world.client.post(f"/api/projects/{world.slug}/changes/{change['id']}/decision", json={"action": "approve", "note": "good"})
    assert r.status_code == 200, r.text
    assert r.json()["change"]["decision"]["status"] == "approved"
    assert world.client.get(f"/api/projects/{world.slug}").json()["pending_changes"] == 0
    messages = world.client.get(f"/api/projects/{world.slug}/messages").json()
    assert messages[-1]["kind"] == "decision" and messages[-1]["status"] == "approved" and messages[-1]["note"] == "good"

    r = world.client.post(f"/api/projects/{world.slug}/changes/{change['id']}/decision", json={"action": "nuke"})
    assert r.status_code == 422


def test_inbox_cursor(world):
    r = world.client.post(f"/api/projects/{world.slug}/commands/post_message", json={"text": "hands are wrong"})
    assert r.status_code == 200 and r.json()["change"] is None and r.json()["messages"][0]["kind"] == "user"
    r = world.client.get(f"/api/projects/{world.slug}/inbox", params={"cursor": 0})
    body = r.json()
    assert [m["text"] for m in body["messages"]] == ["hands are wrong"] and body["cursor"] == 1
    body = world.client.get(f"/api/projects/{world.slug}/inbox", params={"cursor": 1}).json()
    assert body["messages"] == [] and body["cursor"] == 1


def test_post_message_context_names_what_the_user_was_looking_at(world):
    """A note posted from a bin carries the bin and its lead media; the server fills the names."""
    base = f"/api/projects/{world.slug}/commands/post_message"
    r = world.client.post(base, json={"text": "this take needs a wider shot", "context": {"bin": world.image, "media": world.take}})
    assert r.status_code == 200, r.text
    msg = r.json()["messages"][0]
    assert msg["context"] == {"bin": world.image, "bin_name": "Image 1", "media": world.take, "media_label": "take 1"}
    # the agent's inbox carries it too
    inbox = world.client.get(f"/api/projects/{world.slug}/inbox", params={"cursor": 0}).json()
    assert inbox["messages"][-1]["context"]["media"] == world.take
    # a bin alone is fine; an unknown bin or media is refused
    assert world.client.post(base, json={"text": "this bin", "context": {"bin": world.mara}}).status_code == 200
    assert world.client.post(base, json={"text": "x", "context": {"bin": "b_nope"}}).status_code == 404
    assert world.client.post(base, json={"text": "x", "context": {"bin": world.image, "media": "m_nope"}}).status_code == 404


def test_external_edit_is_picked_up(world):
    path = world.store.project_path(world.slug)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["name"] = "Renamed By Hand"
    path.write_text(json.dumps(raw), encoding="utf-8")
    future = time.time() + 5
    os.utime(path, (future, future))
    assert world.client.get(f"/api/projects/{world.slug}").json()["name"] == "Renamed By Hand"


def test_workflows_endpoint_real_files(tmp_path):
    """The listing is workflows_image/ and workflows_video/, and kind is whichever folder a file
    sits in — capabilities are still scanned out of the file, so these are copies of real ones.
    The flat workflows/ folder is deliberately not listed."""
    import shutil

    from thm_core.workflow_names import workflow_kind_dirs

    wdir = tmp_path / "workflows"
    wdir.mkdir()
    kind_dirs = workflow_kind_dirs(wdir)
    for kind, name in (("image", "pixa-four-image_vague.json"), ("video", "i2v_base.json")):
        kind_dirs[kind].mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "workflows" / name, kind_dirs[kind] / name)
    (kind_dirs["image"] / "_ignored.json").write_text("{}", encoding="utf-8")
    (wdir / "unlisted.json").write_text("{}", encoding="utf-8")

    settings = CoreSettings(projects_dir=tmp_path / "p2", workflows_dir=wdir, backups=BackupSettings(throttle_seconds=0))
    app = create_app(settings, web_dist=tmp_path / "nodist")
    from starlette.testclient import TestClient

    client = TestClient(app)
    r = client.get("/api/workflows")
    assert r.status_code == 200
    infos = {w["name"]: w for w in r.json()}
    assert not any(name.startswith("_") for name in infos)
    assert "unlisted.json" not in infos
    pixa = infos["pixa-four-image_vague.json"]
    assert (pixa["kind"], pixa["image_slots"], pixa["addresses_by_token"]) == ("image", 4, True)
    i2v = infos["i2v_base.json"]
    assert i2v["kind"] == "video" and i2v["supports_start_frame"] and i2v["supports_end_frame"] and i2v["has_negative"]
    r = client.get("/api/workflows/i2v_base.json")
    assert r.status_code == 200 and r.json()["kind"] == "video"
    assert client.get("/api/workflows/nope.json").status_code == 404
    # second call is served from the mtime cache and is identical
    cache = app.state.thm.workflows_cache
    assert len(cache) == len(infos)
    assert client.get("/api/workflows").json() == list(r.json() for r in [client.get("/api/workflows")])[0]


def test_loras_endpoint_lists_files_relative_to_the_loras_dir(tmp_path):
    """Names are what a <lora:name:strength> tag takes: the path under [paths] loras, forward slashes."""
    from starlette.testclient import TestClient

    loras = tmp_path / "loras"
    (loras / "sub").mkdir(parents=True)
    (loras / "Style.safetensors").write_bytes(b"x" * 10)
    (loras / "sub" / "char.pt").write_bytes(b"xy")
    (loras / "notes.txt").write_text("no", encoding="utf-8")
    (loras / ".hidden.safetensors").write_bytes(b"x")
    settings = CoreSettings(projects_dir=tmp_path / "p", workflows_dir=tmp_path / "wf", loras_dir=loras, backups=BackupSettings(throttle_seconds=0))
    client = TestClient(create_app(settings, web_dist=tmp_path / "nodist"))
    assert client.get("/api/loras").json() == [{"name": "Style.safetensors", "size": 10}, {"name": "sub/char.pt", "size": 2}]
    unset = CoreSettings(projects_dir=tmp_path / "p", workflows_dir=tmp_path / "wf", backups=BackupSettings(throttle_seconds=0))
    assert TestClient(create_app(unset, web_dist=tmp_path / "nodist")).get("/api/loras").json() == []


def test_core_settings_read_loras_dir(tmp_path):
    from thm_core import load_core_settings

    cfg = tmp_path / "config.toml"
    cfg.write_text('[paths]\nloras = "models/loras"\n', encoding="utf-8")
    assert load_core_settings(cfg, repo_root=tmp_path).loras_dir == (tmp_path / "models" / "loras").resolve()
    cfg.write_text("[paths]\n", encoding="utf-8")
    assert load_core_settings(cfg, repo_root=tmp_path).loras_dir is None
