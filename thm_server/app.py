"""Application factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from thm_core import CoreSettings, Env, ProjectStore, load_core_settings
from thm_core.config import REPO_ROOT

from fastapi import Request
from fastapi.responses import JSONResponse

from . import agent_activity
from .deps import HEADER_ACTOR, HEADER_DOING, ServerState, is_private
from .errors import error_body, install_handlers
from .events import EventBus
from .jobs import JobManager
from .registry import ProjectRegistry
from .routes import agent, clips, commands, enhance, events, jobs, loras, media, projects, uploads, workflows
from .static import mount_spa

VERSION = "0.5.0"


def create_app(
    settings: CoreSettings | None = None, *, web_dist: Path | None = None, jobs_kwargs: dict | None = None
) -> FastAPI:
    """``jobs_kwargs`` reaches ``JobManager`` (tests inject a launcher and a short poll interval)."""
    if settings is None:
        settings = load_core_settings()
        override = os.environ.get("THM_PROJECTS_DIR")
        if override:
            settings.projects_dir = Path(override)
    store = ProjectStore(settings.projects_dir, backups=settings.backups)
    registry = ProjectRegistry(store)
    bus = EventBus()
    st = ServerState(
        settings=settings,
        store=store,
        env=Env(workflows_dir=Path(settings.workflows_dir)),
        registry=registry,
        bus=bus,
        jobs=JobManager(store=store, registry=registry, bus=bus, settings=settings, **(jobs_kwargs or {})),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Path(settings.projects_dir).mkdir(parents=True, exist_ok=True)
        await st.jobs.reconcile()
        yield
        await st.jobs.shutdown()

    app = FastAPI(title="THM", version=VERSION, lifespan=lifespan)
    app.state.thm = st
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_handlers(app)

    @app.middleware("http")
    async def agent_heartbeat(request: Request, call_next):
        """Every agent call is a sign of life; record what it is doing before the call runs, so
        the browser sees "reading your messages" while the agent is still reading.

        A private project is off limits to the agent: every read or write addressed to it,
        media and thumbnails included, is refused here with ``private_project`` before any
        handler runs, and leaves no heartbeat behind."""
        if (request.headers.get(HEADER_ACTOR) or "").strip().lower() == "agent":
            agent_activity.note_server(st)
            target = agent_activity.path_slug(request.url.path)
            if target and is_private(st, target):
                return JSONResponse(error_body("private_project", "this project is private; the agent cannot see it"), status_code=403)
            slug, phrase = agent_activity.describe_path(request.method, request.url.path)
            if slug and (slug in st.agent_activity or st.store.project_path(slug).is_file()):
                agent_activity.note(st, slug, (request.headers.get(HEADER_DOING) or "").strip() or phrase)
        return await call_next(request)

    app.include_router(projects.router)
    app.include_router(agent.router)
    app.include_router(agent.server_router)
    app.include_router(media.router)
    app.include_router(commands.router)
    app.include_router(workflows.router)
    app.include_router(loras.router)
    app.include_router(jobs.router)
    app.include_router(uploads.router)
    app.include_router(clips.router)
    app.include_router(enhance.router)
    app.include_router(events.router)
    mount_spa(app, Path(web_dist) if web_dist else REPO_ROOT / "web" / "dist")
    return app
