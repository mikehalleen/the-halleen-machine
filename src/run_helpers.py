# run_helpers.py
import gradio as gr
import subprocess
import os
import shutil
import json
import requests
from pathlib import Path
from typing import List, Dict, Any, Tuple
from datetime import datetime
import sys
import copy
import re
import random
import threading
from platform_helpers import ProcessManager, PathHelper, ComfyUIManager, IS_WINDOWS

# --- CONFIGURATION ---
SCRIPT_DIRECTORY = str(Path(__file__).parent / "../scripts")
WORKFLOWS_DIR = Path(__file__).parent / "../workflows"
JOYCAPTION_PYTHON = r"D:\joycaption\joycaption-venv\Scripts\python.exe"

# --- Helper Imports ---
from helpers import (
    ensure_settings,
    _ensure_project, _ensure_seq_defaults, _derive_videos_for_seq, 
    _video_seconds, _fmt_clock, _rows_with_times,
    parse_nid, cb_save_project, _get_temp_dir,
    get_node_by_id,
    get_project_poses_dir, save_to_project_folder
)



SCRIPT_DIRECTORY = os.path.join(os.path.dirname(__file__), "../scripts")



from test_gen_helpers import _create_temp_json_for_sequence_batch, run_pose_preview_task

def _format_status_file(file_path: Path, title: str):
    """Helper to read and format a single JSON status file."""
    if not file_path.exists():
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        parts = [f"--- {title} Status ---"]
        if isinstance(data, dict):
            if "status" in data: parts.append(f"Status: {data['status']}")
            if "last_update" in data: parts.append(f"Last Update: {data['last_update']}")
            if "current_task" in data: parts.append(f"Task: {data['current_task']}")
            for key, value in data.items():
                if key not in ["status", "last_update", "current_task"]:
                    parts.append(f"{key.replace('_', ' ').title()}: {value}")
        else:
            parts.append(f"Invalid status file format: {file_path.name}")
        return "\n".join(parts)
    except Exception as e:
        return f"Error reading {file_path.name}: {e}"


def handle_upscale_batch(filepath: str, project_dict: dict, do_upscale: bool, do_interp: bool, sequence_id=None):
    """Constructs and runs the command for the Upscale/Enhance batch."""
    if not filepath: return "Error: No project file loaded."
    if not do_upscale and not do_interp: return "Error: Select at least one option."

    # If sequence_id provided, create temp JSON for that sequence only
    if sequence_id:
        full = project_dict if isinstance(project_dict, dict) else {}
        tmp, err = _create_temp_json_for_sequence_batch(full, sequence_id)
        if not tmp: return f"Error creating config for sequence.\n\n{err}"
        
        # Get sequence paths
        seq_path, sid, error_msg = _get_sequence_paths(project_dict, sequence_id)
        if error_msg: return error_msg
        
        # Write temp config file
        tdir = _get_temp_dir(full) or Path(__file__).parent
        temp_config = tdir / f"__seq_upscale_{sequence_id}.json"
        with open(temp_config, 'w') as f:
            json.dump(tmp, f)
        
        # Use sequence-specific status file
        status_file_path = seq_path / "_seq_upscale_status.json"
        config_path = str(temp_config)
    else:
        # Project-wide: use original filepath
        base_path, _, error_msg = _get_project_paths(project_dict)
        if error_msg: return error_msg
        
        status_file_path = base_path / "_upscale_status.json"
        config_path = filepath
    
    script_path = os.path.join(SCRIPT_DIRECTORY, "run_upscale.py")
    command = [sys.executable, "-u", script_path, "--config", config_path, "--status-file", str(status_file_path)]
    if do_upscale: command.append("--upscale")
    if do_interp: command.append("--interpolate")
    
    return _launch_detached_batch_script(command, status_file_path)

def read_upscale_status(project_dict: dict):
    base_path, _, error_msg = _get_project_paths(project_dict)
    if error_msg: return error_msg
    return _format_status_file(base_path / "_upscale_status.json", "Enhance Batch")


def cancel_upscale_batch(project_dict: dict, sequence_id=None):
    # Determine which status file to use
    if sequence_id:
        seq_path, sid, error_msg = _get_sequence_paths(project_dict, sequence_id)
        if error_msg: return error_msg
        status_file_path = seq_path / "_seq_upscale_status.json"
    else:
        base_path, _, error_msg = _get_project_paths(project_dict)
        if error_msg: return error_msg
        status_file_path = base_path / "_upscale_status.json"
    
    if not status_file_path.exists(): return "No active Enhance batch found."
    try:
        with open(status_file_path, 'r', encoding='utf-8') as f: data = json.load(f)
        pid = data.get("pid")
        if pid:
            # subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
            ProcessManager.kill_process_tree(pid)
            data["status"] = "cancelled"
            with open(status_file_path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2)
            return f"Cancelled Enhance batch (PID {pid})."
    except Exception as e: return f"Error cancelling: {e}"


def build_enhance_manager():
    comps = {}
    with gr.Group():
        # gr.Markdown("### Enhance / Post-Process")
        with gr.Row():
            comps["chk_upscale"] = gr.Checkbox(label="Upscale (2x Res)", value=False)
            comps["chk_interp"] = gr.Checkbox(label="Interpolate (2x FPS)", value=False)
        with gr.Row():
            comps["run_btn"] = gr.Button("Generate Layers", variant="primary")
            comps["cancel_btn"] = gr.Button("Cancel", variant="stop")
    return comps


def build_cascade_manager(pj=None):
    """Build cascade batch UI with separate iteration inputs."""
    c = {}
    
    # Get default values from project if available
    kf_default = 1
    vid_default = 1
    if pj:
        proj = pj if isinstance(pj, dict) else {}
        kf_default = proj.get("project", {}).get("keyframe_generation", {}).get("image_iterations_default", 1)
        vid_default = proj.get("project", {}).get("inbetween_generation", {}).get("video_iterations_default", 1)
    
    with gr.Row():
        c["kf_iter"] = gr.Number(
            label="Keyframe passes",
            info="Number of cascade iterations (1 keyframe per pass)",
            value=kf_default,
            precision=0,
            minimum=0,
            interactive=True,
        )
        c["vid_iter"] = gr.Number(
            label="Videos per pass",
            info="Videos to generate per keyframe pass",
            value=vid_default,
            precision=0,
            minimum=0,
            interactive=True,
        )
    
    with gr.Row():
        c["run_btn"] = gr.Button("Run Cascade", variant="primary")
        c["cancel_btn"] = gr.Button("Cancel Cascade", variant="stop")
    
    return c


def build_run_status_ui():
    comps = {}
    comps["status_window"] = gr.Textbox(label="Batch Status", lines=10, interactive=False, autoscroll=True)
    with gr.Row():
        comps["refresh_btn"] = gr.Button("Refresh Status", variant="secondary")
    return comps



def build_batch_inputs(prefix, label, form=None, paths=None, is_interactive=True):
    comps = {}
    with gr.Row():
        if form and paths:
            comps[f"{prefix}_iter"] = form.add(
                paths["iter"],
                gr.Number(label="Target number",  precision=0, interactive=is_interactive, minimum=0),
                default=1,
                to_json=int,
            )
        else:
            comps[f"{prefix}_iter"] = gr.Number(
                label="Target number",
                value=1,
                precision=0,
                interactive=is_interactive,
                minimum=0,
            )
    with gr.Row():
        comps[f"{prefix}_cap"] = gr.Checkbox(
            label="Cap at target",
            info="Stops generating when this many are present",
            value=False,
            interactive=is_interactive,
        )
        comps[f"{prefix}_sync"] = gr.Checkbox(
            label="Sync seeds",
            info="Use consistent seeds across nodes rather than randomize",
            value=False,
            interactive=is_interactive,
        )
    return comps

def build_batch_run_btn(prefix, label):
    return gr.Button(f"Batch {label}", variant="primary")

def build_batch_cancel_btn(prefix):
    return gr.Button("Cancel", variant="stop")

def build_purge_ui(prefix, label):
    comps = {}
    with gr.Accordion(f"Purge All {label} Media", open=False, elem_classes=["themed-accordion", "stop-theme"]) as acc:
        comps[f"{prefix}_acc"] = acc
        comps[f"{prefix}_purge_btn"] = gr.Button(f"Purge All {label} Media", variant="secondary")
        with gr.Group(visible=False) as confirm:
            gr.Markdown("**ARE YOU SURE?** This cannot be undone.")
            with gr.Row():
                comps[f"{prefix}_yes"] = gr.Button("Yes, Purge", variant="stop")
                comps[f"{prefix}_no"] = gr.Button("Cancel")
        comps[f"{prefix}_confirm_group"] = confirm
    return comps

def _get_project_paths(project_dict: dict) -> Tuple[Path | None, Dict | None, str | None]:
    try:
        data = project_dict if isinstance(project_dict, dict) else {}
        output_root = data.get("project", {}).get("comfy", {}).get("output_root")
        project_name = data.get("project", {}).get("name")
        if not (output_root and project_name and str(project_name).strip()):
            return None, None, "Error: Could not find 'output_root' or 'name' in project JSON. Is a project open?"
        base_path = Path(output_root) / project_name
        return base_path, data, None
    except Exception as e:
        return None, None, f"Error parsing project JSON: {e}"

def _get_sequence_paths(project_dict: dict, seq_id: str) -> Tuple[Path | None, str | None, str | None]:
    base_path, data, error_msg = _get_project_paths(project_dict)
    if error_msg: return None, None, error_msg
    if not seq_id or seq_id not in data.get("sequences", {}):
        return None, None, f"Error: Sequence ID '{seq_id}' not found."
    try:
        seq = data["sequences"][seq_id]
        sid = seq.get("id", seq_id)
        seq_path = base_path / sid
        return seq_path, sid, None
    except Exception as e:
        return None, None, f"Error resolving sequence path: {e}"
    
def generate_beats_readout(project_dict: dict):
    try:
        data = _ensure_project(project_dict) if isinstance(project_dict, dict) else _ensure_project({})
    except Exception as e: return f"Error parsing project JSON: {e}"
    
    beats = []
    cumulative_time = 0.0
    default_dur = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    sorted_seqs = sorted(data.get("sequences", {}).values(), key=lambda x: x.get("order", 0))

    for seq in sorted_seqs:
        setting_prompt = seq.get("setting_prompt", "").strip()
        if setting_prompt: beats.append(f"{_fmt_clock(cumulative_time)} {setting_prompt}")

        for vid_id in seq.get("video_order", []):
            video_obj = seq.get("videos", {}).get(vid_id, {})
            duration = _video_seconds(video_obj, default_dur)
            start_time_str = _fmt_clock(cumulative_time)
            end_time_str = _fmt_clock(cumulative_time + duration)

            start_id = video_obj.get("start_keyframe_id")
            if start_id:
                kf = seq.get("keyframes", {}).get(start_id, {})
                keyframe_prompt = (kf.get("layout", "") or "").strip()
                if keyframe_prompt: beats.append(f"{start_time_str} {keyframe_prompt}")
            
            video_prompt = (video_obj.get("inbetween_prompt", "") or "").strip()
            if video_prompt: beats.append(f"{start_time_str} - {end_time_str} {video_prompt}")

            cumulative_time += duration
    return "\n".join(beats)

def _launch_detached_batch_script(command_parts: List[str], status_file_path: Path | None, env: Dict = None, cwd: str | Path = None) -> str:
    if not command_parts: return "Error: No command provided to run."
    print(f"Executing: {' '.join(command_parts)}")

    if status_file_path:
        try:
            if status_file_path.exists(): os.remove(status_file_path)
        except Exception as e: print(f"Warning: Could not clear status file: {e}")

    try:

        process = ProcessManager.launch_detached(
            command_parts,
            cwd=str(cwd) if cwd else None,
            env=env
        )
        pid = process.pid
        log_msg = f"Batch process started with PID {pid}\nCLI: {' '.join(command_parts)}" # NEW
        if status_file_path:
            try:
                status_data = {"status": "starting", "pid": pid, "last_update": datetime.now().isoformat()}
                with open(status_file_path, 'w', encoding='utf-8') as f: json.dump(status_data, f, indent=2)
            except Exception as e: log_msg += f"\nWarning: Could not write status file: {e}"
        return log_msg
    except Exception as e:
        return f"Error launching detached process: {e}"

def _run_export_script_and_stream(command_parts: List[str]):
    yield "Starting export process...", gr.File(visible=False)
    process = subprocess.Popen(command_parts, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', bufsize=1)
    full_log = ""
    try:
        for line in process.stdout:
            full_log += line
            yield full_log, gr.File(visible=False)
    except BaseException:
        print("Client disconnected.")
    return_code = process.wait()
    if return_code != 0:
        yield full_log + f"\n\nERROR: Exit code {return_code}.", gr.File(visible=False)
        return
    try:
        last_line = full_log.strip().split('\n')[-1]
        output_path = Path(last_line)
        if output_path.exists():
            yield full_log + f"\n\nSuccess!", gr.File(value=str(output_path), visible=True, label="Download")
        else:
            yield full_log + f"\n\nERROR: Output file not found: {last_line}", gr.File(visible=False)
    except Exception as e:
        yield full_log + f"\n\nERROR: {e}", gr.File(visible=False)

def run_images_script(filepath: str, project_dict: dict):
    base_path, _, _ = _get_project_paths(project_dict)
    status_file = base_path / "_images_status.json" if base_path else None
    script = os.path.join(SCRIPT_DIRECTORY, "run_images.py")
    cmd = [sys.executable, "-u", script, "--config", filepath]
    if status_file: cmd.extend(["--status-file", str(status_file)])
    return _launch_detached_batch_script(cmd, status_file)

def run_videos_script(filepath: str, project_dict: dict):
    base_path, _, _ = _get_project_paths(project_dict)
    status_file = base_path / "_videos_status.json" if base_path else None
    script = os.path.join(SCRIPT_DIRECTORY, "run_video.py")
    cmd = [sys.executable, "-u", script, "--config", filepath]
    if status_file: cmd.extend(["--status-file", str(status_file)])
    return _launch_detached_batch_script(cmd, status_file)

def cancel_batch_script(project_dict: dict, batch_type: str, scope: str="project", seq_id: str=None):
    if scope == "sequence":
        path, _, _ = _get_sequence_paths(project_dict, seq_id)
        filename = f"_seq_{batch_type}_status.json"
    else:
        base_path, _, error = _get_project_paths(project_dict)
        if error: return error
        path = base_path
        filename = f"_{batch_type}_status.json"
    
    if not path: return "Path not found."
    status_file = path / filename

    if not status_file.exists(): return f"No active {batch_type} batch ({scope})."
    try:
        with open(status_file, 'r') as f: data = json.load(f)
        pid = data.get("pid")
        if pid:
            # subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
            ProcessManager.kill_process_tree(pid)
            data["status"] = "cancelled"
            with open(status_file, 'w') as f: json.dump(data, f, indent=2)
            return f"Cancelled PID {pid}."
    except Exception as e: return f"Error: {e}"

def export_timeline_script(current_file_path: str):
    if not current_file_path: yield "Error: No file loaded.", gr.File(visible=False); return
    script = os.path.join(SCRIPT_DIRECTORY, "run_export.py")
    yield from _run_export_script_and_stream([sys.executable, "-u", script, "--config", current_file_path])

def stitch_timeline_mp4(current_file_path: str):
    if not current_file_path: yield "Error: No file loaded.", gr.File(visible=False); return
    script = os.path.join(SCRIPT_DIRECTORY, "run_stitch.py")
    yield from _run_stitch_script_and_stream([sys.executable, "-u", script, "--config", current_file_path, "--format", "mp4"], "Download MP4")

def stitch_timeline_gif(current_file_path: str):
    if not current_file_path: yield "Error: No file loaded.", gr.File(visible=False); return
    script = os.path.join(SCRIPT_DIRECTORY, "run_stitch.py")
    yield from _run_stitch_script_and_stream([sys.executable, "-u", script, "--config", current_file_path, "--format", "gif"], "Download GIF")

def _run_stitch_script_and_stream(command_parts: List[str], download_label: str = "Download File"):
    yield "Starting stitch process...", gr.File(visible=False), gr.skip()
    process = subprocess.Popen(command_parts, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', bufsize=1)
    full_log = ""
    try:
        for line in process.stdout: full_log += line; yield full_log, gr.File(visible=False), gr.skip()
    except: pass
    if process.wait() != 0: yield full_log + "\nERROR", gr.File(visible=False), gr.skip(); return
    try:
        out = Path(full_log.strip().split('\n')[-1])
        if out.exists(): yield full_log + "\nSuccess!", gr.File(value=str(out), visible=True, label=download_label), gr.skip()
        else: yield full_log + "\nError finding output.", gr.File(visible=False), gr.skip()
    except: yield full_log + "\nError parsing output.", gr.File(visible=False), gr.skip()


def purge_bridge_media(project_dict: dict):
    base_path, data, err = _get_project_paths(project_dict)
    if err: return project_dict, err
    log = ["Purging bridges..."]
    for seq_id, seq in data.get("sequences", {}).items():
        for sub in ["bridges", "frames"]:
            p = base_path / seq_id / sub
            if p.is_dir(): shutil.rmtree(p); log.append(f"Deleted {p}")
    return project_dict, "\n".join(log)

def purge_keyframe_media(project_dict: dict):
    base_path, data, err = _get_project_paths(project_dict)
    if err: return project_dict, err
    log = ["Purging keyframes..."]
    for seq_id, seq in data.get("sequences", {}).items():
        seq_path = base_path / seq_id
        if not seq_path.is_dir(): continue
        for p in seq_path.glob("id*"):
            if p.is_dir(): shutil.rmtree(p); log.append(f"Deleted {p}")
        for kf in seq.get("keyframes", {}).values(): kf.pop("selected_image_path", None)
    return data, "\n".join(log)

def purge_inbetween_media(project_dict: dict):
    base_path, data, err = _get_project_paths(project_dict)
    if err: return project_dict, err
    log = ["Purging inbetweens..."]
    for seq_id, seq in data.get("sequences", {}).items():
        seq_path = base_path / seq_id
        if not seq_path.is_dir(): continue
        for p in seq_path.glob("vid*"):
            if p.is_dir(): shutil.rmtree(p); log.append(f"Deleted {p}")
        for v in seq.get("videos", {}).values(): v.pop("selected_video_path", None)
    return data, "\n".join(log)

def purge_sequence_keyframes(project_dict: dict, seq_id: str):
    path, sid, err = _get_sequence_paths(project_dict, seq_id)
    if err: return project_dict, err
    log = [f"Purging KFs for {sid}..."]
    if path.is_dir():
        for p in path.glob("id*"):
            if p.is_dir(): shutil.rmtree(p); log.append(f"Deleted {p}")
    data = project_dict.get("project", project_dict) if isinstance(project_dict, dict) else {} # Handling nested structure if any
    # Correction: Use the passed dict directly if it's the root
    if "sequences" in project_dict:
        data = project_dict
    
    seq = data.get("sequences", {}).get(seq_id)
    if seq:
        for kf in seq.get("keyframes", {}).values(): kf.pop("selected_image_path", None)
    return data, "\n".join(log)

def purge_sequence_inbetweens(project_dict: dict, seq_id: str):
    path, sid, err = _get_sequence_paths(project_dict, seq_id)
    if err: return project_dict, err
    log = [f"Purging Vids for {sid}..."]
    if path.is_dir():
        for p in path.glob("vid*"):
            if p.is_dir(): shutil.rmtree(p); log.append(f"Deleted {p}")
    data = project_dict
    seq = data.get("sequences", {}).get(seq_id)
    if seq:
        for v in seq.get("videos", {}).values(): v.pop("selected_video_path", None)
    return data, "\n".join(log)

def read_sequence_status_files(project_dict: dict, seq_id: str):
    path, sid, err = _get_sequence_paths(project_dict, seq_id)
    if err: return err
    s1 = _format_status_file(path / "_seq_images_status.json", "Image Batch")
    s2 = _format_status_file(path / "_seq_videos_status.json", "Video Batch")
    s3 = _format_status_file(path / "_seq_qc_status.json", "QC Batch")
    return "\n\n".join(filter(None, [s1, s2, s3])) or "No active sequence jobs."

def _set_project_selection_to_latest_kf(data: dict, base_path: Path):
    for seq_id, seq in data.get("sequences", {}).items():
        for kf_id, kf in seq.get("keyframes", {}).items():
            d = base_path / seq_id / kf_id
            if d.exists():
                files = sorted([f for f in d.iterdir() if f.suffix.lower() in {'.png','.jpg','.jpeg'}], key=lambda x: x.name, reverse=True)
                if files: kf["selected_image_path"] = str(files[0])

def cancel_cascade_batch(project_dict: dict, scope="project", seq_id=None):
    base_path, _, _ = _get_project_paths(project_dict)
    sfile = base_path / "_cascade_status.json"
    wfile = base_path / "_cascade_worker_status.json"
    if scope == "sequence" and seq_id:
        _, sid, _ = _get_sequence_paths(project_dict, seq_id)
        sfile = base_path / sid / "_seq_cascade_status.json"
        wfile = base_path / sid / "_seq_cascade_worker_status.json"
    
    log = []
    if sfile.exists():
        try:
            with open(sfile, 'r') as f: d = json.load(f)
            d["status"] = "cancelled"; 
            with open(sfile, 'w') as f: json.dump(d, f, indent=2)
            log.append("Cancelled Cascade.")
        except: pass
    if wfile.exists():
        try:
            with open(wfile, 'r') as f: d = json.load(f)
            pid = d.get("pid")
            if pid: subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"]); log.append(f"Killed worker {pid}")
        except: pass
    return "\n".join(log) or "No cascade found."



def handle_qc_batch(filepath: str, project_dict: dict, scope: str = "project", seq_id: str = None, threshold: int = 3):
    """
    Launch batch QC scoring as a detached process.
    Survives browser close and app restart.
    """
    if not filepath:
        return "Error: No file."
    
    base_path, _, _ = _get_project_paths(project_dict)
    if not base_path:
        return "Error: Could not determine project path."
    

    if scope == "sequence" and seq_id:
        seq_path, _, _ = _get_sequence_paths(project_dict, seq_id)
        status_file = seq_path / "_seq_qc_status.json"
    elif scope == "poses":
        poses_dir = base_path / "_poses"
        poses_dir.mkdir(parents=True, exist_ok=True)
        status_file = poses_dir / "_qc_status.json"
    else:
        status_file = base_path / "_qc_status.json"
    # script = os.path.join(SCRIPT_DIRECTORY, "run_qc_batch.py")
    script = os.path.join(SCRIPT_DIRECTORY, "qc", "run_qc_batch.py")    
    cmd = [
        JOYCAPTION_PYTHON,
        "-u", script,
        "--config", filepath,
        "--scope", scope,
        "--threshold", str(threshold),
        "--status-file", str(status_file)
    ]
    
    if scope == "poses":
        cmd.append("--pose")
    
    if scope == "sequence" and seq_id:
        cmd.extend(["--seq-id", seq_id])
    
    return _launch_detached_batch_script(cmd, status_file)


def cancel_qc_batch(project_dict: dict, scope: str = "project", seq_id: str = None):
    """Cancel running QC batch."""
    return cancel_batch_script(project_dict, "qc", scope, seq_id)




def read_qc_status(project_dict: dict, scope: str = "project", seq_id: str = None):
    """Read QC batch status."""
    if scope == "sequence" and seq_id:
        path, _, _ = _get_sequence_paths(project_dict, seq_id)
        status_file = path / "_seq_qc_status.json" if path else None
    elif scope == "poses":
        base_path, _, _ = _get_project_paths(project_dict)
        status_file = base_path / "_poses" / "_qc_status.json" if base_path else None
    else:
        base_path, _, _ = _get_project_paths(project_dict)
        status_file = base_path / "_qc_status.json" if base_path else None

    if not status_file or not status_file.exists():
        return ""
    
    try:
        with open(status_file, 'r') as f:
            data = json.load(f)
        status = data.get("status", "unknown")
        progress = data.get("progress", "")
        info = data.get("info", "")
        passed = data.get("passed", 0)
        failed = data.get("failed", 0)
        error = data.get("error", "")
        
        lines = [f"QC Batch: {status}"]
        if progress:
            lines.append(f"Progress: {progress}")
        if info:
            lines.append(info)
        lines.append(f"Passed: {passed}, Rejected: {failed}")
        if error:
            lines.append(f"Error: {error}")
        
        return "\n".join(lines)
    except:
        return ""




def handle_cascade_batch(filepath: str, project_dict: dict, scope: str = "project", seq_id: str = None, kf_iterations: int = None, vid_iterations: int = None):
    """
    Cascade batch:
    - Always ADD mode (force generate)
    - Random seed per node (no sync)
    - Generates all keyframes, sets as selected, then generates videos
    - Repeats for kf_iterations passes
    """
    if not filepath:
        return "Error: No file."
    
    import random
    import threading
    
    def run():
        base_path, _, _ = _get_project_paths(project_dict)
        sfile = base_path / "_cascade_status.json"
        wfile = base_path / "_cascade_worker_status.json"
        
        if scope == "sequence" and seq_id:
            seq_path, sid, _ = _get_sequence_paths(project_dict, seq_id)
            sfile = seq_path / "_seq_cascade_status.json"
            wfile = seq_path / "_seq_cascade_worker_status.json"
        
        try:
            with open(filepath, 'r') as f:
                master = json.load(f)
            
            kf_gen = master.get("project", {}).get("keyframe_generation", {})
            ib_gen = master.get("project", {}).get("inbetween_generation", {})
            
            # Use passed iterations or fall back to project defaults
            xt = kf_iterations if kf_iterations is not None else int(kf_gen.get("image_iterations_default", 1))
            yt = vid_iterations if vid_iterations is not None else int(ib_gen.get("video_iterations_default", 1))
            
            tdir = _get_temp_dir(master) or Path(__file__).parent
            
            for i in range(max(1, xt)):
                # Hot reload master settings before every pass
                with open(filepath, 'r') as f:
                    master = json.load(f)
                
                # --- PHASE 1: KEYFRAME GENERATION ---
                if xt > 0:
                    _write_status(sfile, os.getpid(), "running", f"Pass {i+1}/{xt}", "Generating Keyframes...")
                    
                    kfd = copy.deepcopy(master)
                    if scope == "sequence" and seq_id:
                        kfd, _ = _create_temp_json_for_sequence_batch(kfd, seq_id)
                    
                    # Set force_generate and random seed per keyframe
                    for s in kfd.get("sequences", {}).values():
                        for k in s.get("keyframes", {}).values():
                            k["force_generate"] = True
                            k["sampler_seed_start"] = random.randint(0, 2**31 - 1)
                    
                    kfd["project"]["keyframe_generation"]["image_iterations_default"] = 1
                    
                    ktmp = tdir / f"__cas_kf_{i}.json"
                    with open(ktmp, 'w') as f:
                        json.dump(kfd, f)
                    
                    subprocess.run(
                        [sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_images.py"), "--config", str(ktmp), "--status-file", str(wfile)],
                        check=True
                    )
                    
                    if ktmp.exists():
                        os.remove(ktmp)
                    
                    # --- PHASE 2: LINK (set selected_image_path to latest) ---
                    _set_project_selection_to_latest_kf(master, base_path)
                    with open(filepath, 'w') as f:
                        json.dump(master, f, indent=2)
                
                # --- PHASE 3: VIDEO GENERATION ---
                for j in range(yt):
                    # Check for cancellation
                    try:
                        with open(sfile, 'r') as f:
                            if json.load(f).get("status") == "cancelled":
                                return
                    except:
                        pass
                    
                    # Hot reload master settings
                    with open(filepath, 'r') as f:
                        master = json.load(f)
                    
                    _write_status(sfile, os.getpid(), "running", f"Pass {i+1}/{xt} | Video {j+1}/{yt}", "Generating Videos...")
                    
                    ibd = copy.deepcopy(master)
                    if scope == "sequence" and seq_id:
                        ibd, _ = _create_temp_json_for_sequence_batch(ibd, seq_id)
                    
                    # Set force_generate and random seed per video
                    for s in ibd.get("sequences", {}).values():
                        for v in s.get("videos", {}).values():
                            v["force_generate"] = True
                            v["seed_start"] = random.randint(0, 2**31 - 1)
                    
                    ibd["project"]["inbetween_generation"]["video_iterations_default"] = 1
                    
                    itmp = tdir / f"__cas_ib_{i}_{j}.json"
                    with open(itmp, 'w') as f:
                        json.dump(ibd, f)
                    
                    subprocess.run(
                        [sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_video.py"), "--config", str(itmp), "--status-file", str(wfile)],
                        check=True
                    )
                    
                    if itmp.exists():
                        os.remove(itmp)
            
            _write_status(sfile, os.getpid(), "completed", "Cascade Finished")
        
        except Exception as e:
            status = "cancelled" if "cancelled" in str(e).lower() else "failed"
            _write_status(sfile, os.getpid(), status, error=str(e))
    
    threading.Thread(target=run, daemon=True).start()
    return "Cascade Started."



# =============================================================================
# POSE BATCH HANDLER
# Add this to run_helpers.py after handle_cascade_batch (around line 800)
# Also add to imports at top: import re, import random, import threading
# =============================================================================

# --- POSE CONSTANTS (match assets_helpers.py) ---
POSE_STYLE_SIMPLE = "shaded sketch, depth shaded foreground, background perspective lines show the space receding behind"
POSE_MODEL_SIMPLE = "sdXL_v10VAEFix.safetensors"
POSE_NEGATIVE_SIMPLE = "text, watermark, camera, tripod, light stand, celebrity, infinity wall, native attire, amputation, amputee, hats, hat, fancy clothes, flat background, noise, nude, nsfw"
POSE_GEN_CHARACTER_OVERRIDE_SIMPLE = "wearing simple sleek body suit, natural proportions, smooth seamless unitard suit"

POSE_STYLE_EXPRESSIVE = "high quality detailed illustration"
POSE_MODEL_EXPRESSIVE = "obsessionIllustrious_v21.safetensors"
POSE_NEGATIVE_EXPRESSIVE = "text, watermark, camera, tripod, light stand, celebrity, ornate, frame"
POSE_GEN_CHARACTER_OVERRIDE_EXPRESSIVE = ""

POSE_GEN_SETTING_OVERRIDE_ONE = "exactly one person"
POSE_GEN_NEGATIVE_ONE = "(((more than one person))) extra limbs, distorted bodies"

PROJECT_ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"


def _build_pose_prompt_for_keyframe(project_data: dict, keyframe_data: dict) -> Tuple[str, str, bool]:
    """
    Build pose generation prompt from keyframe data.
    Returns (prompt, mode, has_character)
    
    =======================================================================
    DUPLICATED LOGIC: Character LoRA extraction is duplicated in editor_helpers.py
    If you change how LoRA tags or keywords are extracted, update both files.
    See: editor_helpers.py :: _eh_generate_pose_for_keyframe()
    =======================================================================
    """
    import re
    
    layout = keyframe_data.get("layout", "").strip()
    char_ids = keyframe_data.get("characters") or ["", ""]
    char_left_id = char_ids[0] if char_ids else ""
    
    # Find character
    characters = project_data.get("project", {}).get("characters", [])
    char = next((c for c in characters if c.get("id") == char_left_id), None) if char_left_id else None
    
    has_character = bool(char)
    prompt_parts = [layout] if layout else []
    
    if char:
        # Extract __lora:...:..__ tags from character's prompt field
        char_prompt = char.get("prompt", "") or ""
        lora_tags = re.findall(r'__lora:[^_]+__', char_prompt)
        prompt_parts.extend(lora_tags)
        
        # Get trigger keywords
        lora_keyword = char.get("lora_keyword", "").strip()
        if lora_keyword:
            prompt_parts.append(lora_keyword)
    
    # Use Expressive mode when character is selected
    mode = "Expressive" if has_character else "Simple"
    prompt = ", ".join(filter(None, prompt_parts))
    
    return prompt, mode, has_character


def _should_skip_pose_keyframe(kf: dict) -> Tuple[bool, str]:
    """
    Determine if keyframe should be skipped for pose generation.
    Skip if: has pose already, OR (no layout AND no character)
    """
    if kf.get("pose"):
        return True, "already has pose"
    
    layout = kf.get("layout", "").strip()
    char_ids = kf.get("characters") or ["", ""]
    char_left_id = char_ids[0] if char_ids else ""
    
    if not layout and not char_left_id:
        return True, "no layout and no character"
    
    return False, None


def _create_temp_json_for_pose(pose_prompt: str, full_project_data: dict, pose_mode: str) -> Tuple[Dict, str]:
    """
    Creates temp project config for pose generation.
    Mirrors assets_helpers._create_temp_json_for_pose_gen()
    Returns (temp_data, unique_id)
    """
    import random
    
    # Select workflow based on mode
    if pose_mode == "Project Style":
        pose_workflow_path = str(WORKFLOWS_DIR / "pose_OPEN.json")
    else:
        pose_workflow_path = str(WORKFLOWS_DIR / "pose_factory.json")
    
    unique_id = f"id_pose_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    temp_data = copy.deepcopy(full_project_data)
    temp_data["project"]["name"] = "__test_cache__"
    
    # Ensure kf_gen exists
    kf_gen = temp_data["project"].setdefault("keyframe_generation", {})
    kf_gen["image_iterations_default"] = 1
    kf_gen["sampler_seed_start"] = random.randint(0, 2**32 - 1)
    
    # Mode-specific settings
    if pose_mode == "Project Style":
        temp_data["project"]["style_prompt"] = full_project_data.get("project", {}).get("style_prompt", "")
        temp_data["project"]["model"] = full_project_data.get("project", {}).get("model", "")
        src_kf = full_project_data.get("project", {}).get("keyframe_generation", {})
        kf_gen["cfg"] = src_kf.get("cfg", 4.0)
        kf_gen["sampler_name"] = src_kf.get("sampler_name", "dpmpp_2m_sde")
        kf_gen["scheduler"] = src_kf.get("scheduler", "karras")
        kf_gen["steps"] = 30
        char_prompt_modifier = ""
        base_negative = full_project_data.get("project", {}).get("negatives", {}).get("global", "")
    elif pose_mode == "Expressive":
        temp_data["project"]["style_prompt"] = POSE_STYLE_EXPRESSIVE
        temp_data["project"]["model"] = full_project_data.get("project", {}).get("pose_model_enhanced", POSE_MODEL_EXPRESSIVE)
        kf_gen["cfg"] = 4.0
        kf_gen["steps"] = 30
        kf_gen["sampler_name"] = "dpmpp_2m_sde"
        kf_gen["scheduler"] = "karras"
        char_prompt_modifier = POSE_GEN_CHARACTER_OVERRIDE_EXPRESSIVE
        base_negative = POSE_NEGATIVE_EXPRESSIVE
    else:  # Simple
        temp_data["project"]["style_prompt"] = POSE_STYLE_SIMPLE
        temp_data["project"]["model"] = full_project_data.get("project", {}).get("pose_model_fast", POSE_MODEL_SIMPLE)
        kf_gen["cfg"] = 4.0
        kf_gen["steps"] = 30
        kf_gen["sampler_name"] = "dpmpp_2m_sde"
        kf_gen["scheduler"] = "karras"
        char_prompt_modifier = POSE_GEN_CHARACTER_OVERRIDE_SIMPLE
        base_negative = POSE_NEGATIVE_SIMPLE
    
    final_negative = " ".join(filter(None, [base_negative, POSE_GEN_NEGATIVE_ONE])).strip()
    
    pose_character = {
        "id": "temp_pose_char_id", 
        "name": "Pose Character", 
        "lora_keyword": "", 
        "prompt": char_prompt_modifier, 
        "negative_prompt": ""
    }
    temp_data["project"]["characters"] = [pose_character]
    
    pose_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "basic": True,
        "pose": "none",
        "characters": ["Pose Character", ""],
        "workflow_json": pose_workflow_path,
        "layout": pose_prompt,
        "template": "",
        "use_animal_pose": False,
        "negatives": {"global": final_negative}
    }
    
    pose_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_prompt": POSE_GEN_SETTING_OVERRIDE_ONE,
        "keyframes": {unique_id: pose_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }
    
    temp_data["sequences"] = {unique_id: pose_seq}
    
    return temp_data, unique_id


def _sanitize_pose_filename(text: str, fallback: str = "file") -> str:
    """Sanitize text for use as filename."""
    import re
    if not text:
        return fallback
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', text)
    sanitized = re.sub(r'\s+', '_', sanitized)
    sanitized = re.sub(r'_+', '_', sanitized)
    sanitized = sanitized.strip('_')[:50]
    return sanitized or fallback


def _auto_version_pose_path(path: Path) -> Path:
    """Auto-version a path if it exists."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        new_path = parent / f"{stem}_({counter}){suffix}"
        if not new_path.exists():
            return new_path
        counter += 1


def handle_pose_batch(filepath: str, project_dict: dict, scope: str = "project", seq_id: str = None):
    """
    Pose batch generator:
    - Iterates keyframes needing poses (no pose, has layout or character)
    - Builds prompt with character LoRA extraction
    - Generates pose via run_images.py
    - Saves to poses library
    - Assigns pose path to keyframe
    
    Like cascade, runs in daemon thread so browser can close.
    """
    if not filepath:
        return "Error: No file."
    
    import random
    import threading
    import shutil
    
    def run():
        base_path, _, _ = _get_project_paths(project_dict)
        
        # Determine status file first
        sfile = base_path / "_poses_status.json"
        if scope == "sequence" and seq_id:
            seq_path, sid, _ = _get_sequence_paths(project_dict, seq_id)
            sfile = seq_path / "_seq_poses_status.json"
        
        poses_dir = get_project_poses_dir(project_dict)
        if not poses_dir:
            _write_status(sfile, os.getpid(), "failed", error="Could not determine poses directory")
            return
        poses_dir = Path(poses_dir)
        poses_dir.mkdir(parents=True, exist_ok=True)
        
        tdir = _get_temp_dir(project_dict) or Path(__file__).parent
        
        try:
            # Load master
            with open(filepath, 'r') as f:
                master = json.load(f)
            
            # Get sequences to process
            sequences = master.get("sequences", {})
            if scope == "sequence" and seq_id:
                sequences = {seq_id: sequences.get(seq_id, {})}
            
            # Count keyframes needing poses
            keyframes_to_process = []
            for s_id, seq in sequences.items():
                for kf_id, kf in seq.get("keyframes", {}).items():
                    skip, reason = _should_skip_pose_keyframe(kf)
                    if not skip:
                        keyframes_to_process.append((s_id, kf_id, kf))
            
            total = len(keyframes_to_process)
            if total == 0:
                _write_status(sfile, os.getpid(), "completed", "No keyframes need poses")
                return
            
            completed = 0
            failed = 0
            info_lines = []
            
            for s_id, kf_id, kf in keyframes_to_process:
                # Check for cancellation
                try:
                    with open(sfile, 'r') as f:
                        if json.load(f).get("status") == "cancelled":
                            return
                except:
                    pass
                
                # Hot reload master
                with open(filepath, 'r') as f:
                    master = json.load(f)
                
                # Get fresh keyframe data
                kf = master.get("sequences", {}).get(s_id, {}).get("keyframes", {}).get(kf_id, {})
                
                # Build prompt with LoRA extraction
                prompt, mode, has_character = _build_pose_prompt_for_keyframe(master, kf)
                
                if not prompt:
                    _write_status(sfile, os.getpid(), "running", f"{completed}/{total}", f"Skipped {kf_id}: empty prompt")
                    continue
                
                _write_status(sfile, os.getpid(), "running", f"{completed}/{total}", f"Generating pose for {kf_id}...")
                
                # Create temp config
                temp_data, unique_id = _create_temp_json_for_pose(prompt, master, mode)
                
                ktmp = tdir / f"__pose_{kf_id}_{unique_id}.json"
                with open(ktmp, 'w') as f:
                    json.dump(temp_data, f)
                
                try:
                    # Run generation
                    subprocess.run(
                        [sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_images.py"), "--config", str(ktmp)],
                        check=True
                    )
                    # Find output image
                    output_root = temp_data.get("project", {}).get("comfy", {}).get("output_root", "")
                    image_dir = Path(output_root) / "__test_cache__" / unique_id / unique_id

                    print(f"[POSE DEBUG] Looking for images in: {image_dir}")
                    print(f"[POSE DEBUG] image_dir exists: {image_dir.exists()}")

                    main_image = None
                    if image_dir.exists():
                        preview_keywords = {"openposepreview", "shapepreview", "outlinepreview"}
                        candidates = [
                            f for f in image_dir.iterdir() 
                            if f.suffix.lower() in {'.png', '.jpg', '.jpeg'}
                            and not any(kw in f.name for kw in preview_keywords)
                        ]
                        print(f"[POSE DEBUG] Found {len(candidates)} candidate images")
                        if candidates:
                            main_image = max(candidates, key=lambda x: x.stat().st_mtime)
                            print(f"[POSE DEBUG] Selected: {main_image}")


                    if main_image:
                        # Extract controlnet previews
                        pose_preview = None
                        shape_preview = None
                        outline_preview = None
                        
                        print(f"[POSE] Extracting controlnet previews...")
                        for result in run_pose_preview_task(master, str(main_image), str(poses_dir), False):
                            pose_preview = result.get("openpose_path")
                            shape_preview = result.get("shape_path")
                            outline_preview = result.get("outline_path")
                        
                        # Save using proper helper
                        pose_name = _sanitize_pose_filename(kf.get("layout", "")[:40] or f"pose_{kf_id}")
                        pose_name += "_1CHAR"
                        
                        aux_map = {"poses": pose_preview, "shapes": shape_preview, "outlines": outline_preview}
                        status_msg, saved_path = save_to_project_folder(str(main_image), str(poses_dir), pose_name, aux_map)
                        
                        print(f"[POSE] Save result: {status_msg}")
                        
                        if saved_path:
                            # Assign to keyframe
                            master["sequences"][s_id]["keyframes"][kf_id]["pose"] = saved_path
                            
                            # Save master
                            with open(filepath, 'w') as f:
                                json.dump(master, f, indent=2)
                            
                            completed += 1
                            print(f"[POSE] Assigned to keyframe: {saved_path}")
                        else:
                            failed += 1
                            print(f"[POSE] Save failed: {status_msg}")
                    else:
                        failed += 1


                except Exception as e:
                    print(f"[POSE] Error generating for {kf_id}: {e}")
                    failed += 1
                
                finally:
                    if ktmp.exists():
                        os.remove(ktmp)
            
            final_status = "completed" if failed == 0 else "completed_with_errors"
            info_lines.append(f"Done: {completed} poses, {failed} failed")
            _write_status(sfile, os.getpid(), final_status, f"{completed}/{total}", "\n".join(info_lines))
        
        except Exception as e:
            import traceback
            traceback.print_exc()
            status = "cancelled" if "cancelled" in str(e).lower() else "failed"
            _write_status(sfile, os.getpid(), status, error=str(e))
    
    threading.Thread(target=run, daemon=True).start()
    return "Pose Batch Started."


def cancel_pose_batch(project_dict: dict, sequence_id: str = None):
    """Cancel running pose batch."""
    if sequence_id:
        seq_path, sid, error_msg = _get_sequence_paths(project_dict, sequence_id)
        if error_msg:
            return error_msg
        status_file_path = seq_path / "_seq_poses_status.json"
    else:
        base_path, _, error_msg = _get_project_paths(project_dict)
        if error_msg:
            return error_msg
        status_file_path = base_path / "_poses_status.json"
    
    if not status_file_path.exists():
        return "No active Pose batch found."
    
    try:
        with open(status_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data["status"] = "cancelled"
        with open(status_file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return "Pose batch cancelled."
    except Exception as e:
        return f"Error cancelling: {e}"


def read_pose_status(project_dict: dict, sequence_id: str = None):
    """Read pose batch status."""
    if sequence_id:
        seq_path, sid, error_msg = _get_sequence_paths(project_dict, sequence_id)
        if error_msg:
            return error_msg
        status_file = seq_path / "_seq_poses_status.json"
    else:
        base_path, _, error_msg = _get_project_paths(project_dict)
        if error_msg:
            return error_msg
        status_file = base_path / "_poses_status.json"
    
    return _format_status_file(status_file, "Pose Batch")


def _write_status(fpath, pid, status, progress="", info="", error=""):
    try:
        with open(fpath, 'w') as f: json.dump({"pid":pid, "status":status, "progress":progress, "info":info, "error":error, "timestamp":datetime.now().isoformat()}, f)
    except: pass

def _set_project_selection_to_latest_kf(data: dict, base_path: Path):
    """Update selected_image_path for each keyframe to the latest generated image."""
    for seq_id, seq in data.get("sequences", {}).items():
        for kf_id, kf in seq.get("keyframes", {}).items():
            d = base_path / seq_id / kf_id
            if d.exists():
                files = sorted([f for f in d.iterdir() if f.suffix.lower() in {'.png','.jpg','.jpeg'}], key=lambda x: x.name, reverse=True)
                if files: kf["selected_image_path"] = str(files[0])

def cancel_sequence_batch_script(pj, seq_id, task_type="images"):
    """
    Cancels a detached batch script for a sequence.
    task_type: 'images' or 'videos'
    """
    path, _, _ = _get_sequence_paths(pj, seq_id)
    if not path: return "Sequence path not found."
    
    filename = "_seq_images_status.json" if task_type == "images" else "_seq_videos_status.json"
    status_file = path / filename
    
    if not status_file.exists():
        return f"No running process found for {task_type}."
        
    return _kill_process_from_status(status_file)

def handle_sequence_image_batch(pj, seq_id, iterations_override=None, cap=False, sync=False):
    if not pj: return "Error: No project data."
    full = pj if isinstance(pj, dict) else {}
    tmp, err = _create_temp_json_for_sequence_batch(full, seq_id)
    if not tmp: return f"Error creating config.\n\n..."
    
    # *** ADD OVERRIDE LOGIC HERE ***
    if iterations_override is not None:
        try:
            iter_count = int(iterations_override)
            if iter_count > 0:
                if "project" not in tmp:
                    tmp["project"] = {}
                if "keyframe_generation" not in tmp["project"]:
                    tmp["project"]["keyframe_generation"] = {}
                tmp["project"]["keyframe_generation"]["image_iterations_default"] = iter_count
        except (ValueError, TypeError):
            pass
    

    # Apply cap/force logic and seed randomization
    import random
    seq_data = tmp.get("sequences", {}).get(seq_id, {})
    for kf in seq_data.get("keyframes", {}).values():
        if not cap:
            kf["force_generate"] = True
        if not sync:
            kf["sampler_seed_start"] = random.randint(0, 2**31 - 1)
    
    path, _, _ = _get_sequence_paths(pj, seq_id)
    sf = path / "_seq_images_status.json"
    tdir = _get_temp_dir(full) or Path(__file__).parent
    tf = tdir / f"__seq_img_{seq_id}.json"
    with open(tf, 'w') as f: json.dump(tmp, f)
    
    return _launch_detached_batch_script([sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_images.py"), "--config", str(tf), "--status-file", str(sf)], sf)



def handle_sequence_video_batch(pj, seq_id, iterations_override=None, cap=False, sync=False):
    if not pj: return "Error: No project data."
    full = pj if isinstance(pj, dict) else {}
    tmp, err = _create_temp_json_for_sequence_batch(full, seq_id)
    # if not tmp: return "Error creating config."
    if not tmp: return f"Error creating config.\n\ncreate_temp_json error: {err}\nseq_id passed in: {seq_id}\nsequence keys: {list(full.get('sequences', {}).keys())}"

    # Apply ephemeral iteration override if provided
    # Apply iteration override if provided
    if iterations_override is not None:
        try:
            iter_count = int(iterations_override)
            if iter_count > 0:
                if "project" not in tmp:
                    tmp["project"] = {}
                if "inbetween_generation" not in tmp["project"]:
                    tmp["project"]["inbetween_generation"] = {}
                tmp["project"]["inbetween_generation"]["video_iterations_default"] = iter_count
        except (ValueError, TypeError):
            pass
    

    import random
    seq_data = tmp.get("sequences", {}).get(seq_id, {})
    for vid in seq_data.get("videos", {}).values():
        if not cap:
            vid["force_generate"] = True
        if not sync:
            vid["seed_start"] = random.randint(0, 2**31 - 1)
    
    path, _, _ = _get_sequence_paths(pj, seq_id)
    sf = path / "_seq_videos_status.json"
    tdir = _get_temp_dir(full) or Path(__file__).parent
    tf = tdir / f"__seq_vid_{seq_id}.json"
    with open(tf, 'w') as f: json.dump(tmp, f)
    
    return _launch_detached_batch_script([sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_video.py"), "--config", str(tf), "--status-file", str(sf)], sf)

def cancel_comfy_queue(project_dict):
    try:
        url = project_dict.get("project", {}).get("comfy", {}).get("api_base", "http://127.0.0.1:8188")
        if not url.startswith("http"): url = f"http://{url}"
        requests.post(f"{url.rstrip('/')}/interrupt", timeout=2)
        requests.post(f"{url.rstrip('/')}/queue", json={"clear":True}, timeout=2)
        return "Cancelled ComfyUI Queue."
    except: return "Failed to contact ComfyUI."

# def handle_comfyui_restart(settings_json):
#     import json
#     sett = json.loads(settings_json) if isinstance(settings_json, str) else settings_json
#     cust = sett.get("comfyui_restart_script_path", "")
    
#     msg = ["Attempting restart..."]
#     yield "\n".join(msg)
    
#     # Kill 8188
#     subprocess.run(["powershell", "-Command", "Stop-Process -Id (Get-NetTCPConnection -LocalPort 8188 -ErrorAction SilentlyContinue).OwningProcess -Force"], capture_output=True)
#     msg.append("Killed old process.")
#     yield "\n".join(msg)
    
#     if cust and os.path.exists(cust):
#         subprocess.Popen([cust], shell=True, creationflags=subprocess.DETACHED_PROCESS)
#         msg.append("Launched custom script.")
#     else:
#         # Fallback hardcoded
#         py = r"D:\ComfyUI\comfy-py0310-venv\Scripts\python.exe"
#         main = r"D:\ComfyUI\main.py"
#         if os.path.exists(py) and os.path.exists(main):
#             subprocess.Popen([py, main, "--listen", "0.0.0.0", "--port", "8188"], cwd=os.path.dirname(main), creationflags=subprocess.DETACHED_PROCESS)
#             msg.append("Launched ComfyUI.")
#         else:
#             msg.append("Could not find ComfyUI or custom script.")
            
#     yield "\n".join(msg)

def handle_comfyui_restart(settings_json):
    import json
    sett = json.loads(settings_json) if isinstance(settings_json, str) else settings_json
    cust = sett.get("comfyui_restart_script_path", "")
    
    # Get paths from settings (cross-platform)
    comfy_python = sett.get("comfyui_python_path", "")
    comfy_main = sett.get("comfyui_main_path", "")
    
    # Platform-specific defaults if not configured
    if not comfy_python:
        if IS_WINDOWS:
            comfy_python = r"D:\ComfyUI\comfy-py0310-venv\Scripts\python.exe"
        else:
            comfy_python = "/workspace/ComfyUI/venv/bin/python"
    
    if not comfy_main:
        if IS_WINDOWS:
            comfy_main = r"D:\ComfyUI\main.py"
        else:
            comfy_main = "/workspace/ComfyUI/main.py"
    
    msg = ["Attempting restart..."]
    yield "\n".join(msg)
    
    api_base = sett.get("comfy", {}).get("api_base", "http://127.0.0.1:8188")

    # Use cross-platform ComfyUI manager
    restart_log = ComfyUIManager.restart_comfyui(
        # Extract port from api_base (e.g., "http://127.0.0.1:4000" -> 4000)
        port = ComfyUIManager.extract_port_from_url(api_base, default=8188),
        # port=8188,
        custom_script=cust if cust else None,
        python_path=comfy_python,
        main_script=comfy_main,
        listen="0.0.0.0"
    )
    msg.extend(restart_log)
    
    yield "\n".join(msg)

def check_comfyui_status(project_dict, api_base=None):
    # If api_base provided, use it (from app startup settings)
    if api_base:
        url = api_base
    else:
        # Fallback: check project dict (legacy), but this shouldn't be used anymore
        data = project_dict if isinstance(project_dict, dict) else {}
        url = data.get("project", {}).get("comfy", {}).get("api_base")
        if not url:
            url = "http://127.0.0.1:8188"  # Hard default instead of calling ensure_settings()

    if not isinstance(url, str) or not url.strip():
        url = "http://127.0.0.1:8188"

    url = url.strip()
    if not url.startswith("http"):
        url = f"http://{url}"

    test_url = f"{url.rstrip('/')}/queue"


    try:
        r = requests.get(test_url, timeout=1)
        if r.status_code == 200:
            # Uses '●' which will be green, while the link is orange
            return f"● [ComfyUI Online]({url.rstrip('/')}/)"
    except Exception:
        pass
    
    # Uses '○' for a hollow, neutral "offline" look
    return "○ ComfyUI Offline"



def run_bridge_script_and_stream(fp):
    script = os.path.join(SCRIPT_DIRECTORY, "run_bridge.py")
    yield from _run_stitch_script_and_stream([sys.executable, "-u", script, "--config", fp], "Bridge Done")

def purge_sequence_bridges(project_dict, seq_id):
    path, _, _ = _get_sequence_paths(project_dict, seq_id)
    if path.exists():
        for sub in ["bridges", "frames"]:
            if (path/sub).exists(): shutil.rmtree(path/sub)
    return project_dict, "Purged bridges."

def read_status_files(project_dict):
    base_path, _, _ = _get_project_paths(project_dict)
    if not base_path: return ""
    return "\n\n".join(filter(None, [
        _format_status_file(base_path/"_images_status.json", "Image Batch"),
        _format_status_file(base_path/"_videos_status.json", "Video Batch"),
        _format_status_file(base_path/"_qc_status.json", "QC Batch"),
        _format_status_file(base_path/"_poses_status.json", "Pose Batch"),
        _format_status_file(base_path/"_poses"/"_qc_status.json", "Pose QC Batch"),
    ]))

def handle_bridge_batch(fp, pj, scope="project", seq_id=None):
    if not fp: return "Error"
    if scope=="sequence":
        full = pj if isinstance(pj, dict) else {}
        tmp, err = _create_temp_json_for_sequence_batch(full, seq_id)
        path, _, _ = _get_sequence_paths(pj, seq_id)
        sf = path / "_seq_bridge_status.json"
        tdir = _get_temp_dir(full) or Path(__file__).parent
        tf = tdir / f"__seq_br_{seq_id}.json"
        with open(tf, 'w') as f: json.dump(tmp, f)
        return _launch_detached_batch_script([sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_bridge.py"), "--config", str(tf), "--status-file", str(sf)], sf)
    
    # Project
    base, _, _ = _get_project_paths(pj)
    sf = base / "_bridge_status.json"
    return _launch_detached_batch_script([sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_bridge.py"), "--config", fp, "--status-file", str(sf)], sf)

def cancel_bridge_batch(pj, scope="project", seq_id=None):
    base, _, _ = _get_project_paths(pj)
    sf = base / "_bridge_status.json"
    if scope=="sequence" and seq_id:
        _, sid, _ = _get_sequence_paths(pj, seq_id)
        sf = base / sid / "_seq_bridge_status.json"
    
    if sf.exists():
        try:
            with open(sf, 'r') as f: d = json.load(f)
            if pid:=d.get("pid"): subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"])
            d["status"]="cancelled"
            with open(sf, 'w') as f: json.dump(d, f)
            return "Cancelled Bridge."
        except: pass
    return "No active bridge."

def read_bridge_status(pj, scope="project", seq_id=None):
    base, _, _ = _get_project_paths(pj)
    sf = base / "_bridge_status.json"
    if scope=="sequence" and seq_id:
        _, sid, _ = _get_sequence_paths(pj, seq_id)
        sf = base / sid / "_seq_bridge_status.json"
    return _format_status_file(sf, "Bridge")

def build_bridge_manager(scope="project"):
    c = {}
    with gr.Group():
        with gr.Row():
            c["gen_btn"] = gr.Button("Generate Bridges", variant="primary")
            c["cancel_btn"] = gr.Button("Cancel", variant="stop")
        with gr.Group(visible=False) as conf:
            gr.Markdown("**Purge existing bridges?**")
            with gr.Row():
                c["confirm_yes"] = gr.Button("Yes", variant="stop")
                c["confirm_no"] = gr.Button("Cancel")
        c["confirm_group"] = conf
    return c

def list_project_audio(pj):
    base, _, _ = _get_project_paths(pj)
    if not base: return []
    d = base / "_audio"
    if not d.exists(): return []
    return [str(f) for f in d.iterdir() if f.suffix in {'.mp3','.wav','.m4a'}]

def refresh_audio_list_ui(pj): return gr.update(choices=list_project_audio(pj))

def save_uploaded_audio(f, pj):
    if not f: return gr.update(), None
    base, _, _ = _get_project_paths(pj)
    d = base / "_audio"; d.mkdir(exist_ok=True)
    shutil.copy(f.name, d / Path(f.name).name)
    return gr.update(choices=list_project_audio(pj), value=str(d/Path(f.name).name)), None

def list_existing_exports(pj):
    base, _, _ = _get_project_paths(pj)
    d = base / "exports"
    if not d.exists(): return []
    return [str(p) for p in d.iterdir() if p.suffix in {'.mp4','.gif'}]

def build_export_panel(scope="project"):
    c = {}
    with gr.Group():
        # gr.Markdown(f"Export ({scope}):")
        c["source_layer"] = gr.Dropdown(label="Version", allow_custom_value=False, filterable=False,  info="Which version to export if present", choices=["Original", "Upscale (2xh)", "Interpolate (2xf)", "Both (2xh_2xf)"], value="Original")
        c["animatic"] = gr.Checkbox(label="Animatic", info="Generate timing preview from keyframes only (no GPU rendering)", value=False)
        c["export_btn"] = gr.Button("Export", variant="primary")
        c["download"] = gr.File(label="Download", visible=False)
        c["format"] = gr.Radio(["MP4", "GIF"], label="Format", value="MP4")
        c["resize"] = gr.Checkbox(label="Resize to Project", info="Enforce final size to be project size (not needed with Quarter Size video used)", value=False, visible=False)
        c["fps"] = gr.Radio(["Default", "2x Default"], info="Use 2x to correct speed when exporting 2xf version", label="FPS", value="Default")
        c["audio_dd"] = gr.Dropdown(label="Audio", info="Select any uploaded audio to attach to exported mp4", allow_custom_value=False, filterable=False,choices=[], interactive=True)
        c["audio_upload"] = gr.UploadButton("Upload Audio")
        c["log"] = gr.Textbox(label="Log", lines=4, visible=False)
        c["history_dd"] = gr.Dropdown(label="History", info="Re-download previous exports", choices=[], visible=False)
    return c

def _create_temp_json_for_single_kf(full, nid):
    node, kind, pseq, sid = _resolve_context_safe(full, nid)
    if kind!="kf": return None, None
    tmp = copy.deepcopy(full)
    s = tmp["sequences"][sid]
    tmp["sequences"] = {sid: s}
    s["keyframes"] = {nid: s["keyframes"][nid]}
    s["keyframe_order"] = [nid]
    s["videos"] = {}
    s["video_order"] = []
    return tmp, f"{sid}_{nid}"

def _create_temp_json_for_single_vid(full, nid):
    node, kind, pseq, sid = _resolve_context_safe(full, nid)
    if kind!="vid": return None, None
    tmp = copy.deepcopy(full)
    s = tmp["sequences"][sid]
    tmp["sequences"] = {sid: s}
    v = s["videos"][nid]
    s["videos"] = {nid: v}
    s["video_order"] = [nid]
    sk, ek = v.get("start_keyframe_id"), v.get("end_keyframe_id")
    kf = {}
    if sk in s["keyframes"]: kf[sk] = s["keyframes"][sk]
    if ek in s["keyframes"]: kf[ek] = s["keyframes"][ek]
    s["keyframes"] = kf
    s["keyframe_order"] = [k for k in s.get("keyframe_order",[]) if k in kf]
    return tmp, f"{sid}_{nid}"

def _resolve_context_safe(data, nid):
    try:
        n, k = get_node_by_id(data, nid)
        if k=="seq": return n, k, n, n["id"]
        sid = n.get("sequence_id")
        return n, k, data["sequences"][sid], sid
    except: return None,None,None,None

def handle_single_kf_batch(fp, pj, nid):
    full = pj if isinstance(pj, dict) else {}
    tmp, uid = _create_temp_json_for_single_kf(full, nid)
    if not tmp: return "Error"
    base, _, _ = _get_project_paths(pj)
    sf = base / f"_batch_{uid}_status.json"
    tdir = _get_temp_dir(full) or Path(__file__).parent
    tf = tdir / f"__batch_{uid}.json"
    with open(tf, 'w') as f: json.dump(tmp, f)
    return _launch_detached_batch_script([sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_images.py"), "--config", str(tf), "--status-file", str(sf)], sf)

def handle_single_vid_batch(fp, pj, nid):
    full = pj if isinstance(pj, dict) else {}
    tmp, uid = _create_temp_json_for_single_vid(full, nid)
    if not tmp: return "Error"
    base, _, _ = _get_project_paths(pj)
    sf = base / f"_batch_{uid}_status.json"
    tdir = _get_temp_dir(full) or Path(__file__).parent
    tf = tdir / f"__batch_{uid}.json"
    with open(tf, 'w') as f: json.dump(tmp, f)
    return _launch_detached_batch_script([sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_video.py"), "--config", str(tf), "--status-file", str(sf)], sf)

def handle_export_task(fp, pj, scope, seq_id, fmt, res, fps, layer, audio, animatic=False):
    if not fp: yield "Error: No file", None, None; return
    
    # If scope is sequence, we must generate a temp config containing JUST that sequence
    # run_stitch.py usually stitches everything it finds in the project.
    config_to_use = fp
    if scope == "sequence" and seq_id:
        full = pj if isinstance(pj, dict) else {}
        tmp, err = _create_temp_json_for_sequence_batch(full, seq_id)
        if not tmp: yield "Error creating temp config", None, None; return
        tdir = _get_temp_dir(full) or Path(__file__).parent
        tf = tdir / f"__export_{seq_id}.json"
        with open(tf, 'w') as f: json.dump(tmp, f)
        config_to_use = str(tf)

    cmd = [sys.executable, "-u", os.path.join(SCRIPT_DIRECTORY, "run_stitch.py"), "--config", config_to_use, "--format", fmt.lower()]
    if fps == "2x Default": cmd.extend(["--fps_mult", "2.0"])
    if res: cmd.append("--resize")
    
    # Layer Mapping
    if "Upscale" in layer: cmd.extend(["--layer", "2xh"])
    elif "Interpolate" in layer: cmd.extend(["--layer", "2xf"])
    elif "Both" in layer: cmd.extend(["--layer", "2xh_2xf"])
    
    if audio: cmd.extend(["--audio", audio])
    if animatic: cmd.append("--animatic")
    
    yield from _run_stitch_script_and_stream(cmd, "Download Export")
    yield gr.skip(), gr.skip(), gr.update(choices=list_existing_exports(pj))

def handle_sequence_export_task(fp, pj, nid, fmt, res, fps, layer="Original", audio=None, animatic=False):
    yield from handle_export_task(fp, pj, "sequence", nid, fmt, res, fps, layer, audio, animatic)

def handle_project_export_task(fp, pj, fmt, res, fps, layer="Original", audio=None, animatic=False):
    yield from handle_export_task(fp, pj, "project", None, fmt, res, fps, layer, audio, animatic)

def build_run_tab(fp, pj, sett, form=None, features={}):
    with gr.Row():
        with gr.Column(scale=1):
            with gr.Accordion("ComfyUI", open=False,elem_classes=["themed-accordion", "proj-theme"]):
                cancel_all = gr.Button("Cancel and Clear Queue", variant="stop")
                restart = gr.Button("Restart ComfyUI", variant="stop")
            with gr.Accordion("Exports", open=False,elem_classes=["themed-accordion", "proj-theme"]):
                pem = build_export_panel("project")
                with gr.Accordion("Legacy", open=False, visible=False):
                    etn = gr.Button("Export FCPX XML")
                    dl = gr.File(visible=False)
                    beats = gr.Button("Beats")
                    bout = gr.Textbox(lines=5)
        
        with gr.Column(scale=4):
            with gr.Accordion("Batch Manager", open=True,elem_classes=["themed-accordion", "proj-theme"]):
                stat = build_run_status_ui()
                with gr.Row():
                    ckf = gr.Button("Cancel KF", variant="stop")
                    cvid = gr.Button("Cancel Inbetween", variant="stop")
                    cbr = gr.Button("Cancel Bridge", variant="stop")
                    cup = gr.Button("Cancel Upscale", variant="stop")
            
            with gr.Accordion("Keyframes", open=False,elem_classes=["themed-accordion", "kf-theme"]):
                kp = {"iter": "project.keyframe_generation.image_iterations_default", "seed": "project.keyframe_generation.sampler_seed_start", "adv": "project.keyframe_generation.advance_seed_by"}
                kfi = build_batch_inputs("kf", "Keyframe", form, kp)
                with gr.Row():
                    kfb = build_batch_run_btn("kf", "Keyframe")
                    qcb = gr.Button("QC Delete Batch", variant="stop", visible=features.get("show_QC", True))
                kfp = build_purge_ui("kf", "Keyframe")
                with gr.Row():
                    pose_batch_btn = gr.Button("Batch Poses (close browser after)", variant="secondary", visible=features.get("show_pose_automation"))
                    poses_qcb = gr.Button("QC Delete Batch", variant="stop", visible=features.get("show_QC", False))
                # with gr.Row(visible=features.get("show_cascade_batches")):
                #     casb = gr.Button("Cascade Batch", variant="primary")
                #     ccas = gr.Button("Cancel Cascade", variant="stop")

            with gr.Accordion("In-betweens", open=False,elem_classes=["themed-accordion", "vid-theme"]):
                vp = {"iter": "project.inbetween_generation.video_iterations_default", "seed": "project.inbetween_generation.seed_start", "adv": "project.inbetween_generation.advance_seed_by"}
                vidi = build_batch_inputs("vid", "Inbetween", form, vp)
                with gr.Row():
                    vidb = build_batch_run_btn("vid", "Inbetween")
                vidp = build_purge_ui("vid", "Inbetween")


            with gr.Accordion("Cascade", open=False, visible=features.get("show_cascade_batches", True), elem_classes=["themed-accordion", "proj-theme"]):
                cas = build_cascade_manager()


            with gr.Accordion("Bridges", visible=features.get("show_bridges", False),open=False,elem_classes=["themed-accordion", "proj-theme"]):
                brm = build_bridge_manager()

            with gr.Accordion("Upscale", open=False,elem_classes=["themed-accordion", "proj-theme"]):
                enm = build_enhance_manager()

            with gr.Accordion("Project", open=False,elem_classes=["themed-accordion", "proj-theme"], visible=True):
                dup = gr.Button("Duplicate Project")
                with gr.Group(visible=False) as cpg:
                    cpath = gr.Textbox(label="New Name")
                    cyes = gr.Button("Save")
                    cno = gr.Button("Cancel")

    # Wire
    # stat["refresh_btn"].click(lambda p: read_status_files(p) + "\n\n" + read_bridge_status(p) + "\n\n" + read_upscale_status(p), inputs=[pj], outputs=[stat["status_window"]])
    stat["refresh_btn"].click(
        lambda p: (read_status_files(p) or "") + "\n\n" + (read_bridge_status(p) or "") + "\n\n" + (read_upscale_status(p) or ""),
        inputs=[pj],
        outputs=[stat["status_window"]],
    )

    restart.click(handle_comfyui_restart, inputs=[sett], outputs=[stat["status_window"]])
    cancel_all.click(cancel_comfy_queue, inputs=[pj], outputs=[stat["status_window"]])
    
    ckf.click(lambda p: cancel_batch_script(p, "images"), inputs=[pj], outputs=[stat["status_window"]])
    cvid.click(lambda p: cancel_batch_script(p, "videos"), inputs=[pj], outputs=[stat["status_window"]])
    cbr.click(lambda p: cancel_bridge_batch(p), inputs=[pj], outputs=[stat["status_window"]])
    cup.click(lambda p: cancel_upscale_batch(p), inputs=[pj], outputs=[stat["status_window"]])
    # ccas.click(lambda p: cancel_cascade_batch(p), inputs=[pj], outputs=[stat["status_window"]])

    kfb.click(cb_save_project, inputs=[fp, pj, sett], outputs=[]).then(run_images_script, inputs=[fp, pj], outputs=[stat["status_window"]])
    qcb.click(cb_save_project, inputs=[fp, pj, sett], outputs=[]).then(handle_qc_batch, inputs=[fp, pj], outputs=[stat["status_window"]])
    pose_batch_btn.click(cb_save_project, inputs=[fp, pj, sett], outputs=[]).then(
        handle_pose_batch, inputs=[fp, pj, gr.State("project"), gr.State(None)], outputs=[stat["status_window"]]
    )
    poses_qcb.click(cb_save_project, inputs=[fp, pj, sett], outputs=[]).then(
        lambda fp, pj: handle_qc_batch(fp, pj, "poses"), inputs=[fp, pj], outputs=[stat["status_window"]]
    )
    kfp["kf_purge_btn"].click(lambda: gr.update(visible=True), outputs=[kfp["kf_confirm_group"]])
    kfp["kf_no"].click(lambda: gr.update(visible=False), outputs=[kfp["kf_confirm_group"]])
    # kfp["kf_yes"].click(purge_keyframe_media, inputs=[pj], outputs=[pj, stat["status_window"]]).then(lambda: gr.update(visible=False), outputs=[kfp["kf_confirm_group"]])
    # kfp["kf_yes"].click(purge_keyframe_media, inputs=[pj], outputs=[pj, stat["status_window"]]).then(lambda: (gr.update(visible=False), gr.update(open=False)), outputs=[kfp["kf_confirm_group"], kfp["kf_purge_btn"].parent.parent])

    # vidb.click(cb_save_project, inputs=[fp, pj, sett], outputs=[]).then(run_videos_script, inputs=[fp, pj], outputs=[stat["status_window"]])
    # vidp["vid_purge_btn"].click(lambda: gr.update(visible=True), outputs=[vidp["vid_confirm_group"]])
    # vidp["vid_no"].click(lambda: gr.update(visible=False), outputs=[vidp["vid_confirm_group"]])
    # # vidp["vid_yes"].click(purge_inbetween_media, inputs=[pj], outputs=[pj, stat["status_window"]]).then(lambda: gr.update(visible=False), outputs=[vidp["vid_confirm_group"]])
    # vidp["vid_yes"].click(purge_inbetween_media, inputs=[pj], outputs=[pj, stat["status_window"]]).then(lambda: (gr.update(visible=False), gr.update(open=False)), outputs=[vidp["vid_confirm_group"], vidp["vid_purge_btn"].parent.parent])
    kfp["kf_yes"].click(purge_keyframe_media, inputs=[pj], outputs=[pj, stat["status_window"]]).then(lambda: (gr.update(visible=False), gr.update(open=False)), outputs=[kfp["kf_confirm_group"], kfp["kf_acc"]])

    vidb.click(cb_save_project, inputs=[fp, pj, sett], outputs=[]).then(run_videos_script, inputs=[fp, pj], outputs=[stat["status_window"]])
    vidp["vid_purge_btn"].click(lambda: gr.update(visible=True), outputs=[vidp["vid_confirm_group"]])
    vidp["vid_no"].click(lambda: gr.update(visible=False), outputs=[vidp["vid_confirm_group"]])
    vidp["vid_yes"].click(purge_inbetween_media, inputs=[pj], outputs=[pj, stat["status_window"]]).then(lambda: (gr.update(visible=False), gr.update(open=False)), outputs=[vidp["vid_confirm_group"], vidp["vid_acc"]])
    
    # casb.click(cb_save_project, inputs=[fp, pj, sett], outputs=[]).then(handle_cascade_batch, inputs=[fp, pj], outputs=[stat["status_window"]])
    cas["run_btn"].click(
        cb_save_project, inputs=[fp, pj, sett], outputs=[]
    ).then(
        handle_cascade_batch,
        inputs=[fp, pj, gr.State("project"), gr.State(None), cas["kf_iter"], cas["vid_iter"]],
        outputs=[stat["status_window"]]
    )
    cas["cancel_btn"].click(
        lambda p: cancel_cascade_batch(p),
        inputs=[pj],
        outputs=[stat["status_window"]]
    )

    brm["gen_btn"].click(lambda: gr.update(visible=True), outputs=[brm["confirm_group"]])
    brm["confirm_no"].click(lambda: gr.update(visible=False), outputs=[brm["confirm_group"]])
    brm["confirm_yes"].click(cb_save_project, inputs=[fp, pj, sett], outputs=[]).then(purge_bridge_media, inputs=[pj], outputs=[pj, stat["status_window"]]).then(lambda f, p: handle_bridge_batch(f, p), inputs=[fp, pj], outputs=[stat["status_window"]]).then(lambda: gr.update(visible=False), outputs=[brm["confirm_group"]])
    brm["cancel_btn"].click(lambda p: cancel_bridge_batch(p), inputs=[pj], outputs=[stat["status_window"]])

    enm["run_btn"].click(cb_save_project, inputs=[fp, pj, sett], outputs=[]).then(handle_upscale_batch, inputs=[fp, pj, enm["chk_upscale"], enm["chk_interp"]], outputs=[stat["status_window"]])
    enm["cancel_btn"].click(cancel_upscale_batch, inputs=[pj], outputs=[stat["status_window"]])

    pem["audio_upload"].upload(save_uploaded_audio, inputs=[pem["audio_upload"], pj], outputs=[pem["audio_dd"], pem["audio_upload"]])
    pem["audio_dd"].focus(refresh_audio_list_ui, inputs=[pj], outputs=[pem["audio_dd"]])
    pem["history_dd"].focus(list_existing_exports, inputs=[pj], outputs=[pem["history_dd"]])
    pem["history_dd"].change(lambda p: gr.update(value=p, visible=True), inputs=[pem["history_dd"]], outputs=[pem["download"]])
    
    # pem["export_btn"].click(handle_project_export_task, inputs=[fp, pj, pem["format"], pem["resize"], pem["fps"], pem["source_layer"], pem["audio_dd"]], outputs=[pem["log"], pem["download"], pem["history_dd"]])
    pem["export_btn"].click(handle_project_export_task, inputs=[fp, pj, pem["format"], pem["resize"], pem["fps"], pem["source_layer"], pem["audio_dd"], pem["animatic"]], outputs=[pem["log"], pem["download"], pem["history_dd"]])
    etn.click(export_timeline_script, inputs=[fp], outputs=[stat["status_window"], dl])
    beats.click(generate_beats_readout, inputs=[pj], outputs=[bout])

    # return (kfb, vidb, kfi["kf_iter"], kfi["kf_seed"], kfi["kf_adv"], vidi["vid_iter"], vidi["vid_seed"], vidi["vid_adv"], stat["status_window"], dup, cpg, cpath, cyes, cno)
    return (kfb, vidb, kfi["kf_iter"], vidi["vid_iter"], stat["status_window"], dup, cpg, cpath, cyes, cno)
