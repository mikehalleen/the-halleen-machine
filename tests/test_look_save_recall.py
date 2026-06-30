"""Look library save naming and recall metadata."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from helpers import DEFAULT_PROJECT_WORKFLOW_FILENAME, IMAGE_MODEL_FAMILY_CUSTOM, IMAGE_MODEL_FAMILY_DEFAULT
from single_gen_helpers import (
    _flat_look_fields_from_context,
    looks_save_basename,
    look_project_context_from_project,
)


def test_looks_save_basename_default_family():
    proj = {
        "project": {
            "image_model_family": "default",
            "default_workflow_json": "pose_OPEN.json",
            "model": "checkpoints/foo.safetensors",
            "style_prompt": "cinematic soft light",
            "keyframe_generation": {
                "steps": 30,
                "cfg": 4.0,
                "sampler_name": "dpmpp_2m_sde",
            },
        }
    }
    name = looks_save_basename(proj)
    assert name.startswith("foo-30-4.0-dpmpp_2m_sde-")
    assert "cinematic" in name
    assert not name.startswith("custom_")


def test_looks_save_basename_custom_family():
    proj = {
        "project": {
            "image_model_family": "custom",
            "default_workflow_json": "klein_multi_image.json",
            "style_prompt": "editorial portrait style",
        }
    }
    name = looks_save_basename(proj)
    assert name.startswith("custom_klein_multi_image-")
    assert "editorial" in name


def test_look_project_context_includes_family_and_workflow():
    proj = {
        "project": {
            "image_model_family": "custom",
            "default_workflow_json": "klein.json",
            "style_prompt": "test",
        }
    }
    ctx = look_project_context_from_project(proj)
    assert ctx["image_model_family"] == IMAGE_MODEL_FAMILY_CUSTOM
    assert ctx["default_workflow_json"] == "klein.json"


def test_flat_look_fields_default_family_when_missing():
    flat = _flat_look_fields_from_context({"style_prompt": "x"})
    assert flat["image_model_family"] == IMAGE_MODEL_FAMILY_DEFAULT
    assert flat["default_workflow_json"] == DEFAULT_PROJECT_WORKFLOW_FILENAME


def test_flat_look_fields_preserves_custom():
    flat = _flat_look_fields_from_context(
        {
            "image_model_family": "custom",
            "default_workflow_json": "klein_multi_image.json",
        }
    )
    assert flat["image_model_family"] == IMAGE_MODEL_FAMILY_CUSTOM
    assert flat["default_workflow_json"] == "klein_multi_image.json"
