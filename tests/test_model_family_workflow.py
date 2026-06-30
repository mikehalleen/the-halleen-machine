"""Default vs Custom image model family workflow and sampler rules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from helpers import (
    DEFAULT_PROJECT_WORKFLOW_FILENAME,
    IMAGE_MODEL_FAMILY_CUSTOM,
    IMAGE_MODEL_FAMILY_DEFAULT,
    default_family_pose_workflow_filename,
    first_outline_node_id,
    keyframe_workflow_basename_for_node,
    migrate_keyframes_to_default_workflows,
    migrate_keyframes_to_custom_reference_bindings,
    pose_paths_represent_different_pose,
    should_migrate_keyframes_on_family_change,
    should_migrate_keyframes_to_custom_bindings,
    workflow_filename_for_pose_change,
)
from scripts import workflow_controls as wc
from single_gen_helpers import _workflow_for_asset_test
from src.workflow_capabilities import runtime_injection_flags, scan_workflow_file


def test_custom_sampler_policy_seed_only():
    workflow = {
        "1": {
            "_meta": {"title": "KSampler"},
            "class_type": "KSampler",
            "inputs": {"seed": 1, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal"},
        }
    }
    wc.set_seed(workflow, 999)
    assert workflow["1"]["inputs"]["seed"] == 999
    assert workflow["1"]["inputs"]["steps"] == 20
    assert workflow["1"]["inputs"]["cfg"] == 7.0


def test_default_sampler_policy_overwrites():
    workflow = {
        "1": {
            "_meta": {"title": "KSampler"},
            "class_type": "KSampler",
            "inputs": {"seed": 1, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal"},
        }
    }
    wc.set_generation_settings(
        workflow,
        seed=42,
        cfg=5.0,
        steps=12,
        sampler_name="dpmpp_2m",
        scheduler="karras",
    )
    assert workflow["1"]["inputs"]["seed"] == 42
    assert workflow["1"]["inputs"]["steps"] == 12
    assert workflow["1"]["inputs"]["cfg"] == 5.0


def test_pose_paths_represent_different_pose():
    assert not pose_paths_represent_different_pose("/proj/poses/a.png", "/tmp/a.png")
    assert pose_paths_represent_different_pose("/proj/poses/a.png", "/proj/poses/b.png")
    assert not pose_paths_represent_different_pose("", "")


def test_workflow_filename_for_pose_change_only_on_real_change():
    default_proj = {"project": {"image_model_family": "default"}}
    custom_proj = {"project": {"image_model_family": "custom", "default_workflow_json": "klein.json"}}
    assert workflow_filename_for_pose_change(default_proj, "/p/old.png", "/p/old.png") is None
    assert workflow_filename_for_pose_change(default_proj, "/p/old.png", "/p/new_2CHAR.png") == "pose_2CHAR.json"
    assert workflow_filename_for_pose_change(custom_proj, "/p/old.png", "/p/new_2CHAR.png") is None
    assert (
        workflow_filename_for_pose_change(default_proj, "/p/foo.png", "")
        == DEFAULT_PROJECT_WORKFLOW_FILENAME
    )


def test_default_family_pose_workflow_filename():
    assert default_family_pose_workflow_filename("") == DEFAULT_PROJECT_WORKFLOW_FILENAME
    assert default_family_pose_workflow_filename(None) == DEFAULT_PROJECT_WORKFLOW_FILENAME
    assert default_family_pose_workflow_filename("/poses/foo_1CHAR.png") == "pose_1CHAR.json"
    assert default_family_pose_workflow_filename("/poses/foo_2CHAR.png") == "pose_2CHAR.json"
    assert default_family_pose_workflow_filename("/poses/generic.png") == "pose_1CHAR.json"


def test_should_migrate_keyframes_on_family_change():
    """UI must never mass-rewrite keyframe workflows on family switch."""
    assert not should_migrate_keyframes_on_family_change(
        IMAGE_MODEL_FAMILY_DEFAULT, IMAGE_MODEL_FAMILY_DEFAULT
    )
    assert not should_migrate_keyframes_on_family_change(
        IMAGE_MODEL_FAMILY_CUSTOM, IMAGE_MODEL_FAMILY_CUSTOM
    )
    assert not should_migrate_keyframes_on_family_change(
        IMAGE_MODEL_FAMILY_CUSTOM, IMAGE_MODEL_FAMILY_DEFAULT
    )
    assert not should_migrate_keyframes_on_family_change(
        IMAGE_MODEL_FAMILY_DEFAULT, IMAGE_MODEL_FAMILY_CUSTOM
    )


def test_should_migrate_keyframes_to_custom_bindings():
    assert should_migrate_keyframes_to_custom_bindings(
        IMAGE_MODEL_FAMILY_DEFAULT, IMAGE_MODEL_FAMILY_CUSTOM
    )
    assert not should_migrate_keyframes_to_custom_bindings(
        IMAGE_MODEL_FAMILY_CUSTOM, IMAGE_MODEL_FAMILY_DEFAULT
    )
    assert not should_migrate_keyframes_to_custom_bindings(
        IMAGE_MODEL_FAMILY_DEFAULT, IMAGE_MODEL_FAMILY_DEFAULT
    )


def test_migrate_keyframes_to_custom_reference_bindings():
    wf = "pixa-four-image_vague.json"
    proj = {
        "project": {
            "image_model_family": "custom",
            "default_workflow_json": wf,
            "characters": [{"id": "c1"}, {"id": "c2"}],
        },
        "sequences": {
            "s1": {
                "setting_id": "loc1",
                "keyframes": {
                    "k1": {
                        "pose": "/poses/jump.png",
                        "characters": ["c1", "c2"],
                        "workflow_json": wf,
                    },
                },
            }
        },
    }
    out = migrate_keyframes_to_custom_reference_bindings(proj)
    bindings = out["sequences"]["s1"]["keyframes"]["k1"]["reference_bindings"]
    semantics = [b.get("semantic") for b in bindings.values()]
    assert "pose" in semantics
    assert sum(1 for b in bindings.values() if b.get("character_id") == "c1") == 1
    assert sum(1 for b in bindings.values() if b.get("character_id") == "c2") == 1
    assert any(b.get("semantic") == "location" and b.get("source") == "sequence" for b in bindings.values())


def test_migrate_keyframes_to_default_workflows():
    """Repair utility only — not invoked from the UI on family switch."""
    proj = {
        "project": {"image_model_family": "default"},
        "sequences": {
            "s1": {
                "keyframes": {
                    "k1": {"pose": "", "workflow_json": "klein_multi_image.json"},
                    "k2": {"pose": "/p/x_2CHAR.png", "workflow_json": "klein_multi_image.json"},
                }
            }
        },
    }
    out = migrate_keyframes_to_default_workflows(proj)
    k1 = out["sequences"]["s1"]["keyframes"]["k1"]
    k2 = out["sequences"]["s1"]["keyframes"]["k2"]
    assert k1["workflow_json"].endswith("pose_OPEN.json")
    assert k2["workflow_json"].endswith("pose_2CHAR.json")


def test_migrate_skips_custom_family():
    proj = {
        "project": {"image_model_family": "custom"},
        "sequences": {"s1": {"keyframes": {"k1": {"workflow_json": "klein_multi_image.json"}}}},
    }
    out = migrate_keyframes_to_default_workflows(proj)
    assert out["sequences"]["s1"]["keyframes"]["k1"]["workflow_json"] == "klein_multi_image.json"


def test_keyframe_workflow_basename_for_node():
    proj = {
        "sequences": {
            "s1": {
                "keyframes": {
                    "id1": {"workflow_json": "/workflows/klein_multi_image.json"},
                }
            }
        }
    }
    assert keyframe_workflow_basename_for_node(proj, "id1") == "klein_multi_image.json"
    assert keyframe_workflow_basename_for_node(proj, "s1") is None
    assert keyframe_workflow_basename_for_node(proj, None) is None


def test_first_outline_node_id():
    proj = {
        "sequence_order": ["s1"],
        "sequences": {
            "s1": {
                "id": "s1",
                "keyframes": {},
                "videos": {},
                "video_order": [],
            }
        },
    }
    assert first_outline_node_id(proj) == "s1"
    assert first_outline_node_id({}) is None


def test_asset_session_workflow_custom_only():
    custom = {"project": {"image_model_family": "custom", "default_workflow_json": "klein_multi_image.json"}}
    look = {"default_workflow_json": "pose_OPEN.json"}
    path = _workflow_for_asset_test(
        custom,
        pose_path="/poses/foo_1CHAR.png",
        kind="character",
        look_flat=look,
    )
    assert path.endswith("pose_OPEN.json")

    default = {"project": {"image_model_family": "default"}}
    path_default = _workflow_for_asset_test(
        default,
        pose_path="/poses/foo_1CHAR.png",
        kind="character",
        look_flat={"default_workflow_json": "klein_multi_image.json"},
    )
    assert path_default.endswith("klein_multi_image.json")


def test_runtime_injection_flags_klein():
    caps = scan_workflow_file("klein_multi_image.json")
    assert caps.error is None, caps.error
    inj = runtime_injection_flags(caps)
    assert inj.use_prompt
    assert not inj.use_two_char
