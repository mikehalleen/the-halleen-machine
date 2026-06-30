"""Tests for thm-agent builder fork."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BUILDER_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BUILDER_DIR.parent
if str(_BUILDER_DIR) not in sys.path:
    sys.path.insert(0, str(_BUILDER_DIR))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

import builder  # noqa: E402
from helpers import _ensure_project  # noqa: E402


@pytest.fixture
def tmp_project_path(tmp_path):
    return tmp_path / "test-project.json"


def test_create_blank_has_sequence_and_keyframe():
    data = builder.create_blank("my-test")
    assert data["project"]["name"] == "my-test"
    assert data.get("sequence_order")
    sid = data["sequence_order"][0]
    assert sid in data["sequences"]
    assert data["sequences"][sid]["keyframe_order"]


def test_narrative_build_video_chain(tmp_project_path):
    """build_narrative_sequence chains multiple beats into ONE sequence (oner)."""
    data = builder.create_blank("store-walk")
    data, char_id = builder.add_character(data, "Shopper", "a person in casual clothes")
    data, setting_id = builder.add_setting(data, "Store", "interior of a grocery store")
    beats = [
        builder.BeatSpec("wide shot of store entrance", "character walks through automatic doors", 4, (char_id, "")),
        builder.BeatSpec("medium shot in produce aisle", "camera follows from behind down the aisle", 5, (char_id, "")),
        builder.BeatSpec("close shot at checkout counter", "character places items on belt", 4, (char_id, "")),
    ]
    data, seq_id = builder.build_narrative_sequence(
        data,
        beats=beats,
        setting_id=setting_id,
        seq_id=data["sequence_order"][0],
    )
    seq = data["sequences"][seq_id]
    assert len(seq["keyframe_order"]) == 3
    assert len(seq["video_order"]) == 3
    assert len(data["sequence_order"]) == 1
    issues = builder.validate_project(data)
    assert not issues, issues

    builder.save_project(tmp_project_path, data)
    loaded = builder.load_project(tmp_project_path)
    assert loaded["project"]["name"] == "store-walk"


def test_build_shots_one_sequence_per_shot(tmp_project_path):
    """build_shots creates discrete cuts as separate sequences."""
    data = builder.create_blank("store-shots")
    data, char_id = builder.add_character(data, "Shopper", "a person in casual clothes")
    data, setting_id = builder.add_setting(data, "Store", "interior of a grocery store")
    shots = [
        builder.ShotSpec("wide exterior entrance", "walks toward doors", 4, (char_id, "")),
        builder.ShotSpec("medium produce aisle", "walks down aisle", 5, (char_id, "")),
        builder.ShotSpec("close-up checkout", "hands on conveyor", 4, (char_id, "")),
    ]
    data, seq_ids = builder.build_shots(data, shots, setting_id=setting_id)
    assert len(seq_ids) == 3
    assert len(data["sequence_order"]) == 3
    for sid in seq_ids:
        seq = data["sequences"][sid]
        assert len(seq["keyframe_order"]) == 1
        assert len(seq["video_order"]) == 1
        kf_id = seq["keyframe_order"][0]
        assert seq["keyframes"][kf_id]["layout"]
    issues = builder.validate_project(data)
    assert not issues, issues
    builder.save_project(tmp_project_path, data)


def test_recommend_edit_structure():
    assert builder.recommend_edit_structure("five shots in a store") == "cuts"
    assert builder.recommend_edit_structure("one continuous take through the store") == "oner"
    assert builder.recommend_edit_structure("walk through", explicit_oner=True) == "oner"
    assert builder.recommend_edit_structure("many shots", explicit_cuts=True) == "cuts"
    assert builder.recommend_edit_structure("3 cuts and a wide shot") == "cuts"
    assert builder.recommend_edit_structure("long take no cuts") == "oner"


def test_remove_placeholder_sequences():
    data = builder.create_blank("placeholder-test")
    assert len(data["sequence_order"]) == 1
    cleaned = builder.remove_placeholder_sequences(data)
    assert cleaned["sequence_order"] == []
    assert cleaned["sequences"] == {}


def test_normalize_clip_duration_whole_seconds():
    assert builder.normalize_clip_duration_sec(4) == 4
    assert builder.normalize_clip_duration_sec(4.7) == 5
    assert builder.normalize_clip_duration_sec(0.4) == 1
    assert builder.normalize_clip_duration_sec(99) == 10
    assert builder.clip_duration_choices() == list(range(1, 11))


def test_validate_rejects_fractional_duration():
    data = builder.create_blank("frac-dur")
    sid = data["sequence_order"][0]
    kf_id = data["sequences"][sid]["keyframe_order"][0]
    data["sequences"][sid]["keyframes"][kf_id]["layout"] = "test"
    vid_id = data["sequences"][sid]["video_order"][0]
    data["sequences"][sid]["videos"][vid_id]["duration_override_sec"] = 3.5
    issues = builder.validate_project(data)
    assert any("whole number" in i for i in issues)


def test_recommend_video_plan_product_pickup():
    os, oe = builder.recommend_video_plan(
        "person stands with the product in their hand",
        "person walks down the aisle until they find and pick up the product",
    )
    assert (os, oe) == (True, False)


def test_recommend_video_plan_continues_from_still():
    os, oe = builder.recommend_video_plan(
        "person holds product up to camera",
        "smiles and turns to walk away down the aisle",
    )
    assert (os, oe) == (False, True)


def test_build_shot_both_open_two_videos():
    data = builder.create_blank("both-open")
    data, char_id = builder.add_character(data, "Shopper", "person")
    data, setting_id = builder.add_setting(data, "Store", "store")
    shots = [
        builder.ShotSpec(
            "person holds product smiling at camera",
            "walks down aisle and picks up product",
            4,
            (char_id, ""),
            open_start=True,
            open_end=True,
            inbetween_prompt_out="turns and walks away down the aisle",
            duration_sec_out=4,
        ),
    ]
    data, seq_ids = builder.build_shots(data, shots, setting_id=setting_id)
    seq = data["sequences"][seq_ids[0]]
    assert seq["video_plan"] == {"open_start": True, "open_end": True}
    assert len(seq["video_order"]) == 2
    v0 = seq["videos"][seq["video_order"][0]]
    v1 = seq["videos"][seq["video_order"][1]]
    assert v0["start_keyframe_id"] is None
    assert v0["end_keyframe_id"] == seq["keyframe_order"][0]
    assert v1["start_keyframe_id"] == seq["keyframe_order"][0]
    assert v1["end_keyframe_id"] is None
    assert "picks up" in v0["inbetween_prompt"]
    assert "walks away" in v1["inbetween_prompt"]
    assert not builder.validate_project(data)


def test_build_shot_both_closed_keyframe_pair():
    data = builder.create_blank("kf-pair")
    data, char_id = builder.add_character(data, "Shopper", "person")
    data, setting_id = builder.add_setting(data, "Store", "store")
    shots = [
        builder.ShotSpec(
            "wide shot at aisle entrance",
            "camera dollies forward down the aisle",
            5,
            (char_id, ""),
            layout_end="close-up of product on shelf",
        ),
    ]
    data, seq_ids = builder.build_shots(data, shots, setting_id=setting_id)
    seq = data["sequences"][seq_ids[0]]
    assert seq["video_plan"] == {"open_start": False, "open_end": False}
    assert len(seq["keyframe_order"]) == 2
    assert len(seq["video_order"]) == 1
    vid = seq["videos"][seq["video_order"][0]]
    assert vid["start_keyframe_id"] == seq["keyframe_order"][0]
    assert vid["end_keyframe_id"] == seq["keyframe_order"][1]
    assert not builder.validate_project(data)


def test_describe_video_plan():
    assert "middle" in builder.describe_video_plan(True, True, 1)
    assert "keyframe → keyframe" in builder.describe_video_plan(False, False, 2)
    assert "0 videos" in builder.describe_video_plan(False, False, 1)


def test_build_shot_open_start_product_example():
    data = builder.create_blank("open-start-shot")
    data, char_id = builder.add_character(data, "Shopper", "person")
    data, setting_id = builder.add_setting(data, "Store", "store")
    shots = [
        builder.ShotSpec(
            "person stands with the product in their hand",
            "person walks down the aisle until they find and pick up the product",
            5,
            (char_id, ""),
        ),
    ]
    data, seq_ids = builder.build_shots(data, shots, setting_id=setting_id)
    seq = data["sequences"][seq_ids[0]]
    assert seq["video_plan"] == {"open_start": True, "open_end": False}
    vid = seq["videos"][seq["video_order"][0]]
    assert vid["start_keyframe_id"] is None
    assert vid["end_keyframe_id"] == seq["keyframe_order"][0]


def test_build_shots_clears_action_prompt():
    data = builder.create_blank("action-clear")
    data, char_id = builder.add_character(data, "Shopper", "person")
    data, setting_id = builder.add_setting(data, "Store", "store interior")
    shots = [
        builder.ShotSpec(
            "close-up face",
            "holds the product up to camera and smiles",
            4,
            (char_id, ""),
            action_prompt="should not appear on single clip",
        ),
    ]
    data, seq_ids = builder.build_shots(data, shots, setting_id=setting_id)
    seq = data["sequences"][seq_ids[0]]
    assert seq["action_prompt"] == ""
    assert "smiles" in seq["videos"][seq["video_order"][0]]["inbetween_prompt"]
    issues = builder.validate_project(data)
    assert not any("action_prompt" in i for i in issues)


def test_oner_multi_clip_allows_action_prompt():
    data = builder.create_blank("dance-oner")
    data, char_id = builder.add_character(data, "Dancer", "dancer")
    beats = [
        builder.BeatSpec("wide full body", "arms rise", 4, (char_id, "")),
        builder.BeatSpec("medium waist up", "spins left", 4, (char_id, "")),
    ]
    data, seq_id = builder.build_narrative_sequence(
        data,
        beats=beats,
        action_prompt="2 beats per second dance",
        seq_id=data["sequence_order"][0],
    )
    seq = data["sequences"][seq_id]
    assert seq["action_prompt"] == "2 beats per second dance"
    assert len(seq["video_order"]) == 2


def test_validate_rejects_action_prompt_on_single_video():
    data = builder.create_blank("bad-action")
    sid = data["sequence_order"][0]
    kf_id = data["sequences"][sid]["keyframe_order"][0]
    data["sequences"][sid]["keyframes"][kf_id]["layout"] = "test"
    data["sequences"][sid]["action_prompt"] = "holds product and smiles"
    issues = builder.validate_project(data)
    assert any("action_prompt" in i and "one video" in i for i in issues)


def test_validate_catches_orphan_setting_id():
    data = builder.create_blank("orphan-test")
    sid = data["sequence_order"][0]
    data["sequences"][sid]["setting_id"] = "00000000-0000-0000-0000-000000000000"
    kf_id = data["sequences"][sid]["keyframe_order"][0]
    data["sequences"][sid]["keyframes"][kf_id]["layout"] = "test layout"
    issues = builder.validate_project(data)
    assert any("setting_id" in i for i in issues)


def test_validate_catches_empty_layout():
    data = builder.create_blank("empty-layout")
    issues = builder.validate_project(data)
    assert any("layout is empty" in i for i in issues)


def test_preserve_generation_fields():
    data = builder.create_blank("preserve-test")
    sid = data["sequence_order"][0]
    kf_id = data["sequences"][sid]["keyframe_order"][0]
    data["sequences"][sid]["keyframes"][kf_id]["layout"] = "filled layout"
    data["sequences"][sid]["keyframes"][kf_id]["selected_image_path"] = "/output/test.png"
    data["sequences"][sid]["keyframes"][kf_id]["pose"] = "/output/_poses/pose.png"

    patched = builder.patch_field(data, f"sequences.{sid}.keyframes.{kf_id}.layout", "new layout")
    merged = builder.preserve_generation_fields(data, patched)
    kf = merged["sequences"][sid]["keyframes"][kf_id]
    assert kf["layout"] == "new layout"
    assert kf["selected_image_path"] == "/output/test.png"
    assert kf["pose"] == "/output/_poses/pose.png"


def test_round_trip_save_load_patch(tmp_project_path):
    data = builder.create_blank("round-trip")
    sid = data["sequence_order"][0]
    kf_id = data["sequences"][sid]["keyframe_order"][0]
    data["sequences"][sid]["keyframes"][kf_id]["layout"] = "initial"
    builder.save_project(tmp_project_path, data)

    loaded = builder.load_project(tmp_project_path)
    loaded = builder.patch_field(loaded, f"sequences.{sid}.keyframes.{kf_id}.layout", "updated")
    builder.save_project(tmp_project_path, loaded)

    again = builder.load_project(tmp_project_path)
    assert again["sequences"][sid]["keyframes"][kf_id]["layout"] == "updated"
    assert _ensure_project(again)["project"]["name"] == "round-trip"


def test_recommend_model_family():
    assert builder.recommend_model_family(num_characters=1) == "default"
    assert builder.recommend_model_family(needs_reference_images=True) == "custom"
    assert builder.recommend_model_family(num_characters=3) == "custom"


def test_recommend_video_model_family():
    assert builder.recommend_video_model_family() == "default"
    assert builder.recommend_video_model_family(needs_custom_video_workflow=True) == "custom"
    assert builder.recommend_video_model_family(brief="LTX i2v project") == "custom"


def test_summarize_includes_assets():
    data = builder.create_blank("summary-test")
    data, _ = builder.add_character(data, "Hero", "tall figure")
    text = builder.summarize_project(data)
    assert "Hero" in text
    assert "summary-test" in text


def test_clone_project_from_host(tmp_path):
    host_path = tmp_path / "host.json"
    host = builder.create_blank("host-project")
    host, char_id = builder.add_character(host, "Hero", "tall green figure")
    host["project"]["characters"][-1]["reference_image"] = "/output/host/hero.png"
    host["project"]["style_prompt"] = "cinematic dusk"
    sid = host["sequence_order"][0]
    host["sequences"][sid]["keyframes"][host["sequences"][sid]["keyframe_order"][0]]["layout"] = "old story"
    host["sequences"][sid]["keyframes"][host["sequences"][sid]["keyframe_order"][0]]["selected_image_path"] = "/old.png"
    builder.save_project(host_path, host)

    dest = tmp_path / "new-story.json"
    _new, written = builder.clone_project_from_host(host_path, "new-story", dest_path=dest)

    assert written == dest
    host_after = builder.load_project(host_path)
    assert host_after["sequences"][sid]["keyframes"][host_after["sequences"][sid]["keyframe_order"][0]]["layout"] == "old story"

    cloned = builder.load_project(dest)
    assert cloned["project"]["name"] == "new-story"
    assert cloned["project"]["style_prompt"] == "cinematic dusk"
    assert cloned["project"]["characters"][0]["reference_image"] == "/output/host/hero.png"
    assert cloned["project"]["characters"][0]["id"] == char_id
    new_sid = cloned["sequence_order"][0]
    new_kf = cloned["sequences"][new_sid]["keyframes"][cloned["sequences"][new_sid]["keyframe_order"][0]]
    assert new_kf.get("layout", "") != "old story" or new_kf.get("layout", "") == ""
    assert not new_kf.get("selected_image_path")
    assert len(cloned["sequence_order"]) == 1

    files_dir = tmp_path / "new-story-files"
    assert files_dir.is_dir()
    assert (files_dir / "previews").is_dir()
    assert (files_dir / "_about-this-folder.md").is_file()
