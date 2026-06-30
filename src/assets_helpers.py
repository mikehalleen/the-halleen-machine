# assets_helpers.py
from __future__ import annotations
import json
import uuid
import re
import os
import subprocess
import shutil
from pathlib import Path
from typing import Dict, List, Tuple, Any
import gradio as gr
import copy
import random
from datetime import datetime
from qc_helpers import handle_pose_qc 
# from run_helpers import handle_qc_batch
from helpers import (
    cb_list_pose_files, WORKFLOWS_DIR, 
    _sanitize_filename, _auto_version_path, save_to_project_folder,
    get_png_metadata, get_project_poses_dir, get_pose_gallery_list,
    gallery_gr_update,
    get_project_characters_dir, get_project_locations_dir, get_project_styles_dir,
    refresh_pose_components,
    apply_pose_gen_sampler_defaults,
    force_pose_gen_sampler_driven,
)

from single_gen_helpers import (
    run_image_generation_task,
    run_pose_preview_task,
    handle_character_test,
    handle_setting_test,
    handle_style_asset_test,
    get_style_test_images,
    recall_project_globals,
    recall_asset_generation_from_reference,
    format_asset_look_status_markdown,
    format_asset_look_status_parts,
)

def _refresh_pose_list(project_dict, pending_id, last_known_dir=None):
    """Refreshes the gallery."""
    data = project_dict
    
    poses_dir = get_project_poses_dir(data)
    current_poses_dir_str = str(poses_dir) if poses_dir else ""

    if poses_dir:
        gallery_items = get_pose_gallery_list(str(poses_dir))
    else:
        gallery_items = []

    # Restore selection if possible
    selected_index = None
    if pending_id:
        try:
            norm_pending = str(Path(pending_id).resolve()).lower()
            for i, item in enumerate(gallery_items):
                val_to_check = item[0] if isinstance(item, (list, tuple)) else item
                if str(Path(val_to_check).resolve()).lower() == norm_pending:
                    selected_index = i
                    break
        except Exception:
            pass
            
    return gr.update(value=gallery_items, selected_index=selected_index), None, current_poses_dir_str

def _on_pose_selected(project_dict, evt: gr.SelectData):
    """Handles selection in the Assets Pose Gallery."""
    # data = _loads(project_dict) # Unused but keeps signature consistent
    
    # Gradio galleries return different structures depending on config.
    selected_path = None
    if isinstance(evt.value, dict):
        selected_path = evt.value.get("image", {}).get("path") or evt.value.get("name")
    else:
        selected_path = evt.value

    return gr.update(visible=True), selected_path


def lora_choice_update(lora_list):
    """Refresh inject-dropdown choices from disk; always leave selection empty (neutral)."""
    data = lora_list if isinstance(lora_list, list) else []
    return gr.update(choices=data, value=None)


def broadcast_lora_choices(lora_list, consumers):
    """Apply ``lora_choice_update`` to every inject LoRA dropdown."""
    u = lora_choice_update(lora_list)
    return [u for _ in consumers]


def _inject_lora_simple(current_text, lora_path):
    """On user pick: prepend ``__lora:…__`` to the prompt and clear the dropdown."""
    if not lora_path:
        return gr.update(), gr.update(value=None)
    try:
        filename = os.path.basename(lora_path)
        lora_tag = f"__lora:{filename}:1.0__ "
        new_text = lora_tag + (current_text or "")
        return gr.update(value=new_text), gr.update(value=None)
    except Exception:
        return gr.update(), gr.update(value=None)
    


# --- POSE GENERATION OVERRIDE PROMPTS ---
# FAST (Original Defaults)
POSE_STYLE_FAST = "shaded sketch, depth shaded foreground, background perspective lines show the space receding behind"
POSE_MODEL_FAST = "sdXL_v10VAEFix.safetensors"
POSE_NEGATIVE_FAST = "text, watermark, camera, tripod, light stand, celebrity, infinity wall, native attire, amputation, amputee, hats, hat, fancy clothes, flat background, noise, nude, nsfw"
# POSE_GEN_CHARACTER_OVERRIDE_FAST = "body suit, natural proportions, pose model"
POSE_GEN_CHARACTER_OVERRIDE_FAST = "wearing simple sleek body suit, natural proportions, smooth seamless unitard suit"

# ENHANCED (New Defaults)
POSE_STYLE_ENHANCED = "high quality detailed illustration"
POSE_MODEL_ENHANCED = "obsessionIllustrious_v21.safetensors"
POSE_NEGATIVE_ENHANCED = "text, watermark, camera, tripod, light stand, celebrity, ornate, frame"
POSE_GEN_CHARACTER_OVERRIDE_ENHANCED = ""

POSE_GEN_SETTING_OVERRIDE_ONE = "exactly one person"
POSE_GEN_SETTING_OVERRIDE_TWO = "exactly two people, balanced framing, characters are separated into left and right sides "
# POSE_GEN_CHARACTER_OVERRIDE = "body suit, natural proportions, pose model"
POSE_GEN_NEGATIVE_ONE = "(((more than one person))) extra limbs, distorted bodies"
POSE_GEN_NEGATIVE_TWO = "(((more than two people))) comic book panels, extra limbs, distorted bodies, vertical line, line in the middle, divider"




def _resolve_asset_aux(base_path: str, subfolder: str) -> str | None:
    if not base_path: return None
    try:
        p = Path(base_path)
        # 1. Exact match
        aux = p.parent / subfolder / p.name
        if aux.exists(): return str(aux)
        # 2. Stem match
        parent = p.parent / subfolder
        if parent.exists():
            stem = p.stem.lower()
            for child in parent.iterdir():
                if child.is_file() and child.stem.lower() == stem:
                    return str(child)
        return None
    except: return None

def _get_pose_gallery_update(base_dir: str):
    """Scans the pose directory and returns a gr.update object for a Gallery."""
    return gallery_gr_update(base_dir, label_filename=True)

# ---- CHARACTER HELPERS ----
def _get_characters(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(data, dict): data = {}
    proj = data.setdefault("project", {})
    chars = proj.setdefault("characters", [])
    return chars


def _build_character_choices(data: Dict[str, Any]) -> List[Tuple[str, str]]:
    chars = _get_characters(data)
    for char in chars:
        char.setdefault("id", str(uuid.uuid4()))
    
    sorted_chars = sorted(chars, key=lambda c: c.get("name", "").lower())
    # CHANGE: Return (Name, ID) instead of (Name, Name)
    return [(c.get("name", "Unknown"), c.get("id")) for c in sorted_chars]


def _strip_pose_suffixes(filename_stem: str) -> Tuple[str, bool, str]:
    """Strips known suffixes from a pose filename stem."""
    base_name = filename_stem
    is_animal = False
    char_count = "1 Character" # Default
    
    if base_name.endswith("_ANIMAL"):
        base_name = base_name[:-len("_ANIMAL")]
        is_animal = True
        
    if base_name.endswith("_1CHAR"):
        base_name = base_name[:-len("_1CHAR")]
        char_count = "1 Character"
    elif base_name.endswith("_2CHAR"):
        base_name = base_name[:-len("_2CHAR")]
        char_count = "2 Characters"
    elif not base_name.endswith("_ANIMAL"): # Avoid stripping "No Limit" if it's part of the name
        pass # Default to "1 Character"
        
    # Re-check animal suffix in case it was before the char count
    if not is_animal and base_name.endswith("_ANIMAL"):
         base_name = base_name[:-len("_ANIMAL")]
         is_animal = True

    return base_name, is_animal, char_count

def _create_temp_json_for_pose_gen(pose_prompt: str, full_project_data: Dict, use_animal_pose: bool, char_count_choice: str, pose_mode: str, pose_negative: str = ""):
    # Select workflow based on mode
    if pose_mode == "Project Style":
        pose_workflow_path = str(WORKFLOWS_DIR / "pose_OPEN.json")
    else:
        pose_workflow_path = str(WORKFLOWS_DIR / "pose_factory.json")

    if not os.path.exists(pose_workflow_path):
        print(f"FATAL: Pose workflow not found at {pose_workflow_path}")
        return None, None 

    unique_id = f"id_pose_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"    
    temp_data = copy.deepcopy(full_project_data)
    temp_data["project"]["name"] = "__test_cache__"
    
    # Ensure kf_gen exists
    kf_gen = temp_data["project"].setdefault("keyframe_generation", {})
    
    # Override iterations/seed for pose gen (Always)
    kf_gen["image_iterations_default"] = 1
    kf_gen["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    # --- Mode Logic ---
    if pose_mode == "Project Style":
        # Use Project Settings
        temp_data["project"]["style_prompt"] = full_project_data.get("project", {}).get("style_prompt", "")
        temp_data["project"]["model"] = full_project_data.get("project", {}).get("model", "")
        
        from helpers import project_controls_kf_sampler_settings

        if project_controls_kf_sampler_settings(full_project_data):
            src_kf = full_project_data.get("project", {}).get("keyframe_generation", {})
            kf_gen["cfg"] = src_kf.get("cfg", 4.0)
            kf_gen["sampler_name"] = src_kf.get("sampler_name", "dpmpp_2m_sde")
            kf_gen["scheduler"] = src_kf.get("scheduler", "karras")
            kf_gen["steps"] = src_kf.get("steps", 30)

        char_prompt_modifier = ""
        base_negative = full_project_data.get("project", {}).get("negatives", {}).get("global", "")

    elif pose_mode == "Expressive":
        # Use Enhanced Overrides - model from config via project JSON
        temp_data["project"]["style_prompt"] = POSE_STYLE_ENHANCED
        temp_data["project"]["model"] = full_project_data.get("project", {}).get("pose_model_enhanced", POSE_MODEL_ENHANCED)
        apply_pose_gen_sampler_defaults(kf_gen)
        char_prompt_modifier = POSE_GEN_CHARACTER_OVERRIDE_ENHANCED
        base_negative = POSE_NEGATIVE_ENHANCED

    else: # "Fast" (Default)
        # Use Fast Overrides - model from config via project JSON
        temp_data["project"]["style_prompt"] = POSE_STYLE_FAST
        temp_data["project"]["model"] = full_project_data.get("project", {}).get("pose_model_fast", POSE_MODEL_FAST)
        apply_pose_gen_sampler_defaults(kf_gen)
        char_prompt_modifier = POSE_GEN_CHARACTER_OVERRIDE_FAST
        base_negative = POSE_NEGATIVE_FAST
    
    # --- Character Count Logic ---
    setting_override = ""
    negative_one = ""
    negative_two = ""
    
    character_list = ["Pose Character", ""] 
    
    if char_count_choice == "1 Character":
        setting_override = POSE_GEN_SETTING_OVERRIDE_ONE
        negative_one = POSE_GEN_NEGATIVE_ONE
    elif char_count_choice == "2 Characters":
        setting_override = POSE_GEN_SETTING_OVERRIDE_TWO
        negative_two = POSE_GEN_NEGATIVE_TWO
        
    user_negative = (pose_negative or "").strip()
    final_negative = " ".join(filter(None, [user_negative, base_negative, negative_one, negative_two])).strip()
    # --- End Character Count Logic ---

    # pose_character = {"id": "temp_pose_char_id", "name": "Pose Character", "lora_name": "", "lora_strength": 1.0, "lora_keyword": "", "prompt_modifier": char_prompt_modifier}
    pose_character = {"id": "temp_pose_char_id", "name": "Pose Character", "prompt": char_prompt_modifier, "negative_prompt": ""}

    temp_data["project"]["characters"] = [pose_character]
    
    pose_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "basic": True, 
        "pose": "none", 
        "characters": character_list, 
        "workflow_json": pose_workflow_path, 
        "layout": pose_prompt, 
        "template": "", 
        "use_animal_pose": use_animal_pose,
        "negatives": {"global": final_negative},
        "pose_user_negative": user_negative,
    }
    
    # Construct V2 Sequence
    pose_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_prompt": setting_override,
        "keyframes": { unique_id: pose_kf },
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }
    
    # Sequences is now a Dict for V2
    temp_data["sequences"] = { unique_id: pose_seq }

    if pose_mode in ("Expressive", "Fast"):
        force_pose_gen_sampler_driven(temp_data)

    return temp_data, unique_id


def handle_auto_generate_with_qc(pose_prompt: str, project_json: str, use_animal_pose: bool, char_count_choice: str, pose_mode: str, pose_negative: str = "", max_iterations: int = 10):
    """
    Auto-generate poses until one scores 3/3 or max iterations reached.
    Yields same 10 outputs as handle_pose_generation.
    """
    from qc_helpers import handle_pose_qc
    import re
    
    if not pose_prompt:
        yield (None, None, None, None, "Please enter a prompt for the pose.", None, None, None, None, None)
        return
    
    cumulative_log = []
    final_outputs = (None, None, None, None, "", None, None, None, None, None)
    
    for iteration in range(1, max(1, int(max_iterations)) + 1):
        cumulative_log.append(f"\n=== Auto QC: Iteration {iteration}/{int(max_iterations)} ===")
        cumulative_log.append("Generating pose...")
        yield (None, None, None, None, "\n".join(cumulative_log), None, None, None, None, None)
        
        # Generate pose
        gen = handle_pose_generation(pose_prompt, project_json, use_animal_pose, char_count_choice, pose_mode, pose_negative)
        temp_path = None
        for result in gen:
            # result is a 10-tuple
            if result[4]:  # log output
                display_log = cumulative_log + [result[4]]
                final_outputs = (result[0], result[1], result[2], result[3], "\n".join(display_log), result[5], result[6], result[7], result[8], result[9])
                yield final_outputs
            if result[6]:  # temp_path
                temp_path = result[6]
        
        if not temp_path:
            cumulative_log.append("Error: No image generated.")
            yield (None, None, None, None, "\n".join(cumulative_log), None, None, None, None, None)
            return
        
        cumulative_log.append(f"Generated: {os.path.basename(temp_path)}")
        cumulative_log.append("")
        cumulative_log.append("Scoring...")
        yield (final_outputs[0], final_outputs[1], final_outputs[2], final_outputs[3], "\n".join(cumulative_log), final_outputs[5], final_outputs[6], final_outputs[7], final_outputs[8], final_outputs[9])
        
        # Score pose
        qc_result = ""
        for qc_output in handle_pose_qc(temp_path, pose=True):
            qc_result = qc_output
            display_log = cumulative_log + [qc_result]
            yield (final_outputs[0], final_outputs[1], final_outputs[2], final_outputs[3], "\n".join(display_log), final_outputs[5], final_outputs[6], final_outputs[7], final_outputs[8], final_outputs[9])
        
        cumulative_log.append(qc_result)
        
        # Parse score from result (look for "Score: X/3")
        score_match = re.search(r'Score:\s*(\d)/3', qc_result)
        if score_match:
            score = int(score_match.group(1))
            if score == 3:
                cumulative_log.append("")
                cumulative_log.append(f"✓ Success after {iteration} iteration(s)")
                yield (final_outputs[0], final_outputs[1], final_outputs[2], final_outputs[3], "\n".join(cumulative_log), final_outputs[5], final_outputs[6], final_outputs[7], final_outputs[8], final_outputs[9])
                return
            else:
                cumulative_log.append(f"Score {score}/3 - retrying...")
        else:
            cumulative_log.append("Could not parse score - retrying...")
        
        cumulative_log.append("")
    
    # Max iterations reached
    cumulative_log.append(f"⚠ Max iterations ({int(max_iterations)}) reached")
    yield (final_outputs[0], final_outputs[1], final_outputs[2], final_outputs[3], "\n".join(cumulative_log), final_outputs[5], final_outputs[6], final_outputs[7], final_outputs[8], final_outputs[9])


def handle_pose_generation(pose_prompt: str, project_json: str, use_animal_pose: bool, char_count_choice: str, pose_mode: str, pose_negative: str = ""):
    """Generates a pose image by preparing data and calling the shared helper."""
    # Yields 10 values: main, pose, shape, outline, log, json, temp_path, state_pose, state_shape, state_outline
    if not pose_prompt:
        yield (None, None, None, None, "Please enter a prompt for the pose.", None, None, None, None, None)
        return

    full_data = project_json
    
    temp_data, unique_id = _create_temp_json_for_pose_gen(pose_prompt, full_data, use_animal_pose, char_count_choice, pose_mode, pose_negative)
    
    if not temp_data:
        yield (None, None, None, None, f"Error: Pose Workflow Path not found.", None, None, None, None, None)
        return

    # temp_data_str = temp_data
    # yield (None, None, None, None, "Starting pose generation...", temp_data_str, None, None, None, None)
    temp_data_str = json.dumps(temp_data, indent=2)
    yield (None, None, None, None, "Starting pose generation...", temp_data_str, None, None, None, None)


    final_main_image_path = None
    final_openpose_path = None
    final_shape_path = None
    final_outline_path = None
    final_log = ""
    
    # Loop over the dictionary results
    for result in run_image_generation_task(temp_data, "__test_cache__", unique_id, unique_id):
        final_main_image_path = result.get("main_image_path")
        final_openpose_path = result.get("openpose_path")
        final_shape_path = result.get("shape_path")
        final_outline_path = result.get("outline_path")
        final_log = result.get("log_output", "")
        # Stream updates
        yield (
            final_main_image_path, 
            final_openpose_path, 
            final_shape_path, 
            final_outline_path, 
            final_log, 
            gr.update(), 
            final_main_image_path,
            final_openpose_path,
            final_shape_path,
            final_outline_path
        )

    # After the loop, perform a final yield
    # Show notification
    if final_main_image_path:
        prompt_snippet = pose_prompt[:40] + "..." if len(pose_prompt) > 40 else pose_prompt
        gr.Info(f"✓ Pose generated: {prompt_snippet}")
    
    # If using Project Style (pose_OPEN workflow), generate previews separately
    if pose_mode == "Project Style" and final_main_image_path and not final_openpose_path:
        yield (
            final_main_image_path, 
            None, 
            None, 
            None, 
            "Extracting controlnet previews...", 
            gr.update(), 
            final_main_image_path,
            None,
            None,
            None
        )
        
        # for result in run_pose_preview_task(full_data, final_main_image_path):
        poses_dir = str(get_project_poses_dir(full_data) or "")
        for result in run_pose_preview_task(full_data, final_main_image_path, poses_dir, use_animal_pose):
            final_openpose_path = result.get("openpose_path") or final_openpose_path
            final_shape_path = result.get("shape_path") or final_shape_path
            final_outline_path = result.get("outline_path") or final_outline_path
            final_log = result.get("log_output", final_log)
    
    yield (
        final_main_image_path, 
        final_openpose_path, 
        final_shape_path, 
        final_outline_path, 
        final_log, 
        gr.update(), 
        final_main_image_path,  # This is pose_gen_temp_path - used by .then() for Save button visibility
        final_openpose_path,
        final_shape_path,
        final_outline_path
    )




def _on_pose_gallery_select(poses_dir: str, evt: gr.SelectData):    
    """Handles when a user clicks an item in the pose gallery."""
    
    # Check for deselection or invalid event
    if evt.index is None or not evt.value:
        return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), 
                gr.update(visible=False), gr.update(visible=False), 
                gr.update(value=None), gr.update(value=None), gr.update(value=None), 
                None, None, None)
    
    # Check that evt.value is a dictionary with the expected structure
    if not (isinstance(evt.value, dict) and evt.value.get('image') and evt.value['image'].get('path')):
         return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), 
                 gr.update(visible=False), gr.update(visible=False),
                 gr.update(value=None), gr.update(value=None), gr.update(value=None), 
                 None, None, None)
    
    try:
        selected_path_str = evt.value['image']['path']
        selected_path = Path(selected_path_str)
        
        if not selected_path.is_file():
            return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), 
                    gr.update(visible=False), gr.update(visible=False),
                    gr.update(value=None), gr.update(value=None), gr.update(value=None), 
                    None, None, None)

        base_name, is_animal, char_count = _strip_pose_suffixes(selected_path.stem)
        
        # Resolve Aux Images
        aux_pose = _resolve_asset_aux(selected_path_str, "poses")
        aux_shape = _resolve_asset_aux(selected_path_str, "shapes")
        aux_outline = _resolve_asset_aux(selected_path_str, "outlines")
        
        return (
            selected_path_str, # pose_edit_path_state
            selected_path_str, # pose_gen_img
            base_name,         # pose_edit_name
            is_animal,         # pose_gen_animal
            char_count,        # pose_gen_char_count
            gr.update(visible=True), # pose_edit_group
            gr.update(visible=True), # pose_delete_btn
            # Aux Images (Visuals)
            gr.update(value=aux_pose),
            gr.update(value=aux_shape),
            gr.update(value=aux_outline),
            # Aux Paths (State for Saving/Updating)
            aux_pose,
            aux_shape,
            aux_outline
        )
    except Exception as e:
        print(f"Error in gallery select: {e}")
        return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), 
                gr.update(visible=False), gr.update(visible=False),
                gr.update(value=None), gr.update(value=None), gr.update(value=None), 
                None, None, None)

def recall_pose_params(image_path: str):
    """Recalls generation parameters from a saved pose image."""
    if not image_path or not os.path.exists(image_path):
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "Image not found."

    meta = get_png_metadata(image_path)
    if not meta:
         return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "No metadata found."

    # 1. Pose Prompt (Layout)
    pose_prompt = meta.get("item_data", {}).get("layout", "")

    # 1b. User Negative Prompt (raw, as typed at generation time)
    user_negative = meta.get("item_data", {}).get("pose_user_negative", "")

    # 2. Animal
    use_animal = meta.get("item_data", {}).get("use_animal_pose", False)

    # 3. Mode (Infer from Model)
    model = meta.get("project_context", {}).get("model", "")
    mode = "Project Style" # Default fallback
    if model == POSE_MODEL_FAST:
        mode = "Simple"
    elif model == POSE_MODEL_ENHANCED:
        mode = "Expressive"
    
    # 4. Char Count (Infer from Setting Prompt)
    setting = meta.get("sequence_context", {}).get("setting_prompt", "")
    char_count = "No Limit"
    if POSE_GEN_SETTING_OVERRIDE_ONE in setting:
        char_count = "1 Character"
    elif POSE_GEN_SETTING_OVERRIDE_TWO in setting:
        char_count = "2 Characters"

    return (
        pose_prompt,
        user_negative,
        use_animal,
        mode,
        char_count,
        "Params loaded."
    )


def delete_pose(path_to_delete: str, poses_dir: str):
    """Deletes a pose file from the library."""
    if not (path_to_delete and poses_dir):
        return gr.update(), "Error: Missing path or poses directory."

    try:
        poses_root = Path(poses_dir)

        # Always delete the base pose file from the root pose folder, by filename.
        # This handles cases where UI passes:
        # - full path
        # - relative path
        # - aux-layer path (poses/shapes/outlines)
        filename = Path(path_to_delete).name
        base_pose_path = poses_root / filename

        if base_pose_path.is_file():
            # Delete aux files in subfolders first
            for folder in ["poses", "shapes", "outlines"]:
                aux_p = poses_root / folder / filename
                if aux_p.exists():
                    os.remove(aux_p)

            os.remove(base_pose_path)
            return _get_pose_gallery_update(poses_dir), f"Deleted {filename}"

        return gr.update(), "Error: File not found or is not in the pose library."

    except Exception as e:
        return gr.update(), f"Error deleting file: {e}"



    
def save_uploaded_pose(file_obj: Any, poses_dir: str, project_data: dict, use_animal_pose: bool = False, char_count_choice: str = "1 Character"):
    """
    Handles uploaded pose - extracts previews and prepares for Save.
    Matches handle_pose_generation output format.
    Yields 10 values: main, pose, shape, outline, log, json, temp_path, state_pose, state_shape, state_outline
    """
    if not file_obj:
        yield (None, None, None, None, "Error: No file uploaded.", None, None, None, None, None)
        return
    
    source_path = Path(file_obj.name)
    
    if not source_path.exists():
        yield (None, None, None, None, f"Error: Uploaded file not found.", None, None, None, None, None)
        return
    
    # Show the uploaded image immediately
    yield (
        str(source_path),  # main image
        None, None, None,  # previews (pending)
        "Extracting controlnet previews...",
        None,
        str(source_path),  # temp_path for Save button
        None, None, None
    )
    
    # Run preview extraction (saves to default ComfyUI output)
    final_openpose_path = None
    final_shape_path = None
    final_outline_path = None
    final_log = ""
    
    # for result in run_pose_preview_task(project_data, str(source_path)):    
    for result in run_pose_preview_task(project_data, str(source_path), output_dir=None, use_animal_pose=use_animal_pose):        
        final_openpose_path = result.get("openpose_path") or final_openpose_path
        final_shape_path = result.get("shape_path") or final_shape_path
        final_outline_path = result.get("outline_path") or final_outline_path
        final_log = result.get("log_output", final_log)
        
        yield (
            str(source_path),
            final_openpose_path,
            final_shape_path,
            final_outline_path,
            final_log,
            None,
            str(source_path),
            final_openpose_path,
            final_shape_path,
            final_outline_path
        )
    
    # Final yield
    if final_openpose_path:
        gr.Info(f"✓ Pose uploaded: {source_path.name}")
    
    yield (
        str(source_path),
        final_openpose_path,
        final_shape_path,
        final_outline_path,
        final_log,
        None,
        str(source_path),
        final_openpose_path,
        final_shape_path,
        final_outline_path
    )

def save_or_update_pose(original_full_path: str, pose_name: str, poses_dir: str, use_animal_pose: bool, char_count_choice: str, 
                        temp_pose: str, temp_shape: str, temp_outline: str):
    if not (original_full_path and pose_name and poses_dir):
        return "Error: Missing temp file path, pose name, or library path.", gr.update()
    if not os.path.isdir(poses_dir):
        return f"Error: Pose Library Path '{poses_dir}' is not a valid directory.", gr.update()

    try:
        source_path = Path(original_full_path)
        if not source_path.exists():
            return f"Error: Source file not found: {original_full_path}", gr.update()

        # 1. Sanitize the provided name
        base_name = _sanitize_filename(pose_name, fallback="generated_pose")
        
        # 2. Add suffixes
        if char_count_choice == "1 Character":
            base_name += "_1CHAR"
        elif char_count_choice == "2 Characters":
            base_name += "_2CHAR"
        
        if use_animal_pose:
            base_name += "_ANIMAL"
        
        dest_dir_path = Path(poses_dir)
        
        # 3. Check if we are renaming or saving new
        # If source parent is the destination, it's a rename.
        is_rename = (source_path.parent == dest_dir_path)
        
        if is_rename:
            # --- RENAME LOGIC (Specific to Asset Management) ---
            initial_dest_path = dest_dir_path / f"{base_name}{source_path.suffix}"
            
            # Use shared auto-version logic if name changed
            final_dest_path = initial_dest_path
            if source_path.name != initial_dest_path.name:
                final_dest_path = _auto_version_path(initial_dest_path)
                
            os.rename(source_path, final_dest_path)
            
            # Rename aux files
            for folder in ["poses", "shapes", "outlines"]:
                old_aux = source_path.parent / folder / source_path.name
                if old_aux.exists():
                    new_aux_dir = dest_dir_path / folder
                    new_aux_dir.mkdir(exist_ok=True)
                    os.rename(old_aux, new_aux_dir / final_dest_path.name)
                    
            status = f"Success! Renamed pose to {final_dest_path.name}"
            
        else:
            # --- SAVE NEW LOGIC (Using Shared Helper) ---
            aux_map = { "poses": temp_pose, "shapes": temp_shape, "outlines": temp_outline }
            status, _ = save_to_project_folder(original_full_path, poses_dir, base_name, aux_map)

        gallery_update = _get_pose_gallery_update(poses_dir)
        return status, gallery_update
    except Exception as e:
        return f"Error saving file: {e}", gr.update()

_ASSET_REFERENCE_DIRS = {
    "characters": get_project_characters_dir,
    "settings": get_project_locations_dir,
    "styles": get_project_styles_dir,
}


def _asset_id_for_path(asset_id: str) -> str:
    """Filesystem-safe asset id (display names are not used on disk)."""
    aid = (asset_id or "").strip()
    if not aid:
        return "asset"
    aid = re.sub(r'[<>:"/\\|?*]', "", aid)
    return aid or "asset"


def _reference_image_display(path: str | None):
    if path and os.path.isfile(path):
        return gr.update(value=path)
    return gr.update(value=None)


def _coerce_project_dict(project_dict: Any) -> dict:
    if isinstance(project_dict, dict):
        return project_dict
    if isinstance(project_dict, str) and project_dict.strip():
        try:
            loaded = json.loads(project_dict)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass
    return {}


def _normalize_gallery_select_index(index: Any) -> int | None:
    """Gradio Gallery may use int or tuple index depending on version/layout."""
    if index is None:
        return None
    if isinstance(index, int):
        return index
    if isinstance(index, (list, tuple)) and index:
        return int(index[0])
    try:
        return int(index)
    except (TypeError, ValueError):
        return None


def _paths_from_gallery_value(gallery_value: Any) -> list[str]:
    """Extract filesystem paths from gr.Gallery value in display order."""
    if not gallery_value:
        return []
    paths: list[str] = []
    for item in gallery_value:
        if isinstance(item, (list, tuple)) and item:
            paths.append(str(item[0]))
        elif isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict):
            img = item.get("image") if isinstance(item.get("image"), dict) else item
            if isinstance(img, dict):
                p = img.get("path") or img.get("name")
                if p:
                    paths.append(str(p))
    return paths


def _path_from_gallery_select_event(
    asset_dir: Path | None,
    gallery_value: Any,
    evt: gr.SelectData | None,
) -> str | None:
    """Resolve a stable on-disk path for a gallery click."""
    idx = _normalize_gallery_select_index(getattr(evt, "index", None) if evt else None)
    # Prefer the gallery item's filesystem path by click index.
    # This avoids always promoting the "first resolvable" file regardless of which thumbnail was clicked.
    paths = _paths_from_gallery_value(gallery_value)
    if idx is not None and 0 <= idx < len(paths):
        candidate = paths[idx]
        if candidate and os.path.isfile(candidate):
            resolved = _resolve_path_under_asset_dir(asset_dir, candidate)
            if resolved:
                return resolved

    if evt is not None and isinstance(getattr(evt, "value", None), dict):
        img = evt.value.get("image")
        if isinstance(img, dict):
            p = img.get("path") or img.get("name")
            if p:
                resolved = _resolve_path_under_asset_dir(asset_dir, str(p))
                if resolved:
                    return resolved

    if asset_dir and asset_dir.is_dir() and idx is not None:
        disk_items = get_pose_gallery_list(str(asset_dir))
        if 0 <= idx < len(disk_items):
            return str(disk_items[idx][0])

    return None


def _resolve_path_under_asset_dir(asset_dir: Path | None, any_path: str) -> str | None:
    """Prefer the real project path when Gradio serves a cache copy."""
    p = Path(str(any_path).strip())
    if not p.is_file():
        return None
    try:
        resolved = str(p.resolve())
    except Exception:
        resolved = str(p)

    if not asset_dir:
        return resolved

    try:
        if Path(resolved).parent.resolve() == asset_dir.resolve():
            return resolved
    except Exception:
        pass

    by_name = asset_dir / p.name
    if by_name.is_file():
        try:
            return str(by_name.resolve())
        except Exception:
            return str(by_name)

    return resolved


def _gradio_image_to_path(image_value: Any) -> str | None:
    """Normalize Gradio Image values (filepath, dict, or numpy) to a readable path."""
    if image_value is None:
        return None

    if isinstance(image_value, str):
        p = image_value.strip()
        return p if p and os.path.isfile(p) else None

    if isinstance(image_value, (list, tuple)) and image_value:
        return _gradio_image_to_path(image_value[0])

    if isinstance(image_value, dict):
        for key in ("path", "name"):
            candidate = image_value.get(key)
            if candidate:
                p = str(candidate)
                if os.path.isfile(p):
                    return p
        return None

    try:
        import numpy as np
        from PIL import Image
        import tempfile

        if hasattr(image_value, "shape"):
            arr = np.asarray(image_value)
            if arr.size == 0:
                return None
            if arr.dtype != np.uint8:
                if float(arr.max()) <= 1.0:
                    arr = (arr * 255).clip(0, 255)
                arr = arr.astype(np.uint8)
            if arr.ndim == 3 and arr.shape[2] == 4:
                arr = arr[:, :, :3]
            img = Image.fromarray(arr)
            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            img.save(tmp, format="PNG")
            return tmp
        if hasattr(image_value, "save"):
            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            image_value.save(tmp, format="PNG")
            return tmp
    except Exception:
        return None

    return None


def _find_asset_item(data: dict, collection_key: str, asset_id: str) -> dict | None:
    items = data.get("project", {}).get(collection_key, [])
    if not isinstance(items, list):
        return None
    return next((i for i in items if isinstance(i, dict) and i.get("id") == asset_id), None)


def _asset_item_dir(project_dict: Any, collection_key: str, asset_id: str) -> Path | None:
    dest_dir_fn = _ASSET_REFERENCE_DIRS.get(collection_key)
    if not dest_dir_fn:
        return None
    dest_dir = dest_dir_fn(project_dict if isinstance(project_dict, dict) else {})
    if not dest_dir:
        return None
    return Path(dest_dir) / _asset_id_for_path(asset_id)


def _asset_gallery_image_count(asset_dir: Path | None) -> int:
    if not asset_dir:
        return 0
    return len(get_pose_gallery_list(str(asset_dir)))


def _promote_asset_reference_image(
    project_dict: Any,
    collection_key: str,
    asset_id: str,
    image_path: str,
):
    """Set reference_image to an existing gallery file (no copy)."""
    data = copy.deepcopy(_coerce_project_dict(project_dict))
    path = str(image_path or "").strip()
    if not path or not os.path.isfile(path):
        return data, "Image file not found.", _reference_image_display(None), gr.update()

    item = _find_asset_item(data, collection_key, asset_id)
    if not item:
        return data, "Error: selected asset not found.", gr.update(), gr.update()

    item["reference_image"] = path
    display_name = item.get("name") or asset_id
    asset_dir = _asset_item_dir(data, collection_key, asset_id)
    gal = gallery_gr_update(str(asset_dir) if asset_dir else "", path)
    return (
        data,
        f"Selected reference for {display_name}: {Path(path).name}",
        _reference_image_display(path),
        gal,
    )


def _save_to_asset_gallery(
    project_dict: Any,
    collection_key: str,
    asset_id: str,
    temp_image_path: Any,
    base_name: str = "gallery",
):
    """Copy test/upload image into per-asset folder; auto-select if first library image."""
    data = copy.deepcopy(_coerce_project_dict(project_dict))
    src_path = _gradio_image_to_path(temp_image_path)
    if not src_path:
        return data, "No image to save. Run Generate first.", gr.update(), gr.update()

    item = _find_asset_item(data, collection_key, asset_id)
    if not item:
        return data, "Error: selected asset not found.", gr.update(), gr.update()

    asset_dir = _asset_item_dir(data, collection_key, asset_id)
    if not asset_dir:
        return data, "Error: project output path is not configured.", gr.update(), gr.update()

    first_library_image = _asset_gallery_image_count(asset_dir) == 0
    asset_dir.mkdir(parents=True, exist_ok=True)
    msg, saved_path = save_to_project_folder(src_path, str(asset_dir), base_name)
    if not saved_path:
        return data, msg or "Failed to save image.", gr.update(), gr.update()

    display_name = item.get("name") or asset_id
    if first_library_image:
        item["reference_image"] = saved_path
        ref = saved_path
        status = (
            f"Saved to library for {display_name}: {Path(saved_path).name} "
            f"(set as selected reference)"
        )
        ref_img_up = _reference_image_display(saved_path)
    else:
        ref = item.get("reference_image")
        status = f"Saved to library for {display_name}: {Path(saved_path).name}"
        ref_img_up = gr.update()

    gal = gallery_gr_update(str(asset_dir), ref)
    return data, status, gal, ref_img_up


def _upload_asset_gallery_image(
    project_dict: Any,
    collection_key: str,
    asset_id: str,
    uploaded_file: Any,
):
    """Copy upload into asset gallery folder (no post-processing)."""
    if not uploaded_file:
        return (
            copy.deepcopy(_coerce_project_dict(project_dict)),
            "Error: No file uploaded.",
            gr.update(),
            gr.update(),
        )

    src = getattr(uploaded_file, "name", None) or str(uploaded_file)
    if not src or not os.path.isfile(src):
        return (
            copy.deepcopy(_coerce_project_dict(project_dict)),
            "Error: Uploaded file not found.",
            gr.update(),
            gr.update(),
        )

    stem = _sanitize_filename(Path(src).stem, fallback="upload")
    return _save_to_asset_gallery(project_dict, collection_key, asset_id, src, base_name=stem)


def _delete_asset_gallery_image(
    project_dict: Any,
    collection_key: str,
    asset_id: str,
    path_to_delete: str | None,
):
    data = copy.deepcopy(project_dict) if isinstance(project_dict, dict) else {}
    path = str(path_to_delete or "").strip()
    if not path:
        return (
            data,
            "No image selected to delete.",
            gr.update(),
            gr.update(),
            None,
            gr.update(visible=False),
        )

    item = _find_asset_item(data, collection_key, asset_id)
    if not item:
        return (
            data,
            "Error: selected asset not found.",
            gr.update(),
            gr.update(),
            None,
            gr.update(visible=False),
        )

    try:
        p = Path(path)
        if p.is_file():
            os.remove(p)
        ref = str(item.get("reference_image") or "")
        if ref and Path(ref).resolve() == p.resolve():
            item.pop("reference_image", None)
        asset_dir = _asset_item_dir(data, collection_key, asset_id)
        new_ref = item.get("reference_image")
        gal = gallery_gr_update(str(asset_dir) if asset_dir else "", new_ref)
        return (
            data,
            f"Deleted {p.name}",
            gal,
            _reference_image_display(new_ref),
            None,
            gr.update(visible=False),
        )
    except Exception as exc:
        return (
            data,
            f"Error deleting file: {exc}",
            gr.update(),
            gr.update(),
            None,
            gr.update(visible=False),
        )


def _on_asset_gallery_select(
    project_dict: Any,
    collection_key: str,
    asset_id: str,
    gallery_value: Any,
    evt: gr.SelectData,
):
    """Promote gallery item to reference_image using displayed gallery order + disk path."""
    base = _coerce_project_dict(project_dict)
    if evt is None or _normalize_gallery_select_index(getattr(evt, "index", None)) is None or not asset_id:
        return (
            copy.deepcopy(base),
            gr.update(),
            gr.update(),
            gr.update(),
            None,
            gr.update(visible=False),
        )

    asset_dir = _asset_item_dir(base, collection_key, asset_id)
    path = _path_from_gallery_select_event(asset_dir, gallery_value, evt)
    if not path:
        return (
            copy.deepcopy(base),
            "Error: could not resolve selected image path.",
            gr.update(),
            gr.update(),
            None,
            gr.update(visible=False),
        )

    data, status, img_up, gal_up = _promote_asset_reference_image(
        base, collection_key, asset_id, path
    )
    return data, status, img_up, gal_up, path, gr.update(visible=True)


def _on_reference_asset_inspector_load(
    project_dict: Any,
    collection_key: str,
    asset_id: str,
):
    """Load inspector fields, selected reference image, and gallery for one asset."""
    data = project_dict if isinstance(project_dict, dict) else {}
    item = _find_asset_item(data, collection_key, asset_id) if asset_id else None
    empty_gal = gallery_gr_update("")

    if not item:
        return (
            gr.update(visible=False),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            _reference_image_display(None),
            empty_gal,
            gr.update(visible=False),
            None,
            gr.update(value=""),
        )

    prompt_val = item.get("prompt", "") or item.get("prompt_modifier", "")
    ref = item.get("reference_image")
    asset_dir = _asset_item_dir(data, collection_key, asset_id)
    dir_str = str(asset_dir) if asset_dir and asset_dir.is_dir() else ""
    gal = gallery_gr_update(dir_str, ref) if dir_str else empty_gal

    return (
        gr.update(visible=True),
        gr.update(value=item.get("name", "")),
        gr.update(value=prompt_val),
        gr.update(value=item.get("negative_prompt", "")),
        gr.update(value=item.get("generator_prompt", "")),
        gr.update(value=item.get("generator_negative_prompt", "")),
        _reference_image_display(ref),
        gal,
        gr.update(visible=bool(dir_str)),
        None,
        gr.update(value=""),
    )


def _make_asset_inspector_load_handler(collection_key: str):
    """Gradio-visible handler: only (project_dict, asset_id) — collection_key is closed over."""

    def handle_load(project_dict: Any, asset_id: str):
        return _on_reference_asset_inspector_load(project_dict, collection_key, asset_id)

    return handle_load


def _make_asset_gallery_select_handler(collection_key: str):
    """Gradio-visible handler: (project_dict, asset_id, gallery_value, evt)."""

    def handle_gallery_select(
        project_dict: Any,
        asset_id: str,
        gallery_value: Any,
        evt: gr.SelectData,
    ):
        return _on_asset_gallery_select(
            project_dict, collection_key, asset_id, gallery_value, evt
        )

    return handle_gallery_select


def _make_save_to_asset_gallery_handler(collection_key: str):
    def handle_save(project_dict: Any, asset_id: str, test_image: Any):
        return _save_to_asset_gallery(project_dict, collection_key, asset_id, test_image)

    return handle_save


def _make_upload_asset_gallery_handler(collection_key: str):
    def handle_upload(uploaded_file: Any, project_dict: Any, asset_id: str):
        return _upload_asset_gallery_image(project_dict, collection_key, asset_id, uploaded_file)

    return handle_upload


def _make_delete_asset_gallery_handler(collection_key: str):
    def handle_delete(project_dict: Any, asset_id: str, path_to_delete: str | None):
        return _delete_asset_gallery_image(
            project_dict, collection_key, asset_id, path_to_delete
        )

    return handle_delete


def _wire_asset_tab_enter(
    tab,
    *,
    refresh_fn,
    preview_code,
    selector,
    pending_state,
    selected_id_state,
    collection_key: str,
    inspector_outputs: list,
):
    """Refresh asset list on tab enter; auto-select first when empty; load inspector."""
    load_fn = _make_asset_inspector_load_handler(collection_key)
    tab.select(
        fn=refresh_fn,
        inputs=[preview_code, selector, pending_state],
        outputs=[selector, pending_state],
        queue=False,
    ).then(
        lambda sel: sel,
        inputs=[selector],
        outputs=[selected_id_state],
        queue=False,
    ).then(
        fn=load_fn,
        inputs=[preview_code, selected_id_state],
        outputs=inspector_outputs,
        queue=False,
    )


def _wire_asset_reference_library(
    *,
    preview_code,
    collection_key: str,
    selected_id_state,
    reference_gallery,
    gallery_path_state,
    gallery_delete_btn,
    gallery_status,
    reference_image,
    upload_btn,
    save_to_library_btn,
    test_image,
):
    """Wire gallery upload/select/delete/save for one asset type."""

    reference_gallery.select(
        fn=_make_asset_gallery_select_handler(collection_key),
        inputs=[preview_code, selected_id_state, reference_gallery],
        outputs=[
            preview_code,
            gallery_status,
            reference_image,
            reference_gallery,
            gallery_path_state,
            gallery_delete_btn,
        ],
        show_progress="hidden",
        queue=False,
    )

    save_to_library_btn.click(
        fn=_make_save_to_asset_gallery_handler(collection_key),
        inputs=[preview_code, selected_id_state, test_image],
        outputs=[preview_code, gallery_status, reference_gallery, reference_image],
        show_progress="hidden",
        queue=True,
    )

    upload_btn.upload(
        fn=_make_upload_asset_gallery_handler(collection_key),
        inputs=[upload_btn, preview_code, selected_id_state],
        outputs=[preview_code, gallery_status, reference_gallery, reference_image],
        show_progress="hidden",
        queue=True,
    )

    gallery_delete_btn.click(
        fn=_make_delete_asset_gallery_handler(collection_key),
        inputs=[preview_code, selected_id_state, gallery_path_state],
        outputs=[
            preview_code,
            gallery_status,
            reference_gallery,
            reference_image,
            gallery_path_state,
            gallery_delete_btn,
        ],
        show_progress="hidden",
        queue=True,
    )


# ---- EVENT HANDLERS (for Characters)----
def _on_asset_selected(pre_txt: str, selected_id: str):
    data = pre_txt if isinstance(pre_txt, dict) else {}
    chars = _get_characters(data)
    char_data = next((c for c in chars if c.get("id") == selected_id), None)
    if char_data:
        prompt_val = char_data.get("prompt", "")
        if not prompt_val:
            prompt_val = char_data.get("prompt_modifier", "")
        return (
            gr.update(visible=True),
            char_data.get("name", ""),
            prompt_val,
            char_data.get("negative_prompt", ""),
            _reference_image_display(char_data.get("reference_image")),
        )
    return (
        gr.update(visible=False),
        "",
        "",
        "",
        gr.update(value=None),
    )


def _resolve_asset_list_selection(
    current_val: str | None,
    pending_id: str | None,
    valid_ids: list[str],
) -> str | None:
    if pending_id and pending_id in valid_ids:
        return pending_id
    if current_val in valid_ids:
        return current_val
    if valid_ids:
        return valid_ids[0]
    return None


def _iter_project_sequences(data: dict) -> list[dict]:
    seqs = data.get("sequences") or {}
    if isinstance(seqs, dict):
        return [s for s in seqs.values() if isinstance(s, dict)]
    if isinstance(seqs, list):
        return [s for s in seqs if isinstance(s, dict)]
    return []


def _iter_sequence_keyframes(seq: dict):
    for bucket_key in ("keyframes", "i2v_base_images"):
        bucket = seq.get(bucket_key)
        if isinstance(bucket, dict):
            for kf in bucket.values():
                if isinstance(kf, dict):
                    yield kf


def _scrub_reference_binding_for_deleted_asset(
    binding: Any,
    collection_key: str,
    asset_id: str,
    seq: dict | None,
) -> dict:
    if not isinstance(binding, dict):
        return {"semantic": "unset"}
    sem = str(binding.get("semantic") or "").strip().lower()
    if collection_key == "settings":
        if str(binding.get("setting_id") or "").strip() == asset_id:
            return {"semantic": "unset"}
        if sem == "location" and str(binding.get("source") or "").strip().lower() == "sequence":
            sid = str((seq or {}).get("setting_id") or (seq or {}).get("setting_asset") or "").strip()
            if sid == asset_id:
                return {"semantic": "unset"}
    elif collection_key == "styles":
        if str(binding.get("style_id") or "").strip() == asset_id:
            return {"semantic": "unset"}
        if sem == "style" and str(binding.get("source") or "").strip().lower() == "sequence":
            sid = str((seq or {}).get("style_id") or "").strip()
            if sid == asset_id:
                return {"semantic": "unset"}
    elif collection_key == "characters":
        if str(binding.get("character_id") or "").strip() == asset_id:
            return {"semantic": "unset"}
    return dict(binding)


def _purge_asset_references(
    data: dict,
    collection_key: str,
    asset_id: str,
    *,
    deleted_name: str | None = None,
) -> None:
    """Remove sequence/keyframe references to a deleted project asset."""
    asset_id = str(asset_id or "").strip()
    if not asset_id or not isinstance(data, dict):
        return

    for seq in _iter_project_sequences(data):
        cleared_seq_setting = False
        cleared_seq_style = False
        if collection_key == "settings":
            if str(seq.get("setting_id") or "").strip() == asset_id:
                seq["setting_id"] = ""
                seq.pop("setting_reference_image", None)
                cleared_seq_setting = True
            if str(seq.get("setting_asset_last_id") or "").strip() == asset_id:
                seq.pop("setting_asset_last_id", None)
            if str(seq.get("setting_asset") or "").strip() == asset_id:
                seq["setting_asset"] = ""
        elif collection_key == "styles":
            if str(seq.get("style_id") or "").strip() == asset_id:
                seq["style_id"] = ""
                seq.pop("style_reference_image", None)
                cleared_seq_style = True
            if str(seq.get("style_asset_last_id") or "").strip() == asset_id:
                seq.pop("style_asset_last_id", None)

        for kf in _iter_sequence_keyframes(seq):
            if collection_key == "characters" and deleted_name:
                chars = kf.get("characters")
                if isinstance(chars, list):
                    kf["characters"] = ["" if c == deleted_name else c for c in chars]
            bindings = kf.get("reference_bindings")
            if isinstance(bindings, dict):
                for slot, binding in list(bindings.items()):
                    b = binding if isinstance(binding, dict) else {}
                    sem = str(b.get("semantic") or "").strip().lower()
                    src = str(b.get("source") or "").strip().lower()
                    if cleared_seq_setting and sem == "location" and src == "sequence":
                        bindings[slot] = {"semantic": "unset"}
                        continue
                    if cleared_seq_style and sem == "style" and src == "sequence":
                        bindings[slot] = {"semantic": "unset"}
                        continue
                    bindings[slot] = _scrub_reference_binding_for_deleted_asset(
                        binding, collection_key, asset_id, seq
                    )


def _refresh_char_list(json_txt: str, current_val: str, pending_id: str | None):
    """Refreshes the character list, prioritizing a pending selection if set."""
    try:
        data = json_txt
        choices = _build_character_choices(data)
        valid_ids = [c[1] for c in choices]
        final_val = _resolve_asset_list_selection(current_val, pending_id, valid_ids)
        return gr.update(choices=choices, value=final_val), None
    except Exception:
        return gr.update(), None

def _add_character(pre_txt: str):
    data = pre_txt if isinstance(pre_txt, dict) else {}
    proj = data.setdefault("project", {})
    chars = proj.setdefault("characters", [])
    new_id = str(uuid.uuid4())
    new_char = {
        "id": new_id,
        "name": "New Character",
        "prompt": "",
        "negative_prompt": ""
    }
    chars.append(new_char)
    return data, new_id


def _delete_character(pre_txt: str, selected_id: str):
    data = pre_txt if isinstance(pre_txt, dict) else {}
    chars = _get_characters(data)
    deleted = next((c for c in chars if c.get("id") == selected_id), None)
    deleted_name = (deleted or {}).get("name")
    chars_after_delete = [c for c in chars if c.get("id") != selected_id]
    data["project"]["characters"] = chars_after_delete
    _purge_asset_references(data, "characters", selected_id, deleted_name=deleted_name)
    return data, None

def _refresh_asset_look_gallery(project_dict):
    paths = get_style_test_images(project_dict)
    return paths, paths


def _asset_look_ui_parts(project_dict, look_context, look_paths=None):
    return format_asset_look_status_parts(look_context, project_dict, look_paths)


def _on_asset_look_gallery_select(evt: gr.SelectData, project_dict, paths: list):
    if evt is None or getattr(evt, "index", None) is None or not paths:
        return (None, *_asset_look_ui_parts(project_dict, None, paths))
    try:
        path = paths[evt.index]
    except (IndexError, TypeError):
        return (None, *_asset_look_ui_parts(project_dict, None, paths))
    look_flat, _msg = recall_project_globals(path)
    return look_flat, *_asset_look_ui_parts(project_dict, look_flat, paths)


def _recall_asset_gen_from_reference_image(image_path: str, project_dict, look_paths=None):
    look_flat, gen_prompt, gen_neg, _summary, status = recall_asset_generation_from_reference(
        image_path
    )
    indicator, details = _asset_look_ui_parts(project_dict, look_flat, look_paths)
    return look_flat, gen_prompt, gen_neg, indicator, details, status


def _update_character_fields(
    pre_txt: str,
    selected_id: str,
    name,
    prompt_val,
    neg_prompt,
    gen_prompt,
    gen_neg,
):
    if not selected_id:
        return pre_txt, gr.update()
    data = pre_txt if isinstance(pre_txt, dict) else {}
    chars = _get_characters(data)
    char_to_update = next((c for c in chars if c.get("id") == selected_id), None)
    if char_to_update:
        old_name = char_to_update.get("name")
        new_name = name.strip()
        char_to_update["name"] = new_name
        char_to_update["prompt"] = prompt_val
        char_to_update["negative_prompt"] = neg_prompt
        char_to_update["generator_prompt"] = gen_prompt
        char_to_update["generator_negative_prompt"] = gen_neg
        char_to_update.pop("lora_keyword", None)
        
        # UPDATE: V2 Safe Traversal for updating references
        if old_name and old_name != new_name:
            seqs = data.get("sequences", {})
            # Handle list (V1) or dict (V2)
            iterator = seqs.values() if isinstance(seqs, dict) else seqs
            
            for seq in iterator:
                # 1. Check V2 'keyframes'
                if "keyframes" in seq and isinstance(seq["keyframes"], dict):
                    for kf in seq["keyframes"].values():
                        if "characters" in kf:
                            kf["characters"] = [new_name if char == old_name else char for char in kf["characters"]]
                
                # 2. Check V1 'i2v_base_images' (Backup)
                if "i2v_base_images" in seq and isinstance(seq["i2v_base_images"], dict):
                    for kf in seq["i2v_base_images"].values():
                         if "characters" in kf:
                            kf["characters"] = [new_name if char == old_name else char for char in kf["characters"]]

    new_choices = _build_character_choices(data)
    return data, gr.update(choices=new_choices)




def _get_list_by_key(data: Dict, key: str) -> List[Dict]:
    return data.get("project", {}).setdefault(key, [])

def _build_simple_choices(data: Dict, key: str) -> List[Tuple[str, str]]:
    items = _get_list_by_key(data, key)
    # Ensure IDs
    for item in items:
        if "id" not in item: item["id"] = str(uuid.uuid4())
    sorted_items = sorted(items, key=lambda x: x.get("name", "").lower())
    return [(i.get("name", "Unknown"), i.get("id")) for i in sorted_items]


def _refresh_simple_list(json_txt: str, key: str, current_val: str, pending_id: str | None):
    try:
        data = json_txt
        choices = _build_simple_choices(data, key)
        valid_ids = [c[1] for c in choices]
        final_val = _resolve_asset_list_selection(current_val, pending_id, valid_ids)
        return gr.update(choices=choices, value=final_val), None
    except Exception:
        return gr.update(), None

def _add_simple_item(data, path, default_name):
    # Resolve target container
    if isinstance(path, tuple):
        d = data
        for key in path[:-1]:
            d = d.setdefault(key, {})
        key = path[-1]
    else:
        d = data
        key = path

    items = d.setdefault(key, [])

    new_id = str(uuid.uuid4())
    item = {
        "id": new_id,
        "name": default_name,
        "prompt": "",
        "negative_prompt": ""
    }
    items.append(item)
    return data



def _delete_simple_item(project_dict, key, item_id):
    data = project_dict if isinstance(project_dict, dict) else {}
    items = data.get("project", {}).get(key, [])
    if not isinstance(items, list):
        return data, None

    data["project"][key] = [i for i in items if isinstance(i, dict) and i.get("id") != item_id]
    if key in ("settings", "styles"):
        _purge_asset_references(data, key, item_id)
    return data, None


def _on_simple_item_selected(project_dict, key, item_id):
    data = project_dict if isinstance(project_dict, dict) else {}

    # Resolve items list (supports string or tuple path)
    if isinstance(key, tuple):
        d = data
        for k in key:
            d = d.get(k, {})
        items = d
    else:
        items = data.get("project", {}).get(key, [])

    if not isinstance(items, list):
        items = []

    item = next((i for i in items if isinstance(i, dict) and i.get("id") == item_id), None)
    if item:
        return (
            gr.update(visible=True),
            gr.update(value=item.get("name", "")),
            gr.update(value=item.get("prompt", "")),
            gr.update(value=item.get("negative_prompt", "")),
            _reference_image_display(item.get("reference_image")),
        )

    return (
        gr.update(visible=False),
        gr.update(value=""),
        gr.update(value=""),
        gr.update(value=""),
        gr.update(value=None),
    )




def _update_simple_fields(
    project_dict,
    key,
    item_id,
    name_val,
    prompt_val,
    negative_prompt_val,
    generator_prompt_val,
    generator_negative_val,
):
    data = project_dict if isinstance(project_dict, dict) else {}
    items = data.get("project", {}).get(key, [])
    if not isinstance(items, list):
        items = []
        data.setdefault("project", {})[key] = items

    for item in items:
        if isinstance(item, dict) and item.get("id") == item_id:
            item["name"] = name_val
            item["prompt"] = prompt_val
            item["negative_prompt"] = negative_prompt_val
            item["generator_prompt"] = generator_prompt_val
            item["generator_negative_prompt"] = generator_negative_val
            item.pop("lora_keyword", None)
            break

    # Rebuild choices
    for item in items:
        if isinstance(item, dict) and "id" not in item:
            item["id"] = str(uuid.uuid4())

    sorted_items = sorted(
        [i for i in items if isinstance(i, dict)],
        key=lambda x: x.get("name", "").lower()
    )
    choices = [(i.get("name", "Unknown"), i.get("id")) for i in sorted_items]

    return data, gr.update(choices=choices, value=item_id)



# def build_assets_tab(preview_code: gr.Code, settings_json: gr.State):
def build_assets_tab(preview_code: gr.Code, settings_json: gr.State, current_file_path: gr.State, features: Dict = {}):
    gr.HTML("""
    <style>
      #pose_gallery .grid-container,
      #char_reference_gallery .grid-container,
      #setting_reference_gallery .grid-container,
      #style_reference_gallery .grid-container,
      #char_look_gallery .grid-container,
      #setting_look_gallery .grid-container,
      #style_look_gallery .grid-container {
        grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      }
      .asset-model-settings-stack > .form {
        gap: 4px !important;
        padding: 0 !important;
      }
      .asset-model-settings-stack .asset-look-indicator.block,
      .asset-model-settings-stack .asset-look-indicator.block > .prose,
      .asset-model-settings-stack .asset-look-details.block,
      .asset-model-settings-stack .asset-look-details.block > .prose {
        margin: 0 !important;
        padding: 0 !important;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
      }
      .asset-model-settings-stack > .form > .block.accordion {
        margin-top: 0 !important;
      }
      .asset-columns-row > .column {
        gap: 8px !important;
        min-width: 0 !important;
        background: transparent !important;
        background-color: transparent !important;
        align-self: flex-start !important;
        flex-grow: 1 !important;
        height: auto !important;
        min-height: 0 !important;
      }
      .asset-columns-row > .column > .form {
        align-items: flex-start !important;
        height: auto !important;
        flex-grow: 0 !important;
        background: transparent !important;
        background-color: transparent !important;
      }
      .asset-columns-row > .column > .form > .block {
        flex-grow: 0 !important;
        flex-shrink: 0 !important;
      }
      .asset-inspector-shell,
      .asset-inspector-shell > .form {
        border: none !important;
        box-shadow: none !important;
        padding: 0 !important;
        background: transparent !important;
        background-color: transparent !important;
        gap: 8px !important;
      }
      .asset-inspector-shell .styler {
        background: transparent !important;
      }
      .asset-columns-row .group {
        background: transparent !important;
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 !important;
      }
      .asset-preview-row {
        width: 100% !important;
        max-width: 100% !important;
        box-sizing: border-box !important;
      }
      @media (max-width: 900px) {
        #pose_gallery .grid-container,
        #char_reference_gallery .grid-container,
        #setting_reference_gallery .grid-container,
        #style_reference_gallery .grid-container {
          grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
        }
        .asset-columns-row {
          flex-direction: column !important;
          flex-wrap: nowrap !important;
        }
        .asset-columns-row > .column {
          width: 100% !important;
          flex: 1 1 auto !important;
          max-width: 100% !important;
        }
        .asset-columns-row > .column > .form > .block {
          flex-shrink: 1 !important;
          max-width: 100% !important;
        }
        .asset-preview-row {
          flex-wrap: wrap !important;
          gap: 4px !important;
          width: 100% !important;
          max-width: 100% !important;
          overflow: hidden !important;
          box-sizing: border-box !important;
        }
        .asset-preview-row > .column {
          flex: 1 1 calc((100% - 8px) / 3) !important;
          min-width: 0 !important;
          max-width: calc((100% - 8px) / 3) !important;
          width: calc((100% - 8px) / 3) !important;
          box-sizing: border-box !important;
          overflow: hidden !important;
        }
        .asset-preview-row > .column > .form,
        .asset-preview-row > .column > .form > .block {
          width: 100% !important;
          max-width: 100% !important;
          min-width: 0 !important;
          flex-shrink: 1 !important;
          box-sizing: border-box !important;
        }
        .asset-preview-row .block.image {
          width: 100% !important;
          max-width: 100% !important;
          height: auto !important;
          max-height: 72px !important;
          min-height: 0 !important;
        }
        .asset-preview-row .block.image .image-container,
        .asset-preview-row .block.image .wrap {
          width: 100% !important;
          max-width: 100% !important;
          height: 64px !important;
          max-height: 64px !important;
          min-height: 0 !important;
        }
        .asset-preview-row .block.image img {
          width: 100% !important;
          max-width: 100% !important;
          max-height: 64px !important;
          height: auto !important;
          object-fit: contain !important;
        }
      }
      @media (max-width: 420px) {
        .asset-preview-row > .column {
          flex: 1 1 calc((100% - 4px) / 2) !important;
          max-width: calc((100% - 4px) / 2) !important;
          width: calc((100% - 4px) / 2) !important;
        }
      }
    </style>
    """)
    asset_gen_look_context = gr.State(value=None)
    asset_look_paths_state = gr.State(value=[])

    with gr.Tabs():
        # ============================================================
        # POSES TAB
        # ============================================================
        # with gr.TabItem("Poses"):
        with gr.TabItem("Poses") as poses_tab:    
            poses_dir_state = gr.State(value="")
            pose_edit_path_state = gr.State(value=None)
            
            # States for Auxiliary Files
            pose_gen_pose_path = gr.State(value=None)
            pose_gen_shape_path = gr.State(value=None)
            pose_gen_outline_path = gr.State(value=None)

            with gr.Row(elem_classes=["asset-columns-row"], equal_height=False):
                # ========== COLUMN 1: Properties & Generate ==========
                with gr.Column(scale=1):
                    with gr.Accordion("New Pose Properties", open=True):
                        pose_gen_prompt = gr.Textbox(
                            label="Pose Prompt",
                            info="Describe the desired pose and composition",
                            lines=2,
                            placeholder="e.g., a person standing with arms crossed",
                        )

                        pose_gen_negative = gr.Textbox(
                            label="Negative Prompt",
                            info="Optional. Added on top of the built-in pose negatives",
                            lines=2,
                            placeholder="e.g., blurry, extra limbs",
                        )

                        pose_gen_animal = gr.Checkbox(
                            label="Animal",
                            info="Enable animal-specific controlnet processing",
                            value=False,
                        )

                        pg_choices = ["Simple", "Expressive"]
                        if features.get("show_project_style_pose", True):
                            pg_choices.append("Project Style")

                        pose_gen_mode = gr.Radio(
                            label="Generation Mode",
                            choices=pg_choices,
                            value="Simple",
                            info="Simple: clean monochrome base (recommended) • Expressive: detailed but may limit flexibility",
                        )

                        pose_gen_char_count = gr.Radio(
                            label="Character Count",
                            choices=["1 Character", "2 Characters", "No Limit"],
                            value="1 Character",
                            info="1 Character: enforces single subject • 2 Characters: balanced layout for two subjects • No Limit: open composition",
                        )

                        pose_gen_btn = gr.Button("Generate Pose", variant="secondary")
                    with gr.Accordion("Status", open=False) as pose_gen_status_acc:
                        pose_gen_status = gr.Textbox(
                            label="Status",
                            interactive=False,
                            lines=4,
                        )

                # ========== COLUMN 2: Generated Result ==========
                with gr.Column(scale=1):
                    pose_gen_img = gr.Image(
                        label="Generated Result",
                        interactive=False,
                        height=256,
                    )

                    with gr.Row(elem_classes=["asset-preview-row"]):
                        pose_gen_preview_img = gr.Image(
                            label="Pose",
                            interactive=False,
                            height=128,
                        )
                        pose_gen_shape_preview_img = gr.Image(
                            label="Shape",
                            interactive=False,
                            height=128,
                        )
                        pose_gen_outline_preview_img = gr.Image(
                            label="Outline",
                            interactive=False,
                            height=128,
                        )

                    with gr.Accordion("QC", open=False, visible=features.get("show_QC", False)) as pose_qc_accordion:
                        pose_qc_btn = gr.Button("QC Pose", variant="secondary")
                        pose_qc_max_iter = gr.Number(label="Max Iterations", value=10, precision=0, minimum=1, maximum=50, interactive=True)
                        with gr.Row():
                            pose_auto_qc_btn = gr.Button("Auto Generate with QC", variant="secondary")
                            pose_auto_qc_cancel_btn = gr.Button("Cancel", variant="stop")

                    with gr.Group(visible=False) as pose_edit_group:
                        pose_edit_name = gr.Textbox(
                            label="Pose Name",
                            info="Name for saving to gallery",
                            interactive=True,
                        )
                        with gr.Row():
                            pose_update_btn = gr.Button(
                                "Save / Update Pose",
                                variant="secondary",
                            )

                    pose_gen_temp_json_preview = gr.Code(
                        language="json",
                        interactive=False,
                        visible=False,
                    )
                    pose_gen_temp_path = gr.State(value=None)

                # ========== COLUMN 3: Pose Library ==========
                with gr.Column(scale=1):
                    with gr.Accordion("Pose Library", open=True, elem_classes=["themed-accordion", "proj-theme"]):
                        pose_gallery = gr.Gallery(
                            label="Pose Library",
                            elem_id="pose_gallery",
                            height=200,
                            object_fit="contain",
                            allow_preview=False,
                        )

                        pose_upload_btn = gr.UploadButton(
                            "Upload Pose",
                            file_types=["image"],
                            file_count="single",
                            scale=1,
                        )
                        with gr.Row():
                            pose_recall_btn = gr.Button(
                                "Load Properties from Image",
                                variant="secondary",
                                scale=1,
                            )

                            pose_delete_btn = gr.Button(
                                "Delete Pose",
                                variant="stop",
                                visible=False,
                            )

                        pose_upload_status = gr.Textbox(
                            label="Status",
                            interactive=False,
                            show_label=False,
                            visible=False,
                        )

            # ========== EVENT HANDLERS ==========
            pose_upload_btn.upload(
                fn=save_uploaded_pose,
                inputs=[pose_upload_btn, poses_dir_state, preview_code, pose_gen_animal, pose_gen_char_count],
                outputs=[
                    pose_gen_img, pose_gen_preview_img, pose_gen_shape_preview_img, pose_gen_outline_preview_img,
                    pose_gen_status, pose_gen_temp_json_preview, pose_gen_temp_path,
                    pose_gen_pose_path, pose_gen_shape_path, pose_gen_outline_path
                ]
            ).then(
                fn=lambda temp_path: (gr.update(visible=(temp_path is not None)), Path(temp_path).stem if temp_path else "", temp_path, gr.update(visible=False)),
                inputs=[pose_gen_temp_path],
                outputs=[pose_edit_group, pose_edit_name, pose_edit_path_state, pose_delete_btn]
            )
            

            pose_update_btn.click(
                fn=save_or_update_pose,
                inputs=[
                    pose_edit_path_state,
                    pose_edit_name,
                    poses_dir_state,
                    pose_gen_animal,
                    pose_gen_char_count,
                    pose_gen_pose_path,
                    pose_gen_shape_path,
                    pose_gen_outline_path,
                ],
                outputs=[pose_gen_status, pose_gallery],
            )

            pose_delete_btn.click(
                fn=delete_pose,
                inputs=[pose_edit_path_state, poses_dir_state],
                outputs=[pose_gallery, pose_gen_status]
            ).then(
                fn=lambda: (None, None, "", False, "1 Character", gr.update(visible=False), gr.update(visible=False)),
                outputs=[pose_edit_path_state, pose_gen_img, pose_edit_name, pose_gen_animal, pose_gen_char_count, pose_edit_group, pose_delete_btn]
            )
            
            pose_recall_btn.click(
                fn=recall_pose_params,
                inputs=[pose_edit_path_state],
                outputs=[pose_gen_prompt, pose_gen_negative, pose_gen_animal, pose_gen_mode, pose_gen_char_count, pose_gen_status]
            )
            
            def _pose_qc_wrapper(img):
                yield from handle_pose_qc(img, pose=True)
            
            pose_qc_btn.click(
                fn=lambda: gr.update(open=True),
                inputs=[],
                outputs=[pose_gen_status_acc]
            ).then(
                fn=_pose_qc_wrapper,
                inputs=[pose_edit_path_state],
                outputs=[pose_gen_status]
            )
            
            auto_qc_event = pose_auto_qc_btn.click(
                fn=lambda: gr.update(open=True),
                inputs=[],
                outputs=[pose_gen_status_acc]
            ).then(
                fn=handle_auto_generate_with_qc,
                inputs=[pose_gen_prompt, preview_code, pose_gen_animal, pose_gen_char_count, pose_gen_mode, pose_gen_negative, pose_qc_max_iter],
                outputs=[
                    pose_gen_img, pose_gen_preview_img, pose_gen_shape_preview_img, pose_gen_outline_preview_img,
                    pose_gen_status, pose_gen_temp_json_preview, pose_gen_temp_path,
                    pose_gen_pose_path, pose_gen_shape_path, pose_gen_outline_path
                ]
            ).then(
                fn=lambda temp_path, prompt: (gr.update(visible=(temp_path is not None)), _sanitize_filename(prompt), temp_path, gr.update(visible=False)),
                inputs=[pose_gen_temp_path, pose_gen_prompt],
                outputs=[pose_edit_group, pose_edit_name, pose_edit_path_state, pose_delete_btn]
            )
            
            pose_auto_qc_cancel_btn.click(
                fn=None,
                inputs=None,
                outputs=None,
                cancels=[auto_qc_event]
            )

            pose_gen_btn.click(
                fn=handle_pose_generation,
                inputs=[pose_gen_prompt, preview_code, pose_gen_animal, pose_gen_char_count, pose_gen_mode, pose_gen_negative],
                outputs=[
                    pose_gen_img, pose_gen_preview_img, pose_gen_shape_preview_img, pose_gen_outline_preview_img,
                    pose_gen_status, pose_gen_temp_json_preview, pose_gen_temp_path,
                    pose_gen_pose_path, pose_gen_shape_path, pose_gen_outline_path
                ]
            ).then(
                fn=lambda temp_path, prompt: (gr.update(visible=(temp_path is not None)), _sanitize_filename(prompt), temp_path, gr.update(visible=False)),
                inputs=[pose_gen_temp_path, pose_gen_prompt],
                outputs=[pose_edit_group, pose_edit_name, pose_edit_path_state, pose_delete_btn]
            )
            
            pose_gallery.select(
                fn=_on_pose_gallery_select,
                inputs=[poses_dir_state],
                outputs=[
                    pose_edit_path_state,
                    pose_gen_img,
                    pose_edit_name,
                    pose_gen_animal,
                    pose_gen_char_count,
                    pose_edit_group,
                    pose_delete_btn,
                    # Aux Layers
                    pose_gen_preview_img,
                    pose_gen_shape_preview_img,
                    pose_gen_outline_preview_img,
                    pose_gen_pose_path,
                    pose_gen_shape_path,
                    pose_gen_outline_path
                ],
                show_progress="hidden",
                queue=False
            )
            def _refresh_pose_gallery_on_tab(pj, pd):
                print("[DEBUG] poses_tab.select triggered - refreshing gallery")
                gallery_update, _, new_dir = _refresh_pose_list(pj, None, pd)
                print(f"[DEBUG] gallery_update: {gallery_update}")
                return gallery_update, new_dir
            
            poses_tab.select(
                fn=_refresh_pose_gallery_on_tab,
                inputs=[preview_code, poses_dir_state],
                outputs=[pose_gallery, poses_dir_state],
                queue=False
            )

        # ============================================================
        # CHARACTERS TAB
        # ============================================================
        with gr.TabItem("Characters") as characters_tab:
            with gr.Row():
                add_char_btn = gr.Button("+ Add Character", variant="primary", scale=0)
                char_selector = gr.Dropdown(
                    label="Character",
                    choices=[],
                    value=None,
                    interactive=True,
                    allow_custom_value=False,
                    filterable=False,
                    scale=3,
                )

            with gr.Group(visible=False, elem_classes=["asset-inspector-shell"]) as inspector_group:
                with gr.Row(elem_classes=["asset-columns-row"], equal_height=False):
                    with gr.Column(scale=1):
                        with gr.Accordion("Properties", open=True):
                            char_name = gr.Textbox(
                                label="Character Name",
                                info="Display name for this character",
                            )
                            char_prompt_mod = gr.Textbox(
                                label="Character Prompt",
                                info="Included in keyframe generation",
                                lines=4,
                            )
                            char_inject_lora = gr.Dropdown(
                                label="Inject LoRA Tag",
                                info="Add a LoRA tag to the keyframe prompt (dropdown clears after each pick)",
                                choices=[],
                                value=None,
                                interactive=True,
                            )
                            char_neg_prompt = gr.Textbox(
                                label="Negative Prompt",
                                info="Included in keyframe generation",
                                lines=2,
                            )
                            char_test_pose = gr.Dropdown(
                                label="Test Pose",
                                info="Select a pose to preview this character",
                                choices=[],
                                interactive=True,
                                filterable=False,
                                visible=False,
                            )

                    with gr.Column(scale=1):
                        with gr.Accordion("Generation", open=True):
                            char_test_image = gr.Image(
                                label="Generated Result",
                                type="filepath",
                                interactive=False,
                                height=256,
                            )
                            test_char_btn = gr.Button("Generate", variant="primary")
                            char_reference_save_btn = gr.Button(
                                "Save to this Character", variant="secondary", visible=True
                            )
                            with gr.Group() as char_gen_prompt_group:
                                char_gen_prompt = gr.Textbox(
                                    label="Generator Prompt",
                                    info="Session asset generation only; falls back to Character Prompt if empty",
                                    lines=4,
                                )
                                char_gen_inject_lora = gr.Dropdown(
                                    label="Inject LoRA Tag",
                                    info="Add a LoRA tag to the generator prompt (dropdown clears after each pick)",
                                    choices=[],
                                    value=None,
                                    interactive=True,
                                )
                                char_gen_neg_prompt = gr.Textbox(
                                    label="Negative Prompt",
                                    info="Session asset generation only; falls back to keyframe Negative Prompt if empty",
                                    lines=2,
                                )
                            with gr.Group(elem_classes=["asset-model-settings-stack"]) as char_model_settings_group:
                                char_look_indicator = gr.Markdown(
                                    elem_classes=["info-text", "asset-look-indicator"],
                                )
                                with gr.Accordion("Model Settings", open=False):
                                    char_look_gallery = gr.Gallery(
                                        show_label=False,
                                        elem_id="char_look_gallery",
                                        height=160,
                                        object_fit="contain",
                                        allow_preview=False,
                                    )
                                    char_look_details = gr.Markdown(elem_classes=["info-text", "asset-look-details"])
                        with gr.Accordion("Status", open=False):
                            char_test_log = gr.Textbox(
                                label="Generation Log",
                                lines=8,
                                interactive=False,
                                autoscroll=True,
                            )

                    with gr.Column(scale=1) as char_reflib_group:
                        with gr.Accordion(
                            "Reference Library",
                            open=True,
                            elem_classes=["themed-accordion", "proj-theme"],
                        ):
                            char_ref_gallery = gr.Gallery(
                                label="Reference images",
                                elem_id="char_reference_gallery",
                                height=200,
                                object_fit="contain",
                                allow_preview=False,
                            )
                            char_gallery_path_state = gr.State(value=None)
                            with gr.Row():
                                char_ref_upload_btn = gr.UploadButton(
                                    "Upload image",
                                    file_types=["image"],
                                    file_count="single",
                                )
                                char_gallery_delete_btn = gr.Button(
                                    "Delete image",
                                    variant="stop",
                                    visible=False,
                                )
                            char_gallery_status = gr.Markdown("")
                            char_recall_gen_btn = gr.Button(
                                "Load generation settings from image",
                                variant="secondary",
                            )
                            char_reference_image = gr.Image(
                                label="Selected reference",
                                type="filepath",
                                interactive=False,
                                height=200,
                            )

                with gr.Accordion("Manage", open=False, elem_classes=["themed-accordion", "stop-theme"]):
                    delete_char_btn = gr.Button("Delete Character", variant="stop")

            # [State and event handlers remain the same - lines 1065-1145]
            selected_char_id = gr.State(value="")
            pending_char_selection = gr.State(value=None)

            preview_code.change(
                _refresh_char_list,
                inputs=[preview_code, selected_char_id, pending_char_selection],
                outputs=[char_selector, pending_char_selection],
                queue=False
            )

            _char_inspector_outputs = [
                inspector_group,
                char_name,
                char_prompt_mod,
                char_neg_prompt,
                char_gen_prompt,
                char_gen_neg_prompt,
                char_reference_image,
                char_ref_gallery,
                char_gallery_delete_btn,
                char_gallery_path_state,
                char_gallery_status,
            ]
            _wire_asset_tab_enter(
                characters_tab,
                refresh_fn=_refresh_char_list,
                preview_code=preview_code,
                selector=char_selector,
                pending_state=pending_char_selection,
                selected_id_state=selected_char_id,
                collection_key="characters",
                inspector_outputs=_char_inspector_outputs,
            )

            char_selector.change(
                lambda sel: sel, inputs=[char_selector], outputs=[selected_char_id], queue=False
            ).then(
                fn=_make_asset_inspector_load_handler("characters"),
                inputs=[preview_code, selected_char_id],
                outputs=_char_inspector_outputs,
                queue=False,
            )
            
            add_char_btn.click(_add_character, inputs=[preview_code], outputs=[preview_code, pending_char_selection])
            delete_char_btn.click(_delete_character, inputs=[preview_code, selected_char_id], outputs=[preview_code, pending_char_selection])

            char_inject_lora.select(
                fn=_inject_lora_simple,
                inputs=[char_prompt_mod, char_inject_lora],
                outputs=[char_prompt_mod, char_inject_lora],
                queue=False,
                show_progress="hidden",
            ).then(
                fn=_update_character_fields,
                inputs=[
                    preview_code,
                    selected_char_id,
                    char_name,
                    char_prompt_mod,
                    char_neg_prompt,
                    char_gen_prompt,
                    char_gen_neg_prompt,
                ],
                outputs=[preview_code, char_selector],
                queue=False,
                show_progress="hidden"
            )

            char_gen_inject_lora.select(
                fn=_inject_lora_simple,
                inputs=[char_gen_prompt, char_gen_inject_lora],
                outputs=[char_gen_prompt, char_gen_inject_lora],
                queue=False,
                show_progress="hidden",
            ).then(
                fn=_update_character_fields,
                inputs=[
                    preview_code,
                    selected_char_id,
                    char_name,
                    char_prompt_mod,
                    char_neg_prompt,
                    char_gen_prompt,
                    char_gen_neg_prompt,
                ],
                outputs=[preview_code, char_selector],
                queue=False,
                show_progress="hidden"
            )

            inspector_fields = [
                char_name,
                char_prompt_mod,
                char_neg_prompt,
                char_gen_prompt,
                char_gen_neg_prompt,
            ]
            text_or_number_fields = inspector_fields

            for field in inspector_fields:
                inputs = [preview_code, selected_char_id, *inspector_fields]
                outputs = [preview_code, char_selector]
                
                if field in text_or_number_fields:
                    field.blur(_update_character_fields, inputs=inputs, outputs=outputs, queue=False, show_progress="hidden")
                    field.submit(_update_character_fields, inputs=inputs, outputs=outputs, queue=False, show_progress="hidden")
                else:
                    field.change(_update_character_fields, inputs=inputs, outputs=outputs, queue=False, show_progress="hidden")

            test_char_btn.click(
                fn=handle_character_test,
                inputs=[preview_code, selected_char_id, asset_gen_look_context],
                outputs=[char_test_image, char_test_log]
            )

            characters_tab.select(
                fn=_refresh_asset_look_gallery,
                inputs=[preview_code],
                outputs=[char_look_gallery, asset_look_paths_state],
                queue=False,
            ).then(
                fn=_asset_look_ui_parts,
                inputs=[preview_code, asset_gen_look_context, asset_look_paths_state],
                outputs=[char_look_indicator, char_look_details],
                queue=False,
            )

            char_look_gallery.select(
                fn=_on_asset_look_gallery_select,
                inputs=[preview_code, asset_look_paths_state],
                outputs=[asset_gen_look_context, char_look_indicator, char_look_details],
                queue=False,
            )

            char_recall_gen_btn.click(
                fn=_recall_asset_gen_from_reference_image,
                inputs=[char_gallery_path_state, preview_code, asset_look_paths_state],
                outputs=[
                    asset_gen_look_context,
                    char_gen_prompt,
                    char_gen_neg_prompt,
                    char_look_indicator,
                    char_look_details,
                    char_gallery_status,
                ],
                queue=False,
            )

            _wire_asset_reference_library(
                preview_code=preview_code,
                collection_key="characters",
                selected_id_state=selected_char_id,
                reference_gallery=char_ref_gallery,
                gallery_path_state=char_gallery_path_state,
                gallery_delete_btn=char_gallery_delete_btn,
                gallery_status=char_gallery_status,
                reference_image=char_reference_image,
                upload_btn=char_ref_upload_btn,
                save_to_library_btn=char_reference_save_btn,
                test_image=char_test_image,
            )

        # ============================================================
        # LOCATIONS TAB (formerly Settings)
        # ============================================================
        with gr.TabItem("Locations") as settings_tab:
            with gr.Row():
                add_setting_btn = gr.Button("+ Add Location", variant="primary", scale=0)
                setting_selector = gr.Dropdown(
                    label="Location",
                    choices=[],
                    value=None,
                    interactive=True,
                    allow_custom_value=False,
                    filterable=False,
                    scale=3,
                )

            with gr.Group(visible=False, elem_classes=["asset-inspector-shell"]) as setting_inspector:
                with gr.Row(elem_classes=["asset-columns-row"], equal_height=False):
                    with gr.Column(scale=1):
                        with gr.Accordion("Properties", open=True):
                            setting_name = gr.Textbox(
                                label="Location Name",
                                info="Display name for this location or setting",
                            )
                            setting_prompt = gr.Textbox(
                                label="Location Prompt",
                                info="Included in keyframe generation",
                                lines=6,
                            )
                            setting_inject_lora = gr.Dropdown(
                                label="Inject LoRA Tag",
                                info="Add a LoRA tag to the keyframe prompt (dropdown clears after each pick)",
                                choices=[],
                                value=None,
                                interactive=True,
                            )
                            setting_neg_prompt = gr.Textbox(
                                label="Negative Prompt",
                                info="Included in keyframe generation",
                                lines=2,
                            )

                    with gr.Column(scale=1):
                        with gr.Accordion("Generation", open=True):
                            setting_test_image = gr.Image(
                                label="Generated Result",
                                type="filepath",
                                interactive=False,
                                height=256,
                            )
                            test_setting_btn = gr.Button("Generate", variant="primary")
                            setting_reference_save_btn = gr.Button(
                                "Save to this Location", variant="secondary", visible=True
                            )
                            with gr.Group() as setting_gen_prompt_group:
                                setting_gen_prompt = gr.Textbox(
                                    label="Generator Prompt",
                                    info="Session asset generation only; falls back to Location Prompt if empty",
                                    lines=4,
                                )
                                setting_gen_inject_lora = gr.Dropdown(
                                    label="Inject LoRA Tag",
                                    info="Add a LoRA tag to the generator prompt (dropdown clears after each pick)",
                                    choices=[],
                                    value=None,
                                    interactive=True,
                                )
                                setting_gen_neg_prompt = gr.Textbox(
                                    label="Negative Prompt",
                                    info="Session asset generation only; falls back to keyframe Negative Prompt if empty",
                                    lines=2,
                                )
                            with gr.Group(elem_classes=["asset-model-settings-stack"]) as setting_model_settings_group:
                                setting_look_indicator = gr.Markdown(
                                    elem_classes=["info-text", "asset-look-indicator"],
                                )
                                with gr.Accordion("Model Settings", open=False):
                                    setting_look_gallery = gr.Gallery(
                                        show_label=False,
                                        elem_id="setting_look_gallery",
                                        height=160,
                                        object_fit="contain",
                                        allow_preview=False,
                                    )
                                    setting_look_details = gr.Markdown(elem_classes=["info-text", "asset-look-details"])
                        with gr.Accordion("Status", open=False):
                            setting_test_log = gr.Textbox(
                                label="Generation Log",
                                lines=8,
                                interactive=False,
                                autoscroll=True,
                            )

                    with gr.Column(scale=1) as setting_reflib_group:
                        with gr.Accordion(
                            "Reference Library",
                            open=True,
                            elem_classes=["themed-accordion", "proj-theme"],
                        ):
                            setting_ref_gallery = gr.Gallery(
                                label="Reference images",
                                elem_id="setting_reference_gallery",
                                height=200,
                                object_fit="contain",
                                allow_preview=False,
                            )
                            setting_gallery_path_state = gr.State(value=None)
                            with gr.Row():
                                setting_ref_upload_btn = gr.UploadButton(
                                    "Upload image",
                                    file_types=["image"],
                                    file_count="single",
                                )
                                setting_gallery_delete_btn = gr.Button(
                                    "Delete image",
                                    variant="stop",
                                    visible=False,
                                )
                            setting_gallery_status = gr.Markdown("")
                            setting_recall_gen_btn = gr.Button(
                                "Load generation settings from image",
                                variant="secondary",
                            )
                            setting_reference_image = gr.Image(
                                label="Selected reference",
                                type="filepath",
                                interactive=False,
                                height=200,
                            )
                with gr.Accordion("Manage", open=False, elem_classes=["themed-accordion", "stop-theme"]):   
                    delete_setting_btn = gr.Button("Delete Location", variant="stop")

            # [State and event handlers remain the same - lines 1171-1234]
            selected_setting_id = gr.State(value="")
            pending_setting_id = gr.State(value=None)
            
            setting_inject_lora.select(
                fn=_inject_lora_simple,
                inputs=[setting_prompt, setting_inject_lora],
                outputs=[setting_prompt, setting_inject_lora],
                queue=False,
                show_progress="hidden",
            ).then(
                fn=lambda j, i, n, p, np, gp, gn: _update_simple_fields(
                    j, "settings", i, n, p, np, gp, gn
                ),
                inputs=[
                    preview_code,
                    selected_setting_id,
                    setting_name,
                    setting_prompt,
                    setting_neg_prompt,
                    setting_gen_prompt,
                    setting_gen_neg_prompt,
                ],
                outputs=[preview_code, setting_selector],
                queue=False,
                show_progress="hidden"
            )

            setting_gen_inject_lora.select(
                fn=_inject_lora_simple,
                inputs=[setting_gen_prompt, setting_gen_inject_lora],
                outputs=[setting_gen_prompt, setting_gen_inject_lora],
                queue=False,
                show_progress="hidden",
            ).then(
                fn=lambda j, i, n, p, np, gp, gn: _update_simple_fields(
                    j, "settings", i, n, p, np, gp, gn
                ),
                inputs=[
                    preview_code,
                    selected_setting_id,
                    setting_name,
                    setting_prompt,
                    setting_neg_prompt,
                    setting_gen_prompt,
                    setting_gen_neg_prompt,
                ],
                outputs=[preview_code, setting_selector],
                queue=False,
                show_progress="hidden"
            )

            preview_code.change(
                fn=lambda j, c, p: _refresh_simple_list(j, "settings", c, p),
                inputs=[preview_code, selected_setting_id, pending_setting_id],
                outputs=[setting_selector, pending_setting_id], queue=False
            )

            _setting_inspector_outputs = [
                setting_inspector,
                setting_name,
                setting_prompt,
                setting_neg_prompt,
                setting_gen_prompt,
                setting_gen_neg_prompt,
                setting_reference_image,
                setting_ref_gallery,
                setting_gallery_delete_btn,
                setting_gallery_path_state,
                setting_gallery_status,
            ]
            _wire_asset_tab_enter(
                settings_tab,
                refresh_fn=lambda j, c, p: _refresh_simple_list(j, "settings", c, p),
                preview_code=preview_code,
                selector=setting_selector,
                pending_state=pending_setting_id,
                selected_id_state=selected_setting_id,
                collection_key="settings",
                inspector_outputs=_setting_inspector_outputs,
            )

            setting_selector.change(lambda s: s, inputs=[setting_selector], outputs=[selected_setting_id], queue=False).then(
                fn=_make_asset_inspector_load_handler("settings"),
                inputs=[preview_code, selected_setting_id],
                outputs=_setting_inspector_outputs,
                queue=False,
            )

            add_setting_btn.click(
                fn=lambda j: (
                    lambda d: (d, d.get("project", {}).get("settings", [])[-1].get("id") if d.get("project", {}).get("settings") else None)
                )( _add_simple_item(j, ("project", "settings"), "New Location") ),
                inputs=[preview_code], outputs=[preview_code, pending_setting_id]
            )

            delete_setting_btn.click(
                fn=lambda j, i: _delete_simple_item(j, "settings", i), 
                inputs=[preview_code, selected_setting_id], outputs=[preview_code, pending_setting_id]
            )
            
            _setting_fields = [
                setting_name,
                setting_prompt,
                setting_neg_prompt,
                setting_gen_prompt,
                setting_gen_neg_prompt,
            ]
            for f in _setting_fields:
                f.blur(
                    fn=lambda j, i, n, p, np, gp, gn: _update_simple_fields(
                        j, "settings", i, n, p, np, gp, gn
                    ),
                    inputs=[
                        preview_code,
                        selected_setting_id,
                        setting_name,
                        setting_prompt,
                        setting_neg_prompt,
                        setting_gen_prompt,
                        setting_gen_neg_prompt,
                    ],
                    outputs=[preview_code, setting_selector],
                    queue=False,
                )

            test_setting_btn.click(
                fn=handle_setting_test,
                inputs=[preview_code, selected_setting_id, asset_gen_look_context],
                outputs=[setting_test_image, setting_test_log]
            )

            settings_tab.select(
                fn=_refresh_asset_look_gallery,
                inputs=[preview_code],
                outputs=[setting_look_gallery, asset_look_paths_state],
                queue=False,
            ).then(
                fn=_asset_look_ui_parts,
                inputs=[preview_code, asset_gen_look_context, asset_look_paths_state],
                outputs=[setting_look_indicator, setting_look_details],
                queue=False,
            )

            setting_look_gallery.select(
                fn=_on_asset_look_gallery_select,
                inputs=[preview_code, asset_look_paths_state],
                outputs=[asset_gen_look_context, setting_look_indicator, setting_look_details],
                queue=False,
            )

            setting_recall_gen_btn.click(
                fn=_recall_asset_gen_from_reference_image,
                inputs=[setting_gallery_path_state, preview_code, asset_look_paths_state],
                outputs=[
                    asset_gen_look_context,
                    setting_gen_prompt,
                    setting_gen_neg_prompt,
                    setting_look_indicator,
                    setting_look_details,
                    setting_gallery_status,
                ],
                queue=False,
            )

            _wire_asset_reference_library(
                preview_code=preview_code,
                collection_key="settings",
                selected_id_state=selected_setting_id,
                reference_gallery=setting_ref_gallery,
                gallery_path_state=setting_gallery_path_state,
                gallery_delete_btn=setting_gallery_delete_btn,
                gallery_status=setting_gallery_status,
                reference_image=setting_reference_image,
                upload_btn=setting_ref_upload_btn,
                save_to_library_btn=setting_reference_save_btn,
                test_image=setting_test_image,
            )

        # ============================================================
        # STYLES TAB
        # ============================================================
        with gr.TabItem("Styles") as styles_tab:
            with gr.Row():
                add_style_btn = gr.Button("+ Add Style", variant="primary", scale=0)
                style_selector = gr.Dropdown(
                    label="Style",
                    choices=[],
                    value=None,
                    interactive=True,
                    allow_custom_value=False,
                    filterable=False,
                    scale=3,
                )

            with gr.Group(visible=False, elem_classes=["asset-inspector-shell"]) as style_inspector:
                with gr.Row(elem_classes=["asset-columns-row"], equal_height=False):
                    with gr.Column(scale=1):
                        with gr.Accordion("Properties", open=True):
                            style_name = gr.Textbox(
                                label="Style Name",
                                info="Display name for this visual style",
                            )
                            style_prompt = gr.Textbox(
                                label="Camera Style Prompt",
                                info="Included in keyframe generation",
                                lines=6,
                            )
                            style_inject_lora = gr.Dropdown(
                                label="Inject LoRA Tag",
                                info="Add a LoRA tag to the keyframe prompt (dropdown clears after each pick)",
                                choices=[],
                                value=None,
                                interactive=True,
                            )
                            style_neg_prompt = gr.Textbox(
                                label="Negative Prompt",
                                info="Included in keyframe generation",
                                lines=2,
                            )

                    with gr.Column(scale=1):
                        with gr.Accordion("Generation", open=True):
                            style_test_image = gr.Image(
                                label="Generated Result",
                                type="filepath",
                                interactive=False,
                                height=256,
                            )
                            test_style_btn = gr.Button("Generate", variant="primary")
                            style_reference_save_btn = gr.Button(
                                "Save to this Style", variant="secondary", visible=True
                            )
                            with gr.Group() as style_gen_prompt_group:
                                style_gen_prompt = gr.Textbox(
                                    label="Generator Prompt",
                                    info="Session asset generation only; falls back to Camera Style Prompt if empty",
                                    lines=4,
                                )
                                style_gen_inject_lora = gr.Dropdown(
                                    label="Inject LoRA Tag",
                                    info="Add a LoRA tag to the generator prompt (dropdown clears after each pick)",
                                    choices=[],
                                    value=None,
                                    interactive=True,
                                )
                                style_gen_neg_prompt = gr.Textbox(
                                    label="Negative Prompt",
                                    info="Session asset generation only; falls back to keyframe Negative Prompt if empty",
                                    lines=2,
                                )
                            with gr.Group(elem_classes=["asset-model-settings-stack"]) as style_model_settings_group:
                                style_look_indicator = gr.Markdown(
                                    elem_classes=["info-text", "asset-look-indicator"],
                                )
                                with gr.Accordion("Model Settings", open=False):
                                    style_look_gallery = gr.Gallery(
                                        show_label=False,
                                        elem_id="style_look_gallery",
                                        height=160,
                                        object_fit="contain",
                                        allow_preview=False,
                                    )
                                    style_look_details = gr.Markdown(elem_classes=["info-text", "asset-look-details"])
                        with gr.Accordion("Status", open=False):
                            style_test_log = gr.Textbox(
                                label="Generation Log",
                                lines=8,
                                interactive=False,
                                autoscroll=True,
                            )

                    with gr.Column(scale=1) as style_reflib_group:
                        with gr.Accordion(
                            "Reference Library",
                            open=True,
                            elem_classes=["themed-accordion", "proj-theme"],
                        ):
                            style_ref_gallery = gr.Gallery(
                                label="Reference images",
                                elem_id="style_reference_gallery",
                                height=200,
                                object_fit="contain",
                                allow_preview=False,
                            )
                            style_gallery_path_state = gr.State(value=None)
                            with gr.Row():
                                style_ref_upload_btn = gr.UploadButton(
                                    "Upload image",
                                    file_types=["image"],
                                    file_count="single",
                                )
                                style_gallery_delete_btn = gr.Button(
                                    "Delete image",
                                    variant="stop",
                                    visible=False,
                                )
                            style_gallery_status = gr.Markdown("")
                            style_recall_gen_btn = gr.Button(
                                "Load generation settings from image",
                                variant="secondary",
                            )
                            style_reference_image = gr.Image(
                                label="Selected reference",
                                type="filepath",
                                interactive=False,
                                height=200,
                            )
                with gr.Accordion("Manage", open=False, elem_classes=["themed-accordion", "stop-theme"]):  
                    delete_style_btn = gr.Button("Delete Style", variant="stop")

            # [State and event handlers remain the same - lines 1261-1332]
            selected_style_id = gr.State(value="")
            pending_style_id = gr.State(value=None)

            style_inject_lora.select(
                fn=_inject_lora_simple,
                inputs=[style_prompt, style_inject_lora],
                outputs=[style_prompt, style_inject_lora],
                queue=False,
                show_progress="hidden",
            ).then(
                fn=lambda j, i, n, p, np, gp, gn: _update_simple_fields(
                    j, "styles", i, n, p, np, gp, gn
                ),
                inputs=[
                    preview_code,
                    selected_style_id,
                    style_name,
                    style_prompt,
                    style_neg_prompt,
                    style_gen_prompt,
                    style_gen_neg_prompt,
                ],
                outputs=[preview_code, style_selector],
                queue=False,
                show_progress="hidden"
            )

            style_gen_inject_lora.select(
                fn=_inject_lora_simple,
                inputs=[style_gen_prompt, style_gen_inject_lora],
                outputs=[style_gen_prompt, style_gen_inject_lora],
                queue=False,
                show_progress="hidden",
            ).then(
                fn=lambda j, i, n, p, np, gp, gn: _update_simple_fields(
                    j, "styles", i, n, p, np, gp, gn
                ),
                inputs=[
                    preview_code,
                    selected_style_id,
                    style_name,
                    style_prompt,
                    style_neg_prompt,
                    style_gen_prompt,
                    style_gen_neg_prompt,
                ],
                outputs=[preview_code, style_selector],
                queue=False,
                show_progress="hidden"
            )

            preview_code.change(
                fn=lambda j, c, p: _refresh_simple_list(j, "styles", c, p),
                inputs=[preview_code, selected_style_id, pending_style_id],
                outputs=[style_selector, pending_style_id], queue=False
            )

            _style_inspector_outputs = [
                style_inspector,
                style_name,
                style_prompt,
                style_neg_prompt,
                style_gen_prompt,
                style_gen_neg_prompt,
                style_reference_image,
                style_ref_gallery,
                style_gallery_delete_btn,
                style_gallery_path_state,
                style_gallery_status,
            ]
            _wire_asset_tab_enter(
                styles_tab,
                refresh_fn=lambda j, c, p: _refresh_simple_list(j, "styles", c, p),
                preview_code=preview_code,
                selector=style_selector,
                pending_state=pending_style_id,
                selected_id_state=selected_style_id,
                collection_key="styles",
                inspector_outputs=_style_inspector_outputs,
            )

            style_selector.change(lambda s: s, inputs=[style_selector], outputs=[selected_style_id], queue=False).then(
                fn=_make_asset_inspector_load_handler("styles"),
                inputs=[preview_code, selected_style_id],
                outputs=_style_inspector_outputs,
                queue=False,
            )

            add_style_btn.click(
                fn=lambda j: (
                    lambda d: (d, d.get("project", {}).get("styles", [])[-1].get("id") if d.get("project", {}).get("styles") else None)
                )( _add_simple_item(j, ("project", "styles"), "New Style") ),
                inputs=[preview_code], outputs=[preview_code, pending_style_id]
            )
            
            delete_style_btn.click(
                fn=lambda j, i: _delete_simple_item(j, "styles", i), 
                inputs=[preview_code, selected_style_id], outputs=[preview_code, pending_style_id]
            )
            
            _style_fields = [
                style_name,
                style_prompt,
                style_neg_prompt,
                style_gen_prompt,
                style_gen_neg_prompt,
            ]
            for f in _style_fields:
                f.blur(
                    fn=lambda j, i, n, p, np, gp, gn: _update_simple_fields(
                        j, "styles", i, n, p, np, gp, gn
                    ),
                    inputs=[
                        preview_code,
                        selected_style_id,
                        style_name,
                        style_prompt,
                        style_neg_prompt,
                        style_gen_prompt,
                        style_gen_neg_prompt,
                    ],
                    outputs=[preview_code, style_selector],
                    queue=False,
                )

            test_style_btn.click(
                fn=handle_style_asset_test,
                inputs=[preview_code, selected_style_id, asset_gen_look_context],
                outputs=[style_test_image, style_test_log]
            )

            styles_tab.select(
                fn=_refresh_asset_look_gallery,
                inputs=[preview_code],
                outputs=[style_look_gallery, asset_look_paths_state],
                queue=False,
            ).then(
                fn=_asset_look_ui_parts,
                inputs=[preview_code, asset_gen_look_context, asset_look_paths_state],
                outputs=[style_look_indicator, style_look_details],
                queue=False,
            )

            style_look_gallery.select(
                fn=_on_asset_look_gallery_select,
                inputs=[preview_code, asset_look_paths_state],
                outputs=[asset_gen_look_context, style_look_indicator, style_look_details],
                queue=False,
            )

            style_recall_gen_btn.click(
                fn=_recall_asset_gen_from_reference_image,
                inputs=[style_gallery_path_state, preview_code, asset_look_paths_state],
                outputs=[
                    asset_gen_look_context,
                    style_gen_prompt,
                    style_gen_neg_prompt,
                    style_look_indicator,
                    style_look_details,
                    style_gallery_status,
                ],
                queue=False,
            )

            _wire_asset_reference_library(
                preview_code=preview_code,
                collection_key="styles",
                selected_id_state=selected_style_id,
                reference_gallery=style_ref_gallery,
                gallery_path_state=style_gallery_path_state,
                gallery_delete_btn=style_gallery_delete_btn,
                gallery_status=style_gallery_status,
                reference_image=style_reference_image,
                upload_btn=style_ref_upload_btn,
                save_to_library_btn=style_reference_save_btn,
                test_image=style_test_image,
            )

    return (
        pose_gallery,
        poses_dir_state,
        char_inject_lora,
        char_gen_inject_lora,
        setting_inject_lora,
        setting_gen_inject_lora,
        style_inject_lora,
        style_gen_inject_lora,
        char_reference_save_btn,
        setting_reference_save_btn,
        style_reference_save_btn,
        char_gen_prompt_group,
        char_model_settings_group,
        char_reflib_group,
        setting_gen_prompt_group,
        setting_model_settings_group,
        setting_reflib_group,
        style_gen_prompt_group,
        style_model_settings_group,
        style_reflib_group,
    )