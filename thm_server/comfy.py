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


def restart_comfy(script: str, cwd: str, log_path: Any) -> Any:
    """Run the user's ComfyUI restart script, detached: it outlives this server (a ``.ps1`` is
    run through PowerShell with the user's profile, as they run it themselves; anything else is
    run as a program). Output goes to ``log_path``. Raises when the script is missing."""
    from pathlib import Path

    path = Path(script)
    if not path.is_file():
        raise FileNotFoundError(f"restart script not found: {script}")
    if path.suffix.lower() == ".ps1":
        cmd = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    else:
        cmd = [str(path)]
    log = Path(log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    with open(log, "ab") as out:
        out.write(f"\n==== restart {__import__('datetime').datetime.now().isoformat(timespec='seconds')} ====\n".encode("utf-8"))
        return subprocess.Popen(cmd, cwd=cwd or None, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kwargs)


def recent_runs(api_base: str, *, max_items: int = 50, timeout_s: float = 10.0, transport=None) -> list[tuple[str, dict]]:
    """``/history`` entries newest first, for importing a workflow the user ran in ComfyUI.
    Read-only. Raises on an unreachable ComfyUI."""
    base = str(api_base).rstrip("/")
    with httpx.Client(timeout=timeout_s, transport=transport) as client:
        history = _history_entries(client, base, None, max_items)
    entries = [(str(pid), e) for pid, e in history.items() if isinstance(e, dict)]

    def created(item: tuple[str, dict]) -> float:
        prompt = item[1].get("prompt") or []
        extra = prompt[3] if isinstance(prompt, (list, tuple)) and len(prompt) > 3 and isinstance(prompt[3], dict) else {}
        return float(extra.get("create_time") or 0)

    # history is oldest-first; the stable sort keeps that order for entries without a time
    return sorted(reversed(entries), key=created, reverse=True)


def run_entry(api_base: str, prompt_id: str, *, timeout_s: float = 10.0, transport=None) -> dict | None:
    base = str(api_base).rstrip("/")
    with httpx.Client(timeout=timeout_s, transport=transport) as client:
        return _history_entries(client, base, prompt_id, 1).get(str(prompt_id))
