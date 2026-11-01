"""Per-project change notifications and SSE framing.

Three event kinds. ``project`` with ``{"version", "slug"}`` is sent whenever a
command or decision lands, and carries the project version as the SSE id so a
reconnecting client can catch up with ``Last-Event-ID``. ``job`` carries a
generation's progress (``queued``, ``running``, ``done``, ``failed``) and
``message`` carries one session feed entry (the message with its change and
card media); neither has an id, so they never move the client's catch-up point.
Subscribers are asyncio queues of prepared ``(event, data, id)`` tuples; the bus
never blocks a publisher. ``close`` (server shutdown) puts a ``None`` sentinel on
every queue so each stream can end on its own instead of being cancelled.
"""

from __future__ import annotations

import asyncio
import json

HEARTBEAT = ": keep-alive\n\n"

Event = tuple[str, dict, int | None]


class EventBus:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self.closed = False

    def subscribe(self, slug: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        if self.closed:
            q.put_nowait(None)  # a request that got in after shutdown began: end it straight away
        self._subs.setdefault(slug, set()).add(q)
        return q

    def close(self) -> None:
        """Tell every subscriber the server is stopping. Idempotent."""
        self.closed = True
        for subs in self._subs.values():
            for q in subs:
                q.put_nowait(None)

    def unsubscribe(self, slug: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(slug)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            del self._subs[slug]

    def publish_event(self, slug: str, event: str, data: dict, id: int | None = None) -> int:
        """Queue one event for every subscriber of ``slug``; returns how many received it."""
        subs = list(self._subs.get(slug, ()))
        for q in subs:
            q.put_nowait((event, data, id))
        return len(subs)

    def publish(self, slug: str, version: int) -> int:
        """The ``project`` event: the project changed and is now at ``version``."""
        return self.publish_event(slug, "project", {"version": version, "slug": slug}, version)

    def subscriber_count(self, slug: str) -> int:
        return len(self._subs.get(slug, ()))


def sse_event(event: str, data: dict, id: int | None = None) -> str:
    lines = []
    if id is not None:
        lines.append(f"id: {id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"
