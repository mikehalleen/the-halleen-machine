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
from lora_tags import LoraSpec, find_lora_tags, format_lora_tag, has_lora_named, scale_lora_spec, strip_lora_tags
import workflow_controls as wc
import comfy_history
import comfy_progress
import generation_spec
_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"
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

PRIMER_STEPS = 2  # THM-SlowMoPrimer / legacy primer budget; not UI-configurable (change here only)
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


def calculate_lane_sum(text_sources: list):
    total = 0.0
    for text in text_sources:
        if not text: continue
        for spec in find_lora_tags(text):
            total += spec.strength
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

def _write_status(status_path, pid: int, status: str, current_task: str = None, sub_task: str = None, error: str = None, progress_percent: float = None, completed_count: int = None, total_count: int = None, outputs: list = None, extra: dict = None):
    try:
        status_data = {
            "pid": pid, "status": status, "current_task": current_task, "sub_task": sub_task, "error": error,
            "progress_percent": f"{progress_percent:.1f}" if progress_percent is not None else None,
            "batch_completed_count": completed_count, "batch_total_count": total_count, "last_update": datetime.now().isoformat()
        }
        if extra:
            # live ComfyUI progress (comfy_progress.PromptTracker): phase, step/steps, pass_no/passes, queue_ahead...
            status_data.update({k: v for k, v in extra.items() if k not in status_data})
        if outputs is not None:
            # Files ComfyUI reported for this run: [{"path", "node_id", "prompt_id", "label"}]
            status_data["outputs"] = list(outputs)
        temp_path = str(status_path) + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f: json.dump(status_data, f, indent=2)
        os.replace(temp_path, status_path)
    except Exception as e: print(f"[WARN] Failed to write status file '{status_path}': {e}")

def apply_force_open(ctype, force_open):
    """Honor the project ``force_open`` override that turns a closed oner into a cut.

    A video with BOTH keyframes normally renders as ``SE`` and its start/end frames
    are matched against the neighboring clips to create one continuous take. When the
    project setting ``inbetween_generation.force_open`` is set, one of the two images
    is dropped so every such clip renders open-ended instead, breaking that join into
    a hard cut:

        force_open == "start" -> ignore the start image -> "OE" (open start)
        force_open == "end"   -> ignore the end image   -> "SO" (open end)
        anything else / empty -> no change (default)

    The override only applies to ``SE`` clips. A video that already has just one
    keyframe (``SO``/``OE``) or none is returned unchanged, so the setting is a no-op
    for clips that are already open.
    """
    if ctype != "SE":
        return ctype
    mode = str(force_open or "").strip().lower()
    if mode == "start":
        return "OE"
    if mode == "end":
        return "SO"
    return ctype

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

def post_prompt(api_base, graph, project_name=None, label=None, output_root=None, client_id=None):
    wc.strip_prompt_metadata(graph)
    wc.annotate_comfy_image_inputs(graph, output_root=output_root or "", api_base=api_base)
    extra_data = {}
    if project_name:
        extra_data["machine_ui_project"] = project_name
    if label:
        extra_data["machine_ui_label"] = label
    comfy_api_key = os.environ.get("THM_COMFY_API_KEY", "").strip()
    if comfy_api_key:
        extra_data["api_key_comfy_org"] = comfy_api_key
    r = requests.post(api_base.rstrip("/") + "/prompt", json={"prompt": graph, "client_id": client_id or str(uuid.uuid4()), "extra_data": extra_data}, timeout=60)
    if not r.ok:
        print(f"[COMFY] POST /prompt failed: {r.status_code}")
        try:
            print(r.text)
        except Exception:
            pass
    r.raise_for_status()
    return r.json().get("prompt_id")

def fetch_history_entry(api_base, prompt_id, timeout_s=3600, poll_s=1.0):
    """The ComfyUI history entry for ``prompt_id``, or None on timeout or execution error."""
    entry = comfy_history.poll_history_entry(api_base, prompt_id, timeout_s, poll_s=poll_s)
    if entry is None:
        return None
    detail = comfy_history.history_error_detail(entry)
    if detail:
        print(f"[ERR] ComfyUI prompt {prompt_id} failed: {detail}")
        return None
    return entry

def wait_history_done(api_base, prompt_id, timeout_s=3600, poll_s=1.0):
    return fetch_history_entry(api_base, prompt_id, timeout_s, poll_s) is not None

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
    if not wan_nid:
        tagged = wc.find_control_nodes(graph, wc.VIDEO_GENERATOR)
        if tagged:
            wan_nid = tagged[0].node_id
    if not wan_nid:
        raise RuntimeError("Video generator node not found (THM-VideoGenerator or WanFirstLastFrameToVideo).")
    start_scale_nid = ensure_scale_node(graph, "StartImage", half_w, half_h)
    end_scale_nid   = ensure_scale_node(graph, "EndImage", half_w, half_h)
    if clip_type in ("SE","SO"):
        connect_output_to_input(graph, l1, start_scale_nid, "image", out_index=0)
        connect_output_to_input(graph, start_scale_nid, wan_nid, "start_image", out_index=0)
    if clip_type in ("SE","OE"):
        connect_output_to_input(graph, l2, end_scale_nid, "image", out_index=0)
        connect_output_to_input(graph, end_scale_nid, wan_nid, "end_image", out_index=0)


def get_fps_from_create_video(node):
    if not isinstance(node, dict) or "inputs" not in node: return None
    for k in ("fps","frame_rate","framerate"):
        v = node["inputs"].get(k)
        if isinstance(v, (int,float)) and v > 0: return float(v)
    return None


def _resolve_project_fps(vg: dict, wf_path: str | None) -> float:
    if vg.get("fps") is not None:
        return float(vg["fps"])
    if not wf_path:
        return 16.0
    try:
        graph = jload(wf_path)
        for control_node in wc.find_control_nodes(graph, wc.FRAME_RATE):
            for key in ("value", "float", "fps"):
                val = control_node.node.get("inputs", {}).get(key)
                if isinstance(val, (int, float)) and val > 0:
                    return float(val)
        _, cv = first_node_by_title(graph, "Create Video")
        baked = get_fps_from_create_video(cv)
        if baked:
            return baked
    except Exception:
        pass
    return 16.0


def _apply_express_sampler_steps(
    graph: dict,
    express_video: bool,
    *,
    total_steps: int = 14,
) -> int:
    """Split legacy SlowMoPrimer / IterKSampler / WanFixedSeed ranges from project total_steps."""
    primer_end = PRIMER_STEPS
    if express_video:
        t_steps = max(primer_end + 1, int(total_steps) // 2)
        iter_end = t_steps
    else:
        t_steps = max(primer_end + 2, int(total_steps))
        post_primer = t_steps - primer_end
        iter_end = primer_end + post_primer // 2
    for _, node in find_nodes_by_title(graph, "SlowMoPrimer"):
        set_if_exists(node, "steps", t_steps)
        set_if_exists(node, "start_at_step", 0)
        set_if_exists(node, "end_at_step", primer_end)
    for _, node in find_nodes_by_title(graph, "IterKSampler"):
        set_if_exists(node, "steps", t_steps)
        set_if_exists(node, "start_at_step", primer_end)
        set_if_exists(node, "end_at_step", iter_end)
    for _, node in find_nodes_by_title(graph, "WanFixedSeed"):
        set_if_exists(node, "steps", t_steps)
        set_if_exists(node, "start_at_step", iter_end)
        set_if_exists(node, "end_at_step", t_steps)
    return t_steps


def _warn_partner_key_missing(vid_caps: "wc.VideoCapabilities") -> None:
    if vid_caps.has_partner_node and not os.environ.get("THM_COMFY_API_KEY", "").strip():
        classes = ", ".join(vid_caps.partner_node_classes) or "unknown"
        print(
            f"[VIDEO] WARNING: workflow contains THM-PartnerNode ({classes}) "
            "but THM_COMFY_API_KEY is not set - the partner API call will likely fail. "
            "Set the environment variable before launching THM."
        )

def _log_video_caps(
    caps: wc.VideoCapabilities,
    fps: float,
    frames: int,
    clip_type: str | None = None,
) -> None:
    print(f"[VIDEO] lora_mode={caps.lora_mode}")
    if clip_type:
        use_start = clip_type in ("SE", "SO")
        use_end = clip_type in ("SE", "OE")
        print(
            f"[VIDEO] frame_mode={clip_type} "
            f"start={'active' if use_start else 'off'} "
            f"end={'active' if use_end else 'off'}"
        )
    print(
        f"[VIDEO] frame_support start={'yes' if caps.supports_start_frame else 'no'} "
        f"end={'yes' if caps.supports_end_frame else 'no'}"
    )
    for label, found in (
        ("THM-VideoGenerator", caps.has_video_generator),
        ("THM-FrameCount", caps.has_frame_count),
        ("THM-FrameRate", caps.has_frame_rate),
        ("THM-SaveVideo", caps.has_save_video),
        ("THM-StartFrame-tag", caps.has_start_frame),
        ("THM-EndFrame-tag", caps.has_end_frame),
        ("THM-VideoReference", caps.video_reference_count > 0),
        ("THM-KSampler", caps.has_thm_ksampler_passes),
        ("THM-SlowMoPrimer", caps.has_thm_slowmo_primer),
        ("THM-Steps", caps.has_thm_steps),
        ("express_samplers", caps.has_express_samplers),
        ("legacy_wan_generator", caps.has_legacy_wan_generator),
    ):
        print(f"[VIDEO] {label}: {'found' if found else 'missed'}")
    print(f"[VIDEO] fps={fps} frames={frames}")

VIDEO_OUTPUT_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv")

def _history_video_hit(found, folder, base_name):
    """The single video ComfyUI reported in ``folder`` for this clip, else None."""
    hits = comfy_history.outputs_in_folder(found, folder, VIDEO_OUTPUT_EXTS, prefix=base_name)
    return hits[0] if len(hits) == 1 else None

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



def _normalize_lora_specs(lora_list) -> list[LoraSpec]:
    from workflow_controls import _coerce_lora_spec

    specs: list[LoraSpec] = []
    for entry in lora_list or []:
        spec = _coerce_lora_spec(entry)
        if spec:
            specs.append(spec)
    return specs


def inject_prompt_loras(graph: dict, lora_list):
    """Legacy dual-pass LoRA injection (i2v_base); delegates to workflow_controls."""
    return wc.inject_video_dual_loras(graph, lora_list, resolve_pair=resolve_lora_pair)


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
    discovered_outputs: list = []  # history-reported files, mirrored into the status file
    project, sequences = cfg["project"], cfg["sequences"]
    
    api_base = get(project, "comfy", "api_base")
    timeout_s = float(get(project, "comfy", "timeout_seconds", default=300))
    out_root = get(project, "comfy", "output_root")
    project_name = project["name"]
    full_w, full_h = int(project.get("width", 1280)), int(project.get("height", 720))
    half_w, half_h = full_w // 2, full_h // 2

    vg = get(project, "inbetween_generation", default={})
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from thm_core.runner_helpers import resolve_project_video_workflow, resolve_project_video_seed_titles

    wf_path = resolve_project_video_workflow({"project": project})
    iters_def = int(vg.get("video_iterations_default", 8))
    seed_start = int(vg.get("seed_start", 500000))
    seed_step = int(vg.get("advance_seed_by", 13))
    dur_def_sec = float(vg.get("duration_default_sec", 3.0))
    # Project-level open/closed override applied to every SE (both-keyframe) in-between,
    # for one-off and batch generation alike. "" (default) | "start" | "end".
    force_open_mode = str(vg.get("force_open") or "").strip().lower()

    # express_video (aka "draft mode") is deprecated: step reduction is now handled by the
    # custom workflow, and the toggle was removed from the UI. Force it off so run_video always
    # uses the full step budget, ignoring any leftover inbetween_generation.express_video in a project.
    express_video = False
    quarter_size_video = bool(get(project, "inbetween_generation", "quarter_size_video", default=False))
    upscale_video = bool(get(project, "inbetween_generation", "upscale_video", default=DEFAULT_UPSCALE))
    # fix_slowmo = bool(get(project, "inbetween_generation", "fix_slowmo", default=DEFAULT_SLOMOFIX))
    # fix_primer = float(get(project, "inbetween_generation", "fix_slowmo_primer_cfg", default=DEFAULT_SLOMOFIX_CFG))
    # fix_main = float(get(project, "inbetween_generation", "fix_slowmo_main_cfg", default=1.0))





    # Family-aware: Default overrides to i2v_base's seed nodes even when the project
    # still stores a last-used custom workflow's titles.
    seed_target_title, seed_exclude_title = resolve_project_video_seed_titles({"project": project})
    project_fps = _resolve_project_fps(vg, wf_path)

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

    if status_path: _write_status(status_path, script_pid, "running", "Initializing...", progress_percent=0.0, outputs=discovered_outputs)

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
                if status_path: _write_status(status_path, script_pid, "completed", "Done", progress_percent=100.0, outputs=discovered_outputs)
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
                    base_ctype = "SE" if (start_id and end_id) else "OE" if end_id else "SO" if start_id else None
                    ctype = apply_force_open(base_ctype, force_open_mode)
                    if not ctype: continue
                    if ctype != base_ctype:
                        dropped = "start" if ctype == "OE" else "end"
                        print(f"[VID] {seq_id}/{vid_id}: force_open='{force_open_mode}' -> "
                              f"{base_ctype} treated as {ctype} (ignoring {dropped} frame, join becomes a cut)")

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
                            _write_status(status_path, script_pid, "running", seq_task, sub_task, progress_percent=prog, completed_count=completed_iters, total_count=total_iters, outputs=discovered_outputs)

                        vid_seed_override = vid_conf.get("seed_start")
                        effective_seed_start = int(vid_seed_override) if vid_seed_override is not None else seed_start
                        seed = effective_seed_start + it * seed_step   ## set here to fix
                        sp = get(ibase, start_id, "selected_image_path") if ctype in ("SE","SO") else None
                        ep = get(ibase, end_id, "selected_image_path") if ctype in ("OE","SE") else None

                        # Frame path - use temp folder, will rename after we know actual mp4 filename
                        temp_frames_dir = os.path.join(vid_folder, "_temp_frames")
                        f_pre = os.path.join(temp_frames_dir, "frame")

                        try: graph = jload(wf_path)
                        except Exception as e: print(f"[ERR] Workflow load failed: {e}"); break

                        vid_caps = wc.discover_video_capabilities(graph)
                        _warn_partner_key_missing(vid_caps)
                        need_start = vid_caps.supports_start_frame and ctype in ("SE", "SO")
                        need_end = vid_caps.supports_end_frame and ctype in ("SE", "OE")
                        # An unfed guide branch still executes (ComfyUI ignores node mute),
                        # so an open end/start has to leave the graph entirely.
                        if not need_end:
                            wc.deactivate_video_frame_branch(graph, "end")
                        if not need_start:
                            wc.deactivate_video_frame_branch(graph, "start")
                        if (need_start and not sp) or (need_end and not ep):
                            print(f"[WARN] Missing keyframe image for {vid_id}. Skipping.")
                            continue

                        total_steps = int(vg.get("video_steps_default", 14))
                        primer_steps = PRIMER_STEPS
                        has_tagged_samplers = (
                            vid_caps.has_thm_ksampler_passes or vid_caps.has_thm_slowmo_primer
                        )
                        has_sampler_injection = (
                            has_tagged_samplers
                            or vid_caps.has_express_samplers
                            or vid_caps.has_thm_steps
                        )
                        t_steps = 0
                        if has_sampler_injection:
                            print(f"[VIDEO] total_steps={total_steps} (project video_steps_default)")
                            if upscale_video:
                                inject_film_vfi_upscaler(graph)
                            if has_tagged_samplers or vid_caps.has_express_samplers:
                                inject_frame_save_node(graph, f_pre)
                            if has_tagged_samplers:
                                t_steps = wc.apply_video_sampler_passes(
                                    graph,
                                    total_steps=total_steps,
                                    express=express_video,
                                    primer_steps=primer_steps,
                                )
                            elif vid_caps.has_express_samplers:
                                t_steps = _apply_express_sampler_steps(
                                    graph,
                                    express_video,
                                    total_steps=total_steps,
                                )
                            elif vid_caps.has_thm_steps:
                                wc.set_steps(graph, total_steps)
                                t_steps = total_steps

                        ptxt = compose_video_prompt(project, seq, vid_conf, it)
                        plora_specs = find_lora_tags(ptxt)
                        pclean = strip_lora_tags(ptxt)

                        if wc.workflow_uses_ltx_text_encoder(graph):
                            pclean = wc.sanitize_prompt_for_ltx(pclean)
                            neg_text = wc.sanitize_prompt_for_ltx(neg_text)

                        normalized_loras = None
                        if plora_specs:
                            mults = seq_multipliers.get(seq_id, {"bg": 1.0, "fg": 1.0})
                            fg_blob = (seq.get("action_prompt", "") + " " + vid_conf.get("inbetween_prompt", ""))
                            fl: list[LoraSpec] = []
                            for spec in plora_specs:
                                m = mults["fg"] if has_lora_named(fg_blob, spec.name) else mults["bg"]
                                fl.append(scale_lora_spec(spec, m))
                            normalized_loras = list(reversed(fl))

                        wi, hi = (half_w, half_h) if quarter_size_video else (full_w, full_h)
                        frames = int(round(dur * float(project_fps))) + 1

                        if not (vid_caps.has_start_frame or vid_caps.has_end_frame) and vid_caps.has_legacy_wan_generator:
                            l1, l2 = ensure_two_loaders(graph)
                            if sp:
                                set_loader_path(graph[l1], sp)
                            if ep:
                                set_loader_path(graph[l2], ep)
                            try:
                                wire_half_to_wan(graph, ctype, l1, l2, wi, hi)
                            except Exception as e:
                                print(f"[ERR] Wiring failed: {e}")
                                continue

                        # Single-path markers are THM-owned: always run injection so a marker's
                        # baked LoRA is neutralised even when no LoRAs are selected (WORKFLOWS.md
                        # "THM owns that input fully"). Dual mode anchors on the UNet loaders and
                        # has nothing baked to clear, so it still needs a non-empty list.
                        if vid_caps.lora_mode == "single":
                            wc.inject_loras(graph, normalized_loras)
                        elif vid_caps.lora_mode == "dual" and normalized_loras:
                            inject_prompt_loras(graph, normalized_loras)

                        inj_ctx = wc.VideoInjectionContext(
                            positive_prompt=pclean,
                            negative_prompt=neg_text,
                            seed=seed,
                            fps=project_fps,
                            frame_count=frames,
                            width=wi,
                            height=hi,
                            save_video_prefix=os.path.join(vid_folder, base_name),
                            frame_clip_type=ctype,
                            start_frame_path=sp,
                            end_frame_path=ep,
                            seed_target_title=seed_target_title,
                            seed_exclude_title=seed_exclude_title,
                        )
                        wc.apply_video_injection(graph, inj_ctx)
                        _log_video_caps(vid_caps, project_fps, frames, clip_type=ctype)

                        print(f"\n[VID] {seq_id}/{vid_id} iter {it+1}")
                        print(f"[VID] type={ctype} dur={dur:.2f}s fps={project_fps} frames={frames} steps={t_steps} seed={seed}")
                        print(f"[VID] workflow={wf_path}")
                        print(f"[VID] out_folder={vid_folder}")
                        print(f"[VID] out_prefix={os.path.join(vid_folder, base_name)}")
                        print(f"[VID] start_image={sp}" if sp else "[VID] start_image=<none>")
                        print(f"[VID] end_image={ep}" if ep else "[VID] end_image=<none>")
                        print("\n[PROMPT]\n" + pclean)
                        print("\n[NEGATIVE]\n" + neg_text)

                        

                        try:
                            # Debug: Save workflow before posting
                            debug_path = os.path.join(vid_folder, f"debug_workflow_iter{it+1:05d}.json")
                            os.makedirs(os.path.dirname(debug_path), exist_ok=True)
                            with open(debug_path, 'w') as f:
                                json.dump(graph, f, indent=2)
                            print(f"[DEBUG] Saved workflow to: {debug_path}")
                            
                            job_label = f"{seq_id}|{vid_id}" if seq_id and seq_id != vid_id else vid_id
                            pid = post_prompt(api_base, graph, project_name=project_name, label=job_label, output_root=out_root)
                            print("[PID]", pid)
                            print(f"[DEBUG] Posted prompt {pid}, waiting with timeout={timeout_s}s")
                            entry = fetch_history_entry(api_base, pid, timeout_s)
                            wait_result = entry is not None
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
                                # Prefer the exact file ComfyUI reported for this prompt; fall back to mtime.
                                found = comfy_history.extract_history_outputs(entry, out_root, prompt_id=pid, label=job_label)
                                discovered_outputs.extend(found)
                                final_path = _history_video_hit(found, vid_folder, base_name)
                                if final_path is None:
                                    print(f"[WARN] history reported no single video output in {vid_folder}; falling back to newest file by mtime")
                                    cands = list_videos_with_prefix(vid_folder, base_name)
                                    if cands:
                                        cands.sort(key=lambda f: os.path.getmtime(f), reverse=True)
                                        final_path = cands[0]
                                if final_path:
                                    inject_metadata_mp4(final_path, snap)
                                    print(f"RESULT: {final_path}")
                                    
                                    # Create lossless video from temp frames with matching name
                                    print(f"[LOSSLESS] temp_frames_dir exists={os.path.isdir(temp_frames_dir)}, path={temp_frames_dir}")
                                    if os.path.isdir(temp_frames_dir):
                                        lossless_path = final_path.replace(".mp4", "_lossless.mkv")
                                        print(f"[LOSSLESS] Stitching to: {lossless_path}")
                                        if stitch_frames_to_lossless(temp_frames_dir, lossless_path, fps=float(project_fps)):
                                            print(f"[LOSSLESS] Success, cleaning up")
                                            cleanup_temp_frames(temp_frames_dir)
                                        else:
                                            print(f"[LOSSLESS] Stitch failed!")
                                    else:
                                        print(f"[LOSSLESS] Skipped - no temp_frames_dir")
                                    
                                if DROP_JOIN_FRAME and pos < len(iter_video_entries(seq)) - 1:
                                    # Logic to drop last frame if not last video
                                    pass 

                        except Exception as e: print(f"[ERR] Gen failed: {e}")

            if status_path: _write_status(status_path, script_pid, "completed", "Done", progress_percent=100.0, completed_count=completed_iters, total_count=total_iters, outputs=discovered_outputs)

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
                    ctype = apply_force_open(ctype, force_open_mode)
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
        if status_path: _write_status(status_path, script_pid, "failed", error=str(e), outputs=discovered_outputs)
        sys.exit(1)

# ================= THM ENTRY MODE =================

def _inject_legacy_wan_frames(graph, vid_caps, clip_type, sp, ep, width=None, height=None) -> bool:
    """Untagged WAN first/last-frame graphs (no THM-StartFrame / THM-EndFrame): the 1.x wiring.

    ``apply_video_injection`` only writes anchors into tagged loaders, so a legacy
    graph such as ``i2v_base.json`` would be posted with its loaders still at
    ``example.png``. Mirror the 1.x batch path: take the first two LoadImage
    nodes (creating them if needed), write the paths, and route them through the
    ``StartImage`` / ``EndImage`` scalers into the generator. Scaler size comes
    from the params, else the generator's own width and height.
    """
    if vid_caps.has_start_frame or vid_caps.has_end_frame or not vid_caps.has_legacy_wan_generator:
        return False
    l1, l2 = ensure_two_loaders(graph)
    if sp:
        set_loader_path(graph[l1], sp)
    if ep:
        set_loader_path(graph[l2], ep)
    _, wan = first_node_by_title(graph, "WanFirstLastFrameToVideo")
    wan_inputs = (wan or {}).get("inputs") or {}
    w = width or wan_inputs.get("width") or 512
    h = height or wan_inputs.get("height") or 512
    wire_half_to_wan(graph, clip_type, l1, l2, w, h)
    print(f"[VIDEO] legacy WAN anchors: start={l1 if sp else '<none>'} end={l2 if ep else '<none>'} scale={w}x{h}")
    return True


def _set_legacy_wan_length(graph, vid_caps, frames) -> bool:
    """``set_frame_count`` only writes tagged nodes; a legacy WAN generator keeps its baked ``length``.

    The spec's ``length_s`` is the shot's LENGTH, so honour it on the generator itself.
    """
    if not frames or not vid_caps.has_legacy_wan_generator:
        return False
    if wc.find_control_nodes(graph, wc.FRAME_COUNT) or wc.find_control_nodes(graph, wc.VIDEO_GENERATOR):
        return False
    _, wan = first_node_by_title(graph, "WanFirstLastFrameToVideo")
    inputs = (wan or {}).get("inputs")
    if not isinstance(inputs, dict) or "length" not in inputs:
        return False
    inputs["length"] = int(frames)
    print(f"[VIDEO] legacy WAN length={int(frames)}")
    return True


def run_generation(spec_path, status_file_override=None):
    """THM entry mode: run one video workflow from a generation spec.

    The spec (see ``generation_spec.py``) carries the submitted prompt and
    negative, prelude lines, paths by slot (``start`` / ``end`` anchors, image
    slot node ids, THM-VideoReference node ids), params (seed, length_s, fps,
    width, height, steps), and structured LoRAs. No 1.x project payload is read.

    Not applied in this mode, since they are 1.x project flags: the legacy
    express sampler triple, the FILM upscaler, the temp-frame saver, and the
    lossless stitch. Outputs come from ComfyUI history and are written into the
    status file; each is printed as ``RESULT: <path>``.
    """
    spec = generation_spec.load_generation_spec(spec_path)
    script_pid = os.getpid()
    comfy = spec["comfy"]
    api_base = comfy["api_base"]
    out_root = comfy["output_root"]
    timeout_s = float(comfy.get("timeout_seconds", 3600))
    output_dir = os.path.abspath(spec["output_dir"])
    prefix = str(spec["filename_prefix"])
    label = str(spec.get("label") or spec.get("id") or prefix)
    params = spec.get("params") or {}
    paths_by_slot = generation_spec.paths_by_slot_from_spec(spec)
    status_path = generation_spec.generation_status_path(spec, status_file_override)
    outputs: list = []
    ids: dict = {}  # {"prompt_id": ...} once posted; kept on the final write so a late pickup can find the entry
    _write_status(status_path, script_pid, "running", "Preparing graph", progress_percent=0.0, outputs=outputs)

    try:
        generation_spec.output_dir_relative_to_root(output_dir, out_root)
        wf_name = str(spec["workflow"])
        wf_path = wf_name if os.path.isabs(wf_name) else str(_WORKFLOWS_DIR / wf_name)
        if not os.path.isfile(wf_path):
            raise FileNotFoundError(f"Workflow not found: {wf_path}")
        graph = jload(wf_path)
        vid_caps = wc.discover_video_capabilities(graph)
        _warn_partner_key_missing(vid_caps)

        # Anchors: an unfed guide branch still executes, so an absent side leaves the graph.
        sp, ep = paths_by_slot.get("start"), paths_by_slot.get("end")
        need_start = bool(vid_caps.supports_start_frame and sp)
        need_end = bool(vid_caps.supports_end_frame and ep)
        if not need_end:
            wc.deactivate_video_frame_branch(graph, "end")
        if not need_start:
            wc.deactivate_video_frame_branch(graph, "start")
        ctype = "SE" if (need_start and need_end) else "SO" if need_start else "OE" if need_end else None

        # Canvas: the project's width/height from the spec. ``params.quarter`` (a per-generation
        # draft toggle, the 2.0 form of 1.x's project-wide quarter_size_video) halves both sides,
        # a quarter of the pixels. Plain integer halves, no rounding: any multiple-of-N rule is
        # the workflow's own business, as it is for whatever canvas the project sets.
        width = int(params["width"]) if params.get("width") else None
        height = int(params["height"]) if params.get("height") else None
        quarter = bool(params.get("quarter")) and width is not None and height is not None
        if quarter:
            width, height = width // 2, height // 2
        if ctype:
            _inject_legacy_wan_frames(graph, vid_caps, ctype, sp if need_start else None, ep if need_end else None, width, height)

        # Reference slots: image loaders by node id, video loaders by node id.
        slots = wc.discover_image_reference_slots(graph)
        wc.clear_tagged_reference_nodes(graph)
        wc.clear_tagged_video_reference_nodes(graph)
        if slots:
            wc.inject_reference_slot_paths(graph, slots, paths_by_slot)
        wc.inject_video_reference_slot_paths(graph, wc.discover_video_reference_slots(graph), paths_by_slot)
        wc.deactivate_empty_reference_loaders(graph)

        fps = float(params["fps"]) if params.get("fps") else _resolve_project_fps({}, wf_path)
        length_s = params.get("length_s")
        frames = int(round(float(length_s) * fps)) + 1 if length_s else 0

        t_steps = 0
        if params.get("steps"):
            steps = int(params["steps"])
            if vid_caps.has_thm_ksampler_passes or vid_caps.has_thm_slowmo_primer:
                t_steps = wc.apply_video_sampler_passes(graph, total_steps=steps, express=False, primer_steps=PRIMER_STEPS)
            elif vid_caps.has_thm_steps:
                wc.set_steps(graph, steps)
                t_steps = steps

        # LoRAs ride in the prompt text as <lora:name:strength[:strength_b]> tags (or the 1.x
        # __lora:...__ form); they come out before the text reaches the encoder.
        raw_text = generation_spec.prompt_text_from_spec(spec)
        lora_specs = generation_spec.lora_specs_from_spec(spec, raw_text)
        text = strip_lora_tags(raw_text)
        negative = str(spec.get("negative") or "")
        if wc.workflow_uses_ltx_text_encoder(graph):
            text = wc.sanitize_prompt_for_ltx(text)
            negative = wc.sanitize_prompt_for_ltx(negative)
        if lora_specs:
            print("[LORAS] " + ", ".join(format_lora_tag(s) for s in lora_specs))

        seed = int(params["seed"]) if params.get("seed") is not None else random.randint(0, 2**31 - 1)

        inj_ctx = wc.VideoInjectionContext(
            positive_prompt=text,
            negative_prompt=negative,
            seed=seed,
            fps=fps,
            frame_count=frames,
            width=width,
            height=height,
            save_video_prefix=os.path.join(output_dir, prefix),
            frame_clip_type=ctype,
            start_frame_path=sp if need_start else None,
            end_frame_path=ep if need_end else None,
            lora_specs=lora_specs or None,
        )
        wc.apply_video_injection(graph, inj_ctx)
        _set_legacy_wan_length(graph, vid_caps, frames)
        _log_video_caps(vid_caps, fps, frames, clip_type=ctype)
        os.makedirs(output_dir, exist_ok=True)

        print(f"[VID] workflow={wf_path}")
        print(f"[VID] type={ctype} length_s={length_s} fps={fps} frames={frames} steps={t_steps} seed={seed}")
        if quarter:
            print(f"[VID] quarter size: {width}x{height}")
        print(f"[VID] out_prefix={os.path.join(output_dir, prefix)}")
        print("\n[PROMPT]\n" + text)
        print("\n[NEGATIVE]\n" + negative)

        _write_status(status_path, script_pid, "running", "queued", progress_percent=None, outputs=outputs)

        def on_progress(fields):
            _write_status(status_path, script_pid, "running", fields.get("task"), progress_percent=fields.get("progress_percent"), outputs=outputs, extra=fields)

        with comfy_progress.PromptTracker(api_base, graph, on_progress) as tracker:
            pid = post_prompt(api_base, graph, project_name=spec.get("project"), label=label, output_root=out_root, client_id=tracker.client_id)
            print("[PID]", pid)
            ids["prompt_id"] = str(pid)
            entry = tracker.wait(pid, timeout_s)
        if entry is None:
            raise RuntimeError(f"prompt {pid} {tracker.gave_up or 'gave up'}")
        # what the render itself took, queue wait excluded: ComfyUI's own clock from the history
        # entry, else the tracker's execution_start to now
        exec_s = comfy_history.history_exec_seconds(entry)
        if exec_s is None and tracker.exec_started_at is not None:
            exec_s = round(time.time() - tracker.exec_started_at, 1)
        if exec_s is not None:
            ids["exec_seconds"] = exec_s
            print(f"[TIMING] comfy execution {exec_s:.1f}s")
        err = comfy_history.history_error_detail(entry)
        if err:
            raise RuntimeError(f"ComfyUI prompt {pid} failed: {err}")

        found = comfy_history.extract_history_outputs(entry, out_root, prompt_id=pid, label=label)
        if not found:
            print(f"[WARN] history reported no outputs for {pid}; falling back to newest file by mtime")
            cands = list_videos_with_prefix(output_dir, prefix)
            if cands:
                cands.sort(key=lambda f: os.path.getmtime(f), reverse=True)
                found = [{"path": cands[0], "node_id": "", "prompt_id": str(pid), "label": label}]
        outputs.extend(found)

        snap = {
            "thm": {
                "generation": spec.get("id"),
                "project": spec.get("project"),
                "workflow": os.path.basename(wf_path),
                "prompt": text,
                "negative": negative,
                "seed": seed,
                "params": params,
                "fps": fps,
                "frames": frames,
                "steps": t_steps,
                "clip_type": ctype,
                "loras": spec.get("loras") or [],
                "paths_by_slot": paths_by_slot,
            },
            "meta": {"timestamp": datetime.now().isoformat()},
        }
        for o in found:
            path = o["path"]
            if path.lower().endswith(VIDEO_OUTPUT_EXTS):
                inject_metadata_mp4(path, snap)
            print(f"RESULT: {path}")
        _write_status(status_path, script_pid, "completed", "Done", progress_percent=100.0, outputs=outputs, extra=ids)
    except Exception as e:
        print(f"[FATAL] {e}")
        _write_status(status_path, script_pid, "failed", error=str(e), outputs=outputs, extra=ids)
        sys.exit(1)


def build_arg_parser():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", help="1.x project payload with sequences")
    mode.add_argument("--generation", help="THM generation spec JSON (see generation_spec.py)")
    ap.add_argument("--export-only", action="store_true", help="FCPXML export (with --config)")
    ap.add_argument("--status-file", required=False)
    return ap


if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    if args.generation:
        run_generation(args.generation, status_file_override=args.status_file)
    else:
        run(args.config, export_only=args.export_only, status_file_override=args.status_file)