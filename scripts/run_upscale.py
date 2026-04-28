# scripts/run_upscale.py
#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
import uuid
import shutil
import requests
import struct
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# --- CONFIGURATION ---
# Verify this matches your actual ComfyUI input directory
# COMFY_INPUT_DIR = "D:/ComfyUI/input" 
# UPSCALE_MODEL_NAME = "4xLSDIR.pth" 

INTERPOLATION_MULTIPLIER = 2
DEFAULT_PROJECT_FPS = 16

# Temp filename for the lossless stitch
TEMP_VIDEO_NAME = "temp_stitch_lossless.mp4"


def lossless_from_selected_video(sel_path: str) -> Optional[Path]:
    """Find matching lossless video for a compressed video path."""
    if not sel_path:
        return None
    p = Path(sel_path)
    # Try both naming patterns
    lossless = Path(str(sel_path).replace(".mp4", "_lossless.mkv"))
    if lossless.exists():
        return lossless
    return None


def copy_lossless_to_temp(lossless_path: Path, comfy_input_dir: Path, fps: float):
    """Copy lossless video to ComfyUI input directory. Returns (path, w, h)."""
    output_path = comfy_input_dir / TEMP_VIDEO_NAME
    
    # Get dimensions from lossless video
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(lossless_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        w, h = map(int, result.stdout.strip().split(","))
    except Exception as e:
        print(f"[WARN] Could not get dimensions from {lossless_path}: {e}")
        return None, 0, 0
    
    # Transcode to H.264 lossless (ComfyUI VHS_LoadVideo may not support FFV1)
    if os.path.exists(output_path):
        os.remove(output_path)
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(lossless_path),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-qp", "0",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        str(output_path)
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[LOSSLESS] Transcoded {lossless_path.name} to temp")
        return output_path, w, h
    except Exception as e:
        print(f"[ERR] Transcode failed: {e}")
        return None, 0, 0


def stitch_frames_to_lossless(frames_dir: Path, output_path: Path, fps: float = 16.0) -> bool:
    """Stitch PNG frames to lossless FFV1 video."""
    import re
    if not frames_dir.exists():
        print(f"[WARN] Frames dir not found: {frames_dir}")
        return False
    
    pngs = sorted(frames_dir.glob("*.png"))
    if not pngs:
        print(f"[WARN] No PNGs in {frames_dir}")
        return False
    
    # Detect frame pattern - handle ComfyUI naming like frame_00001_.png
    first = pngs[0].name
    m = re.match(r'^(.+_)(\d+)(_\.png)$', first)
    if not m:
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
        str(output_path)
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

    
def _update_status(status_file, data):
    """Writes status to a JSON file safely."""
    if not status_file: return
    try:
        data["last_update"] = datetime.now().isoformat()
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[WARN] Could not write status file: {e}")

def get_png_dimensions(file_path):
    """Reads the width and height from a PNG file header."""
    try:
        with open(file_path, 'rb') as f:
            data = f.read(24)
            if data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR':
                w, h = struct.unpack('>II', data[16:24])
                return w, h
    except Exception:
        pass
    return None, None

def get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def iter_video_entries_in_order(sequences_container):
    """Yields (seq_id, vid_key, vid_conf) in order."""
    
    # Normalize sequences to a list
    if isinstance(sequences_container, dict):
        # V2: dict keyed by ID, sorted by 'order'
        seq_list = sorted(sequences_container.values(), key=lambda x: x.get("order", 0))
    else:
        # V1: list
        seq_list = sequences_container

    for seq in seq_list:
        seq_id = seq.get("id") or seq.get("name")
        
        # V2 vs V1 video container
        videos = seq.get("videos") or seq.get("i2v_videos", {})
        
        # Determine order
        if "video_order" in seq:
            # V2 explicit order
            ordered_keys = seq["video_order"]
        else:
            # V1 implicit sort
            ordered_keys = sorted(videos.keys(), key=lambda k: int(''.join(filter(str.isdigit, k)) or 0))
            
        for vk in ordered_keys:
            if vk in videos:
                yield seq_id, vk, videos[vk]

def find_frames_folder(vid_folder, selected_video_path):
    """
    Determines the correct source frames folder based on the selected video path.
    """
    if not selected_video_path:
        return None
    
    try:
        fname = Path(selected_video_path).name
        # Matches _XXXXX_.mp4 or _XXXXX.mp4
        import re
        m = re.search(r'_(\d{5})_?\.mp4$', fname)
        if m:
            idx_str = m.group(1)
            target_dir = Path(vid_folder) / f"frames_{idx_str}"
            if target_dir.exists():
                return target_dir
    except Exception:
        pass
        
    return None

def stitch_source_to_temp(frames_dir, fps, comfy_input_dir):
    """Stitches specific frame folder to a LOSSLESS temp MP4. Returns (path, w, h)."""
    concat_list_path = "concat_list_upscale.txt"
    
    imgs = sorted([str(p.resolve()) for p in frames_dir.glob("*.png")])
    if not imgs:
        return None, 0, 0

    # Get dimensions from the first frame
    width, height = get_png_dimensions(imgs[0])
    if not width or not height:
        print(f"[ERR] Could not read dimensions from {imgs[0]}")
        return None, 0, 0

    with open(concat_list_path, "w", encoding="utf-8") as f:
        for img_path in imgs:
            safe_path = img_path.replace("\\", "/")
            f.write(f"file '{safe_path}'\n")
            f.write(f"duration {1.0/fps}\n")
    
    output_path = comfy_input_dir / TEMP_VIDEO_NAME
    if os.path.exists(output_path):
        os.remove(output_path)

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-qp", "0", # Lossless
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        output_path
    ]
    
    # Quiet output
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    if os.path.exists(concat_list_path):
        os.remove(concat_list_path)
        
    return output_path, width, height

def run_comfy_job(workflow_path, final_prefix, api_base, 
                  do_upscale, do_interp, target_w, target_h, source_w, source_h,
                  upscale_model, interpolation_model):
    
    with open(workflow_path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    
    nodes = {n["id"]: n for n in graph["nodes"]}
    
    nodes[1]["widgets_values"]["video"] = TEMP_VIDEO_NAME
    nodes[2]["widgets_values"][0] = upscale_model
    nodes[5]["widgets_values"][1] = INTERPOLATION_MULTIPLIER 
    nodes[6]["widgets_values"][0] = final_prefix
    nodes[4]["widgets_values"][2] = target_w
    nodes[4]["widgets_values"][3] = target_h

    current_source_id = 1 # Start at LoadVideo
    
    if do_upscale:
        nodes[3]["inputs"] = [
            {"name": "upscale_model", "type": "UPSCALE_MODEL", "link": 101}, 
            {"name": "image", "type": "IMAGE", "link": 102}
        ]
        nodes[4]["inputs"] = [{"name": "image", "type": "IMAGE", "link": 103}]
        current_source_id = 4 
        
    if do_interp:
        nodes[5]["inputs"] = [
            {"name": "frames", "type": "IMAGE", "link": 104},
            {"name": "optional_interpolation_states", "type": "INTERPOLATION_STATES", "link": None}
        ]
        current_source_id = 5

    nodes[6]["inputs"] = [{"name": "images", "type": "IMAGE", "link": 105}]

    prompt = {}
    for node in graph["nodes"]:
        nid = str(node["id"])
        if not do_upscale and nid in ["2", "3", "4"]: continue
        if not do_interp and nid == "5": continue
        
        prompt[nid] = {
            "inputs": {},
            "class_type": node["type"],
            "widgets_values": node.get("widgets_values")
        }
        inputs = prompt[nid]["inputs"]
        
        if nid == "1": # LoadVideo
            inputs["video"] = TEMP_VIDEO_NAME
            inputs["force_rate"] = 0
            inputs["force_size"] = "Disabled"
            inputs["custom_width"] = source_w
            inputs["custom_height"] = source_h
            inputs["frame_load_cap"] = 0
            inputs["skip_first_frames"] = 0
            inputs["select_every_nth"] = 1
        elif nid == "2": inputs["model_name"] = upscale_model
        elif nid == "3": 
            inputs["upscale_model"] = ["2", 0]
            inputs["image"] = ["1", 0]
        elif nid == "4": # ImageScale
            inputs["upscale_method"] = "lanczos"
            inputs["width"] = target_w
            inputs["height"] = target_h
            inputs["crop"] = "disabled"
            inputs["image"] = ["3", 0]
        elif nid == "5": # RIFE
            inputs["ckpt_name"] = interpolation_model
            inputs["clear_cache_after_n_frames"] = 10
            inputs["multiplier"] = INTERPOLATION_MULTIPLIER
            inputs["fast_mode"] = True
            inputs["ensemble"] = True
            inputs["scale_factor"] = 1.0
            inputs["frames"] = ["4", 0] if do_upscale else ["1", 0]
        elif nid == "6": # SaveImage
            inputs["filename_prefix"] = final_prefix
            if do_interp: inputs["images"] = ["5", 0]
            elif do_upscale: inputs["images"] = ["4", 0]

    print(f"[COMFY] Sending job (Upscale={do_upscale}, Interp={do_interp})...")
    p = {"prompt": prompt, "client_id": str(uuid.uuid4())}
    
    try:
        r = requests.post(api_base + "/prompt", json=p)
        r.raise_for_status()
    # except requests.exceptions.RequestException as e:
    #     print(f"[ERR] ComfyUI API Error: {e}")
    #     raise
    except requests.exceptions.RequestException as e:
        print(f"[ERR] ComfyUI API Error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"[ERR] Server Details: {e.response.text}")
        raise
        
    prompt_id = r.json().get("prompt_id")
    while True:
        r = requests.get(api_base + f"/history/{prompt_id}")
        if r.status_code == 200 and prompt_id in r.json():
            break
        time.sleep(1.0)
    print("[COMFY] Job complete.")

# def run(config_path, do_upscale, do_interp, status_file=None):
#     if not do_upscale and not do_interp:
#         print("Nothing to do. Use --upscale and/or --interpolate.")
#         return

#     with open(config_path, "r", encoding="utf-8") as f:
#         cfg = json.load(f)
    
#     project = cfg["project"]
#     api_base = project["comfy"].get("api_base", "http://127.0.0.1:8188")
#     output_root = project["comfy"]["output_root"]
#     project_name = project["name"]
    
#     target_w = int(project.get("width", 1280))
#     target_h = int(project.get("height", 720))
    
#     wf_path = os.path.join(os.path.dirname(__file__), "../workflows/upscale_video.json")
def run(config_path, do_upscale, do_interp, status_file=None):
    if not do_upscale and not do_interp:
        print("Nothing to do. Use --upscale and/or --interpolate.")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    
    project = cfg["project"]
    api_base = project["comfy"].get("api_base", "http://127.0.0.1:8188")
    output_root = project["comfy"]["output_root"]
    project_name = project["name"]
    comfy_input_dir = Path(output_root).parent / "input"
    target_w = int(project.get("width", 1280))
    target_h = int(project.get("height", 720))
    
    # Load model names from project JSON - REQUIRED, no defaults
    upscale_model = project.get("upscale_model")
    interpolation_model = project.get("interpolation_model")
    
    # Validate required models are specified
    if do_upscale and not upscale_model:
        print("[ERROR] Upscale model not specified in project JSON.")
        print("Add 'upscale_model' to config.toml [models] section, then create a new project.")
        sys.exit(1)
    
    if do_interp and not interpolation_model:
        print("[ERROR] Interpolation model not specified in project JSON.")
        print("Add 'interpolation_model' to config.toml [models] section, then create a new project.")
        sys.exit(1)
    
    print(f"[UPSCALE] Using models: upscale={upscale_model}, interpolation={interpolation_model}")
    
    wf_path = os.path.join(os.path.dirname(__file__), "../workflows/upscale_video.json")
    
    folder_tags = []
    if do_upscale: folder_tags.append("2xh")
    if do_interp: folder_tags.append("2xf")
    folder_suffix_base = "_".join(folder_tags)

    # Build list of tasks
    tasks = []
    for seq_id, vid_key, vid_conf in iter_video_entries_in_order(cfg["sequences"]):
        selected_path = vid_conf.get("selected_video_path")
        if not selected_path: continue
        
        vid_folder = os.path.join(output_root, project_name, seq_id, vid_key)
        
        # Try lossless video first
        lossless = lossless_from_selected_video(selected_path)
        frames_dir = None if lossless else find_frames_folder(vid_folder, selected_path)
        
        if not lossless and not frames_dir: 
            continue
        
        # Extract index from video filename for output folder naming
        import re
        m = re.search(r'_(\d{5})_?\.mp4$', Path(selected_path).name)
        source_idx = m.group(1) if m else "00001"
        
        output_folder_name = f"frames_{folder_suffix_base}_{source_idx}"
        output_dir = Path(vid_folder) / output_folder_name
        
        tasks.append({
            "label": f"{seq_id}/{vid_key}",
            "frames_dir": frames_dir,
            "lossless_path": lossless,
            "output_dir": output_dir,
            "output_folder_name": output_folder_name,
            "rel_prefix": f"{project_name}/{seq_id}/{vid_key}/{output_folder_name}/frame"
        })

    # --- Process Bridges ---
    sequences_container = cfg["sequences"]
    if isinstance(sequences_container, dict):
        seq_list = sorted(sequences_container.values(), key=lambda x: x.get("order", 0))
    else:
        seq_list = sequences_container

    for seq in seq_list:
        seq_id = seq.get("id") or seq.get("name")
        bridges_root = Path(output_root) / project_name / seq_id / "bridges"
        
        if bridges_root.exists():
            for bridge_dir in sorted(bridges_root.iterdir()):
                frames_dir = bridge_dir / "frames"
                if bridge_dir.is_dir() and frames_dir.exists():
                    output_folder_name = f"frames_{folder_suffix_base}"
                    output_dir = bridge_dir / output_folder_name
                    
                    tasks.append({
                        "label": f"{seq_id}/bridge/{bridge_dir.name}",
                        "frames_dir": frames_dir,
                        "output_dir": output_dir,
                        "output_folder_name": output_folder_name,
                        "rel_prefix": f"{project_name}/{seq_id}/bridges/{bridge_dir.name}/{output_folder_name}/frame"
                    })

    _update_status(status_file, {
        "status": "running", 
        "current_task": "Starting...", 
        "pid": os.getpid(), 
        "total": len(tasks), 
        "completed": 0
    })

    for i, task in enumerate(tasks):
        _update_status(status_file, {
            "status": "running", 
            "current_task": f"Processing {task['label']}", 
            "pid": os.getpid(), 
            "total": len(tasks), 
            "completed": i
        })
        
        print(f"\n>>> Processing {task['label']} -> {task['output_folder_name']}")
        
        if task['output_dir'].exists():
            shutil.rmtree(task['output_dir'])
        os.makedirs(task['output_dir'], exist_ok=True)
        
        # Try lossless video first, fall back to frames folder
        lossless = task.get('lossless_path')
        if lossless:
            stitch_path, src_w, src_h = copy_lossless_to_temp(lossless, comfy_input_dir, fps=DEFAULT_PROJECT_FPS)
        else:
            stitch_path, src_w, src_h = stitch_source_to_temp(task['frames_dir'], fps=DEFAULT_PROJECT_FPS, comfy_input_dir=comfy_input_dir)
        
        if not stitch_path:
             print("[SKIP] Stitch failed.")
             continue
        
        try:
            run_comfy_job(wf_path, task['rel_prefix'], api_base, 
                          do_upscale, do_interp, target_w, target_h, src_w, src_h,
                          upscale_model, interpolation_model)
            
            # Stitch output frames to lossless mkv
            output_dir = task['output_dir']
            # Compute FPS - base is 16, interpolation doubles it
            out_fps = DEFAULT_PROJECT_FPS * (INTERPOLATION_MULTIPLIER if do_interp else 1)
            lossless_name = f"{task['output_folder_name']}_lossless.mkv"
            lossless_out = output_dir.parent / lossless_name
            
            if stitch_frames_to_lossless(output_dir, lossless_out, fps=out_fps):
                # Remove frames folder after successful stitch
                shutil.rmtree(output_dir)
                print(f"[CLEANUP] Removed {output_dir}")
            else:
                print(f"[WARN] Keeping frames folder - lossless stitch failed")
                
        except Exception as e:
            print(f"[ERR] Job failed: {e}")
        
    # Cleanup
    # Cleanup
    temp_path = comfy_input_dir / TEMP_VIDEO_NAME
    if temp_path.exists(): os.remove(temp_path)
    
    _update_status(status_file, {
        "status": "finished", 
        "current_task": "Done", 
        "pid": os.getpid(), 
        "total": len(tasks), 
        "completed": len(tasks)
    })

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--upscale", action="store_true")
    ap.add_argument("--interpolate", action="store_true")
    ap.add_argument("--status-file", required=False)
    args = ap.parse_args()
    run(args.config, args.upscale, args.interpolate, args.status_file)