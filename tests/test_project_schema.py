import copy
import json
from pathlib import Path

from helpers import _ensure_project, migrate_project_v2, normalize_project_shape, parse_nid


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with (FIXTURES / name).open("r", encoding="utf-8") as f:
        return json.load(f)


def test_migrate_project_v2_converts_legacy_sequences_and_preserves_order():
    data = load_fixture("legacy_project_v1.json")

    migrated = migrate_project_v2(copy.deepcopy(data))

    assert migrated["sequence_order"] == ["seq_legacy"]
    assert isinstance(migrated["sequences"], dict)

    seq = migrated["sequences"]["seq_legacy"]
    assert seq["id"] == "seq_legacy"
    assert seq["keyframe_order"] == ["kf_a", "kf_b"]
    assert list(seq["keyframes"]) == ["kf_a", "kf_b"]
    assert "i2v_base_images" not in seq
    assert "i2v_videos" not in seq


def test_migrate_project_v2_derives_video_gaps_from_keyframe_order():
    data = load_fixture("legacy_project_v1.json")

    seq = migrate_project_v2(copy.deepcopy(data))["sequences"]["seq_legacy"]

    assert len(seq["video_order"]) == 3
    videos = [seq["videos"][video_id] for video_id in seq["video_order"]]
    assert (videos[0]["start_keyframe_id"], videos[0]["end_keyframe_id"]) == (None, "kf_a")
    assert (videos[1]["start_keyframe_id"], videos[1]["end_keyframe_id"]) == ("kf_a", "kf_b")
    assert videos[1]["id"] == "vid_existing"
    assert videos[1]["duration_override_sec"] == 6
    assert (videos[2]["start_keyframe_id"], videos[2]["end_keyframe_id"]) == ("kf_b", None)

    for video in videos:
        assert video["sequence_id"] == "seq_legacy"
        assert video["type"] == "video"
        assert "start_id" not in video
        assert "end_id" not in video


def test_ensure_project_repairs_required_defaults_and_existing_v2_sequence():
    data = load_fixture("minimal_project_v2.json")
    data["project"]["characters"] = "not-a-list"
    data["project"].pop("settings")
    data["project"].pop("styles")
    data["project"].pop("comfy")
    data["project"].pop("inbetween_generation")

    repaired = _ensure_project(copy.deepcopy(data))

    project = repaired["project"]
    assert project["characters"] == []
    assert project["settings"] == []
    assert project["styles"] == []
    assert project["comfy"]["timeout_seconds"] == 3600
    assert project["comfy"]["output_root"]
    assert project["inbetween_generation"]["duration_default_sec"] == 3.0

    seq = repaired["sequences"]["seq1"]
    assert seq["id"] == "seq1"
    assert seq["type"] == "sequence"
    assert seq["video_plan"] == {"open_start": False, "open_end": True}


def test_normalize_project_shape_moves_top_level_project_values_under_project():
    normalized = normalize_project_shape(
        {
            "name": "loose_project",
            "width": 1024,
            "height": 576,
            "project": {},
        }
    )

    assert normalized["project"]["name"] == "loose_project"
    assert normalized["project"]["width"] == 1024
    assert normalized["project"]["height"] == 576
    assert "name" not in normalized
    assert "width" not in normalized
    assert "height" not in normalized
    assert normalized["sequences"] == {}


def test_parse_nid_supports_legacy_ids_and_rejects_unknown_shapes():
    assert parse_nid("kf:2:kf_abc") == ("kf", 2, "kf_abc")
    assert parse_nid("seq:0") == ("seq", 0, None)
    assert parse_nid("kf:not-an-index:kf_abc") == (None, None, None)
    assert parse_nid("plain-id") == (None, None, None)
    assert parse_nid(None) == (None, None, None)
