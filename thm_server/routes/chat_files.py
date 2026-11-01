"""Message files: what a note carries that is not project media.

A marked-up screenshot, a picture from somewhere else, pasted into the composer to show the
agent something. The file lands in ``projects/<slug>-files/chat/`` as ``<id>_<name>`` and the
message that carries it names it by id (``Message.files``). It joins no bin, so the library,
the picker and the references never see it. ``keep`` is the way across: it copies the file
into ``imports/`` and lands it in a bin exactly like an upload.
"""

from __future__ import annotations

import shutil

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from thm_core import MessageFile, ids, media_type_for
from thm_core.commands import CommandRefused, message_file

from ..deps import ServerState, actor_context, state
from ..errors import ApiError
from ..imports import fresh_import_path, safe_name
from ..schemas import KeepFileBody, RemoveFileOut, UploadOut
from .media import IMMUTABLE, _mime_for
from .uploads import MAX_UPLOAD_BYTES, _write, land_import

router = APIRouter(prefix="/api/projects/{slug}", tags=["chat-files"])


def _needs_session(st: ServerState, slug: str, actor: str) -> None:
    if actor != "agent":
        return
    sess = st.registry.get(slug).agent_session
    if sess is None or sess.status != "live":
        raise ApiError(409, "no_session", "an agent upload needs a live session; connect first")


def _file_or_404(st: ServerState, slug: str, file_id: str) -> MessageFile:
    try:
        return message_file(st.store, st.registry.get(slug), file_id)
    except CommandRefused as exc:
        raise ApiError(404, exc.code, exc.detail, {"type": "file", "id": file_id}) from None


@router.post("/chat-files", response_model=MessageFile)
async def upload_chat_file(slug: str, request: Request, file: UploadFile = File(...)):
    """A file for a note, not for the project: no bin, no media record, no change. The id that
    comes back goes in ``post_message``'s ``files``."""
    st = state(request)
    ctx = actor_context(request)
    _needs_session(st, slug, ctx.actor)
    name = safe_name(file.filename)
    kind = media_type_for(name)
    if kind is None:
        raise ApiError(422, "bad_value", f"unsupported file type: {name}", {"filename": name})
    size = int(file.size or 0)
    if size > MAX_UPLOAD_BYTES:
        raise ApiError(413, "too_large", f"{name} is {size} bytes; the limit is {MAX_UPLOAD_BYTES}")
    if size == 0:
        raise ApiError(422, "bad_value", f"{name} is empty")
    project = st.registry.get(slug)  # 404 before anything is written
    folder = st.store.chat_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    file_id = ids.new_id(ids.FILE)
    await run_in_threadpool(_write, file.file, folder / f"{file_id}_{name}")
    return MessageFile(id=file_id, name=name, type=kind)


@router.get("/chat-files/{file_id}")
async def chat_file(slug: str, file_id: str, request: Request):
    st = state(request)
    _file_or_404(st, slug, file_id)
    path = st.store.chat_file(slug, file_id)
    return FileResponse(str(path), media_type=_mime_for(path), filename=path.name[len(file_id) + 1 :], headers={"Cache-Control": IMMUTABLE}, content_disposition_type="inline")


@router.delete("/chat-files/{file_id}", response_model=RemoveFileOut)
async def remove_chat_file(slug: str, file_id: str, request: Request):
    """Take back a file that was never sent. One a message already carries stays: the feed
    still shows it."""
    st = state(request)
    if actor_context(request).actor != "user":
        raise ApiError(409, "wrong_actor", "only the user removes a note's files")
    _file_or_404(st, slug, file_id)
    if any(f.id == file_id for m in st.store.read_messages(slug) for f in m.files):
        raise ApiError(409, "in_use", f"a message carries {file_id}")
    st.store.chat_file(slug, file_id).unlink(missing_ok=True)
    return RemoveFileOut(file=file_id, deleted=True)


@router.post("/chat-files/{file_id}/keep", response_model=UploadOut)
async def keep_chat_file(slug: str, file_id: str, body: KeepFileBody, request: Request):
    """Make a note's file project media: a copy lands in ``imports/`` and joins the bin like
    any upload (a bin id, a bin name, or the next ``bin-n``). The note keeps its own file."""
    st = state(request)
    ctx = actor_context(request)
    _needs_session(st, slug, ctx.actor)
    record = _file_or_404(st, slug, file_id)
    project = st.registry.get(slug)
    dest = fresh_import_path(st.store.files_dir(project), record.name)
    await run_in_threadpool(shutil.copyfile, st.store.chat_file(project, file_id), dest)
    return await land_import(st, slug, ctx, dest, bin=body.bin, bin_name=body.bin_name)
