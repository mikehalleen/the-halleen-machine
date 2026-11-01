"""The clipper: cut a shorter segment or lift a still frame from a video (design section 3,
"Clip and still"). The source file is untouched; the result is adopted like any other media
(``thm_core.media.adopt_media``) with a ``clip`` or ``frame`` origin.

Mirrors ``media.py``'s ``_make_poster`` for the ffmpeg invocation style: a plain
``subprocess.run``, ``ffmpeg`` resolved off PATH, no shell.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def extract_segment(source: str, start_s: float, end_s: float, dest: str | Path) -> None:
    """Cut ``[start_s, end_s)`` of ``source`` into ``dest``, re-encoded to a browser-playable
    ``.mp4`` regardless of the source container. ``-ss``/``-to`` are placed after ``-i`` so
    ``-to`` is an absolute source timestamp and the cut is frame-accurate — source clips are a
    few seconds, so decode-and-cut is cheap.
    """
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe, "-y", "-v", "error", "-i", str(source),
        "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-movflags", "+faststart", str(dest),
    ]
    subprocess.run(cmd, capture_output=True, timeout=120, check=True)


def extract_still(source: str, at_s: float, dest: str | Path) -> None:
    """Lift the frame at ``at_s`` from ``source`` into ``dest`` (a ``.png``)."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-y", "-v", "error", "-i", str(source), "-ss", f"{at_s:.3f}", "-frames:v", "1", str(dest)]
    subprocess.run(cmd, capture_output=True, timeout=60, check=True)
