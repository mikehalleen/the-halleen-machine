"""Look context workflow resolution for Assets tab generation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from single_gen_helpers import _workflow_for_asset_test, _resolve_session_asset_workflow_path


WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"


def test_look_context_overrides_project_workflow():
    proj = {"project": {"image_model_family": "custom", "default_workflow_json": "klein_multi_image.json"}}
    look = {"default_workflow_json": "pose_OPEN.json", "image_model_family": "custom"}
    path = _workflow_for_asset_test(proj, kind="character", look_flat=look)
    assert path.endswith("pose_OPEN.json")


def test_look_context_ignored_when_missing_workflow():
    proj = {"project": {"image_model_family": "custom", "default_workflow_json": "klein_multi_image.json"}}
    path = _workflow_for_asset_test(proj, kind="character", look_flat={})
    assert path.endswith("klein_multi_image.json")


def test_look_context_ignored_in_default_family_without_custom_override():
    proj = {"project": {"image_model_family": "default"}}
    look = {"default_workflow_json": "klein_multi_image.json"}
    path = _workflow_for_asset_test(proj, kind="character", look_flat=look)
    assert path.endswith("klein_multi_image.json")


def test_resolve_session_workflow_path_still_available_for_agent():
    assert _resolve_session_asset_workflow_path("pose_OPEN.json")
    assert _resolve_session_asset_workflow_path("") is None
    assert _resolve_session_asset_workflow_path("not_a_real_workflow_xyz.json") is None
