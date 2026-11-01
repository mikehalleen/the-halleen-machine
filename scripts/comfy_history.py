"""ComfyUI ``/history`` helpers shared by ``run_images.py`` and ``run_video.py``.

ComfyUI's history entry for a prompt id lists every output file exactly, per node,
so the runners no longer have to guess the newest file by mtime. This module is
UI-free and prints nothing but ASCII. The runners import it bare
(``import comfy_history``), the same way they import ``workflow_controls``.

History entry shape (the parts used here)::

    {
      "outputs": {"<node_id>": {"images": [{"filename", "subfolder", "type"}],
                                "gifs":   [...same keys, plus format/frame_rate...],
                                "videos": [...]}},
      "status":  {"status_str": "success" | "error", "completed": bool, "messages": [...]}
    }

Core ``SaveImage`` and ``SaveVideo`` report under ``images``; ``VHS_VideoCombine``
reports under ``gifs``; some packs use ``videos``. Only ``type == "output"`` files
count; ``temp`` previews and ``input`` echoes are ignored.
"""

from __future__ import annotations

import os
import time

import requests

HISTORY_MEDIA_KEYS = ("images", "gifs", "videos")


def poll_history_entry(api_base, prompt_id, timeout_s, *, poll_s=1.0, settle_s=0.0, get=None):
    """GET ``/history/<prompt_id>`` until the entry appears; return it, or None on timeout.

    Request errors are swallowed and retried until ``timeout_s`` elapses. ``settle_s``
    is slept once after the entry appears so a save node can flush before the caller
    reads or rewrites the file (Windows). ``get`` is injectable for tests.
    """
    getter = get or requests.get
    url = str(api_base).rstrip("/") + f"/history/{prompt_id}"
    t0 = time.time()
    while True:
        try:
            r = getter(url, timeout=30)
            if r.status_code == 200:
                payload = r.json()
                if isinstance(payload, dict) and prompt_id in payload:
                    if settle_s:
                        time.sleep(settle_s)
                    return payload[prompt_id]
        except Exception:
            pass
        if time.time() - t0 >= timeout_s:
            return None
        time.sleep(poll_s)


def history_error_detail(entry):
    """A short error string when the entry reports an execution error, else None."""
    if not isinstance(entry, dict):
        return None
    status = entry.get("status") or {}
    if not isinstance(status, dict):
        return None
    status_str = str(status.get("status_str") or "").lower()
    if status_str != "error":
        return None
    messages = [m for m in (status.get("messages") or []) if isinstance(m, (list, tuple)) and len(m) >= 2]
    preferred = [m for m in messages if "error" in str(m[0]).lower()] or messages
    for msg in preferred:
        body = msg[1]
        if isinstance(body, dict):
            text = body.get("exception_message") or body.get("message") or body
        else:
            text = body
        return str(text)[:500]
    return status_str


EXEC_END_MARKS = ("execution_success", "execution_error", "execution_interrupted")


def history_exec_seconds(entry):
    """How long ComfyUI spent running the prompt, in seconds, from the entry's own clock.

    ``status.messages`` lists ``["execution_start", {"timestamp": <ms>}]`` when the prompt
    left the queue and one of ``execution_success`` / ``execution_error`` /
    ``execution_interrupted`` when it stopped; queue time is not in it. None when the entry
    lacks either mark (an older ComfyUI, or a hand-made entry).
    """
    if not isinstance(entry, dict):
        return None
    status = entry.get("status") or {}
    if not isinstance(status, dict):
        return None
    start = end = None
    for msg in status.get("messages") or []:
        if not isinstance(msg, (list, tuple)) or len(msg) < 2 or not isinstance(msg[1], dict):
            continue
        kind, ts = str(msg[0]), msg[1].get("timestamp")
        if not isinstance(ts, (int, float)):
            continue
        if kind == "execution_start" and start is None:
            start = float(ts)
        elif kind in EXEC_END_MARKS:
            end = float(ts)
    if start is None or end is None or end < start:
        return None
    return round((end - start) / 1000.0, 1)


def extract_history_outputs(entry, output_root, *, prompt_id="", label=""):
    """Every ``type == "output"`` media file the entry reports, as absolute paths.

    Returns a list of ``{"path", "node_id", "prompt_id", "label"}`` in node order,
    deduplicated by path. ``output_root`` is the ComfyUI output directory.
    """
    if not isinstance(entry, dict):
        return []
    outputs = entry.get("outputs")
    if not isinstance(outputs, dict):
        return []
    root = str(output_root or "")
    seen: set[str] = set()
    found: list[dict] = []
    for node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for key in HISTORY_MEDIA_KEYS:
            for item in node_out.get(key) or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "output":
                    continue
                filename = item.get("filename") or ""
                if not filename:
                    continue
                path = os.path.abspath(os.path.join(root, item.get("subfolder") or "", filename))
                marker = os.path.normcase(path)
                if marker in seen:
                    continue
                seen.add(marker)
                found.append({"path": path, "node_id": str(node_id), "prompt_id": str(prompt_id or ""), "label": str(label or "")})
    return found


def outputs_in_folder(found, folder, exts, *, prefix=None):
    """Paths from ``found`` that sit directly in ``folder`` with one of ``exts``.

    ``prefix`` (case-insensitive basename prefix) narrows further when given.
    """
    folder_nc = os.path.normcase(os.path.abspath(str(folder)))
    exts_l = tuple(e.lower() for e in exts)
    hits = []
    for o in found:
        path = o.get("path") or ""
        if not path.lower().endswith(exts_l):
            continue
        if os.path.normcase(os.path.dirname(path)) != folder_nc:
            continue
        if prefix and not os.path.basename(path).lower().startswith(str(prefix).lower()):
            continue
        hits.append(path)
    return hits
