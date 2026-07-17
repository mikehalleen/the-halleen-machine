# Notes intake for the remote interface — append-only JSONL audit log plus a
# clearable queue the agent polls. The server only appends; the agent clears.
# session.json stays agent-owned (one-writer rule), so no cross-file locking.

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NOTES_LOG_FILE = "notes.jsonl"
INPUT_QUEUE_FILE = "input-queue.json"
PRIORITIES = ("batch", "urgent")

PathLike = str | Path


def _empty_queue() -> dict[str, list]:
    return {"urgent": [], "batch": []}


class NotesLog:
    """
    Remote-note storage for one project, rooted at {workspace}/{project}-files/.

    notes.jsonl is the immutable audit trail; input-queue.json is the agent's
    inbox. Appends from concurrent server threads are serialized by a lock;
    queue rewrites are atomic (temp file + os.replace) so a crashed write can
    never corrupt the queue.
    """

    def __init__(self, files_dir: PathLike):
        self.files_dir = Path(files_dir)
        self.log_path = self.files_dir / NOTES_LOG_FILE
        self.queue_path = self.files_dir / INPUT_QUEUE_FILE
        self._lock = threading.Lock()

    # -- write side (server) -------------------------------------------------

    def append(
        self,
        text: str,
        *,
        priority: str = "batch",
        item_id: str | None = None,
        source: str = "phone",
        note_type: str = "note",
    ) -> dict[str, Any]:
        """Append one note to the JSONL log and the queue. Returns the record."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("note text is empty")
        if priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}, got {priority!r}")

        now = datetime.now(timezone.utc)
        record = {
            "id": f"note_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            "ts": now.isoformat(timespec="seconds"),
            "source": source,
            "priority": priority,
            "item_id": item_id or None,
            "type": note_type,
            "text": text,
        }

        with self._lock:
            self.files_dir.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            queue = self._read_queue()
            queue[priority].append(record)
            self._write_queue(queue)
        return record

    # -- read side (agent) ---------------------------------------------------

    def load_queue(self) -> dict[str, list]:
        """Current inbox, without clearing — for checkpoint inspection."""
        with self._lock:
            return self._read_queue()

    def pop_urgent(self) -> list[dict[str, Any]]:
        """Remove and return urgent notes. Call before each generation step."""
        return self._pop("urgent")

    def clear_batch(self) -> list[dict[str, Any]]:
        """Remove and return batch notes. Call once acted on at a QC checkpoint."""
        return self._pop("batch")

    def _pop(self, priority: str) -> list[dict[str, Any]]:
        with self._lock:
            queue = self._read_queue()
            popped = queue.get(priority, [])
            if popped:
                queue[priority] = []
                self._write_queue(queue)
            return popped

    # -- storage -------------------------------------------------------------

    def _read_queue(self) -> dict[str, list]:
        if not self.queue_path.is_file():
            return _empty_queue()
        try:
            data = json.loads(self.queue_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _empty_queue()
        queue = _empty_queue()
        if isinstance(data, dict):
            for key in PRIORITIES:
                if isinstance(data.get(key), list):
                    queue[key] = data[key]
        return queue

    def _write_queue(self, queue: dict[str, list]) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.queue_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.queue_path)


def notes_log_for_project(project_name: str) -> NotesLog:
    """NotesLog rooted at the configured {workspace}/{project}-files/ folder."""
    from client.workspace import preview_dir_for_project

    return NotesLog(preview_dir_for_project(project_name).parent)
