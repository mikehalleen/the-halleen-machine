import copy
import json
from pathlib import Path

import pytest

from scripts import workflow_controls as wc

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"
VAGUE_FOUR_WORKFLOW = WORKFLOWS_DIR / "pixa-four-image_vague.json"


def _load_vague_four_workflow() -> dict:
    return json.loads(VAGUE_FOUR_WORKFLOW.read_text(encoding="utf-8"))


def _load_workflow_as_image_reference_only(name: str, node_ids: tuple[str, ...]) -> dict:
    """Load a shipped workflow JSON and retag ref loaders in-memory (does not touch disk)."""
    workflow = json.loads((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))
    for node_id in node_ids:
        workflow[node_id]["_meta"]["title"] = "THM-ImageReference"
    return workflow


def node(title, class_type, inputs):
    return {"_meta": {"title": title}, "class_type": class_type, "inputs": dict(inputs)}


def nid(workflow, title: str) -> str:
    matches = wc.find_nodes_by_title(workflow, title)
    assert len(matches) == 1, f"expected one node titled {title!r}, got {len(matches)}"
    return matches[0][0]


def node_by_title(workflow, title: str) -> dict:
    return wc.find_nodes_by_title(workflow, title)[0][1]


def wire(workflow, title: str, output: int = 0) -> list:
    """Node output reference for fixture wiring: ``[node_id, output_index]``."""
    return [nid(workflow, title), output]


def injected_lora_nodes(workflow) -> list[tuple[str, dict]]:
    return [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and wc.node_title(node).startswith("Injected_")
    ]


def test_prompt_prefers_thm_tag_over_legacy_titles():
    workflow = {
        "1": node("THM-Prompt", "CLIPTextEncode", {"text": ""}),
        "2": node("MainPrompt", "CLIPTextEncode", {"text": ""}),
        "3": node("LeftPrompt", "CLIPTextEncode", {"text": ""}),
    }

    assert wc.set_prompt(workflow, "tagged prompt") == 1

    assert workflow["1"]["inputs"]["text"] == "tagged prompt"
    assert workflow["2"]["inputs"]["text"] == ""
    assert workflow["3"]["inputs"]["text"] == ""


def test_prompt_uses_all_legacy_aliases_without_thm_tag():
    workflow = {
        "1": node("MainPrompt", "CLIPTextEncode", {"text": ""}),
        "2": node("LeftPrompt", "CLIPTextEncode", {"text": ""}),
    }

    assert wc.set_prompt(workflow, "legacy prompt") == 2

    assert workflow["1"]["inputs"]["text"] == "legacy prompt"
    assert workflow["2"]["inputs"]["text"] == "legacy prompt"


def test_negative_prompt_prefers_tag_and_legacy_fallbacks():
    tagged = {"1": node("THM-NegativePrompt", "CLIPTextEncode", {"text": ""}), "2": node("MainNegPrompt", "CLIPTextEncode", {"text": ""})}
    legacy = {"1": node("MainNegPrompt", "CLIPTextEncode", {"text": ""}), "2": node("NegPrompt", "CLIPTextEncode", {"text": ""})}

    assert wc.set_negative_prompt(tagged, "tag negative") == 1
    assert tagged["1"]["inputs"]["text"] == "tag negative"
    assert tagged["2"]["inputs"]["text"] == ""

    assert wc.set_negative_prompt(legacy, "legacy negative") == 2
    assert legacy["1"]["inputs"]["text"] == "legacy negative"
    assert legacy["2"]["inputs"]["text"] == "legacy negative"


def test_negative_prompt_blank_overwrites_baked_in_workflow_text():
    workflow = {"1": node("THM-NegativePrompt", "CLIPTextEncode", {"text": "baked in negative"})}

    assert wc.set_negative_prompt(workflow, "") == 1

    assert workflow["1"]["inputs"]["text"] == ""


def test_prompt_writes_primitive_string_multiline_value_not_text():
    workflow = {
        "1": node(
            "THM-Prompt",
            "PrimitiveStringMultiline",
            {"value": "baked workflow prompt"},
        ),
    }

    assert wc.set_prompt(workflow, "from project") == 1

    assert workflow["1"]["inputs"]["value"] == "from project"
    assert "text" not in workflow["1"]["inputs"]


def test_prompt_blank_clears_primitive_string_multiline_baked_value():
    workflow = {
        "1": node(
            "THM-Prompt",
            "PrimitiveStringMultiline",
            {"value": "baked workflow prompt"},
        ),
    }

    assert wc.set_prompt(workflow, "") == 1

    assert workflow["1"]["inputs"]["value"] == ""
    assert "text" not in workflow["1"]["inputs"]


def test_checkpoint_writes_tagged_or_legacy_ckpt_name_inputs():
    tagged = {"1": node("THM-Checkpoint", "CheckpointLoaderSimple", {"ckpt_name": "old.safetensors"})}
    legacy = {"1": node("MainCheckpoint", "CheckpointLoaderSimple", {"ckpt_name": "old.safetensors"})}

    assert wc.set_checkpoint(tagged, "new.safetensors") == 1
    assert wc.set_checkpoint(legacy, "legacy.safetensors") == 1

    assert tagged["1"]["inputs"]["ckpt_name"] == "new.safetensors"
    assert legacy["1"]["inputs"]["ckpt_name"] == "legacy.safetensors"


def test_checkpoint_errors_when_tagged_but_no_model():
    workflow = {"1": node("THM-Checkpoint", "CheckpointLoaderSimple", {"ckpt_name": "old.safetensors"})}

    with pytest.raises(wc.WorkflowControlError):
        wc.set_checkpoint(workflow, None)

    with pytest.raises(wc.WorkflowControlError):
        wc.set_checkpoint(workflow, "   ")


def test_checkpoint_no_tag_allows_missing_model():
    workflow = {"1": node("PoseCheckPoint", "CheckpointLoaderSimple", {"ckpt_name": "baked.safetensors"})}

    assert wc.set_checkpoint(workflow, None) == 0
    assert workflow["1"]["inputs"]["ckpt_name"] == "baked.safetensors"


def test_save_image_prefix_prefers_tag_and_falls_back_to_save_image_title():
    tagged = {
        "1": node("THM-SaveImage", "SaveImage", {"filename_prefix": "old", "output_dir": "old-dir"}),
        "2": node("Save Image", "SaveImage", {"filename_prefix": "legacy"}),
    }
    legacy = {"1": node("Save Image", "SaveImage", {"filename_prefix": "legacy"})}

    assert wc.set_save_image_prefix(tagged, "project/seq/kf") == 1
    assert tagged["1"]["inputs"]["filename_prefix"] == "project/seq/kf"
    assert "output_dir" not in tagged["1"]["inputs"]
    assert tagged["2"]["inputs"]["filename_prefix"] == "legacy"

    assert wc.set_save_image_prefix(legacy, "project/seq/kf") == 1
    assert legacy["1"]["inputs"]["filename_prefix"] == "project/seq/kf"


def test_pose_reference_supports_legacy_main_image_and_mask_only():
    legacy = {"1": node("MainImageAndMask", "LoadImage", {"image": ""})}
    assert wc.set_pose_reference(legacy, "legacy.png") == 1
    assert legacy["1"]["inputs"]["image"] == "legacy.png"


def test_deprecated_reference_tags_are_unknown_not_scanned_controls():
    workflow = {
        "1": node("THM-CharacterReference-1", "LoadImage", {"image": ""}),
        "2": node("THM-LocationReference-background", "LoadImage", {"image": ""}),
    }
    scan = wc.scan_workflow_controls(workflow)
    assert "character_reference" not in scan
    assert "location_reference" not in scan
    unknown_titles = {n["title"] for n in scan.get("unknown", [])}
    assert "THM-CharacterReference-1" in unknown_titles
    assert "THM-LocationReference-background" in unknown_titles


def test_deprecated_setting_reference_is_not_image_reference():
    workflow = {"1": node("THM-SettingReference-bg", "LoadImage", {"image": ""})}
    assert not wc.find_control_nodes(workflow, wc.IMAGE_REFERENCE)


def test_main_image_and_mask_is_pose_reference_only():
    workflow = {"1": node("MainImageAndMask", "LoadImage", {"image": ""})}
    assert wc.find_control_nodes(workflow, wc.POSE_REFERENCE)
    assert not wc.find_control_nodes(workflow, wc.IMAGE_REFERENCE)


def test_thm_tags_are_case_insensitive():
    assert wc._parse_tag("thm-prompt") == (wc.PROMPT, None)
    assert wc._parse_tag("THM-ImageReference") == (wc.IMAGE_REFERENCE, None)
    assert wc._parse_tag("Thm-Lora") == (wc.LORA, None)
    assert wc._parse_tag("THM-Lora-High") == (wc.LORA_HIGH, None)
    assert wc._parse_tag("THM-Lora-Low") == (wc.LORA_LOW, None)
    assert wc._parse_tag("THM-PoseReference") == (None, None)
    assert wc._parse_tag("THM-FutureThing") == (None, None)
    assert wc._parse_tag("MainPrompt") == (None, None)


def test_discover_image_size_partial_width_only():
    workflow = {"1": node("THM-Width", "Primitive", {"value": 1})}
    disc = wc.discover_image_size_control(workflow)
    assert disc.status == "partial"


def test_discover_generation_klein_like():
    workflow = {
        "1": node("RandomNoise", "RandomNoise", {"noise_seed": 0}),
        "2": node("Flux2Scheduler", "Flux2Scheduler", {"steps": 4}),
        "3": node("CFGGuider", "CFGGuider", {"cfg": 1}),
        "4": node("KSamplerSelect", "KSamplerSelect", {"sampler_name": "euler"}),
    }
    gen = wc.discover_generation_settings_control(workflow)
    assert gen.confirmed_fields() == ["seed", "steps", "cfg", "sampler"]
    assert gen.not_controlled_fields() == ["scheduler"]


def test_dimensions_prefer_tagged_nodes_over_class_fallbacks():
    workflow = {
        "1": node("THM-ImageSize", "CustomSize", {"width": 1, "height": 1}),
        "2": node("THM-Width", "Primitive", {"value": 1}),
        "3": node("THM-Height", "Primitive", {"value": 1}),
        "4": node("EmptyLatentImage", "EmptyLatentImage", {"width": 1, "height": 1}),
    }

    assert wc.set_dimensions(workflow, 1152, 768) == 4

    assert workflow["1"]["inputs"]["width"] == 1152
    assert workflow["1"]["inputs"]["height"] == 768
    assert workflow["2"]["inputs"]["value"] == 1152
    assert workflow["3"]["inputs"]["value"] == 768
    assert workflow["4"]["inputs"]["width"] == 1
    assert workflow["4"]["inputs"]["height"] == 1


def test_dimensions_fall_back_to_known_dimension_classes():
    workflow = {
        "1": node("Empty Latent Image", "EmptyLatentImage", {"width": 1, "height": 1}),
        "2": node("Crop", "ImageCrop", {"width": 1, "height": 1}),
        "3": node("Crop Improved", "InpaintCropImproved", {"output_target_width": 1, "output_target_height": 1}),
    }

    assert wc.set_dimensions(workflow, 1024, 576) == 6

    assert workflow["1"]["inputs"]["width"] == 1024
    assert workflow["1"]["inputs"]["height"] == 576
    assert workflow["2"]["inputs"]["width"] == 1024
    assert workflow["2"]["inputs"]["height"] == 576
    assert workflow["3"]["inputs"]["output_target_width"] == 1024
    assert workflow["3"]["inputs"]["output_target_height"] == 576


def test_generation_settings_prefer_tagged_nodes_and_fall_back_to_classes():
    tagged = {
        "1": node("THM-Seed", "RandomNoise", {"noise_seed": 0}),
        "2": node("THM-Steps", "Primitive", {"value": 0}),
        "3": node("THM-CFG", "Primitive", {"value": 0}),
        "4": node("THM-Sampler", "Primitive", {"value": ""}),
        "5": node("THM-Scheduler", "Primitive", {"value": ""}),
        "6": node("KSampler", "KSampler", {"seed": 0, "steps": 0, "cfg": 0, "sampler_name": "", "scheduler": ""}),
    }
    legacy = {
        "1": node("KSampler", "KSampler", {"seed": 0, "steps": 0, "cfg": 0, "sampler_name": "", "scheduler": ""}),
        "2": node("RandomNoise", "RandomNoise", {"noise_seed": 0}),
    }

    tagged_counts = wc.set_generation_settings(
        tagged,
        seed=123,
        steps=30,
        cfg=4.5,
        sampler_name="dpmpp_2m",
        scheduler="karras",
    )
    legacy_counts = wc.set_generation_settings(
        legacy,
        seed=456,
        steps=20,
        cfg=5.5,
        sampler_name="euler",
        scheduler="simple",
    )

    assert tagged_counts == {"seed": 1, "steps": 1, "cfg": 1, "sampler": 1, "scheduler": 1}
    assert tagged["1"]["inputs"]["noise_seed"] == 123
    assert tagged["2"]["inputs"]["value"] == 30
    assert tagged["3"]["inputs"]["value"] == 4.5
    assert tagged["4"]["inputs"]["value"] == "dpmpp_2m"
    assert tagged["5"]["inputs"]["value"] == "karras"
    assert tagged["6"]["inputs"]["seed"] == 0

    assert legacy_counts == {"seed": 2, "steps": 1, "cfg": 1, "sampler": 1, "scheduler": 1}
    assert legacy["1"]["inputs"]["seed"] == 456
    assert legacy["1"]["inputs"]["steps"] == 20
    assert legacy["1"]["inputs"]["cfg"] == 5.5
    assert legacy["1"]["inputs"]["sampler_name"] == "euler"
    assert legacy["1"]["inputs"]["scheduler"] == "simple"
    assert legacy["2"]["inputs"]["noise_seed"] == 456


def test_read_generation_settings_from_workflow_tagged_and_legacy():
    workflow = {
        "1": node("THM-Steps", "Primitive", {"value": 8}),
        "2": node("THM-CFG", "Primitive", {"value": 1.5}),
        "3": node("THM-Sampler", "Primitive", {"value": "res_multistep"}),
        "4": node("THM-Scheduler", "Primitive", {"value": "simple"}),
        "5": node("KSampler", "KSampler", {"steps": 99, "cfg": 9.0, "sampler_name": "euler", "scheduler": "karras"}),
    }
    baked = wc.read_generation_settings_from_workflow(workflow)
    assert baked["steps"] == 8
    assert baked["cfg"] == 1.5
    assert baked["sampler_name"] == "res_multistep"
    assert baked["scheduler"] == "simple"

    legacy_only = {
        "1": node("KSampler", "KSampler", {"steps": 12, "cfg": 2.0, "sampler_name": "dpmpp_2m", "scheduler": "normal"}),
    }
    assert wc.read_generation_settings_from_workflow(legacy_only) == {
        "steps": 12,
        "cfg": 2.0,
        "sampler_name": "dpmpp_2m",
        "scheduler": "normal",
    }


def test_thm_ksampler_drives_one_of_two_samplers():
    workflow = {
        "1": node(
            "THM-KSampler",
            "KSampler",
            {
                "seed": 0,
                "steps": 4,
                "cfg": 1,
                "sampler_name": "res_multistep",
                "scheduler": "simple",
            },
        ),
        "2": node(
            "KSampler",
            "KSampler",
            {
                "seed": 0,
                "steps": 12,
                "cfg": 3,
                "sampler_name": "euler",
                "scheduler": "karras",
                "denoise": 0.8,
            },
        ),
    }
    counts = wc.set_generation_settings(
        workflow,
        seed=99,
        steps=30,
        cfg=6.0,
        sampler_name="dpmpp_2m_sde",
        scheduler="karras",
    )
    assert counts == {"seed": 2, "steps": 1, "cfg": 1, "sampler": 1, "scheduler": 1}
    assert workflow["1"]["inputs"]["steps"] == 30
    assert workflow["1"]["inputs"]["cfg"] == 6.0
    assert workflow["1"]["inputs"]["sampler_name"] == "dpmpp_2m_sde"
    assert workflow["1"]["inputs"]["scheduler"] == "karras"
    assert workflow["1"]["inputs"]["seed"] == 99
    assert workflow["2"]["inputs"]["steps"] == 12
    assert workflow["2"]["inputs"]["cfg"] == 3
    assert workflow["2"]["inputs"]["sampler_name"] == "euler"
    assert workflow["2"]["inputs"]["scheduler"] == "karras"
    assert workflow["2"]["inputs"]["denoise"] == 0.8
    assert workflow["2"]["inputs"]["seed"] == 99


def test_partial_thm_steps_does_not_bleed_cfg_to_untagged_ksampler():
    workflow = {
        "1": node(
            "THM-Steps",
            "KSampler",
            {"steps": 5, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "seed": 0},
        ),
        "2": node(
            "KSampler",
            "KSampler",
            {"steps": 8, "cfg": 2.0, "sampler_name": "dpmpp_2m", "scheduler": "karras", "seed": 0},
        ),
    }
    counts = wc.set_generation_settings(
        workflow,
        seed=1,
        steps=30,
        cfg=9.0,
        sampler_name="uni_pc",
        scheduler="normal",
    )
    assert counts["steps"] == 1
    assert counts["cfg"] == 0
    assert counts["sampler"] == 0
    assert counts["scheduler"] == 0
    assert workflow["1"]["inputs"]["steps"] == 30
    assert workflow["1"]["inputs"]["cfg"] == 1.0
    assert workflow["2"]["inputs"]["steps"] == 8
    assert workflow["2"]["inputs"]["cfg"] == 2.0


def test_read_generation_settings_from_thm_ksampler():
    workflow = {
        "1": node(
            "THM-KSampler",
            "KSampler",
            {"steps": 4, "cfg": 1, "sampler_name": "res_multistep", "scheduler": "simple"},
        ),
        "2": node(
            "KSampler",
            "KSampler",
            {"steps": 99, "cfg": 9.0, "sampler_name": "euler", "scheduler": "karras"},
        ),
    }
    baked = wc.read_generation_settings_from_workflow(workflow)
    assert baked == {
        "steps": 4,
        "cfg": 1,
        "sampler_name": "res_multistep",
        "scheduler": "simple",
    }


def test_discover_generation_settings_thm_ksampler_selective():
    workflow = {
        "1": node(
            "THM-KSampler",
            "KSampler",
            {"steps": 4, "cfg": 1, "sampler_name": "euler", "scheduler": "simple", "seed": 0},
        ),
        "2": node(
            "KSampler",
            "KSampler",
            {"steps": 12, "cfg": 3, "sampler_name": "euler", "scheduler": "karras", "seed": 0},
        ),
    }
    disc = wc.discover_generation_settings_control(workflow)
    assert disc.fields[wc.STEPS].status == "confirmed"
    assert any(
        n.get("title") == "THM-KSampler"
        for mech in disc.fields[wc.STEPS].mechanisms
        for n in mech.get("nodes") or []
    )
    assert disc.fields[wc.CFG].status == "confirmed"
    legacy_ksampler_in_steps = any(
        mech.get("kind") == "legacy_class" and mech.get("class_type") == "KSampler"
        for mech in disc.fields[wc.STEPS].mechanisms
    )
    assert not legacy_ksampler_in_steps


def test_unknown_thm_tags_are_reported_without_failure():
    workflow = {"1": node("THM-HorizontalFlip", "Toggle", {"value": False})}

    scan = wc.scan_workflow_controls(workflow)

    assert scan["unknown"][0]["title"] == "THM-HorizontalFlip"


def test_inject_loras_chains_before_thm_lora_on_unet_workflow():
    workflow = {
        "unet": node("Load Diffusion Model", "UNETLoader", {"unet_name": "base.safetensors"}),
        "lora": node(
            "THM-Lora",
            "LoraLoaderModelOnly",
            {"lora_name": "baked.safetensors", "strength_model": 1.0, "model": ["unet", 0]},
        ),
        "sampling": node("ModelSamplingAuraFlow", "ModelSamplingAuraFlow", {"shift": 3, "model": ["lora", 0]}),
    }

    assert wc.inject_loras(workflow, [("AlienXenomorph.safetensors", "1.0")]) == 1

    injected = injected_lora_nodes(workflow)
    assert len(injected) == 1
    injected_id, injected_node = injected[0]
    assert injected_node["inputs"]["lora_name"] == "AlienXenomorph.safetensors"
    assert injected_node["inputs"]["model"] == wire(workflow, "Load Diffusion Model")
    assert workflow[nid(workflow, "ModelSamplingAuraFlow")]["inputs"]["model"] == [injected_id, 0]
    assert node_by_title(workflow, "THM-Lora")["mode"] == wc.COMFY_NODE_MODE_NEVER
    assert node_by_title(workflow, "THM-Lora")["inputs"]["lora_name"] == "baked.safetensors"


def test_inject_loras_native_lora_loader_marker_never_has_empty_lora_name():
    workflow = {
        "ckpt": node("PoseCheckPoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "lora": node(
            "THM-Lora",
            "LoraLoader",
            {
                "lora_name": "100_Workgear_Lora_v1.3_XL.safetensors",
                "strength_model": 1.0,
                "strength_clip": 1.0,
                "model": ["ckpt", 0],
                "clip": ["ckpt", 1],
            },
        ),
        "sampler": node("KSampler", "KSampler", {"model": ["lora", 0]}),
        "prompt": node("MainPrompt", "CLIPTextEncode", {"text": "test", "clip": ["lora", 1]}),
    }

    assert wc.inject_loras(
        workflow,
        [
            ("SDXL-LORA-genfoo-step00003000.safetensors", 1.0, 0.0),
            ("SDXL-LORA-BixBeez--step00003000.safetensors", 0.75, 0.0),
        ],
    ) == 2

    assert node_by_title(workflow, "THM-Lora")["mode"] == wc.COMFY_NODE_MODE_NEVER
    assert node_by_title(workflow, "THM-Lora")["inputs"]["lora_name"] == "100_Workgear_Lora_v1.3_XL.safetensors"

    injected = injected_lora_nodes(workflow)
    assert len(injected) == 2
    ckpt_ref = wire(workflow, "PoseCheckPoint")
    first_id, first_node = next(
        (node_id, n) for node_id, n in injected if n["inputs"].get("model") == ckpt_ref
    )
    assert first_node["inputs"]["clip"] == [nid(workflow, "PoseCheckPoint"), 1]
    chain_end_id = workflow[nid(workflow, "KSampler")]["inputs"]["model"][0]
    assert workflow[chain_end_id]["inputs"]["model"] == [first_id, 0]
    assert workflow[nid(workflow, "MainPrompt")]["inputs"]["clip"] == [chain_end_id, 1]


def test_inject_loras_clears_baked_rgthree_lora_slots():
    workflow = {
        "ckpt": node("THM-Checkpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "lora": node(
            "THM-Lora",
            "Power Lora Loader (rgthree)",
            {
                "model": ["ckpt", 0],
                "clip": ["ckpt", 1],
                "lora_1": {"on": True, "lora": "baked.safetensors", "strength": 1.0},
                "lora_2": {"on": False, "lora": "other.safetensors", "strength": 0.5},
            },
        ),
        "sampler": node("KSampler", "KSampler", {"model": ["lora", 0]}),
    }

    wc.inject_loras(workflow, [("project.safetensors", 0.5)])

    lora_inputs = node_by_title(workflow, "THM-Lora")["inputs"]
    assert lora_inputs["lora_1"]["on"] is False
    assert lora_inputs["lora_1"]["lora"] == ""
    assert lora_inputs["lora_2"]["lora"] == ""
    assert node_by_title(workflow, "THM-Lora")["mode"] == wc.COMFY_NODE_MODE_BYPASS


def test_inject_loras_zero_project_loras_rewires_past_disabled_marker():
    workflow = {
        "ckpt": node("PoseCheckPoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "lora": node("THM-Lora", "ModelPassThrough", {"model": ["ckpt", 0]}),
        "sampler": node("KSampler", "KSampler", {"model": ["lora", 0]}),
    }

    assert wc.inject_loras(workflow, []) == 0

    assert workflow[nid(workflow, "KSampler")]["inputs"]["model"] == wire(workflow, "THM-Lora")
    assert node_by_title(workflow, "THM-Lora")["mode"] == wc.COMFY_NODE_MODE_BYPASS


def test_inject_loras_zero_loras_still_clears_baked_rgthree_marker():
    workflow = {
        "ckpt": node("MainCheckpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "lora": node(
            "THM-Lora",
            "Power Lora Loader (rgthree)",
            {
                "model": ["ckpt", 0],
                "lora_1": {"on": True, "lora": "baked.safetensors", "strength": 1.0},
            },
        ),
        "sampler": node("KSampler", "KSampler", {"model": ["lora", 0]}),
    }

    assert wc.inject_loras(workflow, []) == 0

    lora_inputs = node_by_title(workflow, "THM-Lora")["inputs"]
    assert lora_inputs["lora_1"]["on"] is False
    assert lora_inputs["lora_1"]["lora"] == ""
    assert node_by_title(workflow, "THM-Lora")["mode"] == wc.COMFY_NODE_MODE_BYPASS


def test_inject_loras_prefers_thm_lora_over_legacy_main_lora():
    workflow = {
        "ckpt": node("THM-Checkpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "thm_lora": node("THM-Lora", "LoraLoaderModelOnly", {"lora_name": "", "strength_model": 1.0, "model": ["ckpt", 0]}),
        "main_lora": node("MainLora", "Power Lora Loader (rgthree)", {"model": ["ckpt", 0]}),
        "sampler": node("KSampler", "KSampler", {"model": ["thm_lora", 0]}),
    }

    assert wc.inject_loras(workflow, [("style.safetensors", "0.8")]) == 1

    injected = injected_lora_nodes(workflow)
    assert len(injected) == 1
    injected_id, injected_node = injected[0]
    assert workflow[nid(workflow, "KSampler")]["inputs"]["model"] == [injected_id, 0]
    assert injected_node["inputs"]["model"] == wire(workflow, "THM-Checkpoint")
    assert node_by_title(workflow, "THM-Lora")["mode"] == wc.COMFY_NODE_MODE_NEVER
    assert workflow[nid(workflow, "MainLora")]["inputs"]["model"] == wire(workflow, "THM-Checkpoint")


def test_inject_loras_legacy_main_lora_before_checkpoint():
    workflow = {
        "ckpt": node("MainCheckpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "lora": node("MainLora", "Power Lora Loader (rgthree)", {"model": ["ckpt", 0], "clip": ["ckpt", 1]}),
        "sampler": node("KSampler", "KSampler", {"model": ["lora", 0]}),
    }

    assert wc.inject_loras(workflow, [("char.safetensors", "1.2")]) == 1

    injected = injected_lora_nodes(workflow)
    assert len(injected) == 1
    injected_id, injected_node = injected[0]
    assert injected_node["class_type"] == "LoraLoader"
    assert injected_node["inputs"]["model"] == wire(workflow, "MainLora")
    assert injected_node["inputs"]["clip"] == [nid(workflow, "MainLora"), 1]
    assert injected_node["inputs"]["strength_clip"] == 1.2
    assert workflow[nid(workflow, "KSampler")]["inputs"]["model"] == [injected_id, 0]
    assert node_by_title(workflow, "MainLora")["mode"] == wc.COMFY_NODE_MODE_BYPASS


def test_detect_lora_clip_support_true_when_marker_has_clip_input():
    workflow = {
        "ckpt": node("MainCheckpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "lora": node("MainLora", "Power Lora Loader (rgthree)", {"model": ["ckpt", 0], "clip": ["ckpt", 1]}),
    }
    lora_id = nid(workflow, "MainLora")
    marker = wc.ControlNode(lora_id, workflow[lora_id], wc.LORA, "MainLora", "Power Lora Loader (rgthree)")

    assert wc.detect_lora_clip_support(workflow, marker) is True


def test_detect_lora_clip_support_true_when_encoder_uses_marker_clip():
    workflow = {
        "ckpt": node("MainCheckpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "lora": node("THM-Lora", "ModelPassThrough", {"model": ["ckpt", 0]}),
        "encoder": node("CLIP Text Encode (Prompt)", "CLIPTextEncode", {"text": "", "clip": ["lora", 1]}),
    }
    lora_id = nid(workflow, "THM-Lora")
    marker = wc.ControlNode(lora_id, workflow[lora_id], wc.LORA, "THM-Lora", "ModelPassThrough")

    assert wc.detect_lora_clip_support(workflow, marker) is True


def test_detect_lora_clip_support_false_for_unet_marker():
    workflow = {
        "unet": node("Load Diffusion Model", "UNETLoader", {"unet_name": "base.safetensors"}),
        "lora": node("THM-Lora", "LoraLoaderModelOnly", {"model": ["unet", 0]}),
    }
    lora_id = nid(workflow, "THM-Lora")
    marker = wc.ControlNode(lora_id, workflow[lora_id], wc.LORA, "THM-Lora", "LoraLoaderModelOnly")

    assert wc.detect_lora_clip_support(workflow, marker) is False


def test_inject_loras_sdxl_rewires_clip_text_encode_to_lora_loader():
    workflow = {
        "ckpt": node("MainCheckpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "lora": node("MainLora", "Power Lora Loader (rgthree)", {"model": ["ckpt", 0], "clip": ["ckpt", 1]}),
        "sampler": node("KSampler", "KSampler", {"model": ["lora", 0]}),
        "encoder": node("CLIP Text Encode (Prompt)", "CLIPTextEncode", {"text": "a woman", "clip": ["lora", 1]}),
    }

    assert wc.inject_loras(workflow, [("char.safetensors", "1.0")]) == 1

    injected_id, _ = injected_lora_nodes(workflow)[0]
    assert workflow[nid(workflow, "CLIP Text Encode (Prompt)")]["inputs"]["clip"] == [injected_id, 1]
    assert node_by_title(workflow, "MainLora")["mode"] == wc.COMFY_NODE_MODE_BYPASS


def test_inject_loras_zero_loras_rewires_clip_through_inline_marker():
    workflow = {
        "ckpt": node("MainCheckpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "lora": node("THM-Lora", "ModelPassThrough", {"model": ["ckpt", 0]}),
        "sampler": node("KSampler", "KSampler", {"model": ["lora", 0]}),
        "encoder": node("CLIP Text Encode (Prompt)", "CLIPTextEncode", {"text": "a woman", "clip": ["lora", 1]}),
    }

    wc.inject_loras(workflow, [])

    assert workflow[nid(workflow, "CLIP Text Encode (Prompt)")]["inputs"]["clip"] == wire(workflow, "THM-Lora", 1)
    assert workflow[nid(workflow, "KSampler")]["inputs"]["model"] == wire(workflow, "THM-Lora")
    assert node_by_title(workflow, "THM-Lora")["mode"] == wc.COMFY_NODE_MODE_BYPASS


def test_scan_workflow_controls_reports_lora_clip_support():
    sdxl = {
        "1": node("MainLora", "Power Lora Loader (rgthree)", {"model": ["0", 0], "clip": ["0", 1]}),
    }
    klein = {
        "1": node("THM-Lora", "LoraLoaderModelOnly", {"model": ["0", 0]}),
    }

    assert wc.scan_workflow_controls(sdxl)["lora_clip_support"] is True
    assert wc.scan_workflow_controls(klein)["lora_clip_support"] is False


def test_2char_legacy_prompt_and_negative_aliases():
    workflow = {
        "1": node("LeftPrompt", "CLIPTextEncode", {"text": ""}),
        "2": node("RightPrompt", "CLIPTextEncode", {"text": ""}),
        "3": node("HealPosPrompt", "CLIPTextEncode", {"text": ""}),
        "4": node("LeftNegPrompt", "CLIPTextEncode", {"text": ""}),
        "5": node("RightNegPrompt", "CLIPTextEncode", {"text": ""}),
        "6": node("HealNegPrompt", "CLIPTextEncode", {"text": ""}),
    }

    assert wc.set_prompt(workflow, "all prompts") == 3
    assert wc.set_negative_prompt(workflow, "all negatives") == 3
    for node_id in ("1", "2", "3"):
        assert workflow[node_id]["inputs"]["text"] == "all prompts"
    for node_id in ("4", "5", "6"):
        assert workflow[node_id]["inputs"]["text"] == "all negatives"


def test_discover_two_char_pipeline_full_slots():
    workflow = {
        "1": node("LeftLora", "Power Lora Loader (rgthree)", {"model": ["0", 0]}),
        "2": node("RightLora", "Power Lora Loader (rgthree)", {"model": ["0", 0]}),
        "3": node("LeftPrompt", "CLIPTextEncode", {"text": ""}),
        "4": node("RightPrompt", "CLIPTextEncode", {"text": ""}),
        "5": node("HealPosPrompt", "CLIPTextEncode", {"text": ""}),
        "6": node("LeftNegPrompt", "CLIPTextEncode", {"text": ""}),
        "7": node("RightNegPrompt", "CLIPTextEncode", {"text": ""}),
        "8": node("HealNegPrompt", "CLIPTextEncode", {"text": ""}),
    }
    disc = wc.discover_two_char_pipeline(workflow)
    assert disc.active
    assert disc.has_heal_pass
    assert disc.slots["lora_left"].present and disc.slots["lora_right"].present
    assert disc.present_slot_count("prompt_") == 3
    assert disc.present_slot_count("neg_") == 3


def test_discover_two_char_pipeline_partial_lora_only():
    workflow = {"1": node("LeftLora", "Power Lora Loader (rgthree)", {"model": ["0", 0]})}
    disc = wc.discover_two_char_pipeline(workflow)
    assert not disc.active
    assert disc.slots["lora_left"].present
    assert not disc.slots["lora_right"].present
    assert not disc.has_heal_pass


def test_left_prompt_only_is_not_two_char_pipeline():
    workflow = {"1": node("LeftPrompt", "CLIPTextEncode", {"text": ""})}
    disc = wc.discover_two_char_pipeline(workflow)
    assert disc.slots["prompt_left"].present
    assert not disc.active
    assert not disc.has_heal_pass


def _dangling_input_refs(workflow: dict) -> list[tuple[str, str, list]]:
    """Return (node_id, input_key, ref) for wire refs pointing at missing nodes."""
    missing = []
    node_ids = set(workflow.keys())
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        for input_key, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and len(value) >= 2 and str(value[0]) not in node_ids:
                missing.append((node_id, input_key, value))
    return missing


def test_strip_prompt_metadata_keeps_subgraph_node_ids():
    path = WORKFLOWS_DIR / "klein_multi_image.json"
    graph = json.loads(path.read_text(encoding="utf-8"))
    colon_before = {k for k in graph if isinstance(k, str) and ":" in k}
    assert colon_before, "fixture must include subgraph node ids"

    graph = copy.deepcopy(graph)
    graph["_is_flux2"] = True
    wc.strip_prompt_metadata(graph)

    assert "_is_flux2" not in graph
    assert colon_before <= set(graph.keys())
    assert "110:78" in graph
    assert _dangling_input_refs(graph) == []


def test_strip_prompt_metadata_numeric_ids_unchanged():
    graph = {
        "1": node("MainPrompt", "CLIPTextEncode", {"text": ""}),
        "2": node("Save Image", "SaveImage", {"images": ["1", 0], "filename_prefix": "out"}),
        "_is_flux2": True,
    }
    wc.strip_prompt_metadata(graph)
    assert graph.keys() == {"1", "2"}
    assert _dangling_input_refs(graph) == []


def test_clear_tagged_reference_nodes_clears_image_reference_only():
    workflow = {
        "1": node("THM-ImageReference", "LoadImage", {"image": "cleared.jpg"}),
        "2": node("THM-PoseReference", "LoadImage", {"image": "stay.jpg"}),
        "3": node("UntaggedLoader", "LoadImage", {"image": "keep_me.jpg"}),
    }
    assert wc.clear_tagged_reference_nodes(workflow) == 1
    assert workflow["1"]["inputs"]["image"] == ""
    assert workflow["2"]["inputs"]["image"] == "stay.jpg"
    assert workflow["3"]["inputs"]["image"] == "keep_me.jpg"


def test_apply_reference_injection_bindings_on_image_reference_slots(tmp_path):
    pose = tmp_path / "jump.png"
    pose.write_bytes(b"x")
    workflow = {
        "1": node("THM-ImageReference", "LoadImage", {"image": "baked.jpg"}),
        "2": node("THM-ImageReference", "LoadImage", {"image": "old_pose.jpg"}),
    }
    project = {
        "characters": [
            {"id": "c1", "name": "Hero", "reference_image": r"D:\proj\_characters\hero.png"},
        ]
    }
    id_conf = {
        "characters": ["c1"],
        "pose": str(pose),
        "reference_bindings": {
            "1": {"semantic": "character", "character_id": "c1"},
            "2": {"semantic": "pose"},
        },
    }
    counts = wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        pose_path=str(pose),
        custom_mode=True,
    )
    assert counts["cleared"] == 2
    assert workflow["2"]["inputs"]["image"] == str(pose)
    assert workflow["1"]["inputs"]["image"] == project["characters"][0]["reference_image"]


def test_apply_reference_injection_empty_binding_leaves_slot_blank():
    workflow = {"1": node("THM-ImageReference", "LoadImage", {"image": "baked.jpg"})}
    project = {"characters": [{"id": "c1", "name": "Hero", "reference_image": ""}]}
    id_conf = {"reference_bindings": {"1": {"semantic": "unset"}}}
    wc.apply_reference_injection(
        workflow, project=project, id_conf=id_conf, pose_path=None, custom_mode=True
    )
    assert workflow["1"]["inputs"]["image"] == ""


def test_resolve_character_reference_paths_by_name():
    project = {"characters": [{"id": "c1", "name": "Hero", "reference_image": "/tmp/hero.png"}]}
    paths = wc.resolve_character_reference_paths(project, {"characters": ["Hero", ""]})
    assert paths[None] == "/tmp/hero.png"


def test_apply_reference_injection_character_via_image_reference_binding():
    workflow = {"1": node("THM-ImageReference", "LoadImage", {"image": "baked.jpg"})}
    ref = r"D:\proj\_characters\c1\reference.png"
    project = {"characters": [{"id": "c1", "name": "Hero", "reference_image": ref}]}
    id_conf = {"reference_bindings": {"1": {"semantic": "character", "character_id": "c1"}}}
    wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        pose_path=None,
        custom_mode=True,
    )
    assert workflow["1"]["inputs"]["image"] == ref


def test_resolve_location_reference_path():
    project = {
        "settings": [
            {"id": "s1", "name": "Moon", "reference_image": r"D:\proj\_locations\s1\reference.png"},
        ]
    }
    seq = {"setting_id": "s1"}
    assert wc.resolve_location_reference_path(project, seq) == r"D:\proj\_locations\s1\reference.png"
    assert wc.resolve_location_reference_path(project, {"setting_id": ""}) == ""


def test_apply_reference_injection_location_via_image_reference_binding(tmp_path):
    loc = tmp_path / "ref.png"
    loc.write_bytes(b"x")
    workflow = {"1": node("THM-ImageReference", "LoadImage", {"image": "baked.jpg"})}
    project = {
        "settings": [{"id": "s1", "reference_image": str(loc)}],
    }
    id_conf = {
        "reference_bindings": {
            "1": {"semantic": "location", "setting_id": "s1", "source": "sequence"},
        },
    }
    counts = wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        sequence={"setting_id": "s1"},
        pose_path=None,
        custom_mode=True,
    )
    assert workflow["1"]["inputs"]["image"] == str(loc)
    assert counts["location"] == 1


def test_discover_reference_wiring_two_image_test():
    workflow = _load_workflow_as_image_reference_only("two-image-test.json", ("76", "81"))
    disc = wc.discover_reference_wiring_order(workflow)
    assert disc.tier == "linear_ref_stack"
    roles = [e.role for e in disc.entries]
    assert roles == ["ref_76", "ref_81"]
    assert [e.image_index for e in disc.entries] == [1, 2]


def test_legacy_klein_multi_image_discovers_no_image_reference_slots():
    caps = wc.discover_image_reference_slots(
        json.loads((WORKFLOWS_DIR / "klein_multi_image.json").read_text(encoding="utf-8"))
    )
    assert caps == []


def test_discover_reference_wiring_klein_multi_image():
    workflow = _load_workflow_as_image_reference_only("klein_multi_image.json", ("76", "81", "120"))
    disc = wc.discover_reference_wiring_order(workflow)
    assert disc.tier == "linear_ref_stack"
    roles = [e.role for e in disc.entries]
    assert roles == ["ref_76", "ref_81", "ref_120"]


def test_mute_inactive_pose_mutes_parallel_reference_latents():
    """Klein-style parallel ReferenceLatent branches must not run with empty LoadImage."""
    workflow = _load_workflow_as_image_reference_only("klein_multi_image.json", ("76", "81", "120"))
    active = {
        "ref_76": False,
        "ref_81": False,
        "ref_120": True,
    }
    char_ref = r"D:\proj\_characters\c1\reference.png"
    project = {"characters": [{"id": "c1", "reference_image": char_ref}], "settings": []}
    id_conf = {"characters": ["c1"]}
    # Inject with fake path then mute (file need not exist for mute test)
    workflow["120"]["inputs"]["image"] = char_ref
    disc = wc.discover_reference_wiring_order(workflow)
    wc.mute_inactive_reference_slots(workflow, disc, active)
    assert workflow["81"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    assert workflow["110:77"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    assert workflow["110:76"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    assert workflow["120"].get("mode") != wc.COMFY_NODE_MODE_NEVER


def test_apply_reference_injection_mutes_empty_character():
    workflow = {
        "10": node("THM-ImageReference", "LoadImage", {"image": "baked.jpg"}),
        "20": node("Zero", "ConditioningZeroOut", {"conditioning": ["99", 0]}),
        "21": node("Enc", "VAEEncode", {"pixels": ["10", 0], "vae": ["99", 0]}),
        "30": node("Ref", "ReferenceLatent", {"conditioning": ["20", 0], "latent": ["21", 0]}),
        "99": node("VAE", "VAELoader", {"vae_name": "x"}),
    }
    project = {"characters": [{"id": "c1", "name": "Hero", "reference_image": ""}]}
    id_conf = {"reference_bindings": {"10": {"semantic": "character", "character_id": "c1"}}}
    counts = wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        pose_path=None,
        custom_mode=True,
    )
    assert workflow["10"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    assert counts["branch_changes"] > 0


def test_apply_reference_branch_activation_skips_inactive_location(tmp_path):
    workflow = {
        "10": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "11": node("THM-ImageReference", "LoadImage", {"image": "pose.png"}),
        "20": node("Zero", "ConditioningZeroOut", {"conditioning": ["99", 0]}),
        "30": node("EncodeLoc", "VAEEncode", {"pixels": ["10", 0], "vae": ["99", 0]}),
        "31": node("EncodePose", "VAEEncode", {"pixels": ["11", 0], "vae": ["99", 0]}),
        "40": node("RefLoc", "ReferenceLatent", {"conditioning": ["20", 0], "latent": ["30", 0]}),
        "41": node("RefPose", "ReferenceLatent", {"conditioning": ["40", 0], "latent": ["31", 0]}),
        "99": node("VAE", "VAELoader", {"vae_name": "x"}),
    }
    pose_file = tmp_path / "pose.png"
    pose_file.write_bytes(b"x")
    workflow["11"]["inputs"]["image"] = str(pose_file)
    discovery = wc.discover_reference_wiring_order(workflow)
    active = {
        "ref_10": False,
        "ref_11": True,
    }
    changes = wc.apply_reference_branch_activation(workflow, discovery, active)
    assert changes > 0
    assert workflow["41"]["inputs"]["conditioning"] == ["20", 0]
    assert workflow["40"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    wc._assign_effective_image_indices(discovery.entries, active)
    assert discovery.entries[1].effective_image_index == 1


def test_compose_reference_prelude_active_slots():
    disc = wc.ReferenceWiringDiscovery(
        tier="linear_ref_stack",
        entries=[
            wc.ReferenceWiringEntry(
                role="ref_11",
                image_index=1,
                effective_image_index=1,
                reference_latent_id="a",
                load_image_id="11",
                title="THM-ImageReference",
                control=wc.IMAGE_REFERENCE,
            ),
        ],
    )
    text = wc.compose_reference_prelude(
        disc,
        {"ref_11": True},
        project={"characters": []},
        sequence={},
        id_conf={"reference_bindings": {"11": {"semantic": "pose"}}},
    )
    assert "image1 defines the pose" in text


def _pixa_four_project(tmp_path, *, c1=True, c2=True, loc=True):
    def mk(name):
        p = tmp_path / name
        p.write_bytes(b"x")
        return str(p)

    return {
        "characters": [
            {
                "id": "c1",
                "name": "Boss",
                "reference_image": mk("c1.png") if c1 else "",
            },
            {
                "id": "c2",
                "name": "Intern",
                "reference_image": mk("c2.png") if c2 else "",
            },
        ],
        "settings": [
            {
                "id": "s1",
                "name": "The Office",
                "reference_image": mk("loc.png") if loc else "",
            },
        ],
    }


def _pixa_four_id_conf(workflow, *, c1=True, c2=True, loc=True, pose=False):
    """Build reference_bindings keyed by Comfy node_id for pixa-four-image workflows."""
    slots = sorted(
        wc.discover_image_reference_slots(workflow),
        key=lambda s: s.image_index or 999,
    )
    bindings: dict[str, dict] = {}
    idx = 0
    if loc and idx < len(slots):
        bindings[wc.binding_key_for_slot(slots[idx])] = {
            "semantic": "location",
            "setting_id": "s1",
            "source": "sequence",
        }
        idx += 1
    if c1 and idx < len(slots):
        bindings[wc.binding_key_for_slot(slots[idx])] = {
            "semantic": "character",
            "character_id": "c1",
        }
        idx += 1
    if c2 and idx < len(slots):
        bindings[wc.binding_key_for_slot(slots[idx])] = {
            "semantic": "character",
            "character_id": "c2",
        }
        idx += 1
    if pose and idx < len(slots):
        bindings[wc.binding_key_for_slot(slots[idx])] = {"semantic": "pose"}
    return {
        "characters": [("c1" if c1 else ""), ("c2" if c2 else "")],
        "reference_bindings": bindings,
    }


def test_legacy_pixa_four_image_discovers_no_image_reference_slots():
    workflow = json.loads((WORKFLOWS_DIR / "pixa-four-image.json").read_text(encoding="utf-8"))
    assert wc.discover_image_reference_slots(workflow) == []


def test_pixa_four_image_omits_location_rewires_both_stacks(tmp_path):
    workflow = _load_vague_four_workflow()
    project = _pixa_four_project(tmp_path, loc=False)
    id_conf = _pixa_four_id_conf(workflow, loc=False)
    wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        pose_path=None,
        sequence={},
        custom_mode=True,
    )
    assert workflow["218"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    assert workflow["224"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    assert workflow["210"]["inputs"]["conditioning"] == ["204", 0]
    assert workflow["209"]["inputs"]["conditioning"] == ["205", 0]
    assert workflow["163"]["inputs"]["positive"] == ["210", 0]
    assert workflow["163"]["inputs"]["negative"] == ["209", 0]


def test_pixa_four_image_omits_pose_rewires_sampler_to_last_active(tmp_path):
    workflow = _load_vague_four_workflow()
    project = _pixa_four_project(tmp_path)
    id_conf = _pixa_four_id_conf(workflow, loc=True, c1=True, c2=True, pose=False)
    wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        pose_path=None,
        sequence={"setting_id": "s1"},
        custom_mode=True,
    )
    assert workflow["226"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    assert workflow["225"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    assert workflow["163"]["inputs"]["positive"] == ["221", 0]
    assert workflow["163"]["inputs"]["negative"] == ["220", 0]


def test_pixa_four_image_prelude_two_chars_no_location(tmp_path):
    workflow = _load_vague_four_workflow()
    project = _pixa_four_project(tmp_path, loc=False)
    id_conf = _pixa_four_id_conf(workflow, loc=False)
    counts = wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        pose_path=None,
        sequence={},
        custom_mode=True,
    )
    text = wc.compose_reference_prelude(
        counts["discovery"],
        counts["active_by_role"],
        project=project,
        sequence={},
        id_conf=id_conf,
    )
    assert "image1 is a character reference." in text
    assert "image2 is a character reference." in text
    assert "setting" not in text
    assert "Boss" not in text
    assert "Intern" not in text


def test_pixa_four_image_prelude_includes_setting_name(tmp_path):
    workflow = _load_vague_four_workflow()
    project = _pixa_four_project(tmp_path, c2=False)
    id_conf = _pixa_four_id_conf(workflow, loc=True, c1=True, c2=False)
    counts = wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        pose_path=None,
        sequence={"setting_id": "s1"},
        custom_mode=True,
    )
    text = wc.compose_reference_prelude(
        counts["discovery"],
        counts["active_by_role"],
        project=project,
        sequence={"setting_id": "s1"},
        id_conf=id_conf,
    )
    assert "image1 is the setting and location reference." in text
    assert "The Office" not in text


def test_discover_reference_wiring_order_is_deterministic():
    workflow = _load_vague_four_workflow()
    first = wc.discover_reference_wiring_order(workflow)
    second = wc.discover_reference_wiring_order(workflow)
    assert [e.reference_latent_id for e in first.entries] == [
        e.reference_latent_id for e in second.entries
    ]
    assert first.entries[0].reference_latent_id == "204"


def test_inject_pose_reference_flips_on_main_image_and_mask():
    workflow = {
        "10": node("MainImageAndMask", "LoadImage", {"image": "pose.png"}),
        "20": node("RefLatent", "ReferenceLatent", {"image": ["10", 0]}),
    }
    inserted = wc.inject_pose_reference_flips(
        workflow,
        {"pose_flip_horizontal": True, "pose_flip_vertical": False},
    )
    assert inserted == 1
    flip_nodes = [
        (nid, n)
        for nid, n in workflow.items()
        if isinstance(n, dict) and n.get("class_type") == "ImageFlip+"
    ]
    assert len(flip_nodes) == 1
    flip_id, flip_node = flip_nodes[0]
    assert flip_node["inputs"]["axis"] == "x"
    assert flip_node["inputs"]["image"] == ["10", 0]
    assert workflow["20"]["inputs"]["image"] == [flip_id, 0]


def test_inject_pose_reference_flips_legacy_main_image_and_mask():
    workflow = {
        "10": node("MainImageAndMask", "LoadImage", {"image": "pose.png"}),
        "20": node("Consumer", "ReferenceLatent", {"image": ["10", 0]}),
    }
    inserted = wc.inject_pose_reference_flips(
        workflow,
        {"pose_flip_horizontal": False, "pose_flip_vertical": True},
    )
    assert inserted == 1
    flip_nodes = [
        n for n in workflow.values() if isinstance(n, dict) and n.get("class_type") == "ImageFlip+"
    ]
    assert len(flip_nodes) == 1
    assert flip_nodes[0]["inputs"]["axis"] == "y"


def test_apply_reference_injection_character_reference_2_empty_with_one_char():
    workflow = {
        "1": node("THM-ImageReference", "LoadImage", {"image": "baked.jpg"}),
        "2": node("THM-ImageReference", "LoadImage", {"image": "baked2.jpg"}),
    }
    ref = r"D:\proj\_characters\c1\reference.png"
    project = {"characters": [{"id": "c1", "name": "Hero", "reference_image": ref}]}
    id_conf = {
        "reference_bindings": {
            "1": {"semantic": "character", "character_id": "c1"},
            "2": {"semantic": "unset"},
        },
    }
    wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        pose_path=None,
        custom_mode=True,
    )
    assert workflow["1"]["inputs"]["image"] == ref
    assert workflow["2"]["inputs"]["image"] == ""


def test_discover_image_reference_slots_mixed_tags():
    """Only THM-ImageReference is discovered; deprecated ref tags are ignored."""
    workflow = {
        "1": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "2": node("THM-ImageReference-2", "LoadImage", {"image": ""}),
        "3": node("THM-PoseReference", "LoadImage", {"image": ""}),
        "4": node("THM-CharacterReference", "LoadImage", {"image": ""}),
    }
    slots = wc.discover_image_reference_slots(workflow)
    assert len(slots) == 2
    assert [s.node_id for s in slots] == ["1", "2"]


def _four_duplicate_image_reference_workflow():
    """Four bare THM-ImageReference tags on a linear ReferenceLatent stack."""
    return {
        "10": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "11": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "12": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "13": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "20": node("Zero", "ConditioningZeroOut", {"conditioning": ["99", 0]}),
        "21": node("Enc1", "VAEEncode", {"pixels": ["10", 0], "vae": ["99", 0]}),
        "22": node("Enc2", "VAEEncode", {"pixels": ["11", 0], "vae": ["99", 0]}),
        "23": node("Enc3", "VAEEncode", {"pixels": ["12", 0], "vae": ["99", 0]}),
        "24": node("Enc4", "VAEEncode", {"pixels": ["13", 0], "vae": ["99", 0]}),
        "30": node("Ref1", "ReferenceLatent", {"conditioning": ["20", 0], "latent": ["21", 0]}),
        "31": node("Ref2", "ReferenceLatent", {"conditioning": ["30", 0], "latent": ["22", 0]}),
        "32": node("Ref3", "ReferenceLatent", {"conditioning": ["31", 0], "latent": ["23", 0]}),
        "33": node("Ref4", "ReferenceLatent", {"conditioning": ["32", 0], "latent": ["24", 0]}),
        "99": node("VAE", "VAELoader", {"vae_name": "x"}),
    }


def test_discover_image_reference_slots_duplicate_titles_get_unique_binding_keys():
    slots = wc.discover_image_reference_slots(_four_duplicate_image_reference_workflow())
    generic = [s for s in slots if s.tag_control == wc.IMAGE_REFERENCE]
    assert len(generic) == 4
    assert [s.binding_key for s in generic] == ["10", "11", "12", "13"]
    assert len({s.binding_key for s in generic}) == 4


def test_inject_duplicate_image_reference_bindings_independently(tmp_path):
    workflow = _four_duplicate_image_reference_workflow()
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    path_a.write_bytes(b"a")
    path_b.write_bytes(b"b")
    project = {
        "characters": [
            {"id": "c1", "name": "Alpha", "reference_image": str(path_a)},
            {"id": "c2", "name": "Beta", "reference_image": str(path_b)},
        ]
    }
    slots = wc.discover_image_reference_slots(workflow)
    kf = {
        "reference_bindings": {
            "10": {"semantic": "character", "character_id": "c1"},
            "11": {"semantic": "character", "character_id": "c2"},
        },
    }
    paths = wc.resolve_reference_paths_from_bindings(kf, slots, project, None)
    wc.inject_reference_slot_paths(workflow, slots, paths)
    assert workflow["10"]["inputs"]["image"] == str(path_a)
    assert workflow["11"]["inputs"]["image"] == str(path_b)
    assert workflow["12"]["inputs"]["image"] == ""
    assert workflow["13"]["inputs"]["image"] == ""


def test_build_reference_active_by_role_duplicate_image_refs(tmp_path):
    path_a = tmp_path / "a.png"
    path_a.write_bytes(b"a")
    slots = wc.discover_image_reference_slots(_four_duplicate_image_reference_workflow())
    paths = {s.binding_key: str(path_a) if s.node_id == "10" else "" for s in slots}
    active = wc.build_reference_active_by_role(
        pose_path=None,
        character_paths={},
        location_path="",
        slots=slots,
        paths_by_slot=paths,
    )
    assert active["ref_10"] is True
    assert active["ref_11"] is False
    assert active["ref_12"] is False
    assert active["ref_13"] is False


def test_patch_discovery_roles_from_slots_duplicate_titles():
    workflow = _four_duplicate_image_reference_workflow()
    slots = wc.discover_image_reference_slots(workflow)
    discovery = wc.discover_reference_wiring_order(workflow)
    wc.patch_discovery_roles_from_slots(discovery, slots)
    roles = [e.role for e in discovery.entries]
    assert roles == ["ref_10", "ref_11", "ref_12", "ref_13"]


def test_discover_image_reference_slots_vague_workflow_file():
    path = Path(__file__).resolve().parent.parent / "workflows" / "pixa-four-image_vague.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))
    slots = wc.discover_image_reference_slots(workflow)
    generic = [s for s in slots if s.tag_control == wc.IMAGE_REFERENCE]
    assert len(generic) == 4
    assert [s.binding_key for s in generic] == ["198", "213", "218", "224"]


def test_remap_reference_bindings_legacy_ordinal_to_node_id(tmp_path):
    root = Path(__file__).resolve().parent.parent / "workflows"
    slots = wc.discover_image_reference_slots(json.loads((root / "pixa-four-image_vague.json").read_text()))
    pin = tmp_path / "loc.png"
    pin.write_bytes(b"x")
    legacy = {
        "": {"semantic": "location", "setting_id": "s1", "reference_image": str(pin)},
        "2": {"semantic": "character", "character_id": "c1"},
    }
    remapped = wc.remap_reference_bindings_for_slots(legacy, slots)
    assert remapped["198"]["reference_image"] == str(pin)
    assert remapped["213"]["character_id"] == "c1"
    assert "" not in remapped


def test_remap_reference_bindings_ordinal_roundtrip(tmp_path):
    root = Path(__file__).resolve().parent.parent / "workflows"
    vague = wc.discover_image_reference_slots(json.loads((root / "pixa-four-image_vague.json").read_text()))
    pin = tmp_path / "pinned.png"
    pin.write_bytes(b"x")
    saved = {
        "1": {"semantic": "location", "setting_id": "s1", "reference_image": str(pin)},
        "2": {"semantic": "character", "character_id": "c1"},
        "3": {"semantic": "character", "character_id": "c2"},
    }
    on_vague = wc.remap_reference_bindings_for_slots(saved, vague)
    assert on_vague["198"]["reference_image"] == str(pin)
    assert on_vague["213"]["character_id"] == "c1"
    assert on_vague["218"]["character_id"] == "c2"


def test_remap_does_not_steal_empty_key_for_other_slots():
    root = Path(__file__).resolve().parent.parent / "workflows"
    slots = wc.discover_image_reference_slots(json.loads((root / "pixa-four-image_vague.json").read_text()))
    legacy = {
        "": {"semantic": "location", "setting_id": "s1"},
        "2": {"semantic": "unset"},
        "3": {"semantic": "unset"},
    }
    remapped = wc.remap_reference_bindings_for_slots(legacy, slots)
    assert remapped["198"]["semantic"] == "location"
    assert remapped["213"]["semantic"] == "unset"
    assert remapped["218"]["semantic"] == "unset"


def test_explicit_unset_slot_not_auto_filled_from_sequence():
    slots = [
        wc.ImageReferenceSlot("", "1", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 1, "1"),
        wc.ImageReferenceSlot("", "2", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 2, "2"),
    ]
    kf = {"reference_bindings": {"1": {"semantic": "unset"}, "2": {"semantic": "character", "character_id": "c1"}}}
    seq = {"setting_id": "loc1", "style_id": "st1"}
    bindings = wc.normalize_reference_bindings(kf, slots, {}, seq)
    assert bindings["1"]["semantic"] == "unset"
    assert bindings["2"]["character_id"] == "c1"


def test_default_asset_reference_path_uses_gallery_when_canonical_missing(tmp_path):
    gallery = tmp_path / "proj" / "_characters" / "c1"
    gallery.mkdir(parents=True)
    img = gallery / "gallery.png"
    img.write_bytes(b"x")
    project = {
        "name": "proj",
        "comfy": {"output_root": str(tmp_path)},
        "characters": [{"id": "c1", "name": "Hero", "reference_image": str(tmp_path / "missing.png")}],
    }
    assert wc._default_asset_reference_path(project, "characters", "c1") == str(img)


def test_ensure_binding_default_reference_image_on_semantic_change(tmp_path):
    gallery = tmp_path / "proj" / "_locations" / "s1"
    gallery.mkdir(parents=True)
    img = gallery / "loc.png"
    img.write_bytes(b"x")
    project = {
        "name": "proj",
        "comfy": {"output_root": str(tmp_path)},
        "settings": [{"id": "s1", "name": "Beach"}],
    }
    binding = {"semantic": "location", "setting_id": "s1"}
    out = wc.ensure_binding_default_reference_image(
        binding, project, None, choice_changed=True
    )
    assert out["reference_image"] == str(img)

def test_apply_reference_injection_vague_workflow_mutes_empty_fourth_slot(tmp_path):
    """Duplicate THM-ImageReference titles must mute per-slot, not collapse to ref_1."""
    path = Path(__file__).resolve().parent.parent / "workflows" / "pixa-four-image_vague.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))
    img1 = tmp_path / "one.png"
    img2 = tmp_path / "two.png"
    img3 = tmp_path / "three.png"
    for p in (img1, img2, img3):
        p.write_bytes(b"x")
    project = {
        "characters": [
            {"id": "c1", "name": "A", "reference_image": str(img2)},
            {"id": "c2", "name": "B", "reference_image": str(img3)},
        ],
        "settings": [{"id": "s1", "name": "Loc", "reference_image": str(img1)}],
        "styles": [],
    }
    id_conf = {
        "characters": ["", ""],
        "reference_bindings": {
            "198": {"semantic": "location", "setting_id": "s1"},
            "213": {"semantic": "character", "character_id": "c1"},
            "218": {"semantic": "character", "character_id": "c2"},
            "224": {"semantic": "unset"},
        },
    }
    counts = wc.apply_reference_injection(
        workflow,
        project=project,
        id_conf=id_conf,
        pose_path="",
        sequence={},
        custom_mode=True,
    )
    assert counts["active_by_role"]["ref_224"] is False
    assert workflow["224"].get("mode") == wc.COMFY_NODE_MODE_NEVER
    assert counts["branch_changes"] > 0

def test_normalize_reference_bindings_migrates_legacy_pose_and_characters():
    slots = [
        wc.ImageReferenceSlot("", "10", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 1, "10"),
        wc.ImageReferenceSlot("", "11", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 2, "11"),
        wc.ImageReferenceSlot("", "12", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 3, "12"),
    ]
    kf = {"pose": "/poses/jump.png", "characters": ["c1", "c2"]}
    project = {"characters": [{"id": "c1", "name": "Hero"}, {"id": "c2", "name": "Sidekick"}]}
    bindings = wc.normalize_reference_bindings(kf, slots, project)
    assert bindings["10"]["semantic"] == "pose"
    assert bindings["11"] == {"semantic": "character", "character_id": "c1"}
    assert bindings["12"] == {"semantic": "character", "character_id": "c2"}


def test_normalize_reference_bindings_pose_chars_then_sequence():
    slots = [
        wc.ImageReferenceSlot("", "10", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 1, "10"),
        wc.ImageReferenceSlot("", "11", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 2, "11"),
        wc.ImageReferenceSlot("", "12", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 3, "12"),
        wc.ImageReferenceSlot("", "13", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 4, "13"),
    ]
    kf = {"pose": "/poses/jump.png", "characters": ["c1", "c2"]}
    project = {"characters": [{"id": "c1"}, {"id": "c2"}]}
    seq = {"setting_id": "loc1"}
    bindings = wc.normalize_reference_bindings(kf, slots, project, seq)
    assert bindings["10"]["semantic"] == "pose"
    assert bindings["11"]["character_id"] == "c1"
    assert bindings["12"]["character_id"] == "c2"
    assert bindings["13"] == {"semantic": "location", "source": "sequence"}


def test_normalize_reference_bindings_characters_then_sequence_no_pose():
    slots = [
        wc.ImageReferenceSlot("", "1", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 1, "1"),
        wc.ImageReferenceSlot("", "2", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 2, "2"),
        wc.ImageReferenceSlot("", "3", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 3, "3"),
        wc.ImageReferenceSlot("", "4", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 4, "4"),
    ]
    kf = {"characters": ["c1", "c2"]}
    project = {"characters": [{"id": "c1"}, {"id": "c2"}]}
    seq = {"setting_id": "loc1", "style_id": "st1"}
    bindings = wc.normalize_reference_bindings(kf, slots, project, seq)
    assert bindings["1"]["character_id"] == "c1"
    assert bindings["2"]["character_id"] == "c2"
    assert bindings["3"] == {"semantic": "location", "source": "sequence"}
    assert bindings["4"] == {"semantic": "style", "source": "sequence"}


def test_legacy_characters_backfill_when_pose_already_bound():
    slots = [
        wc.ImageReferenceSlot("", "10", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 1, "10"),
        wc.ImageReferenceSlot("", "11", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 2, "11"),
    ]
    kf = {"characters": ["c1", "c2"]}
    project = {"characters": [{"id": "c1"}, {"id": "c2"}]}
    bindings_in = {"10": {"semantic": "pose"}}
    kf["reference_bindings"] = bindings_in
    bindings = wc.normalize_reference_bindings(kf, slots, project)
    assert bindings["10"]["semantic"] == "pose"
    assert bindings["11"]["character_id"] == "c1"


def test_enforce_one_pose_binding_clears_earlier_pose():
    bindings = {
        "": {"semantic": "pose"},
        "2": {"semantic": "pose"},
    }
    wc.enforce_one_pose_binding(bindings)
    assert bindings[""]["semantic"] == "unset"
    assert bindings["2"]["semantic"] == "pose"


def test_inject_generic_image_reference_bindings(tmp_path):
    workflow = {
        "10": node("THM-ImageReference", "LoadImage", {"image": "baked.jpg"}),
        "11": node("THM-ImageReference", "LoadImage", {"image": "baked2.jpg"}),
    }
    pose = tmp_path / "pose.png"
    char = tmp_path / "hero.png"
    pose.write_bytes(b"x")
    char.write_bytes(b"x")
    project = {"characters": [{"id": "c1", "name": "Hero", "reference_image": str(char)}]}
    slots = wc.discover_image_reference_slots(workflow)
    kf = {
        "pose": str(pose),
        "reference_bindings": {
            "10": {"semantic": "pose"},
            "11": {"semantic": "character", "character_id": "c1"},
        },
    }
    paths = wc.resolve_reference_paths_from_bindings(kf, slots, project, None)
    wc.inject_reference_slot_paths(workflow, slots, paths)
    assert workflow["10"]["inputs"]["image"] == str(pose)
    assert workflow["11"]["inputs"]["image"] == str(char)


def test_clear_tagged_reference_nodes_includes_image_reference():
    workflow = {
        "1": node("THM-ImageReference", "LoadImage", {"image": "keep.jpg"}),
        "2": node("Untagged", "LoadImage", {"image": "stay.jpg"}),
    }
    assert wc.clear_tagged_reference_nodes(workflow) == 1
    assert workflow["1"]["inputs"]["image"] == ""
    assert workflow["2"]["inputs"]["image"] == "stay.jpg"


def test_discover_style_reference_slot():
    workflow = {
        "1": node("THM-StyleReference", "LoadImage", {"image": ""}),
    }
    slots = wc.discover_image_reference_slots(workflow)
    assert len(slots) == 0


def test_resolve_style_reference_path_sequence_and_pin(tmp_path):
    style_img = tmp_path / "noir.png"
    pin = tmp_path / "pinned.png"
    style_img.write_bytes(b"s")
    pin.write_bytes(b"p")
    project = {
        "styles": [{"id": "st1", "name": "Noir", "reference_image": str(style_img)}],
    }
    seq = {"style_id": "st1"}
    assert wc.resolve_style_reference_path(project, seq) == str(style_img)
    binding = {"semantic": "style", "reference_image": str(pin)}
    assert wc.resolve_style_reference_path(project, seq, binding) == str(pin)


def test_sequence_auto_assign_location_then_style():
    slots = [
        wc.ImageReferenceSlot("", "1", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 1, "1"),
        wc.ImageReferenceSlot("", "2", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 2, "2"),
    ]
    kf: dict = {}
    project: dict = {}
    seq = {"setting_id": "loc1", "style_id": "st1"}
    bindings = wc.normalize_reference_bindings(kf, slots, project, seq)
    assert bindings["1"] == {"semantic": "location", "source": "sequence"}
    assert bindings["2"] == {"semantic": "style", "source": "sequence"}


def test_resolve_location_from_binding_explicit_and_sequence(tmp_path):
    loc_a = tmp_path / "beach.png"
    loc_b = tmp_path / "city.png"
    loc_a.write_bytes(b"a")
    loc_b.write_bytes(b"b")
    project = {
        "settings": [
            {"id": "s1", "name": "Beach", "reference_image": str(loc_a)},
            {"id": "s2", "name": "City", "reference_image": str(loc_b)},
        ],
    }
    seq = {"setting_id": "s1"}
    binding_seq = {"semantic": "location", "source": "sequence"}
    assert wc.resolve_location_reference_path_from_binding(project, seq, binding_seq) == str(loc_a)
    binding_override = {"semantic": "location", "setting_id": "s2"}
    assert wc.resolve_location_reference_path_from_binding(project, seq, binding_override) == str(loc_b)


def test_pinned_reference_image_overrides_asset_default(tmp_path):
    asset = tmp_path / "default.png"
    pinned = tmp_path / "pin.png"
    asset.write_bytes(b"d")
    pinned.write_bytes(b"p")
    project = {"characters": [{"id": "c1", "name": "Hero", "reference_image": str(asset)}]}
    slots = [
        wc.ImageReferenceSlot("", "1", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 1, "1"),
    ]
    kf = {"reference_bindings": {"1": {"semantic": "character", "character_id": "c1", "reference_image": str(pinned)}}}
    paths = wc.resolve_reference_paths_from_bindings(kf, slots, project, None)
    assert paths["1"] == str(pinned)


def test_sequence_auto_fill_skips_explicit_unset(tmp_path):
    beach = tmp_path / "beach.png"
    beach.write_bytes(b"b")
    project = {
        "settings": [
            {"id": "s1", "name": "Beach", "reference_image": str(beach)},
        ],
    }
    slots = [
        wc.ImageReferenceSlot("", "10", "THM-ImageReference", wc.IMAGE_REFERENCE, "unset", 1, "10"),
    ]
    kf = {"reference_bindings": {"10": {"semantic": "unset"}}}
    seq = {"setting_id": "s1"}
    bindings = wc.normalize_reference_bindings(kf, slots, project, seq)
    assert bindings["10"]["semantic"] == "unset"


def test_inject_style_reference_tag(tmp_path):
    workflow = {"1": node("THM-ImageReference", "LoadImage", {"image": ""})}
    style_img = tmp_path / "style.png"
    style_img.write_bytes(b"x")
    project = {"styles": [{"id": "st1", "name": "Noir", "reference_image": str(style_img)}]}
    slots = wc.discover_image_reference_slots(workflow)
    kf = {"reference_bindings": {"1": {"semantic": "style", "style_id": "st1"}}}
    paths = wc.resolve_reference_paths_from_bindings(kf, slots, project, None)
    wc.inject_reference_slot_paths(workflow, slots, paths)
    assert workflow["1"]["inputs"]["image"] == str(style_img)


def test_prelude_label_for_style_ref_role():
    project = {"styles": [{"id": "st1", "name": "Noir"}]}
    id_conf = {"reference_bindings": {"3": {"semantic": "style", "style_id": "st1"}}}
    label = wc._prelude_label_for_ref_role(
        "ref_3", project=project, sequence=None, id_conf=id_conf
    )
    assert label == "style:Noir"


def test_resolve_sequence_setting_reference_path_pin_and_fallback(tmp_path):
    asset_default = tmp_path / "default.png"
    seq_pin = tmp_path / "seq_pin.png"
    asset_default.write_bytes(b"d")
    seq_pin.write_bytes(b"p")
    loc_dir = tmp_path / "test" / "_locations" / "s1"
    loc_dir.mkdir(parents=True)
    gallery_file = loc_dir / "gallery.png"
    gallery_file.write_bytes(b"g")
    project = {
        "name": "test",
        "comfy": {"output_root": str(tmp_path)},
        "settings": [{"id": "s1", "name": "Beach", "reference_image": str(asset_default)}],
    }
    seq = {"setting_id": "s1", "setting_reference_image": str(seq_pin)}
    assert wc.resolve_sequence_setting_reference_path(project, seq) == str(seq_pin)

    seq_no_pin = {"setting_id": "s1"}
    assert wc.resolve_sequence_setting_reference_path(project, seq_no_pin) == str(asset_default)

    project["settings"][0]["reference_image"] = ""
    assert wc.resolve_sequence_setting_reference_path(project, seq_no_pin) == str(gallery_file)


def test_binding_source_sequence_uses_sequence_pin(tmp_path):
    asset = tmp_path / "asset.png"
    seq_pin = tmp_path / "seq.png"
    asset.write_bytes(b"a")
    seq_pin.write_bytes(b"s")
    project = {"settings": [{"id": "s1", "name": "Beach", "reference_image": str(asset)}]}
    seq = {"setting_id": "s1", "setting_reference_image": str(seq_pin)}
    binding = {"semantic": "location", "source": "sequence"}
    path = wc.resolve_location_reference_path_from_binding(project, seq, binding)
    assert path == str(seq_pin)


def test_keyframe_binding_pin_overrides_sequence_pin(tmp_path):
    asset = tmp_path / "asset.png"
    seq_pin = tmp_path / "seq.png"
    kf_pin = tmp_path / "kf.png"
    asset.write_bytes(b"a")
    seq_pin.write_bytes(b"s")
    kf_pin.write_bytes(b"k")
    project = {"settings": [{"id": "s1", "reference_image": str(asset)}]}
    seq = {"setting_id": "s1", "setting_reference_image": str(seq_pin)}
    binding = {"semantic": "location", "source": "sequence", "reference_image": str(kf_pin)}
    path = wc.resolve_location_reference_path_from_binding(project, seq, binding)
    assert path == str(kf_pin)


def test_merge_binding_on_semantic_choice_preserves_pin_when_unchanged():
    old = {"semantic": "location", "source": "sequence", "reference_image": "/tmp/kf.png"}
    new = {"semantic": "location", "source": "sequence"}
    merged = wc.merge_binding_on_semantic_choice(old, new, "Restaurant", "Restaurant")
    assert merged["reference_image"] == "/tmp/kf.png"


def test_merge_binding_on_semantic_choice_clears_pin_on_different_choice():
    old = {"semantic": "location", "source": "sequence", "reference_image": "/tmp/kf.png"}
    new = {"semantic": "character", "character_id": "c1"}
    merged = wc.merge_binding_on_semantic_choice(old, new, "Restaurant", "Alice")
    assert "reference_image" not in merged


def test_merge_binding_on_semantic_choice_preserves_pin_when_prev_unset():
    """Gallery pin before first semantic .change event (prev is None)."""
    old = {"semantic": "location", "source": "sequence", "reference_image": "/tmp/kf.png"}
    new = {"semantic": "location", "source": "sequence"}
    merged = wc.merge_binding_on_semantic_choice(old, new, None, "Restaurant")
    assert merged["reference_image"] == "/tmp/kf.png"


def test_keyframe_pin_persists_after_semantic_refresh_simulation(tmp_path):
    """Regression: seq->kf navigation re-fires semantic .change with same value."""
    asset = tmp_path / "asset.png"
    seq_pin = tmp_path / "seq.png"
    kf_pin = tmp_path / "kf.png"
    asset.write_bytes(b"a")
    seq_pin.write_bytes(b"s")
    kf_pin.write_bytes(b"k")
    project = {"settings": [{"id": "s1", "name": "Restaurant", "reference_image": str(asset)}]}
    seq = {"setting_id": "s1", "setting_reference_image": str(seq_pin)}
    old_binding = {"semantic": "location", "source": "sequence", "reference_image": str(kf_pin)}
    new_binding = {"semantic": "location", "source": "sequence"}
    binding = wc.merge_binding_on_semantic_choice(
        old_binding, new_binding, "Restaurant", "Restaurant"
    )
    path = wc.resolve_location_reference_path_from_binding(project, seq, binding)
    assert path == str(kf_pin)


def test_ref_slot_semantic_choice_changed_navigation_refresh():
    from src.editor_helpers import _ref_slot_semantic_choice_changed

    assert not _ref_slot_semantic_choice_changed("Alice", "Alice", "Alice")
    assert not _ref_slot_semantic_choice_changed(None, "Restaurant", "Restaurant")
    assert _ref_slot_semantic_choice_changed(None, "Restaurant", "Alice")
    assert _ref_slot_semantic_choice_changed("Alice", "Bob", "Bob")


def test_ensure_binding_keeps_pin_when_prev_unset_and_display_matches(tmp_path):
    from src.editor_helpers import _ref_slot_semantic_choice_changed

    asset = tmp_path / "asset.png"
    kf_pin = tmp_path / "kf.png"
    asset.write_bytes(b"a")
    kf_pin.write_bytes(b"k")
    project = {
        "name": "proj",
        "comfy": {"output_root": str(tmp_path)},
        "settings": [{"id": "s1", "name": "Restaurant", "reference_image": str(asset)}],
    }
    old_binding = {"semantic": "location", "source": "sequence", "reference_image": str(kf_pin)}
    new_binding = {"semantic": "location", "source": "sequence"}
    merged = wc.merge_binding_on_semantic_choice(old_binding, new_binding, None, "Restaurant")
    choice_changed = _ref_slot_semantic_choice_changed(None, "Restaurant", "Restaurant")
    assert not choice_changed
    out = wc.ensure_binding_default_reference_image(
        merged, project, {"setting_id": "s1"}, choice_changed=choice_changed
    )
    assert out["reference_image"] == str(kf_pin)


def test_reference_slot_semantic_change_one_preserves_other_slots(tmp_path):
    from src.editor_helpers import _eh_reference_slot_semantic_change_one
    from src.workflow_capabilities import scan_workflow_file

    caps = scan_workflow_file("pixa-four-image_vague.json")
    assert caps.image_reference_slots
    slots = caps.image_reference_slots[:4]
    pin_paths = []
    for i in range(4):
        p = tmp_path / f"pin_{i}.png"
        p.write_bytes(bytes([i]))
        pin_paths.append(str(p))

    bindings = {}
    for slot, pin in zip(slots, pin_paths):
        bk = wc.binding_key_for_slot(slot)
        bindings[bk] = {
            "semantic": "character",
            "character_id": f"c{i}",
            "reference_image": pin,
        }

    project = {
        "name": "proj",
        "image_model_family": "custom",
        "characters": [
            {"id": f"c{i}", "name": f"Char{i}", "reference_image": pin_paths[i]}
            for i in range(4)
        ],
    }
    data = {
        "project": project,
        "sequences": {
            "s1": {
                "keyframes": {
                    "k1": {
                        "workflow_json": "pixa-four-image_vague.json",
                        "reference_bindings": copy.deepcopy(bindings),
                        "reference_slot_last_choice": {
                            wc.binding_key_for_slot(slots[i]): f"Char{i}" for i in range(4)
                        },
                    }
                },
                "keyframe_order": ["k1"],
            }
        },
    }

    result = _eh_reference_slot_semantic_change_one(
        data,
        "k1",
        "proj",
        "pixa-four-image_vague.json",
        0,
        "Char0",
    )
    kf = result["sequences"]["s1"]["keyframes"]["k1"]
    for i in range(4):
        bk = wc.binding_key_for_slot(slots[i])
        assert kf["reference_bindings"][bk]["reference_image"] == pin_paths[i]


def test_reference_slot_semantic_change_one_preserves_pin_without_last_choice(tmp_path):
    from src.editor_helpers import _eh_reference_slot_semantic_change_one
    from src.workflow_capabilities import scan_workflow_file

    asset = tmp_path / "asset.png"
    kf_pin = tmp_path / "kf.png"
    asset.write_bytes(b"a")
    kf_pin.write_bytes(b"k")
    caps = scan_workflow_file("pixa-four-image_vague.json")
    slot0 = caps.image_reference_slots[0]
    bk = wc.binding_key_for_slot(slot0)

    data = {
        "project": {
            "name": "proj",
            "image_model_family": "custom",
            "settings": [{"id": "s1", "name": "Restaurant", "reference_image": str(asset)}],
        },
        "sequences": {
            "s1": {
                "setting_id": "s1",
                "keyframes": {
                    "k1": {
                        "workflow_json": "pixa-four-image_vague.json",
                        "reference_bindings": {
                            bk: {
                                "semantic": "location",
                                "source": "sequence",
                                "reference_image": str(kf_pin),
                            }
                        },
                    }
                },
                "keyframe_order": ["k1"],
            }
        },
    }

    result = _eh_reference_slot_semantic_change_one(
        data,
        "k1",
        "proj",
        "pixa-four-image_vague.json",
        0,
        "Restaurant",
    )
    binding = result["sequences"]["s1"]["keyframes"]["k1"]["reference_bindings"][bk]
    assert binding["reference_image"] == str(kf_pin)


def test_seed_reference_slot_last_choices_from_bindings():
    from src.editor_helpers import _eh_seed_reference_slot_last_choices
    from src.workflow_capabilities import scan_workflow_file

    caps = scan_workflow_file("pixa-four-image_vague.json")
    slot0 = caps.image_reference_slots[0]
    bk = wc.binding_key_for_slot(slot0)
    data = {
        "project": {
            "name": "proj",
            "image_model_family": "custom",
            "settings": [{"id": "s1", "name": "Restaurant"}],
        },
        "sequences": {
            "s1": {
                "setting_id": "s1",
                "keyframes": {
                    "k1": {
                        "workflow_json": "pixa-four-image_vague.json",
                        "reference_bindings": {
                            bk: {"semantic": "location", "source": "sequence"},
                        },
                    }
                },
                "keyframe_order": ["k1"],
            }
        },
    }

    result = _eh_seed_reference_slot_last_choices(data, "k1", "pixa-four-image_vague.json")
    kf = result["sequences"]["s1"]["keyframes"]["k1"]
    assert kf["reference_slot_last_choice"][bk] == "Restaurant"


def test_resolve_sequence_style_reference_path(tmp_path):
    style_img = tmp_path / "noir.png"
    style_img.write_bytes(b"x")
    project = {"styles": [{"id": "st1", "name": "Noir", "reference_image": str(style_img)}]}
    seq = {"style_id": "st1"}
    assert wc.resolve_sequence_style_reference_path(project, seq) == str(style_img)
    assert wc.resolve_style_reference_path(project, seq) == str(style_img)


I2V_BASE = WORKFLOWS_DIR / "i2v_base.json"


def test_video_generator_writes_length_width_height():
    workflow = {
        "1": node("THM-VideoGenerator", "WanFunInpaintToVideo", {"width": 1, "height": 1, "length": 1}),
    }
    assert wc.set_video_generator(workflow, width=640, height=960, length=49) == 3
    assert workflow["1"]["inputs"]["width"] == 640
    assert workflow["1"]["inputs"]["height"] == 960
    assert workflow["1"]["inputs"]["length"] == 49


def test_legacy_wan_generator_fallback_writes_dimensions():
    workflow = {
        "1": node("WanFirstLastFrameToVideo", "WanFirstLastFrameToVideo", {"width": 1, "height": 1, "length": 1}),
    }
    assert wc.set_video_generator(workflow, width=320, height=180, length=25) == 3
    assert workflow["1"]["inputs"]["width"] == 320
    assert workflow["1"]["inputs"]["length"] == 25


def test_frame_rate_tag_and_thm_fps_alias():
    tagged = {"1": node("THM-FrameRate", "PrimitiveFloat", {"value": 24.0})}
    alias = {"2": node("THM-FPS", "PrimitiveFloat", {"value": 24.0})}
    assert wc.set_frame_rate(tagged, 16.0) == 1
    assert tagged["1"]["inputs"]["value"] == 16.0
    assert wc.set_frame_rate(alias, 30.0) == 1
    assert alias["2"]["inputs"]["value"] == 30.0


def test_fps_passthrough_project_field_writes_tagged_node():
    workflow = json.loads(I2V_BASE.read_text(encoding="utf-8"))
    fps_node = wc.find_control_nodes(workflow, wc.FRAME_RATE)[0]
    assert wc.set_frame_rate(workflow, 24.0) >= 1
    assert fps_node.node["inputs"]["value"] == 24.0
    frames = int(round(3.0 * 24.0)) + 1
    assert wc.set_frame_count(workflow, frames) >= 1
    gen = wc.find_control_nodes(workflow, wc.VIDEO_GENERATOR)[0]
    assert gen.node["inputs"]["length"] == frames


def test_discover_video_capabilities_on_i2v_base():
    workflow = json.loads(I2V_BASE.read_text(encoding="utf-8"))
    caps = wc.discover_video_capabilities(workflow)
    assert caps.lora_mode == "dual"
    assert caps.has_legacy_wan_generator
    assert caps.has_express_samplers
    assert not caps.has_thm_ksampler_passes
    assert not caps.has_thm_slowmo_primer


def _tagged_video_sampler_workflow(*, swap_chain_ids: bool = False) -> dict:
    """Three THM-KSampler passes + THM-SlowMoPrimer wired toward VAEDecode."""
    if swap_chain_ids:
        primer_id, pass_a, pass_b, pass_c = "30", "10", "20", "40"
    else:
        primer_id, pass_a, pass_b, pass_c = "10", "20", "30", "40"
    return {
        "1": node("Gen", "WanFirstLastFrameToVideo", {"latent": ["gen_latent", 0]}),
        "gen_latent": node("Latent", "EmptyLatentImage", {"width": 512, "height": 512}),
        primer_id: node(
            "THM-SlowMoPrimer",
            "KSamplerAdvanced",
            {
                "steps": 8,
                "start_at_step": 0,
                "end_at_step": 2,
                "latent_image": ["1", 2],
            },
        ),
        pass_a: node(
            "THM-KSampler",
            "KSamplerAdvanced",
            {"steps": 8, "start_at_step": 0, "end_at_step": 8, "latent_image": [primer_id, 0]},
        ),
        pass_b: node(
            "THM-KSampler",
            "KSamplerAdvanced",
            {"steps": 8, "start_at_step": 0, "end_at_step": 8, "latent_image": [pass_a, 0]},
        ),
        pass_c: node(
            "THM-KSampler",
            "KSamplerAdvanced",
            {"steps": 8, "start_at_step": 0, "end_at_step": 8, "latent_image": [pass_b, 0]},
        ),
        "99": node(
            "VAE Decode",
            "VAEDecode",
            {"samples": [pass_c, 0], "vae": ["vae", 0]},
        ),
        "vae": node("Load VAE", "VAELoader", {"vae_name": "wan.safetensors"}),
    }


def _sampler_range(workflow, title: str) -> tuple[int, int, int]:
    node_obj = node_by_title(workflow, title)
    return (
        int(node_obj["inputs"]["steps"]),
        int(node_obj["inputs"]["start_at_step"]),
        int(node_obj["inputs"]["end_at_step"]),
    )


def test_apply_video_sampler_passes_full_three_pass_with_primer():
    workflow = _tagged_video_sampler_workflow()
    assert wc.apply_video_sampler_passes(
        workflow, total_steps=14, express=False, primer_steps=2
    ) == 14
    assert _sampler_range(workflow, "THM-SlowMoPrimer") == (14, 0, 2)
    passes = wc.discover_thm_ksampler_passes(workflow)
    assert len(passes) == 3
    ranges = [
        (
            int(passes[i].node["inputs"]["start_at_step"]),
            int(passes[i].node["inputs"]["end_at_step"]),
        )
        for i in range(3)
    ]
    assert ranges == [(2, 6), (6, 10), (10, 14)]


def test_apply_video_sampler_passes_express_three_pass():
    workflow = _tagged_video_sampler_workflow()
    wc.apply_video_sampler_passes(workflow, total_steps=14, express=True, primer_steps=2)
    assert _sampler_range(workflow, "THM-SlowMoPrimer") == (7, 0, 2)
    passes = wc.discover_thm_ksampler_passes(workflow)
    ranges = [
        (
            int(p.node["inputs"]["start_at_step"]),
            int(p.node["inputs"]["end_at_step"]),
        )
        for p in passes
    ]
    # chain_budget=5, full_shares=[1,1,3] -> express [1, 0, 0]
    assert ranges == [(2, 3), (3, 3), (3, 3)]


def test_apply_video_sampler_passes_express_two_pass():
    workflow = _tagged_video_sampler_workflow()
    passes = wc.discover_thm_ksampler_passes(workflow)
    # drop last pass node from workflow, rewire decode to pass_b
    last = passes[-1].node_id
    second = passes[1].node_id
    del workflow[last]
    workflow["99"]["inputs"]["samples"] = [second, 0]
    wc.apply_video_sampler_passes(workflow, total_steps=14, express=True, primer_steps=2)
    chain_passes = wc.discover_thm_ksampler_passes(workflow)
    assert len(chain_passes) == 2
    first_range = (
        int(chain_passes[0].node["inputs"]["start_at_step"]),
        int(chain_passes[0].node["inputs"]["end_at_step"]),
    )
    second_range = (
        int(chain_passes[1].node["inputs"]["start_at_step"]),
        int(chain_passes[1].node["inputs"]["end_at_step"]),
    )
    assert first_range == (2, 7)
    assert second_range == (7, 7)


def test_discover_thm_ksampler_passes_follows_latent_chain_not_node_id():
    ordered = wc.discover_thm_ksampler_passes(_tagged_video_sampler_workflow())
    swapped = wc.discover_thm_ksampler_passes(_tagged_video_sampler_workflow(swap_chain_ids=True))
    assert [item.node_id for item in ordered] == ["20", "30", "40"]
    assert [item.node_id for item in swapped] == ["10", "20", "40"]


def test_i2v_base_legacy_express_samplers_without_thm_tags():
    workflow = json.loads(I2V_BASE.read_text(encoding="utf-8"))
    caps = wc.discover_video_capabilities(workflow)
    assert caps.has_express_samplers
    assert not caps.has_thm_ksampler_passes


def test_video_lora_mode_dual_vs_single():
    dual = {
        "1": node("HighNoiseUnet", "UNETLoader", {"unet_name": "h.safetensors"}),
        "2": node("LowNoiseUnet", "UNETLoader", {"unet_name": "l.safetensors"}),
    }
    single = {
        "1": node("THM-LoraAfterThisNode", "UNETLoader", {"unet_name": "m.safetensors"}),
        "2": {"class_type": "KSampler", "inputs": {"model": ["1", 0]}, "_meta": {"title": "Sampler"}},
    }
    assert wc.video_lora_mode(dual) == "dual"
    assert wc.video_lora_mode(single) == "single"


def test_video_lora_mode_dual_with_thm_tags_on_unet_loader():
    workflow = {
        "101": node("THM-Lora-High", "UNETLoader", {"unet_name": "high.safetensors"}),
        "102": node("THM-Lora-Low", "UNETLoader", {"unet_name": "low.safetensors"}),
    }
    assert wc.video_lora_mode(workflow) == "dual"


def test_inject_video_dual_loras_thm_unet_loader_markers():
    workflow = {
        "101": node("THM-Lora-High", "UNETLoader", {"unet_name": "high.safetensors"}),
        "102": node("THM-Lora-Low", "UNETLoader", {"unet_name": "low.safetensors"}),
        "93": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"shift": 8, "model": ["101", 0]},
            "_meta": {"title": "HighSampling"},
        },
        "94": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"shift": 8, "model": ["102", 0]},
            "_meta": {"title": "LowSampling"},
        },
        "96": {
            "class_type": "KSamplerAdvanced",
            "inputs": {"model": ["93", 0]},
            "_meta": {"title": "THM-KSampler"},
        },
        "95": {
            "class_type": "KSamplerAdvanced",
            "inputs": {"model": ["94", 0], "latent_image": ["96", 0]},
            "_meta": {"title": "THM-KSampler"},
        },
    }

    def fake_resolve(name: str):
        return (f"high_{name}", f"low_{name}")

    from scripts.lora_tags import LoraSpec

    wc.inject_video_dual_loras(
        workflow, [LoraSpec("mylora.safetensors", 0.75, 0.5)], resolve_pair=fake_resolve
    )
    high = [n for n, node in workflow.items() if wc.node_title(node).startswith("Injected_High_")]
    low = [n for n, node in workflow.items() if wc.node_title(node).startswith("Injected_Low_")]
    assert len(high) == 1
    assert len(low) == 1
    assert workflow["93"]["inputs"]["model"][0] == high[0]
    assert workflow["94"]["inputs"]["model"][0] == low[0]


def test_inject_loras_on_thm_lora_after_this_node_marker():
    from scripts.lora_tags import LoraSpec

    workflow = {
        "15": node("MainCheckpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "10": {
            "_meta": {"title": "THM-LoraAfterThisNode"},
            "class_type": "Power Lora Loader (rgthree)",
            "inputs": {"model": ["15", 0], "clip": ["15", 1]},
        },
        "20": {"class_type": "KSampler", "inputs": {"model": ["10", 0]}, "_meta": {"title": "Sampler"}},
    }
    wc.inject_loras(workflow, [LoraSpec("style.safetensors", 0.8)])
    injected = injected_lora_nodes(workflow)
    assert len(injected) == 1
    assert workflow["20"]["inputs"]["model"][0] == injected[0][0]


def test_inject_video_dual_loras_legacy_anchors():
    workflow = {
        "10": node("HighNoiseUnet", "UNETLoader", {"unet_name": "high.safetensors"}),
        "20": node("LowNoiseUnet", "UNETLoader", {"unet_name": "low.safetensors"}),
        "30": {"class_type": "KSampler", "inputs": {"model": ["10", 0]}, "_meta": {"title": "SamplerHigh"}},
        "40": {"class_type": "KSampler", "inputs": {"model": ["20", 0]}, "_meta": {"title": "SamplerLow"}},
    }

    def fake_resolve(name: str):
        return (f"high_{name}", f"low_{name}")

    from scripts.lora_tags import LoraSpec

    wc.inject_video_dual_loras(
        workflow, [LoraSpec("mylora.safetensors", 0.75, 0.5)], resolve_pair=fake_resolve
    )
    high = [n for n, node in workflow.items() if wc.node_title(node).startswith("Injected_High_")]
    low = [n for n, node in workflow.items() if wc.node_title(node).startswith("Injected_Low_")]
    assert len(high) == 1
    assert len(low) == 1
    assert workflow[high[0]]["inputs"]["strength_model"] == 0.75
    assert workflow[low[0]]["inputs"]["strength_model"] == 0.5


def test_resolve_project_fps_default_when_field_omitted():
    from scripts.run_video import _resolve_project_fps

    assert _resolve_project_fps({}, None) == 16.0
    assert _resolve_project_fps({"fps": 24}, None) == 24.0


def test_set_video_seeds_i2v_base_legacy_triple():
    workflow = json.loads(I2V_BASE.read_text(encoding="utf-8"))
    assert wc.set_video_seeds(
        workflow,
        4242,
        seed_target_title="IterKSampler",
        seed_exclude_title="WanFixedSeed",
    ) >= 2
    assert workflow["204"]["inputs"]["noise_seed"] == 4242
    assert workflow["86"]["inputs"]["noise_seed"] == 4242
    assert workflow["85"]["inputs"]["noise_seed"] == 0


def test_set_video_seeds_fun_inpaint_style_thm_ksampler_chain():
    workflow = {
        "95": {
            "class_type": "KSamplerAdvanced",
            "_meta": {"title": "THM-KSampler"},
            "inputs": {"add_noise": "disable", "noise_seed": 0},
        },
        "96": {
            "class_type": "KSamplerAdvanced",
            "_meta": {"title": "THM-KSampler"},
            "inputs": {"add_noise": "enable", "noise_seed": 999},
        },
    }
    assert wc.set_video_seeds(workflow, 777) == 1
    assert workflow["96"]["inputs"]["noise_seed"] == 777
    assert workflow["95"]["inputs"]["noise_seed"] == 0


def test_set_video_seeds_thm_seed_tagged_path():
    workflow = {
        "1": {
            "class_type": "KSamplerAdvanced",
            "_meta": {"title": "THM-Seed"},
            "inputs": {"add_noise": "enable", "noise_seed": 0},
        },
        "2": {
            "class_type": "KSamplerAdvanced",
            "_meta": {"title": "WanFixedSeed"},
            "inputs": {"add_noise": "disable", "noise_seed": 0},
        },
    }
    assert wc.set_video_seeds(workflow, 100, seed_exclude_title="WanFixedSeed") == 1
    assert workflow["1"]["inputs"]["noise_seed"] == 100
    assert workflow["2"]["inputs"]["noise_seed"] == 0


def test_pos_prompt_legacy_alias_for_video():
    workflow = {"1": node("PosPrompt", "CLIPTextEncode", {"text": ""})}
    assert wc.set_prompt(workflow, "video positive") == 1
    assert workflow["1"]["inputs"]["text"] == "video positive"


def _load_i2v_base_workflow() -> dict:
    return json.loads(I2V_BASE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "clip_type,start_path,end_path,expect_start_wired,expect_end_wired",
    [
        ("SE", "/start.png", "/end.png", True, True),
        ("SO", "/start.png", None, True, False),
        ("OE", None, "/end.png", False, True),
    ],
)
def test_configure_video_frames_open_closed_wiring(
    clip_type, start_path, end_path, expect_start_wired, expect_end_wired
):
    workflow = _load_i2v_base_workflow()
    wc.configure_video_frames(workflow, clip_type, start_path, end_path)
    gen = workflow["73"]["inputs"]

    if expect_start_wired:
        assert gen["start_image"] == ["70", 0]
        assert workflow["70"]["inputs"]["image"] == start_path
    else:
        assert "start_image" not in gen
        assert workflow["70"]["inputs"]["image"] == ""

    if expect_end_wired:
        assert gen["end_image"] == ["74", 0]
        assert workflow["74"]["inputs"]["image"] == end_path
    else:
        assert "end_image" not in gen
        assert workflow["74"]["inputs"]["image"] == ""


def test_apply_video_injection_uses_frame_clip_type_for_tagged_workflow():
    workflow = _load_i2v_base_workflow()
    ctx = wc.VideoInjectionContext(
        frame_clip_type="SO",
        start_frame_path="/only_start.png",
        end_frame_path="/ignored.png",
    )
    wc.apply_video_injection(workflow, ctx)
    gen = workflow["73"]["inputs"]
    assert "end_image" not in gen
    assert workflow["70"]["inputs"]["image"] == "/only_start.png"
    assert workflow["74"]["inputs"]["image"] == ""


def test_discover_video_frame_input_support_ltx_start_only():
    graph = json.loads((WORKFLOWS_DIR / "THM_video_ltx2_i2v.json").read_text(encoding="utf-8"))
    support = wc.discover_video_frame_input_support(graph)
    assert support.supports_start_frame is True
    assert support.supports_end_frame is False
    assert "THM-StartFrame" in support.start_mechanisms


def test_discover_video_frame_input_support_fun_inpaint_both():
    graph = json.loads((WORKFLOWS_DIR / "THM_video_wan2_2_14B_fun_inpaint.json").read_text(encoding="utf-8"))
    support = wc.discover_video_frame_input_support(graph)
    assert support.supports_start_frame is True
    assert support.supports_end_frame is True


def test_sanitize_prompt_for_ltx_strips_sd_emphasis():
    raw = "(((nsfw))), (((nude))), plain text"
    assert wc.sanitize_prompt_for_ltx(raw) == "nsfw, nude, plain text"


def test_discover_workflow_baked_sigma_schedules_ltx():
    graph = json.loads((WORKFLOWS_DIR / "THM_video_ltx2_i2v.json").read_text(encoding="utf-8"))
    baked = wc.discover_workflow_baked_sigma_schedules(graph)
    assert any(b.get("class_type") == "ManualSigmas" for b in baked)

