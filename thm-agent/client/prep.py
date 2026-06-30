# Forked from src/single_gen_helpers.py and single_video_helpers.py temp-JSON patterns.

from __future__ import annotations

import copy
import random
import uuid
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Tuple

from client.config_helpers import (
    DEFAULT_KF_CN_SETTINGS,
    TEST_CHARACTER_DEFAULT_NEGATIVE,
    TEST_CHARACTER_PROMPT,
    TEST_CHARACTER_SETTING_PROMPT,
    TEST_SETTING_ANCHOR_PROMPT,
    TEST_SETTING_DEFAULT_NEGATIVE,
    TEST_SETTING_LAYOUT_PROMPT,
    TEST_STYLE_DEFAULT_NEGATIVE,
    apply_look_context_to_temp_project,
    asset_generator_negative,
    asset_generator_prompt,
    asset_test_negative,
    mirror_project_sampler_globals,
    resolve_context,
    workflow_for_asset_test,
)

AssetType = Literal["character", "setting", "style"]


def _unique_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


def _apply_image_seed(temp_data: dict, seed: Optional[int]) -> None:
    if "keyframe_generation" not in temp_data["project"]:
        return
    temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
    if seed is not None:
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = seed
        temp_data["project"]["keyframe_generation"]["advance_seed_by"] = 0
    else:
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)


def _apply_video_seed(temp_data: dict, seed: Optional[int]) -> None:
    if "inbetween_generation" not in temp_data["project"]:
        return
    temp_data["project"]["inbetween_generation"]["video_iterations_default"] = 1
    if seed is not None:
        temp_data["project"]["inbetween_generation"]["seed_start"] = seed
        temp_data["project"]["inbetween_generation"]["advance_seed_by"] = 0
    else:
        temp_data["project"]["inbetween_generation"]["seed_start"] = random.randint(0, 2**32 - 1)


def prep_keyframe_run(
    full_data: dict,
    seq_id: str,
    kf_id: str,
    *,
    seed: Optional[int] = None,
) -> dict:
    """Minimal project JSON for a single keyframe generation run."""
    if seq_id not in full_data.get("sequences", {}):
        raise KeyError(f"Sequence not found: {seq_id}")
    seq = full_data["sequences"][seq_id]
    if kf_id not in seq.get("keyframes", {}):
        raise KeyError(f"Keyframe not found: {kf_id} in {seq_id}")

    temp_data = copy.deepcopy(full_data)
    _apply_image_seed(temp_data, seed)

    target_seq = temp_data["sequences"][seq_id]
    target_kf = copy.deepcopy(target_seq["keyframes"][kf_id])
    target_kf["image_iterations_override"] = 1
    target_kf["force_generate"] = True

    target_seq["keyframes"] = {kf_id: target_kf}
    target_seq["keyframe_order"] = [kf_id]
    target_seq["videos"] = {}
    target_seq["video_order"] = []
    temp_data["sequences"] = {seq_id: target_seq}
    return temp_data


def prep_keyframe_run_by_id(full_data: dict, kf_id: str, *, seed: Optional[int] = None) -> Tuple[dict, str, str]:
    """Resolve keyframe by id and prep temp JSON."""
    _node, kind, _parent, seq_id = resolve_context(full_data, kf_id)
    if kind != "kf" or not seq_id:
        raise KeyError(f"Keyframe not found: {kf_id}")
    return prep_keyframe_run(full_data, seq_id, kf_id, seed=seed), seq_id, kf_id


def prep_video_run(
    full_data: dict,
    seq_id: str,
    vid_id: str,
    *,
    seed: Optional[int] = None,
) -> dict:
    """Minimal project JSON for a single video generation run."""
    seq = full_data.get("sequences", {}).get(seq_id)
    if not seq:
        raise KeyError(f"Sequence not found: {seq_id}")
    videos = seq.get("videos", {})
    if vid_id not in videos:
        raise KeyError(f"Video not found: {vid_id} in {seq_id}")

    temp_data = copy.deepcopy(full_data)
    _apply_video_seed(temp_data, seed)

    target_seq = copy.deepcopy(seq)
    target_vid = copy.deepcopy(videos[vid_id])
    target_vid["video_iterations_override"] = 1
    target_vid["force_generate"] = True
    if "start_keyframe_id" in target_vid:
        target_vid["start_id"] = target_vid["start_keyframe_id"]
    if "end_keyframe_id" in target_vid:
        target_vid["end_id"] = target_vid["end_keyframe_id"]

    target_seq["videos"] = {vid_id: target_vid}
    target_seq["video_order"] = [vid_id]

    all_keyframes = target_seq.get("keyframes", {})
    required: Dict[str, Any] = {}
    start_id = target_vid.get("start_keyframe_id")
    end_id = target_vid.get("end_keyframe_id")
    if start_id and start_id in all_keyframes:
        required[start_id] = all_keyframes[start_id]
    if end_id and end_id in all_keyframes:
        required[end_id] = all_keyframes[end_id]
    target_seq["keyframes"] = required
    target_seq["keyframe_order"] = [k for k in target_seq.get("keyframe_order", []) if k in required]

    temp_data["sequences"] = {seq_id: target_seq}
    return temp_data


def prep_video_run_by_id(full_data: dict, vid_id: str, *, seed: Optional[int] = None) -> Tuple[dict, str, str]:
    _node, kind, _parent, seq_id = resolve_context(full_data, vid_id)
    if kind != "vid" or not seq_id:
        raise KeyError(f"Video not found: {vid_id}")
    return prep_video_run(full_data, seq_id, vid_id, seed=seed), seq_id, vid_id


def prep_asset_run(
    full_data: dict,
    asset_type: AssetType,
    asset_id: str,
    *,
    look_context: dict | None = None,
    session_workflow: str | None = None,
    seed: Optional[int] = None,
    layout_override: str | None = None,
) -> Tuple[dict, str, str]:
    """Build temp JSON for character/setting/style asset test generation."""
    proj = full_data.get("project", {})
    list_key = {"character": "characters", "setting": "settings", "style": "styles"}[asset_type]
    assets = proj.get(list_key, [])
    selected = next((a for a in assets if a.get("id") == asset_id), None)
    if not selected:
        raise KeyError(f"{asset_type} not found: {asset_id}")

    unique_id = _unique_id(f"id_{asset_type[:4]}")
    temp_data = copy.deepcopy(full_data)
    cache_name = {
        "character": "__test_cache_character__",
        "setting": "__test_cache_setting__",
        "style": "__test_cache_style__",
    }[asset_type]
    temp_data["project"]["name"] = cache_name
    temp_data["project"]["style_prompt"] = proj.get("style_prompt", "")
    temp_data["project"]["model"] = proj.get("model", "")
    mirror_project_sampler_globals(temp_data, full_data)
    apply_look_context_to_temp_project(temp_data, look_context)
    _apply_image_seed(temp_data, seed)

    workflow_json = workflow_for_asset_test(
        full_data,
        kind=asset_type,
        look_flat=look_context,
        session_workflow=session_workflow,
    )

    if asset_type == "character":
        char_for_test = copy.deepcopy(selected)
        char_for_test["prompt"] = asset_generator_prompt(selected)
        char_for_test["negative_prompt"] = asset_test_negative(
            asset_generator_negative(selected), TEST_CHARACTER_DEFAULT_NEGATIVE
        )
        temp_data["project"]["characters"] = [char_for_test]
        temp_data["project"]["settings"] = []
        temp_data["project"]["styles"] = []
        char_name = selected.get("name", "character")
        if layout_override:
            layout = layout_override
        else:
            layout = f"(({TEST_CHARACTER_PROMPT}))".strip().strip(",")
        setting_prompt = TEST_CHARACTER_SETTING_PROMPT
        style_prompt = ""
        negatives = {"left": "", "right": "", "heal": ""}
        characters = [char_name, ""]
        pose = ""
        use_animal_pose = False
    elif asset_type == "setting":
        temp_data["project"]["settings"] = [copy.deepcopy(selected)]
        temp_data["project"]["characters"] = []
        temp_data["project"]["styles"] = []
        setting_prompt_text = asset_generator_prompt(selected)
        setting_neg = asset_generator_negative(selected)
        setting_prompt = "\n".join(
            p for p in [setting_prompt_text, TEST_SETTING_ANCHOR_PROMPT] if p
        ).strip()
        if layout_override:
            layout = layout_override
        else:
            layout = TEST_SETTING_LAYOUT_PROMPT
        style_prompt = ""
        negatives = {
            "left": asset_test_negative(setting_neg, TEST_SETTING_DEFAULT_NEGATIVE),
            "right": "",
            "heal": "",
        }
        characters = ["", ""]
        pose = ""
        use_animal_pose = False
    else:
        style_for_test = copy.deepcopy(selected)
        temp_data["project"]["styles"] = [style_for_test]
        temp_data["project"]["characters"] = []
        temp_data["project"]["settings"] = []
        style_prompt = asset_generator_prompt(selected)
        style_neg = asset_generator_negative(selected)
        setting_prompt = TEST_SETTING_ANCHOR_PROMPT
        layout = "((default scene))"
        negatives = {
            "left": asset_test_negative(style_neg, TEST_STYLE_DEFAULT_NEGATIVE),
            "right": "",
            "heal": "",
        }
        characters = ["", ""]
        pose = ""
        use_animal_pose = False

    test_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "pose": pose,
        "layout": layout,
        "template": "",
        "workflow_json": workflow_json,
        "negatives": negatives,
        "characters": characters,
        "selected_image_path": None,
        "use_animal_pose": use_animal_pose,
        "controlnet_settings": copy.deepcopy(DEFAULT_KF_CN_SETTINGS),
        "image_iterations_override": 1,
        "force_generate": True,
    }
    test_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_prompt": setting_prompt,
        "style_prompt": style_prompt,
        "action_prompt": "",
        "video_plan": {"open_start": False, "open_end": True},
        "keyframes": {unique_id: test_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": [],
    }
    temp_data["sequences"] = {unique_id: test_seq}
    return temp_data, unique_id, unique_id
