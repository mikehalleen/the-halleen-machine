# helpers.py
from __future__ import annotations
import json, os, tempfile, shutil, re, uuid, sys
from pathlib import Path
from datetime import datetime
import gradio as gr
from typing import Any, Dict, List, Tuple, Optional
from PIL import Image

# print("🔄 helpers.py loaded/reloaded")

# TOML support for configuration files
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        print("WARNING: tomli not installed. Install with: pip install tomli")
        print("   Falling back to JSON-based configuration.")
        tomllib = None


APP_TITLE = "The Halleen Machine"
DEFAULT_KF_USE_ANIMAL_POSE = False
DEFAULT_KF_CN_SETTINGS = {
    # Pose defaults
    "1": {"switch": "On", "strength": 0.9, "start_percent": 0.0, "end_percent": 0.9},
    # Shape defaults
    "2": {"switch": "On", "strength": 0.5, "start_percent": 0.0, "end_percent": 0.5},
    # Outline defaults
    "3": {"switch": "ON", "strength": 0.5, "start_percent": 0.0, "end_percent": 0.5}
}

# Defaults for Test Generation if not specified in Project
TEST_LAYOUT_PROMPT = "((neutral pose))"
TEST_SETTING_PROMPT = "simple background"

# ---- Paths / Settings ----
APP_ROOT = Path(__file__).parent
PROJECT_ROOT = Path(__file__).parent.parent
WORKSPACE_DIR = APP_ROOT
SETTINGS_PATH = WORKSPACE_DIR / "workspace_settings.json"
# WORKFLOWS_DIR = APP_ROOT / "workflows"
# Initialize from config.toml if available, otherwise use default
WORKFLOWS_DIR = (PROJECT_ROOT / "workflows").resolve()

DUR_CHOICES = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]

# Throttling state for backups
LAST_BACKUP_TIME = 0.0

# --- STYLE PRESETS ---
STYLE_PRESETS = {
    "Outdoor Portrait": {
        "setting": "a lush green forest valley between giant rocky and snow tipped mountains, in the center is a creek flowing towards the viewer.  ", 
        "layout": "((a close up of a smiling face of a (man) wearing a tshirt.  his head, shoulders, upper arms, torso. clothed, modest))"
    },
    "Indoor Portrait": {
        "setting": "slender steel columns with a matte finish punctuate the open floor plan, supporting a ceiling grid of dark acoustic baffles and exposed silver ductwork.",
        "layout": "((a close up of a smiling face of a (man) wearing a tshirt.  his head, shoulders, upper arms, torso. clothed, modest))"
    },
    "Outdoor Sphere": {
        "setting": "a lush green forest valley between giant rocky and snow tipped mountains, in the center is a creek flowing towards the viewer.  ",
        "layout": "((a giant chrome sphere hovers above the ground, relfecting the environment around it))"
    },
    "Indoor Sphere": {
        "setting": "slender steel columns with a matte finish punctuate the open floor plan, supporting a ceiling grid of dark acoustic baffles and exposed silver ductwork.",
        "layout": "((a giant chrome sphere hovers above the ground, relfecting the environment around it))"
    }
}

# ---- Defaults ----
DEFAULT_SETTINGS = {
    "comfy": {
        "api_base": "http://127.0.0.1:8188",
        "timeout_seconds": 3600,
        "output_root": ""  # Empty = must be configured in config.toml
    },
    "workspace_root": "./samples",
    "models_root": "",  # Empty = must be configured in config.toml
    "loras_root": "",  # Empty = must be configured in config.toml
    "comfyui_restart_script_path": "",
    "backups": {
        "retention_count": 10,
        "throttle_seconds": 60
    },
    "features": {
        "show_bridges": False,
        "show_cascade_batches": False,
        "show_delete_others": False,
        "show_project_style_pose": False,
    }
}



DEFAULT_PROJECT = {
    "project": {
        "name": "",
        "characters": [],
        "settings": [],
        "styles": [],
        "model": "",
        "controlnet_model": "", 
        "inpainting_model": "", 
        "upscale_model": "",
        "interpolation_model": "",
        "width": 1152,
        "height": 768,
        "is_protected_from_empty_save": False,
        "style_prompt": "",
        "negatives": {
            "global": "(((nsfw))),(((nude)))",
            "keyframes_all": "",
            "inbetween_all": "",
            "heal_all": "",
        },
        "comfy": {
            "api_base": "",
            "timeout_seconds": 3600,
            "output_root": "D:/ComfyUI/output",
        },
        "keyframe_generation": {
            "image_iterations_default": 1,
            "sampler_seed_start": 0,
            "advance_seed_by": 1,
            "cfg": 4.0,
            "sampler_name": "dpmpp_2m_sde",
            "scheduler": "karras"
        },
        "inbetween_generation": {
            "video_workflow_json": str(WORKFLOWS_DIR / "i2v_base.json"),
            "video_iterations_default": 1,
            "duration_default_sec": 2,
            "prompt_template": "",
            "seed_start": 0,
            "advance_seed_by": 1,
            "seed_target_title": "IterKSampler",
            "seed_exclude_title": "WanFixedSeed",
            "express_video": False,
            "quarter_size_video": True,
            "lora_normalization_enabled": True,
            "lora_normalization_max": 1.5
        },
        "lora_normalization": {
            "fg_enabled": True,
            "fg_max": 1.5,
            "bg_enabled": True,
            "bg_max": 1.5
        },
    },
"sequences": {}, # Now a dict: id -> seq_obj
}

def load_config() -> dict:
    """
    Load configuration from config.toml, with fallback to defaults.
    
    Priority:
    1. config.toml (user's installation settings)
    2. DEFAULT_SETTINGS (hardcoded fallback)
    
    Returns dict matching DEFAULT_SETTINGS structure.
    """
    config_path = Path("config.toml")
    
    # Try loading config.toml
    if config_path.exists() and tomllib is not None:
        try:
            with open(config_path, "rb") as f:
                toml_data = tomllib.load(f)
            
            # Convert TOML structure to app's expected format
            config = {
                "comfy": {
                    "api_base": toml_data.get("comfyui", {}).get("api_base", DEFAULT_SETTINGS["comfy"]["api_base"]),
                    "timeout_seconds": toml_data.get("comfyui", {}).get("timeout_seconds", DEFAULT_SETTINGS["comfy"]["timeout_seconds"]),
                    "output_root": toml_data.get("comfyui", {}).get("output_root", DEFAULT_SETTINGS["comfy"]["output_root"]),
                },
                "workspace_root": toml_data.get("paths", {}).get("workspace", DEFAULT_SETTINGS["workspace_root"]),
                "models_root": toml_data.get("paths", {}).get("models", DEFAULT_SETTINGS["models_root"]),
                "loras_root": toml_data.get("paths", {}).get("loras", DEFAULT_SETTINGS["loras_root"]),
                "comfyui_restart_script_path": toml_data.get("advanced", {}).get("comfyui_restart_script", DEFAULT_SETTINGS["comfyui_restart_script_path"]),
                "models": toml_data.get("models", {}), # Add this line to pass the raw models section through
                "backups": {
                    "retention_count": toml_data.get("backups", {}).get("retention", DEFAULT_SETTINGS["backups"]["retention_count"]),
                    "throttle_seconds": toml_data.get("backups", {}).get("throttle_seconds", DEFAULT_SETTINGS["backups"]["throttle_seconds"]),
                },
                "features": toml_data.get("features", DEFAULT_SETTINGS["features"]),
            }
            
            print("Loaded configuration from config.toml")
            
            # Validate critical paths and warn if empty
            if not config["models_root"]:
                print("⚠️  WARNING: models_root not configured!")
                print("   Edit config.toml and set [paths] models = \"your/path/here\"")
            
            if not config["comfy"]["output_root"]:
                print("⚠️  WARNING: output_root not configured!")
                print("   Edit config.toml and set [comfyui] output_root = \"your/path/here\"")
            
            return config
            
        except Exception as e:
            print(f"⚠️  Error loading config.toml: {e}")
            print("   Falling back to defaults...")
    
    elif not config_path.exists():
        print("⚠️  config.toml not found!")
        print("   Copy config.toml.example to config.toml and configure your paths.")
        print("   Running with default settings (may not work correctly)...")
    
    # Fallback to defaults
    return _deep_copy(DEFAULT_SETTINGS)

def _deep_copy(obj): return json.loads(json.dumps(obj))
# ---- MIGRATION & DATA MODEL LOGIC ----

def migrate_project_v2(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Migrates project data to the ID-centric V2 schema.
    - Converts list-based 'sequences' to ID-keyed dict.
    - Creates 'sequence_order' list to preserve ordering.
    - Explicitly creates 'videos' for all gaps.
    - Assigns explicit 'id', 'type'.
    """
    proj = data.get("project", {})
    raw_seqs = data.get("sequences")

    # If sequences is already a dict, assume it's already V2 or close to it
    if isinstance(raw_seqs, dict):
        # Ensure all V2 invariants
        for seq_id, seq in raw_seqs.items():
            _ensure_seq_v2(seq, seq_id)
        return data

    # Migration from List (V1) to Dict (V2)
    new_seqs_dict = {}
    new_seq_order = []  # Build sequence_order during migration
    
    if isinstance(raw_seqs, list):
        for idx, seq in enumerate(raw_seqs):
            # Ensure basic ID
            seq_id = seq.get("id") or f"seq{idx + 1}"
            seq["id"] = seq_id
            # Note: sequence_id (self-ref) and order (integer) no longer set
            
            new_seq_order.append(seq_id)  # Track order
            
            # Migrate Keyframes
            old_kfs = seq.get("i2v_base_images", {})
            new_kfs = {}
            kf_order = []
            
            # Old KFs were dict keys, insertion order matters
            for kf_id, kf_data in old_kfs.items():
                kf_data["id"] = kf_id
                kf_data["sequence_id"] = seq_id
                kf_data["type"] = "keyframe"
                new_kfs[kf_id] = kf_data
                kf_order.append(kf_id)
            
            seq["keyframes"] = new_kfs
            seq["keyframe_order"] = kf_order
            
            # Migrate/Derive Videos
            # We must reconstruct the video chain based on the KF order
            old_vids = seq.get("i2v_videos", {})
            new_vids = {}
            vid_order = []
            
            default_dur = float(proj.get("inbetween_generation", {}).get("duration_default_sec", 3.0))
            
            # 1. Determine gaps
            open_start = bool(seq.get("video_plan", {}).get("open_start", False))
            open_end = bool(seq.get("video_plan", {}).get("open_end", True))
            
            required_gaps = []
            if not kf_order:
                if open_start and open_end: required_gaps.append(("open", "open"))
            else:
                if open_start: required_gaps.append(("open", kf_order[0]))
                for i in range(len(kf_order) - 1):
                    required_gaps.append((kf_order[i], kf_order[i+1]))
                if open_end: required_gaps.append((kf_order[-1], "open"))
            
            # 2. Find or Create Video Objects
            used_old_ids = set()
            
            for start_id, end_id in required_gaps:
                # Find matching old video
                found_vid_id = None
                found_vid_data = None
                
                for vk, v in old_vids.items():
                    if vk in used_old_ids: continue
                    v_start = v.get("start_id") or "open"
                    v_end = v.get("end_id") or "open"
                    
                    if v_start == (start_id if start_id != "open" else "open") and \
                       v_end == (end_id if end_id != "open" else "open"):
                        found_vid_id = vk
                        found_vid_data = v
                        used_old_ids.add(vk)
                        break
                
                if found_vid_data:
                    vid_id = found_vid_id
                    v_obj = found_vid_data
                else:
                    # Create new unique ID
                    base_vid_id = "vid0"
                    count = 0
                    while f"vid{count}" in new_vids or f"vid{count}" in old_vids:
                        count += 1
                    vid_id = f"vid{count}"
                    
                    v_obj = {
                        "inbetween_prompt": "",
                        "negative_prompt": "",
                        "selected_video_path": None
                    }

                # Normalize Video Object
                v_obj["id"] = vid_id
                v_obj["sequence_id"] = seq_id
                v_obj["type"] = "video"
                v_obj["start_keyframe_id"] = None if start_id == "open" else start_id
                v_obj["end_keyframe_id"] = None if end_id == "open" else end_id
                
                # Cleanup old keys
                v_obj.pop("start_id", None)
                v_obj.pop("end_id", None)
                
                new_vids[vid_id] = v_obj
                vid_order.append(vid_id)

            seq["videos"] = new_vids
            seq["video_order"] = vid_order
            
            # Remove old containers
            seq.pop("i2v_base_images", None)
            seq.pop("i2v_videos", None)
            
            new_seqs_dict[seq_id] = seq

    data["sequences"] = new_seqs_dict
    if new_seq_order:  # Only set if we actually migrated from list
        data["sequence_order"] = new_seq_order
    return data

def _ensure_seq_v2(seq: Dict[str, Any], seq_id: str):
    """Ensures a sequence dict has all required V2 fields."""
    seq["id"] = seq_id
    seq.setdefault("type", "sequence")
    # Note: 'order' removed - sequence order is now determined by position in data["sequence_order"]
    seq.setdefault("keyframes", {})
    seq.setdefault("keyframe_order", [])
    seq.setdefault("videos", {})
    seq.setdefault("video_order", [])
    seq.setdefault("setting_prompt", "")
    seq.setdefault("style_prompt", "")
    seq.setdefault("action_prompt", "")
    seq.setdefault("video_plan", {"open_start": False, "open_end": True})

def _ensure_project(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict): data = {}
    
    # 1. Normalize Project Globals
    proj = data.setdefault("project", {})
    proj.setdefault("name", DEFAULT_PROJECT["project"]["name"])
    proj.setdefault("width", DEFAULT_PROJECT["project"]["width"])
    proj.setdefault("height", DEFAULT_PROJECT["project"]["height"])
    if not isinstance(proj.get("characters"), list): proj["characters"] = []
    if not isinstance(proj.get("settings"), list): proj["settings"] = []
    if not isinstance(proj.get("styles"), list): proj["styles"] = []

    comfy = proj.setdefault("comfy", {})
    comfy.setdefault("api_base", "")
    comfy.setdefault("timeout_seconds", 3600)
    comfy.setdefault("output_root", "D:/ComfyUI/output")

    ib = proj.setdefault("inbetween_generation", {})
    ib.setdefault("duration_default_sec", 3.0)

    # 2. Migrate Structure to V2
    data = migrate_project_v2(data)
    
    # 3. Ensure sequence_order exists (migrate from integer order if needed)
    data = _ensure_sequence_order(data)
    data = _validate_and_repair_project(data)
    return data

def _validate_and_repair_project(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates project data integrity and repairs common issues:
    - Duplicate keyframe IDs across sequences
    - Duplicate video IDs across sequences  
    - Orphaned entries in keyframe_order/video_order
    """
    seqs = data.get("sequences", {})
    seen_kf_ids: Dict[str, str] = {}
    seen_vid_ids: Dict[str, str] = {}
    repairs_made = []
    
    for seq_id, seq in seqs.items():
        kf_order = seq.get("keyframe_order", [])
        keyframes = seq.get("keyframes", {})
        vid_order = seq.get("video_order", [])
        videos = seq.get("videos", {})
        
        # Remove orphaned keyframe_order entries
        valid_kf_order = [kf_id for kf_id in kf_order if kf_id in keyframes]
        if len(valid_kf_order) != len(kf_order):
            repairs_made.append(f"Removed orphaned keyframe_order entries in {seq_id}")
            seq["keyframe_order"] = valid_kf_order
            kf_order = valid_kf_order
        
        # Fix duplicate keyframe IDs
        for kf_id in list(keyframes.keys()):
            if kf_id in seen_kf_ids:
                max_num = max((int(k[2:]) for s in seqs.values() for k in s.get("keyframes", {}).keys() if k.startswith("id") and k[2:].isdigit()), default=0)
                max_num = max(max_num, max((int(k[2:]) for k in seen_kf_ids.keys() if k.startswith("id") and k[2:].isdigit()), default=0))
                new_id = f"id{max_num + 1}"
                
                kf_obj = keyframes.pop(kf_id)
                kf_obj["id"] = new_id
                keyframes[new_id] = kf_obj
                if kf_id in kf_order:
                    kf_order[kf_order.index(kf_id)] = new_id
                seen_kf_ids[new_id] = seq_id
                repairs_made.append(f"Renamed duplicate keyframe {kf_id} to {new_id}")
            else:
                seen_kf_ids[kf_id] = seq_id
        
        # Remove orphaned video_order entries
        valid_vid_order = [vid_id for vid_id in vid_order if vid_id in videos]
        if len(valid_vid_order) != len(vid_order):
            repairs_made.append(f"Removed orphaned video_order entries in {seq_id}")
            seq["video_order"] = valid_vid_order
    
    if repairs_made:
        print(f"[DATA VALIDATION] Repairs made: {repairs_made}")
    
    return data

def _ensure_sequence_order(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensures sequence_order list exists at data root level.
    Migrates from integer 'order' field if sequence_order doesn't exist.
    Also removes redundant fields: 'sequence_id' on sequences, 'order' integer.
    """
    seqs = data.get("sequences", {})
    
    # Create sequence_order if missing
    if "sequence_order" not in data:
        # Build from existing integer order values
        seq_list = sorted(seqs.values(), key=lambda s: s.get("order", 0))
        data["sequence_order"] = [s["id"] for s in seq_list]
    
    # Validate: ensure all sequences are in sequence_order and vice versa
    seq_order = data["sequence_order"]
    seq_ids = set(seqs.keys())
    order_ids = set(seq_order)
    
    # Add any missing sequences to end
    for sid in seq_ids - order_ids:
        seq_order.append(sid)
    
    # Remove any orphaned entries
    data["sequence_order"] = [sid for sid in seq_order if sid in seq_ids]
    
    # Clean up redundant fields from sequences
    for seq_id, seq in seqs.items():
        seq.pop("sequence_id", None)  # Remove self-reference nonsense
        seq.pop("order", None)        # Order now determined by sequence_order position
    
    return data

def _ensure_seq_defaults(seq: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """Deprecated compatibility wrapper. In V2, use _ensure_seq_v2."""
    if "id" not in seq: seq["id"] = f"seq{idx+1}"
    _ensure_seq_v2(seq, seq["id"])
    return seq

def _derive_videos_for_seq(seq: Dict[str, Any], default_dur: float) -> Dict[str, Any]:
    """
    Deprecated compatibility wrapper. 
    In V2, videos are explicit. This returns a dict of {vid_id: vid_obj} 
    mapped to the old expected format for legacy helpers if needed.
    """
    # Simply return the explicit videos dict
    return seq.get("videos", {})

# ---- TRAVERSAL & UI HELPERS ----

def get_node_by_id(data: Dict[str, Any], node_id: str) -> Tuple[Dict[str, Any] | None, str | None]:
    """
    Locates any node (Sequence, Keyframe, Video) by its ID.
    Returns (node_dict, type_string).
    """
    # 1. Check Sequences
    seqs = data.get("sequences", {})
    if node_id in seqs:
        return seqs[node_id], "seq"
        
    # 2. Check Children
    for seq in seqs.values():
        if node_id in seq.get("keyframes", {}):
            return seq["keyframes"][node_id], "kf"
        if node_id in seq.get("videos", {}):
            return seq["videos"][node_id], "vid"
            
    return None, None

def _get_setting_name_from_id(data: Dict[str, Any], asset_id: str) -> str:
    if not asset_id: return ""
    settings_list = data.get("project", {}).get("settings", [])
    for asset in settings_list:
        if asset.get("id") == asset_id:
            return asset.get("name", "")
    return ""

def _get_char_name_from_id_v2(data: Dict[str, Any], char_id: str) -> str:
    # (Same as before but cleaner access)
    chars = data.get("project", {}).get("characters", [])
    for c in chars:
        if c.get("id") == char_id:
            return c.get("name", "")
    return char_id

def _rows_with_times(data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Generates the UI list for the sidebar.
    Returns [(Label, ID_String), ...]
    Implements cumulative timeline and Ready/Pending iconography.
    """
    data = _ensure_project(data)
    rows: List[Tuple[str, str]] = []
    default_dur = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))

    seqs_dict = data.get("sequences", {})
    seq_order = data.get("sequence_order", [])
    
    # Iterate in sequence_order 
    sorted_seqs = [seqs_dict[sid] for sid in seq_order if sid in seqs_dict]
    
    current_time = 0.0
    for seq in sorted_seqs:
        seq_id = seq.get("id")
        vids_in_seq = seq.get("videos", {})
        vid_order = seq.get("video_order", [])
        
        # Calculate totals and "Export-Ready" status
        seq_total_dur = 0.0
        all_vids_rendered = len(vid_order) > 0 
        
        for v_id in vid_order:
            v = vids_in_seq.get(v_id, {})
            seq_total_dur += _video_seconds(v, default_dur)
            if not v.get("selected_video_path"):
                all_vids_rendered = False

        seq_icon = "◆" if all_vids_rendered else "◇"
        
        # Lookup the display Name using the Asset ID found in the sequence
        asset_id = seq.get("setting_id", "")
        setting_label = _get_setting_name_from_id(data, asset_id)
        # print("[SETTING LABEL] ", setting_label, asset_id)

        # Fallback to manual prompt if no asset is selected or name is empty
        if not setting_label:
            setting_label = seq.get("setting_prompt", "").strip()
        
        seq_time = _fmt_clock(current_time)
        
        # Seq Label: [Icon] [Start Time] | [Asset Name or Prompt]
        seq_label_text = f"{seq_icon} {seq_time} {seq_id} "
        if setting_label:
            seq_label_text += f"| {setting_label[:40]}"
            
        rows.append((seq_label_text, seq_id))
        

        videos_by_start = {}
        for vid_id in vid_order:
            vid = vids_in_seq.get(vid_id)
            if vid:
                s_id = vid.get("start_keyframe_id")
                key = s_id if s_id else "open"
                videos_by_start[key] = vid

        def add_vid_row(v_obj):
            nonlocal current_time
            v_dur = _video_seconds(v_obj, default_dur)
            v_time = _fmt_clock(current_time)
            v_prompt = v_obj.get("inbetween_prompt", "").strip()
            v_icon = "▶" if v_obj.get("selected_video_path") else "▷"
            
            # v_label = f"{v_icon} {v_time} {v_obj.get("vid")}"
            v_label = f"{v_icon} {v_time} {v_obj.get('id')}"
            if v_prompt:
                v_label += f" | {v_prompt[:40]}"
            
            rows.append((v_label, v_obj["id"]))
            current_time += v_dur

        if "open" in videos_by_start:
            add_vid_row(videos_by_start["open"])

        for kf_id in seq.get("keyframe_order", []):
            kf = seq.get("keyframes", {}).get(kf_id)
            if not kf: continue
            
            kf_icon = "▣" if kf.get("selected_image_path") else "□"
            kf_time = _fmt_clock(current_time)
            
            parts = []
            chars = kf.get("characters", [])
            if isinstance(chars, list):
                names = [_get_char_name_from_id_v2(data, c) for c in chars if c]
                if names: parts.append(", ".join(names))
            
            layout = kf.get("layout", "").strip()
            if layout: 
                parts.append(layout)
            elif kf.get("selected_image_path"):
                parts.append(Path(kf["selected_image_path"]).name)
            
            kf_text = " | ".join(parts) if parts else "Empty Keyframe"
            
            # Format: [Icon] [Current Time] | [Prompt Content]
            rows.append((f"{kf_icon} {kf_time} {kf_id} | {kf_text}", kf_id))
            
            if kf_id in videos_by_start:
                add_vid_row(videos_by_start[kf_id])

    return rows


def parse_nid(nid: str) -> Tuple[str | None, int | None, str | None]:
    """
    DEPRECATED: Attempts to parse old NID strings (kind:index:id) for backward compatibility.
    New code should use IDs directly.
    Returns: (kind, seq_index, item_id)
    """
    if not isinstance(nid, str): return None, None, None
    parts = nid.split(":")
    if len(parts) >= 2:
        try:
            kind = parts[0]
            idx = int(parts[1])
            item_id = parts[2] if len(parts) > 2 else None
            return kind, idx, item_id
        except ValueError:
            pass
    # If it's not a legacy string, we can't infer index/kind easily without the full data object.
    # Return None to signal caller to switch strategies.
    return None, None, None

def _project_effective_length(data: Dict[str, Any]) -> float:
    data = _ensure_project(data)
    total = 0.0
    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    for seq in data.get("sequences", {}).values():
        for vid_id in seq.get("video_order", []):
            vid = seq.get("videos", {}).get(vid_id)
            if vid:
                total += _video_seconds(vid, d)
    return total

def _sequence_effective_length(seq: Dict[str, Any], default_dur: float) -> float:
    total = 0.0
    for vid_id in seq.get("video_order", []):
        vid = seq.get("videos", {}).get(vid_id)
        if vid:
            total += _video_seconds(vid, default_dur)
    return total
# ---- Standard Helpers ----

def get_char_name_from_id(data: Dict[str, Any], char_id: str) -> str:
    return _get_char_name_from_id_v2(data, char_id)

def _fmt_clock(seconds: float) -> str:
    seconds = max(0, float(seconds)); m = int(seconds // 60); s = int(round(seconds % 60))
    return f"{m}:{s:02d}"

def _video_seconds(v: Dict[str, Any], default_dur: float) -> float:
    try: return float(v.get("duration_override_sec"))
    except (ValueError, TypeError): return float(default_dur)

def _get_mtime(path: str | Path) -> float:
    try: return Path(path).stat().st_mtime
    except (OSError, AttributeError, TypeError): return 0.0

def flush_gradio_cache():
    try:
        gradio_tmp_dir = Path(tempfile.gettempdir()) / 'gradio'
        if gradio_tmp_dir.exists(): shutil.rmtree(gradio_tmp_dir, ignore_errors=True)
        gradio_tmp_dir.mkdir(exist_ok=True)
        return f"Gradio's temporary file cache has been flushed. {now_stamp()}"
    except Exception as e: return f"Error flushing cache: {e}"

def cb_save_settings(settings_json_txt: str, workspace_path: str, models_path: str, loras_path: str, comfyui_restart_script_path: str, api_base: str, timeout: int, output_root: str):    
    try:
        data = load_json_file(SETTINGS_PATH) if SETTINGS_PATH.exists() else {}
        ui_data = json.loads(settings_json_txt) if settings_json_txt else {}
        data.update(ui_data)
    except Exception: data = {}

    data["workspace_root"] = workspace_path.strip()
    data["models_root"] = models_path.strip()
    data["loras_root"] = loras_path.strip()
    data["comfyui_restart_script_path"] = comfyui_restart_script_path.strip()
    data["comfy"] = {
        "api_base": api_base.strip(),
        "timeout_seconds": int(timeout),
        "output_root": output_root.strip()
    }
    data["workflows_root"] = str(WORKFLOWS_DIR)
    atomic_write(SETTINGS_PATH, data)
    return json.dumps(data, indent=2, ensure_ascii=False), f"Settings saved. {now_stamp()}"

def get_project_poses_dir(project_json_or_dict) -> Path | None:
    try:
        data = project_json_or_dict
        output_root = data.get("project", {}).get("comfy", {}).get("output_root")
        project_name = data.get("project", {}).get("name")
        if not output_root or not project_name: return None
        base_path = Path(output_root) / project_name / "_poses"
        base_path.mkdir(parents=True, exist_ok=True)
        return base_path
    except Exception: return None

def _get_temp_dir(project_json: str | Dict) -> Path | None:
    try:
        data = project_json
        output_root = data.get("project", {}).get("comfy", {}).get("output_root")
        project_name = data.get("project", {}).get("name")
        if not output_root or not project_name: return None
        temp_dir = Path(output_root) / project_name / "_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir
    except Exception: return None

# ---- File Listings & Save/Load ----

def cb_refresh_all_lists(workspace_dir, models_dir, loras_dir, project_json: str, current_model: str, current_lora: str, current_workflow: str, current_pose: str, current_project: str):
    project_list_update = cb_list_json_files(workspace_dir, current_value=current_project)
    model_list_update = cb_list_model_files(models_dir, current_value=current_model)
    _lora_upd = cb_list_model_files(loras_dir, current_value=current_lora)
    lora_files_list = _lora_upd.get("choices", []) if isinstance(_lora_upd, dict) else []
    workflow_list_update = cb_list_workflow_files(str(WORKFLOWS_DIR), current_value=current_workflow)
    _poses_dir, pose_gallery_update, pose_dropdown_update = refresh_pose_components(project_json, current_pose_val=current_pose)
    
    return (
        project_list_update,
        model_list_update,
        lora_files_list,
        workflow_list_update,
        pose_dropdown_update, 
        pose_gallery_update,  
    )

def cb_list_json_files(base_dir: str, current_value: str = None):
    print(f"🔥 cb_list_json_files DEFINITELY CALLED with base_dir={repr(base_dir)}")
    print(f"[DEBUG] cb_list_json_files called with base_dir={base_dir}")
    base = (base_dir or "").strip()
    p = Path(base) if base else Path.cwd()
    print(f"[DEBUG] Looking for files in: {p}")
    if not p.exists(): 
        print(f"[DEBUG] Path doesn't exist!")
        return gr.update(choices=[], value=None)
    
    # Just use filenames - we'll resolve full path when loading
    files = sorted([fp.name for fp in p.glob("*.json")])
    print(f"[DEBUG] Found {len(files)} files: {files}")
    
    # Match current_value by filename only
    current_name = Path(current_value).name if current_value else None
    val = current_name if current_name and current_name in files else None
    
    print(f"[DEBUG] Returning choices={len(files)}, value={val}")
    return gr.update(choices=files, value=val)

def cb_list_model_files(base_dir: str, current_value: str = None):
    p = Path((base_dir or "").strip() or ".")
    if not p.exists(): return gr.update(choices=[], value=None)
    exts = {".safetensors", ".ckpt", ".pt", ".bin"}
    try:
        files = sorted([fp.name for fp in p.iterdir() if fp.is_file() and fp.suffix.lower() in exts])
    except Exception: files = []
    cur = (Path(current_value).name if current_value else None)
    val = cur if (cur and cur in files) else None
    return gr.update(choices=files, value=val)

def cb_list_workflow_files(base_dir: str, current_value: str = None):
    p = Path(str(base_dir or "").strip())
    choices = [""]; value_to_set = ""
    if p.is_dir():
        try:
            files = sorted([fp.name for fp in p.glob("*.json") if fp.is_file()])
            choices.extend(files)
            cur_name = Path(current_value).name if current_value else ""
            if cur_name and cur_name in files: value_to_set = cur_name
        except Exception: pass
    return gr.update(choices=choices, value=value_to_set)

def cb_list_pose_files(base_dir: str, current_value: str = None):
    p = Path(str(base_dir or "").strip())
    choices = [("(No pose)", "")]; value_to_set = "" 
    img_exts = {".png", ".jpg", ".jpeg", ".webp"}
    if p.is_dir():
        try:
            files = sorted([fp.resolve() for fp in p.iterdir() if fp.is_file() and fp.suffix.lower() in img_exts])
            pose_choices = [(fp.stem, str(fp)) for fp in files]
            choices.extend(pose_choices)
        except Exception: pass
    if current_value and current_value.strip(): value_to_set = str(current_value).strip()
    return gr.update(choices=choices, value=value_to_set)

def get_pose_gallery_list(base_dir: str) -> List[Tuple[str, str]]:
    p = Path(str(base_dir or "").strip())
    if not p.is_dir(): return []
    img_exts = {".png", ".jpg", ".jpeg", ".webp"}
    try:
        files = sorted(
            [fp.resolve() for fp in p.iterdir() if fp.is_file() and fp.suffix.lower() in img_exts],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        return [(str(fp), fp.stem) for fp in files]
    except Exception: return []

def refresh_pose_components(project_json: str, current_pose_val: str = None):
    poses_dir_path = get_project_poses_dir(project_json)
    poses_dir_str = str(poses_dir_path) if poses_dir_path else ""
    pose_dropdown_update = cb_list_pose_files(poses_dir_str, current_value=current_pose_val)
    pose_choices_for_dropdown = pose_dropdown_update.get("choices", []) if isinstance(pose_dropdown_update, dict) else []
    pose_value_for_gallery = [(path, Path(path).name) for stem, path in pose_choices_for_dropdown if path]
    selected_index = None
    if current_pose_val:
        try:
            norm_cur = str(Path(current_pose_val).resolve()).lower()
            for i, (p, _) in enumerate(pose_value_for_gallery):
                if str(Path(p).resolve()).lower() == norm_cur:
                    selected_index = i
                    break
        except Exception: selected_index = None
    pose_gallery_update = (
        gr.update(value=pose_value_for_gallery, selected_index=selected_index)
        if selected_index is not None else gr.update(value=pose_value_for_gallery)
    )
    return (gr.update(value=poses_dir_str), pose_gallery_update, pose_dropdown_update)

def _set_by_path(d: dict, path: str, value: Any):
    keys = path.split('.')
    for key in keys[:-1]: d = d.setdefault(key, {})
    d[keys[-1]] = value

def _auto_version_title(base_title: str, workspace_dir: Path) -> str:
    title = (base_title or "Untitled").strip()
    existing_stems = {fp.stem for fp in workspace_dir.glob("*.json")}
    if title not in existing_stems: return title
    for i in range(2, 1000):
        cand = f"{title} ({i})"
        if cand not in existing_stems: return cand
    return f"{title} ({int(datetime.now().timestamp())})"

def _ensure_nonempty_api_base(data: dict, settings_json: str) -> dict:
    try: settings = json.loads(settings_json) if settings_json else ensure_settings()
    except Exception: settings = ensure_settings()
    global_comfy = settings.get("comfy", DEFAULT_SETTINGS["comfy"])
    data = normalize_project_shape(data) # Uses standard normalize
    # V2 check: if we just normalized a V1 list into data['sequences'], re-run ensure
    data = _ensure_project(data)
    
    pj = data["project"]
    pj["comfy"]["api_base"] = global_comfy.get("api_base", "http://127.0.0.1:8188")
    pj["comfy"]["timeout_seconds"] = global_comfy.get("timeout_seconds", 3600)
    pj["comfy"]["output_root"] = global_comfy.get("output_root", "D:/ComfyUI/output")
    return data

def cb_save_as(save_as_name_or_path: str, settings_json: str, current_project: dict):
    """
    PHASE 2A: Returns (dict, str) - ALWAYS dict, never string
    """
    raw = (save_as_name_or_path or "").strip()
    if not raw: raise gr.Error("Enter a file name (no extension) or a full path.")
    try: settings = json.loads(settings_json) if settings_json else ensure_settings()
    except Exception: settings = ensure_settings()
    workspace_root = settings.get("workspace_root") or str(Path.cwd())
    p = Path(raw)
    if not p.is_absolute(): p = Path(workspace_root) / raw
    if p.suffix.lower() != ".json": p = p.with_suffix(".json")
    p = _auto_version_path(p)
    
    data = _deep_copy(current_project) if isinstance(current_project, dict) else _deep_copy(DEFAULT_PROJECT)
    
    # Clean media paths
    data = _ensure_project(data)
    for seq in data.get("sequences", {}).values():
        for kf in seq.get("keyframes", {}).values():
            kf["selected_image_path"] = None
        for vid in seq.get("videos", {}).values():
            vid["selected_video_path"] = None
            
    data = _ensure_nonempty_api_base(data, settings_json)
    if data.get("project", {}).get("is_protected_from_empty_save") and not data.get("sequences"):
        raise gr.Error("Save As aborted: A protected project cannot be saved with an empty sequence list.")
    atomic_write(p, data)

    return (data, str(p))

def cb_open_file(file_path: str, settings_json: str):
    """
    PHASE 2A: Returns (dict, str) - ALWAYS dict, never string
    """
    if not file_path or not Path(file_path).exists():
        print(f"[OPEN] Failed: Path not found: {file_path}")
        # PHASE 2A: Return DEFAULT_PROJECT which is dict
        return DEFAULT_PROJECT, file_path
    try:
        print(f"[OPEN] Loading: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
            
        # Detect V1 (List-based sequences)
        is_v1 = isinstance(raw_data.get("sequences"), list)
        
        data = _ensure_project(raw_data) # Migrates on load
                
        # If we migrated from V1, force a save to disk immediately 
        # to prevent backend operations from reading the stale V1 file.
        if is_v1:
            print(f"[OPEN] Migrated V1 -> V2. Auto-saving: {file_path}")
            atomic_write(Path(file_path), data)
            
        print(f"[OPEN] Success. Project: {data.get('project', {}).get('name')}")
        
        return data, file_path
    except Exception as e:
        print(f"Error opening file: {e}")
        return DEFAULT_PROJECT, file_path


def cb_create_new_project(name_or_base: str, settings_json: str):

    try: settings = json.loads(settings_json) if settings_json else ensure_settings()
    except Exception: settings = ensure_settings()
    workspace_root = settings.get("workspace_root") or str(Path.cwd())
    raw = (name_or_base or _ts_basename()).strip()
    p = Path(raw)
    if not p.is_absolute(): p = Path(workspace_root) / raw
    if p.suffix.lower() != ".json": p = p.with_suffix(".json")
    ws = Path(workspace_root)
    title = _auto_version_title(p.stem, ws)
    p = p if title == p.stem else p.with_name(f"{title}{p.suffix}")
    p = _auto_version_path(p)
    data = _deep_copy(DEFAULT_PROJECT)
    
    data["project"]["name"] = p.stem
    try:
        # Load defaults from TOML
        cfg = load_config().get("models", {})
        print(f"[DEBUG] App Config 'models' section: {cfg}")
        
        data["project"]["model"] = cfg.get("default_project_model", "sdXL_v10VAEFix.safetensors")
        data["project"]["pose_model_fast"] = cfg.get("pose_model_fast", "sdXL_v10VAEFix.safetensors")
        data["project"]["pose_model_enhanced"] = cfg.get("pose_model_enhanced", "obsessionIllustrious_v21.safetensors")
        data["project"]["inpainting_model"] = cfg.get("inpainting_model", "juggernautxl-inpainting.safetensors")
        data["project"]["controlnet_model"] = cfg.get("controlnet_model", "diffusion_pytorch_model_promax.safetensors")
        data["project"]["upscale_model"] = cfg.get("upscale_model", "4x_NMKD-Siax_200k.pth")
        data["project"]["interpolation_model"] = cfg.get("interpolation_model", "rife47.pth")

        print(f"[INIT] New project initialized with model: {data['project']['model']}")
    except Exception as e:
        print(f"[WARN] Could not load TOML defaults, using fallbacks: {e}")

    data = _ensure_nonempty_api_base(data, settings_json)

    from editor_helpers import _add_sequence, _add_keyframe
    data, new_seq_id = _add_sequence(data)
    data, new_kf_id = _add_keyframe(data, new_seq_id)

    atomic_write(p, data)

    return (data, str(p))


def _is_identical_to_default(data: dict) -> bool:
    """Check if data is functionally identical to DEFAULT_PROJECT"""
    default = _deep_copy(DEFAULT_PROJECT)
    test = _deep_copy(data)
    
    # Exclude auto-populated fields from comparison
    for d in [default, test]:
        proj = d.get("project", {})
        proj.pop("name", None)  # Always differs (timestamp-based)
        proj.pop("is_protected_from_empty_save", None)  # Dynamically set
        comfy = proj.get("comfy", {})
        comfy.pop("api_base", None)  # Populated from settings
    
    return test == default

# ---- PHASE 1: VALIDATION FUNCTION ----
def validate_before_save(data: dict, filepath: str):
    from typing import Tuple
    sequences = data.get("sequences", {})
    
    # Check if project is unchanged from default
    if _is_identical_to_default(data):
        return False, "Cannot save: Project is unchanged from default. Please add sequences, assets, or settings before saving."
    
    # Check project.name (for output path safety)
    project_name = data.get("project", {}).get("name", "")
    if not project_name:
        return False, "Cannot save: Project name is empty. This determines the output folder path."
    
    return True, "OK"

# Global debounce tracking
LAST_SAVE_TIME = {}
SAVE_DEBOUNCE_MS = 500  # Minimum 100ms between saves to same file

def cb_save_project(current_file_path: str, current_project: dict, settings_json: str):
    """
    PHASE 2A NOTE: Accepts dict but has defensive isinstance check during migration.
    The isinstance check will be removed in Phase 2D.
    """
    import time
    
    if not (current_file_path or '').strip(): return
    if isinstance(current_project, (list, tuple)) and current_project: current_project = current_project[0]
    
    # Debounce: Skip if saved too recently
    current_time = time.time() * 1000  # milliseconds
    last_save = LAST_SAVE_TIME.get(current_file_path, 0)
    if current_time - last_save < SAVE_DEBOUNCE_MS:
        return  # Skip this save, too soon
    LAST_SAVE_TIME[current_file_path] = current_time
    
    if not isinstance(current_project, dict): return
    try:
        data = current_project
        data = _ensure_nonempty_api_base(data, settings_json)
        
        p = Path(current_file_path)

        # PHASE 1: Validate before save
        is_valid, error_message = validate_before_save(data, str(p))
        if not is_valid:
            raise gr.Error(error_message)

        # --- SAFETY INTERLOCK ---
        # Prevent overwriting a valid project with a default/empty state (ghost project)
        if p.exists():
            try:
                existing_data = load_json_file(p)
                existing_seqs = existing_data.get("sequences", {})
                new_seqs = data.get("sequences", {})
                
                # Critical Block 1: Sequence Loss
                if existing_seqs and not new_seqs:
                    print(f"🛑 [SAFETY BLOCK] Save aborted! Attempted to overwrite {len(existing_seqs)} sequences with empty data. This indicates UI state loss.")
                    return

                # Critical Block 2: Name Reversion
                existing_name = existing_data.get("project", {}).get("name", "")
                new_name = data.get("project", {}).get("name", "")
                if not new_name and existing_name:
                    print(f"🛑 [SAFETY BLOCK] Project name corrupted (was '{existing_name}', now empty)")
                    return

            except Exception as read_err:

                print(f"Warning: Safety check failed to read existing file: {read_err}")
        # ------------------------

        if data.get("project", {}).get("is_protected_from_empty_save") and not data.get("sequences"):
             print("Save skipped: Protected project cannot be saved empty.")
             return
             

        atomic_write(p, data)
 
        print(f"SAVE -> {p.name}")

    except Exception as e: print(f"Error saving file: {e}")



def atomic_write(path: Path, data: dict):
    global LAST_BACKUP_TIME
    import time
    
    path.parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(data, indent=2, ensure_ascii=False)
    
    # Use the existing ensure_settings() which returns the cached config
    # instead of load_config() which reads the disk.
    settings = ensure_settings() 
    
    backup_cfg = settings.get("backups", {})
    throttle_limit = backup_cfg.get("throttle_seconds", 60)
    retention_limit = backup_cfg.get("retention_count", 10)
        
    # Write to a temp file first
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(path.parent)) as tmp:
        tmp.write(txt)
        tmp_path = Path(tmp.name)

    # Throttled Backup Logic
    current_time = time.time()
    if path.exists() and (current_time - LAST_BACKUP_TIME > throttle_limit):
        try:
            backup_dir = path.parent / "_backups"
            backup_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_filename = f"{path.stem}.json.backup.{timestamp}"
            backup_path = backup_dir / backup_filename
            shutil.copy2(path, backup_path)
            LAST_BACKUP_TIME = current_time
            
            backup_pattern = f"{path.stem}.json.backup.*"
            existing_backups = sorted(backup_dir.glob(backup_pattern))
            if len(existing_backups) > retention_limit:
                for old_backup in existing_backups[:-retention_limit]:
                    try:
                        old_backup.unlink()
                    except Exception: pass
        except Exception as e:
            print(f"Warning: Failed to create backup for {path}: {e}")

    # Atomic replace
    try:
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"ERROR: Failed to save {path}: {e}")
        try: os.remove(tmp_path)
        except Exception: pass

def load_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f: return json.load(f)

def now_stamp() -> str: return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def _ts_basename() -> str: return datetime.now().strftime("Untitled-%Y%m%d-%H%M%S")

def normalize_project_shape(data: dict) -> dict:
    data = data or {}
    if not isinstance(data, dict):
        data = {}

    # Ensure project wrapper is a dict
    if not isinstance(data.get("project"), dict):
        data["project"] = {}
    pj = data["project"]

    base = DEFAULT_PROJECT["project"]

    # If legacy/miswired code wrote project keys at the top-level, pull them back under "project"
    for k, v in base.items():
        if isinstance(v, dict):
            continue
        if k in data and k not in ("project", "sequences") and k not in pj:
            pj[k] = data.pop(k)

    # Fill missing scalar defaults
    for k, v in base.items():
        if not isinstance(v, dict):
            pj.setdefault(k, v)

    # Fill missing nested defaults
    for key in ["negatives", "comfy", "keyframe_generation", "inbetween_generation"]:
        if not isinstance(pj.get(key), dict):
            pj[key] = {}
        for kk, vv in base[key].items():
            pj[key].setdefault(kk, vv)

    # Ensure sequences exists at top-level
    if "sequences" not in data:
        data["sequences"] = {}

    return data

_cached_settings = None

def ensure_settings() -> dict:
    """
    Returns cached settings if available, otherwise loads from disk once.
    """
    global _cached_settings
    if _cached_settings is None:
        _cached_settings = load_config()
    return _cached_settings

# def _ensure_settings() -> dict:
#     """
#     Load application settings from multiple sources:
#     1. config.toml - Installation paths and configuration (primary source)
#     2. workspace_settings.json - Runtime state (last_open_project_path only)
    
#     Returns complete settings dict with all required keys.
#     """
#     # Load installation settings from config.toml
#     data = load_config()
    
#     # Load runtime state from workspace_settings.json (if it exists)
#     if SETTINGS_PATH.exists():
#         try:
#             runtime_state = load_json_file(SETTINGS_PATH)
#             if isinstance(runtime_state, dict):
#                 # Only preserve runtime state fields, not installation settings
#                 if "last_open_project_path" in runtime_state:
#                     data["last_open_project_path"] = runtime_state["last_open_project_path"]
#         except Exception as e:
#             print(f"⚠️  Warning: Could not load runtime state from {SETTINGS_PATH}: {e}")
    
#     return data

# Wrapper for external access (matching imported name in other files)
# ensure_settings = _ensure_settings

# Additional utilities required by imports or legacy calls
def _sanitize_filename(name: str, fallback: str = "file") -> str:
    """Sanitizes a string to be safe for filenames."""
    name = (name or "").strip()
    if not name:
        name = fallback
    name = re.sub(r'[<>:"/\\|?*()]', '', name)
    name = name.replace(' ', '_')
    return name[:50]


def _auto_version_path(dest_path: Path) -> Path:
    """If path exists, find a new path by appending _2, _3, etc."""
    if not dest_path.exists():
        return dest_path
    
    stem = dest_path.stem
    suffix = dest_path.suffix
    parent = dest_path.parent
    
    # Match both old format _(2) and new format _2
    match = re.search(r'_\(?(\d+)\)?$', stem)
    base_stem = stem
    next_n = 2
    if match:
        base_stem = stem[:match.start()]
        next_n = int(match.group(1)) + 1
        
    for i in range(next_n, 1002):
        new_stem = f"{base_stem}_{i}"
        new_path = parent / f"{new_stem}{suffix}"
        if not new_path.exists():
            return new_path
            
    return parent / f"{base_stem}_{int(datetime.now().timestamp())}{suffix}"

# def _auto_version_path(dest_path: Path) -> Path:
#     """If path exists, find a new path by appending _(2), _(3), etc."""
#     if not dest_path.exists():
#         return dest_path
    
#     stem = dest_path.stem
#     suffix = dest_path.suffix
#     parent = dest_path.parent
    
#     match = re.search(r'_\((\d+)\)$', stem)
#     base_stem = stem
#     next_n = 2
#     if match:
#         base_stem = stem[:match.start()]
#         next_n = int(match.group(1)) + 1
        
#     for i in range(next_n, 1002):
#         new_stem = f"{base_stem}_({i})"
#         new_path = parent / f"{new_stem}{suffix}"
#         if not new_path.exists():
#             return new_path
            
#     return parent / f"{base_stem}_{int(datetime.now().timestamp())}{suffix}"

def save_to_project_folder(src_path: str, dest_dir: str, base_name: str, aux_files: Dict[str, str] = None) -> Tuple[str, str]:
    """
    Saves a source file to dest_dir with auto-versioning.
    Also copies optional aux_files into subfolders of dest_dir matching their keys.
    Returns (status_message, new_full_path_str).
    """
    if not src_path or not os.path.exists(src_path):
        return "Error: Source file not found.", ""
    
    if not os.path.isdir(dest_dir):
        return f"Error: Destination directory not found: {dest_dir}", ""

    try:
        src = Path(src_path)
        dest_root = Path(dest_dir)
        
        # Construct initial destination path
        initial_path = dest_root / f"{base_name}{src.suffix}"
        
        # Auto-version to avoid collisions
        final_path = _auto_version_path(initial_path)
        
        # Copy Main File
        shutil.copy(src, final_path)
        
        # Copy Aux Files (if any)
        # aux_files is dict: { "subfolder_name": "source_path" }
        if aux_files:
            for folder_name, aux_src in aux_files.items():
                if aux_src and os.path.exists(aux_src):
                    aux_dir = dest_root / folder_name
                    aux_dir.mkdir(exist_ok=True)
                    # Use the SAME filename as the final main file
                    shutil.copy(aux_src, aux_dir / final_path.name)
        
        return f"Success! Saved as {final_path.name}", str(final_path)

    except Exception as e:
        return f"Error saving file: {e}", ""

def write_image_metadata(file_path: str, metadata_dict: dict):
    """
    Writes custom metadata into PNG (tEXt) chunks.
    """
    import json
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    try:
        img = Image.open(file_path)
        metadata_str = json.dumps(metadata_dict, ensure_ascii=False)
        
        # PNG specific metadata handling
        info = PngInfo()
        info.add_text("comment", metadata_str)
        
        img.save(file_path, pnginfo=info)
        print(f"[METADATA] Successfully wrote PNG tEXt chunk to {file_path}")
        return True
    except Exception as e:
        print(f"[METADATA] Error writing metadata: {e}")
        return False

def get_png_metadata(image_path: str) -> Dict[str, Any]:
    """Reads 'the_machine_snapshot' from PNG metadata."""
    try:
        if not os.path.exists(image_path):
            return {}
        with Image.open(image_path) as img:
            meta = img.info
            snapshot_str = meta.get("the_machine_snapshot")
            return json.loads(snapshot_str) if snapshot_str else {}
    except Exception as e:
        print(f"Error reading metadata from {image_path}: {e}")
        return {}    

# def _save_last_open_path_to_settings(path: str):
#     """Updates the settings file with the last opened project path."""
#     try:
#         # Load existing settings or start fresh
#         if SETTINGS_PATH.exists():
#             data = load_json_file(SETTINGS_PATH)
#         else:
#             data = _deep_copy(DEFAULT_SETTINGS)
            
#         data["last_open_project_path"] = str(path)
#         atomic_write(SETTINGS_PATH, data)
#     except Exception as e:
#         print(f"Warning: Failed to save last open path: {e}")

# ============================================================================
# PHASE 3: ATOMIC LOAD FUNCTION
# ============================================================================
def load_project_complete(filepath: str, settings_json: str, form_registry, get_style_test_images_fn):
    """
    PHASE 3: Atomic load function that replaces buffer states and .then() chains.
    
    Loads a project file and calculates ALL derived state in one function call.
    
    Args:
        filepath: Path to the project file to load
        settings_json: JSON string of current settings (for API base, etc.)
        form_registry: The ProjectFormRegistry instance from app.py
        get_style_test_images_fn: Function to get style test images (to avoid circular import)
        
    Returns:
        Tuple of (project_data, filepath, *form_values, *refresh_outputs, file_name_md, poses_dir, pose_gallery, style_gallery)
    """

    
    try:
        print(f"[LOAD_COMPLETE] Starting load for: {filepath}")
        
        # 1. Load the file using existing cb_open_file logic
        project_data, loaded_filepath = cb_open_file(filepath, settings_json)
        print(f"[LOAD_COMPLETE] Step 1: File loaded, type={type(project_data)}")
        
        # 2. Get all form values using form.load_from_json()
        form_values = form_registry.load_from_json(project_data)
        print(f"[LOAD_COMPLETE] Step 2: Form values extracted, count={len(form_values) if isinstance(form_values, (list, tuple)) else 'unknown'}")
        
        # Debug: Check what types of values we're getting
        if isinstance(form_values, (list, tuple)):
            value_types = {}
            for i, val in enumerate(form_values):
                val_type = type(val).__name__
                if val_type == 'dict':
                    # Check if it's a gr.update dict
                    if isinstance(val, dict) and ('__type__' in val or 'choices' in val or 'value' in val):
                        val_type = 'gr.update_dict'
                        print(f"[LOAD_COMPLETE] WARNING: Form value {i} is gr.update dict: {val}")
                value_types[val_type] = value_types.get(val_type, 0) + 1
            print(f"[LOAD_COMPLETE] Step 2 types: {value_types}")
            # Print first few values as sample
            print(f"[LOAD_COMPLETE] Step 2 sample values (first 10): {[f'{type(v).__name__}:{str(v)[:30]}' for v in form_values[:10]]}")
        
        # CRITICAL FIX: Wrap all form values in gr.update to ensure consistent update mechanism
        # This prevents Gradio from getting confused about component state
        form_values_wrapped = [gr.update(value=v) for v in form_values]
        print(f"[LOAD_COMPLETE] Step 2b: Wrapped {len(form_values_wrapped)} form values in gr.update")
        
        # 3. Calculate refresh outputs (for master_refresh_outputs)
        try:
            settings = json.loads(settings_json) if settings_json else ensure_settings()
        except Exception:
            settings = ensure_settings()
        
        workspace_dir = settings.get("workspace_root", str(Path.cwd()))
        models_dir = settings.get("models_root", "")
        loras_dir = settings.get("loras_root", "")
        
        # Extract current values from loaded project to ensure dropdowns show correct selections
        current_model = project_data.get("project", {}).get("model", "")
        current_workflow = project_data.get("project", {}).get("inbetween_generation", {}).get("video_workflow_json", "")
        
        refresh_outputs = cb_refresh_all_lists(
            workspace_dir, 
            models_dir, 
            loras_dir, 
            project_data,  # Pass dict directly (Phase 2 contract)
            current_model,  # Pass actual model from project
            "",  # current_lora
            current_workflow,  # Pass actual workflow from project
            "",  # current_pose
            loaded_filepath  # current_project path
        )
        print(f"[LOAD_COMPLETE] Step 3: Refresh outputs calculated, count={len(refresh_outputs) if isinstance(refresh_outputs, (list, tuple)) else 'unknown'}")
        
        # 4. Calculate current_file_name markdown
        file_name_md = f"**File:** `{Path(loaded_filepath).name}`" if loaded_filepath else ""
        print(f"[LOAD_COMPLETE] Step 4: File name MD calculated")
        
        # 5. Calculate poses_dir and pose_gallery
        poses_dir = get_project_poses_dir(project_data)
        poses_dir_str = str(poses_dir) if poses_dir else ""
        
        # refresh_pose_components returns (poses_dir_update, pose_gallery_update, pose_dropdown_update)
        # We only need the pose_gallery_update (index 1)
        pose_gallery_update = refresh_pose_components(project_data, None)[1]
        print(f"[LOAD_COMPLETE] Step 5: Pose gallery calculated")
        
        # 6. Get style_gallery using the provided function
        style_gallery_update = get_style_test_images_fn(project_data)
        print(f"[LOAD_COMPLETE] Step 6: Style gallery calculated")
        
        # 7. Calculate UI lock updates
        # 7. Calculate UI lock updates (Matched to 6 items in locked_ui_components)
        is_locked = not (loaded_filepath and loaded_filepath.strip())
        tab_update = gr.update(interactive=not is_locked)
        accordion_update = gr.update(visible=not is_locked)
        
        # This MUST match the length of locked_ui_components in app.py
        ui_lock_updates = (
            accordion_update, # project_basics
            accordion_update, # project_style
            tab_update,       # assets_tab
            tab_update,       # editor_tab
            tab_update,       # curate_tab
            tab_update,       # utilities_tab
            accordion_update, # json_renderer
            gr.update(),      # ghost state 1
            gr.update(),      # ghost state 2
            gr.update()       # ghost state 3
        )
        print(f"[LOAD_COMPLETE] Step 7: UI lock updates calculated (is_locked={is_locked})")
        
        # Build return tuple
        result = (
            project_data,          # preview_code
            loaded_filepath,       # current_file_path
            *form_values_wrapped,  # All form field values (wrapped in gr.update)
            *refresh_outputs,      # master_refresh_outputs: [file_picker, model_dd, lora_file_state, kf_workflow_json, refresh_sink, kf_pose_gallery]
            file_name_md,          # current_file_name
            poses_dir_str,         # poses_dir_state
            pose_gallery_update,   # pose_gallery
            style_gallery_update,  # style_gallery
            *ui_lock_updates,      # locked_ui_components: [4 accordions, 4 tabs, 1 json_renderer]
        )
        
        print(f"[LOAD_COMPLETE] Success! Returning {len(result)} values")
        print(f"[LOAD_COMPLETE] Return breakdown:")
        print(f"  - preview_code: {type(result[0]).__name__}")
        print(f"  - current_file_path: {result[1]}")
        print(f"  - form_values[0-2]: {[f'{type(result[2+i]).__name__}:{str(result[2+i])[:50]}' for i in range(min(3, len(form_values_wrapped)))]}")
        print(f"  - refresh_outputs: {[type(x).__name__ for x in refresh_outputs]}")
        return result
        
    except Exception as e:
        print(f"[LOAD_COMPLETE] ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise


