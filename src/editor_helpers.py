# editor_helpers.py
from __future__ import annotations
import json
import copy
import os
import shutil
from pathlib import Path
from typing import Dict, List, Tuple, Any
import gradio as gr
import re
import time
import uuid
from PIL import Image
import subprocess
from run_helpers import handle_pose_batch

from test_gen_helpers import handle_test_generation
from test_gen_helpers import run_pose_preview_task
from test_video_helpers import handle_test_video_generation

from assets_helpers import _inject_lora_simple, handle_pose_generation, save_or_update_pose, _resolve_asset_aux, save_uploaded_pose, handle_pose_qc
from qc_helpers import handle_pose_qc
from run_helpers import ( 
    purge_sequence_keyframes, purge_sequence_inbetweens, 
    read_sequence_status_files, cancel_batch_script,
    handle_sequence_image_batch, handle_sequence_video_batch,
    handle_cascade_batch, cancel_cascade_batch,
    build_run_status_ui, build_batch_inputs, build_batch_run_btn, build_batch_cancel_btn, build_purge_ui,
    build_bridge_manager, handle_bridge_batch, cancel_bridge_batch, purge_sequence_bridges, read_bridge_status,
    build_export_panel, handle_export_task, list_existing_exports, handle_sequence_export_task,
    # save_uploaded_audio, list_project_audio, refresh_audio_list_ui
    save_uploaded_audio, list_project_audio, refresh_audio_list_ui,
    build_enhance_manager, handle_upscale_batch, cancel_upscale_batch, handle_qc_batch
)


from helpers import (
    WORKFLOWS_DIR, DEFAULT_KF_USE_ANIMAL_POSE, 
    DEFAULT_KF_CN_SETTINGS, DUR_CHOICES, 
    _ensure_project, _ensure_seq_defaults, _video_seconds, 
    _derive_videos_for_seq, _fmt_clock, _sequence_effective_length, 
    _project_effective_length, _rows_with_times,
    parse_nid, cb_save_project, get_project_poses_dir,
    get_node_by_id, get_pose_gallery_list, ensure_settings,
    _sanitize_filename
)



# ---- INTERNAL HELPERS ----

def _check_ownership(data: dict, loaded_nid: str, loaded_proj: str) -> bool:
    """Returns True if save should proceed (same project), False to skip."""
    if not loaded_nid:
        return False
    current_proj = data.get("project", {}).get("name", "")
    if loaded_proj and current_proj and loaded_proj != current_proj:
        print(f"[OWNERSHIP] Skipping save: project changed from '{loaded_proj}' to '{current_proj}'")
        return False
    return True

def _resolve_node_context(data: Dict[str, Any], node_id: str) -> Tuple[Dict[str, Any] | None, str | None, Dict[str, Any] | None, str | None]:
    """
    Resolves an ID to (node, type, parent_sequence, parent_sequence_id).
    Type is 'seq', 'kf', or 'vid'.
    """
    if not node_id: return None, None, None, None
    
    # 1. Check if it's a Sequence
    if node_id in data.get("sequences", {}):
        seq = data["sequences"][node_id]
        return seq, "seq", seq, node_id
        
    # 2. Check within Sequences
    for seq_id, seq in data.get("sequences", {}).items():
        # Check Keyframes
        if node_id in seq.get("keyframes", {}):
            return seq["keyframes"][node_id], "kf", seq, seq_id
        
        # Check Videos
        if node_id in seq.get("videos", {}):
            return seq["videos"][node_id], "vid", seq, seq_id
            
    return None, None, None, None

def _compute_required_gaps(seq: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Compute the required video gaps based on keyframe_order and open_start/end flags.
    Returns list of (start_id, end_id) tuples where "open" indicates open boundary.
    """
    kf_order = seq.get("keyframe_order", [])
    open_start = bool(seq.get("video_plan", {}).get("open_start", False))
    open_end = bool(seq.get("video_plan", {}).get("open_end", True))
    
    required_gaps = []
    if not kf_order:
        if open_start and open_end:
            required_gaps.append(("open", "open"))
    else:
        if open_start:
            required_gaps.append(("open", kf_order[0]))
        for i in range(len(kf_order) - 1):
            required_gaps.append((kf_order[i], kf_order[i + 1]))
        if open_end:
            required_gaps.append((kf_order[-1], "open"))
    
    return required_gaps


def _refresh_video_chain(seq: Dict[str, Any], default_dur: float, full_data: Dict[str, Any] = None):
    """
    Reconstructs the video chain for a sequence using POSITIONAL model.
    
    Videos are identified by their position in video_order, not by their endpoints.
    - video_order[i] always fills gap[i]
    - Endpoints are updated to match the gap at that position
    - Videos preserve their content (prompts, paths) across reorders
    
    When gap count changes:
    - Gaps increase: new videos created at end
    - Gaps decrease: excess videos removed from end (lower-indexed videos survive)
    """
    old_vids = seq.get("videos", {})
    old_vid_order = seq.get("video_order", [])
    
    # 1. Determine required gaps
    required_gaps = _compute_required_gaps(seq)
    
    # 2. Gather global forbidden IDs to prevent collisions across sequences
    global_vid_ids = set()
    if full_data:
        for s in full_data.get("sequences", {}).values():
            global_vid_ids.update(s.get("videos", {}).keys())
    
    # 3. Build new video chain positionally
    new_vids = {}
    new_vid_order = []
    
    for gap_idx, (start_id, end_id) in enumerate(required_gaps):
        if gap_idx < len(old_vid_order):
            # Reuse existing video at this position
            vid_id = old_vid_order[gap_idx]
            if vid_id in old_vids and vid_id != "__placeholder__":
                vid = old_vids[vid_id]
                # Update endpoints to match new gap
                vid["start_keyframe_id"] = None if start_id == "open" else start_id
                vid["end_keyframe_id"] = None if end_id == "open" else end_id
                new_vids[vid_id] = vid
                new_vid_order.append(vid_id)
                continue
        
        # Create new video for this gap
        count = 0
        while (f"vid{count}" in new_vids or 
               f"vid{count}" in old_vids or 
               f"vid{count}" in global_vid_ids):
            count += 1
        vid_id = f"vid{count}"
        
        new_vid = {
            "id": vid_id,
            "type": "video",
            "start_keyframe_id": None if start_id == "open" else start_id,
            "end_keyframe_id": None if end_id == "open" else end_id,
            "inbetween_prompt": "",
            "negative_prompt": "",
            "selected_video_path": None
        }
        new_vids[vid_id] = new_vid
        new_vid_order.append(vid_id)
    
    # Videos beyond required_gaps count are dropped (lower-indexed survive)
    
    seq["videos"] = new_vids
    seq["video_order"] = new_vid_order
    return seq

# ---- PATH RESOLVERS ----

def _get_kf_dir(data: Dict[str, Any], seq_id: str, kf_id: str) -> Path | None:
    try:
        proj = data.get("project", {})
        output_root = proj.get("comfy", {}).get("output_root")
        proj_name = proj.get("name")
        
        if not all([output_root, proj_name, seq_id, kf_id]): return None
        return Path(output_root) / proj_name / seq_id / kf_id
    except (IndexError, KeyError): return None

def _get_vid_dir(data: Dict[str, Any], seq_id: str, vid_id: str) -> Path | None:
    try:
        proj = data.get("project", {})
        output_root = proj.get("comfy", {}).get("output_root")
        proj_name = proj.get("name")
        
        if not all([output_root, proj_name, seq_id, vid_id]): return None
        return Path(output_root) / proj_name / seq_id / vid_id
    except (IndexError, KeyError): return None


def _resolve_gallery_index(pose_path, gallery_items):
    if not pose_path or not gallery_items:
        return None

    def _norm(p):
        try:
            return os.path.abspath(os.path.normpath(p))
        except Exception:
            return None

    target = _norm(pose_path)

    for i, item in enumerate(gallery_items):
        if isinstance(item, (list, tuple)) and item:
            item_path = item[0]
        elif isinstance(item, dict):
            item_path = item.get("path")
        else:
            continue

        if _norm(item_path) == target:
            return i

    return None

def _resolve_real_path_from_filename(data: dict, nid: str, ui_path: str) -> str | None:
    """Reconstructs the authoritative file path on disk using the filename from the UI."""
    if not ui_path: return None
    
    try:
        filename = Path(ui_path).name
        
        # New Context Resolution
        node, kind, seq, seq_id = _resolve_node_context(data, nid)
        
        if kind == "kf":
            # Method A: Standard Structure
            target_dir = _get_kf_dir(data, seq_id, node["id"])
            if target_dir and target_dir.exists():
                candidate = target_dir / filename
                if candidate.exists():
                    return str(candidate)

            # Method B: JSON Fallback
            saved_path = node.get("selected_image_path")
            if saved_path:
                parent_dir = Path(saved_path).parent
                candidate = parent_dir / filename
                if candidate.exists():
                    return str(candidate)
        
    except Exception:
        pass
    return None

def _try_delete_path(path: Path, retries=3, delay=0.2):
    if not path.exists(): return
    for i in range(retries):
        try:
            if path.is_file(): os.remove(path)
            elif path.is_dir(): shutil.rmtree(path)
            return
        except (PermissionError, OSError) as e:
            if i < retries - 1: time.sleep(delay)
            else: print(f"Error deleting {path}: {e}")


def _get_filtered_outline_rows(data: Dict, selected_nid: str) -> List[Tuple[str, str]]:
    """
    Gets the list of outline rows.
    Always includes all Sequences.
    Only includes Keyframes/Videos if they belong to the 'Active Sequence'.
    The Active Sequence is defined as:
      - The selected node itself (if it is a sequence).
      - The parent sequence of the selected node (if it is a child).
    """
    full_rows = _rows_with_times(data)
    if not full_rows: return []


    active_seq_id = None
    if selected_nid:
        # Is the selection a Sequence?
        if selected_nid in data.get("sequences", {}):
            active_seq_id = selected_nid
        else:
            # Is it a child? Resolve its parent.
            _, _, _, parent_id = _resolve_node_context(data, selected_nid)
            active_seq_id = parent_id


    filtered_rows = []
    for label, nid in full_rows:
        # Always include Sequences
        if nid in data.get("sequences", {}):
            filtered_rows.append((label, nid))
            continue
            
        # For children (KFs/Vids), only include if their parent is the Active Sequence
        if active_seq_id:
            _, _, _, parent_id = _resolve_node_context(data, nid)
            if parent_id == active_seq_id:
                filtered_rows.append((label, nid))
    

    return filtered_rows

def _dropdown_update_for_kf(data: dict, target_nid: str, preferred_value: str | None):
    dd_update = gr.update(value=None)
    img_update = gr.update(value=None)
    
    node, kind, _, _ = _resolve_node_context(data, target_nid)
    if kind != "kf":
        return dd_update, img_update

    choices_list = _get_kf_gallery_images(data, target_nid)
    norm_map = {str(Path(c).resolve()).lower(): c for c in choices_list}
    val_norm = None
    if preferred_value:
        try:
            pv_resolved = str(Path(str(preferred_value)).resolve()).lower()
            if pv_resolved in norm_map:
                val_norm = norm_map[pv_resolved]
        except Exception: pass
    choices_for_ui = [(os.path.basename(p), p) for p in choices_list]
    dd_update = gr.update(choices=choices_for_ui, value=val_norm)
    img_update = gr.update(value=val_norm)
    return dd_update, img_update

def _project_len_text(data: Dict[str, Any]) -> str:
    return f"Project length: {int(round(_project_effective_length(data)))} sec"

def _build_sequence_assets_html(data: Dict, seq_id: str) -> str:
    """Build HTML display of sequence keyframe images and video status."""
    import base64
    
    if not seq_id:
        return "<div style='color:#888; padding:10px;'>No sequence selected</div>"
    
    seq = data.get("sequences", {}).get(seq_id)
    if not seq:
        return "<div style='color:#888; padding:10px;'>Sequence not found</div>"
    
    keyframes = seq.get("keyframes", {})
    videos = seq.get("videos", {})
    kf_order = seq.get("keyframe_order", [])
    vid_order = seq.get("video_order", [])
    video_plan = seq.get("video_plan", {})
    open_start = video_plan.get("open_start", False)
    
    if not kf_order and not vid_order:
        return "<div style='color:#888; padding:10px;'>No keyframes in sequence</div>"
    
    def _img_to_base64(path: str) -> str:
        """Convert image to base64 data URI."""
        try:
            p = Path(path)
            if not p.exists():
                return ""
            suffix = p.suffix.lower()
            mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(suffix, "image/png")
            with open(p, "rb") as f:
                b64data = base64.b64encode(f.read()).decode("utf-8")
            return f"data:{mime};base64,{b64data}"
        except:
            return ""
    
    def _video_icon_html(vid_obj: dict) -> str:
        """Generate video status icon matching node selector style."""
        has_selection = bool(vid_obj.get("selected_video_path"))
        icon = "▶" if has_selection else "▷"
        return f"""
            <div style='display:flex; align-items:center; justify-content:center; 
                        width:20px; flex-shrink:0;'>
                <span style='color:var(--color-vid); font-size:14px;'>{icon}</span>
            </div>
        """
    
    def _kf_thumbnail_html(kf_obj: dict) -> str:
        """Generate keyframe thumbnail or placeholder."""
        selected_path = kf_obj.get("selected_image_path")
        if selected_path and Path(selected_path).exists():
            b64 = _img_to_base64(selected_path)
            if b64:
                return f"""
                    <div style='width:96px; height:96px; border-radius:4px; overflow:hidden; flex-shrink:0;
                                border:2px solid var(--color-kf);'>
                        <img src='{b64}' style='width:100%; height:100%; object-fit:cover;' />
                    </div>
                """
        return """
            <div style='width:96px; height:96px; border:2px dashed var(--color-kf); border-radius:4px; 
                        display:flex; align-items:center; justify-content:center; flex-shrink:0;
                        background:#2a2a2a;'>
                <span style='color:var(--color-kf); font-size:24px;'>?</span>
            </div>
        """
    html_parts = []
    html_parts.append("""
        <div style='display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:8px;'>
    """)
    
    vid_idx = 0
    
    # Handle open start: first video comes before first keyframe
    if open_start and vid_order:
        vid = videos.get(vid_order[0], {})
        html_parts.append(_video_icon_html(vid))
        vid_idx = 1
    
    # Interleave keyframes and videos
    for i, kf_id in enumerate(kf_order):
        kf = keyframes.get(kf_id, {})
        html_parts.append(_kf_thumbnail_html(kf))
        
        # Video after this keyframe
        if vid_idx < len(vid_order):
            vid = videos.get(vid_order[vid_idx], {})
            html_parts.append(_video_icon_html(vid))
            vid_idx += 1
    
    html_parts.append("</div>")
    return "".join(html_parts)

def _outline_signature(data: Dict[str, Any]) -> str:
    # Used for detecting changes to refresh the sidebar
    data = _ensure_project(data)
    default_dur = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    parts: List[str] = [f"D={default_dur}"]
    
    # Iterate in sequence_order
    seqs_dict = data.get("sequences", {})
    seq_order = data.get("sequence_order", [])
    seqs = [seqs_dict[sid] for sid in seq_order if sid in seqs_dict]
    
    for i, seq in enumerate(seqs):
        kf_ids = seq.get("keyframe_order", [])
        vid_ids = seq.get("video_order", [])
        
        lens = []
        vid_prompts = []
        for vid_id in vid_ids:
            v = seq.get("videos", {}).get(vid_id, {})
            lens.append(str(_video_seconds(v, default_dur)))
            vid_prompts.append((v.get("inbetween_prompt", "") or "")[:10])
            
        open_start = int(seq.get("video_plan", {}).get("open_start", False))
        open_end = int(seq.get("video_plan", {}).get("open_end", True))
        setting = (seq.get("setting_prompt", "") or "")[:10]
        
        kf_sigs = []
        for kid in kf_ids:
            k = seq.get("keyframes", {}).get(kid, {})
            chars = k.get("characters", [])
            c1 = chars[0] if len(chars)>0 else ""
            c2 = chars[1] if len(chars)>1 else ""
            pose = k.get("pose", "")
            kf_sigs.append(f"{c1}{c2}{pose}")
            
        parts.append(f"s={seq['id']}|os={open_start}|oe={open_end}|k={','.join(kf_ids)}|v={','.join(vid_ids)}|l={','.join(lens)}|set={setting}|ks={','.join(kf_sigs)}")
        
    return "||".join(parts)

def _get_max_id_num(id_dict: Dict[str, Any], prefix: str) -> int:
    max_n = 0
    if not isinstance(id_dict, dict): return 0
    for key in id_dict.keys():
        if isinstance(key, str) and key.startswith(prefix):
            try:
                if num_str := ''.join(filter(str.isdigit, key)): max_n = max(max_n, int(num_str))
            except (ValueError, IndexError): continue
    return max_n

# ---- CRUD OPERATIONS (V2) ----

def _add_sequence(data: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    data = _ensure_project(data)
    
    # 1. Generate ID
    existing_ids = data["sequences"].keys()
    max_n = 0
    for sid in existing_ids:
        if sid.startswith("seq"):
            try: max_n = max(max_n, int(sid[3:]))
            except: pass
    new_id = f"seq{max_n + 1}"
    
    # 2. Create Object (no 'order' or 'sequence_id' - ordering via sequence_order list)
    new_seq = {
        "id": new_id,
        "type": "sequence",
        "keyframes": {},
        "keyframe_order": [],
        "videos": {},
        "video_order": [],
        "video_plan": {"open_start": False, "open_end": True}
    }
    

    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    new_seq = _refresh_video_chain(new_seq, d, data)
    
    data["sequences"][new_id] = new_seq
    data["sequence_order"].append(new_id)  # Append to ordering list
    data["project"]["is_protected_from_empty_save"] = True
    
    return data, new_id

def _delete_sequence(data: Dict[str, Any], seq_id: str) -> Tuple[Dict[str, Any], str]:
    data = _ensure_project(data)
    seq_order = data.get("sequence_order", [])
    
    # Find position before deletion for selecting nearest
    old_idx = seq_order.index(seq_id) if seq_id in seq_order else -1
    
    if seq_id in data["sequences"]:
        del data["sequences"][seq_id]
    
    # Remove from sequence_order
    if seq_id in seq_order:
        seq_order.remove(seq_id)
        
    # Return valid selection (nearest or empty)
    if seq_order:
        # Select the sequence at the same position, or the last one if we deleted the last
        new_idx = min(old_idx, len(seq_order) - 1) if old_idx >= 0 else 0
        return data, seq_order[new_idx]
    return data, ""




def _duplicate_sequence(data: Dict[str, Any], seq_id: str) -> Tuple[Dict[str, Any], str]:
    data = _ensure_project(data)
    if seq_id not in data["sequences"]: return data, seq_id
    
    orig = data["sequences"][seq_id]
    new_seq = copy.deepcopy(orig)
    
    # 1. Generate New Sequence ID
    existing_ids = data["sequences"].keys()
    max_n = 0
    for sid in existing_ids:
        if sid.startswith("seq"):
            try: max_n = max(max_n, int(sid[3:]))
            except: pass
    new_id = f"seq{max_n + 1}"
    
    new_seq["id"] = new_id

    max_kf_num = 0
    max_vid_num = 0
    for s in data["sequences"].values():
        for k in s.get("keyframes", {}).keys():
            if k.startswith("id"):
                try: max_kf_num = max(max_kf_num, int(k[2:]))
                except: pass
        for v in s.get("videos", {}).keys():
            if v.startswith("vid"):
                try: max_vid_num = max(max_vid_num, int(v[3:]))
                except: pass

    # 3. Remap Keyframes
    old_kfs = new_seq.get("keyframes", {})
    new_kfs = {}
    new_kf_order = []
    kf_map = {} # old_id -> new_id
    
    for old_id in new_seq.get("keyframe_order", []):
        if old_id not in old_kfs: continue
        
        max_kf_num += 1
        new_kf_id = f"id{max_kf_num}"
        
        kf_obj = old_kfs[old_id]
        kf_obj["id"] = new_kf_id
        kf_obj["sequence_id"] = new_id
        kf_obj["selected_image_path"] = None 
        
        new_kfs[new_kf_id] = kf_obj
        new_kf_order.append(new_kf_id)
        kf_map[old_id] = new_kf_id
        
    new_seq["keyframes"] = new_kfs
    new_seq["keyframe_order"] = new_kf_order
    
    # 4. Remap Videos
    old_vids = new_seq.get("videos", {})
    new_vids = {}
    new_vid_order = []
    
    for old_id in new_seq.get("video_order", []):
        if old_id not in old_vids: continue
        
        max_vid_num += 1
        new_vid_id = f"vid{max_vid_num}"
        
        vid_obj = old_vids[old_id]
        vid_obj["id"] = new_vid_id
        vid_obj["sequence_id"] = new_id
        vid_obj["selected_video_path"] = None
        
        # Update start/end references using the Keyframe Map
        s_ref = vid_obj.get("start_keyframe_id")
        e_ref = vid_obj.get("end_keyframe_id")
        if s_ref in kf_map: vid_obj["start_keyframe_id"] = kf_map[s_ref]
        if e_ref in kf_map: vid_obj["end_keyframe_id"] = kf_map[e_ref]
        
        new_vids[new_vid_id] = vid_obj
        new_vid_order.append(new_vid_id)
        
    new_seq["videos"] = new_vids
    new_seq["video_order"] = new_vid_order

    # 5. Insert after original in sequence_order
    seq_order = data.get("sequence_order", [])
    try:
        orig_idx = seq_order.index(seq_id)
        seq_order.insert(orig_idx + 1, new_id)
    except ValueError:
        seq_order.append(new_id)
    
    data["sequences"][new_id] = new_seq
    return data, new_id


def _move_sequence_up(data: Dict[str, Any], seq_id: str) -> Tuple[Dict[str, Any], str]:
    """
    Move a sequence up (earlier) in the sequence_order.
    Wraps circularly: moving up from first position goes to last.
    """
    data = _ensure_project(data)
    seq_order = data.get("sequence_order", [])
    
    if seq_id not in seq_order or len(seq_order) < 2:
        return data, seq_id
    
    idx = seq_order.index(seq_id)
    
    # Circular wrap: position 0 goes to end
    if idx == 0:
        # Remove from front, append to end
        seq_order.pop(0)
        seq_order.append(seq_id)
    else:
        # Swap with previous
        seq_order[idx], seq_order[idx - 1] = seq_order[idx - 1], seq_order[idx]
    
    return data, seq_id


def _move_sequence_down(data: Dict[str, Any], seq_id: str) -> Tuple[Dict[str, Any], str]:
    """
    Move a sequence down (later) in the sequence_order.
    Wraps circularly: moving down from last position goes to first.
    """
    data = _ensure_project(data)
    seq_order = data.get("sequence_order", [])
    
    if seq_id not in seq_order or len(seq_order) < 2:
        return data, seq_id
    
    idx = seq_order.index(seq_id)
    
    # Circular wrap: last position goes to front
    if idx == len(seq_order) - 1:
        # Remove from end, insert at front
        seq_order.pop()
        seq_order.insert(0, seq_id)
    else:
        # Swap with next
        seq_order[idx], seq_order[idx + 1] = seq_order[idx + 1], seq_order[idx]
    
    return data, seq_id


def _move_keyframe_up(data: Dict[str, Any], seq_id: str, kf_id: str) -> Tuple[Dict[str, Any], str]:
    """
    Move a keyframe up (earlier) in its sequence's keyframe_order.
    Wraps circularly: moving up from first position goes to last.
    
    Videos remain in their positional slots; endpoints are recomputed.
    """
    data = _ensure_project(data)
    seq = data["sequences"].get(seq_id)
    if not seq:
        return data, kf_id
    
    kf_order = seq.get("keyframe_order", [])
    
    if kf_id not in kf_order or len(kf_order) < 2:
        return data, kf_id
    
    idx = kf_order.index(kf_id)
    
    # Circular wrap: position 0 goes to end
    if idx == 0:
        kf_order.pop(0)
        kf_order.append(kf_id)
    else:
        # Swap with previous
        kf_order[idx], kf_order[idx - 1] = kf_order[idx - 1], kf_order[idx]
    
    # Recompute video endpoints (videos stay in their slots)
    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    _refresh_video_chain(seq, d, data)
    
    return data, kf_id


def _move_keyframe_down(data: Dict[str, Any], seq_id: str, kf_id: str) -> Tuple[Dict[str, Any], str]:
    """
    Move a keyframe down (later) in its sequence's keyframe_order.
    Wraps circularly: moving down from last position goes to first.
    
    Videos remain in their positional slots; endpoints are recomputed.
    """
    data = _ensure_project(data)
    seq = data["sequences"].get(seq_id)
    if not seq:
        return data, kf_id
    
    kf_order = seq.get("keyframe_order", [])
    
    if kf_id not in kf_order or len(kf_order) < 2:
        return data, kf_id
    
    idx = kf_order.index(kf_id)
    
    # Circular wrap: last position goes to front
    if idx == len(kf_order) - 1:
        kf_order.pop()
        kf_order.insert(0, kf_id)
    else:
        # Swap with next
        kf_order[idx], kf_order[idx + 1] = kf_order[idx + 1], kf_order[idx]
    
    # Recompute video endpoints (videos stay in their slots)
    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    _refresh_video_chain(seq, d, data)
    
    return data, kf_id


def _add_keyframe(data: Dict[str, Any], seq_id: str) -> Tuple[Dict[str, Any], str]:
    data = _ensure_project(data)
    seq = data["sequences"].get(seq_id)
    if not seq: return data, ""
    
    # 1. ID (Global Unique Check)
    max_id = 0
    for s in data["sequences"].values():
        for k in s.get("keyframes", {}).keys():
            if k.startswith("id"):
                try: max_id = max(max_id, int(k[2:]))
                except: pass
    kid = f"id{max_id + 1}"
    
    # 2. Object
    new_kf = {
        "id": kid,
        "type": "keyframe",
        "sequence_id": seq_id,
        "pose": "",
        "layout": "",
        "template": "",
        "workflow_json": str(Path(WORKFLOWS_DIR) / "pose_OPEN.json"), 
        "negatives": {"left":"", "right":"", "heal":""},
        "characters": ["", ""],
        "selected_image_path": None,
        "use_animal_pose": DEFAULT_KF_USE_ANIMAL_POSE, 
        "controlnet_settings": copy.deepcopy(DEFAULT_KF_CN_SETTINGS),
        "join_smoothing_level": 1,
        "join_offset": 0
    }
    
    seq["keyframes"][kid] = new_kf
    seq["keyframe_order"].append(kid)
    
    # 3. Refresh Videos
    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    _refresh_video_chain(seq, d, data)
    
    return data, kid

def _delete_keyframe(data: Dict[str, Any], seq_id: str, kf_id: str) -> Tuple[Dict[str, Any], str]:
    data = _ensure_project(data)
    seq = data["sequences"].get(seq_id)
    if not seq: return data, ""
    

    if kf_id in seq.get("keyframes", {}):
        kf_order = seq.get("keyframe_order", [])
        was_last = kf_order and kf_order[-1] == kf_id
        was_first = kf_order and kf_order[0] == kf_id
        
        del seq["keyframes"][kf_id]
        if kf_id in kf_order:
            kf_order.remove(kf_id)
        
        # Auto-open boundaries to preserve videos when deleting edge keyframes
        # (only if other keyframes remain)
        if kf_order:
            if was_last and not seq.get("video_plan", {}).get("open_end", True):
                seq["video_plan"]["open_end"] = True
            if was_first and not seq.get("video_plan", {}).get("open_start", False):
                seq["video_plan"]["open_start"] = True
            
    # Refresh Videos
    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    _refresh_video_chain(seq, d, data)
    
    # Return selection
    if seq["keyframe_order"]:
        return data, seq["keyframe_order"][0]
    return data, seq_id

def _duplicate_keyframe(data: Dict[str, Any], seq_id: str, kf_id: str) -> Tuple[Dict[str, Any], str]:
    data = _ensure_project(data)
    seq = data["sequences"].get(seq_id)
    if not seq or kf_id not in seq.get("keyframes", {}): return data, kf_id
    
    orig = seq["keyframes"][kf_id]
    new_kf = copy.deepcopy(orig)
    
    # New ID
    # kid = f"id{_get_max_id_num(seq['keyframes'], 'id') + 1}"
    # New ID - GLOBAL uniqueness check across all sequences
    max_id = 0
    for s in data["sequences"].values():
        for k in s.get("keyframes", {}).keys():
            if k.startswith("id"):
                try: max_id = max(max_id, int(k[2:]))
                except: pass
    kid = f"id{max_id + 1}"
    new_kf["id"] = kid
    new_kf["selected_image_path"] = None
    
    # Insert After
    try:
        idx = seq["keyframe_order"].index(kf_id)
        seq["keyframe_order"].insert(idx + 1, kid)
    except ValueError:
        seq["keyframe_order"].append(kid)
        
    seq["keyframes"][kid] = new_kf
    
    # Refresh Videos
    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    _refresh_video_chain(seq, d, data)
    
    return data, kid




def _set_open_flag(data: Dict[str, Any], seq_id: str, which: str, value: bool) -> Dict[str, Any]:
    data = _ensure_project(data)
    if seq_id not in data.get("sequences", {}):
        return data
    
    seq = data["sequences"][seq_id]
    old_value = bool(seq.get("video_plan", {}).get(which, which == "open_end"))  # open_end defaults True
    new_value = bool(value)
    
    # Handle open_start toggle - shift video_order to preserve content positions
    if which == "open_start" and old_value != new_value:
        vid_order = seq.get("video_order", [])
        videos = seq.get("videos", {})
        
        if new_value and not old_value:
            # Toggling ON: insert placeholder at front, existing videos shift to correct gaps
            # Create a temporary marker - refresh will create the actual video
            vid_order.insert(0, "__placeholder__")
        elif old_value and not new_value:
            # Toggling OFF: remove first video (the open_start one)
            if vid_order:
                removed_id = vid_order.pop(0)
                videos.pop(removed_id, None)
    
    seq["video_plan"][which] = new_value
    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    _refresh_video_chain(seq, d, data)
    return data


def x_eh_flip_orientation(project_dict: dict, nid: str):
    """
    Flip sequence orientation for single-keyframe sequences.
    Swaps open_start <-> open_end and updates video endpoints without rebuilding.
    Preserves video prompts and media.
    """
    data = _ensure_project(project_dict if isinstance(project_dict, dict) else {})
    seq_id, _, _ = parse_nid(nid)
    seq = data.get("sequences", {}).get(seq_id)
    
    if not seq:
        return data
    
    kf_order = seq.get("keyframe_order", [])
    vid_order = seq.get("video_order", [])
    videos = seq.get("videos", {})
    
    # Only works for single keyframe, single video
    if len(kf_order) != 1 or len(vid_order) != 1:
        gr.Warning("Flip only works with exactly 1 keyframe and 1 video")
        return data
    
    kf_id = kf_order[0]
    vid_id = vid_order[0]
    vid = videos.get(vid_id)
    
    if not vid:
        return data
    
    video_plan = seq.setdefault("video_plan", {})
    old_open_start = video_plan.get("open_start", False)
    old_open_end = video_plan.get("open_end", True)
    
    # Swap the flags
    video_plan["open_start"] = old_open_end
    video_plan["open_end"] = old_open_start
    
    # Update video endpoints to match (no rebuild, preserve data)
    if video_plan["open_start"]:
        vid["start_keyframe_id"] = None
        vid["end_keyframe_id"] = kf_id
    else:
        vid["start_keyframe_id"] = kf_id
        vid["end_keyframe_id"] = None
    
    return data


def _canonicalize_nid_for_ui(data: dict, nid: str | None) -> str | None:
    if not nid: return None
    rows = _get_filtered_outline_rows(data, nid)
    valid_ids = [v for (_, v) in rows]
    return nid if nid in valid_ids else (valid_ids[0] if valid_ids else None)



def _refresh_left(project_dict: dict, keep_id: str = "") -> Tuple[Any, str, Any]:
    # data = _ensure_project(_loads(project_dict))
    data = _ensure_project(project_dict if isinstance(project_dict, dict) else {})
    rows = _get_filtered_outline_rows(data, keep_id)
    
    # Fallback: If filtering returned nothing but we have data, try full rows
    if not rows and data.get("sequences"):
        rows = _rows_with_times(data)

    ids = [v for (_, v) in rows]
    

    if not rows:
         return gr.update(), (keep_id or None), gr.update()

    sel = keep_id if keep_id in ids else (ids[0] if ids else None)
    left = gr.update(choices=rows, value=sel)
    proj = gr.update(value=_project_len_text(data))
    return left, sel, proj



def _rehydrate_if_changed(project_dict: dict, keep_id: str, old_sig: str):
    print(f"[REHYDRATE] keep_id={keep_id}, old_sig={old_sig[:30] if old_sig else 'None'}...")
    # data = _ensure_project(_loads(project_dict))
    data = _ensure_project(project_dict if isinstance(project_dict, dict) else {})
    
    # Pre-check: If we have a selected ID but the data looks empty, block immediately
    has_sequences = bool(data.get("sequences"))
    if keep_id and not has_sequences:
         # Block update to prevent clearing UI
         return gr.update(), keep_id, gr.update(), old_sig

    new_sig = _outline_signature(data)
    if new_sig == (old_sig or ""): return gr.update(), (keep_id or ""), gr.update(), (old_sig or new_sig)
    
    # Robustness: Check for "Signature Collapse"
    is_complex_old = old_sig and len(old_sig) > 15 and "s=" in old_sig
    is_simple_new = not new_sig or (len(new_sig) < 15 and "s=" not in new_sig)
    
    if is_complex_old and is_simple_new:
        print("[Rehydrate] Blocked sidebar clear: Detected signature collapse.")
        return gr.update(), (keep_id or ""), gr.update(), old_sig

    # Use the raw keep_id as a fallback if canonicalization fails but we know we have data
    canon_id = _canonicalize_nid_for_ui(data, keep_id)
    target_id = canon_id if canon_id else keep_id 
    
    left, sel, proj = _refresh_left(data, keep_id=target_id or "")
    return left, sel, proj, new_sig

def _update_clear_button_label(project_dict: dict):
    # data = _ensure_project(_loads(project_dict))
    data = _ensure_project(project_dict if isinstance(project_dict, dict) else {})
    default_dur = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    return gr.update(value=f"Reset to default ({int(round(default_dur))} sec)")


# ---- GALLERY GETTERS ----

def _get_vid_gallery_files(data: Dict, vid_id: str) -> List[str]:
    _, kind, seq, seq_id = _resolve_node_context(data, vid_id)
    if kind != "vid": return []
    
    vid_dir = _get_vid_dir(data, seq_id, vid_id)
    if not vid_dir or not vid_dir.exists(): return []
    try:
        files = [p for p in vid_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"]
        return [str(p) for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]
    except OSError: return []

def _get_kf_gallery_images(data: Dict, kf_id: str) -> List[str]:
    _, kind, seq, seq_id = _resolve_node_context(data, kf_id)
    if kind != "kf": return []
    
    kf_dir = _get_kf_dir(data, seq_id, kf_id)
    if not kf_dir or not kf_dir.exists(): return []
    
    img_exts = {".png", ".jpg", ".jpeg", ".webp"}
    try:
        files = [p for p in kf_dir.iterdir() if p.is_file() and p.suffix.lower() in img_exts]
        return [str(p) for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]
    except OSError: return []

def _sequence_len_text(data: Dict[str, Any], seq_id: str) -> str:
    seq = data.get("sequences", {}).get(seq_id)
    if not seq: return ""
    default_dur = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    return f"Sequence length: {int(round(_sequence_effective_length(seq, default_dur)))} sec"



# ---- METADATA READERS ----

def _read_metadata_png(path: str) -> dict | None:
    try:
        if not path or not os.path.exists(path): return None
        with Image.open(path) as img:
            meta = img.info.get("the_machine_snapshot")
            return json.loads(meta) if meta else None
    except Exception as e:
        print(f"Error reading PNG metadata: {e}")
        return None

def _format_cn_settings(cn: dict) -> str:
    """Format CN settings: 'Pose 0.8/1.0, Shape 0.5/0.5' (omit if off)"""
    parts = []
    labels = {"1": "Pose", "2": "Shape", "3": "Outline"}
    for idx, label in labels.items():
        unit = cn.get(idx, {})
        if unit.get("switch") == "On":
            strength = unit.get("strength", 1.0)
            end = unit.get("end_percent", 1.0)
            parts.append(f"{label} {strength}/{end}")
    return ", ".join(parts) if parts else "None"

def _format_lora_norm(lora_norm: dict) -> str:
    """Format LoRA normalization: 'Foregrounds 1.0, Backgrounds 0.8' (omit if off)"""
    parts = []
    if lora_norm.get("fg_enabled"):
        parts.append(f"Foregrounds {lora_norm.get('fg_max', 1.0)}")
    if lora_norm.get("bg_enabled"):
        parts.append(f"Backgrounds {lora_norm.get('bg_max', 1.0)}")
    return ", ".join(parts) if parts else "None"

def _merge_negatives(*parts) -> str:
    """Merge negative prompt parts, filtering empty strings."""
    return ", ".join(p.strip() for p in parts if p and p.strip())

def _eh_load_execution_info_kf(selected_path: str):
    """Load execution info from image metadata and format as Markdown."""
    if not selected_path:
        return gr.update(value="*No file selected*")
    snapshot = _read_metadata_png(selected_path)
    if not snapshot:
        return gr.update(value="*No metadata found*")
    
    gen = snapshot.get("generation", {})
    proj = snapshot.get("project_context", {})
    item = snapshot.get("item_data", {})
    
    seed = gen.get("seed", "")
    prompt = (gen.get("executed_prompt", "") or "").replace("__", r"\_\_")
    steps = proj.get("steps", "")
    cfg = proj.get("cfg", "")
    sampler = proj.get("sampler", "")
    scheduler = proj.get("scheduler", "")
    model = proj.get("model", "")
    # timestamp = snapshot.get("meta", {}).get("timestamp", "")
    pose = item.get("pose", "")
    
    # CN settings
    cn = item.get("controlnet_settings", {})
    cn_str = _format_cn_settings(cn)
    
    # LoRA normalization
    lora_norm = proj.get("lora_normalization", {})
    lora_str = _format_lora_norm(lora_norm)
    
    # Combined negatives (recompute from parts)
    proj_negs = proj.get("negatives", {})
    item_negs = item.get("negatives", {})
    combined_neg = _merge_negatives(
        proj_negs.get("global", ""),
        proj_negs.get("keyframes_all", ""),
        item_negs.get("heal", "") if isinstance(item_negs, dict) else ""
    )
    
    lines = [
        f"**Seed:** {seed}",
        f"**Prompt:** {prompt}",
        f"**Steps:** {steps} | **CFG:** {cfg} | **Sampler:** {sampler} | **Scheduler:** {scheduler}",
        f"**Model:** {model}",
        # f"**Timestamp:** {timestamp}",
        f"**Pose:** {pose}",
        f"**ControlNet:** {cn_str}",
        f"**LoRA Norm:** {lora_str}",
        f"**Negatives:** {combined_neg}",
    ]
    
    # 2CHAR: add left/right/heal negatives if present
    if isinstance(item_negs, dict) and (item_negs.get("left") or item_negs.get("right") or item_negs.get("heal")):
        lines.append("")
        lines.append("**Character Negatives:**")
        if item_negs.get("left"):
            lines.append(f"- Left: {item_negs['left']}")
        if item_negs.get("right"):
            lines.append(f"- Right: {item_negs['right']}")
        if item_negs.get("heal"):
            lines.append(f"- Heal: {item_negs['heal']}")
    
    return gr.update(value="  \n".join(lines))

def _eh_load_execution_info_vid(selected_path: str):
    """Load execution info from video metadata and format as Markdown."""
    if not selected_path:
        return gr.update(value="*No file selected*")
    print(f"[DEBUG VID META] Reading: {selected_path}")
    snapshot = _read_metadata_mp4(selected_path)
    print(f"[DEBUG VID META] Result: {snapshot}")
    if not snapshot:
        return gr.update(value="*No metadata found*")
    
    gen = snapshot.get("generation", {})
    proj = snapshot.get("project_context", {})
    item = snapshot.get("item_data", {})
    
    seed = gen.get("seed", "")
    prompt = (gen.get("executed_prompt", "") or "").replace("__", r"\_\_")
    steps = proj.get("steps", "")
    
    # Combined negatives (recompute)
    combined_neg = _merge_negatives(
        proj.get("negatives", {}).get("global", "") if isinstance(proj.get("negatives"), dict) else "",
        proj.get("negatives", {}).get("inbetween_all", "") if isinstance(proj.get("negatives"), dict) else "",
        item.get("negative_prompt", "")
    )
    
    lines = [
        f"**Seed:** {seed}",
        f"**Prompt:** {prompt}",
        f"**Steps:** {steps}",
        f"**Negatives:** {combined_neg}",
    ]
    
    return gr.update(value="  \n".join(lines))

def _read_metadata_mp4(path: str) -> dict | None:
    try:
        if not path or not os.path.exists(path): return None
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[MP4 META] ffprobe failed: {result.stderr}")
            return None
        data = json.loads(result.stdout)
        tags = data.get("format", {}).get("tags", {})
        if not tags:
            print(f"[MP4 META] No tags found in: {path}")
            return None
        comment = tags.get("comment") or tags.get("description") or tags.get("prompt")
        if not comment:
            print(f"[MP4 META] No recognized metadata tag. Available tags: {list(tags.keys())}")
            return None
        parsed = json.loads(comment)
        # Check if it's our format (has generation key) vs ComfyUI native format
        if not isinstance(parsed, dict) or "generation" not in parsed:
            print(f"[MP4 META] Metadata exists but not THM format")
            return None
        return parsed
    except json.JSONDecodeError as e:
        print(f"[MP4 META] JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"[MP4 META] Error: {e}")
        return None



def _eh_load_kf_params(project_dict: dict, kf_id: str, selected_path_from_ui: str):
    data = project_dict
    node, kind, _, _ = _resolve_node_context(data, kf_id)
    
    no_change = [gr.update()] * 26
    
    if kind != "kf": return data, *no_change

    real_path = _resolve_real_path_from_filename(data, kf_id, selected_path_from_ui)
    if not real_path:
        saved = node.get("selected_image_path")
        if saved and os.path.exists(saved): real_path = saved
    if not real_path: real_path = selected_path_from_ui


    snapshot = _read_metadata_png(real_path)
    if not snapshot or "item_data" not in snapshot:
        return data, *([gr.update()] * 25), f"Error: No valid metadata found."

    item = snapshot["item_data"]
    blocked = {"seed", "noise_seed", "selected_image_path", "image_iterations_override", "workflow_json", "join_smoothing_level", "join_offset", "force_generate", "id", "type", "sequence_id"}
    
    for k, v in item.items():
        if k not in blocked:
            node[k] = copy.deepcopy(v)

    kf = node
    cn = kf.get("controlnet_settings", {})
    negs = kf.get("negatives", {})
    chars = kf.get("characters", ["", ""])
    
    def _cn_vals(idx):
        c = cn.get(str(idx), {})
        return (
            gr.update(value=(c.get("switch") == "On")),
            gr.update(value=float(c.get("strength", 1.0))),
            gr.update(value=float(c.get("start_percent", 0.0))),
            gr.update(value=float(c.get("end_percent", 1.0)))
        )
        
    project_chars = data.get("project", {}).get("characters", [])
    valid_ids = {c.get("id") for c in project_chars if c.get("id")}
    def _validate_char(c): return c if c and c in valid_ids else None
    
    pose_path = kf.get("pose", "")
    use_animal = kf.get("use_animal_pose", DEFAULT_KF_USE_ANIMAL_POSE)
    
    poses_dir = get_project_poses_dir(project_dict)
    gallery_items = get_pose_gallery_list(str(poses_dir)) if poses_dir else []
    selected_index = _resolve_gallery_index(pose_path, gallery_items)

    # STRICT ORDER: Must match app.py kf_load_outputs (27 items total)
    return (
        data,
        *_cn_vals(1), *_cn_vals(2), *_cn_vals(3),
        gr.update(value=_validate_char(chars[0] if len(chars)>0 else None)),
        gr.update(value=_validate_char(chars[1] if len(chars)>1 else None)),
        gr.update(value=kf.get("layout", "")),
        gr.update(value=negs.get("left", "")), gr.update(value=negs.get("right", "")), gr.update(value=negs.get("heal", "")),
        gr.update(value=pose_path), gr.update(value=pose_path or None),
        gr.update(value=use_animal),
        gr.update(value=_resolve_aux_image(pose_path, "poses", data)),
        gr.update(value=_resolve_aux_image(pose_path, "shapes", data)),
        gr.update(value=_resolve_aux_image(pose_path, "outlines", data)),
        gr.update(value=gallery_items, selected_index=selected_index),
        f"Successfully loaded parameters from {os.path.basename(real_path)}"
    )

def _get_vid_dur_ui_vals(val: Any, default_dur: int) -> Tuple[str | None, str, str]:
    """Centralized logic for video duration UI labels and values."""
    # Ensure radio_value is None if val is missing, which clears the selection in Gradio
    radio_value = str(int(float(val))) if val is not None else None
    
    # Display logic for label and button
    label_text = f"Length: {radio_value} seconds" if val else f"Length: {default_dur} seconds (from project default)"
    reset_label = f"Reset to Default ({default_dur} seconds)"
    return radio_value, label_text, reset_label

def _eh_load_vid_params(project_dict: dict, vid_id: str, selected_path: str):
    data = project_dict if isinstance(project_dict, dict) else {}
    node, kind, _, _ = _resolve_node_context(data, vid_id)
    no_change = [gr.update()] * 4
    
    if kind != "vid" or not selected_path: return data, *no_change
    
    snapshot = _read_metadata_mp4(selected_path)
    if not snapshot or "item_data" not in snapshot: return data, *no_change
    
    item = snapshot["item_data"]
    blocked = {"start_keyframe_id", "end_keyframe_id", "seed", "selected_video_path", "force_generate", "id", "type", "sequence_id"}

    for k, v in item.items():
        if k not in blocked:
            node[k] = copy.deepcopy(v)

    # Trace: If the restored metadata does NOT have a length, remove it from the project node
    if "duration_override_sec" not in item:
        node.pop("duration_override_sec", None)

    restored_val = node.get("duration_override_sec")
    default_dur = int(float(data.get("project", {}).get("inbetween_generation", {}).get("duration_default_sec", 3.0)))
    
    # If restored_val is None, radio_value will be None (clearing the UI)
    radio_value, label_text, reset_label = _get_vid_dur_ui_vals(restored_val, default_dur)
    
           
    return (
        data,
        gr.update(value=radio_value, label=label_text),
        gr.update(value=node.get("inbetween_prompt", "")),
        gr.update(value=node.get("negative_prompt", "")),
        gr.update(value=reset_label)
    )

# ---- EVENT HANDLERS (SIMPLE WRAPPERS) ----

def _eh_add_sequence(project_dict: dict, _cur_sel: str):
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    data, nid = _add_sequence(data)
    left, sel, proj = _refresh_left(data, keep_id=nid)
    seq_len_update = gr.update(value=_sequence_len_text(data, sel), visible=True)
    return data, left, sel, proj, seq_len_update

def _eh_delete_sequence(project_dict: dict, nid: str):
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    data, new_sel = _delete_sequence(data, nid)
    left, sel, proj = _refresh_left(data, keep_id=new_sel)
    seq_len_update = gr.update(value=_sequence_len_text(data, sel), visible=bool(sel and sel.startswith("seq")))
    return data, left, sel, proj, seq_len_update


def _eh_duplicate_sequence(project_dict: dict, nid: str):
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    data, new_sel = _duplicate_sequence(data, nid)
    left, sel, proj = _refresh_left(data, keep_id=new_sel)
    return data, left, sel, proj


def _eh_move_sequence_up(project_dict: dict, nid: str):
    """Event handler for moving a sequence up (earlier) in the order."""
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    # Find the sequence ID if nid is a child (keyframe/video)
    seq_id = nid
    if nid not in data.get("sequences", {}):
        # nid might be a keyframe or video - find parent sequence
        for sid, seq in data.get("sequences", {}).items():
            if nid in seq.get("keyframes", {}) or nid in seq.get("videos", {}):
                seq_id = sid
                break
    
    data, _ = _move_sequence_up(data, seq_id)
    left, sel, proj = _refresh_left(data, keep_id=seq_id)
    return data, left, sel, proj


def _eh_move_sequence_down(project_dict: dict, nid: str):
    """Event handler for moving a sequence down (later) in the order."""
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    # Find the sequence ID if nid is a child (keyframe/video)
    seq_id = nid
    if nid not in data.get("sequences", {}):
        # nid might be a keyframe or video - find parent sequence
        for sid, seq in data.get("sequences", {}).items():
            if nid in seq.get("keyframes", {}) or nid in seq.get("videos", {}):
                seq_id = sid
                break
    
    data, _ = _move_sequence_down(data, seq_id)
    left, sel, proj = _refresh_left(data, keep_id=seq_id)
    return data, left, sel, proj


def _eh_move_keyframe_up(project_dict: dict, nid: str):
    """Event handler for moving a keyframe up (earlier) in its sequence."""
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    
    # nid should be a keyframe ID - find its parent sequence
    seq_id = None
    kf_id = nid
    for sid, seq in data.get("sequences", {}).items():
        if nid in seq.get("keyframes", {}):
            seq_id = sid
            break
    
    if not seq_id:
        # nid might be a sequence - no-op for keyframe move
        return data, gr.update(), nid, gr.update()
    
    data, _ = _move_keyframe_up(data, seq_id, kf_id)
    left, sel, proj = _refresh_left(data, keep_id=kf_id)
    return data, left, sel, proj


def _eh_move_keyframe_down(project_dict: dict, nid: str):
    """Event handler for moving a keyframe down (later) in its sequence."""
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    
    # nid should be a keyframe ID - find its parent sequence
    seq_id = None
    kf_id = nid
    for sid, seq in data.get("sequences", {}).items():
        if nid in seq.get("keyframes", {}):
            seq_id = sid
            break
    
    if not seq_id:
        # nid might be a sequence - no-op for keyframe move
        return data, gr.update(), nid, gr.update()
    
    data, _ = _move_keyframe_down(data, seq_id, kf_id)
    left, sel, proj = _refresh_left(data, keep_id=kf_id)
    return data, left, sel, proj


def _eh_open_flag(project_dict: dict, loaded_nid: str, loaded_proj: str, which: str, value: bool):
    data = _ensure_project(project_dict if isinstance(project_dict, dict) else {})
    if not _check_ownership(data, loaded_nid, loaded_proj):
        left, sel, proj = _refresh_left(data, keep_id=loaded_nid)
        return data, left, sel, proj, gr.update()
    data = _set_open_flag(data, loaded_nid, which, bool(value))
    left, sel, proj = _refresh_left(data, keep_id=loaded_nid)
    seq_len_update = gr.update(value=_sequence_len_text(data, loaded_nid), visible=True)
    return data, left, sel, proj, seq_len_update


# def _eh_flip_orientation(project_dict: dict, nid: str):
#     """
#     Flip sequence orientation for single-keyframe sequences.
#     Swaps open_start <-> open_end and updates video endpoints without rebuilding.
#     Preserves video prompts and media.
#     """
#     data = _ensure_project(project_dict if isinstance(project_dict, dict) else {})
    
#     # nid could be seq_id directly, or a child node - find the sequence
#     seq_id = nid
#     if seq_id not in data.get("sequences", {}):
#         # Try parsing as child node
#         seq_id, _, _ = parse_nid(nid)
    
#     seq = data.get("sequences", {}).get(seq_id)
    
#     if not seq:
#         left, sel, proj = _refresh_left(data, keep_id=nid)
#         seq_len_update = gr.update(value=_sequence_len_text(data, nid), visible=True)
#         return data, left, sel, proj, seq_len_update
    
#     kf_order = seq.get("keyframe_order", [])
#     vid_order = seq.get("video_order", [])
#     videos = seq.get("videos", {})
    
#     # Only works for single keyframe, single video
#     if len(kf_order) != 1 or len(vid_order) != 1:
#         gr.Warning("Flip only works with exactly 1 keyframe and 1 video")
#         left, sel, proj = _refresh_left(data, keep_id=nid)
#         seq_len_update = gr.update(value=_sequence_len_text(data, nid), visible=True)
#         return data, left, sel, proj, seq_len_update
    
#     kf_id = kf_order[0]
#     vid_id = vid_order[0]
#     vid = videos.get(vid_id)
    
#     if not vid:
#         left, sel, proj = _refresh_left(data, keep_id=nid)
#         seq_len_update = gr.update(value=_sequence_len_text(data, nid), visible=True)
#         return data, left, sel, proj, seq_len_update
    
#     video_plan = seq.setdefault("video_plan", {})
#     old_open_start = video_plan.get("open_start", False)
#     old_open_end = video_plan.get("open_end", True)
    
#     # Swap the flags
#     video_plan["open_start"] = old_open_end
#     video_plan["open_end"] = old_open_start
    
#     # Update video endpoints to match (no rebuild, preserve data)
#     if video_plan["open_start"]:
#         vid["start_keyframe_id"] = None
#         vid["end_keyframe_id"] = kf_id
#     else:
#         vid["start_keyframe_id"] = kf_id
#         vid["end_keyframe_id"] = None
    
#     left, sel, proj = _refresh_left(data, keep_id=nid)
#     seq_len_update = gr.update(value=_sequence_len_text(data, nid), visible=True)
#     return data, left, sel, proj, seq_len_update


def _eh_flip_orientation(project_dict: dict, loaded_nid: str, loaded_proj: str):
    """
    Flip sequence orientation.
    ...
    """
    data = _ensure_project(project_dict if isinstance(project_dict, dict) else {})
    
    if not _check_ownership(data, loaded_nid, loaded_proj):
        left, sel, proj = _refresh_left(data, keep_id=loaded_nid)
        return data, left, sel, proj, gr.update()
    
    # nid could be seq_id directly, or a child node - find the sequence
    seq_id = loaded_nid
    if seq_id not in data.get("sequences", {}):
        seq_id, _, _ = parse_nid(loaded_nid)
    
    seq = data.get("sequences", {}).get(seq_id)
    
    if not seq:
        left, sel, proj = _refresh_left(data, keep_id=loaded_nid )
        seq_len_update = gr.update(value=_sequence_len_text(data, loaded_nid ), visible=True)
        return data, left, sel, proj, seq_len_update
    
    video_plan = seq.setdefault("video_plan", {})
    old_open_start = video_plan.get("open_start", False)
    old_open_end = video_plan.get("open_end", True)
    
    # Must have exactly one open end
    if old_open_start == old_open_end:
        gr.Warning("Flip requires exactly one open end (not both or neither)")
        left, sel, proj = _refresh_left(data, keep_id=loaded_nid )
        seq_len_update = gr.update(value=_sequence_len_text(data, loaded_nid ), visible=True)
        return data, left, sel, proj, seq_len_update
    
    # Atomically swap flags
    video_plan["open_start"] = old_open_end
    video_plan["open_end"] = old_open_start
    
    # Let refresh reassign endpoints (same gap count, videos stay in position)
    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    _refresh_video_chain(seq, d, data)
    
    left, sel, proj = _refresh_left(data, keep_id=loaded_nid )
    seq_len_update = gr.update(value=_sequence_len_text(data, loaded_nid ), visible=True)
    return data, left, sel, proj, seq_len_update

def _eh_add_kf(project_dict: dict, nid: str):
    # nid might be a sequence or a child. Find parent seq.
    # Handle both dict and string (JSON) input
    print(f"\n=== ADD KEYFRAME CALLED ===")
    print(f"nid received: '{nid}' (type: {type(nid).__name__})")
    print(f"project_dict type: {type(project_dict).__name__}")
    if isinstance(project_dict, str):
        try:
            import json
            data = _ensure_project(json.loads(project_dict))
        except:
            data = _ensure_project({})
    elif isinstance(project_dict, dict):
        data = _ensure_project(project_dict)
    else:
        data = _ensure_project({})
    
    # Check what sequences exist
    sequences = data.get("sequences", {})
    print(f"Sequences in data: {list(sequences.keys())}")
    
    _, _, _, seq_id = _resolve_node_context(data, nid)
    print(f"Resolved seq_id: '{seq_id}'")
    
    if not seq_id: 
        print(f"❌ NO SEQ_ID - returning early")
        print(f"=== END ADD KEYFRAME ===\n")
        return data, gr.update(), nid, gr.update()
    
    print(f"✓ Adding keyframe to sequence '{seq_id}'")
    data, new_sel = _add_keyframe(data, seq_id)
    left, sel, proj = _refresh_left(data, keep_id=new_sel)
    
    print(f"✓ Keyframe added: {new_sel}")
    print(f"=== END ADD KEYFRAME ===\n")
    
    return data, left, sel, proj


def _eh_duplicate_kf(project_dict: dict, nid: str):
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    _, kind, _, seq_id = _resolve_node_context(data, nid)
    if kind != "kf": return data, gr.update(), nid, gr.update()
    
    data, new_sel = _duplicate_keyframe(data, seq_id, nid)
    left, sel, proj = _refresh_left(data, keep_id=new_sel)
    return data, left, sel, proj

def _eh_delete_kf(project_dict: dict, nid: str):
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    _, kind, _, seq_id = _resolve_node_context(data, nid)
    if kind != "kf": return data, gr.update(), nid, gr.update()
    
    data, new_sel = _delete_keyframe(data, seq_id, nid)
    left, sel, proj = _refresh_left(data, keep_id=new_sel)
    return data, left, sel, proj


def _eh_kf_fields(project_dict: dict, loaded_nid: str, loaded_proj: str, pose_ui: str,
                cn_pose_enable: bool, cn_pose_animal: bool, kf_flip_horiz: bool, kf_flip_vert: bool, cn_pose_strength: float, cn_pose_start: float, cn_pose_end: float,
                cn_shape_enable: bool, cn_shape_strength: float, cn_shape_start: float, cn_shape_end: float,
                cn_outline_enable: bool, cn_outline_strength: float, cn_outline_start: float, cn_outline_end: float,
                prompt: str, template: str, workflow: str,
                negL: str, negR: str, negH: str,
                kf_join_smoothing: int, kf_join_offset: int,
                charL: str, charR: str): 

    print(f"[KF_FIELDS] loaded_nid={loaded_nid}, pose={pose_ui[:30] if pose_ui else 'None'}")
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    data = copy.deepcopy(data)
    
    if not _check_ownership(data, loaded_nid, loaded_proj): return data
    kf, kind, _, _ = _resolve_node_context(data, loaded_nid)
    if kind != "kf": return data

    # Pose Path Logic
    if pose_ui:
        poses_dir = get_project_poses_dir(project_dict)
        if poses_dir:
            # Fix: Prioritize persistent project path over temp Gradio path
            target_path = Path(poses_dir) / os.path.basename(pose_ui)
            if target_path.exists():
                kf["pose"] = str(target_path)
            elif os.path.isfile(pose_ui):
                kf["pose"] = pose_ui
            else:
                kf["pose"] = str(target_path)
        else:
            kf["pose"] = pose_ui
    else:
        kf["pose"] = ""

    kf["use_animal_pose"] = bool(cn_pose_animal)
    kf["join_smoothing_level"] = int(kf_join_smoothing or 1)
    kf["join_offset"] = int(kf_join_offset or 0)
    kf["pose_flip_horizontal"] = bool(kf_flip_horiz)
    kf["pose_flip_vertical"] = bool(kf_flip_vert)
    kf["layout"] = prompt or ""; kf["template"] = template or ""; kf["workflow_json"] = workflow or ""
    kf.setdefault("negatives", {})["left"] = negL or ""; kf["negatives"]["right"] = negR or ""; kf["negatives"]["heal"] = negH or ""
    kf["characters"] = [(charL or ""), (charR or "")]

    cn = kf.setdefault("controlnet_settings", copy.deepcopy(DEFAULT_KF_CN_SETTINGS))
    cn.setdefault("1", {})["switch"] = "On" if cn_pose_enable else "Off"
    cn["1"]["strength"] = float(cn_pose_strength); cn["1"]["start_percent"] = float(cn_pose_start); cn["1"]["end_percent"] = float(cn_pose_end)
    cn.setdefault("2", {})["switch"] = "On" if cn_shape_enable else "Off"
    cn["2"]["strength"] = float(cn_shape_strength); cn["2"]["start_percent"] = float(cn_shape_start); cn["2"]["end_percent"] = float(cn_shape_end)
    cn.setdefault("3", {})["switch"] = "On" if cn_outline_enable else "Off"
    cn["3"]["strength"] = float(cn_outline_strength); cn["3"]["start_percent"] = float(cn_outline_start); cn["3"]["end_percent"] = float(cn_outline_end)

    return data


def _eh_vid_fields(project_dict: dict, loaded_nid: str, loaded_proj: str, length: str, prompt: str, neg: str, is_reset: bool = False):
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    data = copy.deepcopy(data)
    
    if not _check_ownership(data, loaded_nid, loaded_proj): return data
    v, kind, _, _ = _resolve_node_context(data, loaded_nid)
    if kind != "vid": return data

    if is_reset:
        v.pop("duration_override_sec", None)
    else:
        if length is None: 
            v.pop("duration_override_sec", None)
        else:
            try: 
                v["duration_override_sec"] = int(length)
            except (ValueError, TypeError): 
                v.pop("duration_override_sec", None)
                
    v["inbetween_prompt"] = prompt or ""
    v["negative_prompt"] = neg or ""
    return data

def _eh_reset_vid_length(project_dict, loaded_nid, loaded_proj, prompt, neg):
    # 1. Update the JSON state (removes the key)
    new_data = _eh_vid_fields(project_dict, loaded_nid, loaded_proj, None, prompt, neg, is_reset=True)
    # 2. Return the state and the ID to trigger the refresh
    return new_data, loaded_nid

def _eh_seq_text_fields(project_dict: dict, loaded_nid: str, loaded_proj: str, setting_val: str, style_val: str, action_val: str):
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    data = copy.deepcopy(data)
    
    if not _check_ownership(data, loaded_nid, loaded_proj): return data
    seq, kind, _, _ = _resolve_node_context(data, loaded_nid)
    if kind != "seq": return data
    
    seq["setting_prompt"] = setting_val or ""
    seq["style_prompt"] = style_val or ""
    seq["action_prompt"] = action_val or ""
    return data

def _update_seq_field(project_dict: dict, loaded_nid: str, loaded_proj: str, key: str, value: Any):
    data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    data = copy.deepcopy(data)
    
    if not _check_ownership(data, loaded_nid, loaded_proj): return data
    seq, kind, _, _ = _resolve_node_context(data, loaded_nid)
    if kind == "seq":
        seq[key] = value
    return data



# ---- OTHER HANDLERS ----

def _eh_inject_lora(pre_txt: str, nid: str, lora_path: str, current_prompt: str):

    if not lora_path: return gr.update(), gr.update(), gr.update()
    
    new_prompt = current_prompt or ""
    try:
        filename = os.path.basename(lora_path)
        lora_tag = f"__lora:{filename}:1.0__ "
        new_prompt = lora_tag + new_prompt
    except Exception: return gr.update(), gr.update(), gr.update()

    return gr.update(), gr.update(value=new_prompt), gr.update(value=None)



def _eh_vid_lora_changed(pre_txt: str, loaded_nid: str, loaded_proj: str, lora_path: str, current_prompt: str):
    data = pre_txt if isinstance(pre_txt, dict) else {}
    if not lora_path: return pre_txt, gr.update(), gr.update()
    
    if not _check_ownership(data, loaded_nid, loaded_proj): return data, gr.update(), gr.update()
    new_prompt = current_prompt
    try:
        filename = os.path.basename(lora_path)
        lora_tag = f"__lora:{filename}:1.0__ "
        new_prompt = lora_tag + current_prompt
    except Exception: return pre_txt, gr.update(), gr.update()

    v, kind, _, _ = _resolve_node_context(data, loaded_nid)
    if kind == "vid":
        if v.get("inbetween_prompt") != new_prompt:
            v["inbetween_prompt"] = new_prompt
    return data, gr.update(value=new_prompt), gr.update(value=None)



def _resolve_aux_image(base_path: str | None, subfolder: str, project_dict: dict = None) -> str | None:
    if not base_path: return None
    try:
        import re
        p = Path(base_path)
        
        # Gradio sanitizes filenames, removing parentheses from names like "file_(2).png" → "file_2.png"
        # We need to convert back: "file_2" → "file_(2)" to match actual disk files
        def unsanitize_name(name: str) -> str:
            """Convert Gradio-sanitized name back to original with parentheses."""
            stem = Path(name).stem
            suffix = Path(name).suffix
            # Match patterns like "_2", "_3", "_99" at the end
            match = re.search(r'_(\d+)$', stem)
            if match:
                # Replace "_2" with "_(2)"
                base = stem[:match.start()]
                num = match.group(1)
                return f"{base}_({num}){suffix}"
            return name
        
        # 1. Try resolving relative to project poses dir
        if project_dict:
            from helpers import get_project_poses_dir
            poses_dir = get_project_poses_dir(project_dict)
            if poses_dir and poses_dir.exists():
                target_dir = poses_dir / subfolder
                if target_dir.exists():
                    # Try exact match first
                    exact = target_dir / p.name
                    if exact.exists(): return str(exact)
                    
                    # Try unsanitized name (convert "_2" back to "_(2)")
                    unsanitized = target_dir / unsanitize_name(p.name)
                    if unsanitized.exists(): return str(unsanitized)
                    
                    # Fallback: stem match
                    stem = p.stem.lower()
                    for child in target_dir.iterdir():
                        if child.is_file() and child.stem.lower() == stem: return str(child)

        # 2. Try resolving relative to parent (if path is absolute/existing)
        if p.is_absolute() and p.parent.exists():
            poses_root = p.parent
            target_dir = poses_root / subfolder
            if target_dir.exists():
                exact = target_dir / p.name
                if exact.exists(): return str(exact)
                
                unsanitized = target_dir / unsanitize_name(p.name)
                if unsanitized.exists(): return str(unsanitized)
                
                stem = p.stem.lower()
                for child in target_dir.iterdir():
                    if child.is_file() and child.stem.lower() == stem: return str(child)
                    
        return None
    except Exception: return None

def _eh_handle_pose_change(pose_path: str, current_workflow_path: str, project_dict: dict, nid: str):
    # Normalize pose path into a stable on-disk path (so comparisons are meaningful).
    p = ""
    if pose_path:
        try:
            from helpers import get_project_poses_dir
            poses_dir = get_project_poses_dir(project_dict)
            path_obj = Path(pose_path)
            if not path_obj.is_absolute() and poses_dir:
                p = str(poses_dir / path_obj.name)
            else:
                p = pose_path
        except Exception:
            p = pose_path

    # Determine whether this is a REAL pose change (user picked a new pose)
    # vs a pose being re-set during keyframe navigation/load.
    stored_pose = ""
    try:
        node, kind, _, _ = _resolve_node_context(project_dict if isinstance(project_dict, dict) else {}, nid)
        if kind == "kf" and isinstance(node, dict):
            stored_pose = node.get("pose", "") or ""
    except Exception:
        stored_pose = ""

    # def _norm(s: str) -> str:
    #     try:
    #         return os.path.abspath(os.path.normpath(s))
    #     except Exception:
    #         return s or ""

    # is_real_pose_change = False
    # if p or stored_pose:
    #     # If pose differs from what's stored on the keyframe, it's a real user-driven change.
    #     is_real_pose_change = (_norm(p) != _norm(stored_pose))
    def _basename(s: str) -> str:
        try:
            return os.path.basename(s).lower() if s else ""
        except Exception:
            return ""

    is_real_pose_change = False
    if p or stored_pose:
        # Compare by filename only - same file can appear via temp path or stored path
        is_real_pose_change = (_basename(p) != _basename(stored_pose))
    print(f"[DEBUG POSE] p={p}, stored_pose={stored_pose}, nid={nid}, is_real_change={is_real_pose_change}")

    # Apply workflow auto-selection ONLY on real pose change.
    new_workflow_path_val = gr.update()
    if is_real_pose_change:
        if not p:  # Pose cleared
            new_workflow_path_val = gr.update(value="pose_OPEN.json")
        else:
            fname = os.path.basename(p).upper()
            if "_1CHAR" in fname:
                new_workflow_path_val = gr.update(value="pose_1CHAR.json")
            elif "_2CHAR" in fname:
                new_workflow_path_val = gr.update(value="pose_2CHAR.json")
            else:
                new_workflow_path_val = gr.update(value="pose_1CHAR.json")

    animal_flag = "_ANIMAL" in p
    preview_val = p if p else None

    pose_thumb = _resolve_aux_image(p, "poses", project_dict)
    shape_thumb = _resolve_aux_image(p, "shapes", project_dict)
    outline_thumb = _resolve_aux_image(p, "outlines", project_dict)

    return preview_val, gr.update(value=animal_flag), new_workflow_path_val, pose_thumb, shape_thumb, outline_thumb



def _get_bridge_folder(data: dict, seq_index: int, kf_id: str) -> Path | None:
    node, kind, seq, seq_id = _resolve_node_context(data, kf_id)
    if kind != "kf": return None
    
    # Logic: Bridge is between VidA and VidB. VidB starts at this KF.
    vids = seq.get("videos", {})
    vid_b = next((v for v in vids.values() if v.get("start_keyframe_id") == kf_id), None)
    if not vid_b: return None
    
    # Find VidA (precedes VidB in order)
    vid_order = seq.get("video_order", [])
    try:
        b_idx = vid_order.index(vid_b["id"])
        if b_idx == 0: return None
        vid_a_id = vid_order[b_idx - 1]
    except ValueError: return None
    
    proj = data.get("project", {})
    root = proj.get("comfy", {}).get("output_root")
    pname = proj.get("name")
    
    if not (root and pname): return None
    bridges_root = Path(root) / pname / seq_id / "bridges"
    if not bridges_root.exists(): return None
    
    base = f"{vid_a_id}_{vid_b['id']}"
    candidates = list(bridges_root.glob(f"{base}_*"))
    if candidates:
        frames_dir = candidates[0] / "frames"
        return frames_dir if frames_dir.exists() else None
    return None

def _eh_purge_bridge(project_dict: dict, nid: str):
    data = project_dict if isinstance(project_dict, dict) else {}
    frames_dir = _get_bridge_folder(data, 0, nid) # Pass 0 as dummy SI
    if frames_dir:
        try: shutil.rmtree(frames_dir.parent)
        except Exception as e: print(f"Error purging bridge: {e}")
    return gr.update(value=None, visible=False)

def _frames_dir_from_selected_video_path(sel_path: str) -> Path | None:
    try:
        sp = str(Path(sel_path)).replace("\\", "/")
        m = re.search(r"^(.*\/[^\/]+)\/[^\/]+_(\d+)_?\.mp4$", sp)
        if not m:
            vid_dir = Path(sel_path).parent
            if not vid_dir.is_dir(): return None
            candidates = sorted([p for p in vid_dir.glob("frames_*") if p.is_dir()], reverse=True)
            return candidates[0] if candidates else None
        
        base_dir_str = m.group(1)
        idx_str = m.group(2)
        return Path(base_dir_str) / f"frames_{idx_str}"
    except Exception: return None

# ---- DELETION HELPERS ----

def _eh_delete_image(pre_txt: str, loaded_nid: str, loaded_proj: str, selected_path: str):
    time.sleep(0.5)
    data = pre_txt if isinstance(pre_txt, dict) else {}
    
    if not _check_ownership(data, loaded_nid, loaded_proj): return pre_txt, gr.update(), (selected_path or ""), gr.update()
    kf, kind, _, _ = _resolve_node_context(data, loaded_nid)
    
    if kind != "kf" or not selected_path:
        return pre_txt, gr.update(), (selected_path or ""), gr.update()
    
    # 1. Neighbor Logic
    image_files_before = _get_kf_gallery_images(data, loaded_nid)
    index_before = -1
    try:
        norm_target = str(Path(selected_path).resolve()).lower()
        for i, p in enumerate(image_files_before):
            if str(Path(p).resolve()).lower() == norm_target:
                index_before = i; break
    except: pass
    
    # 2. Delete
    try:
        p = Path(selected_path)
        if p.is_file(): _try_delete_path(p, retries=5)
    except Exception as e: print(f"Error deleting image: {e}")
    
    # 3. Update
    if kf.get("selected_image_path") == selected_path:
        kf["selected_image_path"] = None
        
    image_files_after = _get_kf_gallery_images(data, loaded_nid)
    new_selection = None
    if image_files_after:
        if index_before != -1:
            new_index = min(index_before, len(image_files_after) - 1)
            new_selection = image_files_after[new_index]
        else: new_selection = image_files_after[0]
        
    if kf: kf["selected_image_path"] = new_selection
    
    dd_update, img_update = _dropdown_update_for_kf(data, loaded_nid, new_selection)
    return data, dd_update, str(new_selection or ""), img_update

def _eh_delete_video(project_dict: dict, loaded_nid: str, loaded_proj: str, path_to_delete: str):
    data = project_dict if isinstance(project_dict, dict) else {}
    
    if not _check_ownership(data, loaded_nid, loaded_proj):
        yield (data, gr.update(), gr.update())
        return
    
    v, kind, _, _ = _resolve_node_context(data, loaded_nid)
    
    yield (data, gr.update(), gr.update(value=None))
    time.sleep(0.3)
    
    if kind != "vid" or not path_to_delete:
        yield (data, gr.update(), gr.update())
        return

    vid_files_before = _get_vid_gallery_files(data, loaded_nid )
    index_before = -1
    try: index_before = vid_files_before.index(path_to_delete)
    except: pass
    
    try:
        p = Path(path_to_delete)
        if p.is_file():
            frames_path = _frames_dir_from_selected_video_path(str(p))
            if frames_path and frames_path.is_dir(): _try_delete_path(frames_path)
            _try_delete_path(p)
    except OSError as e: print(f"Error deleting video: {e}")
    
    if v.get("selected_video_path") == path_to_delete:
        v["selected_video_path"] = None
        
    vid_files_after = _get_vid_gallery_files(data, loaded_nid)
    new_selection = None
    if vid_files_after:
        if index_before != -1:
            new_index = min(index_before, len(vid_files_after) - 1)
            new_selection = vid_files_after[new_index]
        else: new_selection = vid_files_after[0]
        
    v["selected_video_path"] = new_selection
    choices = [(Path(p).name, p) for p in vid_files_after]
    
    yield (data, gr.update(choices=choices, value=new_selection), gr.update(value=new_selection))


def _eh_refresh_pose_previews(project_dict: dict, loaded_nid: str):
    """Refresh pose preview, CN thumbnails, and workflow after pose selection."""
    data = project_dict if isinstance(project_dict, dict) else {}
    kf, kind, _, _ = _resolve_node_context(data, loaded_nid)
    if kind != "kf" or not kf:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    
    pose_path = kf.get("pose") or ""
    poses_dir = get_project_poses_dir(data)
    
    # Get CN thumbnails
    pose_thumb = _resolve_aux_image(pose_path, "poses", data)
    shape_thumb = _resolve_aux_image(pose_path, "shapes", data)
    outline_thumb = _resolve_aux_image(pose_path, "outlines", data)
    
    # Refresh gallery with selection
    gallery_items = get_pose_gallery_list(str(poses_dir)) if poses_dir else []
    sel_idx = _resolve_gallery_index(pose_path, gallery_items)
    
    # Auto-select workflow based on pose filename (_1CHAR, _2CHAR)
    new_workflow = "pose_1CHAR.json"  # default
    if pose_path:
        fname = os.path.basename(pose_path).upper()
        if "_2CHAR" in fname:
            new_workflow = "pose_2CHAR.json"
        elif "_1CHAR" in fname:
            new_workflow = "pose_1CHAR.json"
    else:
        new_workflow = "pose_OPEN.json"
    
    # Animal pose flag
    animal_flag = "_ANIMAL" in pose_path.upper() if pose_path else False
    
    return (
        gr.update(value=pose_path),                              # kf_pose_preview
        gr.update(value=gallery_items, selected_index=sel_idx),  # kf_pose_gallery
        gr.update(value=pose_thumb),                             # kf_cn_pose_thumb
        gr.update(value=shape_thumb),                            # kf_cn_shape_thumb
        gr.update(value=outline_thumb),                          # kf_cn_outline_thumb
        gr.update(value=new_workflow),                           # kf_workflow_json
        gr.update(value=animal_flag),                            # kf_cn_pose_animal
    )


def _eh_pose_gallery_select(project_dict: dict, gallery_value, evt: gr.SelectData):
    """Get original pose path using index lookup (avoids Gradio's filename sanitization)."""
    if evt is None or evt.index is None: 
        return gr.update(value="")
    try:
        poses_dir = get_project_poses_dir(project_dict)
        if poses_dir:
            # Fresh list from disk has real paths, same order as Gradio's cached gallery
            original_list = get_pose_gallery_list(str(poses_dir))
            if 0 <= evt.index < len(original_list):
                path = str(original_list[evt.index][0])
                print(f"[POSE_SELECT] idx={evt.index} -> {path}")
                return gr.update(value=path)
    except Exception as e:
        print(f"[POSE_SELECT] Error: {e}")
    return gr.update(value="")

# def _eh_clear_pose(gallery_items):
#     new_items = list(gallery_items) if gallery_items else []
#     return gr.update(value=""), gr.update(value=new_items, selected_index=None)

def _eh_clear_pose(project_dict: dict, loaded_nid: str, loaded_proj: str):
    """Clear pose and reset CN settings, saving to project data."""
    print(f"[CLEAR_POSE] Called with loaded_nid={loaded_nid}")
    data = copy.deepcopy(project_dict) if isinstance(project_dict, dict) else {}
    
    # Save cleared values to project
    if _check_ownership(data, loaded_nid, loaded_proj):
        kf, kind, _, _ = _resolve_node_context(data, loaded_nid)
        if kind == "kf" and kf is not None:
            kf["pose"] = ""
            kf["pose_flip_horizontal"] = False
            kf["pose_flip_vertical"] = False
            kf["controlnet_settings"] = copy.deepcopy(DEFAULT_KF_CN_SETTINGS)
    
    return (
        data,                                                                   # preview
        gr.update(value=""),                                                    # kf_pose
        gr.update(value=None),                                                  # kf_pose_preview
        gr.update(value=None),                                                  # kf_cn_pose_thumb
        gr.update(value=None),                                                  # kf_cn_shape_thumb
        gr.update(value=None),                                                  # kf_cn_outline_thumb
        gr.update(value=False),                                                 # kf_flip_horiz
        gr.update(value=False),                                                 # kf_flip_vert
        gr.update(value=(DEFAULT_KF_CN_SETTINGS["1"]["switch"] == "On")),       # kf_cn_pose_enable
        gr.update(value=DEFAULT_KF_CN_SETTINGS["1"]["strength"]),               # kf_cn_pose_strength
        gr.update(value=DEFAULT_KF_CN_SETTINGS["1"]["start_percent"]),          # kf_cn_pose_start
        gr.update(value=DEFAULT_KF_CN_SETTINGS["1"]["end_percent"]),            # kf_cn_pose_end
        gr.update(value=(DEFAULT_KF_CN_SETTINGS["2"]["switch"] == "On")),       # kf_cn_shape_enable
        gr.update(value=DEFAULT_KF_CN_SETTINGS["2"]["strength"]),               # kf_cn_shape_strength
        gr.update(value=DEFAULT_KF_CN_SETTINGS["2"]["start_percent"]),          # kf_cn_shape_start
        gr.update(value=DEFAULT_KF_CN_SETTINGS["2"]["end_percent"]),            # kf_cn_shape_end
        gr.update(value=(DEFAULT_KF_CN_SETTINGS["3"]["switch"] == "On")),       # kf_cn_outline_enable
        gr.update(value=DEFAULT_KF_CN_SETTINGS["3"]["strength"]),               # kf_cn_outline_strength
        gr.update(value=DEFAULT_KF_CN_SETTINGS["3"]["start_percent"]),          # kf_cn_outline_start
        gr.update(value=DEFAULT_KF_CN_SETTINGS["3"]["end_percent"]),            # kf_cn_outline_end
    )

def _eh_upload_pose_for_keyframe(project_dict: dict, nid: str, uploaded_file):
    """
    Saves uploaded pose to library, generates previews, and sets it on the keyframe.
    Yields updates for: kf_pose, kf_pose_preview, kf_pose_gallery, kf_cn_pose_thumb, kf_cn_shape_thumb, kf_cn_outline_thumb
    """
    if not uploaded_file:
        yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        return
    
    poses_dir = get_project_poses_dir(project_dict)
    if not poses_dir:
        yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        return
    
    poses_dir.mkdir(parents=True, exist_ok=True)
    
    # Get original filename from upload
    if hasattr(uploaded_file, 'name'):
        original_name = Path(uploaded_file.name).name
        source_path = uploaded_file.name
    else:
        original_name = Path(uploaded_file).name
        source_path = uploaded_file
    
    # Determine destination path (auto-version if exists)
    dest_path = poses_dir / original_name
    if dest_path.exists():
        stem = dest_path.stem
        suffix = dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = poses_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    
    # Copy file to poses directory
    import shutil
    shutil.copy2(source_path, dest_path)
    
    saved_pose_path = str(dest_path)
    
    # Initial yield - set pose path immediately
    yield (
        gr.update(value=saved_pose_path),
        gr.update(value=saved_pose_path),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    
    # Generate controlnet previews - save to poses library
    pose_thumb = None
    shape_thumb = None
    outline_thumb = None
    # Infer animal pose from filename
    use_animal = "_ANIMAL" in saved_pose_path.upper()
    for result in run_pose_preview_task(project_dict, saved_pose_path, str(poses_dir), use_animal):
    # for result in run_pose_preview_task(project_dict, saved_pose_path, str(poses_dir)):
        pose_thumb = result.get("openpose_path") or pose_thumb
        shape_thumb = result.get("shape_path") or shape_thumb
        outline_thumb = result.get("outline_path") or outline_thumb
    
    # Update gallery
    gallery_items = get_pose_gallery_list(str(poses_dir))
    
    gr.Info(f"Pose uploaded: {dest_path.name}")
    
    yield (
        gr.update(value=saved_pose_path),
        gr.update(value=saved_pose_path),
        gr.update(value=gallery_items),
        gr.update(value=pose_thumb),
        gr.update(value=shape_thumb),
        gr.update(value=outline_thumb),
    )

def _eh_copy_pose_prompt(pose_path: str):
    """Read pose metadata and display prompt in status window."""
    if not pose_path:
        gr.Warning("No pose selected")
        return gr.update(), gr.update()
    
    # Try path directly, then resolve as library reference
    full_path = pose_path if Path(pose_path).exists() else _resolve_asset_aux(pose_path, "poses")
    if not full_path or not Path(full_path).exists():
        gr.Warning("Pose file not found")
        return gr.update(), gr.update()
    
    snapshot = _read_metadata_png(full_path)
    if not snapshot:
        gr.Warning("No metadata found in pose")
        return gr.update(), gr.update()
    
    prompt = snapshot.get("generation", {}).get("executed_prompt", "")
    if not prompt:
        gr.Warning("No prompt found in pose metadata")
        return gr.update(), gr.update()
    
    gr.Info("Pose prompt loaded to Status")
    return gr.update(open=True), gr.update(value=prompt)

def _eh_generate_pose_for_keyframe(project_dict: dict, nid: str, kf_prompt: str, char_left_id: str):
    """
    Generates a pose using the keyframe's prompt, saves it, and sets it on the keyframe.
    If a primary character is selected, extracts LoRA tags/keywords and uses Expressive mode.
    Yields updates for: kf_pose, kf_pose_preview, kf_pose_gallery, kf_cn_pose_thumb, kf_cn_shape_thumb, kf_cn_outline_thumb
    """
    import re
    
    poses_dir = get_project_poses_dir(project_dict)
    if not poses_dir:
        yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        return
    
    poses_dir_str = str(poses_dir)
    
    use_animal = False
    char_count = "1 Character"
    
    # Build prompt with character LoRA if selected
    base_prompt = kf_prompt or ""
    lora_suffix = ""
    has_character = False
    
    if char_left_id:
        characters = project_dict.get("project", {}).get("characters", [])
        char = next((c for c in characters if c.get("id") == char_left_id), None)
        if char:
            has_character = True
            parts = []
            
            # Extract __lora:...:..__ tags from character's prompt field
            char_prompt = char.get("prompt", "") or ""
            lora_tags = re.findall(r'__lora:[^_]+__', char_prompt)
            parts.extend(lora_tags)
            
            # Get trigger keywords
            lora_keyword = char.get("lora_keyword", "").strip()
            if lora_keyword:
                parts.append(lora_keyword)
            
            if parts:
                lora_suffix = ", " + " ".join(parts)
    
    # Use Expressive mode when character is selected (lets LoRA express naturally)
    mode = "Expressive" if has_character else "Simple"
    prompt = base_prompt + lora_suffix
    
    final_main_path = None
    final_pose_path = None
    final_shape_path = None
    final_outline_path = None
    
    for result in handle_pose_generation(prompt, project_dict, use_animal, char_count, mode):
        if result and len(result) >= 10:
            final_main_path = result[0]
            final_pose_path = result[7]
            final_shape_path = result[8]
            final_outline_path = result[9]
            yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    
    if not final_main_path:
        yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        return
    
    pose_name = _sanitize_filename(prompt, fallback="generated_pose")
    
    save_status, gallery_update = save_or_update_pose(
        final_main_path,
        pose_name,
        poses_dir_str,
        use_animal,
        char_count,
        final_pose_path,
        final_shape_path,
        final_outline_path
    )
    
    if "Error" in save_status:
        yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        return
    
    saved_name = pose_name + "_1CHAR"
    saved_pose_path = None
    
    if poses_dir.exists():
        img_files = sorted(
            [f for f in poses_dir.iterdir() if f.is_file() and f.stem.startswith(saved_name) and f.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        if img_files:
            saved_pose_path = str(img_files[0])
    
    if not saved_pose_path and poses_dir.exists():
        img_files = sorted(
            [f for f in poses_dir.iterdir() if f.is_file() and f.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        if img_files:
            saved_pose_path = str(img_files[0])
    
    if not saved_pose_path:
        yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
        return
    
    pose_thumb = _resolve_asset_aux(saved_pose_path, "poses")
    shape_thumb = _resolve_asset_aux(saved_pose_path, "shapes")
    outline_thumb = _resolve_asset_aux(saved_pose_path, "outlines")
    
    gallery_items = get_pose_gallery_list(poses_dir_str)
    
    gr.Info(f"Pose saved: {Path(saved_pose_path).name}")
    
    yield (
        gr.update(value=saved_pose_path),
        gr.update(value=saved_pose_path),
        gr.update(value=gallery_items),
        gr.update(value=pose_thumb),
        gr.update(value=shape_thumb),
        gr.update(value=outline_thumb),
    )


def _eh_upload_image(pre_txt: str, loaded_nid: str, loaded_proj: str, file):
    data = pre_txt if isinstance(pre_txt, dict) else {}
    data = copy.deepcopy(data)
    
    if not _check_ownership(data, loaded_nid, loaded_proj): return pre_txt, gr.update(value=None)
    node, kind, _, seq_id = _resolve_node_context(data, loaded_nid)
    if kind != "kf" or not file: return pre_txt, gr.update(value=None)
    
    kf_dir = _get_kf_dir(data, seq_id, node["id"])
    if not kf_dir: return pre_txt, gr.update(value=None)
    
    kf_dir.mkdir(parents=True, exist_ok=True)
    temp_p = Path(file.name)
    dest_p = kf_dir / temp_p.name
    try:
        shutil.copy(temp_p, dest_p)
        sel_resolved = str(Path(dest_p).resolve())
        node["selected_image_path"] = sel_resolved
        new_pre = data
        dd_update, _ = _dropdown_update_for_kf(data, loaded_nid, sel_resolved)
        return new_pre, dd_update
    except Exception as e:
        print(f"Error uploading: {e}")
        return pre_txt, gr.update()


def _eh_set_selected_image(pre_txt: str, loaded_nid: str, loaded_proj: str, selected_path: str):
    print(f"[SET_SEL_IMG] nid={loaded_nid}, path={selected_path[:40] if selected_path else 'None'}")
    data = pre_txt if isinstance(pre_txt, dict) else {}
    data = copy.deepcopy(data)
    
    if not _check_ownership(data, loaded_nid, loaded_proj): return data, "", gr.update(), gr.update(value="")
    sel = str(Path(selected_path).resolve()) if selected_path else None
    kf, kind, _, _ = _resolve_node_context(data, loaded_nid)
    
    if kind == "kf" and kf:
        if kf.get("selected_image_path") != sel:
            kf["selected_image_path"] = sel
            
    dd_update, img_update = _dropdown_update_for_kf(data, loaded_nid, sel)
    return data, (sel or ""), img_update, gr.update(value="")

# def _eh_set_selected_video(project_dict: dict, loaded_nid: str, loaded_proj: str, selected_path: str):
#     data = project_dict if isinstance(project_dict, dict) else {}
#     data = copy.deepcopy(data)
    
#     # if not _check_ownership(data, loaded_nid, loaded_proj): return data, gr.update()
#     if not _check_ownership(data, loaded_nid, loaded_proj): return data, "", gr.update(), gr.update(value="")
#     v, kind, _, _ = _resolve_node_context(data, loaded_nid)
#     if kind != "vid" or not selected_path: return data, gr.update()
    
#     if v.get("selected_video_path") != selected_path:
#         v["selected_video_path"] = selected_path
#     return data, gr.update(value=selected_path)
def _eh_set_selected_video(project_dict: dict, loaded_nid: str, loaded_proj: str, selected_path: str):
    print(f"[SET_SEL_VID] nid={loaded_nid}, path={selected_path[:40] if selected_path else 'None'}")
    data = project_dict if isinstance(project_dict, dict) else {}
    data = copy.deepcopy(data)
    
    if not _check_ownership(data, loaded_nid, loaded_proj): return data, gr.update(), gr.update(value="")
    v, kind, _, _ = _resolve_node_context(data, loaded_nid)
    if kind != "vid" or not selected_path: return data, gr.update(), gr.update(value="")
    
    if v.get("selected_video_path") != selected_path:
        v["selected_video_path"] = selected_path
    return data, gr.update(value=selected_path), gr.update(value="")

def _eh_next_kf_image(project_dict: dict, nid: str, current_path: str):
    data = project_dict
    image_list = _get_kf_gallery_images(data, nid)
    if not image_list: return gr.update(), gr.update()
    
    current_index = 0
    if current_path:
        try:
            norm_cur = str(Path(current_path).resolve()).lower()
            for i, p in enumerate(image_list):
                if str(Path(p).resolve()).lower() == norm_cur:
                    current_index = i; break
        except Exception: pass

    prev_index = (current_index - 1 + len(image_list)) % len(image_list)
    new_path = image_list[prev_index]
    return gr.update(value=new_path), gr.update(value=new_path)

def _eh_prev_kf_image(project_dict: dict, nid: str, current_path: str):
    data = project_dict
    image_list = _get_kf_gallery_images(data, nid)
    if not image_list: return gr.update(), gr.update()

    current_index = -1
    if current_path:
        try:
            norm_cur = str(Path(current_path).resolve()).lower()
            for i, p in enumerate(image_list):
                if str(Path(p).resolve()).lower() == norm_cur:
                    current_index = i; break
        except Exception: pass

    next_index = (current_index + 1) % len(image_list)
    new_path = image_list[next_index]
    return gr.update(value=new_path), gr.update(value=new_path)

def _eh_next_vid_clip(project_dict: dict, nid: str, current_path: str):
    data = project_dict if isinstance(project_dict, dict) else {}
    clip_list = _get_vid_gallery_files(data, nid)
    if not clip_list: return gr.update(), gr.update()
    current_index = 0
    if current_path and current_path in clip_list:
        try: current_index = clip_list.index(current_path)
        except ValueError: pass
    prev_index = (current_index - 1 + len(clip_list)) % len(clip_list)
    new_path = clip_list[prev_index]
    return gr.update(choices=clip_list, value=new_path), gr.update(value=new_path)

def _eh_prev_vid_clip(project_dict: dict, nid: str, current_path: str):
    data = project_dict if isinstance(project_dict, dict) else {}
    clip_list = _get_vid_gallery_files(data, nid)
    if not clip_list: return gr.update(), gr.update()
    current_index = -1
    if current_path and current_path in clip_list:
        try: current_index = clip_list.index(current_path)
        except ValueError: pass
    next_index = (current_index + 1) % len(clip_list)
    new_path = clip_list[next_index]
    return gr.update(choices=clip_list, value=new_path), gr.update(value=new_path)

def _eh_navigate_vertical(project_dict: dict, current_nid: str, direction: int):
    data = project_dict
    all_rows = _rows_with_times(data)
    if not all_rows: return gr.update(), current_nid
    
    nids = [r[1] for r in all_rows]
    if current_nid not in nids: return gr.update(), current_nid
    
    _, target_kind, _, _ = _resolve_node_context(data, current_nid)
    
    current_idx = nids.index(current_nid)
    num_nids = len(nids)
    search_idx = (current_idx + direction) % num_nids
    found_nid = current_nid
    
    while search_idx != current_idx:
        candidate = nids[search_idx]
        _, kind, _, _ = _resolve_node_context(data, candidate)
        if kind == target_kind:
            found_nid = candidate
            break
        search_idx = (search_idx + direction) % num_nids
        
    new_rows = _get_filtered_outline_rows(data, found_nid)
    return gr.update(choices=new_rows, value=found_nid), found_nid

def _filter_bridge_json_for_kf(project_dict: dict, nid: str) -> dict:
    data = copy.deepcopy(project_dict) if isinstance(project_dict, dict) else {}
    node, kind, seq, seq_id = _resolve_node_context(data, nid)
    if kind != "kf": return data
    
    # Isolate relevant videos (VidA, VidB)
    vids = seq.get("videos", {})
    vid_b = next((v for v in vids.values() if v.get("start_keyframe_id") == node["id"]), None)
    if not vid_b: return data
    
    vid_order = seq.get("video_order", [])
    try:
        b_idx = vid_order.index(vid_b["id"])
        if b_idx == 0: return data
        vid_a_id = vid_order[b_idx - 1]
    except ValueError: return data
    
    data["sequences"][seq_id]["videos"] = {
        vid_a_id: vids[vid_a_id],
        vid_b["id"]: vid_b
    }
    data["sequences"][seq_id]["video_order"] = [vid_a_id, vid_b["id"]]
    
    return data

def _eh_purge_bridge_pre_gen(project_dict: dict, nid: str):
    data = project_dict if isinstance(project_dict, dict) else {}
    frames_dir = _get_bridge_folder(data, 0, nid)
    msg = "No previous bridge found."
    if frames_dir:
        try:
            shutil.rmtree(frames_dir.parent)
            msg = "Purged existing bridge files."
        except Exception as e:
            msg = f"Error purging: {e}"
            print(msg)
    return data, msg

def _eh_delete_all_but_this_image(pre_txt: str, loaded_nid: str, loaded_proj: str, selected_path: str):
    data = pre_txt if isinstance(pre_txt, dict) else {}
    
    if not _check_ownership(data, loaded_nid, loaded_proj):
        yield (_dumps(data), gr.update(), selected_path)
        return
    
    yield (_dumps(data), gr.update(value=None), "")
    time.sleep(0.3)
    
    node, kind, _, seq_id = _resolve_node_context(data, loaded_nid)
    if kind != "kf" or not selected_path:
        kf_files = _get_kf_gallery_images(data, loaded_nid)
        choices = [(Path(p).name, p) for p in kf_files]
        yield (_dumps(data), gr.update(choices=choices, value=selected_path), selected_path)
        return
        
    kf_dir = _get_kf_dir(data, seq_id, node["id"])
    if kf_dir and kf_dir.exists():
        try:
            keep = str(Path(selected_path).resolve())
            for f in kf_dir.iterdir():
                if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    if str(f.resolve()) != keep: _try_delete_path(f)
        except Exception as e: print(f"Error deleting others: {e}")
        
    image_files_after = _get_kf_gallery_images(data, loaded_nid)
    new_selection = selected_path if selected_path in image_files_after else (image_files_after[0] if image_files_after else None)
    
    if node: node["selected_image_path"] = new_selection
    
    dd_update, img_update = _dropdown_update_for_kf(data, loaded_nid, new_selection)
    sel_out = img_update.get("value") if isinstance(img_update, dict) else None
    yield (_dumps(data), dd_update, sel_out or "")

def _eh_delete_all_but_this_video(project_dict: dict, loaded_nid: str, loaded_proj: str, selected_path: str):
    data = project_dict if isinstance(project_dict, dict) else {}
    
    if not _check_ownership(data, loaded_nid, loaded_proj):
        yield (data, gr.update(), gr.update())
        return
    
    yield (data, gr.update(value=None), gr.update(value=None))
    time.sleep(0.3)
    
    node, kind, _, seq_id = _resolve_node_context(data, loaded_nid)
    if kind != "vid" or not selected_path:
        files = _get_vid_gallery_files(data, loaded_nid)
        choices = [(Path(p).name, p) for p in files]
        yield (data, gr.update(choices=choices, value=selected_path), gr.update(value=selected_path))
        return
        
    vid_dir = _get_vid_dir(data, seq_id, node["id"])
    if vid_dir and vid_dir.exists():
        try:
            keep = str(Path(selected_path).resolve())
            for f in vid_dir.glob("*.mp4"):
                if f.is_file():
                    curr = str(f.resolve())
                    if curr != keep:
                        frames = _frames_dir_from_selected_video_path(str(f))
                        if frames and frames.is_dir(): _try_delete_path(frames)
                        _try_delete_path(f)
        except Exception as e: print(f"Error deleting others: {e}")
        
    files_after = _get_vid_gallery_files(data, loaded_nid)
    new_sel = selected_path if selected_path in files_after else (files_after[0] if files_after else None)
    
    if node: node["selected_video_path"] = new_sel
    choices = [(Path(p).name, p) for p in files_after]
    yield (data, gr.update(choices=choices, value=new_sel), gr.update(value=new_sel))


# ---- MAIN SELECTION HANDLER (UPDATED) ----



def _eh_node_selected(project_dict: dict, raw_value, cur_sel: str):
    print(f"[NODE_SEL] raw_value={raw_value}, cur_sel={cur_sel}")
    
    # LOOP GUARD: Skip if we just set this node (prevents node_selector.change feedback)
    # But allow when cur_sel is empty/None (initial load)
    if raw_value and cur_sel and raw_value == cur_sel:
        print(f"[NODE_SEL] SKIP - already on {raw_value}")
        return tuple([gr.update()] * 67)
    
    # Handle both dict and string (JSON) input
    
    # # LOOP GUARD: If already on this node AND we have valid project data, skip
    # # But allow initial load (when project_dict is empty or invalid)
    # data_valid = isinstance(project_dict, dict) and project_dict.get("sequences")
    # if raw_value and cur_sel and raw_value == cur_sel and data_valid:
    #     print(f"[NODE_SEL] SKIP - already on {raw_value}")
    #     return tuple([gr.update()] * 67)
    
    # Handle both dict and string (JSON) input
    if isinstance(project_dict, str):
        try:
            data = _ensure_project(json.loads(project_dict))
        except:
            data = _ensure_project({})
    elif isinstance(project_dict, dict):
        data = _ensure_project(project_dict)
    else:
        data = _ensure_project({})

    nid = raw_value
    node, kind, seq, seq_id = _resolve_node_context(data, nid)
    
    # ... (Dropdown preparation matches original) ...
    settings_raw = data.get("project", {}).get("settings", [])
    styles_raw = data.get("project", {}).get("styles", [])
    
    setting_choices = [("", "")] + sorted(
        [(i.get("name", "Unknown"), i.get("id")) for i in settings_raw if isinstance(i, dict)],
        key=lambda x: (x[0] or "").lower()
    )
    style_choices = [("", "")] + sorted(
        [(i.get("name", "Unknown"), i.get("id")) for i in styles_raw if isinstance(i, dict)],
        key=lambda x: (x[0] or "").lower()
    )

    filtered_rows = _get_filtered_outline_rows(data, nid)
    
    sequences = data.get("sequences")
    if not filtered_rows and nid and (sequences is None or not isinstance(sequences, dict)):
         print("[NodeSelect] Blocked choice clear: Detected empty project data.")
         node_selector_update = gr.update()
    else:
         node_selector_update = gr.update(choices=filtered_rows, value=(nid if nid and filtered_rows else None))
    
    ON, OFF = gr.update(visible=True), gr.update(visible=False)
    
    
    # Initialize Defaults
    seq_vals = [gr.update()] * 9
    kf_vals = [gr.update()] * 31
    vid_vals = [gr.update()] * 6
    
    # Batch iteration defaults (seeds removed from UI)
    pj_kf = data.get("project", {}).get("keyframe_generation", {})
    pj_vid = data.get("project", {}).get("inbetween_generation", {})
    def _int(val, d): 
        try: return int(float(val)) if val is not None else d
        except: return d
    
    seq_kf_iter_value = gr.update(value=_int(pj_kf.get("image_iterations_default"), 1))
    seq_ib_iter_value = gr.update(value=_int(pj_vid.get("video_iterations_default"), 1))

    proj_name = data.get("project", {}).get("name", "")
    base_return_prefix = (node_selector_update, nid, nid, proj_name)  # node_selector, selected_node, loaded_node_id, loaded_project_name
    base_return_suffix = (
        gr.update(value=_sequence_len_text(data, seq_id) if seq_id else "", visible=bool(seq_id)), # seq_len
        gr.update(value=None), # main_preview
        gr.update(value=None), # kf_gallery
        gr.update(value=None), # vid_gallery
        gr.update(value=None), # vid_player
        gr.update(), # vid_lora
        gr.update(value=""), # seq_status
        seq_kf_iter_value, seq_ib_iter_value,  # batch iteration values
        gr.update(value=None, visible=False), gr.update(value=None, visible=False), gr.update(visible=False),
        gr.update(value=None), gr.update()
    )
    
    if kind == "seq":
        seq_vals = [
            gr.update(choices=setting_choices, value=seq.get("setting_id") or ""),
            gr.update(value=seq.get("setting_prompt", "")),
            gr.update(choices=style_choices, value=seq.get("style_id") or ""),
            gr.update(value=seq.get("style_prompt", "")),
            gr.update(value=None),
            gr.update(value=seq.get("action_prompt", "")),
            gr.update(value=bool(seq.get("video_plan", {}).get("open_start", False))),
            gr.update(value=bool(seq.get("video_plan", {}).get("open_end", True))),
            gr.update(visible=False)
        ]
        return (*base_return_prefix, ON, OFF, OFF, *seq_vals, *kf_vals, *vid_vals, *base_return_suffix)

    if kind == "kf":
        kf = node
        # ... (Char choices same as original)
        project_chars = data.get("project", {}).get("characters", [])
        char_choices = sorted([(c.get("name"), c.get("id")) for c in project_chars if c.get("id")], key=lambda x: x[0].lower())
        # FIX: Add empty option to prevent validation errors on new/empty keyframes
        char_choices = [("", "")] + char_choices
        
        # FIX: Populate workflow choices dynamically
        try:
            wf_files = [""] + sorted([f.name for f in WORKFLOWS_DIR.glob("*.json") if f.is_file()])
        except:
            wf_files = [""]

        negs = kf.get("negatives", {})
        chars = kf.get("characters", ["", ""])
        
        selected_path = kf.get("selected_image_path")
        kf_gallery_update, main_preview_image_update = _dropdown_update_for_kf(data, nid, selected_path)
        
        # Pose & Aux
        pose = kf.get("pose", "")
        kf_pose_preview_update = gr.update(value=(pose or None))
        cn = kf.get("controlnet_settings", {})
        
        # Bridge
        bridge_dir = _get_bridge_folder(data, 0, nid)
        bridge_images = sorted([str(p) for p in bridge_dir.glob("*.png")]) if bridge_dir else []
        kf_bridge_gallery = gr.update(value=bridge_images, visible=bool(bridge_images))
        
        # Pose Gallery
        poses_dir = get_project_poses_dir(data)
        gallery_items = get_pose_gallery_list(str(poses_dir)) if poses_dir else []
        sel_idx = _resolve_gallery_index(pose, gallery_items)
        kf_pose_gallery = gr.update(value=gallery_items, selected_index=sel_idx, visible=True)

        kf_vals = [
            gr.update(value=pose), kf_pose_preview_update,
            gr.update(value=(cn.get("1", {}).get("switch")=="On")), gr.update(value=_resolve_aux_image(pose, "poses", data)), gr.update(value=kf.get("use_animal_pose", False)), gr.update(value=kf.get("pose_flip_horizontal", False)), gr.update(value=kf.get("pose_flip_vertical", False)),
            gr.update(value=float(cn.get("1", {}).get("strength", 1.0))), gr.update(value=float(cn.get("1", {}).get("start_percent", 0.0))), gr.update(value=float(cn.get("1", {}).get("end_percent", 1.0))),
            gr.update(value=(cn.get("2", {}).get("switch")=="On")), gr.update(value=_resolve_aux_image(pose, "shapes", data)), gr.update(value=float(cn.get("2", {}).get("strength", 1.0))), gr.update(value=float(cn.get("2", {}).get("start_percent", 0.0))), gr.update(value=float(cn.get("2", {}).get("end_percent", 1.0))),
            gr.update(value=(cn.get("3", {}).get("switch")=="On")), gr.update(value=_resolve_aux_image(pose, "outlines", data)), gr.update(value=float(cn.get("3", {}).get("strength", 1.0))), gr.update(value=float(cn.get("3", {}).get("start_percent", 0.0))), gr.update(value=float(cn.get("3", {}).get("end_percent", 1.0))),
            gr.update(choices=char_choices, value=chars[0]), gr.update(choices=char_choices, value=chars[1]),
            gr.update(value=kf.get("layout", "")), gr.update(value=kf.get("template", "")), gr.update(choices=wf_files, value=Path(kf.get("workflow_json", "")).name),
            gr.update(value=negs.get("left", "")), gr.update(value=negs.get("right", "")), gr.update(value=negs.get("heal", "")),
            # Fix: Ensure these are Integers for the Sliders, not strings
            gr.update(value=int(kf.get("join_smoothing_level", 1))), gr.update(value=int(kf.get("join_offset", 0))),
            gr.update(value=None)
        ]
        
        
        # Override suffixes
        suffix_list = list(base_return_suffix)
        suffix_list[2] = kf_gallery_update
        suffix_list[1] = main_preview_image_update
        suffix_list[9] = kf_bridge_gallery
        suffix_list[13] = kf_pose_gallery
        
        return (*base_return_prefix, OFF, ON, OFF, *seq_vals, *kf_vals, *vid_vals, *suffix_list)

    if kind == "vid":
        v = node
        
        # Find start/end labels
        def _label(kfid):
            if not kfid: return "Open", None
            k = seq["keyframes"].get(kfid, {})
            return Path(k.get("pose", "")).stem or "No pose", k.get("selected_image_path")

        _, s_path = _label(v.get("start_keyframe_id"))
        _, e_path = _label(v.get("end_keyframe_id"))
        

        val = v.get("duration_override_sec")
        default_dur = int(float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0)))
        
        radio_value, label_text, reset_label = _get_vid_dur_ui_vals(val, default_dur)

        vid_vals = [
            gr.update(value=s_path), gr.update(value=e_path), 
            gr.update(value=radio_value, label=label_text),
            gr.update(value=v.get("inbetween_prompt", "")), gr.update(value=v.get("negative_prompt", "")),
            gr.update(value=reset_label)
        ]
        
        vid_files = _get_vid_gallery_files(data, nid)
        sel_vid = v.get("selected_video_path")
        if sel_vid and sel_vid not in vid_files: vid_files.insert(0, sel_vid)
        
        vid_gal_upd = gr.update(choices=[(Path(p).name, p) for p in vid_files], value=sel_vid)
        vid_play_upd = gr.update(value=sel_vid)
        
        suffix_list = list(base_return_suffix)
        suffix_list[3] = vid_gal_upd
        suffix_list[4] = vid_play_upd
        
        return (*base_return_prefix, OFF, OFF, ON, *seq_vals, *kf_vals, *vid_vals, *suffix_list)

    return (*base_return_prefix, OFF, OFF, OFF, *seq_vals, *kf_vals, *vid_vals, *base_return_suffix)

def _eh_run_and_curate_image(project_dict: dict, nid: str, count: int, seed_input: str, path_at_start: str, current_selection: str):
    """
    Generate N keyframe images (ADD mode).
    Default count = 1.
    Includes robust retry logic for filesystem latency.
    """
    yield (
        gr.update(value=None, interactive=False),
        "Starting.",
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update()
    )

    if not project_dict or not nid:
        yield (
            gr.update(interactive=True),
            "Error: Missing project or selection.",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update()
        )
        return

    # Parse seed: blank = None (random), otherwise integer
    seed_override = None
    if seed_input and seed_input.strip():
        try:
            seed_override = int(seed_input.strip())
        except ValueError:
            pass
    
    print(f"[DEBUG SEED] seed_input='{seed_input}', seed_override={seed_override}")

    for i in range(max(1, int(count))):

        # 1. GENERATION
        gen = handle_test_generation(project_dict, nid, path_at_start, seed_override=seed_override)
        result_path = None
        for result in gen:
            # Trace: handle_test_generation yields (path, openpose, logs, metadata)
            if isinstance(result, tuple) and len(result) >= 3:
                current_log = result[2]
                yield (gr.update(interactive=False), current_log, gr.update(), gr.update(), gr.update(), gr.update())
                result_path = result[0]
            else:
                result_path = result
        # Handle generator output format (string or tuple)
        if isinstance(result_path, tuple):
            # result_path = result_path[1] if len(result_path) > 1 else result_path[0]
            result_path = result_path[0] if isinstance(result_path, tuple) else result_path
        
        # 2. CURATION
        user_still_here = (current_selection == nid)
        
        if user_still_here and result_path:
            # Resolve Node Label for notification
            try:
                rows = _get_filtered_outline_rows(project_dict, nid)
                node_label = next((lbl for lbl, id in rows if id == nid), f"Node {nid}")
            except Exception:
                node_label = f"Node {nid}"
            
            # Default values (fallback)
            val_norm = result_path
            choices_for_ui = []
            
            # Retry Logic: File system latency handling
            try:
                import time
                import os
                
                current_files = []
                found_match = False
                
                # Attempt to find the new file in the gallery list (max 2.0s wait)
                for attempt in range(5):
                    current_files = _get_kf_gallery_images(project_dict, nid)
                    
                    # Method A: Exact match
                    if result_path in current_files:
                        val_norm = result_path
                        found_match = True
                        break
                        
                    # Method B: Normalized path match (handles casing/slash differences)
                    try:
                        target_abs = os.path.abspath(result_path).lower()
                        for p in current_files:
                            if os.path.abspath(p).lower() == target_abs:
                                val_norm = p
                                found_match = True
                                break
                    except Exception:
                        pass
                    
                    if found_match:
                        break
                    
                    time.sleep(0.4) # Wait before retry
                
                # Build dropdown choices from the most recent file scan
                choices_for_ui = [(os.path.basename(p), p) for p in current_files]

            except Exception as e:
                print(f"[WARN] Error updating gallery list: {e}")
                # If retry crashes, choices_for_ui stays empty, val_norm stays result_path.
                # The UI will show the full path (graceful degradation).

            # 3. NOTIFICATION & UPDATE
            # gr.Info(f"✓ {node_label}")
            from pathlib import Path
            filename = Path(val_norm).name if val_norm else "unknown"
            gr.Info(f"{filename} completed")
            
            yield (
                gr.update(interactive=False),
                gr.update(),
                gr.update(),  # Don't update here - let .then() handle it
                gr.update(),  # Don't update here - let .then() handle it
                gr.update(),  # Don't update here - let .then() handle it
                (nid, val_norm)  # Pass tuple: (target_node_id, result_path)
            )
        elif result_path:
            # User navigated away
            try:
                rows = _get_filtered_outline_rows(project_dict, nid)
                node_label = next((lbl for lbl, id in rows if id == nid), f"Node {nid}")
            except:
                node_label = f"Node {nid}"
                
            # gr.Info(f"✓ {node_label} (navigate back to view)")
            from pathlib import Path
            filename = Path(result_path).name if result_path else "unknown"
            gr.Info(f"{filename} completed")
            
            yield (
                gr.update(interactive=False),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                (nid, result_path)  # Pass tuple: (target_node_id, result_path)
            )
        else:
            # No result generated
            yield (
                gr.update(interactive=False),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                None  # No tuple needed for failed generation
            )

    yield (
        gr.update(interactive=True),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update()
    )

def _eh_conditional_image_refresh(project_dict: dict, loaded_nid: str, result_tuple):
    """Only update gallery/preview if user is still viewing the generated node."""
    if not result_tuple or not isinstance(result_tuple, tuple):
        return gr.update(), gr.update(), gr.update()
    
    target_nid, result_path = result_tuple
    if not result_path:
        return gr.update(), gr.update(), gr.update()
    
    # Only update if user is still on the node we generated for
    if loaded_nid != target_nid:
        return gr.update(), gr.update(), gr.update()
    
    # User is still here - update the UI
    from pathlib import Path
    import os
    
    current_files = _get_kf_gallery_images(project_dict, target_nid)
    choices_for_ui = [(os.path.basename(p), p) for p in current_files]
    
    # Normalize to find matching value
    val_norm = result_path
    try:
        target_abs = os.path.abspath(result_path).lower()
        for p in current_files:
            if os.path.abspath(p).lower() == target_abs:
                val_norm = p
                break
    except Exception:
        pass
    
    return gr.update(choices=choices_for_ui, value=val_norm), gr.update(value=val_norm), gr.update(value=val_norm)

def _eh_conditional_video_refresh(project_dict: dict, loaded_nid: str, result_tuple):
    """Only update gallery/player if user is still viewing the generated node. Also saves selection to project."""
    if not result_tuple or not isinstance(result_tuple, tuple):
        return project_dict, gr.update(), gr.update()
    
    target_nid, result_path = result_tuple
    if not result_path:
        return project_dict, gr.update(), gr.update()
    
    # Always save to project data, even if user navigated away
    data = copy.deepcopy(project_dict) if isinstance(project_dict, dict) else {}
    v, kind, _, _ = _resolve_node_context(data, target_nid)
    if kind == "vid" and v is not None:
        v["selected_video_path"] = result_path
    
    # Only update UI if user is still on the node we generated for
    if loaded_nid != target_nid:
        return data, gr.update(), gr.update()
    
    # User is still here - update the UI
    from pathlib import Path
    import os
    current_files = _get_vid_gallery_files(data, target_nid)
    choices_for_ui = [(os.path.basename(p), p) for p in current_files]
    
    # Normalize to find matching value
    norm_map = {str(Path(c).resolve()).lower(): c for c in current_files}
    val_norm = None
    try:
        result_resolved = str(Path(result_path).resolve()).lower()
        if result_resolved in norm_map:
            val_norm = norm_map[result_resolved]
    except Exception:
        pass
    
    if not val_norm:
        val_norm = result_path
    
    return data, gr.update(choices=choices_for_ui, value=val_norm), gr.update(value=val_norm)

def _eh_run_and_curate_video(project_dict: dict, nid: str, count: int, seed_input: str, path_at_start: str, current_selection: str):
    """
    Generate N videos (ADD mode).
    Default count = 1.
    Contextually updates UI only if user is still on the same node.
    """
    yield (
        gr.update(value=None, interactive=False),
        "Starting.",
        gr.update(),
        gr.update(),
        gr.update()
    )

    if not project_dict or not nid:
        yield (
            gr.update(interactive=True),
            "Error: Missing project or selection.",
            gr.update(),
            gr.update(),
            gr.update()
        )
        return

    # Parse seed: blank = None (random), otherwise integer
    seed_override = None
    if seed_input and seed_input.strip():
        try:
            seed_override = int(seed_input.strip())
        except ValueError:
            pass

    for i in range(max(1, int(count))):
        # Consume generator to execute generation

        gen = handle_test_video_generation(project_dict, nid, seed_override=seed_override)
        result_path = None
        for result in gen:
            # Trace: handle_test_video_generation yields (path, logs, update)
            if isinstance(result, tuple) and len(result) >= 2:
                current_log = result[1]
                yield (gr.update(interactive=False), current_log, gr.update(), gr.update(), gr.update())
                result_path = result[0]
            else:
                result_path = result

        # Ensure result_path is a string, not a tuple
        if isinstance(result_path, tuple):
            # result_path = result_path[1] if len(result_path) > 1 else result_path[0]
            result_path = result_path[0] if isinstance(result_path, tuple) else result_path
        
        # Check if user is still on the same node
        user_still_here = (current_selection == nid)

        if user_still_here and result_path:
            # Get node label for notification
            rows = _get_filtered_outline_rows(project_dict, nid)
            node_label = next((lbl for lbl, id in rows if id == nid), f"Node {nid}")
            
            # Get current file list from disk
            from pathlib import Path
            import os
            current_files = _get_vid_gallery_files(project_dict, nid)
            
            # Format as (filename, path) tuples (same as _dropdown_update_for_kf)
            choices_for_ui = [(os.path.basename(p), p) for p in current_files]
            
            # Normalize paths to find matching value
            norm_map = {str(Path(c).resolve()).lower(): c for c in current_files}
            val_norm = None
            if result_path:
                try:
                    result_resolved = str(Path(result_path).resolve()).lower()
                    if result_resolved in norm_map:
                        val_norm = norm_map[result_resolved]
                except Exception:
                    pass
            
            # Fallback to direct path if normalization didn't find a match
            if not val_norm:
                val_norm = result_path
            
            # gr.Info(f"✓ {node_label}")
            filename = Path(val_norm).name if val_norm else "unknown"
            gr.Info(f"{filename} completed")
            
            yield (
                gr.update(interactive=False),
                gr.update(),
                gr.update(),  # Don't update here - let .then() handle it
                gr.update(),  # Don't update here - let .then() handle it
                (nid, val_norm)  # Pass tuple: (target_node_id, result_path)
            )

        elif result_path:
            # User navigated away - silent update with notification
            rows = _get_filtered_outline_rows(project_dict, nid)
            node_label = next((lbl for lbl, id in rows if id == nid), f"Node {nid}")
            
            # gr.Info(f"✓ {node_label} (navigate back to view)")
            filename = Path(result_path).name if result_path else "unknown"
            gr.Info(f"{filename} completed")
            
            yield (
                gr.update(interactive=False),
                gr.update(),
                gr.update(),
                gr.update(),
                (nid, result_path)  # Pass tuple: (target_node_id, result_path)
            )
        else:
            # No result
            yield (
                gr.update(interactive=False),
                gr.update(), #f"Generated {i + 1}/{count}",
                gr.update(),
                gr.update(),
                None
            )

    yield (
        gr.update(interactive=True),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update()
    )

def _update_project_field(project_dict, path, value):
    """Update a project field using dot notation path (e.g., 'keyframe_generation.image_iterations_default')."""
    data = project_dict if isinstance(project_dict, dict) else {}
    data = _ensure_project(data)
    
    keys = path.split('.')
    current = data
    
    # Navigate to parent
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    
    # Set value
    try:
        current[keys[-1]] = int(value) if value is not None else 1
    except (ValueError, TypeError):
        current[keys[-1]] = 1
    
    # return json.dumps(data)
    return data


def build_editor_tab(preview: gr.Code, settings_json: gr.State, current_file_path: gr.State, generation_result_buffer: gr.State, features: Dict = {}):
    
    try: data0 = _ensure_project(_loads(preview.value or ""))
    except Exception: data0 = _ensure_project({})
    initial_rows = _rows_with_times(data0)
    initial_sel = initial_rows[0][1] if initial_rows else None
    
    
    with gr.Row():
        with gr.Column(scale=1, min_width=400):
            
            with gr.Group():
                with gr.Accordion("Manage Project Structure", open=False, elem_classes=["themed-accordion", "proj-theme"]):
                    # Row 1: Add Actions
                    with gr.Row(elem_classes=["structural-row"]):
                        add_seq_btn = gr.Button("+ Sequence", variant="secondary")
                        add_kf_btn = gr.Button("+ Keyframe", variant="secondary")
                    
                    # Row 2: Duplicate Actions
                    with gr.Row(elem_classes=["structural-row"]):
                        duplicate_seq_btn = gr.Button("Copy Sequence", variant="secondary")
                        duplicate_kf_btn = gr.Button("Copy Keyframe", variant="secondary")

                    # move
                    with gr.Row(elem_classes=["structural-row"]):
                        move_seq_btn = gr.Button("UP Seq", variant="secondary")
                        move_kf_btn = gr.Button("UP Key", variant="secondary")
                    
                    # move
                    with gr.Row(elem_classes=["structural-row"]):
                        move2_seq_btn = gr.Button("Down Seq", variant="secondary")
                        move2_kf_btn = gr.Button("Down Key", variant="secondary")
                    
                    # Row 3: Delete Actions
                    with gr.Row(elem_classes=["structural-row"]):
                        # Sequence Delete Column
                        with gr.Group() as seq_delete_ui:
                            del_seq_btn = gr.Button("Del Sequence", variant="secondary")
                            with gr.Group(visible=False) as seq_delete_confirm_group:
                                gr.Markdown("Delete?")
                                with gr.Row():
                                    confirm_del_seq_btn = gr.Button("Confirm", variant="stop")
                                    cancel_del_seq_btn = gr.Button("Cancel")
                        
                        # Keyframe Delete Column
                        with gr.Group() as kf_delete_ui:
                            del_kf_btn = gr.Button("Del Keyframe", variant="secondary")
                            with gr.Group(visible=False) as kf_delete_confirm_group:
                                gr.Markdown("Delete?")
                                with gr.Row():
                                    confirm_del_kf_btn = gr.Button("Confirm", variant="stop")
                                    cancel_del_kf_btn = gr.Button("Cancel")


            with gr.Column(elem_id="outline-list-container"):
                node_selector = gr.Radio(label="Select", choices=initial_rows, value=initial_sel, elem_id="outline_list", container=False, interactive=False)

            proj_len = gr.Markdown(_project_len_text(data0))

            with gr.Row():
                skip_up_btn = gr.Button("Skip Up", variant="secondary")
                skip_down_btn = gr.Button("Skip Down", variant="secondary")


        with gr.Column(scale=6):
            with gr.Group(visible=False) as seq_group:
                with gr.Row():
                    # Seq Middle Column
                    with gr.Column(scale=1): 
                        with gr.Accordion("Properties", open=True, elem_classes=["themed-accordion", "seq-theme"]) as seq_props:
                            with gr.Row():
                                seq_open_start = gr.Checkbox(False, label="Open start")
                                seq_open_end   = gr.Checkbox(True,  label="Open end")
                                with gr.Row():
                                    seq_flip_btn = gr.Button("↔ Flip Orientation", variant="secondary", size="sm")
                            seq_setting_dd = gr.Dropdown(label="Location", choices=[("", "")], info="Define these in the Assets tab", value="", interactive=True, allow_custom_value=False, filterable=False)
                            seq_setting_md = gr.Textbox(label="Location modifier prompt", info="Additional modifiers apply only to this sequence, ie. Day/Night", lines=1, interactive=True, scale=3)
                            seq_lora = gr.Dropdown(label="Inject LoRA", info="Injects into Style prompt, but can be copied into any prompt", choices=[], interactive=True, scale=1)
                            seq_style_dd = gr.Dropdown(label="Style",  info="Define these in the Assets tab", choices=[("", "")], value="", interactive=True, allow_custom_value=False, filterable=False)
                            seq_style_prompt_md = gr.Textbox(label="Style Prompt", info="Additional modifiers apply only to this sequence, ie. Camera style",  lines=1, interactive=True)
                            seq_action_prompt_md = gr.Textbox(label="Sequence In-between Positive Prompt", info="Applies individually to every in-between, use for consistency not narrative", lines=1, interactive=True)
                            # seq_len = gr.Markdown("Sequence length: 0 sec", visible=True)
                        

                        with gr.Accordion("Status", open=False, elem_classes=["themed-accordion", "seq-theme"]):
                            seq_run_status = build_run_status_ui()
                            with gr.Row():
                                seq_cancel_kf_btn = gr.Button("Cancel Keyframes", variant="stop")
                                seq_cancel_ib_btn = gr.Button("Cancel In-betweens", variant="stop")

                            # with gr.Row():
                                # seq_cascade_cancel_btn = gr.Button("Cancel Cascade", variant="stop", visible=features.get("show_cascade_batches", True))                                

                        with gr.Accordion("Bridges", open=False, visible=features.get("show_bridges", False), elem_classes=["themed-accordion", "seq-theme"]):
                            seq_bridge_purge = gr.Button("Purge Sequence Bridges", variant="stop")
                            seq_bridge_batch_btn = gr.Button("Batch Sequence Bridges", variant="primary") 
                            seq_bridge_mgr = build_bridge_manager(scope="sequence")

                        with gr.Accordion("Upscaling", open=False, elem_classes=["themed-accordion", "seq-theme"]):
                            seq_enhance = build_enhance_manager()

                        with gr.Accordion("Exports", open=False, elem_classes=["themed-accordion", "seq-theme"]):
                            seq_export_mgr = build_export_panel(scope="sequence")


                    # Seq Right Column
                    with gr.Column(scale=1):
                        with gr.Accordion("Keyframe Batch", open=False, elem_classes=["themed-accordion", "kf-theme"]):
                            seq_kf_purge = build_purge_ui("seq_kf", "Keyframe")
                            seq_kf_inputs = build_batch_inputs("seq_kf", "Keyframe", is_interactive=True)
                            seq_kf_batch_btn = build_batch_run_btn("seq_kf", "Keyframe")
                            with gr.Row():
                                seq_pose_batch_btn = gr.Button("Batch Poses", variant="secondary", visible=features.get("show_pose_automation", False))
                                seq_qc_batch_btn = gr.Button("QC Delete Batch", variant="stop", visible=features.get("show_QC", False))

                        with gr.Accordion("In-between Batch", open=False, elem_classes=["themed-accordion", "vid-theme"]):
                            seq_ib_purge = build_purge_ui("seq_ib", "In-betweens")
                            seq_ib_inputs = build_batch_inputs("seq_ib", "In-betweens", is_interactive=True)
                            seq_ib_batch_btn = build_batch_run_btn("seq_ib", "In-betweens")

                        with gr.Accordion("Cascade", open=False, visible=features.get("show_cascade_batches", True), elem_classes=["themed-accordion", "seq-theme"]):
                            with gr.Row():
                                seq_cascade_kf_iter = gr.Number(
                                    label="Keyframe passes",
                                    info="Number of cascade iterations",
                                    value=1,
                                    precision=0,
                                    minimum=0,
                                    interactive=True,
                                )
                                seq_cascade_vid_iter = gr.Number(
                                    label="Videos per pass",
                                    info="Videos to generate per pass",
                                    value=1,
                                    precision=0,
                                    minimum=0,
                                    interactive=True,
                                )
                            with gr.Row():
                                seq_cascade_btn = gr.Button("Run Cascade", variant="primary")
                                seq_cascade_cancel_btn = gr.Button("Cancel Cascade", variant="stop")

                        with gr.Accordion("Sequence Assets", open=False, elem_classes=["themed-accordion", "seq-theme"]) as seq_assets_accordion:
                            seq_assets_display = gr.HTML(value="<div style='color:#888; padding:10px;'>Select a sequence to view assets</div>")
                            seq_len = gr.Markdown("Sequence length: 0 sec", visible=True)


            # --- GROUP 2: KEYFRAME MODE ---
            with gr.Group(visible=False) as kf_group:
                with gr.Row():
                    # KF Middle Column
                    with gr.Column(scale=1):
                        with gr.Accordion("Properties", open=True, elem_classes=["themed-accordion", "kf-theme"]) as kf_props:

                            with gr.Column(variant="panel"):
                                header = gr.Markdown("Pose")

                                with gr.Row(equal_height=False):
                                    kf_pose_preview = gr.Image(label="Current Pose", visible=True, interactive=False, show_label=True, height=120, scale=0, min_width=140, show_download_button=False)
                                    kf_pose = gr.Textbox(label="Pose Path", visible=False, interactive=True)
                                    with gr.Column(scale=1, min_width=120,elem_classes=["pose-buttons-col"]):
                                        kf_clear_pose_btn = gr.Button("Clear Pose",  variant="secondary")
                                        kf_generate_pose_btn = gr.Button("Auto Generate Pose",  variant="secondary")
                                        kf_pose_upload_btn = gr.UploadButton(
                                            "Upload Pose", 
                                            file_types=["image"], 
                                            file_count="single",
                                            variant="secondary"
                                        )
                                with gr.Row():
                                    kf_flip_horiz = gr.Checkbox(label="Flip Horizontal", value=False)                        
                                    kf_copy_pose_prompt_btn = gr.Button("Get Pose Prompt", variant="secondary", visible=features.get("show_generation_info", False))

                                with gr.Row():

                                    with gr.Accordion("Pose Library and Options", open=False, elem_classes=["themed-accordion", "kf-theme"]):

                                        with gr.Column(variant="panel"):
                                            subheader = gr.Markdown("<span style='font-size: 0.85em; font-style: italic; color: #999;'>Library manager is found in Assets</span>")

                                            with gr.Row():
                                                # kf_pose_gallery = gr.Gallery(label="Pose Library", height=200, columns=4, interactive=True, show_label=True, object_fit="contain", elem_id="kf_pose_gallery")
                                                kf_pose_gallery = gr.Gallery(label="Pose Library", height=200, columns=4, interactive=True, show_label=True, object_fit="contain", elem_id="kf_pose_gallery", allow_preview=False)
                                                
                                            with gr.Row():
                                                # kf_flip_horiz = gr.Checkbox(label="Flip Horizontal", value=False)
                                                kf_flip_vert = gr.Checkbox(label="Flip Vertical", value=False)
                                        # Pose Group
                                        with gr.Column(variant="panel"):
                                            with gr.Row():
                                                with gr.Column():
                                                    with gr.Row():
                                                        kf_cn_pose_enable = gr.Checkbox(label="Pose", value=(DEFAULT_KF_CN_SETTINGS["1"]["switch"] == "On"))
                                                        kf_cn_pose_animal = gr.Checkbox(label="Animal", value=DEFAULT_KF_USE_ANIMAL_POSE)
                                                    kf_cn_pose_strength = gr.Slider(label="Strength", value=DEFAULT_KF_CN_SETTINGS["1"]["strength"], minimum=0.0, maximum=1.0, step=0.01)
                                                    kf_cn_pose_start = gr.Slider(label="Start %", value=DEFAULT_KF_CN_SETTINGS["1"]["start_percent"], minimum=0.0, maximum=1.0, step=0.01, visible=False)
                                                    kf_cn_pose_end = gr.Slider(label="Scope", value=DEFAULT_KF_CN_SETTINGS["1"]["end_percent"], minimum=0.0, maximum=1.0, step=0.01)
                                                with gr.Column(scale=0, min_width=150):
                                                    kf_cn_pose_thumb = gr.Image(show_label=False, interactive=False, show_download_button=False, height=140, container=False)

                                        # Shape Group
                                        with gr.Column(variant="panel"):
                                            with gr.Row():
                                                with gr.Column():
                                                    kf_cn_shape_enable = gr.Checkbox(label="Shape", value=(DEFAULT_KF_CN_SETTINGS["2"]["switch"] == "On"))
                                                    kf_cn_shape_strength = gr.Slider(label="Strength", value=DEFAULT_KF_CN_SETTINGS["2"]["strength"], minimum=0.0, maximum=1.0, step=0.01)
                                                    kf_cn_shape_start = gr.Slider(label="Start %", value=DEFAULT_KF_CN_SETTINGS["2"]["start_percent"], minimum=0.0, maximum=1.0, step=0.01, visible=False)
                                                    kf_cn_shape_end = gr.Slider(label="Scope", value=DEFAULT_KF_CN_SETTINGS["2"]["end_percent"], minimum=0.0, maximum=1.0, step=0.01)
                                                with gr.Column(scale=0, min_width=150):
                                                    kf_cn_shape_thumb = gr.Image(show_label=False, interactive=False, show_download_button=False, height=140, container=False)

                                        # Outline Group
                                        with gr.Column(variant="panel"):
                                            with gr.Row():
                                                with gr.Column():
                                                    kf_cn_outline_enable = gr.Checkbox(label="Outline", value=(DEFAULT_KF_CN_SETTINGS["3"]["switch"] == "On"))
                                                    kf_cn_outline_strength = gr.Slider(label="Strength", value=DEFAULT_KF_CN_SETTINGS["3"]["strength"], minimum=0.0, maximum=1.0, step=0.01)
                                                    kf_cn_outline_start = gr.Slider(label="Start %", value=DEFAULT_KF_CN_SETTINGS["3"]["start_percent"], minimum=0.0, maximum=1.0, step=0.01, visible=False)
                                                    kf_cn_outline_end = gr.Slider(label="Scope", value=DEFAULT_KF_CN_SETTINGS["3"]["end_percent"], minimum=0.0, maximum=1.0, step=0.01)
                                                with gr.Column(scale=0, min_width=150):
                                                    kf_cn_outline_thumb = gr.Image(show_label=False, interactive=False, show_download_button=False, height=140, container=False)


                            with gr.Row():
                                kf_char_left = gr.Dropdown([("", "")], value="", label="Character (primary/left)", info="Use this for most workflows", filterable=False, allow_custom_value=False)
                                kf_char_right = gr.Dropdown([("", "")], value="", label="Character (secondary/right)", info="Only for 2CHAR poses/workflow", filterable=False, allow_custom_value=False)
                            kf_prompt = gr.Textbox(label="Prompt")
                            with gr.Accordion("Advanced", open=False, elem_classes=["themed-accordion", "kf-theme"]):
                                kf_lora = gr.Dropdown(label="Inject LoRA", choices=[], interactive=True)
                                with gr.Row():
                                    kf_neg_left = gr.Textbox(label="Negative (left)")
                                    kf_neg_right = gr.Textbox(label="Negative (right)")
                                    kf_neg_heal = gr.Textbox(label="Negative (heal)")
                                kf_workflow_json = gr.Dropdown(label="Workflow", choices=[""], value="", interactive=True, filterable=False, allow_custom_value=False)
                                kf_template = gr.Textbox(label="Template", visible=False) 
                                kf_load_params_btn = gr.Button("Load Properties from Selected Image", variant="secondary")

                        with gr.Accordion("Status", open=False, elem_classes=["themed-accordion", "kf-theme"]) as kf_batch_accordion:
                            kf_run_status = build_run_status_ui()

                        with gr.Accordion("Bridge", open=False, elem_classes=["themed-accordion", "kf-theme"], visible=features.get("show_bridges", True)):

                            kf_join_smoothing = gr.Slider(label="Join Smoothing", info="1: Simple transition. 2-5: Generative blend.", minimum=1, maximum=5, step=1, value=1, interactive=True)
                            kf_join_offset = gr.Slider(label="Join Offset", info="Shift the transition point (frames)", minimum=-6, maximum=6, step=1, value=0, interactive=True)
                            kf_bridge_mgr = build_bridge_manager(scope="sequence")
                            gr.Markdown("---")
                            kf_bridge_video = gr.Video(label="Bridge Preview", interactive=False, autoplay=True, loop=True, visible=False, height=200)
                            kf_bridge_gallery = gr.Gallery(label="Bridge Frames", rows=1, columns=6, height=200, allow_preview=True, interactive=False, visible=False)
                            kf_bridge_purge_btn = gr.Button("Purge Bridge", variant="stop", size="sm")

                    # KF Right Column
                    with gr.Column(scale=1):
                        with gr.Accordion("Generations", open=True, elem_classes=["themed-accordion", "kf-theme"]):
                            with gr.Group():
                                with gr.Row():
                                    kf_generate_count = gr.Number(label="Iterations",  value=1, precision=0, minimum=1, interactive=True)  #info="Closing browser may disrupt this generation.",
                                    kf_seed_input = gr.Textbox(label="Seed", placeholder="Random", value="", interactive=True, max_lines=1,  visible=features.get("show_generation_info", False))
                                with gr.Row():
                                    test_gen_btn = gr.Button("Generate Keyframe Image", variant="primary")
                                    kf_qc_btn = gr.Button("QC Keyframe Image", variant="secondary", visible=features.get("show_QC", False))

                            with gr.Group():
                                kf_gallery = gr.Dropdown(label="Selected Keyframe Image", choices=[], value=None, interactive=True, allow_custom_value=False, filterable=False) 

                            
                                with gr.Row():
                                    kf_prev_img_btn = gr.Button("Select Previous", variant="secondary")
                                    kf_next_img_btn = gr.Button("Select Next", variant="secondary")

                                main_preview_image = gr.Image(show_label=False, type="filepath", interactive=False)

                            # with gr.Accordion("Manage Files", open=False, elem_classes=["themed-accordion", "stop-theme"]):
                            #     kf_upload_btn = gr.UploadButton("Upload Image", file_count="single", file_types=["image"])
                            #     with gr.Row():
                            #         kf_delete_img_btn = gr.Button("Delete Selected ", variant="stop")
                            #         kf_del_others_btn = gr.Button("Delete All Others", variant="stop", visible=features.get("show_delete_others", False))

                            # # with gr.Group() as kf_tools:
                            #     test_gen_image = gr.Image(label="Keyframe", interactive=False, height=240, visible=False) 
                            #     # kf_refresh_status_btn = gr.Button("Refresh Status", variant="secondary", size="sm") 
                            with gr.Accordion("Manage Files", open=False, elem_classes=["themed-accordion", "stop-theme"]):
                                kf_upload_btn = gr.UploadButton("Upload Image", file_count="single", file_types=["image"])
                                with gr.Row():
                                    kf_delete_img_btn = gr.Button("Delete Selected ", variant="stop")
                                    kf_del_others_btn = gr.Button("Delete All Others", variant="stop", visible=features.get("show_delete_others", False))

                            with gr.Accordion("Execution Info", open=False, elem_classes=["themed-accordion", "kf-theme"],visible=features.get("show_generation_info", False)):
                                kf_exec_load_btn = gr.Button("Load from Selected", variant="secondary")
                                kf_exec_info = gr.Markdown("")

                            # with gr.Group() as kf_tools:
                                test_gen_image = gr.Image(label="Keyframe", interactive=False, height=240, visible=False)


            # --- GROUP 3: IN-BETWEEN MODE ---
            with gr.Group(visible=False) as vid_group:
                with gr.Row():
                    # IB Middle Column
                    with gr.Column(scale=1):
                        with gr.Accordion("Properties", open=True, elem_classes=["themed-accordion", "vid-theme"]) as vid_props:
                            with gr.Row():
                                with gr.Column(scale=1, min_width=10):
                                    vid_start_image = gr.Image(label="Start Image", interactive=False, height=101)
                                with gr.Column(scale=1, min_width=10):
                                    vid_end_image = gr.Image(label="End Image", interactive=False, height=101)
                            with gr.Row():
                                with gr.Group(elem_classes="vid-inspector-controls"):
                                    vid_length = gr.Radio(DUR_CHOICES, label="Clip Length (seconds)", value=None, elem_classes="vid-length-radio")
                                    clear_vid_len_btn = gr.Button("Default (3 sec)") 
                            vid_prompt = gr.Textbox(label="In-between prompt", info="Applies only to this In-bewteen", lines=1, interactive=True)
                            with gr.Accordion("Advanced", open=False):
                                vid_lora = gr.Dropdown(label="Add a LoRA", info="Wan2.2 two-part loras must be named _low_noise/_high_noise or be registered in scripts/lora_registry.py.  Pick either here.", choices=[], interactive=True)
                                vid_neg = gr.Textbox(label="Negative prompt")
                                vid_load_params_btn = gr.Button("Load Properties from Selected Video",  variant="secondary")
                        with gr.Accordion("Status", open=False, elem_classes=["themed-accordion", "vid-theme"]) as vid_batch_accordion:
                            vid_run_status = build_run_status_ui()
                    with gr.Column(scale=1):
                        with gr.Accordion("Generations", open=True, elem_classes=["themed-accordion", "vid-theme"]):
                            with gr.Group():
                                with gr.Row():
                                    vid_generate_count = gr.Number(label="Iterations", value=1, precision=0, minimum=1, interactive=True)  #, info="This many will be generated as long as browser is open"
                                    vid_seed_input = gr.Textbox(label="Seed", placeholder="random", value="", interactive=True, max_lines=1, visible=features.get("show_generation_info", False))

                                test_vid_btn = gr.Button("Generate In-between Video", variant="primary")
                            with gr.Group():                                
                                with gr.Row():
                                    vid_gallery_dd = gr.Dropdown(show_label=False, label="Selected Video", interactive=True, filterable=False, allow_custom_value=False)
                                with gr.Row():
                                    vid_prev_clip_btn = gr.Button("Select Previous", variant="secondary")
                                    vid_next_clip_btn = gr.Button("Select Next", variant="secondary")
                                # proj_w = data0.get("project", {}).get("width", 1280)
                                # proj_h = data0.get("project", {}).get("height", 720)
                                # vid_player = gr.Video(show_label=False, interactive=False, autoplay=True, loop=True, show_share_button=False, width=proj_w, height=proj_h)
                                vid_player = gr.Video(show_label=False, interactive=False, autoplay=True, loop=True, show_share_button=False, elem_classes=["constrained-video"])
                                # vid_player = gr.Video(show_label=False, interactive=False, autoplay=True, loop=True, show_share_button=False)


                            with gr.Accordion("Manage Files", open=False, elem_classes=["themed-accordion", "stop-theme"]):
                                with gr.Row():
                                    vid_delete_btn = gr.Button("Delete Selected", variant="stop")
                                    vid_del_others_btn = gr.Button("Delete All Others", variant="stop", visible=features.get("show_delete_others", False))
                                test_vid_player = gr.Video(label="In-between", interactive=False, autoplay=True, loop=True, show_share_button=False, visible=False)
                            with gr.Accordion("Execution Info", open=False, elem_classes=["themed-accordion", "vid-theme"], visible=features.get("show_generation_info", False)):
                                vid_exec_load_btn = gr.Button("Load from Selected", variant="secondary")
                                vid_exec_info = gr.Markdown("")

            # EVENT BINDING (Retaining Original Logic using new Handlers)
            
            selected_node = gr.State(value=initial_sel)
            loaded_node_id = gr.State(value=initial_sel)  # Tracks which node the form was loaded FOR
            loaded_project_name = gr.State(value=data0.get("project", {}).get("name", ""))  # Tracks which project
            outline_sig = gr.State(value=_outline_signature(data0))
            kf_gallery_selection = gr.State(value=None) 
            dummy_rerun_trigger = gr.State(value="")

            # Helper for constructing node_selector output list (order matters!)
            node_selector_outputs = [
                node_selector, selected_node, loaded_node_id, loaded_project_name, seq_group, kf_group, vid_group,
                seq_setting_dd, seq_setting_md, seq_style_dd, seq_style_prompt_md, seq_lora, seq_action_prompt_md, seq_open_start, seq_open_end,
                gr.State(), # placeholder for clear_assets_btn
                kf_pose, kf_pose_preview,
                kf_cn_pose_enable, kf_cn_pose_thumb, kf_cn_pose_animal, kf_flip_horiz, kf_flip_vert, kf_cn_pose_strength, kf_cn_pose_start, kf_cn_pose_end,
                kf_cn_shape_enable, kf_cn_shape_thumb, kf_cn_shape_strength, kf_cn_shape_start, kf_cn_shape_end,
                kf_cn_outline_enable, kf_cn_outline_thumb, kf_cn_outline_strength, kf_cn_outline_start, kf_cn_outline_end,
                kf_char_left, kf_char_right, kf_prompt, kf_template, kf_workflow_json, kf_neg_left, kf_neg_right, kf_neg_heal, kf_join_smoothing, kf_join_offset, kf_lora,
                vid_start_image, vid_end_image, vid_length, vid_prompt, vid_neg, clear_vid_len_btn,
                seq_len, main_preview_image, kf_gallery, vid_gallery_dd, vid_player, vid_lora,
                gr.State(), # seq_status placeholder
                seq_kf_inputs["seq_kf_iter"], seq_ib_inputs["seq_ib_iter"],  # batch iteration controls
                kf_bridge_gallery, kf_bridge_video, kf_delete_confirm_group,
                gr.State(), # seq_lora reset
                kf_pose_gallery
            ]

            node_selector.change(
                _eh_node_selected,
                inputs=[preview, node_selector, selected_node],
                outputs=node_selector_outputs,
                show_progress="hidden", queue=True
            )
            
            # Rehydrate / Refresh logic
            gr.on(triggers=[], fn=_rehydrate_if_changed, inputs=[preview, selected_node, outline_sig], outputs=[node_selector, selected_node, proj_len, outline_sig], show_progress="hidden", queue=False)
            # preview.change(_rehydrate_if_changed, inputs=[preview, selected_node, outline_sig], outputs=[node_selector, selected_node, proj_len, outline_sig], show_progress="hidden", queue=False)
            
            # Sequence Field Bindings
            # seq_text_fields_inputs = [preview, loaded_node_id, seq_setting_md, seq_style_prompt_md, seq_action_prompt_md]
            seq_text_fields_inputs = [preview, loaded_node_id, loaded_project_name, seq_setting_md, seq_style_prompt_md, seq_action_prompt_md]
            for comp in [seq_setting_md, seq_style_prompt_md, seq_action_prompt_md]:
                comp.change(_eh_seq_text_fields, seq_text_fields_inputs, [preview], show_progress="hidden", queue=True)

            seq_setting_dd.change(fn=lambda t, n, p, v: _update_seq_field(t, n, p, "setting_id", v), inputs=[preview, loaded_node_id, loaded_project_name, seq_setting_dd], outputs=[preview], queue=True)            # LoRA Injectors
            seq_lora.change(_eh_inject_lora, inputs=[preview, selected_node, seq_lora, seq_style_prompt_md], outputs=[preview, seq_style_prompt_md, seq_lora], show_progress="hidden", queue=False)
            kf_lora.change(_eh_inject_lora, inputs=[preview, selected_node, kf_lora, kf_prompt], outputs=[preview, kf_prompt, kf_lora], show_progress="hidden", queue=False)
            vid_lora.change(_eh_inject_lora, inputs=[preview, selected_node, vid_lora, vid_prompt], outputs=[preview, vid_prompt, vid_lora], show_progress="hidden", queue=False)
            
            # seq_style_dd.change(fn=lambda t, n, v: _update_seq_field(t, n, "style_id", v), inputs=[preview, selected_node, seq_style_dd], outputs=[preview], queue=False)
            seq_style_dd.change(fn=lambda t, n, p, v: _update_seq_field(t, n, p, "style_id", v), inputs=[preview, loaded_node_id, loaded_project_name, seq_style_dd], outputs=[preview], queue=True)


            # seq_open_start.change(lambda pre, nid, v: _eh_open_flag(pre, nid, "open_start", v), [preview, selected_node, seq_open_start], [preview, node_selector, selected_node, proj_len, seq_len], show_progress="hidden", queue=False)
            seq_open_start.change(lambda pre, nid, proj, v: _eh_open_flag(pre, nid, proj, "open_start", v), [preview, loaded_node_id, loaded_project_name, seq_open_start], [preview, node_selector, selected_node, proj_len, seq_len], show_progress="hidden", queue=True)
            # Sequence Assets display - refresh when node selection changes
            def _update_seq_assets(pre, raw_value):
                data = pre if isinstance(pre, dict) else {}
                nid = raw_value
                # Get parent sequence ID if a child node is selected
                if nid:
                    _, kind, _, parent_id = _resolve_node_context(data, nid)
                    seq_id = nid if kind == "seq" else parent_id
                else:
                    seq_id = None
                return _build_sequence_assets_html(data, seq_id)
            
            node_selector.change(
                fn=_update_seq_assets,
                inputs=[preview, node_selector],
                outputs=[seq_assets_display],
                show_progress="hidden"
            )


            # seq_open_end.change(lambda pre, nid, v: _eh_open_flag(pre, nid, "open_end", v), [preview, selected_node, seq_open_end], [preview, node_selector, selected_node, proj_len, seq_len], show_progress="hidden", queue=False)
            seq_open_end.change(lambda pre, nid, proj, v: _eh_open_flag(pre, nid, proj, "open_end", v), [preview, loaded_node_id, loaded_project_name, seq_open_end], [preview, node_selector, selected_node, proj_len, seq_len], show_progress="hidden", queue=True)
            # seq_flip_btn.click(_eh_flip_orientation, [preview, selected_node], [preview, node_selector, selected_node, proj_len, seq_len], show_progress="hidden", queue=False)
            seq_flip_btn.click(_eh_flip_orientation, [preview, loaded_node_id, loaded_project_name], [preview, node_selector, selected_node, proj_len, seq_len], show_progress="hidden", queue=False)
            # Keyframe Field Bindings
            kf_all_fields_inputs = [ 
                preview, loaded_node_id, loaded_project_name, kf_pose,
                kf_cn_pose_enable, kf_cn_pose_animal, kf_flip_horiz, kf_flip_vert, kf_cn_pose_strength, kf_cn_pose_start, kf_cn_pose_end,
                kf_cn_shape_enable, kf_cn_shape_strength, kf_cn_shape_start, kf_cn_shape_end,
                kf_cn_outline_enable, kf_cn_outline_strength, kf_cn_outline_start, kf_cn_outline_end,
                kf_prompt, kf_template, kf_workflow_json,
                kf_neg_left, kf_neg_right, kf_neg_heal,
                kf_join_smoothing, kf_join_offset,
                kf_char_left, kf_char_right
            ]
            
            for comp in kf_all_fields_inputs[3:]: # Skip first 3
                if isinstance(comp, gr.components.Textbox):
                    comp.blur(_eh_kf_fields, kf_all_fields_inputs, [preview], show_progress="hidden", queue=True)
                # else:
                #     comp.change(_eh_kf_fields, kf_all_fields_inputs, [preview], show_progress="hidden", queue=True)
            
            # Character dropdowns need explicit .change() handlers (dropdowns don't have .blur())
            # But must guard against firing during navigation when values are cleared
            def _eh_char_change_guarded(project_dict, loaded_nid, loaded_proj, *fields):
                data = project_dict if isinstance(project_dict, dict) else {}
                _, kind, _, _ = _resolve_node_context(data, loaded_nid)
                if kind != "kf":
                    return data  # Not on a keyframe, skip
                return _eh_kf_fields(project_dict, loaded_nid, loaded_proj, *fields)
            
            for comp in [kf_char_left, kf_char_right, kf_flip_horiz, kf_flip_vert]:
                comp.change(_eh_char_change_guarded, kf_all_fields_inputs, [preview], show_progress="hidden", queue=True)




            # for comp in kf_all_fields_inputs[3:]: # Skip first 3
            #     if isinstance(comp, gr.components.Textbox):
            #         comp.blur(_eh_kf_fields, kf_all_fields_inputs, [preview], show_progress="hidden", queue=True)
                # else:
                #     comp.change(_eh_kf_fields, kf_all_fields_inputs, [preview], show_progress="hidden", queue=True)

            # kf_pose.change(
            #     _eh_handle_pose_change,
            #     inputs=[kf_pose, kf_workflow_json, preview, loaded_node_id],
            #     outputs=[kf_pose_preview, kf_cn_pose_animal, kf_workflow_json, kf_cn_pose_thumb, kf_cn_shape_thumb, kf_cn_outline_thumb],
            #     queue=False, show_progress="hidden"
            # )
            # Removed .then(_eh_kf_fields) - was causing save cascade during navigation


            kf_pose_gallery.select(
                fn=_eh_pose_gallery_select, 
                inputs=[preview, kf_pose_gallery], 
                outputs=[kf_pose], 
                show_progress="hidden"
            ).then(
                _eh_kf_fields,
                inputs=kf_all_fields_inputs,
                outputs=[preview],
                show_progress="hidden"
            ).then(
                _eh_refresh_pose_previews,
                inputs=[preview, loaded_node_id],
                outputs=[kf_pose_preview, kf_pose_gallery, kf_cn_pose_thumb, kf_cn_shape_thumb, kf_cn_outline_thumb, kf_workflow_json, kf_cn_pose_animal],
                show_progress="hidden"
            )
            # kf_clear_pose_btn.click(fn=_eh_clear_pose, inputs=[kf_pose_gallery], outputs=[kf_pose, kf_pose_gallery], show_progress="hidden", queue=False)
            kf_clear_pose_btn.click(
                fn=_eh_clear_pose, 
                inputs=[preview, loaded_node_id, loaded_project_name], 
                outputs=[
                    preview, kf_pose, kf_pose_preview,
                    kf_cn_pose_thumb, kf_cn_shape_thumb, kf_cn_outline_thumb,
                    kf_flip_horiz, kf_flip_vert,
                    kf_cn_pose_enable, kf_cn_pose_strength, kf_cn_pose_start, kf_cn_pose_end,
                    kf_cn_shape_enable, kf_cn_shape_strength, kf_cn_shape_start, kf_cn_shape_end,
                    kf_cn_outline_enable, kf_cn_outline_strength, kf_cn_outline_start, kf_cn_outline_end,
                ], 
                show_progress="hidden", 
                queue=False
            )
            
            kf_generate_pose_btn.click(
                fn=_eh_generate_pose_for_keyframe,
                inputs=[preview, selected_node, kf_prompt, kf_char_left],
                outputs=[kf_pose, kf_pose_preview, kf_pose_gallery, kf_cn_pose_thumb, kf_cn_shape_thumb, kf_cn_outline_thumb]
            )
            kf_pose_upload_btn.upload(
                fn=_eh_upload_pose_for_keyframe,
                inputs=[preview, selected_node, kf_pose_upload_btn],
                outputs=[kf_pose, kf_pose_preview, kf_pose_gallery, kf_cn_pose_thumb, kf_cn_shape_thumb, kf_cn_outline_thumb]
            )

            # Video Field Bindings
            # vid_all_fields_inputs = [preview, selected_node, vid_length, vid_prompt, vid_neg]
            vid_all_fields_inputs = [preview, loaded_node_id, loaded_project_name, vid_length, vid_prompt, vid_neg]
            
            # Change on radio triggers save THEN UI refresh to update labels
            vid_length.change(
                _eh_vid_fields, 
                vid_all_fields_inputs, 
                [preview], 
                show_progress="hidden", 
                queue=False
            ).then(
                fn=_eh_node_selected,
                inputs=[preview, selected_node, selected_node],
                outputs=node_selector_outputs,
                show_progress="hidden"
            )

            vid_prompt.blur(_eh_vid_fields, vid_all_fields_inputs, [preview], show_progress="hidden", queue=True)

            # Reset button clears the value and refreshes labels
            clear_vid_len_btn.click(
                fn=_eh_reset_vid_length,
                inputs=[preview, loaded_node_id, loaded_project_name, vid_prompt, vid_neg],
                outputs=[preview, selected_node],
                show_progress="hidden"
            ).then(
                fn=_eh_node_selected,
                inputs=[preview, selected_node, selected_node],
                outputs=node_selector_outputs,
                show_progress="hidden"
            )
            vid_neg.blur(_eh_vid_fields, vid_all_fields_inputs, [preview], show_progress="hidden", queue=True)

            # Batch Iteration Edit Handlers
            seq_kf_inputs["seq_kf_iter"].change(
                fn=lambda pj, val: _update_project_field(pj, "keyframe_generation.image_iterations_default", val),
                inputs=[preview, seq_kf_inputs["seq_kf_iter"]],
                outputs=[preview],
                show_progress="hidden",
                queue=False
            )
            
            seq_ib_inputs["seq_ib_iter"].change(
                fn=lambda pj, val: _update_project_field(pj, "inbetween_generation.video_iterations_default", val),
                inputs=[preview, seq_ib_inputs["seq_ib_iter"]],
                outputs=[preview],
                show_progress="hidden",
                queue=False
            )


            # CRUD Actions
            add_seq_btn.click(_eh_add_sequence, inputs=[preview, selected_node], outputs=[preview, node_selector, selected_node, proj_len, seq_len], show_progress="hidden", queue=False)
            duplicate_seq_btn.click(_eh_duplicate_sequence, [preview, selected_node], [preview, node_selector, selected_node, proj_len], show_progress="hidden", queue=False)
            move_seq_btn.click(_eh_move_sequence_up, [preview, selected_node], [preview, node_selector, selected_node, proj_len], show_progress="hidden", queue=False)
            move2_seq_btn.click(_eh_move_sequence_down, [preview, selected_node], [preview, node_selector, selected_node, proj_len], show_progress="hidden", queue=False)

            del_seq_btn.click(lambda: (gr.update(visible=False), gr.update(visible=True)), outputs=[del_seq_btn, seq_delete_confirm_group])
            cancel_del_seq_btn.click(lambda: (gr.update(visible=True), gr.update(visible=False)), outputs=[del_seq_btn, seq_delete_confirm_group])
            confirm_del_seq_btn.click(_eh_delete_sequence, [preview, selected_node], [preview, node_selector, selected_node, proj_len, seq_len], show_progress="hidden", queue=False).then(lambda: (gr.update(visible=True), gr.update(visible=False)), outputs=[del_seq_btn, seq_delete_confirm_group])



            add_kf_btn.click(_eh_add_kf, inputs=[preview, selected_node], outputs=[preview, node_selector, selected_node, proj_len], show_progress="hidden", queue=False)
            duplicate_kf_btn.click(_eh_duplicate_kf, [preview, selected_node], [preview, node_selector, selected_node, proj_len], show_progress="hidden", queue=False)
            move_kf_btn.click(_eh_move_keyframe_up, [preview, selected_node], [preview, node_selector, selected_node, proj_len], show_progress="hidden", queue=False)
            move2_kf_btn.click(_eh_move_keyframe_down, [preview, selected_node], [preview, node_selector, selected_node, proj_len], show_progress="hidden", queue=False)
            del_kf_btn.click(lambda: (gr.update(visible=False), gr.update(visible=True)), outputs=[del_kf_btn, kf_delete_confirm_group])
            cancel_del_kf_btn.click(lambda: (gr.update(visible=True), gr.update(visible=False)), outputs=[del_kf_btn, kf_delete_confirm_group])
            confirm_del_kf_btn.click(_eh_delete_kf, [preview, selected_node], [preview, node_selector, selected_node, proj_len], show_progress="hidden", queue=False).then(lambda: (gr.update(visible=True), gr.update(visible=False)), outputs=[del_kf_btn, kf_delete_confirm_group])


            # test_vid_btn.click(
            test_gen_btn.click(
                cb_save_project,
                inputs=[current_file_path, preview, settings_json],
                outputs=[]
            ).then(
                _eh_run_and_curate_image,
                inputs=[preview, selected_node, kf_generate_count, kf_seed_input, current_file_path, selected_node],
                outputs=[
                    test_gen_image,
                    kf_run_status["status_window"],
                    kf_gallery,
                    main_preview_image,
                    kf_gallery_selection,
                    generation_result_buffer
                ]
            ).then(
                _eh_conditional_image_refresh,
                inputs=[preview, loaded_node_id, generation_result_buffer],
                outputs=[kf_gallery, main_preview_image, kf_gallery_selection],
                show_progress="hidden"
            )

            kf_qc_btn.click(
                fn=lambda: gr.update(open=True),
                inputs=[],
                outputs=[kf_batch_accordion]
            ).then(
                fn=handle_pose_qc,
                inputs=[kf_gallery, kf_prompt],
                outputs=[kf_run_status["status_window"]]
            )

            test_vid_btn.click(
                cb_save_project,
                inputs=[current_file_path, preview, settings_json],
                outputs=[]
            ).then(
                _eh_run_and_curate_video,
                inputs=[preview, selected_node, vid_generate_count, vid_seed_input, current_file_path, selected_node],
                outputs=[
                    test_vid_player,
                    vid_run_status["status_window"],
                    vid_gallery_dd,
                    vid_player,
                    generation_result_buffer
                ]
            ).then(
                _eh_conditional_video_refresh,
                inputs=[preview, loaded_node_id, generation_result_buffer],
                outputs=[preview, vid_gallery_dd, vid_player],
                show_progress="hidden"
            )

            # Image Actions
            # kf_gallery.change(_eh_set_selected_image, inputs=[preview, selected_node, kf_gallery], outputs=[preview, kf_gallery_selection, main_preview_image])
            # kf_gallery.change(_eh_set_selected_image, inputs=[preview, loaded_node_id, kf_gallery], outputs=[preview, kf_gallery_selection, main_preview_image])
            # kf_gallery.change(_eh_set_selected_image, inputs=[preview, loaded_node_id, loaded_project_name, kf_gallery], outputs=[preview, kf_gallery_selection, main_preview_image])
            # kf_gallery.change(_eh_set_selected_image, inputs=[preview, loaded_node_id, loaded_project_name, kf_gallery], outputs=[preview, kf_gallery_selection, main_preview_image, kf_exec_info], show_progress="hidden")
            kf_gallery.change(_eh_set_selected_image, inputs=[preview, loaded_node_id, loaded_project_name, kf_gallery], outputs=[preview, kf_gallery_selection, main_preview_image, kf_exec_info], show_progress="hidden")
            kf_upload_btn.upload(_eh_upload_image, inputs=[preview, loaded_node_id, loaded_project_name, kf_upload_btn], outputs=[preview, kf_gallery])
            # kf_delete_img_btn.click(_eh_delete_image, inputs=[preview, selected_node, kf_gallery_selection], outputs=[preview, kf_gallery, kf_gallery_selection, main_preview_image], show_progress="hidden")
            kf_delete_img_btn.click(_eh_delete_image, inputs=[preview, loaded_node_id, loaded_project_name, kf_gallery_selection], outputs=[preview, kf_gallery, kf_gallery_selection, main_preview_image], show_progress="hidden")
            kf_del_others_btn.click(_eh_delete_all_but_this_image, inputs=[preview, loaded_node_id, loaded_project_name, kf_gallery_selection], outputs=[preview, kf_gallery, kf_gallery_selection])
            

            kf_prev_img_btn.click(_eh_prev_kf_image, inputs=[preview, selected_node, kf_gallery], outputs=[kf_gallery, main_preview_image]).then(
                _eh_set_selected_image, inputs=[preview, loaded_node_id, loaded_project_name, kf_gallery], outputs=[preview, kf_gallery_selection, main_preview_image, kf_exec_info], show_progress="hidden"
            )
            kf_next_img_btn.click(_eh_next_kf_image, inputs=[preview, selected_node, kf_gallery], outputs=[kf_gallery, main_preview_image]).then(
                _eh_set_selected_image, inputs=[preview, loaded_node_id, loaded_project_name, kf_gallery], outputs=[preview, kf_gallery_selection, main_preview_image, kf_exec_info], show_progress="hidden"
            )
            kf_exec_load_btn.click(_eh_load_execution_info_kf, inputs=[kf_gallery], outputs=[kf_exec_info])


            # vid_gallery_dd.change(_eh_set_selected_video, inputs=[preview, selected_node, vid_gallery_dd], outputs=[preview, vid_player], scroll_to_output=False, show_progress=False)
            # vid_gallery_dd.change(_eh_set_selected_video, inputs=[preview, loaded_node_id, loaded_project_name, vid_gallery_dd], outputs=[preview, vid_player], scroll_to_output=False, show_progress=False)
            vid_gallery_dd.select(_eh_set_selected_video, inputs=[preview, loaded_node_id, loaded_project_name, vid_gallery_dd], outputs=[preview, vid_player, vid_exec_info], scroll_to_output=False, show_progress=False)

            # vid_delete_btn.click(_eh_delete_video, inputs=[preview, selected_node, vid_gallery_dd], outputs=[preview, vid_gallery_dd, vid_player])
            vid_delete_btn.click(_eh_delete_video, inputs=[preview, loaded_node_id, loaded_project_name, vid_gallery_dd], outputs=[preview, vid_gallery_dd, vid_player])
            # vid_del_others_btn.click(_eh_delete_all_but_this_video, inputs=[preview, selected_node, vid_gallery_dd], outputs=[preview, vid_gallery_dd, vid_player])
            vid_del_others_btn.click(_eh_delete_all_but_this_video, inputs=[preview, loaded_node_id, loaded_project_name, vid_gallery_dd], outputs=[preview, vid_gallery_dd, vid_player])

            vid_prev_clip_btn.click(_eh_prev_vid_clip, inputs=[preview, selected_node, vid_gallery_dd], outputs=[vid_gallery_dd, vid_player], scroll_to_output=False, show_progress=False).then(
                _eh_set_selected_video, inputs=[preview, loaded_node_id, loaded_project_name, vid_gallery_dd], outputs=[preview, vid_player, vid_exec_info], show_progress="hidden"
            )
            vid_next_clip_btn.click(_eh_next_vid_clip, inputs=[preview, selected_node, vid_gallery_dd], outputs=[vid_gallery_dd, vid_player], scroll_to_output=False, show_progress=False).then(
                _eh_set_selected_video, inputs=[preview, loaded_node_id, loaded_project_name, vid_gallery_dd], outputs=[preview, vid_player, vid_exec_info], show_progress="hidden"
            ) 
            vid_exec_load_btn.click(_eh_load_execution_info_vid, inputs=[vid_gallery_dd], outputs=[vid_exec_info])

            # Purge Wiring
            seq_kf_purge["seq_kf_purge_btn"].click(lambda: gr.update(visible=True), outputs=[seq_kf_purge["seq_kf_confirm_group"]])
            seq_kf_purge["seq_kf_no"].click(lambda: gr.update(visible=False), outputs=[seq_kf_purge["seq_kf_confirm_group"]])


            seq_kf_purge["seq_kf_yes"].click(purge_sequence_keyframes, inputs=[preview, selected_node], outputs=[preview, seq_run_status["status_window"]]).then(lambda: (gr.update(visible=False), gr.update(open=False)), outputs=[seq_kf_purge["seq_kf_confirm_group"], seq_kf_purge["seq_kf_acc"]])

            seq_ib_purge["seq_ib_purge_btn"].click(lambda: gr.update(visible=True), outputs=[seq_ib_purge["seq_ib_confirm_group"]])
            seq_ib_purge["seq_ib_no"].click(lambda: gr.update(visible=False), outputs=[seq_ib_purge["seq_ib_confirm_group"]])
            seq_ib_purge["seq_ib_yes"].click(purge_sequence_inbetweens, inputs=[preview, selected_node], outputs=[preview, seq_run_status["status_window"]]).then(lambda: (gr.update(visible=False), gr.update(open=False)), outputs=[seq_ib_purge["seq_ib_confirm_group"], seq_ib_purge["seq_ib_acc"]])
            seq_kf_batch_btn.click(handle_sequence_image_batch, inputs=[preview, selected_node, seq_kf_inputs["seq_kf_iter"], seq_kf_inputs["seq_kf_cap"], seq_kf_inputs["seq_kf_sync"]], outputs=[seq_run_status["status_window"]])
            seq_ib_batch_btn.click(handle_sequence_video_batch, inputs=[preview, selected_node, seq_ib_inputs["seq_ib_iter"], seq_ib_inputs["seq_ib_cap"], seq_ib_inputs["seq_ib_sync"]], outputs=[seq_run_status["status_window"]])
            seq_pose_batch_btn.click(
                lambda fp, pj, nid: handle_pose_batch(fp, pj, "sequence", nid),
                inputs=[current_file_path, preview, selected_node],
                outputs=[seq_run_status["status_window"]]
            )
            seq_qc_batch_btn.click(
                lambda fp, pj, nid: handle_qc_batch(fp, pj, "sequence", nid),
                inputs=[current_file_path, preview, selected_node],
                outputs=[seq_run_status["status_window"]]
            )

            # Sequence upscale
            seq_enhance["run_btn"].click(
                cb_save_project,
                inputs=[current_file_path, preview, settings_json],
                outputs=[]
            ).then(
                handle_upscale_batch,
                inputs=[
                    current_file_path, 
                    preview, 
                    seq_enhance["chk_upscale"], 
                    seq_enhance["chk_interp"],
                    selected_node
                ],
                outputs=[seq_run_status["status_window"]]
            )
            seq_enhance["cancel_btn"].click(
                cancel_upscale_batch,
                inputs=[preview],
                outputs=[seq_run_status["status_window"]]
            )


            seq_cascade_btn.click(
                cb_save_project, inputs=[current_file_path, preview, settings_json], outputs=[]
            ).then(
                handle_cascade_batch,
                inputs=[current_file_path, preview, gr.State("sequence"), selected_node, seq_cascade_kf_iter, seq_cascade_vid_iter],
                outputs=[seq_run_status["status_window"]]
            )

            # Sequence Export
            seq_export_mgr["audio_upload"].upload(save_uploaded_audio, inputs=[seq_export_mgr["audio_upload"], preview], outputs=[seq_export_mgr["audio_dd"], seq_export_mgr["audio_upload"]])
            seq_export_mgr["audio_dd"].focus(refresh_audio_list_ui, inputs=[preview], outputs=[seq_export_mgr["audio_dd"]])
            seq_export_mgr["history_dd"].focus(list_existing_exports, inputs=[preview], outputs=[seq_export_mgr["history_dd"]])
            seq_export_mgr["history_dd"].change(lambda p: gr.update(value=p, visible=True), inputs=[seq_export_mgr["history_dd"]], outputs=[seq_export_mgr["download"]])

            seq_export_mgr["export_btn"].click(
                handle_sequence_export_task,
                inputs=[
                    current_file_path,
                    preview,
                    selected_node,
                    seq_export_mgr["format"],
                    seq_export_mgr["resize"],
                    seq_export_mgr["fps"],
                    seq_export_mgr["source_layer"],
                    seq_export_mgr["audio_dd"],
                    seq_export_mgr["animatic"],
                ],
                outputs=[
                    seq_export_mgr["log"],
                    seq_export_mgr["download"],
                    seq_export_mgr["history_dd"],
                ],
            )


            def _refresh_local_status(p, n):
                # Helper to find the sequence ID even if a Keyframe is selected
                if not p or not n: return "No context."
                pj = p if isinstance(p, dict) else {}
                seqs = pj.get("sequences", {})
                
                # Check if n is a sequence
                sid = n if n in seqs else None
                # If not, check if n is a child of a sequence
                if not sid:
                    for s_key, s_val in seqs.items():
                        if n in s_val.get("keyframes", {}) or n in s_val.get("videos", {}):
                            sid = s_key
                            break
                
                if sid: return read_sequence_status_files(pj, sid)
                return "Could not determine Sequence ID for status."

            for stat_ui in [seq_run_status, kf_run_status, vid_run_status]:
                stat_ui["refresh_btn"].click(
                    fn=_refresh_local_status,
                    inputs=[preview, selected_node],
                    outputs=[stat_ui["status_window"]]
                )

            # Cancel (Sequence scope)
            seq_cancel_kf_btn.click(lambda p, n: cancel_batch_script(p, "images", "sequence", n), inputs=[preview, selected_node], outputs=[seq_run_status["status_window"]])
            seq_cancel_ib_btn.click(lambda p, n: cancel_batch_script(p, "videos", "sequence", n), inputs=[preview, selected_node], outputs=[seq_run_status["status_window"]])
            seq_cascade_cancel_btn.click(
                fn=lambda p, n: cancel_cascade_batch(p, scope="sequence", seq_id=n),
                inputs=[preview, selected_node],
                outputs=[seq_run_status["status_window"]]
            )

            # Param Loading
            kf_load_outputs = [
                preview,
                kf_cn_pose_enable, kf_cn_pose_strength, kf_cn_pose_start, kf_cn_pose_end,
                kf_cn_shape_enable, kf_cn_shape_strength, kf_cn_shape_start, kf_cn_shape_end,
                kf_cn_outline_enable, kf_cn_outline_strength, kf_cn_outline_start, kf_cn_outline_end,
                kf_char_left, kf_char_right,
                kf_prompt, kf_neg_left, kf_neg_right, kf_neg_heal,
                kf_pose, kf_pose_preview, kf_cn_pose_animal, 
                kf_cn_pose_thumb, kf_cn_shape_thumb, kf_cn_outline_thumb,
                kf_pose_gallery,
                kf_run_status["status_window"]
            ]
            kf_load_params_btn.click(_eh_load_kf_params, inputs=[preview, selected_node, kf_gallery], outputs=kf_load_outputs, show_progress="hidden")
            kf_copy_pose_prompt_btn.click(
                _eh_copy_pose_prompt,
                inputs=[kf_pose],
                outputs=[kf_batch_accordion, kf_run_status["status_window"]]
            )

            vid_load_outputs = [preview, vid_length, vid_prompt, vid_neg, clear_vid_len_btn]
            vid_load_params_btn.click(_eh_load_vid_params, inputs=[preview, selected_node, vid_gallery_dd], outputs=vid_load_outputs, show_progress="hidden")
            

            # Global Vertical Navigation (Skip Up/Down)
            # skip_up_btn.click(lambda p, n: _eh_navigate_vertical(p, n, -1), inputs=[preview, selected_node], outputs=[node_selector, selected_node], show_progress="hidden", queue=False)
            # skip_down_btn.click(lambda p, n: _eh_navigate_vertical(p, n, 1), inputs=[preview, selected_node], outputs=[node_selector, selected_node], show_progress="hidden", queue=False)
            skip_up_btn.click(lambda p, n: _eh_navigate_vertical(p, n, -1), inputs=[preview, selected_node], outputs=[node_selector, selected_node], show_progress="hidden", queue=False).then(
                fn=_eh_node_selected,
                inputs=[preview, node_selector, gr.State(value=None)],
                outputs=node_selector_outputs,
                show_progress="hidden", queue=False
            )
            skip_down_btn.click(lambda p, n: _eh_navigate_vertical(p, n, 1), inputs=[preview, selected_node], outputs=[node_selector, selected_node], show_progress="hidden", queue=False).then(
                fn=_eh_node_selected,
                inputs=[preview, node_selector, gr.State(value=None)],
                outputs=node_selector_outputs,
                show_progress="hidden", queue=False
            )

    return kf_workflow_json, kf_pose, vid_lora, node_selector, node_selector_outputs, seq_lora, kf_pose_gallery, kf_lora, proj_len