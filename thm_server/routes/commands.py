"""Commands, decisions, and the logs: the only write path besides project creation and uploads.

A landed ``generate`` (directly, or an approved agent proposal) also starts a
render job: the busy and ComfyUI checks run inside the project lock before the
command executes, ``jobs.prepare`` runs inside the lock after it, and
``jobs.launch`` runs once the lock is released. Every landed command publishes
its messages as ``message`` events and then a ``project`` event.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool

from thm_core import Change, Generation, Media, Message, Project, ProjectStore, close_pending, decide, execute, parse_command, resolve_comfy

from ..deps import ServerState, actor_context, state
from ..derive import feed_entries
from ..errors import ApiError
from ..publish import publish_result
from ..recover import sweep_bin
from ..schemas import ClosePendingOut, CommandResultOut, DecisionBody, FeedOut, InboxOut
from .uploads import unlink_upload_file

router = APIRouter(prefix="/api/projects/{slug}", tags=["commands"])

DELETE_COMMANDS = ("delete_media", "delete_bin")


def _media_to_unlink(name: str, command, project: Project) -> list[Media]:
    """The media a delete command is about to remove, captured before ``execute`` runs — an
    upload among them gets its file cleaned up the same way ``uploads.py``'s own delete route
    already does, once the command has fully removed the record."""
    if name == "delete_media":
        ids = [command.media]
    elif name == "delete_bin":
        bin_ = project.bins_by_id.get(command.bin)
        ids = list(bin_.media) if bin_ is not None else []
    else:
        return []
    return [m for m in (project.media_by_id.get(i) for i in ids) if m is not None]


async def _json_object(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise ApiError(422, "invalid_payload", "body must be a JSON object") from None
    if not isinstance(body, dict):
        raise ApiError(422, "invalid_payload", "body must be a JSON object")
    return body


GENERATE_COMMANDS = ("generate", "propose_shots")


def _generate_bin_id(name: str, body: dict) -> str | None:
    if name not in ("generate", "delete_bin"):
        return None
    return str(body.get("bin") or "") or None


def _approved_generate(store: ProjectStore, project: Project, change_id: str, action: str) -> tuple[str, str | None] | None:
    """The command name and bin id of an approved generate proposal."""
    if action != "approve":
        return None
    change = store.read_changes(project).get(change_id)
    if change is None or change.command not in GENERATE_COMMANDS:
        return None
    return change.command, str((change.payload or {}).get("bin") or "") or None


def _refuse_if_busy(st: ServerState, slug: str, project: Project, name: str, bin_id: str | None, *, propose: bool = False) -> None:
    """A bin is not deleted mid-render, and any render needs a ComfyUI output root to write
    into. Renders themselves stack: a bin takes as many generations as you start. A proposal
    renders nothing yet; its approval is checked here again."""
    if name not in GENERATE_COMMANDS and name != "delete_bin":
        return
    if propose:
        return
    if name == "delete_bin":
        live = st.jobs.live_for_bin(slug, bin_id) if bin_id else []
        if live:
            raise ApiError(409, "bin_busy", f"a generation is still {live[0].status} on this bin", {"job": live[0].id, "status": live[0].status})
        return
    try:
        resolve_comfy(project, st.settings.comfy)
    except ValueError as exc:
        raise ApiError(409, "comfy_unconfigured", str(exc)) from None


def _out(result, version: int) -> CommandResultOut:
    return CommandResultOut(
        change=result.change, generation=result.generation, warnings=result.warnings, messages=result.messages, project_version=version,
        changes=result.changes, generations=result.generations,
    )


def _publish(st: ServerState, slug: str, project: Project, result, version: int) -> None:
    publish_result(
        st, slug, project, messages=result.messages, change=result.change, generation=result.generation, version=version,
        changes=result.changes, generations=result.generations,
    )


@router.post("/commands/{name}", response_model=CommandResultOut)
async def run_command(slug: str, name: str, request: Request):
    st = state(request)
    ctx = actor_context(request)
    body = await _json_object(request)
    jobs = []
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        command = parse_command(name, body)
        _refuse_if_busy(st, slug, project, name, _generate_bin_id(name, body), propose=ctx.propose)
        to_unlink = _media_to_unlink(name, command, project) if name in DELETE_COMMANDS else []
        result = await run_in_threadpool(execute, st.store, project, command, ctx, env=st.env)
        # a batch the user asked for out loud queues every render it made; a held one waits for release
        jobs = [st.jobs.prepare(slug, project, gen, queued_by=ctx.actor) for gen in result.all_generations() if gen.status != "held"]
        for media in to_unlink:
            if media.id not in project.media_by_id:  # a delete_bin keeps media something else still holds
                unlink_upload_file(st.store, project, media)
        version = st.registry.touch(slug)
    _publish(st, slug, project, result, version)
    for job in jobs:
        st.jobs.launch(job)
    return _out(result, version)


@router.post("/changes/{change_id}/decision", response_model=CommandResultOut)
async def decide_change(slug: str, change_id: str, body: DecisionBody, request: Request):
    st = state(request)
    ctx = actor_context(request)
    jobs = []
    items = [i.model_dump() for i in body.items] if body.items is not None else None
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        approved = _approved_generate(st.store, project, change_id, body.action)
        if approved is not None:
            _refuse_if_busy(st, slug, project, *approved)
        result = await run_in_threadpool(decide, st.store, project, change_id, body.action, body.note, ctx, env=st.env, items=items)
        # an approved proposal is the agent's render (a batch's, its renders); the user only let it through
        jobs = [st.jobs.prepare(slug, project, gen, queued_by="agent") for gen in result.all_generations() if gen.status != "held"]
        version = st.registry.touch(slug)
    _publish(st, slug, project, result, version)
    for job in jobs:
        st.jobs.launch(job)
    return _out(result, version)


@router.post("/changes/close-pending", response_model=ClosePendingOut)
async def close_pending_changes(slug: str, request: Request):
    """Every card the current session still owes, closed at once: seen, nothing said."""
    st = state(request)
    ctx = actor_context(request)
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        result = await run_in_threadpool(close_pending, st.store, project, ctx)
        version = st.registry.touch(slug)
    _publish(st, slug, project, result, version)
    return ClosePendingOut(closed=len(result.changes), messages=result.messages, project_version=version)


@router.get("/changes", response_model=list[Change])
async def list_changes(slug: str, request: Request, status: str | None = None, actor: str | None = None):
    st = state(request)
    project = st.registry.get(slug)
    changes = list(st.store.read_changes(project).values())
    if actor:
        changes = [c for c in changes if c.actor == actor]
    if status:
        changes = [c for c in changes if c.decision is not None and c.decision.status == status]
    return changes


@router.get("/changes/{change_id}", response_model=Change)
async def get_change(slug: str, change_id: str, request: Request):
    """One change's latest record: an agent reading the outcome of its propose_shots card."""
    st = state(request)
    change = st.store.read_changes(st.registry.get(slug)).get(change_id)
    if change is None:
        raise ApiError(404, "unknown_id", f"no change {change_id}", {"type": "change", "id": change_id})
    return change


@router.get("/messages", response_model=list[Message])
async def list_messages(slug: str, request: Request):
    st = state(request)
    return st.store.read_messages(st.registry.get(slug))


@router.get("/inbox", response_model=InboxOut)
async def inbox(slug: str, request: Request, cursor: int = 0):
    st = state(request)
    messages, next_cursor = st.store.read_inbox(st.registry.get(slug), cursor)
    return InboxOut(messages=messages, cursor=next_cursor)


@router.get("/feed", response_model=FeedOut)
async def feed(slug: str, request: Request, since: int = Query(0, ge=0)):
    """The session feed: messages past ``since`` with their changes and card media. Same cursor as the inbox."""
    st = state(request)
    project = st.registry.get(slug)
    entries, cursor = feed_entries(st.store, project, since, st.env)
    return FeedOut(entries=entries, cursor=cursor)


@router.get("/generations", response_model=list[Generation])
async def list_generations(slug: str, request: Request, bin: str | None = None, status: str | None = None):
    """Every generation, in landing order; ``bin`` and ``status`` narrow it so an agent reading
    one bin's history does not page the whole project. Reading one bin first gives any plain
    media in it whose file carries its render's graph the record it is missing (``recover``),
    so a hand-uploaded output shows in the history as the take it is."""
    st = state(request)
    if bin:
        async with st.registry.lock(slug):
            project = st.registry.get(slug)
            target = project.bins_by_id.get(bin)
            recovered = await run_in_threadpool(sweep_bin, st, slug, project, target) if target is not None else []
            if recovered:
                await run_in_threadpool(st.store.save, project)
                version = st.registry.touch(slug)
        if recovered:
            st.bus.publish(slug, version)
    gens = st.store.read_generations(st.registry.get(slug))
    if bin:
        gens = [g for g in gens if g.bin == bin]
    if status:
        gens = [g for g in gens if g.status == status]
    return gens


@router.get("/generations/{gen_id}", response_model=Generation)
async def get_generation(slug: str, gen_id: str, request: Request):
    """The generation's latest record; a live job overlays its status."""
    st = state(request)
    project = st.registry.get(slug)
    gen = st.store.read_generation(project, gen_id)
    if gen is None:
        raise ApiError(404, "unknown_id", f"no generation {gen_id}", {"type": "generation", "id": gen_id})
    job = st.jobs.get(gen_id)
    if job is not None and job.live:
        gen = gen.model_copy(update={"status": job.status})
    return gen
