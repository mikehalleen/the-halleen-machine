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
    refresh_pose_components
)

from test_gen_helpers import run_image_generation_task, run_pose_preview_task, handle_character_test, handle_setting_test, handle_style_asset_test

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


def _inject_lora_simple(current_text, lora_path):
    """Injects a LoRA tag into the start of a text field."""
    if not lora_path:
        return gr.update(), gr.update()
    try:
        filename = os.path.basename(lora_path)
        lora_tag = f"__lora:{filename}:1.0__ "
        new_text = lora_tag + (current_text or "")
        return gr.update(value=new_text), gr.update(value=None)
    except Exception:
        return gr.update(), gr.update()    
    


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
    p = Path((base_dir or "").strip())
    value = [] # Default to an empty list
    if not p.is_dir():
        return gr.update(value=value)
    
    try:
        img_exts = {".png", ".jpg", ".jpeg", ".webp"}
        files = sorted(
            [fp.resolve() for fp in p.iterdir() if fp.is_file() and fp.suffix.lower() in img_exts],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        # A gallery's `value` is a list of (filepath, label) tuples.
        # Use the full filename (fp.name) as the label.
        value = [(str(fp), fp.name) for fp in files]
    except Exception:
        pass
    
    return gr.update(value=value)

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

def _create_temp_json_for_pose_gen(pose_prompt: str, full_project_data: Dict, use_animal_pose: bool, char_count_choice: str, pose_mode: str):
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
        
        # Inject Project Generation Params
        src_kf = full_project_data.get("project", {}).get("keyframe_generation", {})
        kf_gen["cfg"] = src_kf.get("cfg", 4.0)
        kf_gen["sampler_name"] = src_kf.get("sampler_name", "dpmpp_2m_sde")
        kf_gen["scheduler"] = src_kf.get("scheduler", "karras")
        # For Project Style, we usually don't force steps unless we want to ensure a baseline
        kf_gen["steps"] = 30 

        char_prompt_modifier = ""
        base_negative = full_project_data.get("project", {}).get("negatives", {}).get("global", "")

    elif pose_mode == "Expressive":
        # Use Enhanced Overrides - model from config via project JSON
        temp_data["project"]["style_prompt"] = POSE_STYLE_ENHANCED
        temp_data["project"]["model"] = full_project_data.get("project", {}).get("pose_model_enhanced", POSE_MODEL_ENHANCED)
        
        # Enhanced Generation Params
        kf_gen["cfg"] = 4.0
        kf_gen["steps"] = 30
        kf_gen["sampler_name"] = "dpmpp_2m_sde"
        kf_gen["scheduler"] = "karras"
        
        # char_prompt_modifier = POSE_GEN_CHARACTER_OVERRIDE_ENHANCED
        char_prompt_modifier = POSE_GEN_CHARACTER_OVERRIDE_ENHANCED

        base_negative = POSE_NEGATIVE_ENHANCED

    else: # "Fast" (Default)
        # Use Fast Overrides - model from config via project JSON
        temp_data["project"]["style_prompt"] = POSE_STYLE_FAST
        temp_data["project"]["model"] = full_project_data.get("project", {}).get("pose_model_fast", POSE_MODEL_FAST)
        kf_gen["steps"] = 30
        kf_gen["sampler_name"] = "dpmpp_2m_sde"
        kf_gen["scheduler"] = "karras"
        
        # Fast Generation Params
        kf_gen["cfg"] = 4.0
        # Inherit Steps/Sampler/Scheduler from project defaults (via deepcopy)
        
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
        
    final_negative = " ".join(filter(None, [base_negative, negative_one, negative_two])).strip()
    # --- End Character Count Logic ---

    # pose_character = {"id": "temp_pose_char_id", "name": "Pose Character", "lora_name": "", "lora_strength": 1.0, "lora_keyword": "", "prompt_modifier": char_prompt_modifier}
    pose_character = {"id": "temp_pose_char_id", "name": "Pose Character", "lora_keyword": "", "prompt": char_prompt_modifier, "negative_prompt": ""}

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
        "negatives": {"global": final_negative} 
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

    return temp_data, unique_id


def handle_auto_generate_with_qc(pose_prompt: str, project_json: str, use_animal_pose: bool, char_count_choice: str, pose_mode: str, max_iterations: int = 10):
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
        gen = handle_pose_generation(pose_prompt, project_json, use_animal_pose, char_count_choice, pose_mode)
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


def handle_pose_generation(pose_prompt: str, project_json: str, use_animal_pose: bool, char_count_choice: str, pose_mode: str):
    """Generates a pose image by preparing data and calling the shared helper."""
    # Yields 10 values: main, pose, shape, outline, log, json, temp_path, state_pose, state_shape, state_outline
    if not pose_prompt:
        yield (None, None, None, None, "Please enter a prompt for the pose.", None, None, None, None, None)
        return

    full_data = project_json
    
    temp_data, unique_id = _create_temp_json_for_pose_gen(pose_prompt, full_data, use_animal_pose, char_count_choice, pose_mode)
    
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
        return gr.update(), gr.update(), gr.update(), gr.update(), "Image not found."
        
    meta = get_png_metadata(image_path)
    if not meta:
         return gr.update(), gr.update(), gr.update(), gr.update(), "No metadata found."

    # 1. Pose Prompt (Layout)
    pose_prompt = meta.get("item_data", {}).get("layout", "")

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

# ---- EVENT HANDLERS (for Characters)----
def _on_asset_selected(pre_txt: str, selected_id: str):
    # data = _loads(pre_txt)
    data = pre_txt if isinstance(pre_txt, dict) else {}
    chars = _get_characters(data)
    char_data = next((c for c in chars if c.get("id") == selected_id), None)
    if char_data:
        prompt_val = char_data.get("prompt", "")
        if not prompt_val:
            prompt_val = char_data.get("prompt_modifier", "")
        return (gr.update(visible=True), char_data.get("name", ""), char_data.get("lora_keyword", ""), prompt_val, char_data.get("negative_prompt", ""))
    else:
        return (gr.update(visible=False), "", "", "", "")
# outputs=[inspector_group, char_name, char_prompt_mod, char_neg_prompt], 


def _refresh_char_list(json_txt: str, current_val: str, pending_id: str | None):
    """Refreshes the character list, prioritizing a pending selection if set."""
    try:
        data = json_txt
        choices = _build_character_choices(data)
        valid_ids = [c[1] for c in choices]
        
        final_val = None
        
        # Priority 1: Pending ID (from Add operation)
        if pending_id and pending_id in valid_ids:
            final_val = pending_id
        # Priority 2: Keep current value if valid
        elif current_val in valid_ids:
            final_val = current_val
        
        # Return update for component AND reset pending state to None
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
        "lora_keyword": "",
        "prompt": "",
        "negative_prompt": ""
    }
    chars.append(new_char)
    return data, new_id


def _delete_character(pre_txt: str, selected_id: str):
    data = pre_txt if isinstance(pre_txt, dict) else {}
    chars = _get_characters(data)
    chars_after_delete = [c for c in chars if c.get("id") != selected_id]
    data["project"]["characters"] = chars_after_delete
    # Output Dict and None to clear pending state
    return data, None

def _update_character_fields(pre_txt: str, selected_id: str, name, lora_keyword, prompt_val, neg_prompt):
    if not selected_id: return pre_txt, gr.update()
    data = pre_txt if isinstance(pre_txt, dict) else {}
    chars = _get_characters(data)
    char_to_update = next((c for c in chars if c.get("id") == selected_id), None)
    if char_to_update:
        old_name = char_to_update.get("name")
        new_name = name.strip()
        char_to_update["name"] = new_name
        char_to_update["lora_keyword"] = lora_keyword
        char_to_update["prompt"] = prompt_val
        char_to_update["negative_prompt"] = neg_prompt
        
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

        final_val = None
        if pending_id and pending_id in valid_ids:
            final_val = pending_id
        elif current_val in valid_ids:
            final_val = current_val

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
        "lora_keyword": "",
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
            gr.update(value=item.get("lora_keyword", "")),
            gr.update(value=item.get("prompt", "")),
            gr.update(value=item.get("negative_prompt", ""))
        )

    return (
        gr.update(visible=False),
        gr.update(value=""),
        gr.update(value=""),
        gr.update(value=""),
        gr.update(value="")
    )




def _update_simple_fields(project_dict, key, item_id, name_val, lora_keyword_val, prompt_val, negative_prompt_val):
    data = project_dict if isinstance(project_dict, dict) else {}
    items = data.get("project", {}).get(key, [])
    if not isinstance(items, list):
        items = []
        data.setdefault("project", {})[key] = items

    for item in items:
        if isinstance(item, dict) and item.get("id") == item_id:
            item["name"] = name_val
            item["lora_keyword"] = lora_keyword_val
            item["prompt"] = prompt_val
            item["negative_prompt"] = negative_prompt_val
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
      #pose_gallery .grid-container {
        grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      }
    </style>
    """)
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

            with gr.Row():
                # ========== LEFT COLUMN: Pose Gallery ==========
                with gr.Column(scale=1):
                    with gr.Accordion("Pose Library", open=True,elem_classes=["themed-accordion", "proj-theme"]):
                        pose_gallery = gr.Gallery(
                            label="Pose Library", 
                            elem_id="pose_gallery", 
                            height=200, 
                            object_fit="contain", allow_preview=False
                        )
                        
                        pose_upload_btn = gr.UploadButton(
                            "Upload Pose", 
                            file_types=["image"], 
                            file_count="single",
                            scale=1
                        )
                        with gr.Row():
                            pose_recall_btn = gr.Button(
                                "Load Properties from Image", 
                                variant="secondary",
                                scale=1
                            )
                            
                            # pose_qc_batch_btn = gr.Button("QC Delete Batch", variant="stop",visible=features.get("show_QC", False))

                            pose_delete_btn = gr.Button(
                                "Delete Pose", 
                                variant="stop", 
                                visible=False
                            )
                        
                        pose_upload_status = gr.Textbox(
                            label="Status", 
                            interactive=False,
                            show_label=False,
                            visible=False
                        )

                # ========== MIDDLE COLUMN: Generate New Pose ==========
                with gr.Column(scale=1):
                    with gr.Accordion("New Pose Properties", open=True):
                        # gr.Markdown("### Properties")
                        
                        pose_gen_prompt = gr.Textbox(
                            label="Pose Prompt", 
                            info="Describe the desired pose and composition",
                            lines=2, 
                            placeholder="e.g., a person standing with arms crossed"
                        )
                        
                        pose_gen_animal = gr.Checkbox(
                            label="Animal", 
                            info="Enable animal-specific controlnet processing",
                            value=False
                        )
                        
                        # Generation Mode
                        pg_choices = ["Simple", "Expressive"]
                        if features.get("show_project_style_pose", True):
                            pg_choices.append("Project Style")
                        
                        pose_gen_mode = gr.Radio(
                            label="Generation Mode", 
                            choices=pg_choices, 
                            value="Simple",
                            info="Simple: clean monochrome base (recommended) • Expressive: detailed but may limit flexibility"
                        )
                        
                        pose_gen_char_count = gr.Radio(
                            label="Character Count", 
                            choices=["1 Character", "2 Characters", "No Limit"], 
                            value="1 Character",
                            info="1 Character: enforces single subject • 2 Characters: balanced layout for two subjects • No Limit: open composition"
                        )
                        
                        pose_gen_btn = gr.Button("Generate Pose", variant="secondary")
                    with gr.Accordion("Status", open=False) as pose_gen_status_acc:
                        pose_gen_status = gr.Textbox(
                            label="Status", 
                            interactive=False, 
                            lines=4
                        )

                # ========== RIGHT COLUMN: Results & Save ==========
                with gr.Column(scale=1):
                    # Generated Result (top)
                    pose_gen_img = gr.Image(
                        label="Generated Result", 
                        interactive=False, 
                        height=256
                    )
                    
                    # Auxiliary Layers (row below)
                    with gr.Row():
                        pose_gen_preview_img = gr.Image(
                            label="Pose", 
                            interactive=False, 
                            height=128
                        )
                        pose_gen_shape_preview_img = gr.Image(
                            label="Shape", 
                            interactive=False, 
                            height=128
                        )
                        pose_gen_outline_preview_img = gr.Image(
                            label="Outline", 
                            interactive=False, 
                            height=128
                        )


                    # QC Controls
                    with gr.Accordion("QC", open=False, visible=features.get("show_QC", False)) as pose_qc_accordion:
                        pose_qc_btn = gr.Button("QC Pose", variant="secondary")
                        pose_qc_max_iter = gr.Number(label="Max Iterations", value=10, precision=0, minimum=1, maximum=50, interactive=True)
                        with gr.Row():
                            pose_auto_qc_btn = gr.Button("Auto Generate with QC", variant="secondary")
                            pose_auto_qc_cancel_btn = gr.Button("Cancel", variant="stop")

                    # Save/Update Controls
                    with gr.Group(visible=False) as pose_edit_group:
                        pose_edit_name = gr.Textbox(
                            label="Pose Name", 
                            info="Name for saving to gallery",
                            interactive=True
                        )
                        with gr.Row():
                            pose_update_btn = gr.Button(
                                "Save / Update Pose", 
                                variant="secondary"
                            )

                    

                    
                    pose_gen_temp_json_preview = gr.Code(
                        language="json", 
                        interactive=False, 
                        visible=False
                    ) 
                    pose_gen_temp_path = gr.State(value=None)

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
                outputs=[pose_gen_prompt, pose_gen_animal, pose_gen_mode, pose_gen_char_count, pose_gen_status]
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
                inputs=[pose_gen_prompt, preview_code, pose_gen_animal, pose_gen_char_count, pose_gen_mode, pose_qc_max_iter],
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
                inputs=[pose_gen_prompt, preview_code, pose_gen_animal, pose_gen_char_count, pose_gen_mode],
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
                # Left: Character List
                with gr.Column(scale=1, min_width=340):
                    add_char_btn = gr.Button("+ Add Character", variant="primary")
                    char_selector = gr.Radio(
                        label="Characters", 
                        choices=[], 
                        value=None, 
                        elem_id="asset_list", 
                        container=False, 
                        interactive=True
                    )
                
                # Right: Character Editor
                with gr.Column(scale=2, min_width=640):
                    with gr.Group(visible=False) as inspector_group:
                        char_name = gr.Textbox(
                            label="Character Name",
                            info="Display name for this character"
                        )
                        
                        char_lora_keyword = gr.Textbox(
                            label="LoRA Keywords",
                            info="Trigger words for character LoRAs (e.g., 'john_character, wearing_suit')",
                            visible=False
                        )
                        
                        char_prompt_mod = gr.Textbox(
                            label="Character Prompt", 
                            info="Physical description and attributes",
                            lines=4, 
                            scale=3
                        )
                        
                        char_inject_lora = gr.Dropdown(
                            label="Inject LoRA Tag", 
                            info="Quick-add a LoRA tag to the prompt above",
                            choices=[], 
                            interactive=True, 
                            scale=1
                        )
                        
                        char_neg_prompt = gr.Textbox(
                            label="Negative Prompt",
                            info="Things to avoid when generating this character",
                            lines=2
                        )

                        # gr.Markdown("### Test Generation")
                        char_test_pose = gr.Dropdown(
                            label="Test Pose", 
                            info="Select a pose to preview this character",
                            choices=[], 
                            interactive=True, 
                            filterable=False
                        )
                        test_char_btn = gr.Button("Generate Test", variant="primary")
                        
                        with gr.Group() as char_test_results_group:
                            char_test_image = gr.Image(
                                label="Test Result", 
                                interactive=False, 
                                height=256
                            )
                            with gr.Accordion("Generation Log", open=False):
                                char_test_log = gr.Textbox(
                                    lines=8, 
                                    interactive=False, 
                                    autoscroll=True
                                )

                        delete_char_btn = gr.Button("Delete Character", variant="stop")

            # [State and event handlers remain the same - lines 1065-1145]
            selected_char_id = gr.State(value="")
            pending_char_selection = gr.State(value=None)

            preview_code.change(
                _refresh_char_list, 
                inputs=[preview_code, char_selector, pending_char_selection], 
                outputs=[char_selector, pending_char_selection], 
                queue=False
            )

            characters_tab.select(
                fn=_refresh_char_list,
                inputs=[preview_code, char_selector, pending_char_selection],
                outputs=[char_selector, pending_char_selection],
                queue=False
            )

            char_selector.change(
                lambda sel: sel, inputs=[char_selector], outputs=[selected_char_id], queue=False
            ).then(
                _on_asset_selected, 
                inputs=[preview_code, selected_char_id], 
                outputs=[inspector_group, char_name, char_lora_keyword, char_prompt_mod, char_neg_prompt], 
                queue=False
            ).then(
                cb_list_pose_files,
                inputs=[poses_dir_state, gr.State(None)],
                outputs=[char_test_pose],
                queue=False,
                show_progress="hidden"
            )
            
            add_char_btn.click(_add_character, inputs=[preview_code], outputs=[preview_code, pending_char_selection])
            delete_char_btn.click(_delete_character, inputs=[preview_code, selected_char_id], outputs=[preview_code, pending_char_selection])

            char_inject_lora.change(
                fn=_inject_lora_simple,
                inputs=[char_prompt_mod, char_inject_lora],
                outputs=[char_prompt_mod, char_inject_lora],
                queue=False,
                show_progress="hidden"
            ).then(
                fn=_update_character_fields,
                inputs=[preview_code, selected_char_id, char_name, char_lora_keyword, char_prompt_mod, char_neg_prompt],
                outputs=[preview_code, char_selector],
                queue=False,
                show_progress="hidden"
            )

            inspector_fields = [char_name, char_lora_keyword, char_prompt_mod, char_neg_prompt]
            text_or_number_fields = [char_name, char_lora_keyword, char_prompt_mod, char_neg_prompt]

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
                inputs=[preview_code, selected_char_id, char_test_pose],
                outputs=[char_test_image, char_test_log]
            )

        # ============================================================
        # LOCATIONS TAB (formerly Settings)
        # ============================================================
        with gr.TabItem("Locations") as settings_tab:
            with gr.Row():
                # Left: Location List
                with gr.Column(scale=1, min_width=340):
                    add_setting_btn = gr.Button("+ Add Location", variant="primary")
                    setting_selector = gr.Radio(
                        label="Locations", 
                        choices=[], 
                        value=None, 
                        container=False, 
                        interactive=True
                    )
                
                # Right: Location Editor
                with gr.Column(scale=2, min_width=640):
                    with gr.Group(visible=False) as setting_inspector:
                        setting_name = gr.Textbox(
                            label="Location Name",
                            info="Display name for this location or setting"
                        )
                        
                        setting_lora_keyword = gr.Textbox(
                            label="LoRA Keywords",
                            info="Trigger words for location/environment LoRAs",
                            visible=False
                        )
                        
                        setting_prompt = gr.Textbox(
                            label="Location Prompt", 
                            info="Describe the environment, architecture, atmosphere, and lighting",
                            lines=6
                        )
                        
                        setting_inject_lora = gr.Dropdown(
                            label="Inject LoRA Tag",
                            info="Quick-add a LoRA tag to the prompt above",
                            choices=[], 
                            interactive=True, 
                            scale=1
                        )
                        
                        setting_neg_prompt = gr.Textbox(
                            label="Negative Prompt",
                            info="Elements to avoid in this location",
                            lines=2
                        )

                        # gr.Markdown("### Test Generation")
                        test_setting_btn = gr.Button("Generate Test", variant="primary")
                        
                        with gr.Group() as setting_test_results_group:
                            setting_test_image = gr.Image(
                                label="Test Result", 
                                interactive=False, 
                                height=256
                            )
                            with gr.Accordion("Generation Log", open=False):
                                setting_test_log = gr.Textbox(
                                    lines=8, 
                                    interactive=False, 
                                    autoscroll=True
                                )

                        delete_setting_btn = gr.Button("Delete Location", variant="stop")

            # [State and event handlers remain the same - lines 1171-1234]
            selected_setting_id = gr.State(value="")
            pending_setting_id = gr.State(value=None)
            
            setting_inject_lora.change(
                fn=_inject_lora_simple,
                inputs=[setting_prompt, setting_inject_lora],
                outputs=[setting_prompt, setting_inject_lora],
                queue=False,
                show_progress="hidden"
            ).then(
                fn=lambda j, i, n, lk, p, np: _update_simple_fields(j, "settings", i, n, lk, p, np),
                inputs=[preview_code, selected_setting_id, setting_name, setting_lora_keyword, setting_prompt, setting_neg_prompt],
                outputs=[preview_code, setting_selector],
                queue=False,
                show_progress="hidden"
            )

            preview_code.change(
                fn=lambda j, c, p: _refresh_simple_list(j, "settings", c, p),
                inputs=[preview_code, setting_selector, pending_setting_id],
                outputs=[setting_selector, pending_setting_id], queue=False
            )

            settings_tab.select(
                fn=lambda j, c, p: _refresh_simple_list(j, "settings", c, p),
                inputs=[preview_code, setting_selector, pending_setting_id],
                outputs=[setting_selector, pending_setting_id],
                queue=False
            )

            setting_selector.change(lambda s: s, inputs=[setting_selector], outputs=[selected_setting_id], queue=False).then(
                fn=lambda j, i: _on_simple_item_selected(j, ("project", "settings"), i),
                inputs=[preview_code, selected_setting_id],
                outputs=[setting_inspector, setting_name, setting_lora_keyword, setting_prompt, setting_neg_prompt], queue=False
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
            
            for f in [setting_name, setting_lora_keyword, setting_prompt, setting_neg_prompt]:
                f.blur(
                    fn=lambda j, i, n, lk, p, np: _update_simple_fields(j, "settings", i, n, lk, p, np),
                    inputs=[preview_code, selected_setting_id, setting_name, setting_lora_keyword, setting_prompt, setting_neg_prompt],
                    outputs=[preview_code, setting_selector], queue=False
                )

            test_setting_btn.click(
                fn=handle_setting_test,
                inputs=[preview_code, selected_setting_id],
                outputs=[setting_test_image, setting_test_log]
            )

        # ============================================================
        # STYLES TAB
        # ============================================================
        with gr.TabItem("Styles") as styles_tab:
            with gr.Row():
                # Left: Style List
                with gr.Column(scale=1, min_width=340):
                    add_style_btn = gr.Button("+ Add Style", variant="primary")
                    style_selector = gr.Radio(
                        label="Styles", 
                        choices=[], 
                        value=None, 
                        container=False, 
                        interactive=True
                    )
                
                # Right: Style Editor
                with gr.Column(scale=2, min_width=640):
                    with gr.Group(visible=False) as style_inspector:
                        style_name = gr.Textbox(
                            label="Style Name",
                            info="Display name for this visual style"
                        )
                        
                        style_lora_keyword = gr.Textbox(
                            label="LoRA Keywords",
                            info="Trigger words for style/aesthetic LoRAs",
                            visible=False
                        )
                        
                        style_prompt = gr.Textbox(
                            label="Camera Style Prompt", 
                            info="Optional modifiers such as specific lenses or movement types",
                            lines=6
                        )
                        
                        style_inject_lora = gr.Dropdown(
                            label="Inject LoRA Tag",
                            info="Quick-add a LoRA tag to the prompt above",
                            choices=[], 
                            interactive=True, 
                            scale=1
                        )
                        
                        style_neg_prompt = gr.Textbox(
                            label="Negative Prompt",
                            info="Visual elements or styles to avoid",
                            lines=2
                        )

                        # gr.Markdown("### Test Generation")
                        test_style_btn = gr.Button("Generate Test", variant="primary")
                        
                        with gr.Group() as style_test_results_group:
                            style_test_image = gr.Image(
                                label="Test Result", 
                                interactive=False, 
                                height=256
                            )
                            with gr.Accordion("Generation Log", open=False):
                                style_test_log = gr.Textbox(
                                    lines=8, 
                                    interactive=False, 
                                    autoscroll=True
                                )

                        delete_style_btn = gr.Button("Delete Style", variant="stop")

            # [State and event handlers remain the same - lines 1261-1332]
            selected_style_id = gr.State(value="")
            pending_style_id = gr.State(value=None)

            style_inject_lora.change(
                fn=_inject_lora_simple,
                inputs=[style_prompt, style_inject_lora],
                outputs=[style_prompt, style_inject_lora],
                queue=False,
                show_progress="hidden"
            ).then(
                fn=lambda j, i, n, lk, p, np: _update_simple_fields(j, "styles", i, n, lk, p, np),
                inputs=[preview_code, selected_style_id, style_name, style_lora_keyword, style_prompt, style_neg_prompt],
                outputs=[preview_code, style_selector],
                queue=False,
                show_progress="hidden"
            )

            preview_code.change(
                fn=lambda j, c, p: _refresh_simple_list(j, "styles", c, p),
                inputs=[preview_code, style_selector, pending_style_id],
                outputs=[style_selector, pending_style_id], queue=False
            )

            styles_tab.select(
                fn=lambda j, c, p: _refresh_simple_list(j, "styles", c, p),
                inputs=[preview_code, style_selector, pending_style_id],
                outputs=[style_selector, pending_style_id],
                queue=False
            )

            style_selector.change(lambda s: s, inputs=[style_selector], outputs=[selected_style_id], queue=False).then(
                fn=lambda j, i: _on_simple_item_selected(j, ("project", "styles"), i),
                inputs=[preview_code, selected_style_id],
                outputs=[style_inspector, style_name, style_lora_keyword, style_prompt, style_neg_prompt], queue=False
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
            
            for f in [style_name, style_lora_keyword, style_prompt, style_neg_prompt]:
                f.blur(
                    fn=lambda j, i, n, lk, p, np: _update_simple_fields(j, "styles", i, n, lk, p, np),
                    inputs=[preview_code, selected_style_id, style_name, style_lora_keyword, style_prompt, style_neg_prompt],
                    outputs=[preview_code, style_selector], queue=False
                )

            test_style_btn.click(
                fn=handle_style_asset_test,
                inputs=[preview_code, selected_style_id],
                outputs=[style_test_image, style_test_log]
            )

    # return pose_gallery, poses_dir_state, char_inject_lora, setting_inject_lora, style_inject_lora
    return pose_gallery, poses_dir_state, char_inject_lora, setting_inject_lora, style_inject_lora