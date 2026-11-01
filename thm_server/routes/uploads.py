"""Uploads: the one place a client writes a media file (design section 8).

The file lands in ``projects/<slug>-files/imports/``, is adopted like any other
media (hash, probe, thumbnails), and joins a bin through the ordinary
``add_media_to_bin`` command under the project lock, so the change log and the
session feed see it the way they see everything else. Without a bin (or a bin
name) the upload mints the next ``bin-n``; the client passes the bin it is in.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import BinaryIO

from fastapi import APIRouter, File, Form, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from thm_core import Media, MediaOrigin, Message, Project, ProjectStore, adopt_media, ensure_thumbnails, execute, media_type_for, thumb_cache_dir
from thm_core.commands import AddMediaToBin, RemoveMediaFromBin

from ..deps import actor_context, state
from ..errors import ApiError
from ..imports import IMPORTS_DIR, find_or_create_bin, fresh_import_path, safe_name
from ..publish import publish_result
from ..recover import recover_media
from ..schemas import RemoveUploadOut, UploadOut

router = APIRouter(prefix="/api/projects/{slug}", tags=["uploads"])

MAX_UPLOAD_BYTES = 512 << 20


def _write(src: BinaryIO, dest: Path) -> None:
    with open(dest, "wb") as out:
        shutil.copyfileobj(src, out, 1 << 20)


@router.post("/uploads", response_model=UploadOut)
async def upload(
    slug: str,
    request: Request,
    file: UploadFile = File(...),
    bin: str | None = Form(None),
    bin_name: str | None = Form(None),
    source: str | None = Form(None),
    note: str | None = Form(None),
):
    """Anyone with a say in the project uploads: the user from the browser, the agent (with a
    live session) after working on a fetched file locally. ``source`` and ``note`` are the
    upload's provenance when the uploader has it ("audio removed, ffmpeg -an" from take 26);
    never required, and not a recipe. A file that carries the ComfyUI graph that made it (an
    output THM gave up on before ComfyUI finished, fetched by hand) lands as the take it is:
    the record is rebuilt from the file, or the failed generation it came from is completed."""
    st = state(request)
    ctx = actor_context(request)
    if ctx.actor == "agent":
        sess = st.registry.get(slug).agent_session
        if sess is None or sess.status != "live":
            raise ApiError(409, "no_session", "an agent upload needs a live session; connect first")
    name = safe_name(file.filename)
    if media_type_for(name) is None:
        raise ApiError(422, "bad_value", f"unsupported file type: {name}", {"filename": name})
    size = int(file.size or 0)
    if size > MAX_UPLOAD_BYTES:
        raise ApiError(413, "too_large", f"{name} is {size} bytes; the limit is {MAX_UPLOAD_BYTES}")
    if size == 0:
        raise ApiError(422, "bad_value", f"{name} is empty")

    project = st.registry.get(slug)  # 404 before anything is written
    source = (source or "").strip() or None
    if source and source not in project.media_by_id:
        raise ApiError(404, "unknown_id", f"no media {source}", {"type": "media", "id": source})
    note = (note or "").strip() or None
    dest = fresh_import_path(st.store.files_dir(project), name)
    await run_in_threadpool(_write, file.file, dest)

    change = None
    messages: list[Message] = []
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        adopted_here = False
        try:
            target, messages = find_or_create_bin(st.store, project, ctx, st.env, bin, bin_name)
            media = adopt_media(project, dest, origin=MediaOrigin(type="upload", bin=target.id, source=source, note=note))
            adopted_here = Path(media.path) == dest.resolve()
            if not adopted_here:
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
            recovered = await run_in_threadpool(recover_media, st, slug, project, media)
            if recovered is not None:
                await run_in_threadpool(st.store.save, project)
                version = st.registry.touch(slug)
        except Exception:
            # the cached project may carry an unsaved media record: reload it next time
            st.registry.invalidate(slug)
            dest.unlink(missing_ok=True)
            raise
    publish_result(st, slug, project, messages=messages, change=change, generation=recovered.generation if recovered else None, version=version)
    return UploadOut(media=media, bin=target, change=change, project_version=version)


def _in_use(project: Project, media_id: str) -> bool:
    for bin_ in project.bins_by_id.values():
        if media_id in bin_.media or any(r.media == media_id for r in bin_.references):
            return True
    return False


def unlink_upload_file(store: ProjectStore, project: Project, media: Media) -> bool:
    """Unlink ``media``'s file when it's an upload that lives under ``imports/``. Only ever
    called once nothing references the record any more. Returns whether a file was removed —
    generated takes and anything outside imports/ are never touched."""
    if media.origin.type != "upload":
        return False
    imports = (store.files_dir(project) / IMPORTS_DIR).resolve()
    path = Path(media.path)
    if imports not in path.resolve().parents:
        return False
    path.unlink(missing_ok=True)
    return True


@router.delete("/uploads/{media_id}", response_model=RemoveUploadOut)
async def remove_upload(slug: str, media_id: str, request: Request, bin: str):
    """Take an upload out of a bin. An upload nothing else uses is deleted with its file.

    Only files under the project's ``imports/`` are ever unlinked; a deduped upload that
    pointed at a file elsewhere keeps its record and file.
    """
    st = state(request)
    ctx = actor_context(request)
    if ctx.actor != "user":
        raise ApiError(409, "wrong_actor", "only the user removes uploads")
    messages: list[Message] = []
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        target = project.bins_by_id.get(bin)
        if target is None:
            raise ApiError(404, "unknown_id", f"no bin {bin}", {"type": "bin", "id": bin})
        result = await run_in_threadpool(execute, st.store, project, RemoveMediaFromBin(bin=target.id, media=media_id), ctx, env=st.env)
        messages = list(result.messages)
        deleted = False
        media = project.media_by_id.get(media_id)
        if media is not None and media.origin.type == "upload" and not _in_use(project, media_id):
            del project.media_by_id[media_id]
            st.store.save(project)
            unlink_upload_file(st.store, project, media)
            deleted = True
        version = st.registry.touch(slug)
    publish_result(st, slug, project, messages=messages, change=result.change, generation=None, version=version)
    return RemoveUploadOut(media=media_id, bin=target, deleted=deleted, change=result.change, project_version=version)
