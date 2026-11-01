"""Render jobs: what is queued, running, or finished in this server process, and cancelling one.

Also the held group: generations saved for later (``generate`` with ``hold``), which the job
manager has never seen. ``release`` sends every one of them down the ordinary prepare/launch
path at once; ``discard`` drops one. Reading them is ``GET /generations?status=held``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from thm_core import Generation, resolve_comfy

from ..deps import state
from ..errors import ApiError
from ..jobs import to_out
from ..schemas import JobOut

router = APIRouter(prefix="/api/projects/{slug}", tags=["jobs"])


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


@router.post("/jobs/{gen_id}/cancel", response_model=JobOut)
async def cancel_job(slug: str, gen_id: str, request: Request):
    """Stop a live render: kills the runner, drops the prompt from ComfyUI (deleted if queued,
    interrupted if executing), marks the generation cancelled. Not a command: a job is server
    state, and the change that started the render stays as it was."""
    st = state(request)
    st.registry.get(slug)
    job = st.jobs.get(gen_id)
    if job is None or job.slug != slug:
        raise ApiError(404, "unknown_job", f"no job {gen_id} in {slug}")
    try:
        job = await st.jobs.cancel(gen_id)
    except ValueError as exc:
        raise ApiError(409, "job_not_live", f"job {gen_id} is already {exc}") from None
    return to_out(job)


@router.post("/held/release", response_model=list[JobOut])
async def release_held(slug: str, request: Request):
    """Start every generation saved for later, in the order they were saved: each goes through
    the same prepare/launch as a plain Generate, so ComfyUI's queue serializes them. A held
    record whose bin has since been deleted is discarded instead. The user's gesture, so the
    jobs are queued by the user whoever saved them."""
    st = state(request)
    jobs = []
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        held = [g for g in st.store.read_generations(project) if g.status == "held"]
        if held:
            try:
                resolve_comfy(project, st.settings.comfy)
            except ValueError as exc:
                raise ApiError(409, "comfy_unconfigured", str(exc)) from None
        for gen in held:
            if gen.bin not in project.bins_by_id:
                st.store.append_generation(project, _discarded(gen, "bin deleted before release"))
                continue
            jobs.append(st.jobs.prepare(slug, project, gen.model_copy(update={"status": "queued"}), queued_by="user"))
        version = st.registry.touch(slug) if held else None
    if version is not None:
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
        version = st.registry.touch(slug)
    st.bus.publish(slug, version)
    return gen
