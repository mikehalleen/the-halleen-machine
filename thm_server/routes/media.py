"""Media index, file serving with Range, and lazily derived thumbnails."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from thm_core import Media, Project, ensure_thumbnails, thumb_cache_dir
from thm_core.commands import CommandRefused, clear_unseen_all, clear_unseen_for
from thm_core.media import SHEET_MAX_FRAMES, contact_sheet
from thm_core.models import MediaType
from thm_core.workflow_names import resolve_curated_workflow

from ..deps import state
from ..derive import newest_first
from ..errors import ApiError
from ..recover import recover_media
from ..schemas import BinSeenOut, MediaPage, RecipeOut, SeenManyIn, SeenManyOut, SeenOut

router = APIRouter(tags=["media"])


@router.post("/api/projects/{slug}/media/{media_id}/seen", response_model=SeenOut)
async def mark_seen(slug: str, media_id: str, request: Request):
    """The user has looked at this media: every bin stops marking it as new. Not a change,
    so nothing is logged; a media no bin marks is a no-op that saves nothing."""
    st = state(request)
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        _media_or_404(project, media_id)
        cleared = [b.id for b in project.bins_by_id.values() if media_id in b.unseen]
        for bin_id in cleared:
            b = project.bins_by_id[bin_id]
            b.unseen = [m for m in b.unseen if m != media_id]
        if cleared:
            await run_in_threadpool(st.store.save, project)
            version = st.registry.touch(slug)
        else:
            version = st.registry.version(slug)
    if cleared:
        st.bus.publish(slug, version)
    return SeenOut(media=media_id, cleared=cleared, project_version=version)

@router.post("/api/projects/{slug}/media/seen", response_model=SeenManyOut)
async def mark_many_seen(slug: str, body: SeenManyIn, request: Request):
    """The agent shows the user these media inside something they were given (an assembly):
    their new markers go, and so do those of everything they were made from."""
    st = state(request)
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        try:
            cleared = await run_in_threadpool(
                clear_unseen_all if body.all else clear_unseen_for, st.store, project, *([] if body.all else [body.media])
            )
        except CommandRefused as e:
            raise ApiError(404, e.code, e.detail) from e
        version = st.registry.touch(slug) if cleared else st.registry.version(slug)
    if cleared:
        st.bus.publish(slug, version)
    return SeenManyOut(cleared=cleared, project_version=version)


@router.post("/api/projects/{slug}/bins/{bin_id}/seen", response_model=BinSeenOut)
async def clear_bin_seen(slug: str, bin_id: str, request: Request):
    """The user dismissed every new marker in this bin, whatever the media type."""
    st = state(request)
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        bin_ = project.bins_by_id.get(bin_id)
        if bin_ is None:
            raise ApiError(404, "unknown_id", f"no bin {bin_id}")
        cleared = list(bin_.unseen)
        if cleared:
            bin_.unseen = []
            await run_in_threadpool(st.store.save, project)
            version = st.registry.touch(slug)
        else:
            version = st.registry.version(slug)
    if cleared:
        st.bus.publish(slug, version)
    return BinSeenOut(bin=bin_id, cleared=cleared, project_version=version)


_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}
IMMUTABLE = "private, max-age=31536000, immutable"


def _media_or_404(project: Project, media_id: str) -> Media:
    media = project.media_by_id.get(media_id)
    if media is None:
        raise ApiError(404, "unknown_id", f"no media {media_id}", {"type": "media", "id": media_id})
    return media


def _mime_for(path: Path) -> str:
    return _MIME.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


@router.get("/api/projects/{slug}/media", response_model=MediaPage)
async def list_media(
    slug: str,
    request: Request,
    bin: str | None = None,
    type: MediaType | None = None,
    cursor: str | None = None,
    limit: int = Query(60, ge=1, le=200),
):
    st = state(request)
    project = st.registry.get(slug)
    if bin:
        b = project.bins_by_id.get(bin)
        if b is None:
            raise ApiError(404, "unknown_id", f"no bin {bin}", {"type": "bin", "id": bin})
        base = [project.media_by_id[m] for m in b.media if m in project.media_by_id]
    else:
        base = list(project.media_by_id.values())
    if type:
        base = [m for m in base if m.type == type]
    base = newest_first(base)  # the picker and the session refs read newest first, same as every media list
    try:
        offset = max(0, int(cursor)) if cursor else 0
    except ValueError:
        raise ApiError(422, "bad_value", "cursor must be an integer offset") from None
    page = base[offset : offset + limit]
    next_cursor = str(offset + limit) if offset + limit < len(base) else None
    return MediaPage(items=page, next_cursor=next_cursor, total=len(base))


@router.get("/api/projects/{slug}/media/{media_id}", response_model=Media)
async def get_media(slug: str, media_id: str, request: Request):
    st = state(request)
    return _media_or_404(st.registry.get(slug), media_id)


@router.get("/api/projects/{slug}/media/{media_id}/recipe", response_model=RecipeOut)
async def media_recipe(slug: str, media_id: str, request: Request):
    """The settings that produced this media, for recall into a bin.

    Read straight through the media's origin to the generation record rather than keeping a copy
    on the media, so a rename or a re-read never leaves the two disagreeing. Media with no record
    behind it is read from the file itself: a ComfyUI output carries the graph that made it, and
    the record is rebuilt from that on first ask (``recover``). A clip, a still, or an upload
    from elsewhere carries nothing and answers found=False.
    """
    st = state(request)
    project = st.registry.get(slug)
    media = _media_or_404(project, media_id)
    gen_id = media.origin.generation if media.origin.type == "generation" else None
    gen = st.store.read_generation(project, gen_id) if gen_id else None
    if gen is None:
        async with st.registry.lock(slug):
            project = st.registry.get(slug)
            media = _media_or_404(project, media_id)
            rec = await run_in_threadpool(recover_media, st, slug, project, media)
            if rec is not None:
                await run_in_threadpool(st.store.save, project)
                version = st.registry.touch(slug)
        if rec is None:
            return RecipeOut()
        st.bus.publish(slug, version)
        gen = rec.generation
    return _recipe(st, project, gen)


@router.get("/api/projects/{slug}/generations/{gen_id}/recipe", response_model=RecipeOut)
async def generation_recipe(slug: str, gen_id: str, request: Request):
    """The settings of a generation that has no take yet -- rendering now, or saved for later --
    for recall into a bin, so the bin can be used for something else meanwhile and come back."""
    st = state(request)
    project = st.registry.get(slug)
    gen = st.store.read_generation(project, gen_id)
    if gen is None:
        raise ApiError(404, "unknown_id", f"no generation {gen_id}", {"type": "generation", "id": gen_id})
    return _recipe(st, project, gen)


def _recipe(st, project: Project, gen) -> RecipeOut:
    wanted = [r for r in gen.references if r.media]
    kept = [r for r in wanted if r.media in project.media_by_id]
    seed = gen.params.get("seed")
    length = gen.params.get("length_s")
    return RecipeOut(
        found=True,
        generation=gen.id,
        bin=gen.bin,
        kind=gen.kind,
        workflow=gen.workflow,
        workflow_missing=resolve_curated_workflow(gen.workflow, st.settings.workflows_dir) is None,
        prompt=gen.prompt_typed,
        negative=gen.negative_submitted,
        references=[r.media for r in kept],
        handles=[r.handle for r in kept],
        missing=[r.media for r in wanted if r.media not in project.media_by_id],
        loras=list(gen.loras),
        seed=int(seed) if isinstance(seed, (int, float)) else None,
        length_s=float(length) if isinstance(length, (int, float)) else None,
        width=int(gen.params["width"]) if isinstance(gen.params.get("width"), (int, float)) else None,
        height=int(gen.params["height"]) if isinstance(gen.params.get("height"), (int, float)) else None,
        draft=bool(gen.params.get("draft")),
        quarter=bool(gen.params.get("quarter")),
        exec_seconds=gen.exec_seconds,
        recovered=bool(gen.recovered_from),
    )


@router.get("/media/{slug}/{media_id}/file", include_in_schema=True)
async def media_file(slug: str, media_id: str, request: Request, download: bool = False):
    """The original file, byte for byte. ``download=1`` asks the browser to save it under its
    own name and format rather than show it."""
    st = state(request)
    media = _media_or_404(st.registry.get(slug), media_id)
    path = Path(media.path)
    if not path.is_file():
        raise ApiError(404, "file_missing", media.path)
    headers = {"Cache-Control": IMMUTABLE if media.sha256 else "no-cache"}
    if media.sha256:
        headers["ETag"] = f'"{media.sha256}"'
    return FileResponse(str(path), media_type=_mime_for(path), filename=path.name, headers=headers, content_disposition_type="attachment" if download else "inline")


@router.get("/media/{slug}/{media_id}/thumb", include_in_schema=True)
async def media_thumb(slug: str, media_id: str, request: Request, size: Literal["grid", "detail", "poster"] = "grid"):
    st = state(request)
    project = st.registry.get(slug)
    media = _media_or_404(project, media_id)
    cache = thumb_cache_dir(st.store, project)
    had_sha = bool(media.sha256)
    try:
        made = await run_in_threadpool(ensure_thumbnails, media, cache, sizes=(size,))
    except FileNotFoundError:
        raise ApiError(404, "file_missing", media.path) from None
    if not had_sha and media.sha256:
        async with st.registry.lock(slug):
            st.store.save(project)
            st.registry.touch(slug)
    key = "detail" if (media.type in ("image", "audio") and size == "poster") else size
    path = made.get(key)
    if path is None:
        raise ApiError(500, "thumb_failed", f"could not derive {size} for {media.path}")
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/webp"
    return FileResponse(str(path), media_type=mime, headers={"Cache-Control": IMMUTABLE})


@router.get("/media/{slug}/{media_id}/frames", include_in_schema=True)
async def media_frames(slug: str, media_id: str, request: Request, n: int = Query(4, ge=1, le=SHEET_MAX_FRAMES), size: int = Query(1024, ge=64, le=2048)):
    """A contact sheet of ``n`` evenly spaced frames of a video, each ``size`` on its long
    edge, times stamped: how an agent QCs motion (progression, mid-clip defects, drift) from
    one image instead of a poster frame."""
    st = state(request)
    project = st.registry.get(slug)
    media = _media_or_404(project, media_id)
    if media.type != "video":
        raise ApiError(422, "not_video", f"{media_id} is {media.type}, not a video; use thumb")
    cache = thumb_cache_dir(st.store, project)
    had_sha = bool(media.sha256)
    try:
        path = await run_in_threadpool(contact_sheet, media, cache, n=n, edge=size)
    except FileNotFoundError:
        raise ApiError(404, "file_missing", media.path) from None
    except RuntimeError as exc:
        raise ApiError(409, "frames_failed", str(exc)) from None
    if not had_sha and media.sha256:
        async with st.registry.lock(slug):
            st.store.save(project)
            st.registry.touch(slug)
    return FileResponse(str(path), media_type="image/webp", headers={"Cache-Control": IMMUTABLE})
