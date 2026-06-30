# Persist approved generation selections to project JSON.

from __future__ import annotations

from pathlib import Path
from typing import Union

import builder

PathLike = Union[str, Path]


def set_selected_keyframe_image(
    project_path: PathLike,
    seq_id: str,
    kf_id: str,
    image_path: str,
) -> dict:
    """Set keyframe selected_image_path and save project."""
    data, fingerprint = builder.load_project_with_fingerprint(project_path)
    seq = data.get("sequences", {}).get(seq_id)
    if not seq:
        raise KeyError(f"Sequence not found: {seq_id}")
    kf = seq.get("keyframes", {}).get(kf_id)
    if not kf:
        raise KeyError(f"Keyframe not found: {kf_id}")
    kf["selected_image_path"] = str(image_path)
    builder.save_project(project_path, data, expected_fingerprint=fingerprint)
    return data


def set_selected_video(
    project_path: PathLike,
    seq_id: str,
    vid_id: str,
    video_path: str,
) -> dict:
    """Set video selected_video_path and save project."""
    data, fingerprint = builder.load_project_with_fingerprint(project_path)
    seq = data.get("sequences", {}).get(seq_id)
    if not seq:
        raise KeyError(f"Sequence not found: {seq_id}")
    vid = seq.get("videos", {}).get(vid_id)
    if not vid:
        raise KeyError(f"Video not found: {vid_id}")
    vid["selected_video_path"] = str(video_path)
    builder.save_project(project_path, data, expected_fingerprint=fingerprint)
    return data
