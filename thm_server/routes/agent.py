"""The agent's activity: what it is doing on a project right now, for the strip and the panel.

The record is fed by every agent call through the middleware in ``app.py``; these routes read
it for a fresh page load, and let the agent say in its own words what a longer step is
("rewriting the prompt for Video 6") without putting a line in the feed.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import agent_activity
from ..deps import actor_context, state
from ..errors import ApiError
from ..schemas import AgentActivityBody, AgentActivityOut, AgentStatusOut

router = APIRouter(prefix="/api/projects/{slug}/agent", tags=["agent"])
server_router = APIRouter(prefix="/api/agent", tags=["agent"])


@server_router.get("/status", response_model=AgentStatusOut)
async def agent_status(request: Request):
    """Server-wide: whether an agent CLI has called in lately, on any project. The system bar
    reads this to choose between the ``no agent`` pill and the ``start`` control."""
    return AgentStatusOut(**agent_activity.server_out(state(request)))


@router.get("/activity", response_model=AgentActivityOut)
async def get_activity(slug: str, request: Request):
    st = state(request)
    st.registry.get(slug)
    return AgentActivityOut(**agent_activity.out(st, slug))


@router.post("/activity", response_model=AgentActivityOut)
async def set_activity(slug: str, body: AgentActivityBody, request: Request):
    st = state(request)
    st.registry.get(slug)
    ctx = actor_context(request)
    if ctx.actor != "agent":
        raise ApiError(409, "wrong_actor", "only the agent reports its activity")
    doing = body.doing.strip()
    if not doing:
        raise ApiError(422, "bad_value", "doing is empty")
    agent_activity.note(st, slug, doing, session=ctx.session)
    return AgentActivityOut(**agent_activity.out(st, slug))
