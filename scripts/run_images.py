# scripts/run_images.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Still-image generator (i2v base images) for ComfyUI.

Reads the project JSON config and, for each sequence's keyframes:
  - loads the workflow for this ID
  - injects per-ID pose, characters, LoRAs
  - updates KSampler seeds
  - posts to ComfyUI

Refactored for V2 Data Model (Dictionary-based Sequences/Keyframes + Explicit Order).
"""

import argparse, json, os, time, uuid, requests, sys
from typing import Any
from datetime import datetime
from pathlib import Path
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import re
import random
import workflow_controls as wc
from lora_tags import LoraSpec, find_lora_tags, format_lora_tag, scale_lora_spec, strip_lora_tags

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
WORKFLOWS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "workflows"))
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
from helpers import (
    inject_the_machine_snapshot,
    project_controls_kf_sampler_settings,
    wait_for_file_stable,
)
from workflow_capabilities import scan_workflow_file, runtime_injection_flags
from image_prompt import (
    DEFAULT_IMAGE_TEMPLATE,
    DEFAULT_IMAGE_TEMPLATE_2CHAR,
    DEFAULT_IMAGE_TEMPLATE_CUSTOM,
    compose_image_prompt,
    compose_image_prompt_2char,
    compose_image_prompt_2char_noresolve,
    expand_inline_wildcards,
    resolve_single_pass_prompt,
    resolve_wildcards_in_dict,
)

print(WORKFLOWS_DIR)


def _console_print(message: str) -> None:
    """Print without failing on Windows charmap consoles (cp1252)."""
    try:
        print(message)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(message.encode(enc, errors="replace").decode(enc))


def merge_negatives(*parts):
    return ", ".join(p.strip() for p in parts if p and p.strip())


def _char_negative(char_obj) -> str:
    if not char_obj:
        return ""
    return str(char_obj.get("negative_prompt") or "").strip()


def _merged_negative_single(pneg, kf_negatives, character) -> str:
    kf_negatives = kf_negatives if isinstance(kf_negatives, dict) else {}
    return merge_negatives(
        pneg.get("global", ""),
        pneg.get("keyframes_all", ""),
        _char_negative(character),
        kf_negatives.get("left", ""),
    )


def _merged_negative_2char_side(pneg, kf_negatives, side_key: str, char_obj) -> str:
    kf_negatives = kf_negatives if isinstance(kf_negatives, dict) else {}
    return merge_negatives(
        pneg.get("global", ""),
        pneg.get("keyframes_all", ""),
        _char_negative(char_obj),
        kf_negatives.get(side_key, ""),
    )


def project_neg(cfg_project):
    neg = dict(cfg_project.get("negatives", {}) or {})
    if not neg.get("global"):
        legacy = cfg_project.get("negative_prompt", "")
        if legacy: neg["global"] = legacy
    for k in ("keyframes_all", "inbetween_all", "heal_all"): neg.setdefault(k, "")
    return neg

def is_flux2_workflow(wf_path):
    """Detect Flux2 workflow based on filename"""
    filename = os.path.basename(wf_path)
    return "pose_OPEN_exp" in filename

def calculate_lane_mult(text_sources: list, explicit_strengths: list, max_sat: float):
    if max_sat <= 0: return 1.0
    total = sum(explicit_strengths)
    for text in text_sources:
        if not text:
            continue
        for spec in find_lora_tags(text):
            total += spec.strength
    if total <= max_sat: return 1.0
    return max_sat / total

def _node_title(node):
    return (node.get("_meta", {}) or {}).get("title", "")


def _is_pid_running(pid: int) -> bool:
    if not pid or pid < 0: return False
    try:
        os.kill(pid, 0)
    except OSError: return False
    else: return True

def _write_status(status_path, pid: int, status: str, current_task: str = None, sub_task: str = None, error: str = None, progress_percent: float = None):
    try:
        status_data = {
            "pid": pid,
            "status": status,
            "current_task": current_task,
            "sub_task": sub_task,
            "error": error,
            "progress_percent": f"{progress_percent:.1f}" if progress_percent is not None else None,
            "last_update": datetime.now().isoformat()
        }
        temp_path = str(status_path) + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f: json.dump(status_data, f, indent=2)
        os.replace(temp_path, status_path)
    except Exception as e:
        print(f"[WARN] Failed to write status file '{status_path}': {e}")

def update_controlnet_switches(workflow: dict, switch_settings: dict):
    if not isinstance(switch_settings, dict): return
    pose_control_nodes = find_nodes_by_title(workflow, "PoseControl")
    if not pose_control_nodes: return
    _, node = pose_control_nodes[0]
    inputs = node.setdefault("inputs", {})
    for switch_key in ["switch_1", "switch_2", "switch_3"]:
        if switch_key in switch_settings:
            value = switch_settings[switch_key]
            if value in ["On", "Off"]: inputs[switch_key] = value

def new_node_id(graph):
    numeric = [int(k) for k in graph.keys() if isinstance(k, str) and k.isdigit()]
    return str(max(numeric) + 1) if numeric else str(int(time.time() * 1000) % 2_000_000_000)

def _first_node_by_title(g, title):
    for nid, node in g.items():
        if isinstance(node, dict) and node.get("_meta", {}).get("title") == title:
            return nid, node
    return None, None

def _set_image_path_on_title(g, title, image_path):
    nid, node = _first_node_by_title(g, title)
    if not node: return False
    node.setdefault("inputs", {})["image"] = image_path
    return True

def count_existing_stills(folder: str, base_prefix: str) -> int:
    if not os.path.isdir(folder): return 0
    esc = re.escape(base_prefix)
    pat = re.compile(rf"^{esc}_(\d+)_\.(png|jpg|jpeg|webp)$", re.IGNORECASE)
    indices = []
    for fn in os.listdir(folder):
        if not fn.lower().endswith(IMAGE_EXTS): continue
        m = pat.match(fn)
        if m:
            try: indices.append(int(m.group(1)))
            except: pass
    if not indices: return 0
    indices = sorted(set(indices))
    n = 0
    for i in indices:
        if i == n + 1: n += 1
        else: break
    return n

def jload(p):
    if not p: raise ValueError("jload(): path is empty")
    with open(p, "r", encoding="utf-8") as f: return json.load(f)

def get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur: return default
        cur = cur[k]
    return cur

def set_if_exists(node, input_key, value):
    if isinstance(node, dict) and "inputs" in node:
        node["inputs"][input_key] = value

def find_nodes_by_title(workflow, title):
    return wc.find_nodes_by_title(workflow, title)

def find_nodes_by_class(workflow, class_type):
    return wc.find_nodes_by_class(workflow, class_type)

def _style_line(project):
    v = project.get("style_prompt", "")
    return " ".join(v) if isinstance(v, list) else str(v).strip()

def apply_power_lora(node, lora_name, strength):
    inputs = node.setdefault("inputs", {})
    for k, v in list(inputs.items()):
        if isinstance(v, dict) and {"on", "lora", "strength"} <= set(v.keys()):
            v["on"] = False
    if not lora_name: return
    slot = inputs.setdefault("lora_1", {"on": True, "lora": "", "strength": 1.0})
    slot["on"] = True
    slot["lora"] = lora_name
    slot["strength"] = float(strength)


def update_checkpoints(workflow, model_name):
    try:
        updated_count = wc.set_checkpoint(workflow, model_name)
    except wc.WorkflowControlError as exc:
        print(f"[CHECKPOINT] FATAL: {exc}")
        raise
    if updated_count:
        print(f"[CHECKPOINT] Updated {updated_count} checkpoint node(s) -> {model_name}")
    elif model_name:
        print(f"[CHECKPOINT] WARNING: No checkpoint nodes found to update with model: {model_name}")

def update_save_paths(workflow, out_root, project_name, seq_name, id_name):
    relative_path = os.path.join(project_name, seq_name, id_name)
    base_prefix = f"{project_name}_{seq_name}_{id_name}"
    wc.set_save_image_prefix(workflow, os.path.join(relative_path, base_prefix))
    for _, node in find_nodes_by_class(workflow, "SaveImage"):
        title = (node.get("_meta", {}) or {}).get("title")
        inputs = node.setdefault("inputs", {})
        if "output_dir" in inputs: del inputs["output_dir"]
        if title == "OpenPosePreview": inputs["filename_prefix"] = os.path.join(relative_path, "openposepreview")
        elif title == "ShapePreview": inputs["filename_prefix"] = os.path.join(relative_path, "shapepreview")
        elif title == "OutlinePreview": inputs["filename_prefix"] = os.path.join(relative_path, "outlinepreview")

def override_cn_preprocessor(workflow: dict, new_preprocessor: str):
    nodes = find_nodes_by_title(workflow, "OpenPoseControl")
    for _, node in nodes: node.setdefault("inputs", {})["preprocessor"] = new_preprocessor

def update_pose_control_node(workflow: dict, settings: dict):
    if not isinstance(settings, dict): return
    nodes = find_nodes_by_title(workflow, "PoseControl")
    for _, node in nodes:
        inputs = node.setdefault("inputs", {})
        for i in range(1, 4):
            slot = settings.get(str(i))
            if not isinstance(slot, dict): continue
            if "switch" in slot and slot["switch"] in ["On", "Off"]: inputs[f"switch_{i}"] = slot["switch"]
            if "strength" in slot: 
                try: inputs[f"controlnet_strength_{i}"] = float(slot["strength"])
                except: pass
            if "start_percent" in slot:
                try: inputs[f"start_percent_{i}"] = float(slot["start_percent"])
                except: pass
            if "end_percent" in slot:
                try: inputs[f"end_percent_{i}"] = float(slot["end_percent"])
                except: pass

def inject_mask_resizer(workflow: dict):
    nodes = find_nodes_by_class(workflow, "InpaintCropImproved")
    for _, node in nodes:
        inputs = node.get("inputs", {})
        if "mask" not in inputs: continue
        target_w = inputs.get("preresize_min_width", 1024)
        target_h = inputs.get("preresize_min_height", 1024)
        original_source = inputs["mask"]

        m2i_id = new_node_id(workflow)
        workflow[m2i_id] = {"inputs": {"mask": original_source}, "class_type": "MaskToImage", "_meta": {"title": "Injected_MaskToImage"}}
        
        sc_id = new_node_id(workflow)
        workflow[sc_id] = {"inputs": {"width": target_w, "height": target_h, "upscale_method": "bilinear", "crop": "disabled", "image": [m2i_id, 0]}, "class_type": "ImageScale", "_meta": {"title": "Injected_Mask_Resizer"}}
        
        i2m_id = new_node_id(workflow)
        workflow[i2m_id] = {"inputs": {"image": [sc_id, 0], "channel": "red"}, "class_type": "ImageToMask", "_meta": {"title": "Injected_ImageToMask"}}
        
        inputs["mask"] = [i2m_id, 0]

def update_dims(workflow, width, height):
    wc.set_dimensions(workflow, int(width), int(height))


def iteration_seed(base: int, index: int, advance: int) -> int:
    """0-based image index seed; ComfyUI requires seed >= 0."""
    return max(0, int(base) + int(index) * int(advance))


def apply_generation_seed(
    workflow,
    seed,
    *,
    project_drives_sampler: bool,
    cfg=None,
    sampler_name=None,
    scheduler=None,
    steps=None,
):
    if project_drives_sampler:
        wc.set_generation_settings(
            workflow,
            seed=int(seed),
            cfg=cfg,
            sampler_name=sampler_name,
            scheduler=scheduler,
            steps=steps,
        )
    else:
        wc.set_seed(workflow, int(seed))


def update_seeds(workflow, seed, cfg=None, sampler_name=None, scheduler=None, steps=None):
    """Legacy wrapper; prefer apply_generation_seed with project_drives_sampler."""
    wc.set_generation_settings(
        workflow,
        seed=int(seed),
        cfg=cfg,
        sampler_name=sampler_name,
        scheduler=scheduler,
        steps=steps,
    )


def _snapshot_sampler_fields(
    graph: dict,
    *,
    project_driven: bool,
    steps: int | None,
    cfg: float | None,
    sampler_name: str | None,
    scheduler: str | None,
) -> tuple[Any, Any, Any, Any]:
    if project_driven:
        return steps, cfg, sampler_name, scheduler
    baked = wc.read_generation_settings_from_workflow(graph)
    return (
        baked.get("steps", steps),
        baked.get("cfg", cfg),
        baked.get("sampler_name", sampler_name),
        baked.get("scheduler", scheduler),
    )

def set_image_path_on_titled_node(graph, title, path_str):
    nodes = find_nodes_by_title(graph, title)
    if not nodes: return False
    _, node = nodes[0]
    for k in ("image", "image_path", "file", "filename", "filepath"): set_if_exists(node, k, path_str)
    return True

def run_preview_only(config_path: str, image_path: str, status_file_override: str = None):
    """
    Generates controlnet preview images (openpose, shape, outline) from an existing image.
    Uses pose_factory.json with injected LoadImage node.
    """
    script_pid = os.getpid()
    status_path = status_file_override
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        print(f"[FATAL] Cannot read config: {e}")
        sys.exit(1)
    
    project = config.get("project", {})
    api_base = project.get("comfy", {}).get("api_base", "http://127.0.0.1:8188")
    timeout_s = float(project.get("comfy", {}).get("timeout_seconds", 3600))
    out_root = project.get("comfy", {}).get("output_root", "")
    project_name = project.get("name", "__preview__")
    
    # Load pose_factory workflow
    pose_workflow_path = os.path.join(WORKFLOWS_DIR, "pose_factory.json")
    if not os.path.exists(pose_workflow_path):
        print(f"[FATAL] pose_factory.json not found at {pose_workflow_path}")
        sys.exit(1)
    
    with open(pose_workflow_path, 'r', encoding='utf-8') as f:
        graph = json.load(f)
    
    # Determine output basename from image filename
    output_basename = Path(image_path).stem
    
    # Add LoadImage node
    load_image_nid = new_node_id(graph)
    graph[load_image_nid] = {
        "inputs": {
            "image": image_path
        },
        "class_type": "LoadImage",
        "_meta": {"title": "InjectedLoadImage"}
    }
    
    # Rewire controlnet preprocessors to use LoadImage instead of generation chain
    # Node 14 (OpenPose), Node 19 (Shape), Node 22 (Outline)
    for nid in ["14", "19", "22"]:
        if nid in graph and "inputs" in graph[nid]:
            graph[nid]["inputs"]["image"] = [load_image_nid, 0]
    for seq in config.get("sequences", {}).values():
        for kf in seq.get("keyframes", {}).values():
            if kf.get("use_animal_pose"):
                override_cn_preprocessor(graph, "pose_animal")
                print("[PREVIEW] Using animal pose preprocessor")
                break
    
    # Update save paths for the controlnet outputs
    # Node 16 saves openpose to poses/, Node 21 saves shape to shapes/, Node 24 saves outline to outlines/
    for nid, folder in [("16", "poses"), ("21", "shapes"), ("24", "outlines")]:
        if nid in graph and "inputs" in graph[nid]:
            graph[nid]["inputs"]["filename_prefix"] = f"{project_name}/{folder}/{output_basename}"
    
    # Remove the main pose save node (9) - we already have the image
    if "9" in graph:
        del graph["9"]
    
    # Remove generation nodes (they'd be orphaned anyway)
    for nid in ["3", "4", "5", "7", "8", "12", "13", "25"]:
        if nid in graph:
            del graph[nid]
    
    # Remove preview nodes that referenced deleted nodes
    for nid in ["15", "20", "23"]:
        if nid in graph:
            del graph[nid]
    
    print(f"[PREVIEW] Extracting controlnet maps from: {image_path}")
    print(f"[PREVIEW] Output basename: {output_basename}")
    
    if status_path:
        _write_status(status_path, script_pid, "running", "Extracting controlnet previews...")
    
    # Queue workflow
    try:
        prompt_id = post_prompt(api_base, graph, project_name=project_name, label="id_pose_preview")
        if not prompt_id:
            print("[FATAL] Failed to queue preview workflow")
            sys.exit(1)
        
        print(f"[PREVIEW] Queued (ID: {prompt_id})")
        
        # Wait for completion
        if not wait_history_done(api_base, prompt_id, timeout_s):
            print("[FATAL] Preview extraction timed out")
            sys.exit(1)
        
        # Find output files
        out_base = Path(out_root) / project_name
        
        def find_latest(folder):
            search_dir = out_base / folder
            if not search_dir.exists():
                return None
            matches = sorted(
                [f for f in search_dir.iterdir() if f.stem.startswith(output_basename) and f.suffix.lower() in IMAGE_EXTS],
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )
            return str(matches[0]) if matches else None
        
        openpose_path = find_latest("poses")
        shape_path = find_latest("shapes")
        outline_path = find_latest("outlines")
        
        print(f"PREVIEW_POSE: {openpose_path or 'NOT_FOUND'}")
        print(f"PREVIEW_SHAPE: {shape_path or 'NOT_FOUND'}")
        print(f"PREVIEW_OUTLINE: {outline_path or 'NOT_FOUND'}")
        
        if status_path:
            _write_status(status_path, script_pid, "completed", "Preview extraction complete.", progress_percent=100.0)
        
    except Exception as e:
        print(f"[FATAL] Preview extraction failed: {e}")
        if status_path:
            _write_status(status_path, script_pid, "failed", error=str(e))
        sys.exit(1)

def post_prompt(api_base, graph, project_name=None, label=None):
    wc.strip_prompt_metadata(graph)
    extra_data = {}
    if project_name:
        extra_data["machine_ui_project"] = project_name
    if label:
        extra_data["machine_ui_label"] = label
    r = requests.post(api_base.rstrip("/") + "/prompt", json={"prompt": graph, "client_id": str(uuid.uuid4()), "extra_data": extra_data}, timeout=60)
    if not r.ok:
        print(f"[COMFY] POST /prompt failed: {r.status_code}")
        try:
            print(r.text)
        except Exception:
            pass
    r.raise_for_status()
    return r.json().get("prompt_id")

def wait_history_done(api_base, prompt_id, timeout_s=300):
    url = api_base.rstrip("/") + f"/history/{prompt_id}"
    t0 = time.time()
    while True:
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200 and prompt_id in r.json():
                # Brief settle so SaveImage can flush before we read/re-write outputs (Windows).
                time.sleep(0.5)
                return True
        except: pass
        if time.time() - t0 > timeout_s: return False
        time.sleep(1.0)

def set_text_on_titles(graph, title, text):
    wc.set_text_on_title(graph, title, text)

def resolve_image_workflow_path(id_conf: dict, imggen: dict, config_dir: str) -> str:
    p = (id_conf or {}).get("image_workflow_override_json") or (id_conf or {}).get("workflow_json") or (imggen or {}).get("image_workflow_json")
    if not p: raise ValueError("No workflow JSON set.")
    p = os.path.expanduser(p)
    if not os.path.isabs(p): p = os.path.normpath(os.path.join(WORKFLOWS_DIR, p))
    if not os.path.isfile(p): raise FileNotFoundError(f"Workflow not found: {p}")
    print(p)
    return p

def inject_metadata_png(image_path, snapshot):
    if inject_the_machine_snapshot(image_path, snapshot):
        print(f"[META] Injected snapshot into {os.path.basename(image_path)}")
    else:
        print(f"[WARN] Failed to inject metadata into {os.path.basename(image_path)}")

# ================= MAIN RUN LOGIC =================

def run(config_path, status_file_override=None):
    cfg = jload(config_path)
    config_dir = os.path.dirname(os.path.abspath(config_path))
    script_pid = os.getpid()

    project = cfg["project"]
    sequences = cfg["sequences"] # V2: Can be dict or list

    api_base = get(project, "comfy", "api_base")
    timeout_s = float(get(project, "comfy", "timeout_seconds", default=3600))
    out_root = get(project, "comfy", "output_root")
    project_name = project["name"]
    w, h = int(project["width"]), int(project["height"])

    imggen = get(project, "keyframe_generation", default={})
    base_seed = int(imggen.get("sampler_seed_start", 100000))
    advance = int(imggen.get("advance_seed_by", 17))
    default_n = int(imggen.get("image_iterations_default", 1))
    project_drives_sampler = project_controls_kf_sampler_settings(project)
    sampler_cfg = imggen.get("cfg", 6.0) if project_drives_sampler else None
    sampler_steps_base = imggen.get("steps", 30) if project_drives_sampler else None
    sampler_name = imggen.get("sampler_name", "euler") if project_drives_sampler else None
    sampler_scheduler = imggen.get("scheduler", "karras") if project_drives_sampler else None

    express_mode = bool(get(project, "inbetween_generation", "express_video", default=False))
    if project_drives_sampler and sampler_steps_base is not None:
        sampler_steps = max(12, round(sampler_steps_base / 3)) if express_mode else sampler_steps_base
    else:
        sampler_steps = None

    custom_mode = not project_controls_kf_sampler_settings(project)
    default_template = DEFAULT_IMAGE_TEMPLATE_CUSTOM if custom_mode else DEFAULT_IMAGE_TEMPLATE
    prompt_template = imggen.get("prompt_template", default_template)
    if custom_mode and "[thm.reference_prelude]" not in prompt_template:
        prompt_template = "[thm.reference_prelude]\n" + prompt_template
    prompt_template_2char = imggen.get("prompt_template_2char", DEFAULT_IMAGE_TEMPLATE_2CHAR)

    # Status Setup
    status_path = None
    if status_file_override:
        status_path = Path(status_file_override)
        if status_path.parent: status_path.parent.mkdir(parents=True, exist_ok=True)
    elif out_root and project_name:
        status_path = Path(out_root) / project_name / "_images_status.json"
        (Path(out_root) / project_name).mkdir(parents=True, exist_ok=True)
        
    if status_path: _write_status(status_path, script_pid, "running", "Initializing...", progress_percent=0.0)

    try:
        # --- V2 DATA NORMALIZATION ---
        # Convert sequences to a list of (id, object) for iteration
        if isinstance(sequences, list):
            seq_list = sequences # V1 legacy
        else:
            # Sort by 'order' field if available
            seq_list = sorted(sequences.values(), key=lambda x: x.get("order", 0))

        num_sequences = len(seq_list)

        for seq_idx, seq in enumerate(seq_list):
            seq_id = (seq.get("id") or seq.get("name") or "").strip()
            if not seq_id: continue

            # Resolve Assets
            setting_id = seq.get("setting_id")
            seq["setting_asset"] = next((i.get("prompt", "") for i in project.get("settings", []) if i.get("id") == setting_id), "")
            style_id = seq.get("style_id")
            seq["style_asset"] = next((i.get("prompt", "") for i in project.get("styles", []) if i.get("id") == style_id), "")

            seq_task_str = f"Processing sequence '{seq_id}' ({seq_idx + 1}/{num_sequences})"
            print(f"\n=== {seq_task_str} ===")
            seq_progress_base = (seq_idx / num_sequences) * 100
            if status_path: _write_status(status_path, script_pid, "running", seq_task_str, "Scanning keyframes...", progress_percent=seq_progress_base)

            # --- V2 Keyframe Access ---
            # V2: "keyframes" dict. V1: "i2v_base_images" dict.
            kfs = seq.get("keyframes") or seq.get("i2v_base_images", {})
            if not kfs: continue

            # V2: "keyframe_order" list. V1: Sort by ID.
            kf_order = seq.get("keyframe_order")
            if kf_order:
                # Filter to ensure IDs exist in the dict
                sorted_keyframes = [(kid, kfs[kid]) for kid in kf_order if kid in kfs]
            else:
                # Fallback sort
                def keyf(item):
                    digits = "".join([c for c in item[0] if c.isdigit()])
                    return int(digits) if digits else 0
                sorted_keyframes = sorted(kfs.items(), key=keyf)

            num_keyframes = len(sorted_keyframes)
            if num_keyframes == 0: continue

            proj_chars = (project.get("characters") or [])
            char_by_id = {c.get("id"): c for c in proj_chars if c.get("id")}
            char_by_name = {c.get("name", "").strip().lower(): c for c in proj_chars if c.get("name")}
            
            def get_char_obj(ref):
                if not ref: return None
                if ref in char_by_id: return char_by_id[ref]
                return char_by_name.get(ref.strip().lower())

            for kf_idx, (id_name, id_conf) in enumerate(sorted_keyframes):
                kf_sub_task_str = f"Generating '{id_name}' ({kf_idx + 1}/{num_keyframes})"
                print(f"\n--- {kf_sub_task_str} ---")
                kf_progress_base = seq_progress_base + ((kf_idx / num_keyframes) / num_sequences) * 100
                if status_path: _write_status(status_path, script_pid, "running", seq_task_str, kf_sub_task_str, progress_percent=kf_progress_base)

                try:
                    n_images = int(id_conf.get("image_iterations_override", default_n))
                    print("[ID]",id_conf)
                    print("[IMG]",imggen)
                    print("[CONFIG]",config_dir)
                    wf_path = resolve_image_workflow_path(id_conf, imggen, config_dir)
                    print("[WF]",wf_path)
                    graph = jload(wf_path)
                    wf_caps = scan_workflow_file(Path(wf_path).name)
                    inj = runtime_injection_flags(wf_caps)

                    # Detect workflow type and store on dict
                    graph['_is_flux2'] = is_flux2_workflow(wf_path)
                    if graph['_is_flux2']:
                        print("[WORKFLOW TYPE] Flux2")
                    else:
                        print("[WORKFLOW TYPE] SDXL")
                
                    inject_mask_resizer(graph)
                    if id_conf.get("use_animal_pose", True): override_cn_preprocessor(graph, "pose_animal")

                    desired_chars = [val for val in id_conf.get("characters", []) if val and isinstance(val, str)]
                    num_chars = len(desired_chars)
                    use_2char = inj.use_two_char and num_chars >= 2
                    
                    pose_path = id_conf.get("pose")
                    ref_counts = wc.apply_reference_injection(
                        graph,
                        project=project,
                        id_conf=id_conf,
                        pose_path=pose_path,
                        sequence=seq,
                        custom_mode=custom_mode,
                    )
                    wc.inject_pose_reference_flips(graph, id_conf)
                    char_ref_paths = wc.resolve_character_reference_paths(project, id_conf)
                    primary_char_path = (
                        char_ref_paths.get(None)
                        or char_ref_paths.get("1")
                        or char_ref_paths.get("left")
                        or ""
                    )
                    ref_discovery = ref_counts.get("discovery")
                    reference_prelude = ""
                    if custom_mode and ref_discovery:
                        reference_prelude = wc.compose_reference_prelude(
                            ref_discovery,
                            ref_counts.get("active_by_role") or {},
                            project=project,
                            sequence=seq,
                            id_conf=id_conf,
                            get_character=get_char_obj,
                        )
                    _console_print(
                        f"[REF] cleared={ref_counts.get('cleared', 0)} "
                        f"pose={ref_counts.get('pose', 0)} "
                        f"character={ref_counts.get('character', 0)} "
                        f"location={ref_counts.get('location', 0)} "
                        f"branch_changes={ref_counts.get('branch_changes', 0)} "
                        f"character_path={primary_char_path or '(none)'} "
                        f"location_path={ref_counts.get('location_path') or '(none)'}"
                    )
                    if reference_prelude:
                        _console_print(f"[REF] prelude:\n{reference_prelude}")

                    # --- LoRA / Prompt Logic ---
                    left_char, right_char, character = None, None, None
                    lora_by_name: dict[str, LoraSpec] = {}
                    left_p_raw, right_p_raw, heal_p_raw, simple_p_raw = "", "", "", ""

                    # Normalization
                    fg_norm = bool(get(project, "lora_normalization", "fg_enabled"))
                    fg_max = float(get(project, "lora_normalization", "fg_max", default=1.5))
                    bg_norm = bool(get(project, "lora_normalization", "bg_enabled"))
                    bg_max = float(get(project, "lora_normalization", "bg_max", default=1.5))

                    bg_src = [_style_line(project), seq.get("style_prompt",""), seq.get("style_asset",""), seq.get("setting_prompt",""), seq.get("setting_asset","")]
                    fg_src, fg_exp = [], []

                    if num_chars >= 2:
                        left_char = get_char_obj(desired_chars[0])
                        right_char = get_char_obj(desired_chars[1])
                        if left_char: fg_src.append(left_char.get("prompt","")); fg_exp.append(1.0)
                        if right_char: fg_src.append(right_char.get("prompt","")); fg_exp.append(1.0)
                    elif num_chars == 1:
                        character = get_char_obj(desired_chars[0])
                        if character: fg_src.append(character.get("prompt","")); fg_exp.append(1.0)

                    bg_mult = calculate_lane_mult(bg_src, [], bg_max) if bg_norm else 1.0
                    fg_mult = calculate_lane_mult(fg_src, fg_exp, fg_max) if fg_norm else 1.0
                    
                    if bg_mult < 1.0 or fg_mult < 1.0: print(f"[MIXER] {id_name}: BG x{bg_mult:.2f} | FG x{fg_mult:.2f}")

                    if use_2char:
                        if left_char and right_char:
                            # for t, c in (("LeftLora", left_char), ("RightLora", right_char)):
                            #     pass


                            resolved_template = expand_inline_wildcards(prompt_template_2char)
                            resolved_project = resolve_wildcards_in_dict(project)
                            resolved_seq = resolve_wildcards_in_dict(seq)
                            resolved_kf = resolve_wildcards_in_dict(id_conf)
                            resolved_left = resolve_wildcards_in_dict(left_char)
                            resolved_right = resolve_wildcards_in_dict(right_char)
                            
                            left_p_raw = compose_image_prompt_2char_noresolve(resolved_template, resolved_project, resolved_seq, resolved_kf, resolved_left, resolved_left)
                            right_p_raw = compose_image_prompt_2char_noresolve(resolved_template, resolved_project, resolved_seq, resolved_kf, resolved_right, resolved_right)
                            heal_p_raw = compose_image_prompt_2char_noresolve(resolved_template, resolved_project, resolved_seq, resolved_kf, resolved_left, resolved_right)

                            for spec in find_lora_tags(left_p_raw) + find_lora_tags(right_p_raw):
                                lora_by_name[spec.name] = spec
                    elif num_chars == 1:
                        if character:
                            # for _, n in find_nodes_by_title(graph, "LeftLora"): pass
                            simple_p_raw = compose_image_prompt(
                                prompt_template, project, seq, id_conf, character, 0,
                                reference_prelude=reference_prelude,
                            )
                            for spec in find_lora_tags(simple_p_raw):
                                lora_by_name[spec.name] = spec
                    else:
                        simple_p_raw = compose_image_prompt(
                            prompt_template, project, seq, id_conf, None, 0,
                            reference_prelude=reference_prelude,
                        )
                        for spec in find_lora_tags(simple_p_raw):
                            lora_by_name[spec.name] = spec

                    final_loras: list[LoraSpec] = []
                    if lora_by_name:
                        fg_blob = " ".join(fg_src)
                        for spec in lora_by_name.values():
                            mult = fg_mult if format_lora_tag(spec) in fg_blob else bg_mult
                            final_loras.append(scale_lora_spec(spec, mult))
                    injected_count = 0
                    if inj.use_lora and final_loras:
                        injected_count = wc.inject_loras(graph, final_loras)
                        print(
                            f"[LORAS] Injected {injected_count} LoRA node(s) "
                            f"from {len(final_loras)} project tag(s):"
                        )
                        for spec in final_loras:
                            extra = (
                                f" clip/low={spec.secondary_strength():g}"
                                if spec.strength_b is not None
                                else ""
                            )
                            print(f"  - {spec.name}: model/high={spec.strength:g}{extra}")

                    cn_settings = id_conf.get("controlnet_settings", {})
                    if inj.use_pose_cn:
                        update_pose_control_node(graph, cn_settings)
                        print(f"[CONTROLNET] Pose File: {id_conf.get('pose')}")
                        print("[CONTROLNET] Active Settings:")
                        for k, v in cn_settings.items():
                            if isinstance(v, dict) and v.get("switch") == "On":
                                print(f"  - Unit {k}: Strength {v.get('strength')} | Range {v.get('start_percent')}-{v.get('end_percent')}")



                    # --- DEBUG INSTRUMENTATION START ---
                    print(f"[DEBUG] Updating checkpoints and paths...")
                    
                    # Trace: Inject Main, Inpaint, and ControlNet models from Project JSON
                    target_inpaint = project.get("inpainting_model")
                    target_cn = project.get("controlnet_model")

                    # Standard Project Model Injection
                    update_checkpoints(graph, project.get("model"))

                    # Custom Injections based on Node Titles
                    for node_id, node in graph.items():
                        # Skip non-dict entries (like _is_flux2 metadata)
                        if not isinstance(node, dict):
                            continue
                        
                        title = node.get("_meta", {}).get("title", "")

                        # Inject Inpainting Model into node titled "Inpaint Model"
                        if title == "InpaintCheckpoint" and target_inpaint:
                            if "inputs" in node and "ckpt_name" in node["inputs"]:
                                node["inputs"]["ckpt_name"] = target_inpaint
                                print(f"[INJECT] {title} -> {target_inpaint}")

                        # Inject ControlNet Model into node titled "PoseControl"
                        if title == "PoseControl" and target_cn:
                            if "inputs" in node:
                                for i in range(1, 4):
                                    cn_key = f"controlnet_{i}"
                                    if cn_key in node["inputs"]:
                                        node["inputs"][cn_key] = target_cn
                                        print(f"[INJECT] {title} Slot {i} -> {target_cn}")



                    update_save_paths(graph, out_root, project_name, seq_id, id_name)



                    update_dims(graph, w, h)

                    # Trace: Debug Model Output before posting to API
                    print(f"\n[DEBUG] Final Workflow Models for {id_name}:")
                    print(f"  - Project Model: {project.get('model')}")
                    if target_inpaint: print(f"  - Inpaint Model: {target_inpaint}")
                    if target_cn:     print(f"  - ControlNet:    {target_cn}")

                    out_folder = os.path.join(out_root, project_name, seq_id, id_name)
                    base_filename = f"{project_name}_{seq_id}_{id_name}"
                    existing = count_existing_stills(out_folder, base_filename)
                    force = bool(id_conf.get("force_generate", False))
                    
                    print(f"[DEBUG] Status: Existing Images={existing} | Target Count={n_images} | Force Gen={force}")
                    start, end = (existing, n_images)
                    if force: start, end = (existing, existing + n_images)
                    elif existing >= n_images: 
                        start = end
                        print(f"[SKIPPED] '{id_name}': Found {existing}/{n_images} images. (Increase iterations or set force=True to override)")

                    # for i in range(start, end):
                    for iteration, i in enumerate(range(start, end)):

                        # Snapshot metadata should never reference variables
                        # that aren't assigned in this run (e.g. `hp` when use_2char is False).
                        executed_prompt = simple_p_raw

                        if use_2char:

                            if iteration > 0:
                                # Fresh wildcard resolution for new iteration
                                resolved_template = expand_inline_wildcards(prompt_template_2char)
                                resolved_project = resolve_wildcards_in_dict(project)
                                resolved_seq = resolve_wildcards_in_dict(seq)
                                resolved_kf = resolve_wildcards_in_dict(id_conf)
                                resolved_left = resolve_wildcards_in_dict(left_char)
                                resolved_right = resolve_wildcards_in_dict(right_char)
                                
                                lp = compose_image_prompt_2char_noresolve(resolved_template, resolved_project, resolved_seq, resolved_kf, resolved_left, resolved_left)
                                rp = compose_image_prompt_2char_noresolve(resolved_template, resolved_project, resolved_seq, resolved_kf, resolved_right, resolved_right)
                                hp = compose_image_prompt_2char_noresolve(resolved_template, resolved_project, resolved_seq, resolved_kf, resolved_left, resolved_right)
                            else: lp, rp, hp = left_p_raw, right_p_raw, heal_p_raw
                            
                            final_lp = strip_lora_tags(lp)
                            final_rp = strip_lora_tags(rp)
                            final_hp = strip_lora_tags(hp)

                            # Preserve the pre-strip prompt for metadata.
                            executed_prompt = hp
                            
                            set_text_on_titles(graph, "LeftPrompt", final_lp)
                            set_text_on_titles(graph, "RightPrompt", final_rp)
                            set_text_on_titles(graph, "HealPosPrompt", final_hp)
                            
                            print(f"[PROMPT] Left: {final_lp}")
                            print(f"[PROMPT] Right: {final_rp}")
                        elif inj.use_prompt:
                            sp = resolve_single_pass_prompt(
                                iteration=iteration,
                                image_index=i,
                                simple_p_raw=simple_p_raw,
                                prompt_template=prompt_template,
                                project_data=project,
                                sequence_data=seq,
                                keyframe_data=id_conf,
                                char_data=character,
                                reference_prelude=reference_prelude,
                            )
                            final_sp = strip_lora_tags(sp)

                            # Preserve the pre-strip prompt for metadata.
                            executed_prompt = sp
                            wc.set_prompt(graph, final_sp)
                            print(f"[PROMPT] {final_sp}")

                        pneg = project_neg(project)
                        kf_negatives = id_conf.get("negatives") or {}

                        if use_2char:
                            left_neg = _merged_negative_2char_side(pneg, kf_negatives, "left", left_char)
                            right_neg = _merged_negative_2char_side(pneg, kf_negatives, "right", right_char)
                            set_text_on_titles(graph, "LeftNegPrompt", left_neg)
                            print(f"[NEGATIVE LEFT] {left_neg}")
                            if left_char and _char_negative(left_char):
                                print(f"[NEGATIVE LEFT] char={_char_negative(left_char)}")
                            set_text_on_titles(graph, "RightNegPrompt", right_neg)
                            print(f"[NEGATIVE RIGHT] {right_neg}")
                            if right_char and _char_negative(right_char):
                                print(f"[NEGATIVE RIGHT] char={_char_negative(right_char)}")
                            heal_neg = merge_negatives(
                                pneg.get("global", ""),
                                pneg.get("keyframes_all", ""),
                                pneg.get("heal_all", ""),
                                kf_negatives.get("heal", ""),
                            )
                            set_text_on_titles(graph, "HealNegPrompt", heal_neg)
                            print(f"[NEGATIVE HEAL] {heal_neg}")
                        elif inj.use_single_negative:
                            neg_text = _merged_negative_single(pneg, kf_negatives, character)
                            wc.set_negative_prompt(graph, neg_text)
                            print(f"[NEGATIVE] {neg_text}")
                            if character and _char_negative(character):
                                print(f"[NEGATIVE] char={_char_negative(character)}")


                        kf_seed_override = id_conf.get("sampler_seed_start")
                        effective_base_seed = int(kf_seed_override) if kf_seed_override is not None else base_seed
                        seed = iteration_seed(effective_base_seed, i, advance)
                        print(
                            f"[GEN] Iteration {i+1} | Seed: {seed} | Base: {effective_base_seed} | "
                            f"Steps: {sampler_steps} | CFG: {sampler_cfg} | "
                            f"project_sampler={project_drives_sampler}"
                        )
                        apply_generation_seed(
                            graph,
                            seed,
                            project_drives_sampler=project_drives_sampler,
                            cfg=sampler_cfg,
                            sampler_name=sampler_name,
                            scheduler=sampler_scheduler,
                            steps=sampler_steps,
                        )
                                                
                        try:
                            print("[API BASE]",api_base)

                            # DEBUG: Save graph to file to inspect content without terminal encoding crashes
                            debug_file = os.path.join(out_folder, f"debug_graph_{id_name}_{i}.json")
                            os.makedirs(out_folder, exist_ok=True)
                            with open(debug_file, "w", encoding="utf-8") as f:
                                json.dump(graph, f, indent=2, ensure_ascii=False)
                            print(f"[DEBUG] Graph content saved for inspection: {debug_file}")

                            pid = post_prompt(api_base, graph, project_name=project_name, label=id_name)
                            print("[PID]",pid)
                            ok = wait_history_done(api_base, pid, timeout_s=timeout_s)

                            if ok:

                                snap_steps, snap_cfg, snap_sampler, snap_scheduler = _snapshot_sampler_fields(
                                    graph,
                                    project_driven=project_drives_sampler,
                                    steps=sampler_steps,
                                    cfg=sampler_cfg,
                                    sampler_name=sampler_name,
                                    scheduler=sampler_scheduler,
                                )
                                snapshot = {
                                    "item_data": id_conf,
                                    "sequence_context": {
                                        "setting_prompt": seq.get("setting_prompt"),
                                        "setting_asset": seq.get("setting_asset"),
                                        "style_prompt": seq.get("style_prompt"),
                                        "style_asset": seq.get("style_asset")
                                    },
                                    "project_context": {
                                        "style_prompt": project.get("style_prompt"),
                                        "model": project.get("model"),
                                        "width": w,
                                        "height": h,
                                        "steps": snap_steps,
                                        "cfg": snap_cfg,
                                        "sampler": snap_sampler,
                                        "scheduler": snap_scheduler,
                                        "image_model_family": project.get("image_model_family")
                                        or "default",
                                        "default_workflow_json": (
                                            project.get("default_workflow_json") or "pose_OPEN.json"
                                        ),
                                        "negatives": {
                                            "global": project.get("negatives", {}).get("global"),
                                            "keyframes_all": project.get("negatives", {}).get("keyframes_all"),
                                            "inbetween_all": project.get("negatives", {}).get("inbetween_all"),
                                            "heal_all": project.get("negatives", {}).get("heal_all")
                                        },
                                        "lora_normalization": {
                                            "fg_enabled": project.get("lora_normalization", {}).get("fg_enabled"),
                                            "fg_max": project.get("lora_normalization", {}).get("fg_max"),
                                            "bg_enabled": project.get("lora_normalization", {}).get("bg_enabled"),
                                            "bg_max": project.get("lora_normalization", {}).get("bg_max")
                                        }
                                    },
                                    "generation": {"seed": seed, "executed_prompt": executed_prompt},
                                    "meta": {"timestamp": datetime.now().isoformat()}
                                }



                                # Inject metadata into newest file
                                if os.path.isdir(out_folder):
                                    cands = [os.path.join(out_folder, f) for f in os.listdir(out_folder) if f.startswith(base_filename) and f.lower().endswith(IMAGE_EXTS)]
                                    if cands:
                                        cands.sort(key=os.path.getmtime, reverse=True)
                                        final_path = cands[0]
                                        if wait_for_file_stable(final_path, timeout_s=45.0):
                                            inject_metadata_png(final_path, snapshot)
                                            print(f"RESULT: {final_path}")
                                        else:
                                            print(
                                                f"[WARN] Output file not stable, skipping metadata inject: {final_path}"
                                            )
                                            print(f"RESULT: {final_path}")

                        except Exception as e: print(f"[ERR][a] {seq_id}/{id_name} i={i}: {e}")

                except Exception as e:
                    print(f"[ERR] {seq_id}/{id_name}: {e}")

        if status_path: _write_status(status_path, script_pid, "completed", "All sequences processed.", progress_percent=100.0)

    except Exception as e:
        print(f"[FATAL] {e}")
        if status_path: _write_status(status_path, script_pid, "failed", error=str(e))
        sys.exit(1)



if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--status-file", required=False)
    ap.add_argument("--preview-only", action="store_true", help="Extract controlnet previews from existing image")
    ap.add_argument("--image", required=False, help="Image path for --preview-only mode")
    args = ap.parse_args()
    
    if args.preview_only:
        if not args.image:
            print("[FATAL] --preview-only requires --image <path>")
            sys.exit(1)
        run_preview_only(args.config, args.image, status_file_override=args.status_file)
    else:
        run(args.config, status_file_override=args.status_file)
