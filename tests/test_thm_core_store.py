import json

import pytest

from thm_core import Change, Generation, Message, ProjectStore, SchemaVersionError, upgrade_v3
from thm_core import store as store_mod
from thm_core.models import ChangeTarget
from thm_core_fixtures import add_bin, add_media, bind, make_project, make_store


def _change(id_, **kw):
    base = dict(id=id_, ts="t", actor="agent", command="set_bin_field", target=ChangeTarget(type="bin", id="b_1"))
    base.update(kw)
    return Change(**base)


def test_save_then_load_returns_equal_project(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    mara = add_bin(p, "Mara")
    m = add_media(p, mara)
    s = add_bin(p, "Image 1")
    bind(p, s, m)
    path = store.save(p)
    assert path == tmp_path / "projects" / "rail-dawn-promo.json"
    again = store.load("rail-dawn-promo")
    assert again.model_dump(mode="json") == p.model_dump(mode="json")
    assert store.list_projects() == ["rail-dawn-promo"]
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 4


def test_new_project_slug_avoids_existing_file(tmp_path):
    store = make_store(tmp_path)
    make_project(store)
    second = store.new_project("Rail Dawn Promo")
    assert second.slug == "rail-dawn-promo-2"


def test_load_refuses_1x_project_with_sequences(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "old.json").write_text(json.dumps({"schema_version": 2, "project": {}, "sequences": {}}), encoding="utf-8")
    with pytest.raises(SchemaVersionError) as exc:
        store.load("old")
    assert "1.x" in str(exc.value) and "old.json" in str(exc.value)
    assert store.list_projects() == []


def test_load_refuses_missing_and_future_versions(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "a.json").write_text(json.dumps({"project": {}}), encoding="utf-8")
    (tmp_path / "projects" / "b.json").write_text(json.dumps({"schema_version": 5, "id": "p", "name": "b"}), encoding="utf-8")
    (tmp_path / "projects" / "c.json").write_text(json.dumps({"schema_version": 2, "id": "p", "name": "c"}), encoding="utf-8")
    with pytest.raises(SchemaVersionError) as exc:
        store.load("a")
    assert "missing" in str(exc.value)
    with pytest.raises(SchemaVersionError) as exc:
        store.load("b")
    assert "newer" in str(exc.value)
    with pytest.raises(SchemaVersionError):
        store.load("c")
    assert store.list_projects() == []


# -- schema 3 upgrade ------------------------------------------------------------

V3 = {
    "schema_version": 3,
    "id": "p_v3",
    "name": "Old",
    "slug": "old",
    "defaults": {"image_workflow": "img.json", "video_workflow": "vid.json", "video_length_s": 6},
    "shots": ["s_vid", "s_img"],
    "shots_by_id": {
        "s_vid": {
            "id": "s_vid", "name": "Video 1", "kind": "video", "workflow": "vid.json", "length_s": 5,
            "prompt": ".ed at the rail with .video-1", "negative": "", "loras": [],
            "references": [
                {"id": "r_1", "media": "m_ed", "item": "it_ed", "handle": "ed"},
                {"id": "r_2", "media": "m_take1", "item": "shot:s_vid", "handle": "video-1"},
            ],
            "takes": ["m_take1", "m_take2"], "selected_take": "m_take2", "counters": {"take": 2, "it_ed": 1},
        },
        "s_img": {"id": "s_img", "name": "Image 1", "kind": "image", "workflow": "img.json", "takes": [], "selected_take": None},
    },
    "items_by_id": {
        "it_ed": {"id": "it_ed", "name": "Ed", "kind": "character", "media": ["m_ed", "m_ed2"], "cover": "m_ed2", "description": "the guitarist", "tags": ["cast"], "created": "2026-08-01T00:00:00Z"},
        "it_clips": {"id": "it_clips", "name": "clips", "kind": "", "media": ["m_clip"], "cover": "", "created": "2026-08-02T00:00:00Z"},
        "it_empty": {"id": "it_empty", "name": "empty", "kind": "", "media": [], "cover": "m_gone", "created": "2026-08-03T00:00:00Z"},
    },
    "media_by_id": {
        "m_ed": {"id": "m_ed", "type": "image", "path": "C:/x/ed.png", "created": "2026-08-01T00:00:00Z", "origin": {"type": "upload", "item": "it_ed"}},
        "m_ed2": {"id": "m_ed2", "type": "image", "path": "C:/x/ed2.png", "created": "2026-08-01T00:00:01Z", "origin": {"type": "generation", "generation": "g_ref", "item": "it_ed"}},
        "m_take1": {"id": "m_take1", "type": "video", "path": "C:/x/t1.mp4", "created": "2026-08-05T00:00:00Z", "origin": {"type": "generation", "generation": "g_1", "shot": "s_vid"}},
        "m_take2": {"id": "m_take2", "type": "video", "path": "C:/x/t2.mp4", "created": "2026-08-05T00:00:01Z", "origin": {"type": "generation", "generation": "g_2", "shot": "s_vid"}},
        "m_clip": {"id": "m_clip", "type": "video", "path": "C:/x/c.mp4", "created": "2026-08-06T00:00:00Z", "origin": {"type": "clip", "source": "m_take1", "start_s": 0, "end_s": 1, "item": "it_clips"}},
        "m_orphan": {"id": "m_orphan", "type": "image", "path": "C:/x/o.png", "origin": {"type": "generation", "generation": "g_old", "shot": "s_deleted"}},
    },
    "counters": {"shot_video": 1, "shot_image": 1},
}

V3_GENERATIONS = [
    {"id": "g_1", "shot": "s_vid", "kind": "video", "ts": "t", "workflow": "vid.json", "status": "done", "outputs": ["m_take1"]},
    {"id": "g_2", "shot": "s_vid", "kind": "video", "ts": "t", "workflow": "vid.json", "status": "done", "outputs": ["m_take2"]},
    {"id": "g_ref", "item": "it_ed", "kind": "image", "ts": "t", "workflow": "img.json", "status": "done", "outputs": ["m_ed2"]},
    {"id": "g_x", "item": "it_ed", "kind": None, "ts": "t", "workflow": "img.json", "status": "cancelled"},
]


def _write_v3(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    (projects / "old.json").write_text(json.dumps(V3), encoding="utf-8")
    files = projects / "old-files"
    files.mkdir()
    (files / "generations.jsonl").write_text("\n".join(json.dumps(g) for g in V3_GENERATIONS) + "\n", encoding="utf-8")
    (files / "changes.jsonl").write_text(
        json.dumps({"id": "c_1", "ts": "t", "actor": "agent", "command": "set_shot_field", "target": {"type": "shot", "id": "s_vid"}, "after": {"selected_take": "m_take2"}, "decision": {"status": "pending"}}) + "\n"
        + json.dumps({"id": "c_2", "ts": "t", "actor": "user", "command": "set_item_cover", "target": {"type": "item", "id": "it_ed"}, "after": {"cover": "m_ed2"}}) + "\n",
        encoding="utf-8",
    )
    return projects


def test_upgrade_v3_maps_every_shot_and_item_to_a_bin():
    raw = upgrade_v3(json.loads(json.dumps(V3)))
    assert raw["schema_version"] == 4 and "shots" not in raw and "shots_by_id" not in raw and "items_by_id" not in raw
    assert raw["counters"] == {}
    bins = raw["bins_by_id"]
    assert set(bins) == {"s_vid", "s_img", "it_ed", "it_clips", "it_empty"}  # ids kept
    vid = bins["s_vid"]
    assert vid["media"] == ["m_take1", "m_take2"] and vid["selected"] == "m_take2" and vid["counters"] == {"take": 2, "it_ed": 1}
    assert vid["prompt"] == ".ed at the rail with .video-1" and vid["length_s"] == 5
    assert [(r["bin"], r["handle"]) for r in vid["references"]] == [("it_ed", "ed"), ("s_vid", "video-1")]
    assert all("item" not in r for r in vid["references"])
    ed = bins["it_ed"]
    assert ed["workflow"] == "img.json" and ed["length_s"] is None and ed["selected"] == "m_ed2"
    assert ed["description"] == "the guitarist" and ed["tags"] == ["cast"] and ed["created"] == "2026-08-01T00:00:00Z"
    clips = bins["it_clips"]
    assert clips["workflow"] == "vid.json" and clips["length_s"] == 6 and clips["selected"] is None  # no cover: nothing selected
    assert bins["it_empty"]["selected"] is None  # a cover pointing at nothing is dropped
    assert bins["s_img"]["media"] == [] and bins["s_img"]["selected"] is None
    origins = {mid: m["origin"] for mid, m in raw["media_by_id"].items()}
    assert origins["m_ed"] == {"type": "upload", "bin": "it_ed"}
    assert origins["m_take1"]["bin"] == "s_vid" and "shot" not in origins["m_take1"]
    assert origins["m_clip"]["bin"] == "it_clips" and origins["m_clip"]["source"] == "m_take1"
    assert origins["m_orphan"]["bin"] == "s_deleted"


def test_load_upgrades_a_v3_file_and_reads_its_logs(tmp_path):
    projects = _write_v3(tmp_path)
    store = ProjectStore(projects)
    assert store.list_projects() == ["old"]
    p = store.load("old")
    assert p.schema_version == 4 and len(p.bins_by_id) == 5
    assert p.bins_by_id["s_vid"].references[1].bin == "s_vid"
    assert p.media_by_id["m_orphan"].origin.bin == "s_deleted"
    # take numbers: the shot's from its counters, the item generation from the log
    assert p.media_by_id["m_take1"].take == 1 and p.media_by_id["m_take2"].take == 2
    assert p.media_by_id["m_ed2"].take == 1 and p.bins_by_id["it_ed"].counters["take"] == 1
    assert p.media_by_id["m_ed"].take is None and p.media_by_id["m_clip"].take is None
    gens = store.read_generations(p)
    assert [(g.id, g.bin, g.kind) for g in gens] == [("g_1", "s_vid", "video"), ("g_2", "s_vid", "video"), ("g_ref", "it_ed", "image"), ("g_x", "it_ed", "image")]
    changes = store.read_changes(p)
    assert changes["c_1"].target.type == "bin" and changes["c_2"].target.type == "bin"
    # the file on disk is still schema 3 until something writes it
    assert json.loads((projects / "old.json").read_text(encoding="utf-8"))["schema_version"] == 3
    store.save(p)
    on_disk = json.loads((projects / "old.json").read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 4 and "shots_by_id" not in on_disk
    assert store.load("old").model_dump(mode="json") == p.model_dump(mode="json")


def test_second_save_creates_backup_and_throttle_suppresses_third(tmp_path):
    now = [1000.0]
    store = make_store(tmp_path, throttle=100, clock=lambda: now[0])
    p = make_project(store)  # first save, no backup (file did not exist)
    backups = tmp_path / "projects" / "_backups"
    assert not backups.exists() or not list(backups.iterdir())
    store.save(p)
    assert len(list(backups.glob("rail-dawn-promo.json.backup.*"))) == 1
    now[0] += 10
    store.save(p)
    assert len(list(backups.glob("rail-dawn-promo.json.backup.*"))) == 1
    now[0] += 200
    store.save(p)
    assert len(list(backups.glob("rail-dawn-promo.json.backup.*"))) == 2


def test_backups_prune_to_retention(tmp_path):
    store = make_store(tmp_path, retention=2, throttle=0)
    p = make_project(store)
    for _ in range(5):
        store.save(p)
    backups = tmp_path / "projects" / "_backups"
    assert len(list(backups.glob("rail-dawn-promo.json.backup.*"))) == 2


def test_failed_replace_leaves_old_file_intact(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    p = make_project(store)
    before = store.project_path(p).read_text(encoding="utf-8")
    p.name = "changed"

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(store_mod.os, "replace", boom)
    with pytest.raises(OSError):
        store.save(p)
    assert store.project_path(p).read_text(encoding="utf-8") == before
    assert not list((tmp_path / "projects").glob("*.tmp"))


def test_duplicate_change_id_later_line_wins(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    store.append_change(p, _change("c_1", description="first"))
    store.append_change(p, _change("c_2", description="second"))
    store.append_change(p, _change("c_1", description="first again"))
    changes = store.read_changes(p)
    assert list(changes) == ["c_1", "c_2"]
    assert changes["c_1"].description == "first again"


def test_log_cache_parses_once_and_extends_on_append(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    p = make_project(store)
    store.append_change(p, _change("c_1", description="first"))
    parses = []
    real = store._read_lines
    monkeypatch.setattr(store, "_read_lines", lambda project, name: parses.append(name) or real(project, name))
    assert list(store.read_changes(p)) == ["c_1"]
    assert list(store.read_changes(p)) == ["c_1"]
    store.append_change(p, _change("c_2", description="second"))
    store.append_change(p, _change("c_1", description="first again"))
    changes = store.read_changes(p)
    assert list(changes) == ["c_1", "c_2"] and changes["c_1"].description == "first again"
    store.append_generation(p, Generation(id="g_1", bin="b_1", ts="t", workflow="w"))
    store.append_generation(p, Generation(id="g_1", bin="b_1", ts="t", workflow="w", status="done"))
    assert store.read_generation(p, "g_1").status == "done"
    store.append_message(p, Message(id="msg_1", ts="t", kind="user", text="hi"))
    assert store.message_cursor(p) == 1 and store.read_messages(p)[0].text == "hi"
    assert parses == [store_mod.CHANGES_LOG, store_mod.GENERATIONS_LOG, store_mod.MESSAGES_LOG]


def test_log_cache_reloads_when_the_file_changes_underneath(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    store.append_change(p, _change("c_1"))
    assert list(store.read_changes(p)) == ["c_1"]
    path = store.files_dir(p) / store_mod.CHANGES_LOG
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_change("c_raw").model_dump(mode="json")) + "\n")
    assert list(store.read_changes(p)) == ["c_1", "c_raw"]
    other = ProjectStore(store.projects_dir)
    other.append_change(p, _change("c_other"))
    assert list(store.read_changes(p)) == ["c_1", "c_raw", "c_other"]
    store.append_change(p, _change("c_mine"))
    assert list(other.read_changes(p)) == ["c_1", "c_raw", "c_other", "c_mine"]


def test_read_results_are_fresh_containers(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    store.append_change(p, _change("c_1"))
    store.append_generation(p, Generation(id="g_1", bin="b_1", ts="t", workflow="w"))
    store.append_message(p, Message(id="msg_1", ts="t", kind="user", text="hi"))
    store.read_changes(p).clear()
    store.read_generations(p).clear()
    store.read_messages(p).clear()
    store.read_inbox(p, 0)[0].clear()
    assert list(store.read_changes(p)) == ["c_1"]
    assert [g.id for g in store.read_generations(p)] == ["g_1"]
    assert [m.id for m in store.read_messages(p)] == ["msg_1"]


def test_read_inbox_returns_only_messages_past_cursor(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    for i in range(3):
        store.append_message(p, Message(id=f"msg_{i}", ts="t", kind="user", text=str(i)))
    msgs, cursor = store.read_inbox(p, 1)
    assert [m.text for m in msgs] == ["1", "2"]
    assert cursor == 3
    assert store.read_inbox(p, cursor) == ([], 3)
    assert store.message_cursor(p) == 3
    assert store.read_inbox(p, None)[0][0].text == "0"


def test_generations_preserve_order(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    for i in range(3):
        store.append_generation(p, Generation(id=f"g_{i}", bin="b_1", ts="t", workflow="w"))
    assert [g.id for g in store.read_generations(p)] == ["g_0", "g_1", "g_2"]


def test_load_moves_a_legacy_session_brief_onto_the_project(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    raw = json.loads(store.project_path(p).read_text(encoding="utf-8"))
    raw["agent_session"] = {"id": "as_old", "status": "ended", "brief": "old brief", "brief_item": "it_x"}
    store.project_path(p).write_text(json.dumps(raw), encoding="utf-8")
    loaded = store.load(p.slug)
    assert loaded.brief == "old brief" and loaded.agent_session.status == "ended"


def test_generations_last_line_per_id_wins(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    store.append_generation(p, Generation(id="g_a", bin="b_1", ts="t", workflow="w", status="queued"))
    store.append_generation(p, Generation(id="g_b", bin="b_1", ts="t", workflow="w", status="queued"))
    store.append_generation(p, Generation(id="g_a", bin="b_1", ts="t", workflow="w", status="running", params={"seed": 7}))
    store.append_generation(p, Generation(id="g_a", bin="b_1", ts="t", workflow="w", status="done", outputs=["m_1"]))
    gens = store.read_generations(p)
    assert [g.id for g in gens] == ["g_a", "g_b"]
    assert gens[0].status == "done" and gens[0].outputs == ["m_1"]
    assert store.read_generation(p, "g_b").status == "queued"
    assert store.read_generation(p, "g_zz") is None


def test_store_requires_slug_for_unsaved_project(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    with pytest.raises(ValueError):
        store.project_path(make_project().model_copy(update={"slug": ""}))
