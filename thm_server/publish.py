"""Turn a landed command into events: one ``message`` per posted message, then ``project``.

Messages go first so a client that appends them sees the line before the
refetch the ``project`` event triggers. The ``message`` payload is a feed entry
built from the in-memory result, never re-read from the logs.
"""

from __future__ import annotations

from collections.abc import Iterable

from thm_core import Change, Generation, Message, Project

from .deps import ServerState
from .derive import feed_entry


def publish_result(
    st: ServerState,
    slug: str,
    project: Project,
    *,
    messages: list[Message],
    change: Change | None,
    generation: Generation | None,
    version: int,
    changes: Iterable[Change] = (),
    generations: Iterable[Generation] = (),
) -> None:
    """``changes`` and ``generations`` are a batch's children: each message names the change it
    is about, and the entry joins that change to the generation it made, so a child render's
    card goes out with its own record."""
    by_change = {c.id: c for c in (change, *changes) if c is not None}
    by_gen = {g.id: g for g in (generation, *generations) if g is not None}
    for m in messages:
        ch = by_change.get(m.change) if m.change else None
        gen = by_gen.get(ch.generation) if ch is not None and ch.generation else None
        st.bus.publish_event(slug, "message", feed_entry(project, m, ch, gen, st.env).model_dump(mode="json"))
    st.bus.publish(slug, version)
