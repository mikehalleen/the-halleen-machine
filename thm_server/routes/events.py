"""Server-sent events: ``project`` per landed command, ``job`` per render transition, heartbeats, Last-Event-ID.

This stream ends when the client goes away or when the server shuts down (``serve.py`` closes
the bus, which hands every stream a ``None``). Without that, an open browser tab would hold
Ctrl+C at "Waiting for connections to close" until uvicorn's graceful-shutdown cap cancelled
it with a traceback.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..deps import state
from ..events import HEARTBEAT, sse_event

router = APIRouter(prefix="/api/projects/{slug}", tags=["events"])
HEARTBEAT_SECONDS = 15.0


@router.get("/events")
async def events(slug: str, request: Request):
    st = state(request)
    st.registry.get(slug)  # 404 before the stream starts
    last = (request.headers.get("last-event-id") or "").strip()

    async def gen():
        q = st.bus.subscribe(slug)
        try:
            current = st.registry.version(slug)
            if not (last.isdigit() and int(last) == current):
                yield sse_event("project", {"version": current, "slug": slug}, current)
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                    if item is None:
                        break  # server shutting down
                    event, data, event_id = item
                    yield sse_event(event, data, event_id)
                except asyncio.TimeoutError:
                    yield HEARTBEAT
                if await request.is_disconnected():
                    break
        finally:
            st.bus.unsubscribe(slug, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
