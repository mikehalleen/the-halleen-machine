"""Sequences: the read shapes and the export. Edits go through the command route like every
other write (``create_sequence``, ``add_sequence_items``, ``reorder_sequence``, ...)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from thm_core import execute
from thm_core.commands import CommandRefused, SetSequenceField, _sequence, item_media
from thm_core.media import ffmpeg_available
from thm_core.stitch import build_preview_plan, stitch

from ..deps import actor_context, state
from ..derive import sequence_out
from ..errors import ApiError
from ..publish import publish_result
from ..schemas import ExportBody, ExportStatusOut, PreviewBody, SequenceOut

PREVIEW_DIR = "cache/previews"
MAX_HANDLES_S = 5.0

router = APIRouter(prefix="/api/projects/{slug}/sequences", tags=["sequences"])


def _seq(project, seq_id: str):
    try:
        return _sequence(project, seq_id)
    except CommandRefused as exc:
        raise ApiError(404, exc.code, exc.detail, exc.extra) from None


@router.get("", response_model=list[SequenceOut])
async def list_sequences(slug: str, request: Request):
    st = state(request)
    project = st.registry.get(slug)
    return [sequence_out(project, s) for s in project.sequences]


@router.get("/{seq_id}", response_model=SequenceOut)
async def get_sequence(slug: str, seq_id: str, request: Request):
    st = state(request)
    project = st.registry.get(slug)
    return sequence_out(project, _seq(project, seq_id))


@router.post("/{seq_id}/preview")
async def preview_item(slug: str, seq_id: str, body: PreviewBody, request: Request):
    """One item with its layer slices and mix, rendered through the same plan and filter graph
    as the export (what the agent hears is what the export will have), with up to ``handles_s``
    of the take either side. The file is scratch: served once, kept in the project's cache, never
    media and never in a bin. Agent-only in practice (the UI has no control for it); an agent
    needs a live session, as for an export."""
    st = state(request)
    ctx = actor_context(request)
    if ctx.actor == "agent":
        sess = st.registry.get(slug).agent_session
        if sess is None or sess.status != "live":
            raise ApiError(409, "no_session", "an agent preview needs a live session; connect first")
    if not ffmpeg_available():
        raise ApiError(409, "ffmpeg_unavailable", "ffmpeg is not on PATH")
    if body.handles_s < 0 or body.handles_s > MAX_HANDLES_S:
        raise ApiError(422, "bad_value", f"handles_s must be between 0 and {MAX_HANDLES_S:g}")
    project = st.registry.get(slug)
    seq = _seq(project, seq_id)
    files_dir = st.store.files_dir(project)
    tag = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    work = files_dir / PREVIEW_DIR
    try:
        plan = build_preview_plan(project, seq, body.bin, work, now_tag=tag, handles_s=float(body.handles_s))
    except CommandRefused as exc:
        raise ApiError(404 if exc.code == "not_in_sequence" else 422, exc.code, exc.detail, exc.extra) from None
    dest = work / f"preview_{seq.id}_{body.bin}_{tag}.mp4"
    try:
        await run_in_threadpool(stitch, plan, dest)
    except (RuntimeError, ValueError) as exc:
        raise ApiError(409, "preview_failed", str(exc)[-2000:]) from None
    # a plan with everything skipped renders nothing worth hearing; say so rather than hand back a card
    headers = {"Cache-Control": "no-store", "X-THM-Frames": str(plan.total_frames), "X-THM-Offset-Frame": str(plan.offset_frames), "X-THM-Fps": f"{plan.fps:g}"}
    return FileResponse(str(dest), media_type="video/mp4", filename=dest.name, headers=headers, content_disposition_type="attachment")


@router.get("/{seq_id}/export", response_model=ExportStatusOut)
async def export_status(slug: str, seq_id: str, request: Request):
    st = state(request)
    _seq(st.registry.get(slug), seq_id)
    return st.exports.status(seq_id)


@router.post("/{seq_id}/export", response_model=ExportStatusOut)
async def start_export(slug: str, seq_id: str, request: Request, body: ExportBody | None = None):
    """Render the sequence to one mp4; it lands in the output bin as the next take. One export
    per sequence at a time; progress is the ``export`` event and the GET above. ``bin`` points
    the sequence at another output bin first (one logged change)."""
    st = state(request)
    ctx = actor_context(request)
    body = body or ExportBody()
    if ctx.actor == "agent":
        sess = st.registry.get(slug).agent_session
        if sess is None or sess.status != "live":
            raise ApiError(409, "no_session", "an agent export needs a live session; connect first")
    if not ffmpeg_available():
        raise ApiError(409, "ffmpeg_unavailable", "ffmpeg is not on PATH")
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        seq = _seq(project, seq_id)
        if not seq.items:
            raise ApiError(409, "sequence_empty", f"{seq.name} has no items")
        if st.exports.running(seq_id):
            raise ApiError(409, "export_busy", f"{seq.name} is already exporting")
        retarget = None
        if body.bin and body.bin != seq.output_bin:
            try:
                retarget = await run_in_threadpool(execute, st.store, project, SetSequenceField(sequence=seq_id, field="output_bin", value=body.bin), ctx, env=st.env)
            except CommandRefused as exc:
                raise ApiError(404 if exc.code == "unknown_id" else 422, exc.code, exc.detail, exc.extra) from None
            version = st.registry.touch(slug)
        try:
            status = st.exports.start(slug, project, seq, ctx)
        except ValueError as exc:
            raise ApiError(409, "sequence_empty", str(exc)) from None
        # the user exporting is the user taking what every item plays: those takes are not new
        # any more (only those -- a newer take an item does not play stays marked)
        seen = False
        if ctx.actor == "user":
            playing = {m.id for m in (item_media(project, i) for i in seq.items) if m is not None}
            for b in project.bins_by_id.values():
                if playing & set(b.unseen):
                    b.unseen = [m for m in b.unseen if m not in playing]
                    seen = True
            if seen:
                await run_in_threadpool(st.store.save, project)
                version = st.registry.touch(slug)
    if retarget is not None:
        publish_result(st, slug, project, messages=retarget.messages, change=retarget.change, generation=None, version=version)
    elif seen:
        st.bus.publish(slug, version)
    return status
