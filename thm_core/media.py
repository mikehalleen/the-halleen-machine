"""Media pipeline: hash, probe, thumbnails, adopt (design sections 4 and 8).

Media files stay where ComfyUI wrote them. A record carries the absolute path,
a content hash, and probed dimensions; thumbnails and posters are derived into a
disposable cache keyed by that hash:

    <files dir>/cache/thumbs/<sha>_grid.webp     ~320 px long edge
    <files dir>/cache/thumbs/<sha>_detail.webp   ~1280 px long edge
    <files dir>/cache/thumbs/<sha>_poster.jpg    one video frame (videos only)

Pillow handles images; ffprobe and ffmpeg (on PATH) handle video. Nothing here
prints, and probe failures degrade to ``None`` fields rather than raising.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from . import ids
from .ids import utc_now
from .models import Media, MediaOrigin, MediaType, Project

if TYPE_CHECKING:  # pragma: no cover
    from .store import ProjectStore

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
GRID_EDGE = 320
DETAIL_EDGE = 1280
THUMB_SIZES = ("grid", "detail", "poster")
_EDGES = {"grid": GRID_EDGE, "detail": DETAIL_EDGE}
_EMPTY_PROBE = {"width": None, "height": None, "duration_s": None, "fps": None, "frames": None}


# -- identity ------------------------------------------------------------------


def media_type_for(path) -> MediaType | None:
    ext = Path(str(path)).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def sha256_file(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# -- probe ---------------------------------------------------------------------


def probe_image(path) -> tuple[int, int]:
    with Image.open(path) as im:
        return int(im.size[0]), int(im.size[1])


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _fraction(value) -> float | None:
    try:
        text = str(value)
        if "/" in text:
            a, b = text.split("/", 1)
            den = float(b)
            return float(a) / den if den else None
        return float(text)
    except (TypeError, ValueError):
        return None


def _positive(value) -> float | None:
    f = _fraction(value)
    return f if f is not None and f > 0 else None


def probe_video(path) -> dict:
    """``{"width", "height", "duration_s", "fps", "frames"}``; every value None on any failure."""
    out = dict(_EMPTY_PROBE)
    exe = shutil.which("ffprobe")
    if not exe:
        return out
    cmd = [
        exe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames,duration",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    streams = data.get("streams") or []
    stream = streams[0] if streams and isinstance(streams[0], dict) else {}
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    try:
        w, h = stream.get("width"), stream.get("height")
        out["width"] = int(w) if w else None
        out["height"] = int(h) if h else None
    except (TypeError, ValueError):
        pass
    out["fps"] = _positive(stream.get("r_frame_rate"))
    out["duration_s"] = _positive(stream.get("duration")) or _positive(fmt.get("duration"))
    frames = None
    try:
        nb = int(stream.get("nb_frames") or 0)
        frames = nb if nb > 0 else None
    except (TypeError, ValueError):
        frames = None
    if frames is None and out["duration_s"] and out["fps"]:
        frames = int(round(out["duration_s"] * out["fps"]))
    out["frames"] = frames
    return out


# -- thumbnails ----------------------------------------------------------------


def thumb_cache_dir(store: "ProjectStore", project: Project | str) -> Path:
    return store.files_dir(project) / "cache" / "thumbs"


def thumb_paths(cache_dir, sha: str) -> dict[str, Path]:
    d = Path(cache_dir)
    return {
        "grid": d / f"{sha}_grid.webp",
        "detail": d / f"{sha}_detail.webp",
        "poster": d / f"{sha}_poster.jpg",
    }


def _tmp_for(final: Path) -> Path:
    return final.with_name(f"{final.name}.tmp-{os.getpid()}")


def _save_webp_thumb(source: Image.Image, edge: int, final: Path) -> None:
    im = source.copy()
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if im.mode in ("P", "LA", "PA") else "RGB")
    im.thumbnail((edge, edge), Image.LANCZOS)
    tmp = _tmp_for(final)
    im.save(tmp, "WEBP", quality=82, method=4)
    os.replace(tmp, final)


def _make_poster(media: Media, final: Path) -> bool:
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    duration = media.duration_s or 0.0
    first = min(0.5, 0.10 * duration) if duration > 0 else 0.0
    seeks = [first, 0.0] if first > 0 else [0.0]
    for seek in seeks:
        tmp = _tmp_for(final)
        cmd = [
            exe, "-y", "-v", "error", "-ss", f"{seek:.3f}", "-i", media.path,
            "-frames:v", "1", "-c:v", "mjpeg", "-q:v", "3", "-f", "image2", str(tmp),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
        except Exception:
            pass
        if tmp.is_file() and tmp.stat().st_size > 0:
            os.replace(tmp, final)
            return True
        if tmp.exists():
            tmp.unlink()
    return False


SHEET_MAX_FRAMES = 8
SHEET_COLUMNS = 4


def _grab_frame(exe: str, media: Media, at_s: float, dest: Path) -> bool:
    cmd = [exe, "-y", "-v", "error", "-ss", f"{at_s:.3f}", "-i", media.path, "-frames:v", "1", "-c:v", "mjpeg", "-q:v", "3", "-f", "image2", str(dest)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
    except Exception:
        return False
    return dest.is_file() and dest.stat().st_size > 0


def contact_sheet(media: Media, cache_dir, *, n: int = 4, edge: int = 1024) -> Path:
    """``n`` frames of a video, evenly spaced from the first to the last, tiled into one webp
    with the time stamped on each: the agent's way to see a clip's motion, not one poster.

    Cached beside the thumbnails by content hash, frame count and edge. Each frame is scaled
    to ``edge`` on its long side before tiling (a vision read does not need native pixels);
    rows of ``SHEET_COLUMNS``. Raises ``FileNotFoundError`` for a missing source and
    ``RuntimeError`` when ffmpeg is missing or no frame could be read.
    """
    from PIL import ImageDraw

    src = Path(media.path)
    if not src.is_file():
        raise FileNotFoundError(media.path)
    if media.type != "video":
        raise RuntimeError("contact sheets are for video")
    n = max(1, min(int(n), SHEET_MAX_FRAMES))
    edge = max(64, int(edge))
    if not media.sha256:
        media.sha256 = sha256_file(src)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    final = cache / f"{media.sha256}_sheet{n}_{edge}.webp"
    if final.is_file():
        return final
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    duration = media.duration_s or 0.0
    frame_s = (1.0 / media.fps) if media.fps else 1.0 / 24
    last = max(0.0, duration - frame_s) if duration > 0 else 0.0
    times = [0.0] if n == 1 else [last * i / (n - 1) for i in range(n)]
    frames: list[tuple[float, Image.Image]] = []
    for i, t in enumerate(times):
        tmp = _tmp_for(final.with_name(f"{final.stem}_{i}.jpg"))
        ok = _grab_frame(exe, media, t, tmp) or (t > 0 and _grab_frame(exe, media, max(0.0, t - frame_s), tmp))
        if ok:
            with Image.open(tmp) as im:
                im.load()
                fr = im.convert("RGB")
            fr.thumbnail((edge, edge), Image.LANCZOS)
            frames.append((t, fr))
        if tmp.exists():
            tmp.unlink()
    if not frames:
        raise RuntimeError("no frame could be read from the video")
    w = max(f.width for _, f in frames)
    h = max(f.height for _, f in frames)
    cols = min(SHEET_COLUMNS, len(frames))
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    for i, (t, fr) in enumerate(frames):
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(fr, (x, y))
        label = f"{t:.2f}s"
        draw.rectangle((x + 4, y + 4, x + 12 + 7 * len(label), y + 20), fill=(0, 0, 0))
        draw.text((x + 8, y + 6), label, fill=(255, 255, 255))
    tmp = _tmp_for(final)
    sheet.save(tmp, "WEBP", quality=82, method=4)
    os.replace(tmp, final)
    return final


def ensure_thumbnails(media: Media, cache_dir, *, sizes=None) -> dict[str, Path]:
    """Make the missing derived files for ``media``; return ``{size: path}`` for the ones that exist.

    Idempotent. Raises ``FileNotFoundError`` when the source file is gone. Sets
    ``media.sha256`` when it was missing. For an image, ``poster`` means ``detail``.
    """
    src = Path(media.path)
    if not src.is_file():
        raise FileNotFoundError(media.path)
    if not media.sha256:
        media.sha256 = sha256_file(src)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    paths = thumb_paths(cache, media.sha256)

    wanted = list(sizes or THUMB_SIZES)
    if media.type == "image":
        wanted = ["detail" if s == "poster" else s for s in wanted]
    wanted = [s for s in dict.fromkeys(wanted) if s in THUMB_SIZES]
    missing = [s for s in wanted if not paths[s].is_file()]

    if missing and media.type == "image":
        with Image.open(src) as im:
            im.load()
            for s in missing:
                _save_webp_thumb(im, _EDGES[s], paths[s])
    elif missing:
        poster = paths["poster"]
        if not poster.is_file():
            _make_poster(media, poster)
        if poster.is_file():
            with Image.open(poster) as im:
                im.load()
                for s in missing:
                    if s != "poster":
                        _save_webp_thumb(im, _EDGES[s], paths[s])

    return {s: paths[s] for s in wanted if paths[s].is_file()}


# -- adopt ---------------------------------------------------------------------


def adopt_media(project: Project, path, *, type: MediaType | None = None, origin: MediaOrigin, now: str | None = None) -> Media:
    """Index a file as a media record on ``project``; dedupe by content hash.

    Fills the probed fields. Adds nothing to any item and does not save.
    """
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(str(path))
    mtype = type or media_type_for(p)
    if mtype not in ("image", "video"):
        raise ValueError(f"unsupported media type: {p.suffix or p.name}")
    sha = sha256_file(p)
    for existing in project.media_by_id.values():
        if existing.sha256 == sha:
            return existing
    fields: dict = {}
    if mtype == "image":
        try:
            w, h = probe_image(p)
            fields = {"width": w, "height": h}
        except Exception:
            fields = {}
    else:
        fields = probe_video(p)
    media = Media(
        id=ids.new_id(ids.MEDIA),
        type=mtype,
        path=p.as_posix(),
        sha256=sha,
        created=now or utc_now(),
        origin=origin,
        **fields,
    )
    project.media_by_id[media.id] = media
    return media
