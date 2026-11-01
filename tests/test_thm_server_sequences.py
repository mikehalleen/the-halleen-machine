"""Sequences over HTTP: the read shapes, the commands through the command route, the export."""

import shutil
import time

import pytest

from thm_core import MediaOrigin, adopt_media
from thm_server_fixtures import make_tiny_mp4, seeded_world

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture
def world(tmp_path):
    return seeded_world(tmp_path)


def cmd(w, name, body, headers=None):
    r = w.client.post(f"/api/projects/{w.slug}/commands/{name}", json=body, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def test_project_detail_lists_sequences_without_a_bin_until_export(world):
    w = world
    detail = w.client.get(f"/api/projects/{w.slug}").json()
    assert detail["sequences"] == []
    n_bins = len(detail["bins"])
    out = cmd(w, "create_sequence", {})
    sid = out["sequence"]
    assert sid and out["bin"] is None and out["change"]["description"] == "created sequence Sequence 1"
    detail = w.client.get(f"/api/projects/{w.slug}").json()
    assert [(s["name"], s["items"], s["total_s"], s["output_bin"]) for s in detail["sequences"]] == [("Sequence 1", 0, 0.0, None)]
    assert len(detail["bins"]) == n_bins


def test_sequence_out_joins_items_to_their_takes(world):
    w = world
    sid = cmd(w, "create_sequence", {"sequence_name": "Rough cut"})["sequence"]
    cmd(w, "add_sequence_items", {"sequence": sid, "bins": [w.image, w.video, w.mara]})
    seq = w.client.get(f"/api/projects/{w.slug}/sequences/{sid}").json()
    assert seq["name"] == "Rough cut" and seq["output_bin"] is None and seq["output_bin_name"] == "" and seq["last_export"] is None
    kinds = [(i["position"], i["name"], i["kind"], i["duration_s"]) for i in seq["items"]]
    # Image 1 has a take (a still), Video 1 has no take (a title card at its 5s recipe length), Mara leads with an upload (a still)
    assert kinds == [(1, "Image 1", "still", 3.0), (2, "Video 1", "title", 5.0), (3, "Mara", "still", 3.0)]
    assert seq["items"][0]["media"]["id"] == w.take and seq["items"][1]["media"] is None
    assert seq["total_s"] == 11.0 and seq["without_take"] == 1
    assert [i["start_s"] for i in seq["items"]] == [0.0, 3.0, 8.0]
    # the list route and a missing id
    assert [s["id"] for s in w.client.get(f"/api/projects/{w.slug}/sequences").json()] == [sid]
    assert w.client.get(f"/api/projects/{w.slug}/sequences/sq_nope").status_code == 404


def test_user_edits_land_as_feed_lines_during_a_session(world):
    w = world
    sid = cmd(w, "create_sequence", {"sequence_name": "Rough cut"})["sequence"]
    cmd(w, "add_sequence_items", {"sequence": sid, "bins": [w.image, w.mara]})
    out = cmd(w, "reorder_sequence", {"sequence": sid, "order": [w.mara, w.image]})
    assert out["change"]["description"] == "Rough cut - moved Mara to #1"
    seq = w.client.get(f"/api/projects/{w.slug}/sequences/{sid}").json()
    assert [i["bin"] for i in seq["items"]] == [w.mara, w.image]
    out = cmd(w, "set_item_still", {"sequence": sid, "bin": w.image, "still_s": 4.5})
    assert out["change"]["description"] == "Image 1 shown 3s to 4.5s"
    # the agent's settings are refused from the browser
    r = w.client.post(f"/api/projects/{w.slug}/commands/set_item_agent", json={"sequence": sid, "bin": w.image, "props": {"gain_db": -6}})
    assert r.status_code == 409 and r.json()["code"] == "wrong_actor"


def test_export_refusals(world, monkeypatch):
    w = world
    sid = cmd(w, "create_sequence", {})["sequence"]
    r = w.client.post(f"/api/projects/{w.slug}/sequences/{sid}/export")
    assert r.status_code == 409 and r.json()["code"] in ("sequence_empty", "ffmpeg_unavailable")
    assert w.client.get(f"/api/projects/{w.slug}/sequences/{sid}/export").json()["status"] == "idle"
    from thm_server.routes import sequences as mod

    monkeypatch.setattr(mod, "ffmpeg_available", lambda: False)
    cmd(w, "add_sequence_items", {"sequence": sid, "bins": [w.image]})
    r = w.client.post(f"/api/projects/{w.slug}/sequences/{sid}/export")
    assert r.status_code == 409 and r.json()["code"] == "ffmpeg_unavailable"
    # the agent needs a live session
    r = w.client.post(f"/api/projects/{w.slug}/sequences/{sid}/export", headers={"x-thm-actor": "agent"})
    assert r.status_code == 409 and r.json()["code"] == "no_session"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")
def test_export_lands_as_the_next_take_in_the_output_bin(world, tmp_path):
    w = world
    w.registry.invalidate(w.slug)
    project = w.registry.get(w.slug)
    clip = make_tiny_mp4(tmp_path / "clip.mp4")
    project.defaults.width, project.defaults.height = 128, 96
    m = adopt_media(project, clip, origin=MediaOrigin(type="upload", bin=w.video))
    project.bins_by_id[w.video].media.append(m.id)
    project.bins_by_id[w.video].selected = m.id
    # the take the item plays and another one it does not are both new
    project.bins_by_id[w.video].unseen = [m.id, "m_0ther000"]
    w.store.save(project)
    w.registry.invalidate(w.slug)
    sid = cmd(w, "create_sequence", {"sequence_name": "Rough cut"})["sequence"]
    cmd(w, "add_sequence_items", {"sequence": sid, "bins": [w.video, w.image]})
    cmd(w, "set_item_still", {"sequence": sid, "bin": w.image, "still_s": 0.5})
    card = next(c for c in w.client.get(f"/api/projects/{w.slug}").json()["sequences"] if c["id"] == sid)
    assert card["bins"] == [w.video, w.image] and card["fresh"] == 1  # the clip item plays a new take
    with w.client:  # the lifespan runs the export task on the app's loop
        r = w.client.post(f"/api/projects/{w.slug}/sequences/{sid}/export")
        assert r.status_code == 200 and r.json()["status"] == "running"
        assert w.client.post(f"/api/projects/{w.slug}/sequences/{sid}/export").json()["code"] == "export_busy"
        # exporting is taking what each item plays: that take stops being new, the other stays
        assert w.registry.get(w.slug).bins_by_id[w.video].unseen == ["m_0ther000"]
        deadline = time.time() + 60
        while time.time() < deadline:
            status = w.client.get(f"/api/projects/{w.slug}/sequences/{sid}/export").json()
            if status["status"] in ("done", "failed"):
                break
            time.sleep(0.2)
    assert status["status"] == "done", status
    assert status["take"] == 1 and status["media"]
    seq = w.client.get(f"/api/projects/{w.slug}/sequences/{sid}").json()
    export = seq["last_export"]
    assert export["id"] == status["media"] and export["origin"]["type"] == "export" and export["origin"]["source"] == sid
    assert export["take"] == 1 and export["type"] == "video" and export["origin"]["note"].startswith("2 items")
    # the output bin was made by the export, named for the sequence
    out_bin = w.client.get(f"/api/projects/{w.slug}/bins/{seq['output_bin']}").json()
    assert out_bin["bin"]["name"] == "Rough cut Exports" and out_bin["bin"]["tags"] == ["exports"]
    assert out_bin["media"][0]["id"] == export["id"]
    # the landing is a logged change on the output bin
    changes = w.client.get(f"/api/projects/{w.slug}/changes").json()
    assert any(c["command"] == "add_media_to_bin" and c["target"]["id"] == seq["output_bin"] for c in changes)


def test_a_note_can_be_about_the_sequence_and_its_open_item(world):
    w = world
    sid = cmd(w, "create_sequence", {"sequence_name": "Rough cut"})["sequence"]
    cmd(w, "add_sequence_items", {"sequence": sid, "bins": [w.mara, w.image]})
    base = f"/api/projects/{w.slug}/commands/post_message"
    # the sequence alone
    r = w.client.post(base, json={"text": "this cut drags", "context": {"sequence": sid}})
    assert r.status_code == 200, r.text
    ctx = r.json()["messages"][0]["context"]
    assert ctx["sequence"] == sid and ctx["sequence_name"] == "Rough cut" and ctx["bin"] is None and ctx["position"] is None
    # the open row: its bin and take, with its place in the cut
    r = w.client.post(base, json={"text": "this shot is too long", "context": {"sequence": sid, "bin": w.image, "media": w.take}})
    ctx = r.json()["messages"][0]["context"]
    assert (ctx["position"], ctx["bin_name"], ctx["media_label"]) == (2, "Image 1", "take 1")
    assert w.client.post(base, json={"text": "x", "context": {"sequence": "sq_nope"}}).status_code == 404
    assert w.client.post(base, json={"text": "x", "context": {}}).status_code == 422


def test_an_export_is_never_recovered_as_a_render(world, tmp_path):
    """A derived file keeps its own origin: the recipe route must not rewrite an export (or a clip)
    into a generation of the bin whose graph its bytes happen to carry."""
    from thm_core import MediaOrigin, adopt_media
    from thm_server.recover import recover_media

    w = world
    w.registry.invalidate(w.slug)
    project = w.registry.get(w.slug)
    clip = tmp_path / "x.mp4"
    clip.write_bytes(b"not a real file")
    media = adopt_media(project, clip, type="video", origin=MediaOrigin(type="export", bin=w.image, source="sq_1"))
    assert recover_media(w.app.state.thm, w.slug, project, media) is None
    assert media.origin.type == "export"


def test_a_dangling_target_reads_as_none(world):
    """A target bin removed behind the sequence's back (a hand edit, an older file): the detail and
    the card say there is no bin, so the export screen asks instead of minting quietly."""
    w = world
    sid = cmd(w, "create_sequence", {"sequence_name": "Rough cut"})["sequence"]
    cmd(w, "add_sequence_items", {"sequence": sid, "bins": [w.image]})
    w.registry.invalidate(w.slug)
    project = w.registry.get(w.slug)
    project.sequences[0].output_bin = "b_gone"
    w.store.save(project)
    w.registry.invalidate(w.slug)
    seq = w.client.get(f"/api/projects/{w.slug}/sequences/{sid}").json()
    assert seq["output_bin"] is None and seq["output_bin_name"] == ""
    card = next(s for s in w.client.get(f"/api/projects/{w.slug}").json()["sequences"] if s["id"] == sid)
    assert card["output_bin"] is None
