"""The server's own small ComfyUI client, for what the runners cannot do once launched.

The runners post prompts and poll history; they have no cancel path. Cancelling is the
server's job: kill the runner, then tell ComfyUI to drop the prompt. ComfyUI's queue can be
deleted from by prompt id, but only the currently executing prompt can be interrupted, and
only as "whatever is running" -- so the queue is read first and ``/interrupt`` is sent only
when the running entry is ours. The prompt is found by the label the runner already sends in
``extra_data`` (``machine_ui_label`` carries the generation id, ``machine_ui_project`` the slug).

The same label finds a prompt again after the runner is gone: :func:`late_prompt_state` asks
ComfyUI where a generation's prompt stands (finished in history, still queued, or unknown)
so a result that arrived after THM gave up can still be landed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

import httpx


def _ours(entry: Any, slug: str, gen_id: str) -> bool:
    """A ``/queue`` entry is ``[number, prompt_id, prompt, extra_data, outputs_to_execute]``."""
    extra = entry[3] if isinstance(entry, (list, tuple)) and len(entry) > 3 and isinstance(entry[3], dict) else {}
    return extra.get("machine_ui_project") == slug and gen_id in str(extra.get("machine_ui_label") or "")


def cancel_prompts(api_base: str, slug: str, gen_id: str, *, timeout_s: float = 3.0) -> str:
    """Drop this generation's prompt from ComfyUI: delete it if queued, interrupt it if running.
    Returns one line for the toast. Raises on an unreachable ComfyUI."""
    base = str(api_base).rstrip("/")
    with httpx.Client(timeout=timeout_s) as client:
        queue = client.get(f"{base}/queue").json()
        running = [e[1] for e in queue.get("queue_running") or [] if _ours(e, slug, gen_id)]
        pending = [e[1] for e in queue.get("queue_pending") or [] if _ours(e, slug, gen_id)]
        if pending:
            client.post(f"{base}/queue", json={"delete": [str(p) for p in pending]})
        if running:
            client.post(f"{base}/interrupt")
    if running:
        return "interrupted in ComfyUI"
    if pending:
        return "removed from ComfyUI's queue"
    return "not in ComfyUI's queue (already finished?)"


LateState = tuple[str, str | None, dict | None]


def _history_entries(client: httpx.Client, base: str, prompt_id: str | None, max_items: int) -> dict:
    if prompt_id:
        r = client.get(f"{base}/history/{prompt_id}")
        return r.json() if r.status_code == 200 and isinstance(r.json(), dict) else {}
    r = client.get(f"{base}/history", params={"max_items": max_items})
    return r.json() if r.status_code == 200 and isinstance(r.json(), dict) else {}


def late_prompt_state(
    api_base: str, slug: str, gen_id: str, prompt_id: str | None = None, *, timeout_s: float = 10.0, max_items: int = 200, transport=None
) -> LateState:
    """Where a generation's prompt stands in ComfyUI once THM has stopped watching it.

    ``("done", prompt_id, entry)`` with the history entry (finished, with or without error);
    ``("queued", prompt_id, None)`` while it is still running or pending; ``("gone", None, None)``
    when ComfyUI has no record of it. Looked up by prompt id when known, else by the label in
    ``extra_data`` (the newest match wins; history is oldest-first). Raises on an unreachable ComfyUI.
    """
    base = str(api_base).rstrip("/")
    with httpx.Client(timeout=timeout_s, transport=transport) as client:
        history = _history_entries(client, base, prompt_id, max_items)
        hit: tuple[str, dict] | None = None
        for pid, entry in history.items():
            if not isinstance(entry, dict):
                continue
            if (prompt_id and str(pid) == str(prompt_id)) or (not prompt_id and _ours(entry.get("prompt"), slug, gen_id)):
                hit = (str(pid), entry)
        if hit is not None:
            return "done", hit[0], hit[1]
        queue = client.get(f"{base}/queue").json()
    for key in ("queue_running", "queue_pending"):
        for e in queue.get(key) or []:
            pid = e[1] if isinstance(e, (list, tuple)) and len(e) > 1 else None
            if (prompt_id and str(pid) == str(prompt_id)) or (not prompt_id and _ours(e, slug, gen_id)):
                return "queued", str(pid), None
    return "gone", None, None


def kill_runner(proc: Any | None, pid: int | None) -> None:
    """End a runner process: through its handle when this server started it, else by the pid it
    wrote into its status file (a runner re-attached after a server restart). Never probes."""
    if proc is not None:
        try:
            proc.kill()
            return
        except Exception:
            pass
    if not pid:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.kill(int(pid), signal.SIGKILL)
    except Exception:
        pass
