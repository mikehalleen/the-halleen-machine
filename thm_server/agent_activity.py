"""The agent's sign of life, so the user is never left wondering whether they were heard.

The agent runs in another process and shows its thinking only there. Every call it makes to
this server is a heartbeat: the actor header says it is the agent, the path says roughly what
it is doing, and an optional ``x-thm-agent-doing`` header says it exactly ("rewriting the
prompt for Video 6"). ``note`` records that per project and publishes an ``agent`` SSE event
the strip and the Enhance button render; the browser decides what "recent" means from
``seen``. Nothing here is persisted: a restart forgets it, which is right, because a restart
also means the agent's last call is no longer a promise of anything.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from thm_core.ids import utc_now

from .deps import ServerState

PUBLISH_EVERY_S = 2.0  # the same phrase again is a heartbeat, not news; rate-limit it
# An agent CLI that is running polls the server every few seconds (its watch loop), so a gap
# this long means nothing is running -- or it is deep in a think, which the bar may briefly
# misread as absent. Generous on purpose: "no agent" should never flicker mid-session.
AVAILABLE_S = 120.0

_PATH_SLUG = re.compile(r"^/(?:api/projects|media)/([^/]+)(?:/(.*))?$")
_TAILS = (
    ("commands/generate", "queuing a render"),
    ("commands/propose_shots", "proposing shots"),
    ("commands/mark_take", "marking a take"),
    ("commands/post_message", "writing to you"),
    ("commands/", "changing the project"),
    ("enhance/requests", "picking up an enhance request"),
    ("generations", "reading the take history"),
    ("inbox", "reading your messages"),
    ("uploads", "uploading a file"),
    ("clips", "lifting a still"),
    ("bins", "reading a bin"),
)


@dataclass
class Activity:
    seen: str  # utc, for the client
    doing: str
    at: float  # monotonic, for rate limiting
    published: float = 0.0
    published_doing: str = ""


def path_slug(path: str) -> str | None:
    """The project a request path addresses, if any: ``/api/projects/{slug}...`` or ``/media/{slug}/...``."""
    m = _PATH_SLUG.match(path)
    return m.group(1) if m else None


def describe_path(method: str, path: str) -> tuple[str | None, str]:
    """``(slug, phrase)`` for an agent request, from its path alone."""
    m = _PATH_SLUG.match(path)
    if not m:
        return None, "working"
    slug, tail = m.group(1), m.group(2) or ""
    if tail.startswith("agent/activity"):
        return None, ""  # asking what the agent is doing is not the agent doing something
    if path.startswith("/media/"):
        return slug, "looking at a take"
    for prefix, phrase in _TAILS:
        if tail.startswith(prefix):
            if prefix == "enhance/requests" and method == "POST":
                return slug, "answering an enhance request"
            return slug, phrase
    return slug, "reading the project"


def note(st: ServerState, slug: str, doing: str) -> Activity:
    """Record what the agent is doing on ``slug`` and tell the browser, unless it is the same
    phrase within the last couple of seconds."""
    now = time.monotonic()
    act = st.agent_activity.get(slug)
    if act is None:
        act = Activity(seen=utc_now(), doing=doing, at=now)
        st.agent_activity[slug] = act
    else:
        act.seen, act.doing, act.at = utc_now(), doing, now
    if doing != act.published_doing or now - act.published >= PUBLISH_EVERY_S:
        act.published, act.published_doing = now, doing
        st.bus.publish_event(slug, "agent", out(st, slug))
    return act


def out(st: ServerState, slug: str) -> dict:
    act = st.agent_activity.get(slug)
    if act is None:
        return {"seen": None, "doing": None, "idle_s": None}
    return {"seen": act.seen, "doing": act.doing, "idle_s": round(time.monotonic() - act.at, 1)}


# -- server-wide: is any agent talking to us -----------------------------------------


@dataclass
class ServerSeen:
    seen: str  # utc, for the client
    at: float  # monotonic


def note_server(st: ServerState) -> None:
    """Any call with the agent actor header, on any path (the project list included), is proof
    an agent CLI is running against this server."""
    st.agent_seen = ServerSeen(seen=utc_now(), at=time.monotonic())


def server_out(st: ServerState) -> dict:
    seen = st.agent_seen
    if not isinstance(seen, ServerSeen):
        return {"available": False, "seen": None, "idle_s": None}
    idle = time.monotonic() - seen.at
    return {"available": idle <= AVAILABLE_S, "seen": seen.seen, "idle_s": round(idle, 1)}
