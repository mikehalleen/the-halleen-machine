"""Clips: cut a shorter segment, lift a still frame, or make an animated GIF from an existing
video (design section 3, "clip and still"). Mechanically identical to an upload — the file lands in ``imports/`` and
joins a bin through the ordinary command layer (design section 8) — except ffmpeg produces
the file instead of a browser upload. See ``thm_server/routes/uploads.py`` for the pattern this
mirrors.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from thm_core import MediaOrigin, Message, adopt_media, ensure_thumbnails, execute, extract_gif, extract_segment, extract_still, thumb_cache_dir
from thm_core.commands import AddMediaToBin
from thm_core.media import ffmpeg_available

from ..deps import actor_context, state
from ..errors import ApiError
from ..imports import find_or_create_bin, fresh_import_path
from ..publish import publish_result
from ..schemas import ClipBody, FlipBody, UploadOut

router = APIRouter(prefix="/api/projects/{slug}", tags=["clips"])

MIN_SEGMENT_S = 0.1
DURATION_SLOP_S = 0.05
# one frame, for a source whose rate could not be probed
DEFAULT_FRAME_S = 1.0 / 24
GIF_MAX_FPS = 60
GIF_MIN_WIDTH = 16
GIF_MAX_WIDTH = 4096


def _written(dest: Path) -> bool:
    return dest.is_file() and dest.stat().st_size > 0


def _extract(body: ClipBody, source_path: str, dest: Path) -> None:
    if body.mode == "segment":
        extract_segment(source_path, body.start_s, body.end_s, dest)
    elif body.mode == "gif":
        extract_gif(source_path, dest, start_s=body.start_s, end_s=body.end_s, fps=body.fps, width=body.width)
    else:
        extract_still(source_path, body.at_s, dest)


@router.post("/clips", response_model=UploadOut)
async def create_clip(slug: str, body: ClipBody, request: Request):
    st = state(request)
    ctx = actor_context(request)
    if ctx.actor == "agent":
        # the agent lifts stills (a passing frame becomes a reference) and exports GIFs the user
        # asks for; cutting stays the user's
        if body.mode == "segment":
            raise ApiError(409, "wrong_actor", "only the user cuts clips; the agent lifts stills and makes GIFs")
        sess = st.registry.get(slug).agent_session
        if sess is None or sess.status != "live":
            raise ApiError(409, "no_session", "an agent still needs a live session; connect first")
    if not ffmpeg_available():
        raise ApiError(409, "ffmpeg_unavailable", "ffmpeg is not on PATH")

    project = st.registry.get(slug)  # 404 before anything is written
    source = project.media_by_id.get(body.source)
    if source is None:
        raise ApiError(404, "unknown_id", f"no media {body.source}", {"type": "media", "id": body.source})
    if source.type != "video":
        raise ApiError(422, "not_video", f"{body.source} is {source.type}, not a video")
    duration = source.duration_s

    if body.mode == "segment":
        if body.start_s is None or body.end_s is None:
            raise ApiError(422, "bad_value", "segment needs start_s and end_s")
        if body.start_s < 0 or body.end_s - body.start_s < MIN_SEGMENT_S:
            raise ApiError(422, "bad_value", f"end_s must be at least {MIN_SEGMENT_S}s after start_s")
        if duration is not None and body.end_s > duration + DURATION_SLOP_S:
            raise ApiError(422, "bad_value", f"end_s is past the source's {duration:.1f}s duration")
        dest_name = f"{Path(source.path).stem}_clip.mp4"
    elif body.mode == "gif":
        start = body.start_s if body.start_s is not None else 0.0
        if start < 0:
            raise ApiError(422, "bad_value", "start_s must be 0 or more")
        if body.end_s is not None:
            if body.end_s - start < MIN_SEGMENT_S:
                raise ApiError(422, "bad_value", f"end_s must be at least {MIN_SEGMENT_S}s after start_s")
            if duration is not None and body.end_s > duration + DURATION_SLOP_S:
                raise ApiError(422, "bad_value", f"end_s is past the source's {duration:.1f}s duration")
        elif duration is not None and start > duration - MIN_SEGMENT_S:
            raise ApiError(422, "bad_value", f"start_s is past the source's {duration:.1f}s duration")
        if body.fps is not None and not (0 < body.fps <= GIF_MAX_FPS):
            raise ApiError(422, "bad_value", f"fps must be above 0 and at most {GIF_MAX_FPS}")
        if body.width is not None and not (GIF_MIN_WIDTH <= body.width <= GIF_MAX_WIDTH):
            raise ApiError(422, "bad_value", f"width must be {GIF_MIN_WIDTH}-{GIF_MAX_WIDTH} px")
        dest_name = f"{Path(source.path).stem}.gif"
    else:
        if body.at_s is None or body.at_s < 0:
            raise ApiError(422, "bad_value", "still needs at_s")
        if duration is not None and body.at_s > duration + DURATION_SLOP_S:
            raise ApiError(422, "bad_value", f"at_s is past the source's {duration:.1f}s duration")
        # the slider's far end is the duration itself, where there is no frame to decode and
        # ffmpeg writes nothing: ask for the last frame instead
        frame_s = 1.0 / source.fps if source.fps else DEFAULT_FRAME_S
        if duration is not None and body.at_s > duration - frame_s:
            body = body.model_copy(update={"at_s": max(0.0, duration - frame_s)})
        dest_name = f"{Path(source.path).stem}_frame.png"

    dest = fresh_import_path(st.store.files_dir(project), dest_name)
    try:
        await run_in_threadpool(_extract, body, source.path, dest)
        if body.mode == "still" and not _written(dest) and body.at_s > 0:
            # a rounded duration can still sit past the last decodable frame: one frame earlier
            body = body.model_copy(update={"at_s": max(0.0, body.at_s - frame_s)})
            await run_in_threadpool(_extract, body, source.path, dest)
        if not _written(dest):
            raise RuntimeError("ffmpeg produced no output at that time; try a little earlier")
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise ApiError(500, "clip_failed", str(exc)) from None

    if body.mode == "still":
        origin_type, origin_kwargs = "frame", {"at_s": body.at_s}
    elif body.mode == "gif":
        parts = [f"{body.fps:g} fps" if body.fps else None, f"{body.width} px wide" if body.width else None]
        origin_type = "gif"
        origin_kwargs = {"start_s": body.start_s, "end_s": body.end_s, "note": "gif, " + ", ".join(p for p in parts if p) if any(parts) else "gif"}
    else:
        origin_type, origin_kwargs = "clip", {"start_s": body.start_s, "end_s": body.end_s}

    return await _land(st, slug, ctx, dest, body.bin, body.bin_name, origin_type=origin_type, source=body.source, **origin_kwargs)


async def _land(st, slug: str, ctx, dest: Path, bin_id: str | None, bin_name: str | None, *, origin_type: str, source: str, **origin_kwargs) -> UploadOut:
    """Adopt a file THM just made from another media and add it to a bin, like an upload."""
    change = None
    messages: list[Message] = []
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        try:
            target, messages = find_or_create_bin(st.store, project, ctx, st.env, bin_id, bin_name)
            media = adopt_media(
                project,
                dest,
                origin=MediaOrigin(type=origin_type, source=source, bin=target.id, **origin_kwargs),
            )
            if Path(media.path) != dest.resolve():
                dest.unlink(missing_ok=True)  # byte-identical to a file already indexed; keep the original
            try:
                await run_in_threadpool(ensure_thumbnails, media, thumb_cache_dir(st.store, project))
            except Exception:
                pass
            if media.id in target.media:
                version = st.registry.version(slug)
            else:
                result = await run_in_threadpool(
                    execute, st.store, project, AddMediaToBin(bin=target.id, media=media.id), ctx, env=st.env
                )
                change = result.change
                messages += result.messages
                version = st.registry.touch(slug)
        except Exception:
            # the cached project may carry an unsaved media record: reload it next time
            st.registry.invalidate(slug)
            dest.unlink(missing_ok=True)
            raise
    publish_result(st, slug, project, messages=messages, change=change, generation=None, version=version)
    return UploadOut(media=media, bin=target, change=change, project_version=version)


def _mirror(source_path: str, dest: Path) -> None:
    """A left-right mirror of a still, in the source's own format; EXIF orientation applied
    first so the flip is of the picture as it is seen."""
    from PIL import Image, ImageOps

    with Image.open(source_path) as im:
        fmt = im.format
        out = ImageOps.mirror(ImageOps.exif_transpose(im))
        save = {"quality": 95} if fmt in ("JPEG", "WEBP") else {}
        out.save(dest, format=fmt, **save)


@router.post("/media/{media_id}/flip", response_model=UploadOut)
async def flip_media(slug: str, media_id: str, body: FlipBody, request: Request):
    """A horizontally flipped copy of a still, as new media in the bin (the source's, unless
    another is named). The original stays as it was; the copy says what it was made from."""
    st = state(request)
    ctx = actor_context(request)
    if ctx.actor == "agent":
        sess = st.registry.get(slug).agent_session
        if sess is None or sess.status != "live":
            raise ApiError(409, "no_session", "an agent flip needs a live session; connect first")
    project = st.registry.get(slug)
    source = project.media_by_id.get(media_id)
    if source is None:
        raise ApiError(404, "unknown_id", f"no media {media_id}", {"type": "media", "id": media_id})
    if source.type != "image" or Path(source.path).suffix.lower() == ".gif":
        raise ApiError(422, "bad_value", f"{media_id} is not a still image")
    dest = fresh_import_path(st.store.files_dir(project), f"{Path(source.path).stem}_flip{Path(source.path).suffix}")
    try:
        await run_in_threadpool(_mirror, source.path, dest)
        if not _written(dest):
            raise RuntimeError("nothing was written")
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise ApiError(500, "flip_failed", str(exc)) from None
    home = body.bin or (source.origin.bin if source.origin.bin in project.bins_by_id else None) or next(
        (b.id for b in project.bins_by_id.values() if media_id in b.media), None
    )
    return await _land(st, slug, ctx, dest, home, None, origin_type="upload", source=media_id, note="flipped horizontally")
