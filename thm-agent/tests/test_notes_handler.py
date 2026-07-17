"""Tests for the remote-interface notes intake and gallery server."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_AGENT_DIR = Path(__file__).resolve().parents[1]
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from server.gallery_server import GalleryServer  # noqa: E402
from server.notes_handler import NotesLog  # noqa: E402


@pytest.fixture
def notes(tmp_path) -> NotesLog:
    return NotesLog(tmp_path / "demo-files")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestNotesLog:
    def test_append_writes_log_and_queue(self, notes):
        record = notes.append("arm too stiff", item_id="vid31-B")
        assert record["priority"] == "batch"
        assert record["item_id"] == "vid31-B"
        assert record["id"].startswith("note_")

        log_lines = _read_jsonl(notes.log_path)
        assert len(log_lines) == 1
        assert log_lines[0]["text"] == "arm too stiff"

        queue = notes.load_queue()
        assert [n["id"] for n in queue["batch"]] == [record["id"]]
        assert queue["urgent"] == []

    def test_append_rejects_empty_and_bad_priority(self, notes):
        with pytest.raises(ValueError):
            notes.append("   ")
        with pytest.raises(ValueError):
            notes.append("hi", priority="asap")

    def test_pop_urgent_leaves_batch(self, notes):
        notes.append("batch note")
        urgent = notes.append("stop the batch", priority="urgent")

        popped = notes.pop_urgent()
        assert [n["id"] for n in popped] == [urgent["id"]]

        queue = notes.load_queue()
        assert queue["urgent"] == []
        assert len(queue["batch"]) == 1
        # audit log keeps everything
        assert len(_read_jsonl(notes.log_path)) == 2

    def test_clear_batch(self, notes):
        notes.append("one")
        notes.append("two")
        assert len(notes.clear_batch()) == 2
        assert notes.clear_batch() == []

    def test_corrupt_queue_recovers(self, notes):
        notes.files_dir.mkdir(parents=True, exist_ok=True)
        notes.queue_path.write_text("{not json", encoding="utf-8")
        assert notes.load_queue() == {"urgent": [], "batch": []}
        notes.append("still works")
        assert len(notes.load_queue()["batch"]) == 1

    def test_no_item_id_is_none(self, notes):
        record = notes.append("general note", item_id=None)
        assert record["item_id"] is None


@pytest.fixture
def live_server(tmp_path):
    preview_dir = tmp_path / "demo-files" / "previews"
    preview_dir.mkdir(parents=True)
    (preview_dir / "index.html").write_text("<html>gallery</html>", encoding="utf-8")
    (preview_dir / "vid31-B.mp4").write_bytes(b"0123456789abcdef")

    server = GalleryServer("demo", preview_dir, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=5)


class TestGalleryServer:
    def test_status(self, live_server):
        _server, base = live_server
        data = json.loads(_get(f"{base}/api/status").read())
        assert data["status"] == "ok"
        assert data["project"] == "demo"
        assert data["queue"] == {"urgent": 0, "batch": 0}

    def test_serves_index_and_media(self, live_server):
        _server, base = live_server
        assert b"gallery" in _get(f"{base}/").read()
        assert _get(f"{base}/vid31-B.mp4").read() == b"0123456789abcdef"

    def test_range_request(self, live_server):
        _server, base = live_server
        resp = _get(f"{base}/vid31-B.mp4", headers={"Range": "bytes=4-7"})
        assert resp.status == 206
        assert resp.headers["Content-Range"] == "bytes 4-7/16"
        assert resp.read() == b"4567"

    def test_post_note_lands_in_queue(self, live_server):
        server, base = live_server
        body = json.dumps(
            {"text": "hat morph on the spin", "priority": "urgent", "item_id": "vid31-B"}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/notes",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        data = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert data["status"] == "ok"

        queue = server.notes.load_queue()
        assert queue["urgent"][0]["item_id"] == "vid31-B"
        assert queue["urgent"][0]["text"] == "hat morph on the spin"

    def test_post_rejects_bad_payload(self, live_server):
        _server, base = live_server
        req = urllib.request.Request(
            f"{base}/api/notes",
            data=b'{"text": ""}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400

    def test_path_traversal_blocked(self, live_server):
        _server, base = live_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"{base}/../../secret.txt")
        assert exc.value.code == 404
