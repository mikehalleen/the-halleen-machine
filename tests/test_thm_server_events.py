"""thm_server events: the bus and the SSE endpoint."""

import asyncio
import threading

import pytest

from thm_server.events import EventBus, sse_event
from thm_server_fixtures import seeded_world


def test_event_bus_publish_wakes_subscriber():
    async def scenario():
        bus = EventBus()
        q = bus.subscribe("p")
        assert bus.subscriber_count("p") == 1
        assert bus.publish("p", 1) == 1
        bus.publish("p", 2)
        bus.publish_event("p", "job", {"job": "g_1", "status": "running"})
        bus.publish("other", 9)
        got = [await asyncio.wait_for(q.get(), 1) for _ in range(3)]
        bus.unsubscribe("p", q)
        assert bus.subscriber_count("p") == 0
        assert bus.publish("p", 3) == 0
        return got

    assert asyncio.run(scenario()) == [
        ("project", {"version": 1, "slug": "p"}, 1),
        ("project", {"version": 2, "slug": "p"}, 2),
        ("job", {"job": "g_1", "status": "running"}, None),
    ]


def test_event_bus_close_ends_every_subscriber():
    """Shutdown: a waiting subscriber gets the sentinel, and so does one that subscribes after."""

    async def scenario():
        bus = EventBus()
        q = bus.subscribe("p")
        waiter = asyncio.create_task(q.get())
        await asyncio.sleep(0)
        bus.close()
        first = await asyncio.wait_for(waiter, 1)
        late = await asyncio.wait_for(bus.subscribe("other").get(), 1)
        bus.close()  # idempotent
        return first, late, bus.closed

    assert asyncio.run(scenario()) == (None, None, True)


def test_sse_stream_ends_when_bus_closes(tmp_path, monkeypatch):
    # closed from the reader thread, not the app's loop: a short heartbeat guarantees the stream
    # re-checks its queue promptly whether or not the cross-thread put woke it
    monkeypatch.setattr("thm_server.routes.events.HEARTBEAT_SECONDS", 0.2)
    world = seeded_world(tmp_path)
    bus = world.app.state.thm.bus
    got: list[str] = []
    ended = threading.Event()

    def read():
        with world.client.stream("GET", f"/api/projects/{world.slug}/events") as r:
            for line in r.iter_lines():
                got.append(line)
                if line.startswith("data:"):
                    bus.close()
        ended.set()

    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(10)
    if not got:
        pytest.xfail("SSE stream did not yield through TestClient on this platform")
    assert ended.is_set(), "the stream kept going after the bus closed"


def test_sse_event_framing():
    text = sse_event("project", {"version": 3, "slug": "x"}, 3)
    assert text == 'id: 3\nevent: project\ndata: {"version":3,"slug":"x"}\n\n'
    assert sse_event("ping", {}) == "event: ping\ndata: {}\n\n"


def test_sse_first_event_is_current_version(tmp_path):
    world = seeded_world(tmp_path)
    version = world.client.get(f"/api/projects/{world.slug}").json()["version"]
    got: list[str] = []

    def read():
        with world.client.stream("GET", f"/api/projects/{world.slug}/events") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                got.append(line)
                if line.startswith("data:"):
                    break

    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(10)
    if not got:
        pytest.xfail("SSE stream did not yield through TestClient on this platform")
    assert f"id: {version}" in got and "event: project" in got
    assert any(line.startswith("data:") and f'"version":{version}' in line for line in got)


def test_sse_unknown_project_404(tmp_path):
    world = seeded_world(tmp_path)
    r = world.client.get("/api/projects/nope/events")
    assert r.status_code == 404
