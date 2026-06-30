"""Project image model family and default workflow helpers."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from helpers import (
    DEFAULT_PROJECT_WORKFLOW_FILENAME,
    IMAGE_MODEL_FAMILY_CUSTOM,
    IMAGE_MODEL_FAMILY_DEFAULT,
    assets_reference_save_visible,
    effective_default_workflow_filename,
    image_model_family,
    is_custom_image_family,
    is_default_image_family,
    project_controls_kf_sampler_settings,
    project_default_workflow_filename,
    resolve_project_default_workflow,
    image_family_label_to_json,
    image_family_json_to_label,
    WORKFLOWS_DIR,
)


def test_missing_family_is_default():
    assert image_model_family({}) == IMAGE_MODEL_FAMILY_DEFAULT
    assert is_default_image_family({})
    assert assets_reference_save_visible({})


def test_custom_family():
    proj = {"project": {"image_model_family": "custom"}}
    assert is_custom_image_family(proj)
    assert assets_reference_save_visible(proj)
    assert not project_controls_kf_sampler_settings(proj)


def test_default_family_controls_sampler():
    assert project_controls_kf_sampler_settings({"project": {"image_model_family": "default"}})
    assert project_controls_kf_sampler_settings({})


def test_default_workflow_filename_fallback():
    assert project_default_workflow_filename({}) == DEFAULT_PROJECT_WORKFLOW_FILENAME
    assert project_default_workflow_filename({"project": {"default_workflow_json": "klein_multi_image.json"}}) == "klein_multi_image.json"


def test_resolve_default_workflow_path():
    path = resolve_project_default_workflow({"project": {"default_workflow_json": "pose_OPEN.json"}})
    assert path.endswith("pose_OPEN.json")
    assert Path(path).parent == WORKFLOWS_DIR.resolve()


def test_effective_workflow_default_family_ignores_stored_klein():
    proj = {
        "project": {
            "image_model_family": "default",
            "default_workflow_json": "klein_multi_image.json",
        }
    }
    assert effective_default_workflow_filename(proj) == DEFAULT_PROJECT_WORKFLOW_FILENAME
    assert resolve_project_default_workflow(proj).endswith("pose_OPEN.json")


def test_custom_family_keeps_stored_workflow():
    proj = {
        "project": {
            "image_model_family": "custom",
            "default_workflow_json": "klein_multi_image.json",
        }
    }
    assert effective_default_workflow_filename(proj) == "klein_multi_image.json"
    assert not project_controls_kf_sampler_settings(proj)


def test_custom_pose_open_sampler_not_project_driven():
    proj = {
        "project": {
            "image_model_family": "custom",
            "default_workflow_json": "pose_OPEN.json",
        }
    }
    assert effective_default_workflow_filename(proj) == "pose_OPEN.json"
    assert not project_controls_kf_sampler_settings(proj)


def test_family_label_roundtrip():
    assert image_family_label_to_json("Custom") == IMAGE_MODEL_FAMILY_CUSTOM
    assert image_family_label_to_json("Default") == IMAGE_MODEL_FAMILY_DEFAULT
    assert image_family_json_to_label("custom") == "Custom"
    assert image_family_json_to_label("default") == "Default"
