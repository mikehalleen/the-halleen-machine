# scripts/run_stitch.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A command-line tool to stitch video clips into a single MP4 file using FFmpeg.
Refactored for V2 Data Model.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import shutil

# --- Workflow/FPS Helpers ---
DEBUG_FRAMES = True  # Set to False to disable debug output

def lossless_from_selected_video(sel_path: str) -> Optional[Path]:
    """Find matching lossless video for a compressed video path."""
    if not sel_path:
        return None
    p = Path(sel_path)
    # vid_00001_.mp4 → vid_00001__lossless.mkv
    lossless = p.with_suffix("").with_name(p.stem + "_lossless.mkv")
    if lossless.exists():
        return lossless
    # Also try without double underscore (varies by naming)
    lossless_alt = Path(str(sel_path).replace(".mp4", "_lossless.mkv"))
    if lossless_alt.exists():
        return lossless_alt
    return None


def extract_frames_to_temp(lossless_path: Path, temp_dir: Path) -> Optional[Path]:
    """Extract frames from lossless video to temp directory."""
    import subprocess
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    output_pattern = temp_dir / "frame_%06d.png"
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(lossless_path),
        "-vsync", "0",
        str(output_pattern)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[EXTRACT] Frames extracted to {temp_dir}")
            return temp_dir
        else:
            print(f"[ERR] ffmpeg extract failed: {result.stderr}")
            return None
    except Exception as e:
        print(f"[ERR] ffmpeg exception: {e}")
        return None
    
def _strip_lora_tags(text: str) -> str:
    """Remove __lora:...:..__ tags from text."""
    if not text:
        return ""
    return re.sub(r'__lora:[^_]+__', '', text).strip()

def find_nodes_by_title(workflow: Dict[str, Any], title: str) -> List[tuple[str, dict]]:
    return [(nid, node) for nid, node in workflow.items()
            if isinstance(node, dict) and node.get("_meta", {}).get("title") == title]

def first_node_by_title(workflow: Dict[str, Any], title: str) -> tuple[Optional[str], Optional[dict]]:
    xs = find_nodes_by_title(workflow, title)
    return xs[0] if xs else (None, None)

def get_fps_from_create_video(node: Optional[dict]) -> Optional[float]:
    if not isinstance(node, dict) or "inputs" not in node: 
        return None
    for k in ("fps", "frame_rate", "framerate"):
        v = node["inputs"].get(k)
        if isinstance(v, (int, float)) and v > 0: 
            return float(v)
        if isinstance(v, str):
            try:
                f = float(v)
                if f > 0: return f
            except: pass
    return None

def get_bridge_params(lock_request: int) -> tuple[int, int, int]:
    """Returns (LOCK_N, BRIDGE_SAMPLE_FRAMES, BRIDGE_OUTPUT_FRAMES) matching run_bridge.py"""
    if lock_request == 2:
        return 4, 5, 13
    elif lock_request == 3:
        return 7, 7, 17
    elif lock_request == 4:
        return 8, 9, 21
    else: 
        return 9, 11, 25

def find_latest_bridge_dir(bridges_root: Path, a_name: str, b_name: str) -> Optional[Path]:
    base_name = f"{a_name}_{b_name}"
    candidates = sorted(
        [p for p in bridges_root.glob(f"{base_name}_*") if p.is_dir() and p.name.split('_')[-1].isdigit()],
        reverse=True
    )
    return candidates[0] if candidates else None

def get_project_fps(config: Dict[str, Any], project_root: Path) -> float:
    DEFAULT_FPS = 16
    try:
        vg = config.get("project", {}).get("inbetween_generation", {})
        wf_rel_path = vg.get("video_workflow_json")
        if not wf_rel_path:
            return DEFAULT_FPS

        wf_path = project_root / wf_rel_path
        if not wf_path.exists():
            wf_path = project_root / "workflows" / wf_rel_path
        
        if not wf_path.exists():
            return DEFAULT_FPS

        with open(wf_path, 'r', encoding='utf-8') as f:
            graph = json.load(f)
        
        if "nodes" in graph and isinstance(graph["nodes"], list):
            api_graph = {}
            for n in graph["nodes"]:
                api_graph[str(n["id"])] = {k: n[k] for k in n if k != "id"}
            graph = api_graph

        _, cv_node = first_node_by_title(graph, "Create Video")
        fps = get_fps_from_create_video(cv_node)
        
        return fps if fps else DEFAULT_FPS
    except Exception:
        return DEFAULT_FPS

# --- Frame IO Helpers ---

def list_pngs(folder: Path) -> List[Path]:
    if not folder.exists(): return []
    return sorted([p for p in folder.glob("*.png") if p.is_file()])

def _copy_and_rename(src_paths: List[Path], dest_dir: Path, start_index: int) -> int:
    frame_counter = start_index
    for src_path in src_paths:
        dest_path = dest_dir / f"frame_{frame_counter:06d}{src_path.suffix}"
        try:
            if dest_path.exists(): dest_path.unlink()
            os.link(src_path, dest_path)
        except Exception:
            shutil.copy2(src_path, dest_path)
        frame_counter += 1
    return frame_counter

def _get_keyframe_image_path(seq_obj: dict, kf_id: str) -> Optional[Path]:
    """Get the selected image path for a keyframe, falling back to pose if needed."""
    if kf_id == "open" or kf_id is None:
        return None
    keyframes = seq_obj.get("keyframes") or seq_obj.get("i2v_base_images", {})
    kf = keyframes.get(kf_id, {})
    
    # Try selected_image_path first
    path_str = kf.get("selected_image_path")
    if path_str:
        p = Path(path_str)
        if p.exists():
            return p
    
    # Fall back to pose image
    pose_str = kf.get("pose")
    if pose_str:
        p = Path(pose_str)
        if p.exists():
            return p
    
    return None


def _create_black_frame(width: int, height: int, output_path: Path) -> bool:
    """Create a solid black PNG image."""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=black:s={width}x{height}:d=1",
        "-frames:v", "1",
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter."""
    if not text:
        return ""
    # Escape backslashes first, then special characters
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")  # Replace apostrophe with unicode right single quote
    text = text.replace(",", "\\,")
    return text


def _wrap_text_two_lines(text: str, max_chars: int = 75) -> tuple[str, str]:
    """
    Wrap text to two lines with max_chars per line.
    Truncates second line with '...' if needed.
    Returns (line1, line2).
    """
    if not text:
        return "", ""
    
    text = text.strip()
    
    if len(text) <= max_chars:
        return text, ""
    
    # Find a good break point for first line (last space before max_chars)
    break_point = text.rfind(" ", 0, max_chars)
    if break_point == -1:
        break_point = max_chars
    
    line1 = text[:break_point].strip()
    remainder = text[break_point:].strip()
    
    if len(remainder) <= max_chars:
        line2 = remainder
    else:
        line2 = remainder[:max_chars - 3].strip() + "..."
    
    return line1, line2

def _fmt_clock(seconds: float) -> str:
    """Format seconds as M:SS."""
    seconds = max(0, float(seconds))
    m = int(seconds // 60)
    s = int(round(seconds % 60))
    return f"{m}:{s:02d}"

def _get_setting_name(project_cfg: dict, setting_id: str) -> str:
    """Look up setting name from project settings array."""
    if not setting_id:
        return ""
    settings = project_cfg.get("settings", [])
    for s in settings:
        if s.get("id") == setting_id:
            return s.get("name", "")
    return ""

def _get_style_name(project_cfg: dict, style_id: str) -> str:
    """Look up style name from project styles array."""
    if not style_id:
        return ""
    styles = project_cfg.get("styles", [])
    for s in styles:
        if s.get("id") == style_id:
            return s.get("name", "")
    return ""

def generate_animatic_segment(
    start_image: Optional[Path],
    end_image: Optional[Path],
    output_dir: Path,
    start_frame_index: int,
    duration_sec: float,
    fps: float,
    target_width: int,
    target_height: int,
    scale_factor: float = 1.0,
    temp_dir: Path = None,
    timestamp_text: str = "",
    seq_id_text: str = "",
    setting_text: str = "",
    style_text: str = "",
    action_text: str = "",
    inbetween_text: str = ""
) -> int:
    """
    Generate animatic frames for a single video segment using crossfade.
    
    Creates a dissolve from start_image to end_image.
    If an image is None, uses black.
    Optionally overlays text at top and lower third.
    
    Returns the number of frames generated.
    """
    if temp_dir is None:
        temp_dir = output_dir
    
    num_frames = int(duration_sec * fps)
    if num_frames <= 0:
        return 0
    
    # Calculate output dimensions
    out_w = int(target_width * scale_factor)
    out_h = int(target_height * scale_factor)
    
    # Scale font size with output dimensions
    top_fontsize = max(16, int(out_h * 0.04))
    bottom_fontsize = max(14, int(out_h * 0.03))
    
    # Prepare temporary image files
    temp_start = temp_dir / "_animatic_start.png"
    temp_end = temp_dir / "_animatic_end.png"
    temp_frames = temp_dir / "_animatic_frames"
    temp_frames.mkdir(exist_ok=True)
    
    # Create or copy start image
    if start_image and start_image.exists():
        cmd = ["ffmpeg", "-y", "-i", str(start_image), "-vf", f"scale={out_w}:{out_h}", str(temp_start)]
        subprocess.run(cmd, capture_output=True)
    else:
        _create_black_frame(out_w, out_h, temp_start)
    
    # Create or copy end image
    if end_image and end_image.exists():
        cmd = ["ffmpeg", "-y", "-i", str(end_image), "-vf", f"scale={out_w}:{out_h}", str(temp_end)]
        subprocess.run(cmd, capture_output=True)
    else:
        _create_black_frame(out_w, out_h, temp_end)
    
    # Build filter chain
    filter_parts = [f"[0:v][1:v]xfade=transition=fade:duration={duration_sec}:offset=0[faded]"]
    

    # Add text overlays if provided
    text_filters = []
    current_y = 20
    line_spacing = 4
    box_padding = 5
    
    # Font sizes - timestamp larger, everything else same
    timestamp_fontsize = max(20, int(out_h * 0.05))
    body_fontsize = max(14, int(out_h * 0.03))
    
    # Common font style for bold
    font_style = "font='Arial Bold'"
    
    # Timestamp and sequence ID line (larger font)
    header_text = ""
    if timestamp_text and seq_id_text:
        header_text = f"{timestamp_text} | {seq_id_text}"
    elif timestamp_text:
        header_text = timestamp_text
    elif seq_id_text:
        header_text = seq_id_text
    
    if header_text:
        escaped_header = _escape_drawtext(header_text)
        text_filters.append(
            f"drawtext=text='{escaped_header}':x=20:y={current_y}:fontsize={timestamp_fontsize}:fontcolor=white:{font_style}:box=1:boxcolor=black@0.5:boxborderw={box_padding}"
        )
        current_y += timestamp_fontsize + line_spacing
    

    # Setting text (name + prompt, up to two lines)
    if setting_text:
        line1, line2 = _wrap_text_two_lines(setting_text, 80)
        escaped1 = _escape_drawtext(line1)
        text_filters.append(
            f"drawtext=text='{escaped1}':x=20:y={current_y}:fontsize={body_fontsize}:fontcolor=white:{font_style}:box=1:boxcolor=black@0.5:boxborderw={box_padding}"
        )
        current_y += body_fontsize + line_spacing
        if line2:
            escaped2 = _escape_drawtext(line2)
            text_filters.append(
                f"drawtext=text='{escaped2}':x=20:y={current_y}:fontsize={body_fontsize}:fontcolor=white:{font_style}:box=1:boxcolor=black@0.5:boxborderw={box_padding}"
            )
            current_y += body_fontsize + line_spacing
    
    if style_text:
        line1, line2 = _wrap_text_two_lines(style_text, 80)
        escaped1 = _escape_drawtext(line1)
        text_filters.append(
            f"drawtext=text='{escaped1}':x=20:y={current_y}:fontsize={body_fontsize}:fontcolor=white:{font_style}:box=1:boxcolor=black@0.5:boxborderw={box_padding}"
        )
        current_y += body_fontsize + line_spacing
        if line2:
            escaped2 = _escape_drawtext(line2)
            text_filters.append(
                f"drawtext=text='{escaped2}':x=20:y={current_y}:fontsize={body_fontsize}:fontcolor=white:{font_style}:box=1:boxcolor=black@0.5:boxborderw={box_padding}"
            )
            current_y += body_fontsize + line_spacing
    
    # Action text (up to two lines)
    if action_text:
        line1, line2 = _wrap_text_two_lines(action_text, 80)
        escaped1 = _escape_drawtext(line1)
        text_filters.append(
            f"drawtext=text='{escaped1}':x=20:y={current_y}:fontsize={body_fontsize}:fontcolor=white:{font_style}:box=1:boxcolor=black@0.5:boxborderw={box_padding}"
        )
        current_y += body_fontsize + line_spacing
        if line2:
            escaped2 = _escape_drawtext(line2)
            text_filters.append(
                f"drawtext=text='{escaped2}':x=20:y={current_y}:fontsize={body_fontsize}:fontcolor=white:{font_style}:box=1:boxcolor=black@0.5:boxborderw={box_padding}"
            )
            current_y += body_fontsize + line_spacing
    
    # Inbetween prompt (up to two lines)
    if inbetween_text:
        line1, line2 = _wrap_text_two_lines(inbetween_text, 80)
        escaped1 = _escape_drawtext(line1)
        text_filters.append(
            f"drawtext=text='{escaped1}':x=20:y={current_y}:fontsize={body_fontsize}:fontcolor=white:{font_style}:box=1:boxcolor=black@0.5:boxborderw={box_padding}"
        )
        current_y += body_fontsize + line_spacing
        if line2:
            escaped2 = _escape_drawtext(line2)
            text_filters.append(
                f"drawtext=text='{escaped2}':x=20:y={current_y}:fontsize={body_fontsize}:fontcolor=white:{font_style}:box=1:boxcolor=black@0.5:boxborderw={box_padding}"
            )

    if text_filters:
        filter_parts.append(f"[faded]{','.join(text_filters)}[out]")
        output_label = "[out]"
    else:
        output_label = "[faded]"
    
    filter_complex = ";".join(filter_parts)
    
    # Generate frames with crossfade and text
    frame_pattern = temp_frames / "frame_%06d.png"
    
    cmd_fade = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", str(duration_sec), "-i", str(temp_start),
        "-loop", "1", "-t", str(duration_sec), "-i", str(temp_end),
        "-filter_complex", filter_complex,
        "-map", output_label,
        "-r", str(fps),
        "-frames:v", str(num_frames),
        str(frame_pattern)
    ]
    result = subprocess.run(cmd_fade, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error generating animatic frames: {result.stderr}")
        return 0
    
    # Copy generated frames to output directory with proper naming
    generated_frames = list_pngs(temp_frames)
    frame_counter = _copy_and_rename(generated_frames, output_dir, start_frame_index)
    
    # Cleanup temp files
    try:
        temp_start.unlink(missing_ok=True)
        temp_end.unlink(missing_ok=True)
        shutil.rmtree(temp_frames, ignore_errors=True)
    except:
        pass
    
    return frame_counter - start_frame_index

def assemble_sequence_animatic(
    seq_obj: dict,
    project_cfg: dict,
    dest_dir: Path,
    fps: float,
    start_index: int = 0,
    scale_factor: float = 1.0,
    start_time_sec: float = 0.0
) -> tuple[int, float]:
    """
    Generate animatic frames for an entire sequence.
    Returns (frames_generated, end_time_sec).
    """
    seq_id = seq_obj.get("id", "unknown")
    default_dur = float(project_cfg.get("inbetween_generation", {}).get("duration_default_sec", 3.0))
    target_w = int(project_cfg.get("width", 1280))
    target_h = int(project_cfg.get("height", 720))
    
    # Get video order
    i2v = seq_obj.get("videos") or seq_obj.get("i2v_videos", {})
    if not i2v:
        return 0
    
    if "video_order" in seq_obj:
        ordered_vid_keys = seq_obj["video_order"]
    else:
        ordered_vid_keys = sorted(i2v.keys(), key=lambda k: int(re.sub(r"\D", "", k) or 0))
    
    if DEBUG_FRAMES:
        print(f"\n=== ANIMATIC: {seq_id} ===")
    
    frame_counter = start_index
    
    # Get setting name and prompt for the sequence
    setting_id = seq_obj.get("setting_id", "")
    setting_name = _get_setting_name(project_cfg, setting_id)
    setting_prompt = seq_obj.get("setting_prompt", "")
    current_time = start_time_sec


    # Combine setting name and prompt
    if setting_name and setting_prompt:
        setting_text = f"{setting_name} {_strip_lora_tags(setting_prompt)}"
    else:
        setting_text = setting_name or _strip_lora_tags(setting_prompt)
    
    # Get style name and prompt
    style_id = seq_obj.get("style_id", "")
    style_name = _get_style_name(project_cfg, style_id)
    style_prompt = seq_obj.get("style_prompt", "")
    
    # Combine style name and prompt
    if style_name and style_prompt:
        style_text = f"{style_name} {_strip_lora_tags(style_prompt)}"
    else:
        style_text = style_name or _strip_lora_tags(style_prompt)
    
    # Get action prompt
    action_text = _strip_lora_tags(seq_obj.get("action_prompt", ""))
    
    for vid_id in ordered_vid_keys:
        vid = i2v.get(vid_id)
        if not vid:
            continue
        
        # Get duration
        duration = vid.get("duration_override_sec") or default_dur
        
        # Get keyframe endpoints
        start_kf_id = vid.get("start_keyframe_id")
        end_kf_id = vid.get("end_keyframe_id")
        
        start_img = _get_keyframe_image_path(seq_obj, start_kf_id)
        end_img = _get_keyframe_image_path(seq_obj, end_kf_id)
        
        # Get inbetween prompt for lower third
        inbetween_prompt = _strip_lora_tags(vid.get("inbetween_prompt", ""))

        if DEBUG_FRAMES:
            start_label = start_kf_id or "open"
            end_label = end_kf_id or "open"
            print(f"  {vid_id}: {start_label} -> {end_label}, {duration}s, scale={scale_factor}x")
        

        # Format timestamp for this segment
        timestamp_text = _fmt_clock(current_time)
        
        # Generate frames for this segment
        frames_generated = generate_animatic_segment(
            start_image=start_img,
            end_image=end_img,
            output_dir=dest_dir,
            start_frame_index=frame_counter,
            duration_sec=duration,
            fps=fps,
            target_width=target_w,
            target_height=target_h,
            scale_factor=scale_factor,
            temp_dir=dest_dir,
            timestamp_text=timestamp_text,
            seq_id_text=seq_id,
            setting_text=setting_text,
            style_text=style_text,
            action_text=action_text,
            inbetween_text=inbetween_prompt
        )
        
        frame_counter += frames_generated
        current_time += duration
    
    return frame_counter - start_index, current_time


def frames_dir_from_selected_video_path(sel_path: str, layer_suffix: str = "", temp_base: Path = None) -> Path:
    """
    Get frames directory for a selected video.
    Tries lossless video first (extracts to temp), falls back to frames folder.
    """
    vid_dir = Path(os.path.dirname(sel_path))
    
    # Extract index from video filename
    fname = Path(sel_path).name
    m = re.search(r'_(\d{5})_?\.mp4$', fname)
    idx_str = m.group(1) if m else None
    
    # Try lossless video first (new format)
    lossless = None
    if layer_suffix and idx_str:
        # Layer-specific lossless: frames_{layer}_{idx}_lossless.mkv
        lossless_name = f"frames_{layer_suffix}_{idx_str}_lossless.mkv"
        candidate = vid_dir / lossless_name
        if candidate.exists():
            lossless = candidate
    elif not layer_suffix:
        # Base lossless: matches the mp4 name
        lossless = lossless_from_selected_video(sel_path)
    
    if lossless:
        if temp_base is None:
            temp_base = vid_dir / "_temp_extract"
        suffix_tag = f"_{layer_suffix}" if layer_suffix else ""
        temp_frames = temp_base / f"extract_{Path(sel_path).stem}{suffix_tag}"
        extracted = extract_frames_to_temp(lossless, temp_frames)
        if extracted:
            return extracted
    
    # Fallback to legacy frames folder
    sp = sel_path.replace("/", "\\")
    m = re.search(r"^(.*)\\[^\\]+_(\d+)_\.mp4$", sp)
    if not m:
        vid_dir = Path(os.path.dirname(sp))
        search_pattern = f"frames_{layer_suffix}_*" if layer_suffix else "frames_*"
        candidates = sorted([p for p in vid_dir.glob(search_pattern) if p.is_dir()], reverse=True)
        if candidates: return candidates[0]
        if not layer_suffix: return vid_dir / "frames"
        return None 
    
    base_dir = m.group(1)
    idx_str = m.group(2)
    
    folder_name = f"frames_{layer_suffix}_{idx_str}" if layer_suffix else f"frames_{idx_str}"
    return Path(base_dir) / folder_name

def _run_ffmpeg_stitch(
    seq_id: str,
    frames_dir: Path, 
    output_path: Path, 
    fps: float,
    output_format: str = "mp4",
    target_width: int = -1,
    target_height: int = -1,
    resize: bool = False,
    audio_path: Optional[str] = None
) -> Optional[Path]:
    
    print(f"--- Processing Frames for '{seq_id}' (Format: {output_format}, FPS: {fps}) ---")
    
    try:
        frames = list_pngs(frames_dir)
        if not frames:
            print(f"Warning: No '.png' files found in {frames_dir}. Skipping stitch.")
            return None
        
        total_frames = len(frames)
        duration_sec = total_frames / fps

        first_frame_name = frames[0].name
        frame_pattern_match = re.match(r'^(.*_)(\d+)(\.png)$', first_frame_name)
        
        if frame_pattern_match and frame_pattern_match.group(1) == "frame_":
            num_digits = len(frame_pattern_match.group(2))
            frame_pattern = f"frame_%0{num_digits}d.png"
        else:
             if frame_pattern_match:
                prefix = frame_pattern_match.group(1)
                num_digits = len(frame_pattern_match.group(2))
                suffix = frame_pattern_match.group(3)
                frame_pattern = f"{prefix}%0{num_digits}d{suffix}"
             else:
                print(f"Error: Could not determine frame pattern. Skipping.")
                return None

        input_pattern = frames_dir / frame_pattern
        input_path_str = input_pattern.as_posix()
        
    except Exception as e:
        print(f"Error listing frames: {e}")
        return None

    output_format_lower = output_format.lower()
    
    base_command = [
        "ffmpeg",
        "-r", str(fps),
        "-i", input_path_str,
    ]
    
    video_filters = []
    
    if resize and target_width > 0 and target_height > 0:
        video_filters.append(f"scale={target_width}:{target_height}:flags=lanczos")
    
    if output_format_lower == "mp4":
        # Apply Video Filters
        if video_filters:
            base_command.extend(["-vf", ",".join(video_filters)])
        
        # Audio Logic
        if audio_path and Path(audio_path).exists():
            print(f"  > Adding Audio Track: {audio_path}")
            base_command.extend(["-i", str(audio_path)])
            
            # Build Audio Filter Chain
            a_filters = ["apad"]
            
            if duration_sec > 1.0:
                fade_start = duration_sec - 1.0
                a_filters.append(f"afade=t=out:st={fade_start}:d=1.0")
            
            filter_chain = ",".join(a_filters)
            filter_complex = f"[1:a]{filter_chain}[a]"
            
            command = base_command + [
                "-filter_complex", filter_complex,
                "-map", "0:v",      # Map Video Input
                "-map", "[a]",      # Map Processed Audio
                "-shortest",        # Cut at end of shortest stream
                "-c:v", "libx264",
                "-c:a", "aac",      # Re-encode audio
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-y",
                str(output_path)
            ]
        else:
            # Standard Silent MP4
            if audio_path: print(f"  > Warning: Audio path not found: {audio_path}")
            command = base_command + [
                "-c:v", "libx264",       
                "-pix_fmt", "yuv420p",   
                "-y",                    
                str(output_path)
            ]

    elif output_format_lower == "gif":
        temp_mp4 = output_path.with_suffix(".temp.mp4")
        palette_path = output_path.with_suffix(".palette.png")
        
        print("  > Step 1/3: Generating intermediate MP4...")
        cmd_mp4 = base_command + []
        if video_filters:
            cmd_mp4.extend(["-vf", ",".join(video_filters)])
        cmd_mp4.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(temp_mp4)])
        
        res_mp4 = subprocess.run(cmd_mp4, capture_output=True, text=True)
        if res_mp4.returncode != 0:
            print(f"FFmpeg Intermediate MP4 Error: {res_mp4.stderr}")
            return None

        print("  > Step 2/3: Generating palette...")
        cmd_pal = ["ffmpeg", "-v", "error", "-i", str(temp_mp4), "-vf", "palettegen", "-y", str(palette_path)]
        res_pal = subprocess.run(cmd_pal, capture_output=True, text=True)
        if res_pal.returncode != 0:
             print(f"FFmpeg Palette Error: {res_pal.stderr}")
             return None

        print("  > Step 3/3: Rendering final GIF...")
        cmd_gif = [
            "ffmpeg",
            "-i", str(temp_mp4),
            "-i", str(palette_path),
            "-lavfi", "paletteuse",
            "-r", str(fps),
            "-y",
            str(output_path)
        ]
        
        res_gif = subprocess.run(cmd_gif, capture_output=True, text=True)
        
        try:
            if temp_mp4.exists(): temp_mp4.unlink()
            if palette_path.exists(): palette_path.unlink()
        except: pass

        if res_gif.returncode != 0:
            print(f"FFmpeg GIF Error: {res_gif.stderr}")
            return None
            
        return output_path.resolve()

    else:
        return None

    try:
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        if process.returncode != 0:
            print(f"FFmpeg Error: {process.stderr}")
            return None
        else:
            return output_path.resolve()
    except Exception as e:
        print(f"Error: {e}")
        return None0

def assemble_sequence_frames(seq_obj: dict, project_cfg: dict, dest_dir: Path, fps_mult: float, start_index: int = 0, layer_suffix: str = "", temp_extract_base: Path = None) -> int:
    seq_id = seq_obj.get("id", "unknown")
    default_dur = float(project_cfg.get("inbetween_generation", {}).get("duration_default_sec", 3.0))
    base_fps = 16.0  # Could extract from workflow, but hardcode for debug
    
    # V2 Access
    i2v = seq_obj.get("videos") or seq_obj.get("i2v_videos", {})
    if not i2v: return 0

    # V2 Ordering
    if "video_order" in seq_obj:
        ordered_vid_keys = seq_obj["video_order"]
    else:
        ordered_vid_keys = sorted(i2v.keys(), key=lambda k: int(re.sub(r"\D", "", k) or 0))

    # Filter for selected paths
    vids = []
    for k in ordered_vid_keys:
        if k in i2v and i2v[k].get("selected_video_path"):
            vids.append((k, i2v[k]["selected_video_path"], i2v[k]))

    if not vids: return 0

    if DEBUG_FRAMES:
        print(f"\n=== SEQUENCE: {seq_id} ===")

    vid_frame_dirs = []
    for k, sel, vid_obj in vids:
        d = frames_dir_from_selected_video_path(sel, layer_suffix, temp_base=temp_extract_base)
        if not d or not d.exists():
            if layer_suffix:
                print(f"Error: Layer '{layer_suffix}' not found for video {k}. Run Enhance batch first.")
                return 0
            d = frames_dir_from_selected_video_path(sel, "", temp_base=temp_extract_base) 
        vid_frame_dirs.append(d)
        
        if DEBUG_FRAMES:
            actual_frames = len(list_pngs(d)) if d and d.exists() else 0
            configured_dur = vid_obj.get("duration_override_sec") or default_dur
            expected_frames = int(configured_dur * base_fps * fps_mult)
            delta = actual_frames - expected_frames
            print(f"  {k}: config={configured_dur}s, expected={expected_frames}f, actual={actual_frames}f, delta={delta:+d}f ({delta/base_fps/fps_mult:+.2f}s)")
    
    if not vid_frame_dirs or not vid_frame_dirs[0]: return 0

    seq_root = vid_frame_dirs[0].parents[1]
    bridges_root = seq_root / "bridges"
    
    bridge_frame_dirs = []
    cut_instructions = [] 
    
    all_keyframes = seq_obj.get("keyframes") or seq_obj.get("i2v_base_images", {})

    for pair_idx in range(len(vids) - 1):
        a_key = vids[pair_idx][0]
        b_key = vids[pair_idx + 1][0]
        
        latest_bridge_root = find_latest_bridge_dir(bridges_root, a_key, b_key)
        bridge_path = None
        if latest_bridge_root:
            layer_name = f"frames_{layer_suffix}" if layer_suffix else "frames"
            if (latest_bridge_root / layer_name).exists():
                bridge_path = latest_bridge_root / layer_name
            elif (latest_bridge_root / "frames").exists():
                bridge_path = latest_bridge_root / "frames"
        bridge_frame_dirs.append(bridge_path)

        lock_request = 1 
        offset_request = 0
        b_vid_obj = vids[pair_idx + 1][2]
        b_start_kf_id = b_vid_obj.get("start_keyframe_id") or b_vid_obj.get("start_id")
        
        if b_start_kf_id and b_start_kf_id in all_keyframes:
            kf = all_keyframes[b_start_kf_id]
            try: 
                lock_request = int(kf.get("join_smoothing_level", 1))
                offset_request = int(kf.get("join_offset", 1))  ## default to 0 to leave in all frames, 1 clips out one
            except: pass
        
        if not bridge_path or lock_request <= 1:
            # offset_request >= 1 removes duplicate keyframe, 0 keeps both
            cut_head = 1 if offset_request >= 1 else 0
            cut_instructions.append((0, cut_head))
        else:
            LOCK_N, BASE_WIN, BRIDGE_OUT = get_bridge_params(lock_request)
            safe_limit = max(0, BASE_WIN - LOCK_N)
            final_offset = max(-safe_limit, min(safe_limit, offset_request))
            
            detected_mult = fps_mult
            if detected_mult == 1.0 and bridge_path and "_2xf" in str(bridge_path):
                detected_mult = 2.0
            
            input_tail_base = BASE_WIN - final_offset
            input_head_base = BASE_WIN + final_offset
            
            cut_tail = int(input_tail_base * detected_mult)
            cut_head = int(input_head_base * detected_mult)
            cut_instructions.append((cut_tail, cut_head))

    if not dest_dir.exists(): dest_dir.mkdir(parents=True, exist_ok=True)

    frame_counter = start_index
    frames_copied_debug = []
    
    # Process First Video (Head)
    first_frames = list_pngs(vid_frame_dirs[0])
    first_cut_tail = cut_instructions[0][0] if cut_instructions else 0
    
    # Trim first frame if open start
    first_vid_obj = vids[0][2]
    open_start_trim = 1 if first_vid_obj.get("start_keyframe_id") is None else 0
    
    # For single video with open end, also trim last frame
    is_single_video = len(vids) == 1
    open_end_trim = 0
    if is_single_video and first_vid_obj.get("end_keyframe_id") is None:
        open_end_trim = 1
    
    # Apply trims: [open_start_trim : -tail] or [open_start_trim : ] if no tail
    total_tail_trim = first_cut_tail + open_end_trim
    if total_tail_trim > 0:
        frames_to_copy = first_frames[open_start_trim:-total_tail_trim]
    else:
        frames_to_copy = first_frames[open_start_trim:]
    
    before = frame_counter
    frame_counter = _copy_and_rename(frames_to_copy, dest_dir, frame_counter)
    frames_copied_debug.append((vids[0][0], frame_counter - before, total_tail_trim, open_start_trim))
    # Process Interstices
    # Process Interstices
    for i in range(len(bridge_frame_dirs)):
        bridge_dir = bridge_frame_dirs[i]
        vid_dir = vid_frame_dirs[i + 1]

        # Copy Bridge
        bridge_frames_copied = 0
        if bridge_dir:
            before = frame_counter
            frame_counter = _copy_and_rename(list_pngs(bridge_dir), dest_dir, frame_counter)
            bridge_frames_copied = frame_counter - before

        # Copy Next Video Body
        vid_frames = list_pngs(vid_dir)
        if not vid_frames: 
            continue
            
        cut_head_here = cut_instructions[i][1]
        
        is_last = (i == len(bridge_frame_dirs) - 1)
        if is_last:
            # Trim last frame if open end
            last_vid_obj = vids[i + 1][2]
            open_end_trim = 1 if last_vid_obj.get("end_keyframe_id") is None else 0
            if open_end_trim > 0:
                frames_to_copy = vid_frames[cut_head_here:-open_end_trim]
            else:
                frames_to_copy = vid_frames[cut_head_here:]
        else:
            cut_tail_next = cut_instructions[i + 1][0]
            frames_to_copy = vid_frames[cut_head_here:-cut_tail_next] if cut_tail_next > 0 else vid_frames[cut_head_here:]
        before = frame_counter
        frame_counter = _copy_and_rename(frames_to_copy, dest_dir, frame_counter)
        frames_copied_debug.append((vids[i + 1][0], frame_counter - before, cut_instructions[i][0] if i + 1 < len(cut_instructions) else 0, cut_head_here))

    return frame_counter - start_index

# def run_stitch(config_path: str, output_format: str, fps_mult: float = 1.0, resize: bool = False, layer_suffix: str = "", audio_path: str = ""):
def run_stitch(config_path: str, output_format: str, fps_mult: float = 1.0, resize: bool = False, layer_suffix: str = "", audio_path: str = "", animatic: bool = False):
    print(f"Starting stitch process for: {config_path}")
    config_file_path = Path(config_path)
    project_root = config_file_path.parent

    try:
        with open(config_file_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error reading config: {e}")
        sys.exit(1)

    project = config.get("project", {})
    default_dur = float(project.get("inbetween_generation", {}).get("duration_default_sec", 3.0))
    project_name = project.get("name", "untitled_project")
    output_root = project.get("comfy", {}).get("output_root")
    
    target_w = int(project.get("width", 1280))
    target_h = int(project.get("height", 720))

    if not output_root:
        print("Error: 'project.comfy.output_root' is not defined.")
        sys.exit(1)

    base_fps = get_project_fps(config, project_root)
    final_fps = base_fps * fps_mult
    
    output_dir = Path(output_root) / project_name / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)

    # V2 Data Handling: Dict vs List
    # V2 Data Handling: Use sequence_order for proper ordering
    sequences_raw = config.get("sequences")
    sequence_order = config.get("sequence_order", [])
    
    if isinstance(sequences_raw, dict):
        if sequence_order:
            sequences = [sequences_raw[sid] for sid in sequence_order if sid in sequences_raw]
        else:
            # Fallback to old order field for legacy files
            sequences = sorted(sequences_raw.values(), key=lambda x: x.get("order", 0))
    else:
        sequences = sequences_raw or []

    if not sequences:
        print("No sequences found.")
        sys.exit(0)

    successful_files = []
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_frames_path = Path(tmpdir)
            total_frames = 0
            # Determine scale factor for animatic based on layer and quarter_size
            quarter_size = project.get("inbetween_generation", {}).get("quarter_size_video", False)
            if animatic:
                if "2xh" in layer_suffix or "Both" in layer_suffix:
                    animatic_scale = 1.0 if quarter_size else 2.0
                else:
                    animatic_scale = 0.5 if quarter_size else 1.0

            cumulative_time = 0.0
            
            for seq in sequences:
                try:
                    if animatic:
                        frames_added, cumulative_time = assemble_sequence_animatic(
                            seq,
                            project,
                            temp_frames_path,
                            fps=final_fps,
                            start_index=total_frames,
                            scale_factor=animatic_scale,
                            start_time_sec=cumulative_time
                        )
                    else:
                        temp_extract = temp_frames_path / "_lossless_extract"
                        frames_added = assemble_sequence_frames(
                            seq, 
                            project, 
                            temp_frames_path, 
                            fps_mult=fps_mult, 
                            start_index=total_frames, 
                            layer_suffix=layer_suffix,
                            temp_extract_base=temp_extract
                        )
                    total_frames += frames_added
                except TypeError as e:
                    import traceback
                    traceback.print_exc()
                    raise

            if DEBUG_FRAMES:
                print(f"\n{'='*50}")
                print(f"FINAL SUMMARY")
                print(f"{'='*50}")
                print(f"Total frames assembled: {total_frames}")
                print(f"FPS: {final_fps}")
                # print(f"Actual duration: {total_frames / final_fps:.2f}s ({total_frames / final_fps / 60:.0f}:{(total_frames / final_fps) % 60:05.2f})")
                actual_secs = total_frames / final_fps
                print(f"Actual duration: {actual_secs:.2f}s ({int(actual_secs // 60)}:{int(actual_secs % 60):02d})")
                
                # Calculate expected from config
                expected_total = 0.0
                for seq in sequences:
                    for vid_id in seq.get("video_order", []):
                        vid = seq.get("videos", {}).get(vid_id)
                        if vid:
                            dur = vid.get("duration_override_sec") or default_dur
                            expected_total += dur
                # print(f"Expected duration (from config): {expected_total:.2f}s ({expected_total / 60:.0f}:{expected_total % 60:05.2f})")
                print(f"Expected duration (from config): {expected_total:.2f}s ({int(expected_total // 60)}:{int(expected_total % 60):02d})")
                print(f"Delta: {total_frames / final_fps - expected_total:+.2f}s")
                print(f"{'='*50}\n")

            if total_frames == 0:
                print("No frames assembled.")
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                label = project_name
                if len(sequences) == 1:
                    label = f"{project_name}_{sequences[0].get('id','seq')}"

                resize_tag = f"_{target_w}x{target_h}" if resize else ""
                fps_tag = f"_{int(final_fps)}fps"
                
                animatic_tag = "_animatic" if animatic else ""
                output_filename = f"{label}{animatic_tag}{resize_tag}{fps_tag}_{timestamp}.{output_format}"
                # output_filename = f"{label}{resize_tag}{fps_tag}_{timestamp}.{output_format}"
                final_path = output_dir / output_filename
                
                result_path = _run_ffmpeg_stitch(
                    "project", temp_frames_path, final_path, final_fps, output_format,
                    target_width=target_w, target_height=target_h, resize=resize,
                    audio_path=audio_path
                )
                if result_path:
                    successful_files.append(str(result_path))

    except Exception as e:
        print(f"Error: {e}")

    if successful_files:
        print(f"Successfully created {len(successful_files)} file.")
        print(successful_files[-1])
    else:
        print("No files were created.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--format", required=True)
    parser.add_argument("--fps_mult", type=float, default=1.0)
    parser.add_argument("--resize", action="store_true")
    parser.add_argument("--layer", default="", help="Suffix for frames folder (e.g. 2xh, 2xf)")
    parser.add_argument("--audio", default="", help="Path to audio file to overlay (MP4 only)")
    parser.add_argument("--animatic", action="store_true", help="Generate timing preview from keyframes (slide animation)")
    
    args = parser.parse_args()
    
    config_abs = str(Path(args.config).resolve())

    run_stitch(config_abs, args.format, args.fps_mult, args.resize, args.layer, args.audio, args.animatic)