"""Projects and bins: read side plus project creation."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from thm_core import Message, ids
from thm_core.commands import SetProjectSettings
from thm_core.ids import utc_now
from thm_core.models import ComfySettings

from ..deps import actor_context, state
from ..derive import bin_cards, bin_detail, project_card, project_detail, project_latest
from ..errors import ApiError
from ..jobs import to_out
from ..schemas import BinCard, BinDetail, CreateProjectBody, HealthOut, ProjectCard, ProjectDetail

router = APIRouter(prefix="/api", tags=["projects"])


@router.get("/health", response_model=HealthOut)
async def health(request: Request):
    st = state(request)
    return HealthOut(version=request.app.version, projects_dir=str(st.settings.projects_dir), workflows_dir=str(st.settings.workflows_dir))


@router.get("/projects", response_model=list[ProjectCard])
async def list_projects(request: Request):
    """Every project as a card. The agent's list leaves private projects out entirely: it
    never learns they exist, so ``sessions`` and ``watch`` cannot pick one."""
    st = state(request)
    agent = actor_context(request).actor == "agent"
    cards: list[tuple[str, ProjectCard]] = []
    for slug in st.store.list_projects():
        try:
            project = st.registry.get(slug)
        except Exception as exc:
            print(f"[WARN] skipping project {slug}: {exc}")
            continue
        if agent and project.private:
            continue
        card = project_card(st.store, project, st.registry.version(slug))
        live = [j for j in st.jobs.for_project(slug) if j.live]
        card.generating = len(live)
        card.generating_job = to_out(live[0]) if live else None
        if card.generating_job is not None and project.private:
            # the list is read from every screen: a private render shows its progress, never its words
            card.generating_job.bin_name = None
            card.generating_job.task = None
            card.generating_job.error = None
        cards.append((project_latest(st.store, project), card))
    # newest activity first, with every private project after the public ones (the home page and
    # the picker both keep this order), so the glanceable top of the list never holds a private one
    cards.sort(key=lambda pair: (pair[1].private, _desc(pair[0])))
    return [card for _, card in cards]


def _desc(iso: str) -> tuple[int, ...]:
    """A sort key that puts the later timestamp first when the tuple sorts ascending."""
    return tuple(-ord(c) for c in iso)


@router.post("/projects", response_model=ProjectDetail, status_code=201)
async def create_project(body: CreateProjectBody, request: Request):
    st = state(request)
    name = body.name.strip()
    if not name:
        raise ApiError(422, "bad_value", "project name is empty")
    comfy = ComfySettings(api_base=st.settings.comfy.api_base, output_root=st.settings.comfy.output_root)
    project = st.store.new_project(name, comfy=comfy)
    if body.settings is not None:
        # the settings command validates and applies; creation itself is not a logged change
        settings = SetProjectSettings(**body.settings.model_dump(exclude_none=True))
        if settings.changes():
            ctx = actor_context(request)
            settings.check(project, ctx, st.env)
            settings.apply(project, ctx, st.env)
    brief = (body.brief or "").strip()
    if brief:
        # a harness handing over the brief at creation: it becomes the project's brief and the
        # first line of the feed, the same as a brief typed into the app at the first Start
        project.brief = brief
        st.store.append_message(project, Message(id=ids.new_id(ids.MESSAGE), ts=utc_now(), kind="user", text=brief))
    st.store.save(project)
    st.registry.invalidate(project.slug)
    loaded = st.registry.get(project.slug)
    return project_detail(st.store, loaded, st.registry.version(project.slug), st.env)


@router.delete("/projects/{slug}")
async def delete_project(slug: str, request: Request):
    """Remove the project file and everything under ``<slug>-files/``: uploads, clips, job specs,
    logs, thumbnails. Takes are ComfyUI's output and stay on disk. Refused while a render is live.
    Not a command: commands mutate a loaded project, and this removes the project itself."""
    st = state(request)
    async with st.registry.lock(slug):
        st.registry.get(slug)  # 404 for an unknown project
        live = [j for j in st.jobs.for_project(slug) if j.live]
        if live:
            raise ApiError(409, "project_busy", f"{len(live)} generation(s) still running; cancel or wait first", {"jobs": [j.id for j in live]})
        await run_in_threadpool(st.store.delete_project, slug)
        st.registry.invalidate(slug)
        st.jobs.forget(slug)
    return {"deleted": slug}


@router.get("/projects/{slug}", response_model=ProjectDetail)
async def get_project(slug: str, request: Request):
    st = state(request)
    project = st.registry.get(slug)
    detail = project_detail(st.store, project, st.registry.version(slug), st.env)
    _stamp_live(st, slug, detail.bins)
    return detail


def _stamp_live(st, slug: str, cards: list[BinCard]) -> None:
    """Which bins are rendering right now: job state lives in the manager, not the project."""
    for card in cards:
        card.generating = [j.id for j in st.jobs.live_for_bin(slug, card.id)]


@router.get("/projects/{slug}/bins", response_model=list[BinCard])
async def list_bins(slug: str, request: Request):
    st = state(request)
    project = st.registry.get(slug)
    cards = bin_cards(project, st.env)
    _stamp_live(st, slug, cards)
    return cards


@router.get("/projects/{slug}/bins/{bin_id}", response_model=BinDetail)
async def get_bin(slug: str, bin_id: str, request: Request):
    st = state(request)
    project = st.registry.get(slug)
    bin_ = project.bins_by_id.get(bin_id)
    if bin_ is None:
        raise ApiError(404, "unknown_id", f"no bin {bin_id}", {"type": "bin", "id": bin_id})
    detail = bin_detail(project, bin_, st.env, st.settings.workflows_dir)
    detail.active_jobs = [to_out(j) for j in st.jobs.live_for_bin(slug, bin_id)]
    return detail
