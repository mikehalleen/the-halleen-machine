import json
from pathlib import Path

import pytest

from scripts import workflow_controls as wc
from helpers import IMAGE_MODEL_FAMILY_CUSTOM, _ensure_project

from src.editor_helpers import (
    KF_REF_SLOT_UI_COUNT,
    _eh_refresh_reference_slot_ui,
    _eh_seed_reference_slot_last_choices,
)
from src.workflow_capabilities import (
    WorkflowCapabilities,
    compose_keyframe_reference_prelude_text,
    compose_reference_present_hint,
    compose_reference_present_slots,
    generations_seed_visible,
    keyframe_editor_visibility,
    project_negative_visibility,
    scan_workflow_capabilities,
    scan_workflow_file,
    scan_video_workflow_file,
    scan_video_workflow_capabilities,
    capabilities_summary_line,
    format_capabilities_markdown,
    format_video_capabilities_markdown,
    video_capabilities_summary_line,
    video_generation_defaults_visibility,
    workflow_supports_image_references,
)


def node(title, class_type, inputs):
    return {"_meta": {"title": title}, "class_type": class_type, "inputs": dict(inputs)}


WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"
VAGUE_FOUR = "pixa-four-image_vague.json"


def test_scan_finds_legacy_main_prompt():
    workflow = {
        "1": node("MainCheckpoint", "CheckpointLoaderSimple", {"ckpt_name": "base.safetensors"}),
        "2": node("MainPrompt", "CLIPTextEncode", {"text": ""}),
        "3": node("MainLora", "Power Lora Loader (rgthree)", {"model": ["1", 0], "clip": ["1", 1]}),
    }
    caps = scan_workflow_capabilities(workflow)
    assert caps.has["prompt"]
    assert caps.has["checkpoint"]
    assert caps.has["lora"]
    assert caps.controls["prompt"][0]["source"] == "legacy"


def test_scan_workflow_controls_matches_find_control_nodes():
    workflow = {
        "1": node("MainPrompt", "CLIPTextEncode", {"text": ""}),
    }
    raw = wc.scan_workflow_controls(workflow)
    assert "prompt" in raw
    assert raw["prompt"][0]["title"] == "MainPrompt"


@pytest.mark.parametrize(
    "workflow_name,expect_keys",
    [
        ("pose_1CHAR.json", {"prompt", "lora", "checkpoint"}),
        ("pose_factory.json", {"prompt", "lora"}),
        ("custom_z_test-lora.json", {"prompt", "lora", "image_size"}),
    ],
)
def test_bundled_workflows_scan(workflow_name, expect_keys):
    caps = scan_workflow_file(workflow_name)
    assert caps.error is None, caps.error
    for key in expect_keys:
        assert caps.has.get(key), f"{workflow_name} missing {key}: {capabilities_summary_line(caps)}"


def test_pose_1char_has_pose_control_flag():
    caps = scan_workflow_file("pose_1CHAR.json")
    assert caps.has_pose_control


def test_custom_z_lora_no_clip_support():
    caps = scan_workflow_file("custom_z_test-lora.json")
    assert caps.has["lora"]
    assert caps.lora_clip_support is False


def test_missing_workflow_returns_error():
    caps = scan_workflow_file("")
    assert caps.error


def test_klein_driven_image_size_and_generation():
    caps = scan_workflow_file("klein.json")
    assert caps.error is None, caps.error
    assert caps.image_size_control is not None
    assert caps.image_size_control.status == "full"
    assert caps.has_confirmed_generation_field("seed")
    assert caps.has_confirmed_generation_field("steps")
    assert caps.has_confirmed_generation_field("cfg")
    assert caps.has_confirmed_generation_field("sampler")
    assert "scheduler" in (caps.generation_settings.not_controlled_fields() if caps.generation_settings else [])


def test_capabilities_markdown_excludes_confirmed_from_not_in_workflow():
    caps = scan_workflow_file("klein.json")
    md = format_capabilities_markdown(caps)
    assert "Image size (THM drives):" in md
    assert "Generation settings (project controls):" in md
    assert "Confirmed:" in md
    if "**Not in workflow:**" in md:
        absent_section = md.split("**Not in workflow:**", 1)[1]
        assert "seed" not in absent_section
        assert "steps" not in absent_section


def test_pose_2char_scan_lists_all_role_controls():
    caps = scan_workflow_file("pose_2CHAR.json")
    assert caps.error is None, caps.error
    prompt_titles = {n["title"] for n in caps.controls.get("prompt", [])}
    neg_titles = {n["title"] for n in caps.controls.get("negative_prompt", [])}
    lora_titles = {n["title"] for n in caps.controls.get("lora", [])}
    assert prompt_titles >= {"LeftPrompt", "RightPrompt", "HealPosPrompt"}
    assert neg_titles >= {"LeftNegPrompt", "RightNegPrompt", "HealNegPrompt"}
    assert lora_titles >= {"LeftLora", "RightLora"}
    assert caps.two_char_pipeline is not None
    assert caps.two_char_pipeline.active
    assert caps.two_char_pipeline.has_heal_pass


def test_pose_2char_capabilities_markdown_has_2char_section():
    caps = scan_workflow_file("pose_2CHAR.json")
    md = format_capabilities_markdown(caps)
    assert "**2-character pass (project drives):**" in md
    assert "`LeftLora`" in md
    assert "`RightLora`" in md
    assert "`HealPosPrompt`" in md
    assert "`HealNegPrompt`" in md
    summary = capabilities_summary_line(caps)
    assert "2char=" in summary
    assert "heal=yes" in summary
    assert "2CHAR pipeline" in md


def test_image_z_image_turbo_not_two_char_pipeline():
    caps = scan_workflow_file("image_z_image_turbo.json")
    assert caps.error is None, caps.error
    assert caps.has.get("prompt")
    assert "LeftPrompt" in {n["title"] for n in caps.controls.get("prompt", [])}
    assert caps.two_char_pipeline is not None
    assert not caps.two_char_pipeline.active
    md = format_capabilities_markdown(caps)
    assert "**2-character pass (project drives):**" not in md
    assert "2CHAR pipeline" not in md


def test_generations_seed_visible_requires_feature_and_confirmed_seed():
    caps = scan_workflow_file("klein.json")
    assert generations_seed_visible(caps, {"show_generation_info": True})
    assert not generations_seed_visible(caps, {"show_generation_info": False})
    assert not generations_seed_visible(caps, {})

    no_seed = scan_workflow_capabilities(
        {"1": node("MainPrompt", "CLIPTextEncode", {"text": ""})}
    )
    assert not generations_seed_visible(no_seed, {"show_generation_info": True})


def test_generations_video_seed_visible_for_i2v_workflows():
    features = {"show_generation_info": True}
    for wf in ("i2v_base.json", "THM_video_wan2_2_14B_fun_inpaint.json"):
        scan = scan_video_workflow_file(wf)
        assert scan.has_video_seed
        assert not generations_seed_visible(scan_workflow_file(wf), features)
        assert generations_seed_visible(None, features, video=True, video_scan=scan)


def test_format_video_capabilities_i2v_base():
    scan = scan_video_workflow_file("i2v_base.json")
    assert scan.error is None
    assert scan.video is not None
    assert scan.video.lora_mode == "dual"
    assert scan.video.has_express_samplers
    assert scan.video.has_legacy_wan_generator
    assert scan.project_controls_video_steps is True
    assert not scan.workflow_baked_samplers
    assert video_generation_defaults_visibility(scan).show_video_steps is True
    assert video_generation_defaults_visibility(scan).show_video_fps is False

    md = format_video_capabilities_markdown(scan)
    assert "`dual`" in md
    assert "Legacy triple" in md
    assert "video_steps_default" in md
    assert "Workflow-baked" not in md
    assert "WanFirstLastFrameToVideo" in md or "Legacy Wan" in md
    assert "ReferenceLatent" not in md
    assert "2CHAR" not in md
    assert "2-character" not in md
    assert "**Not in workflow:**" not in md
    assert "**Reference wiring" not in md

    summary = video_capabilities_summary_line(scan)
    assert "lora_mode=dual" in summary
    assert "express_samplers=found" in summary
    assert "legacy_wan_generator=found" in summary


def test_format_video_capabilities_fun_inpaint():
    scan = scan_video_workflow_file("THM_video_wan2_2_14B_fun_inpaint.json")
    assert scan.error is None
    assert scan.video is not None
    assert scan.video.lora_mode == "dual"
    assert scan.thm_ksampler_count >= 2
    assert scan.project_controls_video_steps is True
    assert video_generation_defaults_visibility(scan).show_video_steps is True
    assert video_generation_defaults_visibility(scan).show_video_fps is True

    md = format_video_capabilities_markdown(scan)
    assert "StartFrame" in md
    assert "EndFrame" in md
    assert "FrameRate" in md
    assert "THM-KSampler" in md
    assert "video_steps_default" in md
    assert "Workflow-baked" not in md
    assert "`dual`" in md
    assert "not project CFG" in md
    assert "**Generation settings" not in md
    assert "ReferenceLatent" not in md
    assert "**Not in workflow:**" not in md


def test_format_video_capabilities_5b_flf2v_workflow_baked_steps():
    workflow = {
        "3": node("KSampler", "KSampler", {"steps": 25, "seed": 1}),
        "6": node("THM-Prompt", "CLIPTextEncode", {"text": ""}),
        "79": node("THM-SaveVideo", "VHS_VideoCombine", {"filename_prefix": "out"}),
    }
    scan = scan_video_workflow_capabilities(workflow, workflow_path="tier3_baked.json")
    assert scan.project_controls_video_steps is False
    assert len(scan.workflow_baked_samplers) == 1
    assert scan.workflow_baked_samplers[0]["steps"] == 25
    vis = video_generation_defaults_visibility(scan)
    assert vis.show_video_steps is False
    assert vis.show_video_fps is False

    md = format_video_capabilities_markdown(scan)
    assert "Workflow-baked" in md
    assert "steps=25" in md
    assert "project.inbetween_generation.video_steps_default" not in md
    assert "not controlled by project (no `THM-FrameRate`" in md or "workflow-baked (no" in md


def test_format_video_capabilities_ltx2_thm_steps():
    scan = scan_video_workflow_file("THM_video_ltx2_i2v.json")
    assert scan.error is None
    assert scan.video is not None
    assert scan.video.has_thm_steps is True
    assert scan.project_controls_video_steps is True
    assert scan.supports_start_frame is True
    assert scan.supports_end_frame is False
    assert video_generation_defaults_visibility(scan).show_video_steps is True

    md = format_video_capabilities_markdown(scan)
    assert "THM-Steps" in md
    assert "supported" in md
    assert "not supported by this workflow" in md
    assert "ManualSigmas" in md
    assert "video_steps_default" in md


def test_video_frame_support_fun_inpaint():
    scan = scan_video_workflow_file("THM_video_wan2_2_14B_fun_inpaint.json")
    assert scan.error is None
    assert scan.supports_start_frame is True
    assert scan.supports_end_frame is True


def test_video_frame_support_5b_start_only():
    scan = scan_video_workflow_file("THM_video_wan2_2_5B_ti2v_FLF2V.json")
    assert scan.error is None
    assert scan.supports_start_frame is True
    assert scan.supports_end_frame is False


def test_video_frame_support_i2v_base_legacy():
    scan = scan_video_workflow_file("i2v_base.json")
    assert scan.error is None
    assert scan.supports_start_frame is True
    assert scan.supports_end_frame is True


def test_video_generation_defaults_fps_hidden_without_thm_fps_tag():
    workflow = {
        "3": node("THM-KSampler", "KSampler", {"steps": 20}),
        "6": node("THM-Prompt", "CLIPTextEncode", {"text": ""}),
        "73": node("THM-VideoGenerator", "WanFunInpaintToVideo", {"length": 33}),
        "79": node("THM-SaveVideo", "VHS_VideoCombine", {"filename_prefix": "out"}),
    }
    scan = scan_video_workflow_capabilities(workflow)
    vis = video_generation_defaults_visibility(scan)
    assert vis.show_video_steps is True
    assert vis.show_video_fps is False

    md = format_video_capabilities_markdown(scan)
    assert "not injected (no `THM-FrameRate`" in md or "not controlled by project (no `THM-FrameRate`" in md
    assert "→ `THM-FrameRate`" not in md


def test_scan_video_workflow_file_missing():
    assert scan_video_workflow_file("").error == "no workflow selected"
    assert scan_video_workflow_file(None).error == "no workflow selected"
    missing = scan_video_workflow_file("definitely_missing_workflow_xyz.json")
    assert missing.error


@pytest.mark.parametrize(
    "workflow_name,family_kwargs,expected",
    [
        (
            "klein.json",
            {},
            {
                "show_pose_group": False,
                "show_pose_cn_controls": False,
                "show_prompt": True,
                "show_inject_lora": False,
                "show_neg_left": False,
                "show_char_right": False,
                "show_neg_right": False,
                "show_neg_heal": False,
            },
        ),
        (
            "klein_multi_image.json",
            {"custom_image_family": True},
            {
                "show_pose_group": False,
                "show_pose_cn_controls": False,
                "show_prompt": True,
                "show_neg_left": True,
                "show_char_right": False,
                "show_reference_slots_group": False,
            },
        ),
        (
            "pixa-three-image.json",
            {},
            {
                "show_prompt": True,
                "show_char_right": False,
                "show_neg_right": False,
                "show_neg_heal": False,
            },
        ),
        (
            VAGUE_FOUR,
            {"custom_image_family": True},
            {
                "show_pose_group": False,
                "show_reference_slots_group": True,
                "show_char_left": False,
                "show_char_right": False,
            },
        ),
        (
            "pose_1CHAR.json",
            {"default_image_family": True},
            {
                "show_pose_group": True,
                "show_pose_cn_controls": True,
                "show_neg_left": True,
                "show_char_right": False,
                "show_neg_right": False,
                "show_neg_heal": False,
            },
        ),
        (
            "pose_2CHAR.json",
            {"default_image_family": True},
            {
                "show_pose_group": True,
                "show_char_right": True,
                "show_neg_right": True,
                "show_neg_heal": True,
                "show_neg_left": True,
            },
        ),
    ],
)
def test_keyframe_editor_visibility_matrix(workflow_name, family_kwargs, expected):
    caps = scan_workflow_file(workflow_name)
    assert caps.error is None, caps.error
    vis = keyframe_editor_visibility(caps, **family_kwargs)
    for key, value in expected.items():
        assert getattr(vis, key) == value, f"{workflow_name} {key}: got {getattr(vis, key)!r}, want {value!r}"


def test_has_secondary_character_reference_multiple_image_slots():
    workflow = {
        "1": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "2": node("THM-ImageReference", "LoadImage", {"image": ""}),
    }
    caps = scan_workflow_capabilities(workflow)
    assert caps.has_secondary_character_reference()
    assert not (caps.two_char_pipeline and caps.two_char_pipeline.active)


def test_legacy_pixa_three_image_has_no_image_reference_slots():
    caps = scan_workflow_file("pixa-three-image.json")
    assert caps.error is None, caps.error
    assert not caps.has_secondary_character_reference()
    assert not caps.image_reference_slots


def test_three_image_reference_slots_show_secondary_char():
    workflow = {
        "1": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "2": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "3": node("THM-ImageReference", "LoadImage", {"image": ""}),
    }
    caps = scan_workflow_capabilities(workflow)
    assert caps.has_secondary_character_reference()
    assert len(caps.image_reference_slots) == 3


def test_keyframe_editor_default_family_shows_pose_on_pose_open():
    caps = scan_workflow_file("pose_OPEN.json")
    assert caps.error is None, caps.error
    assert not caps.has.get("pose_reference")
    vis = keyframe_editor_visibility(caps, default_image_family=True)
    assert vis.show_pose_group
    assert vis.show_pose_library


@pytest.mark.parametrize(
    "workflow_name,custom_family,expect_kf,expect_heal",
    [
        ("pose_OPEN.json", False, True, False),
        ("pose_2CHAR.json", False, True, True),
        ("klein_multi_image.json", True, True, False),
        ("klein.json", True, False, False),
    ],
)
def test_project_negative_visibility(workflow_name, custom_family, expect_kf, expect_heal):
    caps = scan_workflow_file(workflow_name)
    assert caps.error is None, caps.error
    vis = project_negative_visibility(caps, custom_family=custom_family)
    assert vis.show_keyframes_all == expect_kf
    assert vis.show_heal_all == expect_heal


def test_project_negative_custom_always_hides_heal():
    caps = scan_workflow_file("pose_2CHAR.json")
    vis = project_negative_visibility(caps, custom_family=True)
    assert vis.show_heal_all is False


def test_project_negative_no_workflow_custom_hides_keyframe():
    vis = project_negative_visibility(
        scan_workflow_file(""),
        custom_family=True,
    )
    assert not vis.show_keyframes_all
    assert not vis.show_heal_all


def test_compose_reference_present_hint_active_slots(tmp_path):
    pose = tmp_path / "pose.png"
    char_ref = tmp_path / "hero.png"
    loc_ref = tmp_path / "desert.png"
    pose.write_bytes(b"x")
    char_ref.write_bytes(b"x")
    loc_ref.write_bytes(b"x")

    caps = scan_workflow_file(VAGUE_FOUR)
    slots = sorted(caps.image_reference_slots, key=lambda s: s.image_index or 999)
    project = {
        "image_model_family": "custom",
        "settings": [{"id": "s1", "name": "Desert", "reference_image": str(loc_ref)}],
        "characters": [{"id": "c1", "name": "Trex", "reference_image": str(char_ref)}],
    }
    seq = {"setting_id": "s1"}
    kf = {
        "pose": str(pose),
        "reference_bindings": {
            wc.binding_key_for_slot(slots[0]): {"semantic": "location", "setting_id": "s1", "source": "sequence"},
            wc.binding_key_for_slot(slots[1]): {"semantic": "pose"},
            wc.binding_key_for_slot(slots[2]): {"semantic": "character", "character_id": "c1"},
        },
    }

    hint = compose_reference_present_hint(project, seq, kf, VAGUE_FOUR)
    assert "image1" in hint
    assert "image2" in hint
    assert "image3" in hint
    assert "Boss" not in hint
    assert "Desert" not in hint


def test_compose_reference_present_hint_skips_pose_only_workflow(tmp_path):
    pose = tmp_path / "pose.png"
    pose.write_bytes(b"x")
    project = {"characters": [{"id": "c1", "name": "Hero"}]}
    kf = {"pose": str(pose), "characters": ["c1"]}
    hint = compose_reference_present_hint(project, {}, kf, "pose_1CHAR.json")
    assert hint == ""
    assert not workflow_supports_image_references(scan_workflow_file("pose_1CHAR.json"))


def test_workflow_supports_image_references_vague():
    assert workflow_supports_image_references(scan_workflow_file(VAGUE_FOUR))


def test_legacy_klein_multi_image_does_not_support_image_references():
    assert not workflow_supports_image_references(scan_workflow_file("klein_multi_image.json"))


def test_compose_reference_present_slots_paths_and_captions(tmp_path):
    char_ref = tmp_path / "hero.png"
    loc_ref = tmp_path / "office.png"
    char_ref.write_bytes(b"x")
    loc_ref.write_bytes(b"x")
    caps = scan_workflow_file(VAGUE_FOUR)
    slots = sorted(caps.image_reference_slots, key=lambda s: s.image_index or 999)
    project = {
        "image_model_family": "custom",
        "settings": [{"id": "s1", "name": "The Office", "reference_image": str(loc_ref)}],
        "characters": [{"id": "c1", "name": "Boss", "reference_image": str(char_ref)}],
    }
    kf = {
        "reference_bindings": {
            wc.binding_key_for_slot(slots[0]): {"semantic": "location", "setting_id": "s1", "source": "sequence"},
            wc.binding_key_for_slot(slots[1]): {"semantic": "character", "character_id": "c1"},
        },
    }
    slots_out = compose_reference_present_slots(project, {"setting_id": "s1"}, kf, VAGUE_FOUR)
    assert len(slots_out) == 2
    assert slots_out[0].caption == "image1"
    assert slots_out[0].path == str(loc_ref)
    assert slots_out[1].caption == "image2"
    assert slots_out[1].path == str(char_ref)


def test_compose_reference_present_hint_renumbers_when_location_omitted(tmp_path):
    char_ref = tmp_path / "hero.png"
    char_ref.write_bytes(b"x")
    caps = scan_workflow_file(VAGUE_FOUR)
    slots = sorted(caps.image_reference_slots, key=lambda s: s.image_index or 999)
    project = {
        "image_model_family": "custom",
        "settings": [{"id": "s1", "name": "The Office", "reference_image": ""}],
        "characters": [
            {"id": "c1", "name": "Boss", "reference_image": str(char_ref)},
            {"id": "c2", "name": "Intern", "reference_image": str(char_ref)},
        ],
    }
    kf = {
        "reference_bindings": {
            wc.binding_key_for_slot(slots[0]): {"semantic": "character", "character_id": "c1"},
            wc.binding_key_for_slot(slots[1]): {"semantic": "character", "character_id": "c2"},
        },
    }
    hint = compose_reference_present_hint(project, {}, kf, VAGUE_FOUR)
    assert hint == "image1, image2"


def test_keyframe_editor_visibility_custom_shows_slots_hides_char_dropdowns():
    caps = scan_workflow_file(VAGUE_FOUR)
    vis = keyframe_editor_visibility(caps, custom_image_family=True)
    assert vis.show_reference_slots_group
    assert not vis.show_char_left
    assert not vis.show_char_right


def test_keyframe_editor_visibility_default_hides_slots_shows_char():
    caps = scan_workflow_file("pixa-four-image.json")
    vis = keyframe_editor_visibility(caps, default_image_family=True)
    assert not vis.show_reference_slots_group
    assert vis.show_char_left


def test_discover_image_reference_slots_on_vague_four():
    caps = scan_workflow_file(VAGUE_FOUR)
    assert len(caps.image_reference_slots) == 4
    assert caps.image_reference_slots[0].image_index == 1


def test_legacy_pixa_four_image_discovers_no_image_reference_slots():
    caps = scan_workflow_file("pixa-four-image.json")
    assert len(caps.image_reference_slots) == 0


def test_compose_reference_present_slots_custom_bindings(tmp_path):
    pose = tmp_path / "pose.png"
    char = tmp_path / "boss.png"
    pose.write_bytes(b"x")
    char.write_bytes(b"x")
    project = {
        "image_model_family": "custom",
        "characters": [{"id": "c1", "name": "Boss", "reference_image": str(char)}],
        "settings": [{"id": "s1", "name": "Office", "reference_image": ""}],
    }
    workflow = {
        "10": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "11": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "20": node("Zero", "ConditioningZeroOut", {"conditioning": ["99", 0]}),
        "21": node("Enc1", "VAEEncode", {"pixels": ["10", 0], "vae": ["99", 0]}),
        "22": node("Enc2", "VAEEncode", {"pixels": ["11", 0], "vae": ["99", 0]}),
        "30": node("Ref1", "ReferenceLatent", {"conditioning": ["20", 0], "latent": ["21", 0]}),
        "31": node("Ref2", "ReferenceLatent", {"conditioning": ["30", 0], "latent": ["22", 0]}),
        "99": node("VAE", "VAELoader", {"vae_name": "x"}),
    }
    kf = {
        "pose": str(pose),
        "reference_bindings": {
            "10": {"semantic": "pose"},
            "11": {"semantic": "character", "character_id": "c1"},
        },
    }
    caps = scan_workflow_capabilities(workflow)
    slots = compose_reference_present_slots(project, {}, kf, caps=caps)
    assert len(slots) == 2
    assert slots[0].caption == "image1"
    assert slots[1].caption == "image2"


def test_keyframe_editor_visibility_custom_hides_pose_when_scan_missing():
    vis = keyframe_editor_visibility(None, custom_image_family=True)
    assert not vis.show_pose_group
    assert not vis.show_pose_library

    vis_err = keyframe_editor_visibility(
        WorkflowCapabilities(error="missing workflow"),
        custom_image_family=True,
    )
    assert not vis_err.show_pose_group


def test_refresh_reference_slot_ui_pose_update_visible_false():
    caps = scan_workflow_file(VAGUE_FOUR)
    assert caps.error is None and caps.image_reference_slots

    data = _ensure_project(
        {
            "project": {"image_model_family": IMAGE_MODEL_FAMILY_CUSTOM},
            "sequences": {
                "s1": {
                    "keyframes": {"k1": {"workflow_json": VAGUE_FOUR, "reference_bindings": {}}},
                    "keyframe_order": ["k1"],
                }
            },
        }
    )
    result = _eh_refresh_reference_slot_ui(VAGUE_FOUR, data, "k1")
    expected_len = 1 + KF_REF_SLOT_UI_COUNT * 4 + 1
    assert len(result) == expected_len
    pose_update = result[-1]
    assert isinstance(pose_update, dict)
    assert pose_update.get("visible") is False
    assert not isinstance(pose_update.get("visible"), dict)


def test_refresh_reference_slot_ui_highlights_pinned_paths(tmp_path):
    caps = scan_workflow_file(VAGUE_FOUR)
    assert caps.image_reference_slots
    slot0 = caps.image_reference_slots[0]
    bk = wc.binding_key_for_slot(slot0)
    gallery_dir = tmp_path / "proj" / "_characters" / "c1"
    gallery_dir.mkdir(parents=True)
    default = gallery_dir / "gallery.png"
    pin = gallery_dir / "gallery_2.png"
    default.write_bytes(b"y")
    pin.write_bytes(b"x")

    data = _ensure_project(
        {
            "project": {
                "name": "proj",
                "image_model_family": IMAGE_MODEL_FAMILY_CUSTOM,
                "comfy": {"output_root": str(tmp_path)},
                "characters": [
                    {"id": "c1", "name": "Alice", "reference_image": str(default)},
                ],
            },
            "sequences": {
                "s1": {
                    "keyframes": {
                        "k1": {
                            "workflow_json": VAGUE_FOUR,
                            "reference_bindings": {
                                bk: {
                                    "semantic": "character",
                                    "character_id": "c1",
                                    "reference_image": str(pin),
                                }
                            },
                            "reference_slot_last_choice": {bk: "Alice"},
                        }
                    },
                    "keyframe_order": ["k1"],
                }
            },
        }
    )
    result = _eh_refresh_reference_slot_ui(VAGUE_FOUR, data, "k1")
    # outputs: group, 4 rows, 4 dropdowns, 4 empty, 4 galleries, pose
    gallery_updates = result[1 + KF_REF_SLOT_UI_COUNT * 3 : 1 + KF_REF_SLOT_UI_COUNT * 4]
    first_gallery = gallery_updates[0]
    assert isinstance(first_gallery, dict)
    assert first_gallery.get("visible") is True
    assert first_gallery.get("selected_index") is not None
    gallery_paths = [item[0] for item in (first_gallery.get("value") or [])]
    assert str(pin) in gallery_paths
    assert gallery_paths[first_gallery["selected_index"]] == str(pin)


def test_keyframe_editor_pose_group_only_default_family():
    workflow = {
        "10": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "11": node("THM-ImageReference", "LoadImage", {"image": ""}),
    }
    caps = scan_workflow_capabilities(workflow)
    vis = keyframe_editor_visibility(caps, custom_image_family=True)
    assert vis.show_reference_slots_group
    assert not vis.show_pose_group

    workflow_pose = {
        "10": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "11": node("THM-ImageReference", "LoadImage", {"image": ""}),
    }
    caps_pose = scan_workflow_capabilities(workflow_pose)
    vis_pose = keyframe_editor_visibility(caps_pose, custom_image_family=True)
    assert not vis_pose.show_pose_group
    vis_default = keyframe_editor_visibility(caps_pose, default_image_family=True)
    assert vis_default.show_pose_group


def test_compose_reference_present_slots_style_binding(tmp_path):
    style_img = tmp_path / "noir.png"
    style_img.write_bytes(b"x")
    project = {
        "image_model_family": "custom",
        "styles": [{"id": "st1", "name": "Noir", "reference_image": str(style_img)}],
    }
    workflow = {
        "10": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "20": node("Zero", "ConditioningZeroOut", {"conditioning": ["99", 0]}),
        "21": node("Enc1", "VAEEncode", {"pixels": ["10", 0], "vae": ["99", 0]}),
        "30": node("Ref1", "ReferenceLatent", {"conditioning": ["20", 0], "latent": ["21", 0]}),
        "99": node("VAE", "VAELoader", {"vae_name": "x"}),
    }
    caps = scan_workflow_capabilities(workflow)
    bk = wc.binding_key_for_slot(caps.image_reference_slots[0])
    kf = {"reference_bindings": {bk: {"semantic": "style", "style_id": "st1"}}}
    slots = compose_reference_present_slots(project, {}, kf, caps=caps)
    assert len(slots) == 1
    assert slots[0].display_name == "image1"
    assert slots[0].caption == "image1"


def test_compose_keyframe_reference_prelude_text_matches_prelude(tmp_path):
    char_ref = tmp_path / "hero.png"
    char_ref.write_bytes(b"x")
    caps = scan_workflow_file(VAGUE_FOUR)
    slots = sorted(caps.image_reference_slots, key=lambda s: s.image_index or 999)
    project = {
        "image_model_family": "custom",
        "characters": [{"id": "c1", "name": "Boss", "reference_image": str(char_ref)}],
    }
    kf = {
        "reference_bindings": {
            wc.binding_key_for_slot(slots[0]): {"semantic": "character", "character_id": "c1"},
        },
    }
    editor_prelude = compose_keyframe_reference_prelude_text(
        project, {}, kf, VAGUE_FOUR, caps=caps
    )
    assert "image1 is a character reference." in editor_prelude
    assert "Boss" not in editor_prelude
