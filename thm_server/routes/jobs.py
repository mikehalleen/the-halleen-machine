"""Render jobs: what is queued, running, or finished in this server process, and cancelling one.

Also the held group: generations saved for later (``generate`` with ``hold``), which the job
manager has never seen. Holding is GPU management, not project management, so the group is the
machine's: ``POST /api/held/release`` sends every held generation in every project down the
ordinary prepare/launch path, oldest save first; ``discard`` drops one. Reading one project's
is ``GET /generations?status=held``; the count per project rides on the project cards.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from thm_core import Generation, resolve_comfy
from thm_core.commands import check_comfy_ops_gate, close_render_card

from .. import agent_activity
from ..deps import actor_context, state
from ..errors import ApiError
from ..jobs import to_out
from ..schemas import CancelBody, ComfyRestartBody, ComfyRestartOut, JobOut

router = APIRouter(prefix="/api/projects/{slug}", tags=["jobs"])
server_router = APIRouter(prefix="/api", tags=["jobs"])


def _discarded(gen: Generation, detail: str) -> Generation:
    gen = gen.model_copy()
    gen.status = "cancelled"
    gen.warnings = list(gen.warnings) + [{"kind": "discarded", "handle": "", "detail": detail}]
    return gen


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(slug: str, request: Request):
    st = state(request)
    st.registry.get(slug)  # 404 for an unknown project
    return [to_out(j) for j in st.jobs.for_project(slug)]


async def _optional_body(request: Request, model):
    raw = await request.body()
    if not raw.strip():
        return model()
    try:
        return model.model_validate_json(raw)
    except Exception:
        raise ApiError(422, "invalid_payload", "body must be a JSON object") from None


@router.post("/jobs/{gen_id}/cancel", response_model=JobOut)
async def cancel_job(slug: str, gen_id: str, request: Request):
    """Stop a live render: kills the runner, drops the prompt from ComfyUI (deleted if queued,
    interrupted if executing), marks the generation cancelled. Not a command: a job is server
    state, and the change that started the render stays as it was. The agent may cancel on its
    own only in an auto session; elsewhere only when the user asked (``requested`` in the body)."""
    st = state(request)
    project = st.registry.get(slug)
    ctx = actor_context(request)
    body = await _optional_body(request, CancelBody)
    check_comfy_ops_gate(project, ctx, requested=body.requested, what="cancel")
    job = st.jobs.get(gen_id)
    if job is None or job.slug != slug:
        raise ApiError(404, "unknown_job", f"no job {gen_id} in {slug}")
    if ctx.actor == "agent":
        agent_activity.note(st, slug, "cancelling a render", session=ctx.session)
    try:
        job = await st.jobs.cancel(gen_id)
    except ValueError as exc:
        raise ApiError(409, "job_not_live", f"job {gen_id} is already {exc}") from None
    if ctx.actor == "agent":
        why = body.reason.strip()
        await st.jobs._alert(slug, f"the agent cancelled the render of {job.bin_name or job.id}{' - ' + why if why else ''}")
    return to_out(job)


@server_router.post("/comfy/restart", response_model=ComfyRestartOut)
async def restart_comfy(request: Request):
    """Stop and start ComfyUI through the user's script (``[advanced] restart_script``). Live
    renders are stopped and queued again; they start when ComfyUI answers. The agent names the
    project it acts for in ``slug``: on its own only in an auto session there, otherwise only
    when the user asked (``requested``). The user's own call needs neither."""
    st = state(request)
    ctx = actor_context(request)
    body = await _optional_body(request, ComfyRestartBody)
    if ctx.actor == "agent":
        if not body.slug:
            raise ApiError(422, "bad_value", "the agent names the project it is acting for (slug)")
        project = st.registry.get(body.slug)
        check_comfy_ops_gate(project, ctx, requested=body.requested, what="restart ComfyUI")
        agent_activity.note(st, body.slug, "restarting ComfyUI", session=ctx.session)
    try:
        out = await st.jobs.restart_comfy(by=ctx.actor, reason=body.reason.strip(), slug=body.slug)
    except ValueError as exc:
        raise ApiError(409, "comfy_restart_unconfigured", str(exc)) from None
    except RuntimeError as exc:
        raise ApiError(500, "comfy_restart_failed", str(exc)) from None
    return ComfyRestartOut(**out)


@server_router.post("/held/release", response_model=list[JobOut])
async def release_held(request: Request):
    """Start every generation saved for later, in every project, oldest save first (FIFO across
    projects: the GPU is one queue whichever project asked). Each goes through the same
    prepare/launch as a plain Generate, so ComfyUI's queue serializes them. A held record whose
    bin has since been deleted is discarded instead; a project with no ComfyUI configured keeps
    its saves held. The user's gesture, so the jobs are queued by the user whoever saved them."""
    st = state(request)
    if actor_context(request).actor != "user":
        raise ApiError(403, "wrong_actor", "only the user starts the saved-for-later queue")
    # every held save on the machine, oldest first
    waiting: list[tuple[str, str, Generation]] = []
    for slug in st.store.list_projects():
        try:
            project = st.registry.get(slug)
        except Exception as exc:
            print(f"[WARN] skipping project {slug}: {exc}")
            continue
        waiting += [(g.ts, slug, g) for g in st.store.read_generations(project) if g.status == "held"]
    waiting.sort(key=lambda w: w[0])
    jobs = []
    touched: dict[str, int] = {}
    for _, slug, held in waiting:
        async with st.registry.lock(slug):
            project = st.registry.get(slug)
            gen = st.store.read_generation(project, held.id)
            if gen is None or gen.status != "held":
                continue  # discarded or released while we gathered
            try:
                resolve_comfy(project, st.settings.comfy)
            except ValueError:
                continue  # nowhere to render it: it stays held
            if gen.bin not in project.bins_by_id:
                st.store.append_generation(project, _discarded(gen, "bin deleted before release"))
            else:
                jobs.append(st.jobs.prepare(slug, project, gen.model_copy(update={"status": "queued"}), queued_by="user"))
            touched[slug] = st.registry.touch(slug)
    for slug, version in touched.items():
        st.bus.publish(slug, version)
    for job in jobs:
        st.jobs.launch(job)
    return [to_out(j) for j in jobs]


@router.post("/held/{gen_id}/discard", response_model=Generation)
async def discard_held(slug: str, gen_id: str, request: Request):
    """Drop one saved generation without rendering it. The change that saved it stays as it was."""
    st = state(request)
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        gen = st.store.read_generation(project, gen_id)
        if gen is None:
            raise ApiError(404, "unknown_id", f"no generation {gen_id}", {"type": "generation", "id": gen_id})
        if gen.status != "held":
            raise ApiError(409, "not_held", f"generation {gen_id} is {gen.status}, not held")
        gen = _discarded(gen, "discarded before release")
        st.store.append_generation(project, gen)
        close_render_card(st.store, project, gen.id, "discarded before it was generated")
        version = st.registry.touch(slug)
    st.bus.publish(slug, version)
    return gen
