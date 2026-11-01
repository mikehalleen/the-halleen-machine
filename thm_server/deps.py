"""Request-scoped access to server state and the actor context from headers."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Request

from thm_core import CommandContext, CoreSettings, Env, ProjectStore

from .errors import ApiError
from .events import EventBus
from .jobs import JobManager
from .registry import ProjectRegistry

HEADER_ACTOR = "x-thm-actor"
HEADER_SESSION = "x-thm-session"
HEADER_CURSOR = "x-thm-inbox-cursor"
HEADER_PROPOSE = "x-thm-propose"
HEADER_MODE = "x-thm-mode"  # the session mode the agent last saw: a change since refuses with mode_changed
HEADER_DOING = "x-thm-agent-doing"  # what the agent is doing right now, in its own words, on any call


@dataclass
class ServerState:
    settings: CoreSettings
    store: ProjectStore
    env: Env
    registry: ProjectRegistry
    bus: EventBus
    jobs: JobManager
    exports: object = None  # exports.ExportManager: sequence exports in flight, set by create_app
    workflows_cache: dict = field(default_factory=dict)
    enhance_requests: dict = field(default_factory=dict)  # id -> routes.enhance.AgentRequest, pending and answered
    enhance_local: dict = field(default_factory=dict)  # id -> routes.enhance.LocalRun: local rewrites in flight, for cancel
    agent_activity: dict = field(default_factory=dict)  # slug -> agent_activity.Activity: the agent's last sign of life
    agent_seen: object = None  # agent_activity.ServerSeen: the agent's last call on any path, for "is an agent running"
    provenance_probed: set = field(default_factory=set)  # (slug, media id, sha) whose file recover.py already read this process
    comfy_transport: object = None  # httpx transport for the workflow import's /history reads; tests set a mock
    uploads: object = None  # uploads_youtube.UploadManager: post uploads in flight, set by create_app
    connects: object = None  # youtube_connect.ConnectManager: channel consents in flight, set by create_app
    youtube_transport: object = None  # thm_core.youtube.Transport for the uploads; tests set a fake
    youtube_env: object = None  # the environment the YouTube credentials are read from; None = os.environ


def state(request: Request) -> ServerState:
    return request.app.state.thm


def is_private(st: ServerState, slug: str) -> bool:
    """Whether ``slug`` is a private project: one the agent may never see. An unknown or
    unreadable project is not private; the handler's own 404 answers for it."""
    try:
        return bool(st.registry.get(slug).private)
    except Exception:
        return False


def actor_context(request: Request) -> CommandContext:
    """Who is asking, from headers. Defaults to the user; the agent must say so."""
    actor = (request.headers.get(HEADER_ACTOR) or "user").strip().lower()
    if actor not in ("user", "agent"):
        raise ApiError(400, "bad_actor", f"{HEADER_ACTOR} must be user or agent, got {actor!r}")
    session = (request.headers.get(HEADER_SESSION) or "").strip() or None
    cursor_raw = (request.headers.get(HEADER_CURSOR) or "").strip()
    cursor: int | None = None
    if cursor_raw:
        try:
            cursor = int(cursor_raw)
        except ValueError:
            raise ApiError(400, "bad_cursor", f"{HEADER_CURSOR} must be an integer") from None
    propose = (request.headers.get(HEADER_PROPOSE) or "").strip().lower() in ("1", "true", "yes")
    seen_mode = (request.headers.get(HEADER_MODE) or "").strip() or None
    return CommandContext(actor=actor, session=session, inbox_cursor=cursor, propose=propose, seen_mode=seen_mode)
