"""Default vs Custom video model family workflow resolution."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from helpers import (
    DEFAULT_VIDEO_WORKFLOW_FILENAME,
    VIDEO_MODEL_FAMILY_CUSTOM,
    VIDEO_MODEL_FAMILY_DEFAULT,
    WORKFLOWS_DIR,
    effective_video_workflow_filename,
    infer_video_model_family,
    is_custom_video_family,
    is_default_video_family,
    migrate_video_to_default_workflow,
    resolve_project_video_workflow,
    should_migrate_video_on_family_change,
    video_family_json_to_label,
    video_family_label_to_json,
    video_model_family,
    _ensure_project,
)
from workflow_capabilities import video_workflow_name_from_project


def test_default_video_family_locks_i2v_base():
    proj = {
        "project": {
            "video_model_family": "default",
            "inbetween_generation": {
                "video_workflow_json": str(WORKFLOWS_DIR / "THM_video_ltx2_i2v.json"),
            },
        }
    }
    assert is_default_video_family(proj)
    assert effective_video_workflow_filename(proj) == DEFAULT_VIDEO_WORKFLOW_FILENAME
    assert Path(resolve_project_video_workflow(proj)).name == DEFAULT_VIDEO_WORKFLOW_FILENAME
    assert video_workflow_name_from_project(proj) == DEFAULT_VIDEO_WORKFLOW_FILENAME


def test_custom_video_family_uses_stored_workflow():
    stored = str(WORKFLOWS_DIR / "THM_video_ltx2_i2v.json")
    proj = {
        "project": {
            "video_model_family": "custom",
            "inbetween_generation": {"video_workflow_json": stored},
        }
    }
    assert is_custom_video_family(proj)
    assert effective_video_workflow_filename(proj) == "THM_video_ltx2_i2v.json"
    assert Path(resolve_project_video_workflow(proj)).name == "THM_video_ltx2_i2v.json"


def test_infer_video_model_family_from_stored_workflow():
    proj = {
        "project": {
            "inbetween_generation": {
                "video_workflow_json": str(WORKFLOWS_DIR / "THM_video_ltx2_i2v.json"),
            },
        }
    }
    assert infer_video_model_family(proj) == VIDEO_MODEL_FAMILY_CUSTOM
    assert video_model_family(proj) == VIDEO_MODEL_FAMILY_CUSTOM


def test_infer_default_when_i2v_base_stored():
    proj = {
        "project": {
            "inbetween_generation": {
                "video_workflow_json": str(WORKFLOWS_DIR / DEFAULT_VIDEO_WORKFLOW_FILENAME),
            },
        }
    }
    assert infer_video_model_family(proj) == VIDEO_MODEL_FAMILY_DEFAULT


def test_should_migrate_video_on_family_change():
    assert should_migrate_video_on_family_change(
        VIDEO_MODEL_FAMILY_CUSTOM, VIDEO_MODEL_FAMILY_DEFAULT
    )
    assert not should_migrate_video_on_family_change(
        VIDEO_MODEL_FAMILY_DEFAULT, VIDEO_MODEL_FAMILY_CUSTOM
    )


def test_migrate_video_to_default_workflow():
    proj = {
        "project": {
            "video_model_family": "default",
            "inbetween_generation": {
                "video_workflow_json": str(WORKFLOWS_DIR / "THM_video_ltx2_i2v.json"),
                "seed_target_title": "THM-Seed",
            },
        }
    }
    out = migrate_video_to_default_workflow(proj)
    ib = out["project"]["inbetween_generation"]
    assert Path(ib["video_workflow_json"]).name == DEFAULT_VIDEO_WORKFLOW_FILENAME
    assert ib["seed_target_title"] == "SlowMoPrimer"
    assert ib["seed_exclude_title"] == "WanFixedSeed"


def test_ensure_project_sets_video_model_family_when_missing():
    data = _ensure_project(
        {
            "project": {
                "inbetween_generation": {
                    "video_workflow_json": str(WORKFLOWS_DIR / "THM_video_ltx2_i2v.json"),
                },
            },
            "sequences": {},
        }
    )
    assert data["project"]["video_model_family"] == VIDEO_MODEL_FAMILY_CUSTOM


def test_video_family_label_converters():
    assert video_family_label_to_json("Custom") == VIDEO_MODEL_FAMILY_CUSTOM
    assert video_family_label_to_json("Default") == VIDEO_MODEL_FAMILY_DEFAULT
    assert video_family_json_to_label("custom") == "Custom"
    assert video_family_json_to_label("default") == "Default"
