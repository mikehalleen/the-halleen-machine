"""Live progress for one ComfyUI prompt, shared by ``run_images.py`` and ``run_video.py``.

ComfyUI reports what it is doing over its websocket, addressed to the ``client_id`` that
queued the prompt: ``executing`` (which node is running), ``progress`` (step ``value`` of
``max`` inside a node, mostly the sampler), and ``execution_*`` markers. Queue position is
not on the socket; it comes from ``GET /queue``, whose entries carry the ``extra_data`` the
runners send, so entries ahead of ours can be told apart as THM's or someone else's.

:class:`PromptTracker` wraps the wait between ``POST /prompt`` and the history entry:
pump the socket, poll the queue while pending, poll history until the entry exists, and
call back with a flat dict of status fields on every change. The runners write that dict
into the status file next to the fields they already keep. UI-free; prints nothing but
ASCII. Imported bare (``import comfy_progress``) like the other script helpers.

Fields reported (all optional; None when unknown)::

    phase            "queued" | "starting" | "loading models" | "sampling" | ... | "finishing"
    step, steps      the current node's progress counter, when it reports one
    pass_no, passes  which sampler this is of how many the graph has (multi-pass flows)
    queue_ahead      prompts ahead of ours in ComfyUI (running + pending before us)
    queue_ahead_ours how many of those are THM's
    task             one human line composed from the above, for the button title / notes
    progress_percent overall sampling progress across passes, or None outside sampling
    prompt_id        the ComfyUI prompt id, so a late pickup can find the entry in /history

The socket is best effort: with no websocket library, or a socket that will not connect,
only the queue and history polling run and the phase stays at "queued" / "generating".
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import requests

try:  # websocket-client: sync, simple, what the venv has
    import websocket as _wsclient
except Exception:  # pragma: no cover - environment dependent
    _wsclient = None

try:  # websockets >= 13 ships a sync client; second choice
    from websockets.sync.client import connect as _ws_connect
except Exception:  # pragma: no cover - environment dependent
    _ws_connect = None


SAMPLER_EXCLUDE = ("select", "scheduler", "sigmas", "guider", "noise")


def phase_for_class(class_type: str | None) -> str:
    """A short lowercase phase label for a node class; falls back to the humanized class name."""
    if not class_type:
        return "generating"
    c = class_type.lower()
    if "loadimage" in c or "loadvideo" in c or "loadaudio" in c or c.startswith("load_"):
        return "loading inputs"
    if "sampler" in c and not any(x in c for x in SAMPLER_EXCLUDE):
        return "sampling"
    if "loader" in c or (("load" in c) and any(x in c for x in ("checkpoint", "unet", "vae", "clip", "lora", "model", "diffusion"))):
        return "loading models"
    if "textencode" in c or "text_encode" in c or "clipencode" in c or ("prompt" in c and "encode" in c):
        return "encoding prompt"
    if "vaedecode" in c or "decode" in c:
        return "decoding"
    if "vaeencode" in c or "encode" in c:
        return "encoding"
    if "upscale" in c:
        return "upscaling"
    if "save" in c or "videocombine" in c or "combine" in c:
        return "saving"
    if "interpolat" in c or "rife" in c or "film" in c:
        return "interpolating"
    # CamelCase / snake_case -> words
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", class_type).replace("_", " ").strip().lower()
    return words or "generating"


def is_sampler(class_type: str | None) -> bool:
    return phase_for_class(class_type) == "sampling"


def _ws_url(api_base: str, client_id: str) -> str:
    parts = urlsplit(str(api_base).rstrip("/"))
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, "/ws", f"clientId={client_id}", ""))


def _is_thm(extra: Any) -> bool:
    return isinstance(extra, dict) and bool(extra.get("machine_ui_label"))


def _entry_pid(e):
    return str(e[1]) if isinstance(e, (list, tuple)) and len(e) > 1 else None


def queue_state(queue: dict, prompt_id: str) -> str | None:
    """``"running"`` / ``"pending"`` for ``prompt_id`` in a ``GET /queue`` payload, or None when
    the prompt is not there (finished, or dropped)."""
    if any(_entry_pid(e) == str(prompt_id) for e in queue.get("queue_running") or []):
        return "running"
    if any(_entry_pid(e) == str(prompt_id) for e in queue.get("queue_pending") or []):
        return "pending"
    return None


def queue_position(queue: dict, prompt_id: str) -> tuple[int | None, int | None]:
    """(ahead, ahead_ours) for ``prompt_id`` in a ``GET /queue`` payload, or (None, None)
    when the prompt is not there (finished, or not yet registered). Running entries count as
    ahead; a running entry that is ours means 0 ahead."""
    running = queue.get("queue_running") or []
    pending = queue.get("queue_pending") or []
    pid = _entry_pid

    def extra(e):
        return e[3] if isinstance(e, (list, tuple)) and len(e) > 3 else None

    for e in running:
        if pid(e) == str(prompt_id):
            return 0, 0
    ordered = sorted(pending, key=lambda e: e[0] if isinstance(e, (list, tuple)) and e and isinstance(e[0], (int, float)) else 0)
    for idx, e in enumerate(ordered):
        if pid(e) == str(prompt_id):
            before = list(running) + ordered[:idx]
            return len(before), sum(1 for b in before if _is_thm(extra(b)))
    return None, None


class _Socket:
    """The two sync websocket libraries behind one ``recv(timeout) -> str | bytes | None``."""

    def __init__(self, url: str, connect_timeout: float = 3.0):
        self._kind = None
        self._ws = None
        if _wsclient is not None:
            self._ws = _wsclient.create_connection(url, timeout=connect_timeout)
            self._kind = "client"
        elif _ws_connect is not None:
            self._ws = _ws_connect(url, open_timeout=connect_timeout)
            self._kind = "websockets"
        else:
            raise RuntimeError("no websocket library")

    def recv(self, timeout: float):
        try:
            if self._kind == "client":
                self._ws.settimeout(timeout)
                return self._ws.recv()
            return self._ws.recv(timeout=timeout)
        except Exception as exc:  # timeouts are the normal idle case
            name = type(exc).__name__.lower()
            if "timeout" in name:
                return None
            raise

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


class PromptTracker:
    """Track one prompt from queue to history. Create before ``POST /prompt`` (so the socket
    is listening under ``client_id`` when the first message arrives), then ``wait``."""

    def __init__(self, api_base: str, graph: dict, on_update: Callable[[dict], None] | None = None, *, client_id: str | None = None):
        self.api_base = str(api_base).rstrip("/")
        self.client_id = client_id or str(uuid.uuid4())
        self.on_update = on_update
        self.classes: dict[str, str] = {}
        for nid, node in (graph or {}).items():
            if isinstance(node, dict) and node.get("class_type"):
                self.classes[str(nid)] = str(node["class_type"])
        self.passes = sum(1 for c in self.classes.values() if is_sampler(c)) or None
        self.prompt_id: str | None = None
        self.sock: _Socket | None = None
        self.state: dict[str, Any] = {
            "phase": "queued", "step": None, "steps": None, "pass_no": None, "passes": self.passes,
            "queue_ahead": None, "queue_ahead_ours": None, "node": None, "task": "queued", "progress_percent": None,
        }
        self._started = False
        self._finished = False
        self.exec_started_at: float | None = None  # when ComfyUI began running the prompt (the timeout's clock)
        self._absent_since: float | None = None  # when the queue first came back without the prompt
        self.gave_up: str | None = None  # why ``wait`` returned None
        self._samplers_seen: list[str] = []
        self._last_emit = 0.0
        self._last_emitted: dict | None = None

    # -- lifecycle ----------------------------------------------------------------

    def open(self) -> "PromptTracker":
        try:
            self.sock = _Socket(_ws_url(self.api_base, self.client_id))
        except Exception as exc:
            self.sock = None
            print(f"[PROGRESS] websocket unavailable ({type(exc).__name__}); queue/history polling only")
        return self

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

    # -- the wait -------------------------------------------------------------------

    def wait(
        self, prompt_id: str, timeout_s: float, *, history_poll_s: float = 1.0, queue_poll_s: float = 2.0,
        settle_s: float = 0.0, get=None, lost_after_s: float = 120.0, heartbeat_s: float = 60.0,
        stop: Callable[[], bool] | None = None,
    ):
        """Pump until ``/history/<prompt_id>`` has the entry; return it, or None when giving up.

        ``timeout_s`` is execution time: the clock starts when ComfyUI begins running the
        prompt (``execution_start`` on the socket, or the entry seen in ``queue_running``), not
        at submission -- a prompt waiting behind others is not timing out. A prompt that is in
        neither the queue nor history for ``lost_after_s`` was dropped (ComfyUI restarted) and
        is given up on too. ``self.gave_up`` says which. ``heartbeat_s`` re-emits the unchanged
        snapshot so the status file's ``last_update`` moves while nothing else does. ``stop``,
        polled each turn, ends the wait early (``gave_up = "cancelled"``) when it returns True;
        the caller has told ComfyUI to drop the prompt, or is about to.
        """
        self.prompt_id = str(prompt_id)
        getter = get or requests.get
        hist_url = f"{self.api_base}/history/{self.prompt_id}"
        next_hist = 0.0
        next_queue = 0.0
        self.gave_up = None
        self._emit(force=True)
        while True:
            if stop is not None and stop():
                self.gave_up = "cancelled"
                return None
            now = time.time()
            if self.exec_started_at is not None and now - self.exec_started_at >= timeout_s:
                self.gave_up = f"timed out after {timeout_s:.0f}s of execution"
                return None
            if self.exec_started_at is None and self._absent_since is not None and now - self._absent_since >= lost_after_s:
                self.gave_up = f"dropped by ComfyUI (in neither its queue nor its history for {lost_after_s:.0f}s)"
                return None
            if self.sock is not None:
                self._pump(0.5 if not self._finished else 0.1)
            else:
                time.sleep(0.25)
            now = time.time()
            if not self._started and now >= next_queue:
                next_queue = now + queue_poll_s
                self._poll_queue(getter)
            if heartbeat_s and now - self._last_emit >= heartbeat_s:
                self._emit(force=True, heartbeat=True)
            if now >= next_hist:
                next_hist = now + (0.3 if self._finished else history_poll_s)
                try:
                    r = getter(hist_url, timeout=30)
                    if r.status_code == 200:
                        payload = r.json()
                        if isinstance(payload, dict) and self.prompt_id in payload:
                            if settle_s:
                                time.sleep(settle_s)
                            return payload[self.prompt_id]
                except Exception:
                    pass

    # -- sources ----------------------------------------------------------------------

    def _mark_started(self) -> None:
        self._started = True
        self._absent_since = None
        if self.exec_started_at is None:
            self.exec_started_at = time.time()

    def _poll_queue(self, getter) -> None:
        try:
            r = getter(f"{self.api_base}/queue", timeout=10)
            if r.status_code != 200:
                return
            queue = r.json()
            ahead, ours = queue_position(queue, self.prompt_id)
        except Exception:
            return
        where = queue_state(queue, self.prompt_id) if isinstance(queue, dict) else None
        if where == "running":
            self._mark_started()
        elif where is None:
            if self._absent_since is None:
                self._absent_since = time.time()
            return
        else:
            self._absent_since = None
        if ahead is None:
            return
        changed = ahead != self.state["queue_ahead"] or ours != self.state["queue_ahead_ours"]
        self.state["queue_ahead"], self.state["queue_ahead_ours"] = ahead, ours
        if ahead == 0 and self.state["phase"] == "queued":
            self.state["phase"] = "starting"
            changed = True
        if changed:
            self._emit(force=True)

    def _pump(self, timeout: float) -> None:
        """Read socket messages for up to ``timeout`` seconds, applying each."""
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            try:
                raw = self.sock.recv(min(remaining, 0.5))
            except Exception as exc:
                print(f"[PROGRESS] websocket dropped ({type(exc).__name__}); queue/history polling only")
                self.close()
                return
            if raw is None:
                return
            if isinstance(raw, (bytes, bytearray)):
                continue  # binary preview frames
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            self._apply(msg)

    def _apply(self, msg: dict) -> None:
        kind = msg.get("type")
        data = msg.get("data") or {}
        if not isinstance(data, dict):
            return
        pid = data.get("prompt_id")
        if pid is not None and self.prompt_id is not None and str(pid) != self.prompt_id:
            return
        s = self.state
        if kind == "execution_start":
            self._mark_started()
            s.update(queue_ahead=0, queue_ahead_ours=0)
            if s["phase"] in ("queued", "starting"):
                s["phase"] = "starting"
            self._emit(force=True)
        elif kind == "executing":
            node = data.get("node")
            self._mark_started()
            if node is None:
                self._finished = True
                s.update(phase="finishing", step=None, steps=None, node=None, progress_percent=100.0 if s["passes"] else None)
            else:
                node = str(node)
                cls = self.classes.get(node)
                if is_sampler(cls) and node not in self._samplers_seen:
                    self._samplers_seen.append(node)
                s.update(phase=phase_for_class(cls), node=cls, step=None, steps=None,
                         pass_no=len(self._samplers_seen) if is_sampler(cls) else s["pass_no"],
                         queue_ahead=0, queue_ahead_ours=0)
            self._emit(force=True)
        elif kind == "progress":
            try:
                value, mx = int(data.get("value")), int(data.get("max"))
            except (TypeError, ValueError):
                return
            node = data.get("node")
            if node is not None:
                cls = self.classes.get(str(node))
                if cls and s["node"] != cls:
                    if is_sampler(cls) and str(node) not in self._samplers_seen:
                        self._samplers_seen.append(str(node))
                    s.update(phase=phase_for_class(cls), node=cls, pass_no=len(self._samplers_seen) if is_sampler(cls) else s["pass_no"])
            s["step"], s["steps"] = value, mx
            if s["phase"] == "sampling" and mx > 0:
                passes = s["passes"] or 1
                done = max(0, (s["pass_no"] or 1) - 1)
                s["progress_percent"] = round(100.0 * (done + value / mx) / passes, 1)
            self._emit(force=(value == mx))
        elif kind in ("execution_error", "execution_interrupted"):
            self._finished = True
            s.update(phase="failed" if kind == "execution_error" else "cancelled")
            self._emit(force=True)
        elif kind == "execution_success":
            self._finished = True
            s.update(phase="finishing", step=None, steps=None)
            self._emit(force=True)

    # -- output ------------------------------------------------------------------------

    def task_line(self) -> str:
        s = self.state
        phase = s["phase"]
        if phase == "queued":
            ahead = s["queue_ahead"]
            if ahead is None:
                return "queued"
            if ahead == 0:
                return "queued - next up"
            ours = s["queue_ahead_ours"] or 0
            ext = ahead - ours
            detail = f"{ahead} ahead"
            if ours and ext:
                detail += f" ({ours} yours, {ext} external)"
            elif ours:
                detail += " (yours)"
            elif ext:
                detail += " (external)"
            return f"queued - {detail}"
        line = phase
        if s["step"] is not None and s["steps"]:
            if phase == "sampling" and (s["passes"] or 0) > 1:
                line = f"pass {s['pass_no'] or 1}/{s['passes']} - {s['step']}/{s['steps']}"
            else:
                line = f"{phase} {s['step']}/{s['steps']}"
        return line

    def snapshot(self) -> dict:
        out = dict(self.state)
        out["task"] = self.task_line()
        out["prompt_id"] = self.prompt_id  # so the status file names the prompt for a late pickup
        return out

    def _emit(self, force: bool = False, heartbeat: bool = False) -> None:
        if self.on_update is None:
            return
        now = time.time()
        if not force and now - self._last_emit < 0.25:
            return
        snap = self.snapshot()
        if snap == self._last_emitted and not heartbeat:
            return
        self._last_emit = now
        self._last_emitted = snap
        try:
            self.on_update(snap)
        except Exception as exc:
            print(f"[PROGRESS] on_update failed: {exc}")
