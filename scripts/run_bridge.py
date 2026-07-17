# scripts/run_bridge.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Bridge generator for ComfyUI.
Interpolates between video clips in a sequence.

Refactored for V2 Data Model.
"""

import argparse, os, re, json, time, shutil, sys
from pathlib import Path
from typing import List, Optional
import requests
from datetime import datetime
from lora_registry import LORA_REGISTRY

_LORA_RE = re.compile(r"__lora:([^:>]+):(.+?)__")

# Import settings from run_video to ensure consistency
try:
    from run_video import PRIMER_STEPS, EXPRESS_STEPS as STEPS_TEST, FULL_STEPS as STEPS_FINAL
except ImportError:
    print("[WARN] Could not import settings from run_video.py. Using defaults.")
    PRIMER_STEPS = 2
    STEPS_TEST = 6
    STEPS_FINAL = 12

COMFY_API_BASE        = "http://127.0.0.1:8188"
POLL_TIMEOUT_S        = 300
POLL_INTERVAL_S       = 2.0

def new_node_id(graph):
    numeric = [int(k) for k in graph.keys() if isinstance(k, str) and k.isdigit()]
    return str(max(numeric) + 1) if numeric else str(int(time.time() * 1000) % 2_000_000_000)

def find_nodes_by_title(graph, title):
    out = []
    for k, n in graph.items():
        if isinstance(n, dict) and n.get("_meta", {}).get("title") == title:
            out.append((k, n))
    return out

def find_nodes_by_class(graph, class_type):
    out = []
    for k, n in graph.items():
        if isinstance(n, dict) and n.get("class_type") == class_type:
            out.append((k, n))
    return out

def first_node_by_title(graph, title):
    out = find_nodes_by_title(graph, title)
    return out[0] if out else (None, None)

def set_if_exists(node, input_key, value):
    if isinstance(node, dict) and "inputs" in node:
        node["inputs"][input_key] = value

def update_video_seeds(workflow, seed):
    for _, node in find_nodes_by_class(workflow, "KSamplerAdvanced"):
        set_if_exists(node, "noise_seed", int(seed))
    for _, node in find_nodes_by_class(workflow, "KSampler"):
        set_if_exists(node, "seed", int(seed))

def inject_prompt_loras(graph: dict, lora_list: list):
    if not lora_list: return

    try:
        high_target_name = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
        high_lora_node = None
        for nid, node in find_nodes_by_class(graph, "LoraLoaderModelOnly"):
            if node.get("inputs", {}).get("lora_name") == high_target_name:
                high_lora_node = node
                break
        
        low_target_name = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
        low_lora_node = None
        for nid, node in find_nodes_by_class(graph, "LoraLoaderModelOnly"):
            if node.get("inputs", {}).get("lora_name") == low_target_name:
                low_lora_node = node
                break

        if not high_lora_node:
            print(f"[WARN] Prompt LoRA: Could not find '{high_target_name}'. Skipping injection.")
            return

        current_high_source = high_lora_node["inputs"]["model"]
        current_low_source = low_lora_node["inputs"]["model"] if low_lora_node else None

        for (lora_name, lora_strength_str) in lora_list:
            try:
                lora_strength = float(lora_strength_str)
            except ValueError:
                continue

            lora_name_clean = lora_name.strip()
            high_file = lora_name_clean
            low_file = lora_name_clean
            do_low = True

            for entry in LORA_REGISTRY:
                if lora_name_clean in entry["triggers"]:
                    high_file = entry["high"]
                    low_file = entry["low"]
                    do_low = True
                    break

            new_high_nid = new_node_id(graph)
            graph[new_high_nid] = {
                "inputs": {
                    "lora_name": high_file,
                    "strength_model": lora_strength,
                    "model": current_high_source
                },
                "class_type": "LoraLoaderModelOnly",
                "_meta": {"title": f"Injected_High_{high_file}"}
            }
            current_high_source = [new_high_nid, 0]

            if do_low and current_low_source:
                new_low_nid = new_node_id(graph)
                graph[new_low_nid] = {
                    "inputs": {
                        "lora_name": low_file,
                        "strength_model": lora_strength,
                        "model": current_low_source
                    },
                    "class_type": "LoraLoaderModelOnly",
                    "_meta": {"title": f"Injected_Low_{low_file}"}
                }
                current_low_source = [new_low_nid, 0]
            
            print(f"[INJECT] LoRA '{lora_name_clean}' -> High: {high_file} | Low: {low_file if do_low else '(skipped)'}")

        high_lora_node["inputs"]["model"] = current_high_source
        if low_lora_node and current_low_source:
            low_lora_node["inputs"]["model"] = current_low_source

    except Exception as e:
        print(f"[WARN] Failed to inject prompt LoRAs: {e}")

def get_bridge_params(lock_request: int) -> tuple[int, int, int]:
    if lock_request == 2:
        return 4, 5, 13
    elif lock_request == 3:
        return 7, 7, 17
    elif lock_request == 4:
        return 8, 9, 21
    else: 
        return 9, 11, 25
    
def _get_bridge_folder(data: dict, seq_index: int, kf_id: str) -> Path | None:
    try:
        seqs = data.get("sequences")
        if isinstance(seqs, list):
            seq = seqs[seq_index]
        elif isinstance(seqs, dict):
            sorted_seqs = sorted(seqs.values(), key=lambda x: x.get("order", 0))
            if 0 <= seq_index < len(sorted_seqs):
                seq = sorted_seqs[seq_index]
            else: return None
        else: return None

        i2v = seq.get("videos") or seq.get("i2v_videos", {})
        
        vid_b_key = next((k for k, v in i2v.items() if v.get("start_keyframe_id") == kf_id or v.get("start_id") == kf_id), None)
        if not vid_b_key: return None

        if "video_order" in seq:
            ordered_keys = seq["video_order"]
        else:
            ordered_keys = sorted(i2v.keys(), key=lambda k: int(re.search(r'\d+', k).group()) if re.search(r'\d+', k) else 0)
            
        try:
            b_idx = ordered_keys.index(vid_b_key)
            if b_idx == 0: return None
            vid_a_key = ordered_keys[b_idx - 1]
        except ValueError: return None

        proj = data.get("project", {})
        root = proj.get("comfy", {}).get("output_root")
        pname = proj.get("name")
        sid = seq.get("id")
        
        if not (root and pname and sid): return None
        bridges_root = Path(root) / pname / sid / "bridges"
        if not bridges_root.exists(): return None

        base = f"{vid_a_key}_{vid_b_key}"
        candidates = list(bridges_root.glob(f"{base}_*"))
        
        if candidates:
            candidates.sort(reverse=True)
            frames_dir = candidates[0] / "frames"
            return frames_dir if frames_dir.exists() else None
            
        return None
    except Exception:
        return None

def _is_pid_running(pid: int) -> bool:
    if not pid or pid < 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True

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
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(status_data, f, indent=2)
        os.replace(temp_path, status_path)
    except Exception as e:
        print(f"[WARN] Failed to write status file '{status_path}': {e}")

def _node_title(node):
    return (node.get("_meta", {}) or {}).get("title", "")

def _set_seed_fields(inputs, seed, force_disable_random=True):
    for k in ("noise_seed", "seed", "rand_seed", "motion_seed", "flow_seed"):
        if k in inputs:
            inputs[k] = int(seed)
    if force_disable_random:
        for k in ("randomize","rand","use_random_seed","random_seed","noise_seed_randomize"):
            if k in inputs:
                inputs[k] = False

def set_seed_on_samplers(graph, seed):
    count = 0
    for _, node in find_nodes_by_class(graph, "KSampler") + find_nodes_by_class(graph, "KSamplerAdvanced"):
        _set_seed_fields(node.setdefault("inputs", {}), seed)
        count += 1
    return count

def roundup_to_div16(x):
    x = int(x)
    return (x + 15) // 16 * 16

def list_pngs(folder: Path):
    return sorted([p for p in folder.glob("*.png")])

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def zero_copy(src: Path, dst: Path):
    try:
        if dst.exists():
            dst.unlink() 
        os.link(src, dst)
    except Exception:
        shutil.copy2(src, dst)

def frames_dir_from_selected_video_path(sel_path: str) -> Path:
    sp = sel_path.replace("/", "\\")
    m = re.search(r"^(.*)\\[^\\]+_(\d+)_\.mp4$", sp)
    if not m:
        return Path(os.path.dirname(sp)) / "frames"
    base_dir = m.group(1)
    idx = m.group(2)
    return Path(base_dir) / f"frames_{idx}"

def seq_output_frames_dir(seq_root: Path) -> Path:
    return seq_root / "frames"

def bridges_root_for_seq(seq_root: Path) -> Path:
    return seq_root / "bridges"

def pair_bridge_dir(seq_root: Path, a_name: str, b_name: str) -> Path:
    return bridges_root_for_seq(seq_root) / f"{a_name}_{b_name}"

def wait_for_expected_pngs(folder: Path, expected: int, timeout_s: int) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if len(list_pngs(folder)) >= expected:
            return True
        time.sleep(2)
    return False

def find_latest_bridge_dir(bridges_root: Path, a_name: str, b_name: str) -> Optional[Path]:
    base_name = f"{a_name}_{b_name}"
    candidates = sorted(
        [p for p in bridges_root.glob(f"{base_name}_*") if p.is_dir() and p.name.split('_')[-1].isdigit()],
        reverse=True
    )
    return candidates[0] if candidates else None

def _copy_and_rename(src_paths: List[Path], dest_dir: Path, start_index: int) -> int:
    frame_counter = start_index
    for src_path in src_paths:
        dest_path = dest_dir / f"frame_{frame_counter:06d}{src_path.suffix}"
        zero_copy(src_path, dest_path)
        frame_counter += 1
    return frame_counter

def next_versioned_pair_dir(base: Path) -> Path:
    base = Path(base)
    i = 1
    while True:
        cand = base.with_name(f"{base.name}_{i:05d}")
        if not cand.exists():
            return cand
        i += 1

# =======================
# Comfy helpers
# =======================
def comfy_post_prompt(graph):
    r = requests.post(f"{COMFY_API_BASE}/prompt", json={"prompt": graph}, timeout=60)
    r.raise_for_status()
    return r.json()["prompt_id"]

def _as_bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")

def comfy_wait_done(prompt_id, timeout_s=POLL_TIMEOUT_S):
    url = f"{COMFY_API_BASE}/history/{prompt_id}"
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and prompt_id in data:
                return True 
        time.sleep(POLL_INTERVAL_S)
    return False 

def assemble_sequence_frames(seq_obj: dict, project_root: Path, project_cfg: dict):
    print(f"\n[ASSEMBLY] Assembling final frames for sequence '{seq_obj.get('id', 'seq')}'...")
    if sys.stdout: sys.stdout.flush()
    
    i2v = seq_obj.get("videos") or seq_obj.get("i2v_videos", {})
    if not i2v:
        print("[ASSEMBLY] No videos found. Skipping.")
        return

    if "video_order" in seq_obj:
        ordered_vid_keys = seq_obj["video_order"]
    else:
        ordered_vid_keys = sorted(i2v.keys(), key=lambda k: int(re.sub(r"\D", "", k) or 0))
    
    vids = []
    for k in ordered_vid_keys:
        if k in i2v and i2v[k].get("selected_video_path"):
            vids.append((k, i2v[k]["selected_video_path"]))

    if not vids:
        print("[ASSEMBLY] No 'selected_video_path' entries found. Skipping.")
        return

    all_keyframes = seq_obj.get("keyframes") or seq_obj.get("i2v_base_images", {})
    
    window_sizes = [] 
    _lvl, default_window, _out = get_bridge_params(2) 

    for pair_idx in range(len(vids) - 1):
        b_key, _ = vids[pair_idx + 1]
        lock_request = 1 
        b_start_kf_id = i2v[b_key].get("start_keyframe_id") or i2v[b_key].get("start_id")
        
        if b_start_kf_id and b_start_kf_id in all_keyframes:
            keyframe_obj = all_keyframes[b_start_kf_id]
            lock_request = keyframe_obj.get("join_smoothing_level", 1)
            try: lock_request = int(lock_request)
            except Exception: lock_request = 1
        
        if lock_request <= 1:
            window_sizes.append(default_window)
        else:
            _lvl, sample_frames, _out = get_bridge_params(lock_request)
            window_sizes.append(sample_frames)

    vid_frame_dirs = [frames_dir_from_selected_video_path(sel) for k, sel in vids]
    seq_root = vid_frame_dirs[0].parents[1] 
    dest_dir = seq_root / "frames"
    
    if dest_dir.exists():
        print(f"[ASSEMBLY] Cleaning destination: {dest_dir}")
        shutil.rmtree(dest_dir)
    ensure_dir(dest_dir)

    bridges_root = bridges_root_for_seq(seq_root)
    bridge_frame_dirs = []
    for pair_idx in range(len(vids) - 1):
        a_key, _ = vids[pair_idx]
        b_key, _ = vids[pair_idx + 1]
        latest_bridge_root = find_latest_bridge_dir(bridges_root, a_key, b_key)
        
        if latest_bridge_root:
            bridge_frames_path = latest_bridge_root / "frames"
            if bridge_frames_path.exists():
                bridge_frame_dirs.append(bridge_frames_path)
            else:
                print(f"[ASSEMBLY] [WARN] Bridge dir found but 'frames' subfolder missing: {bridge_frames_path}")
                bridge_frame_dirs.append(None)
        else:
            print(f"[ASSEMBLY] [WARN] No bridge found for {a_key}->{b_key}. Sequence will have a hard cut.")
            bridge_frame_dirs.append(None)

    frame_counter = 0
    first_vid_frames = list_pngs(vid_frame_dirs[0])
    current_window_size = window_sizes[0] if window_sizes else default_window
    frames_to_copy = first_vid_frames[:-current_window_size]
    frame_counter = _copy_and_rename(frames_to_copy, dest_dir, frame_counter)
    print(f"[ASSEMBLY] Copied {len(frames_to_copy)} frames from '{vid_frame_dirs[0].parent.name}'")

    for i in range(len(bridge_frame_dirs)):
        bridge_dir = bridge_frame_dirs[i]
        vid_dir = vid_frame_dirs[i + 1] 

        if bridge_dir and bridge_dir.exists():
            bridge_frames = list_pngs(bridge_dir)
            frame_counter = _copy_and_rename(bridge_frames, dest_dir, frame_counter)
            print(f"[ASSEMBLY] Copied {len(bridge_frames)} frames from bridge '{bridge_dir.parent.name}'")
        else:
            print(f"[ASSEMBLY] [WARN] Skipping bridge {i} (missing).")

        vid_frames = list_pngs(vid_dir)
        is_last_video = (i == len(bridge_frame_dirs) - 1)

        start_window = window_sizes[i] if i < len(window_sizes) else default_window

        if is_last_video:
            frames_to_copy = vid_frames[start_window:]
        else:
            end_window = window_sizes[i + 1] if (i + 1) < len(window_sizes) else default_window
            if start_window + end_window > len(vid_frames):
                print(f"[ASSEMBLY] [WARN] Video {vid_dir.parent.name} is shorter than its bridge windows. May result in empty clip.")
                frames_to_copy = []
            else:
                frames_to_copy = vid_frames[start_window:-end_window]
        
        frame_counter = _copy_and_rename(frames_to_copy, dest_dir, frame_counter)
        print(f"[ASSEMBLY] Copied {len(frames_to_copy)} frames from '{vid_dir.parent.name}'")

    print(f"[ASSEMBLY] Done. Assembled {frame_counter} total frames in {dest_dir}")


def compute_lock_positions(head_n: int, tail_n: int, total_frames: int):
    L = int(total_frames)
    if L < 2: return [0] * (head_n + tail_n)
    head = list(range(head_n))
    tail = list(range(L - tail_n, L))
    print(f"head {head} tail {tail}")
    return head + tail

def inject_slowmo_fix(graph: dict, target_steps: int, primer_steps: int, primer_cfg: float, main_cfg: float):
    try:
        iter_nid, iter_node = first_node_by_title(graph, "IterKSampler")
        fixed_nid, fixed_node = first_node_by_title(graph, "WanFixedSeed")
        steps_nid, _ = first_node_by_title(graph, "Steps (Final)")
        if not (iter_node and fixed_node and steps_nid): return

        orig_latent = iter_node["inputs"]["latent_image"]
        lora_model_nid = iter_node["inputs"]["model"][0]
        base_model_nid = graph[lora_model_nid]["inputs"]["model"][0]
        shift_val = graph[lora_model_nid]["inputs"].get("shift", 5.0)

        base_samp_nid = new_node_id(graph)
        graph[base_samp_nid] = {"inputs": {"shift": shift_val, "model": graph[base_model_nid]["inputs"]["model"]}, "class_type": "ModelSamplingSD3", "_meta": {"title": "Injected_Base_Samp"}}

        fix_nid = new_node_id(graph)
        graph[fix_nid] = {
            "inputs": {
                "add_noise": "enable", "noise_seed": iter_node["inputs"].get("noise_seed", 0),
                "steps": target_steps, "cfg": primer_cfg, "sampler_name": iter_node["inputs"].get("sampler_name", "euler"),
                "scheduler": iter_node["inputs"].get("scheduler", "simple"), "start_at_step": 0, "end_at_step": primer_steps,
                "return_with_leftover_noise": "enable", "model": [base_samp_nid, 0],
                "positive": iter_node["inputs"]["positive"], "negative": iter_node["inputs"]["negative"], "latent_image": orig_latent
            },
            "class_type": "KSamplerAdvanced", "_meta": {"title": "Injected_SlowMo_Fix"}
        }

        mid = ((target_steps - primer_steps) // 2) + primer_steps
        iter_node["inputs"]["latent_image"] = [fix_nid, 0]
        iter_node["inputs"]["start_at_step"] = primer_steps
        iter_node["inputs"]["end_at_step"] = mid
        iter_node["inputs"]["cfg"] = main_cfg
        iter_node["inputs"]["add_noise"] = "disable"
        
        fixed_node["inputs"]["start_at_step"] = mid
        fixed_node["inputs"]["end_at_step"] = target_steps
        fixed_node["inputs"]["cfg"] = main_cfg
        
        print(f"[INJECT] Injected SlowMo Fix (Primer: 0-{primer_steps} @ {primer_cfg}, Main: {primer_steps}-{target_steps} @ {main_cfg})")
    except Exception as e: print(f"[WARN] Failed to inject slow-mo fix: {e}")

def patch_bridge_graph(
    graph: dict,
    prompt: str,
    neg_prompt: str,
    refs_dir: Path,
    width: int,
    height: int,
    total_frames: int,
    steps_test: int,
    steps_final: int,
    seed: int,
    out_prefix: Path,
    save_video_prefix: Path,
    head_n: int,
    tail_n: int,
    express: bool,
):
    out_len = int(total_frames) 
    positions = compute_lock_positions(head_n, tail_n, out_len)
    ref_pos_str = ",".join(str(p) for p in positions)

    for nid, node in graph.items():
        if node.get("class_type") == "WanMultiFrameRefToVideo":
            inp = node.setdefault("inputs", {})
            inp["width"] = int(width)
            inp["height"] = int(height)
            inp["length"] = out_len                 
            inp["ref_positions"] = ref_pos_str
            print(f"[bridge] inject: length={out_len} (={total_frames}×2)")
            print(f"[bridge] inject: ref_positions={ref_pos_str}")
            if sys.stdout: sys.stdout.flush()

    for nid, node in graph.items():
        if node.get("class_type") == "LoadImagesFromFolderKJ":
            inp = node.setdefault("inputs", {})
            inp["folder"] = str(refs_dir)
            inp["width"] = int(width)
            inp["height"] = int(height)
    print(f"[bridge] inject: refs_dir={refs_dir}, w={width}, h={height}")

    steps_value = int(steps_test if express else steps_final)
    for nid, node in find_nodes_by_class(graph, "KSamplerAdvanced"):
        node.setdefault("inputs", {})["steps"] = steps_value

    for nid, node in graph.items():
        if node.get("class_type") == "SaveImage":
            inp = node.setdefault("inputs", {})
            inp["filename_prefix"] = str(out_prefix)
            
    print(f"[bridge] inject: out_prefix={out_prefix}")
    for nid, node in graph.items():
        if node.get("class_type") == "SaveVideo":
            inp = node.setdefault("inputs", {})
            inp["filename_prefix"] = str(save_video_prefix)

    print(f"[bridge] inject: video_prefix={save_video_prefix}")

    prompt_injected = False
    for nid, node in find_nodes_by_title(graph, "PosPrompt"):
        if node.get("class_type") == "CLIPTextEncode":
            inp = node.setdefault("inputs", {})
            inp["text"] = prompt
            print(f"[bridge] inject: PosPrompt='{prompt[:70]}...'")
            prompt_injected = True
            break 
    if not prompt_injected:
        print("[bridge] [WARN] Could not find 'PosPrompt' node to inject prompt.")

    neg_injected = False
    for nid, node in find_nodes_by_title(graph, "NegPrompt"):
        if node.get("class_type") == "CLIPTextEncode":
            inp = node.setdefault("inputs", {})
            inp["text"] = neg_prompt
            print(f"[bridge] inject: NegPrompt='{neg_prompt[:70]}...'")
            neg_injected = True
            break
    if not neg_injected:
        print("[bridge] [WARN] Could not find 'NegPrompt' node to inject negative prompt.")

    print(f"[bridge] inject: steps mode={'express' if express else 'standard'} steps={steps_value}")
    print(f"[bridge] inject: w={width} h={height} frames_in={total_frames} frames_out={out_len} seed={seed} express={express}")

def load_bridge_workflow_path(vg, project_root: Path):
    script_dir = Path(__file__).resolve().parent
    return script_dir.parent / "workflows" / "i2v_bridge.json"

def process_sequence(
    seq_obj: dict, 
    project_root: Path, 
    project_cfg: dict, 
    status_path: Path = None, 
    script_pid: int = None, 
    progress_base: float = 0.0, 
    progress_chunk: float = 100.0
):
    seq_id = seq_obj.get("id", "seq")
    
    # V2 Access
    i2v = seq_obj.get("videos") or seq_obj.get("i2v_videos", {})
    if not i2v:
        return

    proj_data = project_cfg.get("project", project_cfg)
    vg = proj_data.get("inbetween_generation", {}) or {}
    seed_start = int(vg.get("seed_start", 500000))
    seed_step  = int(vg.get("advance_seed_by", 13))

    full_w = int(proj_data.get("width", 1280))
    full_h = int(proj_data.get("height", 720))

    w = roundup_to_div16(full_w)
    h = roundup_to_div16(full_h)

    bridge_wf_path = load_bridge_workflow_path(vg, project_root)

    # V2 Ordering
    if "video_order" in seq_obj:
        ordered_vid_keys = seq_obj["video_order"]
    else:
        ordered_vid_keys = sorted(i2v.keys(), key=lambda k: int(re.sub(r"\D", "", k) or 0))
    
    vids = []
    for k in ordered_vid_keys:
        if k in i2v and i2v[k].get("selected_video_path"):
            vids.append((k, i2v[k]["selected_video_path"]))

    total_pairs = max(0, len(vids) - 1)

    for pair_idx in range(total_pairs):
        a_key, a_sel = vids[pair_idx]
        b_key, b_sel = vids[pair_idx + 1]

        task_msg = f"Processing sequence '{seq_id}'"
        sub_task = f"Bridge {pair_idx+1}/{total_pairs}: {a_key}->{b_key}"
        current_prog = progress_base + (pair_idx / total_pairs) * progress_chunk
        
        if status_path:
            _write_status(status_path, script_pid, "running", task_msg, sub_task, progress_percent=current_prog)

        lock_request = 1 
        offset_request = -2 
        full_prompt = "" 

        proj_negs = project_cfg.get("project", {}).get("negatives", {})
        neg_parts = [proj_negs.get("global", ""), proj_negs.get("inbetween_all", "")]
        
        # V2 Keyframes
        all_keyframes = seq_obj.get("keyframes") or seq_obj.get("i2v_base_images", {})
        
        b_start_kf_id = i2v[b_key].get("start_keyframe_id") or i2v[b_key].get("start_id")
        
        kf_layout_prompt = ""

        if b_start_kf_id and b_start_kf_id in all_keyframes:
            keyframe_obj = all_keyframes[b_start_kf_id]
            lock_request = int(keyframe_obj.get("join_smoothing_level", 1))
            offset_request = int(keyframe_obj.get("join_offset", 0))
            kf_layout_prompt = keyframe_obj.get("layout", "")
            
            neg_parts.append(keyframe_obj.get("negatives", {}).get("heal", ""))
        
        setting_id = seq_obj.get("setting_id")
        setting_asset = next((i.get("prompt", "") for i in proj_data.get("settings", []) if i.get("id") == setting_id), "")
        
        style_id = seq_obj.get("style_id")
        style_asset = next((i.get("prompt", "") for i in proj_data.get("styles", []) if i.get("id") == style_id), "")

        proj_style_prompt = proj_data.get("style_prompt", "")
        seq_style_prompt = seq_obj.get("style_prompt", "")
        seq_setting_prompt = seq_obj.get("setting_prompt", "")
        seq_action_prompt = seq_obj.get("action_prompt", "")
        
        full_prompt = " ".join(filter(None, [
            seq_action_prompt, 
            kf_layout_prompt,
            setting_asset,
            seq_setting_prompt,
            style_asset,
            seq_style_prompt, 
            proj_style_prompt
        ])).strip()

        full_neg = ", ".join([s.strip() for s in neg_parts if s.strip()])

        if lock_request <= 1:
            print(f"[bridge] Skipping {a_key}->{b_key}: join_smoothing_level is {lock_request} (<= 1).")
            if sys.stdout: sys.stdout.flush()
            continue 

        LOCK_N, BRIDGE_SAMPLE_FRAMES, BRIDGE_OUTPUT_FRAMES = get_bridge_params(lock_request)
        
        safe_limit = max(0, BRIDGE_SAMPLE_FRAMES - LOCK_N)
        final_offset = max(-safe_limit, min(safe_limit, offset_request))
        
        len_a_tail = BRIDGE_SAMPLE_FRAMES - final_offset
        len_b_head = BRIDGE_SAMPLE_FRAMES + final_offset
        
        print(f"[bridge] {a_key}->{b_key}: Level={lock_request}, Offset={final_offset}")
        if sys.stdout: sys.stdout.flush()

        a_frames = frames_dir_from_selected_video_path(a_sel)
        b_frames = frames_dir_from_selected_video_path(b_sel)

        seq_root = Path(a_sel).parents[1]   
        base_pair_dir = pair_bridge_dir(seq_root, a_key, b_key)
        pair_dir = next_versioned_pair_dir(base_pair_dir)
        tmp_dir  = pair_dir / "tmp"
        refs_dir = tmp_dir / "refs"
        out_dir  = pair_dir / "frames"
        ensure_dir(refs_dir)
        ensure_dir(out_dir)

        a_pngs = list_pngs(a_frames)
        b_pngs = list_pngs(b_frames)

        a_slice_start = max(0, len(a_pngs) - len_a_tail)
        a_bridge_tail = a_pngs[a_slice_start:]
        
        b_slice_end = min(len(b_pngs), len_b_head)
        b_bridge_head = b_pngs[:b_slice_end]

        firstA = a_bridge_tail[:LOCK_N]
        lastB  = b_bridge_head[-LOCK_N:]

        extra_frame_gen = 0
        if len(b_pngs) > b_slice_end:
            lastB.append(b_pngs[b_slice_end])
            extra_frame_gen = 1

        for i, p in enumerate(firstA + lastB, start=0):
            zero_copy(p, refs_dir / f"ref_{i:02d}{p.suffix}")

        head_n = len(firstA)
        tail_n = len(lastB)

        graph = json.loads(Path(bridge_wf_path).read_text(encoding="utf-8"))
        if "nodes" in graph and isinstance(graph["nodes"], list):
            api = {}
            for n in graph["nodes"]:
                api[str(n["id"])] = {k: n[k] for k in n if k != "id"}
            graph = api

        seed = seed_start + pair_idx * seed_step

        express = _as_bool(vg.get("express_video", False))
        quarter_size_video = _as_bool(vg.get("quarter_size_video", False))
        
        fix_slowmo = _as_bool(vg.get("fix_slowmo", True)) 
        primer_cfg = float(vg.get("fix_slowmo_primer_cfg", 2.5))
        main_cfg = float(vg.get("fix_slowmo_main_cfg", 1.0))

        if quarter_size_video:
            w_gen, h_gen = roundup_to_div16(full_w // 2), roundup_to_div16(full_h // 2)
        else:
            w_gen, h_gen = w, h

        s_test = STEPS_TEST + (PRIMER_STEPS if fix_slowmo else 0)
        s_final = STEPS_FINAL + (PRIMER_STEPS if fix_slowmo else 0)

        parsed_loras = _LORA_RE.findall(full_prompt)
        full_prompt_clean = _LORA_RE.sub("", full_prompt).strip()

        patch_bridge_graph(
            graph,
            prompt=full_prompt_clean,
            neg_prompt=full_neg,
            refs_dir=refs_dir,
            width=w_gen,
            height=h_gen,
            total_frames=BRIDGE_OUTPUT_FRAMES + extra_frame_gen,
            steps_test=s_test,
            steps_final=s_final,
            seed=seed,
            out_prefix=out_dir / "frame",
            save_video_prefix=pair_dir / "preview",
            head_n=head_n,
            tail_n=tail_n,
            express=express
        )
        
        if parsed_loras:
            print(f"  Parsed {len(parsed_loras)} LoRA tags from prompt.")
            parsed_loras.reverse()
            inject_prompt_loras(graph, parsed_loras)
        
        update_video_seeds(graph, seed)

        if fix_slowmo:
            target_steps = s_test if express else s_final
            inject_slowmo_fix(graph, target_steps, PRIMER_STEPS, primer_cfg, main_cfg)

        print(f"[bridge] {seq_id} {a_key}->{b_key}: frames={BRIDGE_OUTPUT_FRAMES + extra_frame_gen} lock={LOCK_N} (+{extra_frame_gen}) seed={seed} {w_gen}x{h_gen}")

        try:
            pid = comfy_post_prompt(graph)
            ok  = comfy_wait_done(pid, timeout_s=POLL_TIMEOUT_S)
            print(f"[bridge] {a_key}->{b_key} -> {'OK' if ok else 'TIMEOUT/ERROR'}")
            
            if sys.stdout: sys.stdout.flush()
            
            if ok and status_path:
                done_prog = progress_base + ((pair_idx + 1) / total_pairs) * progress_chunk
                _write_status(status_path, script_pid, "running", task_msg, sub_task, progress_percent=done_prog)

        except Exception as e:
            print(f"[ERR] Bridge generation failed: {e}")
            if status_path:
                 _write_status(status_path, script_pid, "failed", task_msg, f"ComfyUI Error: {e}", error=str(e))

    # After bridge gen, run assembly
    assemble_sequence_frames(seq_obj, project_root, project_cfg)

def run(config_path, status_file_override=None):
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    script_pid = os.getpid()
    
    project = cfg.get("project", {})
    out_root = project.get("comfy", {}).get("output_root")
    project_name = project.get("name")
    
    status_path = None
    if status_file_override:
        status_path = Path(status_file_override)
        if status_path.parent: status_path.parent.mkdir(parents=True, exist_ok=True)
    elif out_root and project_name:
        status_path = Path(out_root) / project_name / "_bridge_status.json"
        if status_path.parent: status_path.parent.mkdir(parents=True, exist_ok=True)

    if status_path and status_path.exists():
        try:
            with open(status_path, 'r', encoding='utf-8') as f:
                old_status = json.load(f)
            if old_status.get("status") == "running" and _is_pid_running(old_status.get("pid")):
                print(f"[ERROR] Another bridge process is running.")
                sys.exit(1)
        except Exception: pass

    if status_path:
        _write_status(status_path, script_pid, "running", "Initializing...", progress_percent=0.0)

    # try:
    #     # V2 Data Handling: Dict vs List
    #     sequences_raw = cfg.get("sequences")
    #     if isinstance(sequences_raw, dict):
    #         sequences = sorted(sequences.values(), key=lambda x: x.get("order", 0))
    #     else:
    #         sequences = sequences_raw or []
            
    #     total_seqs = len(sequences)
    try:
        # V2 Data Handling: Dict vs List
        sequences_raw = cfg.get("sequences")
        sequences = []
        
        if isinstance(sequences_raw, dict):
            sequences = sorted(sequences_raw.values(), key=lambda x: x.get("order", 0))
        elif isinstance(sequences_raw, list):
            sequences = sequences_raw
            
        total_seqs = len(sequences)
        
        for i, seq in enumerate(sequences):
            base_prog = (i / total_seqs) * 100
            chunk = (1.0 / total_seqs) * 100
            process_sequence(seq, Path(config_path).parent, cfg, status_path, script_pid, base_prog, chunk)

        if status_path:
            _write_status(status_path, script_pid, "completed", "Bridge generation complete.", progress_percent=100.0)
            
    except Exception as e:
        print(f"[FATAL] Error: {e}")
        if status_path:
            _write_status(status_path, script_pid, "failed", "Fatal Error", error=str(e))
        sys.exit(1)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Path to project JSON")
    ap.add_argument("--status-file", required=False, help="Optional path to write status updates")
    args = ap.parse_args()
    print("running")
    run(args.config, status_file_override=args.status_file)