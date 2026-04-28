# test_gen_helpers.py
import gradio as gr
import subprocess
import os
import json
import copy
from datetime import datetime
from typing import Dict, Any, Tuple
import random 
from pathlib import Path
from PIL import Image

from helpers import (
    WORKFLOWS_DIR, 
    DEFAULT_KF_USE_ANIMAL_POSE, 
    DEFAULT_KF_CN_SETTINGS, 
    parse_nid, 
    _get_temp_dir, 
    _rows_with_times,
    STYLE_PRESETS,  
    TEST_LAYOUT_PROMPT,   
    TEST_SETTING_PROMPT,
    save_to_project_folder,
    get_node_by_id
)

import sys

SCRIPT_DIRECTORY = str(Path(__file__).parent / "../scripts")
# TEST_CHARACTER_SETTING_PROMPT = "a professionl photo studio, infinity wall, ring lighting, neutral background"
# TEST_CHARACTER_PROMPT = "clear view for the character"
TEST_CHARACTER_SETTING_PROMPT = "a professionl photo studio, infinity wall, ring lighting, neutral background"
TEST_CHARACTER_PROMPT = "clear view for the character"

TEST_SETTING_LAYOUT_PROMPT = "((empty space))"
TEST_SETTING_ANCHOR_PROMPT = "empty environment, no people, no character, no subject"

TEST_STYLE_LAYOUT_PROMPT = "((default scene))"
TEST_STYLE_ANCHOR_PROMPT = "an empty modern interior, no people, no character, no subject"


def list_style_test_options(project_dict: dict):
    """
    Parses project dictionary to build a list of options for the Style Test dropdown.
    Returns a list of (Label, Value) tuples.
    """
    options = []
    
    # 1. Add Presets (Imported from helpers)
    for label in STYLE_PRESETS.keys():
        options.append((f"[Preset] {label}", f"PRESET:{label}"))

    # 2. Add Existing Keyframes (using shared timeline logic)
    try:
        data = project_dict if isinstance(project_dict, dict) else {}
        # _rows_with_times returns [(Label, ID), ...] 
        timeline_rows = _rows_with_times(data)
        
        for label, node_id in timeline_rows:
            # Check type using ID lookup
            node, kind = get_node_by_id(data, node_id)
            if kind == "kf":
                options.append((label, node_id))
                
    except Exception:
        pass

    return options

def recall_project_globals(file_path: str):
    """
    Reads Look metadata from image.
    Tries 'the_machine_snapshot' first (new format), falls back to 'comment' (old format).
    Returns flat dict with 17 look fields for UI compatibility.
    """
    import json
    from pathlib import Path
    from PIL import Image
    try:
        import piexif
    except ImportError:
        piexif = None

    try:
        img = Image.open(file_path)
        ext = Path(file_path).suffix.lower()
        
        # Try new format: the_machine_snapshot (comprehensive metadata)
        if ext == ".png":
            snapshot_str = img.info.get("the_machine_snapshot")
            if snapshot_str:
                snapshot = json.loads(snapshot_str)
                # Extract look fields from nested structure
                pc = snapshot.get("project_context", {})
                gen = snapshot.get("generation", {})

                print(f"[RECALL DEBUG] snapshot keys: {list(snapshot.keys())}")
                print(f"[RECALL DEBUG] project_context: {pc}")
                print(f"[RECALL DEBUG] generation: {gen}")


                negs = pc.get("negatives", {})
                lora = pc.get("lora_normalization", {})
                
                return {
                    "width": pc.get("width"),
                    "height": pc.get("height"),
                    "style_prompt": pc.get("style_prompt"),
                    "model": pc.get("model"),
                    "steps": pc.get("steps"),
                    "cfg": pc.get("cfg"),
                    "sampler": pc.get("sampler"),
                    "scheduler": pc.get("scheduler"),
                    "neg_global": negs.get("global"),
                    "neg_kf": negs.get("keyframes_all"),
                    "neg_i2v": negs.get("inbetween_all"),
                    "neg_heal": negs.get("heal_all"),
                    "lora_normalization.fg_enabled": lora.get("fg_enabled"),
                    "lora_normalization.fg_max": lora.get("fg_max"),
                    "lora_normalization.bg_enabled": lora.get("bg_enabled"),
                    "lora_normalization.bg_max": lora.get("bg_max")
                }, "Success"
        
        # Fallback to old format: comment field (backward compatibility)
        raw_data = None
        if ext == ".png":
            raw_data = img.info.get("comment")
        elif ext in [".jpg", ".jpeg"] and piexif:
            exif_data = piexif.load(img.info.get("exif", b""))
            user_comment = exif_data.get("Exif", {}).get(piexif.ExifIFD.UserComment)
            if user_comment:
                raw_data = user_comment.decode('utf-8')

        if raw_data:
            # Old format is already flat dictionary
            return json.loads(raw_data), "Success (legacy format)"
        
        return None, "No metadata found in image."
    except Exception as e:
        return None, f"Error reading metadata: {e}"
    


def _create_temp_json_for_sequence_batch(full_data: Dict, target_nid: str) -> Tuple[Dict | None, str | None]:
    """
    Creates a minimal version of the project JSON for a single sequence batch job.
    V2: Creates a dictionary-based structure for the isolated sequence.
    """
    if not full_data:
        return None, "Error creating config: Project data is empty or None."

    sequences = full_data.get("sequences", None)

    # Verbose diagnostics (safe, text-only)
    seq_type = type(sequences).__name__
    seq_keys = []
    if isinstance(sequences, dict):
        seq_keys = list(sequences.keys())

    # Verify sequences container
    if not isinstance(sequences, dict):
        return None, (
            "Error creating config: full_data['sequences'] is not a dict.\n"
            f"target_nid={target_nid}\n"
            f"sequences_type={seq_type}\n"
        )

    # Verify ID
    if target_nid not in sequences:
        preview_keys = ", ".join(str(k) for k in seq_keys[:25])
        return None, (
            "Error creating config: target sequence id not found.\n"
            f"target_nid={target_nid}\n"
            f"sequence_count={len(seq_keys)}\n"
            f"sequence_keys_preview={preview_keys}\n"
        )

    # Start with a deep copy
    temp_data = copy.deepcopy(full_data)

    # Isolate the target sequence
    target_seq = temp_data["sequences"][target_nid]
    seq_id = target_seq.get("id")

    if not seq_id:
        return None, (
            "Error creating config: target sequence missing 'id' field.\n"
            f"target_nid={target_nid}\n"
            f"target_seq_keys={list(target_seq.keys())}\n"
        )

    # Prune sequences to only this one
    temp_data["sequences"] = {seq_id: target_seq}

    return temp_data, seq_id





def _create_temp_json_for_sequence_test(full_data: Dict, target_nid: str) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a sequence-level test.
    Returns (temp_data, seq_id, kf_id)
    """
    # Verify ID
    original_seq = full_data.get("sequences", {}).get(target_nid)
    if not original_seq:
        return None, None, None
    
    unique_id = f"id_seq_test_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__test_cache_sequence__"

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    # Get the prompts from the original sequence
    setting_prompt = original_seq.get("setting_prompt", "")
    style_prompt = original_seq.get("style_prompt", "")

    # This test uses no character and an empty layout
    test_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "pose": "",
        "layout": TEST_LAYOUT_PROMPT,
        "template": "",
        "workflow_json": str(Path(WORKFLOWS_DIR) / "pose_OPEN.json"),
        "negatives": {"left":"", "right":"", "heal":""},
        "characters": ["", ""], # No characters
        "selected_image_path": None,
        "use_animal_pose": False,
        "image_iterations_override": 1,
        "force_generate": True
    }

    test_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_prompt": setting_prompt, 
        "style_prompt": style_prompt, 
        "action_prompt": "",
        "video_plan": {"open_start": False, "open_end": True},
        # V2 Structure
        "keyframes": {unique_id: test_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }

    temp_data["sequences"] = {unique_id: test_seq}

    return temp_data, unique_id, unique_id


def _create_temp_json_for_character_test(full_data: Dict, selected_char: Dict, pose_path: str) -> Tuple[Dict | None, str | None, str | None]:

    """
    Creates a minimal V2 project JSON for a character test.
    """
    unique_id = f"id_char_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__test_cache_character__"
    
    # Inherit Global Project Styles
    temp_data["project"]["style_prompt"] = full_data["project"].get("style_prompt", "")
    temp_data["project"]["model"] = full_data["project"].get("model", "")
    
    # Mirror sampler globals
    project_kf_globals = full_data["project"].get("keyframe_generation", {})
    if "keyframe_generation" not in temp_data["project"]:
        temp_data["project"]["keyframe_generation"] = {}
        
    temp_data["project"]["keyframe_generation"].update({
        "steps": project_kf_globals.get("steps", 30),
        "cfg": project_kf_globals.get("cfg", 4.0),
        "sampler_name": project_kf_globals.get("sampler_name", "dpmpp_2m_sde"),
        "scheduler": project_kf_globals.get("scheduler", "karras")
    })

    # --- Pose Logic ---
    pose_path = pose_path or ""
    use_animal_pose = "_ANIMAL" in pose_path

    wf_dir = WORKFLOWS_DIR
    char_name = selected_char.get("name", "character")

    if pose_path: 
        workflow_json = str(wf_dir / "pose_1CHAR.json")
    else:
        workflow_json = str(wf_dir / "pose_OPEN.json")

    if "_2CHAR" in pose_path:
        characters = [char_name, char_name]
    else:
        characters = [char_name, ""]

    # Put *only* the selected character into the temp project
    temp_data["project"]["characters"] = [selected_char]

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    final_layout_prompt = f"(({TEST_CHARACTER_PROMPT}))".strip().strip(",")

    test_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "pose": pose_path,
        "layout": final_layout_prompt,
        "template": "",
        "workflow_json": workflow_json,
        "negatives": {"left":"", "right":"", "heal":""},
        "characters": characters,
        "selected_image_path": None,
        "use_animal_pose": use_animal_pose,
        "controlnet_settings": copy.deepcopy(DEFAULT_KF_CN_SETTINGS),
        "image_iterations_override": 1,
        "force_generate": True
    }


    test_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_prompt": TEST_CHARACTER_SETTING_PROMPT,
        "style_prompt": "", 
        "action_prompt": "",
        "video_plan": {"open_start": False, "open_end": True},
        # V2 Structure
        "keyframes": {unique_id: test_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }

    temp_data["sequences"] = {unique_id: test_seq}

    return temp_data, unique_id, unique_id


def _create_temp_json_for_setting_asset_test(full_data: Dict, selected_setting: Dict) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a setting asset test.
    - Focus on the setting prompt
    - Inherit global project styles / negatives
    - Ignore character/pose/style (empty space)
    """
    unique_id = f"id_setting_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__test_cache_setting__"

    # Inherit Global Project Styles
    temp_data["project"]["style_prompt"] = full_data["project"].get("style_prompt", "")
    temp_data["project"]["model"] = full_data["project"].get("model", "")

    # Mirror sampler globals
    project_kf_globals = full_data["project"].get("keyframe_generation", {})
    if "keyframe_generation" not in temp_data["project"]:
        temp_data["project"]["keyframe_generation"] = {}

    temp_data["project"]["keyframe_generation"].update({
        "steps": project_kf_globals.get("steps", 30),
        "cfg": project_kf_globals.get("cfg", 4.0),
        "sampler_name": project_kf_globals.get("sampler_name", "dpmpp_2m_sde"),
        "scheduler": project_kf_globals.get("scheduler", "karras")
    })

    # Put *only* the selected setting into the temp project
    temp_data["project"]["settings"] = [selected_setting]
    temp_data["project"]["styles"] = []

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    # Force an OPEN workflow (no character / pose)
    workflow_json = str(WORKFLOWS_DIR / "pose_OPEN.json")

    # Resolve prompt from new schema with fallback
    setting_kw = selected_setting.get("lora_keyword", "") or ""
    setting_prompt = selected_setting.get("prompt", "") or selected_setting.get("prompt_modifier", "") or ""
    setting_neg = selected_setting.get("negative_prompt", "") or ""

    final_setting_prompt = "\n".join([p for p in [setting_kw, setting_prompt, TEST_SETTING_ANCHOR_PROMPT] if p]).strip()

    test_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "pose": "",
        "layout": TEST_SETTING_LAYOUT_PROMPT,
        "template": "",
        "workflow_json": workflow_json,
        "negatives": {"left": setting_neg, "right": "", "heal": ""},
        "characters": ["", ""],
        "selected_image_path": None,
        "use_animal_pose": False,
        "controlnet_settings": copy.deepcopy(DEFAULT_KF_CN_SETTINGS),
        "image_iterations_override": 1,
        "force_generate": True
    }

    test_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_prompt": final_setting_prompt,
        "style_prompt": "",
        "action_prompt": "",
        "video_plan": {"open_start": False, "open_end": True},
        "keyframes": {unique_id: test_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }

    temp_data["sequences"] = {unique_id: test_seq}

    return temp_data, unique_id, unique_id

def _create_temp_json_for_style_asset_test(full_data: Dict, selected_style: Dict) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a style asset test.
    - Focus on the style prompt
    - Inherit global project styles / negatives
    - Ignore character/pose/setting
    - Uses a default anchor scene for consistency
    """
    unique_id = f"id_styleasset_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__test_cache_style_asset__"

    # Inherit Global Project Styles
    temp_data["project"]["style_prompt"] = full_data["project"].get("style_prompt", "")
    temp_data["project"]["model"] = full_data["project"].get("model", "")

    # Mirror sampler globals
    project_kf_globals = full_data["project"].get("keyframe_generation", {})
    if "keyframe_generation" not in temp_data["project"]:
        temp_data["project"]["keyframe_generation"] = {}

    temp_data["project"]["keyframe_generation"].update({
        "steps": project_kf_globals.get("steps", 30),
        "cfg": project_kf_globals.get("cfg", 4.0),
        "sampler_name": project_kf_globals.get("sampler_name", "dpmpp_2m_sde"),
        "scheduler": project_kf_globals.get("scheduler", "karras")
    })

    # Put *only* the selected style into the temp project
    temp_data["project"]["styles"] = [selected_style]
    temp_data["project"]["settings"] = []

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    workflow_json = str(WORKFLOWS_DIR / "pose_OPEN.json")

    # Resolve prompt from new schema with fallback
    style_kw = selected_style.get("lora_keyword", "") or ""
    style_prompt = selected_style.get("prompt", "") or selected_style.get("prompt_modifier", "") or ""
    style_neg = selected_style.get("negative_prompt", "") or ""

    final_style_prompt = "\n".join([p for p in [style_kw, style_prompt] if p]).strip()

    test_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "pose": "",
        "layout": TEST_STYLE_LAYOUT_PROMPT,
        "template": "",
        "workflow_json": workflow_json,
        "negatives": {"left": style_neg, "right": "", "heal": ""},
        "characters": ["", ""],
        "selected_image_path": None,
        "use_animal_pose": False,
        "controlnet_settings": copy.deepcopy(DEFAULT_KF_CN_SETTINGS),
        "image_iterations_override": 1,
        "force_generate": True
    }

    test_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_prompt": TEST_STYLE_ANCHOR_PROMPT,
        "style_prompt": final_style_prompt,
        "action_prompt": "",
        "video_plan": {"open_start": False, "open_end": True},
        "keyframes": {unique_id: test_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }

    temp_data["sequences"] = {unique_id: test_seq}

    return temp_data, unique_id, unique_id


def _create_temp_json_for_style_test(full_data: Dict, target_choice: str) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a style test.
    Handles 'PRESET:Name' or direct 'kf_id'.
    """
    unique_id = f"id_style_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    
    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__style_cache__"

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    target_choice = target_choice or "PRESET:Standard Landscape"

    # --- CASE A: PRESET ---
    if target_choice.startswith("PRESET:"):
        preset_key = target_choice.split(":", 1)[1]
        preset = STYLE_PRESETS.get(preset_key)
        if not preset:
            preset = STYLE_PRESETS.get("Standard Landscape", next(iter(STYLE_PRESETS.values())))

        test_kf = {
            "id": unique_id,
            "type": "keyframe",
            "sequence_id": unique_id,
            "pose": "",
            "layout": preset["layout"],
            "template": "",
            "workflow_json": str(Path(WORKFLOWS_DIR) / "pose_OPEN.json"),
            "negatives": {"left":"", "right":"", "heal":""},
            "characters": ["", ""],
            "selected_image_path": None,
            "use_animal_pose": False,
            "image_iterations_override": 1,
            "force_generate": True
        }
        
        test_seq = {
            "id": unique_id,
            "type": "sequence",
            "order": 0,
            "setting_prompt": preset["setting"],
            "style_prompt": "",
            "action_prompt": "",
            "video_plan": {"open_start": False, "open_end": True},
            # V2 Structure
            "keyframes": {unique_id: test_kf},
            "keyframe_order": [unique_id],
            "videos": {},
            "video_order": []
        }
    
    # --- CASE B: KEYFRAME ID (V2) ---
    else:
        # Assume target_choice is a node ID
        node, kind, parent_seq, seq_id = _resolve_context_safe(full_data, target_choice)
        
        if kind != "kf" or not parent_seq:
            return None, None, None

        # Copy the parent sequence to preserve context
        test_seq = copy.deepcopy(parent_seq)
        
        # Isolate the specific keyframe
        original_kf = test_seq["keyframes"][target_choice]
        test_kf = copy.deepcopy(original_kf)
        test_kf["image_iterations_override"] = 1
        test_kf["force_generate"] = True
        
        # Override ID to avoid conflicts if needed, or re-use for pathing?
        # Re-using ID allows finding it easily, but we are in a temp project
        # Let's keep the structure clean:
        
        test_seq["id"] = unique_id
        # Update self-ref
        test_seq["sequence_id"] = unique_id
        test_kf["sequence_id"] = unique_id
        
        # Prune
        test_seq["keyframes"] = {unique_id: test_kf}
        test_seq["keyframe_order"] = [unique_id]
        test_seq["videos"] = {}
        test_seq["video_order"] = []

    temp_data["sequences"] = {unique_id: test_seq}
    
    return temp_data, unique_id, unique_id





def _resolve_context_safe(data, nid):
    """Local helper safely wrapping get_node_by_id logic for partial updates"""
    try:
        # Assuming helpers is imported or duplicated logic needed?
        # We imported get_node_by_id, but that returns (node, type).
        # We need parent.
        node, kind = get_node_by_id(data, nid)
        if not node: return None, None, None, None
        
        if kind == "seq":
            return node, "seq", node, node["id"]
            
        # If KF or Vid, we need parent. In V2, parent ID is in the object.
        seq_id = node.get("sequence_id")
        parent = data.get("sequences", {}).get(seq_id)
        
        return node, kind, parent, seq_id
    except:
        return None, None, None, None

def run_image_generation_task(temp_data: Dict, project_name: str, seq_id: str, kf_id: str):
    """
    Generic helper to run an image generation script via subprocess.
    """
    temp_data_str = json.dumps(temp_data, indent=2, ensure_ascii=False)

    temp_dir = _get_temp_dir(temp_data) or (os.path.dirname(__file__) if os.path.dirname(__file__) else ".")
    unique_suffix = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    temp_project_filename = f"__temp_img_{unique_suffix}.json"
    temp_filepath = os.path.join(temp_dir, temp_project_filename)
    
    main_image_path = None
    openpose_path = None
    shape_path = None
    outline_path = None
    output_log = ""

    try:
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            f.write(temp_data_str)
        
        script_path = os.path.join(SCRIPT_DIRECTORY, "run_images.py")
        command = [sys.executable, "-u", script_path, "--config", temp_filepath]
        
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1
        )
        
        for line in process.stdout:
            output_log += line
            yield {
                "main_image_path": None,
                "openpose_path": None,
                "shape_path": None,
                "outline_path": None,
                "log_output": output_log
            }
        process.wait()

        # Find the output images
        try:
            output_root = temp_data.get("project", {}).get("comfy", {}).get("output_root", "")
            image_dir = Path(output_root) / project_name / seq_id / kf_id

            if image_dir.exists():
                image_files = [str(p) for p in image_dir.glob('*') if p.suffix.lower() in ['.png', '.jpg', '.jpeg']]
                if image_files:
                    
                    def find_latest(suffix_key: str) -> str | None:
                        candidates = [f for f in image_files if suffix_key in Path(f).name]
                        return max(candidates, key=os.path.getmtime) if candidates else None

                    openpose_path = find_latest("openposepreview")
                    shape_path = find_latest("shapepreview")
                    outline_path = find_latest("outlinepreview")
                    
                    # Find the main image (newest, not containing any preview keywords)
                    preview_keywords = {"openposepreview", "shapepreview", "outlinepreview"}
                    main_candidates = [f for f in image_files if not any(kw in Path(f).name for kw in preview_keywords)]
                    if main_candidates:
                        main_image_path = max(main_candidates, key=os.path.getmtime)
                    
                    output_log += f"\n\nSuccess: Found main image."
                else:
                    output_log += f"\n\nError: Script finished, but no image was found in {str(image_dir)}"
            else:
                output_log += f"\n\nError: Script finished, but the output directory was not found: {str(image_dir)}"
        except Exception as e:
            output_log += f"\n\nError finding output image(s): {e}"

    finally:
        try:
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)
        except Exception as e:
            print(f"Warning: Failed to clean up temp file {temp_filepath}: {e}")
        
        yield {
            "main_image_path": main_image_path,
            "openpose_path": openpose_path,
            "shape_path": shape_path,
            "outline_path": outline_path,
            "log_output": output_log
        }


# def run_pose_preview_task(project_data: Dict, image_path: str):
def run_pose_preview_task(project_data: Dict, image_path: str, output_dir: str = None, use_animal_pose: bool = False):
    """
    Generates controlnet preview images (openpose, shape, outline) from an existing image.
    Calls run_images.py with --preview-only flag.
    Yields progress dicts with log_output, openpose_path, shape_path, outline_path.
    """
    temp_dir = _get_temp_dir(project_data) or (os.path.dirname(__file__) if os.path.dirname(__file__) else ".")
    unique_suffix = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    temp_project_filename = f"__temp_preview_{unique_suffix}.json"
    temp_filepath = os.path.join(temp_dir, temp_project_filename)

    # Build minimal keyframe structure with use_animal_pose
    temp_data = copy.deepcopy(project_data) if isinstance(project_data, dict) else {}
    unique_id = f"preview_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    temp_kf = {"id": unique_id, "use_animal_pose": use_animal_pose}
    temp_seq = {"id": unique_id, "keyframes": {unique_id: temp_kf}, "keyframe_order": [unique_id]}
    temp_data["sequences"] = {unique_id: temp_seq}

    openpose_path = None
    shape_path = None
    outline_path = None
    output_log = ""
    
    try:
        # Write minimal project config (just need comfy settings)
        # temp_data_str = json.dumps(project_data, indent=2, ensure_ascii=False)
        temp_data_str = json.dumps(temp_data, indent=2, ensure_ascii=False)
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            f.write(temp_data_str)
        
        script_path = os.path.join(SCRIPT_DIRECTORY, "run_images.py")
        command = [
            sys.executable, "-u", script_path,
            "--config", temp_filepath,
            "--preview-only",
            "--image", image_path
        ]
        
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1
        )
        
        for line in process.stdout:
            output_log += line
            
            # Parse result lines
            if line.startswith("PREVIEW_POSE:"):
                path = line.split(":", 1)[1].strip()
                if path != "NOT_FOUND":
                    openpose_path = path
            elif line.startswith("PREVIEW_SHAPE:"):
                path = line.split(":", 1)[1].strip()
                if path != "NOT_FOUND":
                    shape_path = path
            elif line.startswith("PREVIEW_OUTLINE:"):
                path = line.split(":", 1)[1].strip()
                if path != "NOT_FOUND":
                    outline_path = path
            
            yield {
                "openpose_path": openpose_path,
                "shape_path": shape_path,
                "outline_path": outline_path,
                "log_output": output_log
            }
        
        process.wait()
        
        if openpose_path and shape_path and outline_path:
            output_log += "\n\nSuccess: Controlnet previews extracted."
        else:
            output_log += "\n\nWarning: Some preview images were not found."
    
    finally:
        try:
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)
        except Exception as e:
            print(f"Warning: Failed to clean up temp file {temp_filepath}: {e}")
        
        yield {
            "openpose_path": openpose_path,
            "shape_path": shape_path,
            "outline_path": outline_path,
            "log_output": output_log
        }



def _create_temp_json_for_image_test(full_data: Dict, target_kf_id: str, seed_override: int = None) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a single keyframe test.
    """
    # Resolve context using ID
    node, kind, parent_seq, seq_id = _resolve_context_safe(full_data, target_kf_id)
    if kind != "kf" or not parent_seq:
        return None, None, None
    
    temp_data = copy.deepcopy(full_data)

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        if seed_override is not None:
            temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = seed_override
            temp_data["project"]["keyframe_generation"]["advance_seed_by"] = 0  # No advancement for explicit seed
            print(f"[DEBUG SEED] Using override: {seed_override}")
        else:
            temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)
            print(f"[DEBUG SEED] Using random: {temp_data['project']['keyframe_generation']['sampler_seed_start']}")

    # Isolate target sequence
    target_seq = temp_data["sequences"][seq_id]
    
    # Isolate target keyframe
    target_kf = target_seq["keyframes"][target_kf_id]
    
    # Set flags
    target_kf["image_iterations_override"] = 1
    target_kf["force_generate"] = True
    # target_kf.pop("sampler_seed_start", None) 
    
    # Prune
    target_seq["keyframes"] = {target_kf_id: target_kf}
    target_seq["keyframe_order"] = [target_kf_id]
    target_seq["videos"] = {}
    target_seq["video_order"] = []
    
    # Prune sequences
    temp_data["sequences"] = {seq_id: target_seq}
    
    return temp_data, seq_id, target_kf_id

def handle_style_test(project_dict: dict, path_at_start: str, target_source: str = None):
    """
    The main function for the style test generation button.
    Prepares data and calls the shared generation helper.
    """
    if not isinstance(project_dict, dict):
        yield (None, "Error: No project data found.", gr.update())
        return

    full_data = project_dict
    temp_data, seq_id, kf_id = _create_temp_json_for_style_test(full_data, target_source)

    if not temp_data:
        yield (None, "Error: Could not create test data for style test.", gr.update())
        return

    project_name = temp_data.get("project", {}).get("name", "__test_cache_style__")

    yield (None, "Starting style test generation...", gr.update())

    final_main_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_log = result.get("log_output", "")
        yield (final_main_path, final_log, gr.update())

    # Fallback
    if not final_main_path:
        try:
            output_root = full_data.get("project", {}).get("comfy", {}).get("output_root")
            if output_root:
                tmp_dir = Path(output_root) / "__style_cache__"
                if tmp_dir.exists():
                    files = sorted([p for p in tmp_dir.glob("*") if p.suffix.lower() in [".png", ".jpg", ".webp"]], key=os.path.getmtime, reverse=True)
                    if files:
                        final_main_path = str(files[0])
                        final_log += f"\n\n[System] Preview path inferred from newest temp image: {files[0].name}"
        except Exception as e:
            print(f"Fallback failed: {e}")

    # Show notification
    if final_main_path:
        context_name = target_source if target_source else "project style"
        gr.Info(f"✓ Style preview: {context_name}")
    
    yield (final_main_path, final_log, gr.update())


def save_style_to_project(temp_path: str, project_dict: dict):
    """
    Saves a temp style image to the project's _looks folder.
    Metadata is already embedded by run_images.py - just copy the file.
    """
    from helpers import save_to_project_folder
    if not (temp_path and isinstance(project_dict, dict)):
        return "Error: Missing input."
    
    data = project_dict
    proj_data = data.get("project", {})
    output_root = proj_data.get("comfy", {}).get("output_root")
    project_name = proj_data.get("name")
    
    if not (output_root and project_name):
        return "Error: Project paths invalid."
    
    # Build auto-generated filename from current settings
    keygen = proj_data.get("keyframe_generation", {})
    model_name = Path(proj_data.get("model", "unknown")).stem
    style_prompt = proj_data.get("style_prompt", "")
    prompt_clean = "".join(c if c.isalnum() or c == " " else "" for c in style_prompt[:30]).replace(" ", "_")
    steps = keygen.get("steps", "")
    cfg = keygen.get("cfg", "")
    sampler = keygen.get("sampler_name", "")
    name = f"{model_name}-{steps}-{cfg}-{sampler}-{prompt_clean}"
        
    dest_dir = Path(output_root) / project_name / "_looks"
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy file (metadata already embedded from generation)
    msg, new_path = save_to_project_folder(temp_path, str(dest_dir), name)
    
    if new_path:
        return f"Saved Look: {Path(new_path).name}"
    else:
        return msg  # Error message from save_to_project_folder

def handle_test_generation(project_dict: dict, target_nid: str, path_at_start: str, seed_override: int = None):
    """
    The main function for the keyframe test generation button.
    Prepares data and calls the shared generation helper.
    """
    if not isinstance(project_dict, dict) or not target_nid:
        yield (None, None, "Error: No project data or target selected.", None)
        return

    full_data = project_dict
    temp_data, seq_id, kf_id = _create_temp_json_for_image_test(full_data, target_nid, seed_override=seed_override)

    if not temp_data:
        yield (None, None, f"Error: Could not create test data for target '{target_nid}'.", None)
        return

    project_name = full_data.get("project", {}).get("name", "")
    if not project_name:
        yield (None, None, "Error: Project name is missing from JSON data.", None)
        return

    yield (None, None, "Starting keyframe test generation...", None)

    final_main_path = None
    final_openpose_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_openpose_path = result.get("openpose_path") 
        final_log = result.get("log_output", "")
        
        yield (final_main_path, final_openpose_path, final_log, None)

    yield (
        final_main_path, 
        final_openpose_path, 
        final_log, 
        {"final_json": full_data, "source_path": path_at_start}
    )

def handle_character_test(project_dict: dict, selected_char_id: str, pose_path: str):
    """
    Assets tab: Character test generation.
    Returns (image_path, log_text).
    """
    if not isinstance(project_dict, dict):
        yield (None, "Error: No project data found.")
        return

    if not selected_char_id:
        yield (None, "Error: No character is selected.")
        return

    full_data = project_dict

    chars = full_data.get("project", {}).get("characters", [])
    selected_char = next((c for c in chars if c.get("id") == selected_char_id), None)

    if not selected_char:
        yield (None, f"Error: Could not find character with ID {selected_char_id}.")
        return

    temp_data, seq_id, kf_id = _create_temp_json_for_character_test(full_data, selected_char, pose_path)

    if not temp_data:
        yield (None, "Error: Could not create test data for character test.")
        return

    project_name = temp_data.get("project", {}).get("name", "__test_cache_character__")

    yield (None, f"Starting character test for: {selected_char.get('name', 'Unknown')}")

    final_main_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_log = result.get("log_output", "")
        yield (final_main_path, final_log)

    # Show notification
    if final_main_path:
        char_name = selected_char.get('name', 'Unknown')
        gr.Info(f"✓ Character test: {char_name}")
    
    yield (final_main_path, final_log)


def handle_setting_test(project_dict: dict, selected_setting_id: str):
    """
    Assets tab: Setting test generation.
    Returns (image_path, log_text) like character test.
    """
    if not isinstance(project_dict, dict):
        yield (None, "Error: No project data found.")
        return

    if not selected_setting_id:
        yield (None, "Error: No setting is selected.")
        return

    full_data = project_dict

    settings_list = full_data.get("project", {}).get("settings", [])
    selected_setting = next((s for s in settings_list if s.get("id") == selected_setting_id), None)

    if not selected_setting:
        yield (None, f"Error: Could not find setting with ID {selected_setting_id}.")
        return

    temp_data, seq_id, kf_id = _create_temp_json_for_setting_asset_test(full_data, selected_setting)

    if not temp_data:
        yield (None, "Error: Could not create test data for setting test.")
        return

    project_name = temp_data.get("project", {}).get("name", "__test_cache_setting__")

    yield (None, f"Starting setting test for: {selected_setting.get('name', 'Unknown')}")

    final_main_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_log = result.get("log_output", "")
        yield (final_main_path, final_log)

    # Show notification
    if final_main_path:
        setting_name = selected_setting.get('name', 'Unknown')
        gr.Info(f"✓ Setting test: {setting_name}")
    
    yield (final_main_path, final_log)


def handle_style_asset_test(project_dict: dict, selected_style_id: str):
    """
    Assets tab: Style asset test generation.
    Returns (image_path, log_text) like character test.
    """
    if not isinstance(project_dict, dict):
        yield (None, "Error: No project data found.")
        return

    if not selected_style_id:
        yield (None, "Error: No style is selected.")
        return

    full_data = project_dict

    styles_list = full_data.get("project", {}).get("styles", [])
    selected_style = next((s for s in styles_list if s.get("id") == selected_style_id), None)

    if not selected_style:
        yield (None, f"Error: Could not find style with ID {selected_style_id}.")
        return

    temp_data, seq_id, kf_id = _create_temp_json_for_style_asset_test(full_data, selected_style)

    if not temp_data:
        yield (None, "Error: Could not create test data for style test.")
        return

    project_name = temp_data.get("project", {}).get("name", "__test_cache_style_asset__")

    yield (None, f"Starting style test for: {selected_style.get('name', 'Unknown')}")

    final_main_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_log = result.get("log_output", "")
        yield (final_main_path, final_log)

    # Show notification
    if final_main_path:
        style_name = selected_style.get('name', 'Unknown')
        gr.Info(f"✓ Style test: {style_name}")
    
    yield (final_main_path, final_log)



def get_style_test_images(project_dict: dict):
    """Recursively finds images in the [ProjectName]/_looks folder."""
    try:
        data = project_dict if isinstance(project_dict, dict) else {}
        output_root = data.get("project", {}).get("comfy", {}).get("output_root")
        project_name = data.get("project", {}).get("name")
        
        if not output_root or not project_name:
            return []
        
        # Target the new _styles subdirectory
        target_dir = Path(output_root) / project_name / "_looks"
        
        if not target_dir.exists():
            return []
            
        files = []
        for ext in ["*.png", "*.jpg", "*.webp"]:
            files.extend(target_dir.rglob(ext))
            
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [str(p) for p in files]
        
    except Exception:
        return []