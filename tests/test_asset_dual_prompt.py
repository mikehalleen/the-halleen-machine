"""Dual keyframe vs generator asset prompt schema."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from helpers import inject_the_machine_snapshot
from single_gen_helpers import (
    TEST_CHARACTER_DEFAULT_NEGATIVE,
    TEST_SETTING_DEFAULT_NEGATIVE,
    _asset_generator_negative,
    _asset_generator_prompt,
    _create_temp_json_for_character_test,
    _create_temp_json_for_setting_asset_test,
    _create_temp_json_for_style_asset_test,
    _workflow_for_asset_test,
    apply_look_context_to_temp_project,
    recall_asset_generation_from_reference,
)


def test_asset_generator_prompt_fallback():
    assert _asset_generator_prompt({"prompt": "keyframe"}) == "keyframe"
    assert _asset_generator_prompt({"prompt": "keyframe", "generator_prompt": "gen"}) == "gen"
    assert _asset_generator_prompt({}) == ""


def test_asset_generator_negative_fallback():
    assert _asset_generator_negative({"negative_prompt": "kf"}) == "kf"
    assert _asset_generator_negative(
        {"negative_prompt": "kf", "generator_negative_prompt": "gen"}
    ) == "gen"


def test_character_temp_json_uses_generator_fields_without_mutating_source():
    full_data = {"project": {"name": "test", "characters": [], "settings": [], "styles": []}}
    char = {
        "id": "c1",
        "name": "Hero",
        "prompt": "keyframe clean",
        "generator_prompt": "lora heavy gen",
        "negative_prompt": "kf neg",
        "generator_negative_prompt": "gen neg",
    }
    temp_data, seq_id, kf_id = _create_temp_json_for_character_test(full_data, char, pose_path="")
    test_char = temp_data["project"]["characters"][0]
    assert test_char["prompt"] == "lora heavy gen"
    assert test_char["negative_prompt"].startswith("gen neg,")
    assert TEST_CHARACTER_DEFAULT_NEGATIVE in test_char["negative_prompt"]
    assert char["prompt"] == "keyframe clean"
    assert char["negative_prompt"] == "kf neg"


def test_setting_temp_json_uses_generator_prompt():
    full_data = {"project": {"name": "test", "characters": [], "settings": [], "styles": []}}
    setting = {
        "id": "s1",
        "name": "Office",
        "prompt": "keyframe office",
        "generator_prompt": "gen office __lora:x__",
        "negative_prompt": "kf",
        "generator_negative_prompt": "gen neg",
    }
    temp_data, seq_id, kf_id = _create_temp_json_for_setting_asset_test(full_data, setting)
    seq = temp_data["sequences"][seq_id]
    assert "gen office" in seq["setting_prompt"]
    assert "keyframe office" not in seq["setting_prompt"]
    kf = seq["keyframes"][kf_id]
    assert kf["negatives"]["left"].startswith("gen neg,")
    assert TEST_SETTING_DEFAULT_NEGATIVE in kf["negatives"]["left"]
    assert setting["negative_prompt"] == "kf"


def test_apply_look_context_merges_project_fields():
    temp = {"project": {"image_model_family": "default", "negatives": {}}}
    look = {
        "image_model_family": "custom",
        "default_workflow_json": "klein_multi_image.json",
        "model": "flux.safetensors",
        "style_prompt": "cinematic",
        "steps": 20,
        "cfg": 3.5,
        "sampler": "euler",
        "scheduler": "simple",
        "neg_global": "bad",
    }
    apply_look_context_to_temp_project(temp, look)
    proj = temp["project"]
    assert proj["image_model_family"] == "custom"
    assert proj["default_workflow_json"] == "klein_multi_image.json"
    assert proj["model"] == "flux.safetensors"
    assert proj["style_prompt"] == "cinematic"
    assert proj["keyframe_generation"]["steps"] == 20
    assert proj["negatives"]["global"] == "bad"


def test_workflow_from_look_context():
    proj = {"project": {"image_model_family": "default", "default_workflow_json": "pose_OPEN.json"}}
    look = {"default_workflow_json": "klein_multi_image.json"}
    path = _workflow_for_asset_test(proj, look_flat=look)
    assert path.endswith("klein_multi_image.json")


def test_recall_asset_generation_from_reference(tmp_path):
    png = tmp_path / "ref.png"
    from PIL import Image

    Image.new("RGB", (8, 8), color=(1, 2, 3)).save(png)
    snapshot = {
        "generation": {"executed_prompt": "gen executed text"},
        "project_context": {
            "image_model_family": "custom",
            "default_workflow_json": "klein_multi_image.json",
            "style_prompt": "test look",
            "negatives": {"global": "blur"},
        },
        "item_data": {"negatives": {"left": "people"}},
    }
    assert inject_the_machine_snapshot(str(png), snapshot)

    look_flat, gen_prompt, gen_neg, summary, status = recall_asset_generation_from_reference(str(png))
    assert gen_prompt == "gen executed text"
    assert "people" in gen_neg
    assert "blur" in gen_neg
    assert look_flat["default_workflow_json"] == "klein_multi_image.json"
    assert "Session look selected" not in summary
    assert "klein_multi_image.json" in summary
    assert status == "Generation settings loaded."


def test_asset_look_status_project_default():
    from single_gen_helpers import format_asset_look_indicator, format_asset_look_status_parts

    proj = {
        "project": {
            "image_model_family": "default",
            "model": "AnalogMadness5.safetensors",
            "style_prompt": "cinematic",
            "keyframe_generation": {
                "steps": 30,
                "cfg": 3,
                "sampler_name": "dpmpp_2m_sde",
                "scheduler": "karras",
            },
        }
    }
    indicator, details = format_asset_look_status_parts(None, proj, [])
    assert "AnalogMadness5.safetensors" in indicator
    assert "Project Default" in indicator
    assert "Custom workflow" not in indicator
    assert "Look Library" in details
    assert "Workflow:" not in details


def test_asset_look_status_gallery_no_selection():
    from single_gen_helpers import format_asset_look_status_parts

    proj = {
        "project": {
            "image_model_family": "default",
            "model": "AnalogMadness5.safetensors",
        }
    }
    paths = ["/looks/one.png"]
    indicator, details = format_asset_look_status_parts(None, proj, paths)
    assert "Project Default" in indicator
    assert "Select a look above" in details
    assert "model and custom workflow" in details
    assert "Workflow:" not in details


def test_asset_look_status_project_custom():
    from single_gen_helpers import format_asset_look_indicator, format_asset_look_status_parts

    proj = {
        "project": {
            "image_model_family": "custom",
            "default_workflow_json": "klein_multi_image.json",
            "style_prompt": "cinematic",
        }
    }
    indicator = format_asset_look_indicator(None, proj)
    assert "Custom workflow" in indicator
    assert "klein_multi_image.json" in indicator
    assert "Project Default" in indicator
    _, details = format_asset_look_status_parts(None, proj, [])
    assert "Look Library" in details
    assert "Workflow:" not in details


def test_asset_look_status_session_selected():
    from single_gen_helpers import format_asset_look_status_parts

    look = {
        "image_model_family": "custom",
        "default_workflow_json": "klein_multi_image.json",
        "style_prompt": "from png",
    }
    indicator, details = format_asset_look_status_parts(look, {"project": {}})
    assert "Custom workflow" in indicator
    assert "klein_multi_image.json" in indicator
    assert "session look" not in indicator
    assert "Project Default" not in indicator
    assert "Workflow:" in details
    assert "from png" in details
