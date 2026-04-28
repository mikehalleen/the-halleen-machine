# test_video_helpers.py
import gradio as gr
import subprocess
import os
import json
import copy
from datetime import datetime
import time
from typing import Dict, Any, Tuple
import random
from pathlib import Path
from helpers import parse_nid, _get_temp_dir, get_node_by_id
import sys

# --- CONFIGURATION ---
SCRIPT_DIRECTORY = str(Path(__file__).parent / "../scripts")
TEMP_PROJECT_FILENAME = "__temp_project_video.json"

def _create_temp_json_for_video_test(full_data: Dict, target_vid_id: str, seed_override: int = None) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal version of the project JSON for a single video clip test,
    targeting the real project output folder.
    V2 Compliant: Uses ID lookups and dictionary structures.
    """
    # 1. Resolve Target
    node, kind = get_node_by_id(full_data, target_vid_id)
    if kind != "vid":
        return None, None, None
    
    seq_id = node.get("sequence_id")
    
    # Fallback: If sequence_id is missing, search for the containing sequence
    if not seq_id:
        for s_id, seq in full_data.get("sequences", {}).items():
            if target_vid_id in seq.get("videos", {}):
                seq_id = s_id
                break

    source_seq = full_data.get("sequences", {}).get(seq_id)
    if not source_seq:
        return None, None, None

    # 2. Deep Copy Project
    temp_data = copy.deepcopy(full_data)

    # Randomize seed for test run (unless override provided)
    if "inbetween_generation" in temp_data["project"]:
        temp_data["project"]["inbetween_generation"]["video_iterations_default"] = 1
        if seed_override is not None:
            temp_data["project"]["inbetween_generation"]["seed_start"] = seed_override
            temp_data["project"]["inbetween_generation"]["advance_seed_by"] = 0  # No advancement for explicit seed
            print(f"[DEBUG VID SEED] Using override: {seed_override}")
        else:
            rand_seed = random.randint(0, 2**32 - 1)
            temp_data["project"]["inbetween_generation"]["seed_start"] = rand_seed
            print(f"[DEBUG VID SEED] Using random: {rand_seed}")

    # 3. Isolate Sequence
    # In V2, sequences is a dict. We replace it with a dict containing ONLY our target.
    target_seq = copy.deepcopy(source_seq)
    temp_data["sequences"] = {seq_id: target_seq}
    
    # 4. Isolate Video
    # In V2, videos is a dict.
    videos = target_seq.get("videos", {})
    if target_vid_id not in videos:
        return None, None, None
        
    target_vid_config = videos[target_vid_id]
    
    # Overrides for Test
    target_vid_config["video_iterations_override"] = 1
    target_vid_config["force_generate"] = True
    
    # Polyfill V1 keys for backend compatibility
    if "start_keyframe_id" in target_vid_config: target_vid_config["start_id"] = target_vid_config["start_keyframe_id"]
    if "end_keyframe_id" in target_vid_config: target_vid_config["end_id"] = target_vid_config["end_keyframe_id"]
    
    # Prune videos dict
    
    # Prune videos dict
    target_seq["videos"] = {target_vid_id: target_vid_config}
    target_seq["video_order"] = [target_vid_id]

    # 5. Prune Keyframes
    # Identify required keyframes (Start/End)
    all_keyframes = target_seq.get("keyframes", {})
    required_keyframes = {}
    
    start_id = target_vid_config.get("start_keyframe_id")
    end_id = target_vid_config.get("end_keyframe_id")
    
    if start_id and start_id in all_keyframes:
        required_keyframes[start_id] = all_keyframes[start_id]
        
    if end_id and end_id in all_keyframes:
        required_keyframes[end_id] = all_keyframes[end_id]

    target_seq["keyframes"] = required_keyframes
    # Clean order list
    target_seq["keyframe_order"] = [k for k in target_seq.get("keyframe_order", []) if k in required_keyframes]

    return temp_data, seq_id, target_vid_id


def handle_test_video_generation(project_dict: dict, target_nid: str, seed_override: int = None):
    """The main generator function called to run the video script."""
    if not isinstance(project_dict, dict) or not target_nid:
        yield (None, "Error: No project data or target selected.", None)
        return

    full_data = project_dict
    
    # Create V2 Temp Data
    temp_data, seq_id, vid_key = _create_temp_json_for_video_test(full_data, target_nid, seed_override=seed_override)
    
    if not temp_data:
        yield (None, f"Error: Could not create test data for target '{target_nid}'.", None)
        return

    # Serialize
    temp_data_str = json.dumps(temp_data, indent=2, ensure_ascii=False)
    temp_dir = _get_temp_dir(full_data) or Path(__file__).parent
    temp_filepath = temp_dir / f"__temp_video_{datetime.now().strftime('%H%M%S_%f')}.json"
    
    final_video_path = None
    output_log = ""

    try:
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            f.write(temp_data_str)
        
        yield (None, "Starting video generation...", temp_data_str)
        
        script_path = os.path.join(SCRIPT_DIRECTORY, "run_video.py")
        command = [sys.executable, "-u", script_path, "--config", str(temp_filepath)]
        
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1
        )
        
        for line in process.stdout:
            output_log += line
            yield (None, output_log, gr.update())
        process.wait()

        # Output Retrieval
        try:
            project_name = full_data.get("project", {}).get("name", "")
            output_root = full_data.get("project", {}).get("comfy", {}).get("output_root", "")
            video_dir = Path(output_root) / project_name / seq_id / vid_key

            if video_dir.exists() and video_dir.is_dir():
                # Retry loop to handle file system latency
                latest_video = None
                for _ in range(5):
                    video_files = list(video_dir.glob("*.mp4"))
                    if video_files:
                        latest_video = max(video_files, key=lambda p: p.stat().st_mtime)
                        break
                    time.sleep(0.5)
                
                if latest_video:
                    final_video_path = str(latest_video)
                    output_log += f"\n\nSuccess: Found video at {final_video_path}"
                else:
                    output_log += f"\n\nError: Script finished, but no .mp4 video was found in {str(video_dir)}"
            else:
                output_log += f"\n\nError: Script finished, but the output directory was not found: {str(video_dir)}"
        except Exception as e:
            output_log += f"\n\nError while searching for output video: {e}"

    finally:
        # Clean up
        try:
            if temp_filepath.exists():
                os.remove(temp_filepath)
        except Exception as e:
            print(f"Warning: Failed to clean up temp file {temp_filepath}: {e}")
            
        yield (final_video_path, output_log, gr.update())