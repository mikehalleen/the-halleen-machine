"""Tests for per-asset reference image galleries."""

import json
from pathlib import Path

import pytest

from helpers import gallery_selected_index_for_path, get_pose_gallery_list
from assets_helpers import (
    _asset_item_dir,
    _delete_asset_gallery_image,
    _delete_character,
    _delete_simple_item,
    _find_asset_item,
    _normalize_gallery_select_index,
    _path_from_gallery_select_event,
    _paths_from_gallery_value,
    _promote_asset_reference_image,
    _refresh_char_list,
    _refresh_simple_list,
    _resolve_asset_list_selection,
    _save_to_asset_gallery,
)


def _project_with_char(tmp_path: Path, char_id: str = "c1") -> dict:
    root = tmp_path / "out" / "TestProj"
    char_dir = root / "_characters" / char_id
    char_dir.mkdir(parents=True)
    return {
        "project": {
            "name": "TestProj",
            "comfy": {"output_root": str(tmp_path / "out")},
            "characters": [
                {"id": char_id, "name": "Hero", "prompt": "tall"},
            ],
        }
    }


def test_get_pose_gallery_list_empty_dir_returns_nothing():
    assert get_pose_gallery_list("") == []
    assert get_pose_gallery_list("   ") == []


def test_get_pose_gallery_list_lists_asset_folder(tmp_path):
    d = tmp_path / "gallery"
    d.mkdir()
    (d / "a.png").write_bytes(b"x")
    (d / "b.jpg").write_bytes(b"y")
    items = get_pose_gallery_list(str(d))
    assert len(items) == 2
    assert all(Path(p).is_file() for p, _ in items)


def test_gallery_selected_index_for_path():
    items = [("/a/one.png", "one"), ("/a/two.png", "two")]
    assert gallery_selected_index_for_path(items, "/a/two.png") == 1
    assert gallery_selected_index_for_path(items, None) is None


def test_gallery_gr_update_highlights_path(tmp_path):
    d = tmp_path / "g"
    d.mkdir()
    p1 = d / "first.png"
    p2 = d / "second.png"
    p1.write_bytes(b"1")
    p2.write_bytes(b"2")
    raw = get_pose_gallery_list(str(d))
    idx = gallery_selected_index_for_path(raw, str(p2))
    assert idx is not None
    assert raw[idx][0].endswith("second.png")


def test_resolve_asset_list_selection_defaults_to_first():
    assert _resolve_asset_list_selection(None, None, ["a", "b"]) == "a"
    assert _resolve_asset_list_selection("", None, ["a"]) == "a"
    assert _resolve_asset_list_selection("missing", None, ["a", "b"]) == "a"
    assert _resolve_asset_list_selection("b", None, ["a", "b"]) == "b"
    assert _resolve_asset_list_selection(None, "b", ["a", "b"]) == "b"
    assert _resolve_asset_list_selection(None, None, []) is None


def test_refresh_char_list_auto_selects_first(tmp_path):
    project = _project_with_char(tmp_path)
    upd, pending = _refresh_char_list(project, None, None)
    assert upd["value"] == project["project"]["characters"][0]["id"]
    assert pending is None


def test_delete_setting_clears_sequence_setting_id(tmp_path):
    loc_id = "e31c9776-ecf8-4660-a832-74704f679f69"
    project = {
        "project": {
            "name": "TestProj",
            "settings": [{"id": loc_id, "name": "cyber", "prompt": ""}],
        },
        "sequences": {
            "seq1": {
                "setting_id": loc_id,
                "setting_reference_image": "/tmp/pin.png",
                "keyframes": {
                    "kf0": {
                        "reference_bindings": {
                            "198": {"semantic": "location", "setting_id": loc_id},
                            "199": {"semantic": "location", "source": "sequence"},
                        }
                    }
                },
            }
        },
    }
    data, _ = _delete_simple_item(project, "settings", loc_id)
    seq = data["sequences"]["seq1"]
    assert data["project"]["settings"] == []
    assert seq["setting_id"] == ""
    assert "setting_reference_image" not in seq
    bindings = seq["keyframes"]["kf0"]["reference_bindings"]
    assert bindings["198"] == {"semantic": "unset"}
    assert bindings["199"] == {"semantic": "unset"}


def test_delete_character_clears_keyframe_names_and_bindings(tmp_path):
    project = _project_with_char(tmp_path)
    char_id = project["project"]["characters"][0]["id"]
    char_name = project["project"]["characters"][0]["name"]
    project["sequences"] = {
        "seq1": {
            "keyframes": {
                "kf0": {
                    "characters": [char_name, ""],
                    "reference_bindings": {
                        "1": {"semantic": "character", "character_id": char_id},
                    },
                }
            }
        }
    }
    data, _ = _delete_character(project, char_id)
    kf = data["sequences"]["seq1"]["keyframes"]["kf0"]
    assert kf["characters"] == ["", ""]
    assert kf["reference_bindings"]["1"] == {"semantic": "unset"}


def test_refresh_simple_list_auto_selects_first(tmp_path):
    project = _project_with_char(tmp_path)
    project["project"]["settings"] = [{"id": "s1", "name": "Store"}]
    upd, pending = _refresh_simple_list(project, "settings", None, None)
    assert upd["value"] == "s1"
    assert pending is None


def test_save_to_asset_gallery_sets_reference_on_first_save(tmp_path):
    project = _project_with_char(tmp_path)
    src = tmp_path / "gen.png"
    src.write_bytes(b"gen")

    data, msg, _gal, _img = _save_to_asset_gallery(project, "characters", "c1", str(src))
    item = _find_asset_item(data, "characters", "c1")
    assert item is not None
    saved = next(_asset_item_dir(data, "characters", "c1").glob("gallery*.png"))
    assert item["reference_image"] == str(saved)
    assert "Saved to library" in msg
    assert "set as selected reference" in msg
    char_dir = _asset_item_dir(data, "characters", "c1")
    assert char_dir is not None
    assert list(char_dir.glob("gallery*.png"))


def test_save_to_asset_gallery_keeps_existing_reference_on_later_save(tmp_path):
    project = _project_with_char(tmp_path)
    char_dir = _asset_item_dir(project, "characters", "c1")
    assert char_dir is not None
    char_dir.mkdir(parents=True, exist_ok=True)
    first = char_dir / "gallery_001.png"
    first.write_bytes(b"first")
    project["project"]["characters"][0]["reference_image"] = str(first)

    src = tmp_path / "gen.png"
    src.write_bytes(b"gen")
    data, msg, _gal, _img = _save_to_asset_gallery(project, "characters", "c1", str(src))
    item = _find_asset_item(data, "characters", "c1")
    assert item["reference_image"] == str(first)
    assert "set as selected reference" not in msg


def test_promote_sets_reference_image(tmp_path):
    project = _project_with_char(tmp_path)
    img = _asset_item_dir(project, "characters", "c1") / "pick.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"pick")

    data, msg, _img_up, _gal = _promote_asset_reference_image(
        project, "characters", "c1", str(img)
    )
    item = _find_asset_item(data, "characters", "c1")
    assert item["reference_image"] == str(img)
    assert "Selected reference" in msg


def test_paths_from_gallery_value_tuple_list():
    gal = [("/a/one.png", "one"), ("/a/two.png", "two")]
    assert _paths_from_gallery_value(gal) == ["/a/one.png", "/a/two.png"]


def test_normalize_gallery_select_index_tuple():
    assert _normalize_gallery_select_index((1, 0)) == 1
    assert _normalize_gallery_select_index(0) == 0


def test_path_from_gallery_select_event_uses_gallery_value(tmp_path):
    d = tmp_path / "asset"
    d.mkdir()
    p = d / "pick.png"
    p.write_bytes(b"x")
    gal = [(str(p), "pick")]
    evt = type("E", (), {"index": 0, "value": {"image": {"path": str(p)}}})()
    assert _path_from_gallery_select_event(d, gal, evt) == str(p.resolve())


def test_delete_clears_reference_when_selected(tmp_path):
    project = _project_with_char(tmp_path)
    img = _asset_item_dir(project, "characters", "c1") / "pick.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"pick")
    project["project"]["characters"][0]["reference_image"] = str(img)

    data, msg, _gal, _disp, path_state, del_vis = _delete_asset_gallery_image(
        project, "characters", "c1", str(img)
    )
    assert not img.exists()
    item = _find_asset_item(data, "characters", "c1")
    assert "reference_image" not in item
    assert "Deleted" in msg
    assert path_state is None


def test_path_from_gallery_select_event_uses_clicked_index(tmp_path):
    d = tmp_path / "asset2"
    d.mkdir()
    p1 = d / "one.png"
    p2 = d / "two.png"
    p1.write_bytes(b"1")
    p2.write_bytes(b"2")
    gal = [(str(p1), "one"), (str(p2), "two")]
    evt = type("E", (), {"index": 1, "value": {"image": {"path": str(p2)}}})()
    assert _path_from_gallery_select_event(d, gal, evt) == str(p2.resolve())


def test_assets_tab_includes_mobile_column_stack_css():
    src = Path(__file__).resolve().parent.parent / "src" / "assets_helpers.py"
    text = src.read_text(encoding="utf-8")
    assert "@media (max-width: 900px)" in text
    assert "flex-direction: column !important" in text
    assert "asset-preview-row" in text
    assert 'elem_classes=["asset-preview-row"]' in text
    assert "asset-preview-row .block.image" in text
    assert "flex-wrap: wrap !important" in text
