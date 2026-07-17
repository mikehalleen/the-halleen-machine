# scripts/run_export.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Generates an FCPXML timeline from existing video clips based on a project JSON.

This is a standalone script that only performs the export function, using the
same logic and file formats as the original run_video.py script.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple

# --- Constants and Helpers ---

TRIM_SE_EACH_SIDE = 2
TRIM_O_ONE_SIDE   = 1
GENEROUS_ASSET_SIXTEENTHS = 2000

def jload(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)

def list_videos(folder):
    if not os.path.isdir(folder):
        return []
    exts = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
    return sorted([str(Path(folder, f)) for f in os.listdir(folder) if f.lower().endswith(exts)])

def get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def iter_video_entries(seq: dict):
    """
    Yield (vid_index:int, key:str, conf:dict) for video entries.
    V2: Uses 'video_order' and 'videos'.
    V1: Scans 'i2v_videos' keys.
    """
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

def to_file_url(path_str: str) -> str:
    try:
        return Path(path_str).as_uri()
    except Exception:
        p = path_str.replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", p):
            p = "/" + p
        return "file://" + p

def sixteen_str(n: int) -> str:
    return f"{int(n)}/16s"

# --- FCPXML Generation Function ---

def write_fcpxml(project_name, project_width, project_height, sequences_clips, out_root, fps_for_format):
    def frames_to_grid(fr): return int(round((float(fr) / float(fps_for_format)) * 16.0))
    
    def transition_allowed(prev_t, next_t):
        return (prev_t, next_t) in {("OE","SE"), ("SE","SE"), ("SE","SO")}

    DISSOLVE_LEN_GRID = 2

    ts = time.strftime("%Y%m%d_%H%M%S")
    proj_dir = os.path.join(out_root, project_name)
    ensure_dir(proj_dir)
    out_path = os.path.join(proj_dir, f"{project_name}_timeline_{ts}.xml")

    assets_map = {}
    for s in sequences_clips:
        for v in s["vids"]:
            for c in v["clips"]:
                assets_map[c["asset_ref"]] = (c["name"], to_file_url(c["path"]))
    assets_xml = "\n    ".join(
        f'<asset id="{aid}" name="{nm}" src="{src}" start="0s" duration="{sixteen_str(2000)}" hasVideo="1"/>'
        for aid, (nm, src) in assets_map.items()
    )
    dissolve_effect_xml = '<effect id="x_diss" name="Cross Dissolve" uid=".../transition/generic/Cross Dissolve"/>'

    def vid_visible_len_grid(v):
        for c in v.get("clips", []):
            media_frames = int(c["media_frames"])
            trim_start = int(c["trim_start"]); trim_end = int(c["trim_end"])
            vis_frames = max(1, media_frames - trim_start - trim_end)
            return frames_to_grid(vis_frames)
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
                    join_grid = inner_offset
                    trans_offset = max(0, join_grid - (DISSOLVE_LEN_GRID // 2))
                    inner_xml_parts.append(
                        f'<transition ref="x_diss" offset="{sixteen_str(trans_offset)}" duration="{sixteen_str(DISSOLVE_LEN_GRID)}"/>'
                    )

                if lane_idx < len(clips):
                    c = clips[lane_idx]
                    media_frames = int(c["media_frames"])
                    trim_start_f = int(c["trim_start"])
                    trim_end_f   = int(c["trim_end"])
                    vis_frames = max(1, media_frames - trim_start_f - trim_end_f)
                    vis_grid   = frames_to_grid(vis_frames)
                    left_h  = 1 if (vid_i > 0 and transition_allowed(prev_type, v.get("type"))) else 0
                    right_h = 1 if (vid_i < len(vlist)-1 and transition_allowed(v.get("type"), next_type)) else 0
                    start_in_asset_f = max(0, trim_start_f - left_h)
                    used_frames_with_handles = min(media_frames - start_in_asset_f, vis_frames + left_h + right_h)
                    start_in_asset_grid = frames_to_grid(start_in_asset_f)
                    media_used_grid     = frames_to_grid(used_frames_with_handles)
                    inner_xml_parts.append(
                        f'<clip name="{c["name"]}" offset="{sixteen_str(inner_offset)}" duration="{sixteen_str(vis_grid)}">'
                        f'<video ref="{c["asset_ref"]}" start="{sixteen_str(start_in_asset_grid)}" duration="{sixteen_str(media_used_grid)}"/>'
                        f'</clip>'
                    )
                    inner_offset += vis_grid
                else:
                    inner_offset += vid_visible_len_grid(v)
                prev_type = v.get("type")
            
            lane_xml = (
                f'<clip name="Lane {lane_idx+1}" lane="{lane_idx+1}" '
                f'offset="{sixteen_str(cumulative_grid)}" duration="{sixteen_str(seq_len_grid)}" '
                f'start="0s" format="fmt1">'
                f'<spine>' + "".join(inner_xml_parts) + '</spine>'
                f'</clip>'
            )
            spine_items_xml.append(lane_xml)
        cumulative_grid += seq_len_grid

    total_len_grid = max(1, cumulative_grid)
    seq_duration = sixteen_str(total_len_grid)
    all_lane_xml = "\n              ".join(spine_items_xml)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="fmt1" frameDuration="1/16s" width="{project_width}" height="{project_height}" colorSpace="1-1-1 (Rec. 709)"/>
    {dissolve_effect_xml}
    {assets_xml}
  </resources>
  <library>
    <event name="Generated">
      <project name="{project_name}">
        <sequence duration="{seq_duration}" format="fmt1">
          <spine>
            <gap name="Primary" duration="{seq_duration}">
              {all_lane_xml}
            </gap>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"[EXPORT] FCPXML written -> {out_path}")
    return str(out_path.resolve())

# --- Main Execution Logic ---
def run_export(config_path: str):
    """Main orchestration function for the export process."""
    print(f"Starting export process for: {config_path}")
    
    try:
        config = jload(config_path)
        project = config.get("project", {})
        sequences_container = config.get("sequences", [])
        
        out_root = get(project, "comfy", "output_root")
        project_name = get(project, "name")
        full_w = int(get(project, "width", default=1280))
        full_h = int(get(project, "height", default=720))
        dur_def_sec = float(get(project, "inbetween_generation", "duration_default_sec", default=3.0))
        project_fps = 24.0

        if not all([out_root, project_name]):
            print("Error: 'output_root' or 'name' not found in project JSON.")
            sys.exit(1)

        print("Scanning for existing video files...")
        export_collect = []
        asset_id_counter = 1
        
        # V2 Data Handling: Dict vs List
        if isinstance(sequences_container, dict):
            # Sort V2 dict values by 'order'
            sequences = sorted(sequences_container.values(), key=lambda x: x.get("order", 0))
        else:
            sequences = sequences_container

        for seq in sequences:
            seq_id = (seq.get("id") or seq.get("name") or "").strip()
            if not seq_id: continue

            seq_export = {"seq_id": seq_id, "vids": []}
            
            entries = iter_video_entries(seq)
            for vid_idx, vid_key, vid_conf in entries:
                # In V2, vid_key is the ID. In V1, it's the dict key (also 'vidN').
                vid_id = vid_conf.get("id", vid_key)
                
                vid_folder = os.path.join(out_root, project_name, seq_id, vid_id)
                
                files = list_videos(vid_folder)
                if not files:
                    continue

                # Determine clip type for trimming logic
                start_id, end_id = vid_conf.get("start_keyframe_id", vid_conf.get("start_id")), vid_conf.get("end_keyframe_id", vid_conf.get("end_id"))
                clip_type = "SE" if start_id and end_id else "OE" if end_id else "SO"
                
                dur_sec_this = float(vid_conf.get("duration_override_sec", dur_def_sec))
                media_frames = int(round(dur_sec_this * project_fps)) + 1
                trim_start = TRIM_SE_EACH_SIDE if clip_type == "SE" else (TRIM_O_ONE_SIDE if clip_type == "SO" else 0)
                trim_end = TRIM_SE_EACH_SIDE if clip_type == "SE" else (TRIM_O_ONE_SIDE if clip_type == "OE" else 0)

                clips_for_vid = []
                for f in files:
                    asset_ref = f"r{asset_id_counter:04d}"
                    asset_id_counter += 1
                    clips_for_vid.append({
                        "path": f, "name": os.path.basename(f),
                        "media_frames": media_frames, "trim_start": trim_start, "trim_end": trim_end,
                        "asset_ref": asset_ref
                    })
                
                if clips_for_vid:
                    seq_export["vids"].append({
                        "vid_key": vid_id, "type": clip_type, "clips": clips_for_vid
                    })
            
            if seq_export["vids"]:
                export_collect.append(seq_export)
        
        if not export_collect:
            print("Error: No generated video clips were found to export.")
            sys.exit(1)
        
        print(f"Found clips from {len(export_collect)} sequence(s). Generating timeline...")
        final_path = write_fcpxml(project_name, full_w, full_h, export_collect, out_root, project_fps)
        
        # On success, print the final path as the last line
        print(final_path)

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate an FCPXML timeline from a project's existing video files.")
    parser.add_argument("--config", required=True, help="The absolute path to the project's .json configuration file.")
    
    args = parser.parse_args()
    print("running")
    run_export(args.config)