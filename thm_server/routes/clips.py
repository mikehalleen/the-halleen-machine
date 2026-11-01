"""Clips: cut a shorter segment or lift a still frame from an existing video (design section 3,
"clip and still"). Mechanically identical to an upload — the file lands in ``imports/`` and
joins a bin through the ordinary command layer (design section 8) — except ffmpeg produces
the file instead of a browser upload. See ``thm_server/routes/uploads.py`` for the pattern this
mirrors.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from thm_core import MediaOrigin, Message, adopt_media, ensure_thumbnails, execute, extract_segment, extract_still, thumb_cache_dir
from thm_core.commands import AddMediaToBin
from thm_core.media import ffmpeg_available

from ..deps import actor_context, state
from ..errors import ApiError
from ..imports import find_or_create_bin, fresh_import_path
from ..publish import publish_result
from ..schemas import ClipBody, UploadOut

router = APIRouter(prefix="/api/projects/{slug}", tags=["clips"])

MIN_SEGMENT_S = 0.1
DURATION_SLOP_S = 0.05
# one frame, for a source whose rate could not be probed
DEFAULT_FRAME_S = 1.0 / 24


def _written(dest: Path) -> bool:
    return dest.is_file() and dest.stat().st_size > 0


def _extract(body: ClipBody, source_path: str, dest: Path) -> None:
    if body.mode == "segment":
        extract_segment(source_path, body.start_s, body.end_s, dest)
    else:
        extract_still(source_path, body.at_s, dest)


@router.post("/clips", response_model=UploadOut)
async def create_clip(slug: str, body: ClipBody, request: Request):
    st = state(request)
    ctx = actor_context(request)
    if ctx.actor == "agent":
        # the agent lifts stills (a passing frame becomes a reference); cutting stays the user's
        if body.mode != "still":
            raise ApiError(409, "wrong_actor", "only the user cuts clips; the agent lifts stills")
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
        raise ApiError(422, "bad_value", f"{body.source} is not a video")
    duration = source.duration_s

    if body.mode == "segment":
        if body.start_s is None or body.end_s is None:
            raise ApiError(422, "bad_value", "segment needs start_s and end_s")
        if body.start_s < 0 or body.end_s - body.start_s < MIN_SEGMENT_S:
            raise ApiError(422, "bad_value", f"end_s must be at least {MIN_SEGMENT_S}s after start_s")
        if duration is not None and body.end_s > duration + DURATION_SLOP_S:
            raise ApiError(422, "bad_value", f"end_s is past the source's {duration:.1f}s duration")
        dest_name = f"{Path(source.path).stem}_clip.mp4"
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

    origin_kwargs = {"start_s": body.start_s, "end_s": body.end_s} if body.mode == "segment" else {"at_s": body.at_s}

    change = None
    messages: list[Message] = []
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        try:
            target, messages = find_or_create_bin(st.store, project, ctx, st.env, body.bin, body.bin_name)
            media = adopt_media(
                project,
                dest,
                origin=MediaOrigin(type="clip" if body.mode == "segment" else "frame", source=body.source, bin=target.id, **origin_kwargs),
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
