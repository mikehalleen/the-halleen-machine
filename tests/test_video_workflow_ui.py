"""Video workflow path helpers and Generation Defaults dropdown refresh."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from helpers import (
    DEFAULT_PROJECT_WORKFLOW_FILENAME,
    DEFAULT_VIDEO_WORKFLOW_FILENAME,
    WORKFLOWS_DIR,
    cb_master_refresh,
    cb_refresh_video_workflow_dropdown,
    effective_default_workflow_filename,
    video_workflow_dropdown_to_path,
    video_workflow_path_to_dropdown,
)


def test_video_workflow_path_to_dropdown_strips_directory():
    stored = r"C:\Users\halle\the-machine-ui\workflows\i2v_base.json"
    assert video_workflow_path_to_dropdown(stored) == "i2v_base.json"


def test_video_workflow_path_to_dropdown_empty_uses_default():
    assert video_workflow_path_to_dropdown("") == DEFAULT_VIDEO_WORKFLOW_FILENAME
    assert video_workflow_path_to_dropdown(None) == DEFAULT_VIDEO_WORKFLOW_FILENAME


def test_video_workflow_dropdown_to_path_resolves_under_workflows_dir():
    path = video_workflow_dropdown_to_path("i2v_bridge.json")
    assert path.endswith("i2v_bridge.json")
    assert Path(path).parent == WORKFLOWS_DIR.resolve()


def test_video_workflow_dropdown_to_path_empty_uses_default():
    path = video_workflow_dropdown_to_path("")
    assert path.endswith(DEFAULT_VIDEO_WORKFLOW_FILENAME)
    assert Path(path).parent == WORKFLOWS_DIR.resolve()


def test_cb_refresh_video_workflow_dropdown_includes_file_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.WORKFLOWS_DIR", tmp_path.resolve())
    (tmp_path / "i2v_base.json").write_text("{}", encoding="utf-8")
    (tmp_path / "THM_video_wan2_2_14B_fun_inpaint.json").write_text("{}", encoding="utf-8")

    upd = cb_refresh_video_workflow_dropdown(
        str(tmp_path / "THM_video_wan2_2_14B_fun_inpaint.json")
    )
    assert "THM_video_wan2_2_14B_fun_inpaint.json" in upd["choices"]
    assert upd["value"] == "THM_video_wan2_2_14B_fun_inpaint.json"


def test_cb_refresh_video_workflow_dropdown_preserves_stored_basename(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.WORKFLOWS_DIR", tmp_path.resolve())
    (tmp_path / "brand_new_export.json").write_text("{}", encoding="utf-8")

    upd = cb_refresh_video_workflow_dropdown(str(tmp_path / "brand_new_export.json"))
    assert "brand_new_export.json" in upd["choices"]
    assert upd["value"] == "brand_new_export.json"


def test_cb_master_refresh_return_order_matches_ui_outputs(monkeypatch):
    def fake_refresh_all_lists(*args, **kwargs):
        return tuple(f"slot{i}" for i in range(6))

    monkeypatch.setattr("helpers.cb_refresh_all_lists", fake_refresh_all_lists)
    monkeypatch.setattr(
        "helpers.cb_refresh_video_workflow_dropdown",
        lambda current_value: "video-slot",
    )

    result = cb_master_refresh("", "", "", {}, "", "", "", "", "", "")
    assert result == ("slot0", "slot1", "slot2", "slot3", "video-slot", "slot4", "slot5")


def test_cb_master_refresh_uses_keyframe_not_video_for_editor_workflow(monkeypatch):
    captured: dict[str, str] = {}

    def fake_refresh_all_lists(
        workspace_dir,
        models_dir,
        loras_dir,
        project_json,
        current_model,
        current_lora,
        current_workflow,
        current_pose,
        current_project,
    ):
        captured["current_workflow"] = current_workflow
        return tuple({"idx": i} for i in range(6))

    monkeypatch.setattr("helpers.cb_refresh_all_lists", fake_refresh_all_lists)
    monkeypatch.setattr(
        "helpers.cb_refresh_video_workflow_dropdown",
        lambda current_value: {"video": current_value},
    )

    project = {
        "project": {
            "image_model_family": "custom",
            "default_workflow_json": "klein.json",
            "inbetween_generation": {
                "video_workflow_json": str(WORKFLOWS_DIR / "THM_video_wan2_2_14B_fun_inpaint.json"),
            },
        }
    }
    video_path = project["project"]["inbetween_generation"]["video_workflow_json"]

    result = cb_master_refresh(
        "/ws",
        "/models",
        "/loras",
        project,
        "",
        "",
        effective_default_workflow_filename(project),
        video_path,
        "",
        "/ws/project.json",
    )

    assert captured["current_workflow"] == "klein.json"
    assert captured["current_workflow"] != Path(video_path).name
    assert len(result) == 7
    assert result[4]["video"] == video_path


def test_load_project_complete_passes_separate_keyframe_and_video_refresh(monkeypatch):
    import helpers

    captured: dict[str, str] = {}

    def fake_master_refresh(
        workspace_dir,
        models_dir,
        loras_dir,
        project_json,
        current_model,
        current_lora,
        current_kf_workflow,
        current_video_workflow,
        current_pose,
        current_project,
    ):
        captured["kf"] = current_kf_workflow
        captured["video"] = current_video_workflow
        return tuple({"idx": i} for i in range(7))

    class FakeForm:
        def load_from_json(self, data):
            return ["form-value"]

        def get_outputs(self):
            return []

    monkeypatch.setattr(helpers, "cb_open_file", lambda fp, settings: ({"project": {}}, fp))
    monkeypatch.setattr(helpers, "cb_master_refresh", fake_master_refresh)
    monkeypatch.setattr(helpers, "refresh_pose_components", lambda *a, **k: (None, {}, {}))
    monkeypatch.setattr(helpers, "get_project_poses_dir", lambda *a, **k: None)

    project_data = {
        "project": {
            "image_model_family": "custom",
            "default_workflow_json": "pose_OPEN.json",
            "model": "model.safetensors",
            "inbetween_generation": {
                "video_workflow_json": str(WORKFLOWS_DIR / "THM_video_wan2_2_14B_fun_inpaint.json"),
            },
        },
        "sequence_order": ["s1"],
        "sequences": {
            "s1": {
                "id": "s1",
                "keyframes": {
                    "id1": {"workflow_json": "klein_multi_image.json"},
                },
                "keyframe_order": ["id1"],
                "videos": {},
                "video_order": [],
            }
        },
    }

    def fake_open(fp, settings):
        return project_data, fp

    monkeypatch.setattr(helpers, "cb_open_file", fake_open)

    helpers.load_project_complete("/tmp/test.json", "{}", FakeForm(), lambda p: {})

    # First outline row is the sequence; keyframe workflow is applied when a keyframe is selected.
    assert captured["kf"] == DEFAULT_PROJECT_WORKFLOW_FILENAME
    assert captured["video"].endswith("THM_video_wan2_2_14B_fun_inpaint.json")
    assert Path(captured["video"]).name != captured["kf"]


def test_load_project_complete_uses_keyframe_workflow_when_outline_is_keyframe(monkeypatch):
    import helpers

    captured: dict[str, str] = {}

    def fake_master_refresh(
        workspace_dir,
        models_dir,
        loras_dir,
        project_json,
        current_model,
        current_lora,
        current_kf_workflow,
        current_video_workflow,
        current_pose,
        current_project,
    ):
        captured["kf"] = current_kf_workflow
        return tuple({"idx": i} for i in range(7))

    class FakeForm:
        def load_from_json(self, data):
            return ["form-value"]

        def get_outputs(self):
            return []

    monkeypatch.setattr(helpers, "cb_master_refresh", fake_master_refresh)
    monkeypatch.setattr(helpers, "refresh_pose_components", lambda *a, **k: (None, {}, {}))
    monkeypatch.setattr(helpers, "get_project_poses_dir", lambda *a, **k: None)
    monkeypatch.setattr(helpers, "first_outline_node_id", lambda _data: "id1")

    project_data = {
        "project": {
            "image_model_family": "custom",
            "default_workflow_json": "pose_OPEN.json",
            "model": "model.safetensors",
        },
        "sequence_order": ["s1"],
        "sequences": {
            "s1": {
                "id": "s1",
                "keyframes": {
                    "id1": {"workflow_json": "klein_multi_image.json"},
                },
                "keyframe_order": ["id1"],
                "videos": {},
                "video_order": [],
            }
        },
    }

    monkeypatch.setattr(helpers, "cb_open_file", lambda fp, settings: (project_data, fp))

    helpers.load_project_complete("/tmp/test.json", "{}", FakeForm(), lambda p: {})

    assert captured["kf"] == "klein_multi_image.json"


def test_load_project_default_video_family_uses_i2v_base(monkeypatch):
    import helpers

    captured: dict[str, str] = {}

    def fake_master_refresh(
        workspace_dir,
        models_dir,
        loras_dir,
        project_json,
        current_model,
        current_lora,
        current_kf_workflow,
        current_video_workflow,
        current_pose,
        current_project,
    ):
        captured["video"] = current_video_workflow
        return tuple({"idx": i} for i in range(7))

    class FakeForm:
        def load_from_json(self, data):
            return ["form-value"]

        def get_outputs(self):
            return []

    monkeypatch.setattr(helpers, "cb_master_refresh", fake_master_refresh)
    monkeypatch.setattr(helpers, "refresh_pose_components", lambda *a, **k: (None, {}, {}))
    monkeypatch.setattr(helpers, "get_project_poses_dir", lambda *a, **k: None)

    project_data = {
        "project": {
            "video_model_family": "default",
            "inbetween_generation": {
                "video_workflow_json": str(WORKFLOWS_DIR / "THM_video_ltx2_i2v.json"),
            },
        }
    }

    def fake_open(fp, settings):
        return project_data, fp

    monkeypatch.setattr(helpers, "cb_open_file", fake_open)

    helpers.load_project_complete("/tmp/test.json", "{}", FakeForm(), lambda p: {})

    assert captured["video"] == DEFAULT_VIDEO_WORKFLOW_FILENAME
