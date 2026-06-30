"""Keyframe pose persistence via _eh_kf_fields (gallery generate/upload chains rely on this)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from editor_helpers import DEFAULT_KF_CN_SETTINGS, _eh_kf_fields, _eh_workflow_update_if_pose_changed


def _minimal_kf_project(tmp_path: Path, pose_path: str | None = None) -> dict:
    return {
        "project": {
            "name": "test_proj",
            "comfy": {"output_root": str(tmp_path)},
        },
        "sequences": {
            "seq1": {
                "keyframes": {
                    "id1": {
                        "id": "id1",
                        "pose": pose_path or "",
                        "layout": "",
                        "workflow_json": "pose_OPEN.json",
                        "characters": ["", ""],
                        "negatives": {"left": "", "right": "", "heal": ""},
                        "controlnet_settings": {},
                    }
                },
                "keyframe_order": ["id1"],
            }
        },
    }


def _kf_fields_defaults():
    cn = DEFAULT_KF_CN_SETTINGS
    return (
        cn["1"]["switch"] == "On",
        False,
        False,
        False,
        cn["1"]["strength"],
        cn["1"]["start_percent"],
        cn["1"]["end_percent"],
        cn["2"]["switch"] == "On",
        cn["2"]["strength"],
        cn["2"]["start_percent"],
        cn["2"]["end_percent"],
        cn["3"]["switch"] == "On",
        cn["3"]["strength"],
        cn["3"]["start_percent"],
        cn["3"]["end_percent"],
        "",
        "",
        "pose_OPEN.json",
        "",
        "",
        "",
        1,
        0,
        "",
        "",
    )


def test_eh_kf_fields_persists_pose_path(tmp_path):
    poses_dir = tmp_path / "test_proj" / "_poses"
    poses_dir.mkdir(parents=True)
    pose_file = poses_dir / "generated_pose_1CHAR.png"
    pose_file.write_bytes(b"png")

    project = _minimal_kf_project(tmp_path)
    out = _eh_kf_fields(
        project,
        "id1",
        "test_proj",
        str(pose_file),
        *_kf_fields_defaults(),
    )
    kf = out["sequences"]["seq1"]["keyframes"]["id1"]
    assert kf["pose"] == str(pose_file)


def test_eh_kf_fields_clears_pose_when_empty(tmp_path):
    poses_dir = tmp_path / "test_proj" / "_poses"
    poses_dir.mkdir(parents=True)
    old_pose = poses_dir / "old.png"
    old_pose.write_bytes(b"png")

    project = _minimal_kf_project(tmp_path, str(old_pose))
    out = _eh_kf_fields(project, "id1", "test_proj", "", *_kf_fields_defaults())
    assert out["sequences"]["seq1"]["keyframes"]["id1"]["pose"] == ""


def test_workflow_update_if_pose_changed_skips_when_workflow_already_matches():
    project = {
        "project": {"image_model_family": "default"},
        "sequences": {
            "seq1": {
                "keyframes": {
                    "id1": {
                        "pose": "/poses/old.png",
                        "workflow_json": "pose_2CHAR.json",
                    }
                }
            }
        },
    }
    result = _eh_workflow_update_if_pose_changed(project, "id1", "/proj/poses/new_2CHAR.png")
    assert result == {}
