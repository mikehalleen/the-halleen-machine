# scripts/run_video.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ComfyUI i2v video generator (half-res → 2x upscale) + FCPXML export

Refactored for V2 Data Model (Dictionary-based Sequences/Keyframes/Videos + Explicit Order).
"""

import argparse, json, os, re, time, uuid, random, requests, sys, subprocess
from datetime import datetime
from pathlib import Path
from fractions import Fraction
# from lora_registry import LORA_REGISTRY
import csv
from pathlib import Path

# --- CONSTANTS & REGEX ---
_LORA_RE = re.compile(r"__lora:([^:>]+):(.+?)__")
_TEMPLATE_RE = re.compile(r'\[([a-zA-Z0-9_.]+)\]')
_WC_RE = re.compile(r"\{([^{}]+)\}")

DEFAULT_VIDEO_TEMPLATE = """
[sequence.action_prompt]
[video.inbetween_prompt]
[sequence.setting_asset]
[sequence.setting_prompt]
[sequence.style_asset]
[sequence.style_prompt]
[project.style_prompt]
"""

PRIMER_STEPS = 2
EXPRESS_STEPS = 6
FULL_STEPS = 12
DEFAULT_UPSCALE = False
# DEFAULT_SLOMOFIX = True
DROP_JOIN_FRAME = True
# DEFAULT_SLOMOFIX_CFG = 2.5

TRIM_SE_EACH_SIDE = 1
TRIM_O_ONE_SIDE   = 1
GENEROUS_ASSET_FRAMES = 2000
GENEROUS_ASSET_SIXTEENTHS = 2000

# --- HELPERS ---





def load_lora_registry():
    """Load LoRA pairs from CSV file"""
    csv_path = Path(__file__).parent / "lora_pairs.csv"
    if not csv_path.exists():
        return []
    
    registry = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            high = row.get("high", "").strip()
            low = row.get("low", "").strip()
            if high:
                registry.append({"high": high, "low": low or None})
    return registry

LORA_REGISTRY = load_lora_registry()


def is_ltx2_workflow(wf_path):
    """Detect LTX-2 workflow based on filename"""
    filename = os.path.basename(wf_path).lower()
    return "ltx" in filename

def update_ltx2_dims(workflow, width, height):
    """Update LTX-2 WIDTH/HEIGHT nodes"""
    for _, node in find_nodes_by_title(workflow, "WIDTH"):
        set_if_exists(node, "value", int(width))
    for _, node in find_nodes_by_title(workflow, "HEIGHT"):
        set_if_exists(node, "value", int(height))

def update_ltx2_fps(workflow, fps):
    """Update LTX-2 FPS node"""
    for _, node in find_nodes_by_title(workflow, "FPS"):
        set_if_exists(node, "value", float(fps))

def update_ltx2_frames(workflow, frames):
    """Update LTX-2 frame count"""
    for _, node in find_nodes_by_title(workflow, "LENGTH (frames)"):
        set_if_exists(node, "value", int(frames))

def update_ltx2_images(workflow, start_path, end_path):
    """Update LTX-2 first/last frame images"""
    if start_path:
        for _, node in find_nodes_by_title(workflow, "FIRST FRAME"):
            set_if_exists(node, "image", start_path)  # Use full path
    if end_path:
        for _, node in find_nodes_by_title(workflow, "LAST FRAME"):
            set_if_exists(node, "image", end_path)  # Use full path
















def calculate_lane_sum(text_sources: list):
    total = 0.0
    for text in text_sources:
        if not text: continue
        matches = _LORA_RE.findall(text)
        for _, str_val in matches:
            try: total += float(str_val)
            except: pass
    return total

def _split_comma(text):
    if not text: return []
    seen, out = set(), []
    for part in (p.strip() for p in str(text).split(',')):
        if part and part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return out

def merge_negatives(*parts):
    tokens = []
    for p in parts:
        tokens.extend(_split_comma(p))
    return ", ".join(tokens)

def _is_pid_running(pid: int) -> bool:
    if not pid or pid < 0: return False
    try: os.kill(pid, 0)
    except OSError: return False
    else: return True

def _write_status(status_path, pid: int, status: str, current_task: str = None, sub_task: str = None, error: str = None, progress_percent: float = None, completed_count: int = None, total_count: int = None):
    try:
        status_data = {
            "pid": pid, "status": status, "current_task": current_task, "sub_task": sub_task, "error": error,
            "progress_percent": f"{progress_percent:.1f}" if progress_percent is not None else None,
            "batch_completed_count": completed_count, "batch_total_count": total_count, "last_update": datetime.now().isoformat()
        }
        temp_path = str(status_path) + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f: json.dump(status_data, f, indent=2)
        os.replace(temp_path, status_path)
    except Exception as e: print(f"[WARN] Failed to write status file '{status_path}': {e}")

def iter_video_entries(seq: dict):
    # V2 check
    if "video_order" in seq and "videos" in seq:
        videos = seq["videos"]
        order = seq["video_order"]
        items = []
        for idx, vid_id in enumerate(order):
            if vid_id in videos:
                items.append((idx, vid_id, videos[vid_id]))
        return items

    # V1 Fallback
    vids = (seq or {}).get("i2v_videos", {}) or {}
    items = []
    for k, v in vids.items():
        m = re.match(r"^vid(\d+)", k)
        if not m: continue
        idx = int(m.group(1))
        items.append((idx, k, v))
    items.sort(key=lambda x: x[0])
    return items

def inject_film_vfi_upscaler(graph: dict, multiplier: int = 4):
    try:
        cv_nid, cv_node = first_node_by_title(graph, "Create Video")
        if not cv_node: return
        images_input = cv_node.get("inputs", {}).get("images")
        if not isinstance(images_input, list) or len(images_input) < 1: return
        
        va_decode_nid, va_decode_out_idx = images_input[0], images_input[1] if len(images_input) > 1 else 0

        film_loader_nid = new_node_id(graph)
        graph[film_loader_nid] = {"inputs": {"model_name": "film_net_fp32.pt"}, "class_type": "FILMModelLoader", "_meta": {"title": "Injected_FILM_Loader"}}

        film_vfi_nid = new_node_id(graph)
        graph[film_vfi_nid] = {"inputs": {"clear_cache_after_n_frames": 10, "multiplier": multiplier, "frames": [va_decode_nid, va_decode_out_idx], "ckpt_name": [film_loader_nid, 0]}, "class_type": "FILM VFI", "_meta": {"title": "Injected_FILM_VFI"}}
        
        fps_input = cv_node.get("inputs", {}).get("fps")
        if fps_input:
            fps_math_nid = new_node_id(graph)
            graph[fps_math_nid] = {"inputs": {"a": fps_input, "b": multiplier, "operation": "multiply"}, "class_type": "easy mathInt", "_meta": {"title": "Injected_FPS_Multiplier"}}
            cv_node["inputs"]["fps"] = [fps_math_nid, 0]

        cv_node["inputs"]["images"] = [film_vfi_nid, 0]
        print(f"[INJECT] Injected 'FILM VFI' (x{multiplier}) upscaler.")
    except Exception as e: print(f"[WARN] Failed to inject FILM VFI: {e}")

def inject_quarter_size_upscaler(graph: dict):
    try:
        cv_nid, cv_node = first_node_by_title(graph, "Create Video")
        if not cv_node: return
        images_input = cv_node.get("inputs", {}).get("images")
        if not isinstance(images_input, list) or len(images_input) < 1: return
        
        va_decode_nid, va_decode_out_idx = images_input[0], images_input[1] if len(images_input) > 1 else 0

        set_width_nid = new_node_id(graph)
        graph[set_width_nid] = {"inputs": {"value": 1280}, "class_type": "INTConstant", "_meta": {"title": "set_width"}}
        set_height_nid = new_node_id(graph)
        graph[set_height_nid] = {"inputs": {"value": 720}, "class_type": "INTConstant", "_meta": {"title": "set_height"}}

        upscale_nid = new_node_id(graph)
        graph[upscale_nid] = {"inputs": {"upscale_method": "lanczos", "width": [set_width_nid, 0], "height": [set_height_nid, 0], "crop": "disabled", "image": [va_decode_nid, va_decode_out_idx]}, "class_type": "ImageScale", "_meta": {"title": "Upscale to Full"}}

        cv_node["inputs"]["images"] = [upscale_nid, 0]
    except Exception as e: print(f"[WARN] Failed to inject upscaler: {e}")


def inject_frame_save_node(graph: dict, filename_prefix: str):
    try:
        cv_nid, cv_node = first_node_by_title(graph, "Create Video")
        if not cv_node: return
        images_input = cv_node.get("inputs", {}).get("images")
        if not isinstance(images_input, list) or len(images_input) < 1: return
        
        save_image_nid = new_node_id(graph)
        graph[save_image_nid] = {"inputs": {"filename_prefix": filename_prefix, "images": images_input}, "class_type": "SaveImage", "_meta": {"title": "Injected_Frame_Saver"}}
        ensure_dir(os.path.dirname(filename_prefix))
    except Exception as e: print(f"[WARN] Failed to inject frame save: {e}")


def stitch_frames_to_lossless(frames_dir: str, output_path: str, fps: float = 16.0) -> bool:
    """Stitch PNG frames to lossless FFV1 video."""
    import subprocess
    frames_dir = Path(frames_dir)
    if not frames_dir.exists():
        print(f"[WARN] Frames dir not found: {frames_dir}")
        return False
    
    pngs = sorted(frames_dir.glob("*.png"))
    if not pngs:
        print(f"[WARN] No PNGs in {frames_dir}")
        return False
    
    # Detect frame pattern - handle ComfyUI naming like frame_00001_.png
    first = pngs[0].name
    import re
    # Try ComfyUI pattern first: prefix_00001_.png
    m = re.match(r'^(.+_)(\d+)(_\.png)$', first)
    if not m:
        # Fallback: prefix00001.png (no trailing underscore)
        m = re.match(r'^(.*)(\d+)(\.png)$', first)
    if not m:
        print(f"[WARN] Can't parse frame pattern from {first}")
        return False
    
    prefix, num, suffix = m.groups()
    num_digits = len(num)
    pattern = f"{prefix}%0{num_digits}d{suffix}"
    input_pattern = str(frames_dir / pattern)
    
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", input_pattern,
        "-c:v", "ffv1",
        "-level", "3",
        "-pix_fmt", "yuv444p",
        output_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[LOSSLESS] Created {output_path}")
            return True
        else:
            print(f"[ERR] ffmpeg failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"[ERR] ffmpeg exception: {e}")
        return False


def cleanup_temp_frames(frames_dir: str):
    """Remove temp frames directory."""
    import shutil
    try:
        if os.path.isdir(frames_dir):
            shutil.rmtree(frames_dir)
            print(f"[CLEANUP] Removed {frames_dir}")
    except Exception as e:
        print(f"[WARN] Failed to cleanup {frames_dir}: {e}")


def jload(p):
    with open(p, "r", encoding="utf-8") as f: return json.load(f)

def ensure_dir(path): Path(path).mkdir(parents=True, exist_ok=True)

def list_images(folder):
    if not os.path.isdir(folder): return []
    exts = (".png",".jpg",".jpeg",".webp")
    return sorted([str(Path(folder, f)) for f in os.listdir(folder) if f.lower().endswith(exts)])

def list_videos(folder):
    if not os.path.isdir(folder): return []
    exts = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
    return sorted([str(Path(folder, f)) for f in os.listdir(folder) if f.lower().endswith(exts)])

def get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur: return default
        cur = cur[k]
    return cur

def set_if_exists(node, input_key, value):
    if isinstance(node, dict) and "inputs" in node: node["inputs"][input_key] = value

def find_nodes_by_title(workflow, title):
    return [(nid, node) for nid, node in workflow.items()
            if isinstance(node, dict) and node.get("_meta", {}).get("title") == title]

def find_nodes_by_title_ci(workflow, title_lower):
    out = []
    for nid, node in workflow.items():
        if not isinstance(node, dict): continue
        t = (node.get("_meta", {}) or {}).get("title", "")
        if isinstance(t, str) and t.lower() == title_lower: out.append((nid, node))
    return out

def find_nodes_by_class(workflow, class_type):
    return [(nid, node) for nid, node in workflow.items()
            if isinstance(node, dict) and node.get("class_type") == class_type]

def first_node_by_title(workflow, title):
    xs = find_nodes_by_title(workflow, title)
    return xs[0] if xs else (None, None)

def new_node_id(graph):
    numeric = [int(k) for k in graph.keys() if isinstance(k, str) and k.isdigit()]
    return str(max(numeric) + 1) if numeric else str(int(time.time() * 1000) % 2_000_000_000)

def post_prompt(api_base, graph):
    r = requests.post(api_base.rstrip("/") + "/prompt", json={"prompt": graph, "client_id": str(uuid.uuid4())}, timeout=60)
    r.raise_for_status()
    return r.json().get("prompt_id")

def wait_history_done(api_base, prompt_id, timeout_s=3600, poll_s=1.0):
    url = api_base.rstrip("/") + f"/history/{prompt_id}"
    t0 = time.time()
    while True:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            if prompt_id in r.json(): return True
        if time.time() - t0 > timeout_s: return False
        time.sleep(poll_s)

def style_line(project):
    v = project.get("style_prompt", "")
    return " ".join(v) if isinstance(v, list) else str(v).strip()

def expand_inline_wildcards(text, iter_index=0):
    if not text: return ""
    def repl(m):
        opts = [p.strip() for p in m.group(1).split("|")]
        return random.choice(opts) if opts else ""
    return _WC_RE.sub(repl, text)

def compose_video_prompt(project_data, sequence_data, video_data, iter_index):
    template = get(project_data, "inbetween_generation", "prompt_template") or DEFAULT_VIDEO_TEMPLATE
    def resolve_placeholder(match):
        key_path = match.group(1)
        parts = key_path.split('.')
        if len(parts) != 2: return f"[INVALID_KEY: {key_path}]"
        source_name, key = parts[0], parts[1]
        source_data = {}
        if source_name == 'project': source_data = project_data
        elif source_name == 'sequence': source_data = sequence_data
        elif source_name == 'video': source_data = video_data
        else: return f"[UNKNOWN_SOURCE: {source_name}]"
        value = source_data.get(key, "")
        return expand_inline_wildcards(str(value), iter_index)
    prompt = _TEMPLATE_RE.sub(resolve_placeholder, template)
    return '\n'.join(line for line in prompt.splitlines() if line.strip()).strip()

def connect_output_to_input(graph, src_nid, dst_nid, dst_input_name, out_index=0):
    dst = graph.get(dst_nid, {})
    if "inputs" not in dst: dst["inputs"] = {}; graph[dst_nid] = dst
    dst["inputs"][dst_input_name] = [str(src_nid), out_index]

# def ensure_two_loaders(graph):
    # loaders = [(nid, n) for nid, n in graph.items() if n.get("class_type") == "LoadImage"]
def ensure_two_loaders(graph):
    loaders = [(nid, n) for nid, n in graph.items() if isinstance(n, dict) and n.get("class_type") == "LoadImage"]
    ids = [nid for nid,_ in loaders]
    while len(ids) < 2:
        nid = new_node_id(graph)
        graph[nid] = {"class_type": "LoadImage", "inputs": {"image": ""}, "_meta": {"title": f"AutoLoader{len(ids)+1}"}}
        ids.append(nid)
    return ids[:2]

def set_loader_path(node, path_str):
    for k in ("image","image_path","file","filename"): set_if_exists(node, k, path_str)
    set_if_exists(node, "_cache_buster", time.time())

def disconnect_wan_images(graph):
    wan_nid, wan = first_node_by_title(graph, "WanFirstLastFrameToVideo")
    if wan and "inputs" in wan:
        for k in ("start_image","end_image"):
            if k in wan["inputs"]: del wan["inputs"][k]

def ensure_scale_node(graph, title, w, h):
    nodes = find_nodes_by_title(graph, title)
    if nodes: nid, node = nodes[0]
    else:
        nid = new_node_id(graph)
        node = {"class_type": "ImageScale", "inputs": {}, "_meta": {"title": title}}
        graph[nid] = node
    set_if_exists(node, "width", int(w)); set_if_exists(node, "height", int(h))
    return nid

def wire_half_to_wan(graph, clip_type, l1, l2, half_w, half_h):
    disconnect_wan_images(graph)
    wan_nid, _ = first_node_by_title(graph, "WanFirstLastFrameToVideo")
    if not wan_nid: raise RuntimeError("WanFirstLastFrameToVideo node not found.")
    start_scale_nid = ensure_scale_node(graph, "StartImage", half_w, half_h)
    end_scale_nid   = ensure_scale_node(graph, "EndImage", half_w, half_h)
    if clip_type in ("SE","SO"):
        connect_output_to_input(graph, l1, start_scale_nid, "image", out_index=0)
        connect_output_to_input(graph, start_scale_nid, wan_nid, "start_image", out_index=0)
    if clip_type in ("SE","OE"):
        connect_output_to_input(graph, l2, end_scale_nid, "image", out_index=0)
        connect_output_to_input(graph, end_scale_nid, wan_nid, "end_image", out_index=0)

# def enforce_project_dimensions_nodes(workflow, full_w, full_h):
#     for t in ("set_width", "Set Width", "SetWidth"):
#         for _, node in find_nodes_by_title(workflow, t):
#             for k in ("value","val","number","int","width","W"): set_if_exists(node, k, int(full_w))
#     for t in ("set_height", "Set Height", "SetHeight"):
#         for _, node in find_nodes_by_title(workflow, t):
#             for k in ("value","val","number","int","height","H"): set_if_exists(node, k, int(full_h))


def enforce_project_dimensions_nodes(workflow, full_w, full_h):
    is_ltx2 = workflow.get('_is_ltx2', False)
    
    if is_ltx2:
        update_ltx2_dims(workflow, full_w, full_h)
    else:
        for t in ["Project Width", "Project Height"]:
            for _, node in find_nodes_by_title(workflow, t):
                if "inputs" not in node: node["inputs"] = {}
                if t == "Project Width": node["inputs"]["value"] = int(full_w)
                else: node["inputs"]["value"] = int(full_h)

def get_fps_from_create_video(node):
    if not isinstance(node, dict) or "inputs" not in node: return None
    for k in ("fps","frame_rate","framerate"):
        v = node["inputs"].get(k)
        if isinstance(v, (int,float)) and v > 0: return float(v)
    return None

# def set_wan_frames(workflow, frames):
#     nid, wan = first_node_by_title(workflow, "WanFirstLastFrameToVideo")
#     if not wan: return
#     for k in ("frames","length","num_frames","frame_count"):
#         if "inputs" in wan and k in wan["inputs"]: wan["inputs"][k] = int(frames); return
#     set_if_exists(wan, "frames", int(frames))


def set_video_frames(workflow, frames):
    """Set video frame count for any workflow type"""
    is_ltx2 = workflow.get('_is_ltx2', False)
    
    if is_ltx2:
        update_ltx2_frames(workflow, frames)
    else:
        nid, wan = first_node_by_title(workflow, "WanFirstLastFrameToVideo")
        if wan and "inputs" in wan and "length" in wan["inputs"]:
            wan["inputs"]["length"] = int(frames)


def set_wan_size(workflow, w, h):
    nid, wan = first_node_by_title(workflow, "WanFirstLastFrameToVideo")
    if not wan: return
    for k in ("width","W","out_width","video_width"): set_if_exists(wan, k, int(w))
    for k in ("height","H","out_height","video_height"): set_if_exists(wan, k, int(h))

# def update_video_seeds(workflow, seed):
#     for _, node in find_nodes_by_class(workflow, "KSamplerAdvanced"): set_if_exists(node, "noise_seed", int(seed))
#     for _, node in find_nodes_by_class(workflow, "KSampler"): set_if_exists(node, "seed", int(seed))

def update_video_seeds(workflow, seed):
    is_ltx2 = workflow.get('_is_ltx2', False)
    
    if is_ltx2:
        # LTX-2: Update MainSeed, leave FixedSeed alone
        for _, node in find_nodes_by_title(workflow, "MainSeed"):
            set_if_exists(node, "noise_seed", int(seed))
    else:
        # WAN: Update KSamplerAdvanced and KSampler
        for _, node in find_nodes_by_class(workflow, "KSamplerAdvanced"): 
            set_if_exists(node, "noise_seed", int(seed))
        for _, node in find_nodes_by_class(workflow, "KSampler"): 
            set_if_exists(node, "seed", int(seed))


# def update_video_saver(workflow, out_folder, base_name):
#     ensure_dir(out_folder)
#     for _, node in find_nodes_by_class(workflow, "SaveVideo"):
#         set_if_exists(node, "filename_prefix", os.path.join(out_folder, base_name))


def update_video_saver(workflow, out_folder, base_name):
    """Update SaveVideo/VHS_VideoCombine nodes with output path"""
    path = os.path.join(out_folder, base_name)
    is_ltx2 = workflow.get('_is_ltx2', False)
    
    if is_ltx2:
        # LTX-2 uses VHS_VideoCombine
        for _, node in find_nodes_by_class(workflow, "VHS_VideoCombine"):
            set_if_exists(node, "filename_prefix", path)
    else:
        # WAN uses SaveVideo
        for _, node in find_nodes_by_class(workflow, "SaveVideo"):
            if "inputs" in node and "filename_prefix" in node["inputs"]:
                node["inputs"]["filename_prefix"] = path
            
def list_videos_with_prefix(folder, base_prefix):
    files = list_videos(folder)
    return sorted([f for f in files if os.path.basename(f).lower().startswith(base_prefix.lower())])

def get_max_file_index(files: list) -> int:
    max_idx = 0
    for f in files:
        m = re.search(r'_(\d+)_?\.[^.]+$', os.path.basename(f))
        if m:
            try: max_idx = max(max_idx, int(m.group(1)))
            except: pass
    return max_idx

def to_file_url(path_str: str) -> str:
    try: return Path(path_str).as_uri()
    except: return "file://" + path_str.replace("\\", "/")

def sixteen_str(n: int) -> str: return f"{int(n)}/16s"

def write_fcpxml(project_name, project_width, project_height, sequences_clips, out_root, fps_for_format):
    def secs_to_grid(sec): return max(1, int(round(float(sec) * 16.0)))
    def frames_to_grid(fr): return int(round((float(fr) / float(fps_for_format)) * 16.0))
    def transition_allowed(prev_t, next_t): return (prev_t, next_t) in {("OE","SE"), ("SE","SE"), ("SE","SO")}
    DISSOLVE_LEN_GRID = 2
    ts = time.strftime("%Y%m%d_%H%M%S")
    proj_dir = os.path.join(out_root, project_name); ensure_dir(proj_dir)
    out_path = os.path.join(proj_dir, f"{project_name}_timeline_{ts}.xml")

    assets_map = {}
    for s in sequences_clips:
        for v in s["vids"]:
            for c in v["clips"]: assets_map[c["asset_ref"]] = (c["name"], to_file_url(c["path"]))
    
    assets_xml = "\n    ".join(
        f'<asset id="{aid}" name="{nm}" src="{src}" start="0s" duration="{sixteen_str(2000)}" hasVideo="1"/>'
        for aid, (nm, src) in assets_map.items()
    )
    dissolve_effect_xml = '<effect id="x_diss" name="Cross Dissolve" uid=".../transition/generic/Cross Dissolve"/>'

    def vid_visible_len_grid(v):
        for c in v.get("clips", []):
            vis = max(1, int(c["media_frames"]) - int(c["trim_start"]) - int(c["trim_end"]))
            return frames_to_grid(vis)
        return 0

    cumulative_grid = 0
    spine_items_xml = []

    for s in sequences_clips:
        vlist = s["vids"]
        if not vlist: continue
        seq_len_grid = sum(vid_visible_len_grid(v) for v in vlist)
        if seq_len_grid <= 0: continue
        lane_count = max((len(v.get("clips", [])) for v in vlist), default=0)
        if lane_count == 0: continue

        for lane_idx in range(lane_count):
            inner_offset = 0
            inner_xml_parts = []
            prev_type = None
            for vid_i, v in enumerate(vlist):
                clips = v.get("clips", [])
                next_type = vlist[vid_i + 1]["type"] if vid_i + 1 < len(vlist) else None
                if vid_i > 0 and transition_allowed(prev_type, v.get("type")):
                    trans_offset = max(0, inner_offset - (DISSOLVE_LEN_GRID // 2))
                    inner_xml_parts.append(f'<transition ref="x_diss" offset="{sixteen_str(trans_offset)}" duration="{sixteen_str(DISSOLVE_LEN_GRID)}"/>')

                if lane_idx < len(clips):
                    c = clips[lane_idx]
                    vis_frames = max(1, int(c["media_frames"]) - int(c["trim_start"]) - int(c["trim_end"]))
                    vis_grid = frames_to_grid(vis_frames)
                    left_h = 1 if (vid_i > 0 and transition_allowed(prev_type, v.get("type"))) else 0
                    right_h = 1 if (vid_i < len(vlist)-1 and transition_allowed(v.get("type"), next_type)) else 0
                    start_in = max(0, int(c["trim_start"]) - left_h)
                    used = min(int(c["media_frames"]) - start_in, vis_frames + left_h + right_h)
                    
                    inner_xml_parts.append(f'<clip name="{c["name"]}" offset="{sixteen_str(inner_offset)}" duration="{sixteen_str(vis_grid)}"><video ref="{c["asset_ref"]}" start="{sixteen_str(frames_to_grid(start_in))}" duration="{sixteen_str(frames_to_grid(used))}"/></clip>')
                    inner_offset += vis_grid
                else: inner_offset += vid_visible_len_grid(v)
                prev_type = v.get("type")

            spine_items_xml.append(f'<clip name="Lane {lane_idx+1}" lane="{lane_idx+1}" offset="{sixteen_str(cumulative_grid)}" duration="{sixteen_str(seq_len_grid)}" start="0s" format="fmt1"><spine>' + "".join(inner_xml_parts) + '</spine></clip>')
        cumulative_grid += seq_len_grid

    seq_dur = sixteen_str(max(1, cumulative_grid))
    xml = f'<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE fcpxml><fcpxml version="1.10"><resources><format id="fmt1" frameDuration="1/16s" width="{project_width}" height="{project_height}" colorSpace="1-1-1 (Rec. 709)"/>{dissolve_effect_xml}{assets_xml}</resources><library><event name="Generated"><project name="{project_name}"><sequence duration="{seq_dur}" format="fmt1"><spine><gap name="Primary" duration="{seq_dur}">{"".join(spine_items_xml)}</gap></spine></sequence></project></event></library></fcpxml>'
    with open(out_path, "w", encoding="utf-8") as f: f.write(xml)
    print(f"[EXPORT] FCPXML written -> {out_path}")


# def resolve_lora_pair(name: str):
#     """
#     Resolve a LoRA name to high/low file pair.
    
#     Priority:
#     1. Explicit registry entry
#     2. Auto-detect ai-toolkit _high_noise/_low_noise convention
#     3. Fallback: high-only
    
#     Returns: (high_file, low_file) - either can be None
#     """

#     name = name.strip()
#     if not name:
#         return None, None
#     stem = name.replace(".safetensors", "")
    
#     # 1. Registry takes priority (check both with and without extension)
#     for entry in LORA_REGISTRY:
#         if name in entry["triggers"] or stem in entry["triggers"]:
#             high = entry.get("high") or None
#             low = entry.get("low") or None
#             # Ensure .safetensors extension
#             if high and not high.endswith(".safetensors"):
#                 high = f"{high}.safetensors"
#             if low and not low.endswith(".safetensors"):
#                 low = f"{low}.safetensors"
#             print(f"[LORA] Registry match: {name} -> high:{high} low:{low}")
#             return high, low
    
#     # 2. Auto-detect ai-toolkit naming
    
#     if "_high_noise" in stem or "_low_noise" in stem:
#         base = stem.replace("_high_noise", "").replace("_low_noise", "")
#         high_file = f"{base}_high_noise.safetensors"
#         low_file = f"{base}_low_noise.safetensors"
#         print(f"[LORA] Auto-paired: {high_file} + {low_file}")
#         return high_file, low_file
    
#     # 3. No match - high-only
#     print(f"[LORA] No pair found, high-only: {name}")
#     return name, None

def resolve_lora_pair(name: str):
    name = name.strip()
    if not name:
        return None, None
    
    # 1. Check registry (match either high or low name)
    for entry in LORA_REGISTRY:
        if name == entry["high"] or name == entry["low"]:
            print(f"[LORA] Registry match: {name} -> high:{entry['high']} low:{entry['low']}")
            return entry["high"], entry["low"]
    
    # 2. Auto-detect _high_noise/_low_noise pattern
    stem = name.replace(".safetensors", "")
    if "_high_noise" in stem or "_low_noise" in stem:
        base = stem.replace("_high_noise", "").replace("_low_noise", "")
        high_file = f"{base}_high_noise.safetensors"
        low_file = f"{base}_low_noise.safetensors"
        print(f"[LORA] Auto-paired: {high_file} + {low_file}")
        return high_file, low_file
    
    # 3. Fallback - high only
    print(f"[LORA] No pair found, high-only: {name}")
    return name, None

def project_neg(cfg_project):
    neg = dict(cfg_project.get("negatives", {}) or {})
    if not neg.get("global"):
        legacy = cfg_project.get("negative_prompt", "")
        if legacy: neg["global"] = legacy
    for k in ("keyframes_all", "inbetween_all", "heal_all"): neg.setdefault(k, "")
    return neg



def inject_prompt_loras(graph: dict, lora_list):
    lora_list = list(lora_list)  # Convert iterator to list so we can iterate multiple times
    if not lora_list: return
    try:
        # Find UNet loaders by title
        high_unet_nid, _ = first_node_by_title(graph, "HighNoiseUnet")
        low_unet_nid, _ = first_node_by_title(graph, "LowNoiseUnet")
        
        if not high_unet_nid:
            print("[WARN] HighNoiseUnet not found, skipping LoRA injection")
            return
        
        # Find all nodes whose model input points to a given node
        # def find_downstream(source_nid):
        #     results = []
        #     for nid, node in graph.items():
        #         if not isinstance(node, dict): continue
        #         model_input = node.get("inputs", {}).get("model")
        #         if isinstance(model_input, list) and str(model_input[0]) == str(source_nid):
        #             results.append((nid, node))
        #     return results


        # Find all nodes whose model input points to a given node
        def find_downstream(source_nid):
            results = []
            print(f"[DEBUG] find_downstream looking for source_nid={source_nid} (type={type(source_nid).__name__})")
            for nid, node in graph.items():
                if not isinstance(node, dict): continue
                model_input = node.get("inputs", {}).get("model")
                if isinstance(model_input, list):
                    print(f"[DEBUG]   Node {nid} has model[0]={model_input[0]} (type={type(model_input[0]).__name__})")
                    if str(model_input[0]) == str(source_nid):
                        title = node.get("_meta", {}).get("title", "?")
                        print(f"[DEBUG]   ^^^ MATCH: {title}")
                        results.append((nid, node))
            print(f"[DEBUG] find_downstream found {len(results)} nodes")
            return results
        
        # Build a LoRA chain from source, return final reference
        def build_chain(source_ref, noise_type):
            curr = source_ref
            for (name, strength_str) in lora_list:
                try: s = float(strength_str)
                except: continue
                high_file, low_file = resolve_lora_pair(name)
                lora_file = high_file if noise_type == "high" else low_file
                if lora_file:
                    nid = new_node_id(graph)
                    graph[nid] = {
                        "inputs": {"lora_name": lora_file, "strength_model": s, "model": curr},
                        "class_type": "LoraLoaderModelOnly",
                        "_meta": {"title": f"Injected_{noise_type.title()}_{lora_file}"}
                    }
                    curr = [nid, 0]
            return curr
        
        # Inject into all high-noise paths
        for nid, node in find_downstream(high_unet_nid):
            chain_end = build_chain([high_unet_nid, 0], "high")
            node["inputs"]["model"] = chain_end
            target_name = node.get("_meta", {}).get("title", nid)
            print(f"[INJECT] High LoRA chain -> {target_name}")
        
        # Inject into all low-noise paths
        if low_unet_nid:
            for nid, node in find_downstream(low_unet_nid):
                chain_end = build_chain([low_unet_nid, 0], "low")
                node["inputs"]["model"] = chain_end
                target_name = node.get("_meta", {}).get("title", nid)
                print(f"[INJECT] Low LoRA chain -> {target_name}")
        
        # Log what was injected
        for (name, _) in lora_list:
            high_file, low_file = resolve_lora_pair(name)
            print(f"[INJECT] {name} -> High:{high_file or 'Skip'} Low:{low_file or 'Skip'}")
        
    except Exception as e: print(f"[WARN] Failed to inject LoRAs: {e}")



# def inject_prompt_loras(graph: dict, lora_list: list):
#     if not lora_list: return
#     try:
#         high_node, low_node = None, None
#         for nid, node in find_nodes_by_class(graph, "LoraLoaderModelOnly"):
#             if "high_noise.safetensors" in str(node.get("inputs", {}).get("lora_name", "")): high_node = node
#             if "low_noise.safetensors" in str(node.get("inputs", {}).get("lora_name", "")): low_node = node
#         if not high_node: return

#         curr_high = high_node["inputs"]["model"]
#         curr_low = low_node["inputs"]["model"] if low_node else None

#         for (name, strength_str) in lora_list:
#             try: s = float(strength_str)
#             except: continue
#             high_file, low_file = resolve_lora_pair(name)

#             if high_file:
#                 nid_h = new_node_id(graph)
#                 graph[nid_h] = {"inputs": {"lora_name": high_file, "strength_model": s, "model": curr_high}, "class_type": "LoraLoaderModelOnly", "_meta": {"title": f"Injected_High_{high_file}"}}
#                 curr_high = [nid_h, 0]

#             if low_file and curr_low:
#                 nid_l = new_node_id(graph)
#                 graph[nid_l] = {"inputs": {"lora_name": low_file, "strength_model": s, "model": curr_low}, "class_type": "LoraLoaderModelOnly", "_meta": {"title": f"Injected_Low_{low_file}"}}
#                 curr_low = [nid_l, 0]
#             print(f"[INJECT] {name} -> High:{high_file or 'Skip'} Low:{low_file or 'Skip'}")

#         high_node["inputs"]["model"] = curr_high
#         if low_node and curr_low: low_node["inputs"]["model"] = curr_low
#     except Exception as e: print(f"[WARN] Failed to inject LoRAs: {e}")

# def inject_slowmo_fix(graph: dict, target_steps: int, primer_steps: int, primer_cfg: float, main_cfg: float):
#     try:
#         iter_nid, iter_node = first_node_by_title(graph, "IterKSampler")
#         fixed_nid, fixed_node = first_node_by_title(graph, "WanFixedSeed")
#         steps_nid, _ = first_node_by_title(graph, "Steps (Final)")
#         if not (iter_node and fixed_node and steps_nid): return

#         orig_latent = iter_node["inputs"]["latent_image"]
#         lora_model_nid = iter_node["inputs"]["model"][0]
#         base_model_nid = graph[lora_model_nid]["inputs"]["model"][0]
#         shift_val = graph[lora_model_nid]["inputs"].get("shift", 5.0)

#         base_samp_nid = new_node_id(graph)
#         graph[base_samp_nid] = {"inputs": {"shift": shift_val, "model": graph[base_model_nid]["inputs"]["model"]}, "class_type": "ModelSamplingSD3", "_meta": {"title": "Injected_Base_Samp"}}

#         fix_nid = new_node_id(graph)
#         graph[fix_nid] = {
#             "inputs": {
#                 "add_noise": "enable", "noise_seed": iter_node["inputs"].get("noise_seed", 0),
#                 "steps": [steps_nid, 0], "cfg": primer_cfg, "sampler_name": iter_node["inputs"].get("sampler_name", "euler"),
#                 "scheduler": iter_node["inputs"].get("scheduler", "simple"), "start_at_step": 0, "end_at_step": primer_steps,
#                 "return_with_leftover_noise": "enable", "model": [base_samp_nid, 0],
#                 "positive": iter_node["inputs"]["positive"], "negative": iter_node["inputs"]["negative"], "latent_image": orig_latent
#             },
#             "class_type": "KSamplerAdvanced", "_meta": {"title": "Injected_SlowMo_Fix"}
#         }

#         mid = ((target_steps - primer_steps) // 2) + primer_steps
#         iter_node["inputs"]["latent_image"] = [fix_nid, 0]
#         iter_node["inputs"]["start_at_step"] = primer_steps
#         iter_node["inputs"]["end_at_step"] = mid
#         iter_node["inputs"]["cfg"] = main_cfg
#         iter_node["inputs"]["add_noise"] = "disable"
        
#         fixed_node["inputs"]["start_at_step"] = mid
#         fixed_node["inputs"]["end_at_step"] = target_steps
#         fixed_node["inputs"]["cfg"] = main_cfg
#         print(f"[INJECT] SlowMo Fix applied. Primer:{primer_steps} Mid:{mid} Total:{target_steps}")
#     except Exception as e: print(f"[WARN] SlowMo Fix failed: {e}")

def inject_metadata_mp4(video_path, snapshot):
    try:
        if not os.path.exists(video_path): return
        json_str = json.dumps(snapshot)
        temp_path = str(video_path) + ".temp.mp4"
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video_path), "-map", "0", "-c", "copy", "-metadata", f"comment={json_str}", temp_path]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        os.replace(temp_path, video_path)
        print(f"[META] Injected snapshot into {os.path.basename(video_path)}")
    except Exception as e:
        print(f"[WARN] Failed to inject metadata: {e}")
        if os.path.exists(temp_path): os.remove(temp_path)

def run(config_path, export_only=False, status_file_override=None):
    cfg = jload(config_path)
    script_pid = os.getpid()
    project, sequences = cfg["project"], cfg["sequences"]
    
    api_base = get(project, "comfy", "api_base")
    timeout_s = float(get(project, "comfy", "timeout_seconds", default=300))
    out_root = get(project, "comfy", "output_root")
    project_name = project["name"]
    full_w, full_h = int(project.get("width", 1280)), int(project.get("height", 720))
    half_w, half_h = full_w // 2, full_h // 2

    vg = get(project, "inbetween_generation", default={})
    wf_path = vg.get("video_workflow_json")
    iters_def = int(vg.get("video_iterations_default", 8))
    seed_start = int(vg.get("seed_start", 500000))
    seed_step = int(vg.get("advance_seed_by", 13))
    dur_def_sec = float(vg.get("duration_default_sec", 3.0))
    
    express_video = bool(get(project, "inbetween_generation", "express_video", default=False))
    quarter_size_video = bool(get(project, "inbetween_generation", "quarter_size_video", default=False))
    upscale_video = bool(get(project, "inbetween_generation", "upscale_video", default=DEFAULT_UPSCALE))
    # fix_slowmo = bool(get(project, "inbetween_generation", "fix_slowmo", default=DEFAULT_SLOMOFIX))
    # fix_primer = float(get(project, "inbetween_generation", "fix_slowmo_primer_cfg", default=DEFAULT_SLOMOFIX_CFG))
    # fix_main = float(get(project, "inbetween_generation", "fix_slowmo_main_cfg", default=1.0))





    try:
        g = jload(wf_path)
        _, cv = first_node_by_title(g, "Create Video")
        project_fps = float(get_fps_from_create_video(cv) or 24.0)
    except: project_fps = 24.0

    export_collect = []
    asset_id_counter = 1
    
    status_path = None
    if status_file_override:
        status_path = Path(status_file_override)
        if status_path.parent: status_path.parent.mkdir(parents=True, exist_ok=True)
    elif out_root and project_name:
        status_filename = "_export_status.json" if export_only else "_videos_status.json"
        status_path = Path(out_root) / project_name / status_filename
        (Path(out_root) / project_name).mkdir(parents=True, exist_ok=True)

    if status_path: _write_status(status_path, script_pid, "running", "Initializing...", progress_percent=0.0)

    try:
        # V2 Data Normalization: Sequences can be dict or list
        if isinstance(sequences, list): seq_list = sequences
        else: seq_list = sorted(sequences.values(), key=lambda x: x.get("order", 0))

        if not export_only:
            seq_multipliers = {}
            norm_enabled = bool(vg.get("lora_normalization_enabled", False))
            max_sat = float(vg.get("lora_normalization_max", 1.5))

            if norm_enabled:
                print("[MIXER] Pre-scanning for LoRA normalization...")
                for seq in seq_list:
                    seq_id = (seq.get("id") or seq.get("name") or "").strip()
                    if not seq_id: continue
                    bg_src = [style_line(project), seq.get("style_asset", ""), seq.get("style_prompt", ""), seq.get("setting_asset", ""), seq.get("setting_prompt", "")]
                    bg_sum = calculate_lane_sum(bg_src)
                    max_fg = 0.0
                    for _, _, vc in iter_video_entries(seq):
                        if not vc: continue
                        fg = calculate_lane_sum([seq.get("action_prompt",""), vc.get("inbetween_prompt","")])
                        if fg > max_fg: max_fg = fg
                    bg_mult = max_sat/bg_sum if bg_sum > max_sat else 1.0
                    fg_mult = max_sat/max_fg if max_fg > max_sat else 1.0
                    seq_multipliers[seq_id] = {'bg': bg_mult, 'fg': fg_mult}
                    if bg_mult < 1.0 or fg_mult < 1.0: print(f"  [{seq_id}] Locked: BG x{bg_mult:.2f} | FG x{fg_mult:.2f}")

            # Pre-scan total count
            total_iters, completed_iters = 0, 0
            for seq in seq_list:
                for _, _, vc in iter_video_entries(seq):
                    if not vc: continue
                    seq_id = (seq.get("id") or seq.get("name") or "").strip()
                    vid_key = vc.get("id", f"vid{_}") # V2 uses explicit ID
                    
                    vid_folder = os.path.join(out_root, project_name, seq_id, vc["id"])
                    base_name = f"{project_name}_{seq_id}_{vc['id']}"
                    
                    it = int(vc.get("video_iterations_override", iters_def))
                    ex = get_max_file_index(list_videos_with_prefix(vid_folder, base_name))
                    start = ex
                    end = (start + it) if vc.get("force_generate") else it
                    if start < end: total_iters += (end - start)

            print(f"Total iterations: {total_iters}")
            if total_iters == 0:
                if status_path: _write_status(status_path, script_pid, "completed", "Done", progress_percent=100.0)
                return

            # Main Loop
            # for seq_idx, seq in enumerate(seq_list):
            for seq_idx, seq in enumerate(seq_list):
            # Resolve Assets (match run_images.py behavior)
                setting_id = seq.get("setting_id")
                seq["setting_asset"] = next((i.get("prompt", "") for i in project.get("settings", []) if i.get("id") == setting_id), "")
                style_id = seq.get("style_id")
                seq["style_asset"] = next((i.get("prompt", "") for i in project.get("styles", []) if i.get("id") == style_id), "")

                seq_id = (seq.get("id") or seq.get("name") or "").strip()
                if not seq_id: continue
                
                # V2: "keyframes" dict. V1: "i2v_base_images" dict.
                ibase = seq.get("keyframes") or seq.get("i2v_base_images", {})
                
                seq_task = f"Sequence '{seq_id}' ({seq_idx+1}/{len(seq_list)})"
                
                for pos, (vid_idx, vid_key, vid_conf) in enumerate(iter_video_entries(seq)):
                    # vid_key from iter_video_entries is the dict key (V1) or ID (V2)
                    vid_id = vid_conf.get("id", vid_key)
                    
                    start_id, end_id = vid_conf.get("start_keyframe_id", vid_conf.get("start_id")), vid_conf.get("end_keyframe_id", vid_conf.get("end_id"))
                    ctype = "SE" if (start_id and end_id) else "OE" if end_id else "SO" if start_id else None
                    if not ctype: continue

                    iters = int(vid_conf.get("video_iterations_override", iters_def))
                    dur = float(vid_conf.get("duration_override_sec", dur_def_sec))
                    
                    pneg = project_neg(project)
                    neg_text = merge_negatives(pneg.get("global",""), pneg.get("inbetween_all",""), vid_conf.get("negative_prompt",""))

                    vid_folder = os.path.join(out_root, project_name, seq_id, vid_id)
                    base_name = f"{project_name}_{seq_id}_{vid_id}"
                    
                    mx = get_max_file_index(list_videos_with_prefix(vid_folder, base_name))
                    s_it, e_it = mx, iters
                    if vid_conf.get("force_generate"): e_it = s_it + iters
                    elif mx >= iters: s_it = e_it

                    for it in range(s_it, e_it):
                        sub_task = f"Vid '{vid_id}' ({pos+1}) - Iter {it+1}"
                        if status_path:
                            prog = (completed_iters / total_iters) * 100 if total_iters > 0 else 0
                            _write_status(status_path, script_pid, "running", seq_task, sub_task, progress_percent=prog, completed_count=completed_iters, total_count=total_iters)

                        vid_seed_override = vid_conf.get("seed_start")
                        effective_seed_start = int(vid_seed_override) if vid_seed_override is not None else seed_start
                        seed = effective_seed_start + it * seed_step   ## set here to fix
                        sp = get(ibase, start_id, "selected_image_path") if ctype in ("SE","SO") else None
                        ep = get(ibase, end_id, "selected_image_path") if ctype in ("OE","SE") else None
                        
                        if (ctype in ("SE","SO") and not sp) or (ctype in ("OE","SE") and not ep):
                            print(f"[WARN] Missing keyframe image for {vid_id}. Skipping.")
                            continue

                        # Frame path - use temp folder, will rename after we know actual mp4 filename
                        temp_frames_dir = os.path.join(vid_folder, "_temp_frames")
                        f_pre = os.path.join(temp_frames_dir, "frame")

                        try: graph = jload(wf_path)
                        except Exception as e: print(f"[ERR] Workflow load failed: {e}"); break
                        
                        # Detect workflow type (local variable, not stored on dict)
                        is_ltx2 = is_ltx2_workflow(wf_path)
                        
                        if is_ltx2:
                            print("[WORKFLOW TYPE] LTX-2 FLF")
                        else:
                            print("[WORKFLOW TYPE] WAN 2.2")

                        
                        if not is_ltx2:
                            if upscale_video: inject_film_vfi_upscaler(graph)
                            inject_frame_save_node(graph, f_pre)
                            
                            # High-noise steps stay constant; express only trims low-noise
                            # Full:    Primer 0→2, Iter 2→8, Fixed 8→14 (14 total)
                            # Express: Primer 0→2, Iter 2→8, Fixed 8→10 (10 total)
                            PRIMER_END = 2
                            ITER_END = 7 if express_video else 8
                            t_steps = 7 if express_video else 14
                            
                            # Update all three samplers with correct step ranges
                            for _, node in find_nodes_by_title(graph, "SlowMoPrimer"):
                                set_if_exists(node, "steps", t_steps)
                                set_if_exists(node, "start_at_step", 0)
                                set_if_exists(node, "end_at_step", PRIMER_END)
                            for _, node in find_nodes_by_title(graph, "IterKSampler"):
                                set_if_exists(node, "steps", t_steps)
                                set_if_exists(node, "start_at_step", PRIMER_END)
                                set_if_exists(node, "end_at_step", ITER_END)
                            for _, node in find_nodes_by_title(graph, "WanFixedSeed"):
                                set_if_exists(node, "steps", t_steps)
                                set_if_exists(node, "start_at_step", ITER_END)
                                set_if_exists(node, "end_at_step", t_steps)

                        else:
                            # LTX-2: Steps controlled by scheduler, not manually
                            t_steps = 0  # Placeholder for logging

                        enforce_project_dimensions_nodes(graph, full_w, full_h)
                        
                        # Prompt & LoRA
                        ptxt = compose_video_prompt(project, seq, vid_conf, it)
                        ploras = _LORA_RE.findall(ptxt)
                        pclean = _LORA_RE.sub("", ptxt).strip()
                        
                        if ploras:
                            mults = seq_multipliers.get(seq_id, {'bg':1.0,'fg':1.0})
                            fg_blob = (seq.get("action_prompt","") + " " + vid_conf.get("inbetween_prompt",""))
                            fl = []
                            for n, s_str in ploras:
                                try:
                                    s = float(s_str)
                                    tag = f"__lora:{n}:{s_str}__"
                                    m = mults['fg'] if tag in fg_blob else mults['bg']
                                    fl.append((n, str(s*m)))
                                except: fl.append((n, s_str))
                            inject_prompt_loras(graph, reversed(fl))

                        for n in find_nodes_by_title(graph, "PosPrompt"): set_if_exists(n[1], "text", pclean)
                        for n in find_nodes_by_title(graph, "NegPrompt"): set_if_exists(n[1], "text", neg_text)

                        
                        if is_ltx2:
                            # LTX-2: Direct image path update
                            update_ltx2_images(graph, sp, ep)
                        else:
                            # WAN: Complex loader wiring
                            l1, l2 = ensure_two_loaders(graph)
                            if sp: set_loader_path(graph[l1], sp)
                            if ep: set_loader_path(graph[l2], ep)
                            
                            wi, hi = (half_w, half_h) if quarter_size_video else (full_w, full_h)
                            try:
                                wire_half_to_wan(graph, ctype, l1, l2, wi, hi)
                                set_wan_size(graph, wi, hi)
                            except Exception as e: print(f"[ERR] Wiring failed: {e}"); continue

                        _, cv = first_node_by_title(graph, "Create Video")
                        fps = get_fps_from_create_video(cv) or project_fps
                        
                        # Update FPS in workflow if LTX-2
                        # is_ltx2 = graph.get('_is_ltx2', False)
                        if is_ltx2:
                            update_ltx2_fps(graph, fps)
                        
                        frames = int(round(dur * float(fps))) + 1
                        set_video_frames(graph, frames)
                        
                        update_video_saver(graph, vid_folder, base_name)
                        update_video_seeds(graph, seed)

                        print(f"\n[VID] {seq_id}/{vid_id} iter {it+1}")
                        print(f"[VID] type={ctype} dur={dur:.2f}s fps={fps} frames={frames} steps={t_steps} seed={seed}")
                        print(f"[VID] workflow={wf_path}")
                        print(f"[VID] out_folder={vid_folder}")
                        print(f"[VID] out_prefix={os.path.join(vid_folder, base_name)}")
                        print(f"[VID] start_image={sp}" if sp else "[VID] start_image=<none>")
                        print(f"[VID] end_image={ep}" if ep else "[VID] end_image=<none>")
                        print("\n[PROMPT]\n" + pclean)
                        print("\n[NEGATIVE]\n" + neg_text)

                        

                        try:
                            # Remove metadata before posting to ComfyUI
                            graph.pop('_is_ltx2', None)
                            
                            # Debug: Save workflow before posting
                            debug_path = os.path.join(vid_folder, f"debug_workflow_iter{it+1:05d}.json")
                            os.makedirs(os.path.dirname(debug_path), exist_ok=True)
                            with open(debug_path, 'w') as f:
                                json.dump(graph, f, indent=2)
                            print(f"[DEBUG] Saved workflow to: {debug_path}")
                            
                            pid = post_prompt(api_base, graph)
                            print(f"[DEBUG] Posted prompt {pid}, waiting with timeout={timeout_s}s")
                            wait_result = wait_history_done(api_base, pid, timeout_s)
                            print(f"[DEBUG] wait_history_done returned: {wait_result}")
                            if wait_result:
                                completed_iters += 1
                                snapshot_vid_data = dict(vid_conf)
                                snapshot_vid_data["inbetween_prompt"] = pclean
                                
                                # snap = {
                                #     "item_data": snapshot_vid_data,
                                #     "sequence_context": {"setting_prompt": seq.get("setting_prompt"), "style_prompt": seq.get("style_prompt")},
                                #     "project_context": {"model": project.get("model"), "width": full_w, "height": full_h},
                                #     "generation": {"seed": seed, "steps": t_steps, "fps": project_fps},
                                #     "meta": {"timestamp": datetime.now().isoformat()}
                                # }
                                snap = {
                                    "item_data": vid_conf,
                                    "sequence_context": {"setting_prompt": seq.get("setting_prompt"), "style_prompt": seq.get("style_prompt")},
                                    "project_context": {"model": project.get("model"), "width": full_w, "height": full_h, "steps": t_steps, "fps": project_fps},
                                    "generation": {"seed": seed, "executed_prompt": ptxt},
                                    "meta": {"timestamp": datetime.now().isoformat()}
                                }


                                # snap = {
                                #     "item_data": vid_conf,
                                #     "sequence_context": {"setting_prompt": seq.get("setting_prompt"), "style_prompt": seq.get("style_prompt")},
                                #     "project_context": {"model": project.get("model"), "width": full_w, "height": full_h},
                                #     "generation": {"seed": seed, "steps": t_steps, "fps": project_fps},
                                #     "meta": {"timestamp": datetime.now().isoformat()}
                                # }
                                cands = list_videos_with_prefix(vid_folder, base_name)
                                if cands:
                                    cands.sort(key=lambda f: os.path.getmtime(f), reverse=True)
                                    final_path = cands[0] 
                                    inject_metadata_mp4(cands[0], snap)
                                    print(f"RESULT: {final_path}")
                                    
                                    # Create lossless video from temp frames with matching name
                                    print(f"[LOSSLESS] is_ltx2={is_ltx2}, temp_frames_dir exists={os.path.isdir(temp_frames_dir)}, path={temp_frames_dir}")
                                    if not is_ltx2 and os.path.isdir(temp_frames_dir):
                                        lossless_path = final_path.replace(".mp4", "_lossless.mkv")
                                        print(f"[LOSSLESS] Stitching to: {lossless_path}")
                                        if stitch_frames_to_lossless(temp_frames_dir, lossless_path, fps=float(project_fps)):
                                            print(f"[LOSSLESS] Success, cleaning up")
                                            cleanup_temp_frames(temp_frames_dir)
                                        else:
                                            print(f"[LOSSLESS] Stitch failed!")
                                    else:
                                        print(f"[LOSSLESS] Skipped - is_ltx2={is_ltx2} or no temp_frames_dir")
                                    
                                if DROP_JOIN_FRAME and pos < len(iter_video_entries(seq)) - 1:
                                    # Logic to drop last frame if not last video
                                    pass 

                        except Exception as e: print(f"[ERR] Gen failed: {e}")

            if status_path: _write_status(status_path, script_pid, "completed", "Done", progress_percent=100.0, completed_count=completed_iters, total_count=total_iters)

        else:
            # Export Only Logic
            print("[EXPORT] Exporting FCPXML...")
            if status_path: _write_status(status_path, script_pid, "running", "Exporting...", progress_percent=0.0)
            
            for seq in seq_list:
                seq_id = (seq.get("id") or seq.get("name") or "").strip()
                if not seq_id: continue
                
                seq_export = {"seq_id": seq_id, "vids": []}
                export_collect.append(seq_export)
                
                for pos, (vid_idx, vid_key, vid_conf) in enumerate(iter_video_entries(seq)):
                    vid_id = vid_conf.get("id", vid_key)
                    start_id, end_id = vid_conf.get("start_keyframe_id", vid_conf.get("start_id")), vid_conf.get("end_keyframe_id", vid_conf.get("end_id"))
                    ctype = "SE" if (start_id and end_id) else "OE" if end_id else "SO" if start_id else None
                    if not ctype: continue

                    dur_sec = float(vid_conf.get("duration_override_sec", dur_def_sec))
                    vid_folder = os.path.join(out_root, project_name, seq_id, vid_id)
                    base_name = f"{project_name}_{seq_id}_{vid_id}"
                    
                    files = list_videos_with_prefix(vid_folder, base_name)
                    if not files: continue
                    
                    media_frames = int(round(dur_sec * float(project_fps))) + 1
                    ts = TRIM_SE_EACH_SIDE if ctype == "SE" else (TRIM_O_ONE_SIDE if ctype == "SO" else 0)
                    te = TRIM_SE_EACH_SIDE if ctype == "SE" else (TRIM_O_ONE_SIDE if ctype == "OE" else 0)
                    
                    clips = []
                    for f in files:
                        asset_ref = f"r{asset_id_counter:04d}"
                        asset_id_counter += 1
                        clips.append({"path": f, "name": os.path.basename(f), "media_frames": media_frames, "trim_start": ts, "trim_end": te, "asset_ref": asset_ref})
                    
                    if clips: seq_export["vids"].append({"vid_key": vid_id, "type": ctype, "clips": clips})

            if export_collect:
                write_fcpxml(project_name, full_w, full_h, export_collect, out_root, project_fps)
                if status_path: _write_status(status_path, script_pid, "completed", "Export Done", progress_percent=100.0)
            else:
                if status_path: _write_status(status_path, script_pid, "completed", "Nothing to export", progress_percent=100.0)

    except Exception as e:
        print(f"[FATAL] {e}")
        if status_path: _write_status(status_path, script_pid, "failed", error=str(e))
        sys.exit(1)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--status-file", required=False)
    args = ap.parse_args()
    run(args.config, export_only=args.export_only, status_file_override=args.status_file)