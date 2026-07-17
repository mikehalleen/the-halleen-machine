# Home-LAN gallery server — serves the existing preview gallery over HTTP so a
# phone can watch progress, and accepts notes via POST /api/notes. Stdlib only.
#
# One-writer rule: this server never touches session.json or project JSON; it
# only appends to notes.jsonl / input-queue.json via NotesLog.

from __future__ import annotations

import json
import mimetypes
import re
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from server.notes_handler import NotesLog

MAX_NOTE_BODY_BYTES = 64 * 1024
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")

_FORM_PATH = Path(__file__).resolve().parent / "phone_form.html"


def lan_ip() -> str:
    """Best-effort LAN IP for printing a phone-friendly URL."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no packets sent; just picks a route
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class GalleryRequestHandler(BaseHTTPRequestHandler):
    server: "GalleryServer"
    protocol_version = "HTTP/1.1"

    # -- routing -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("", "/", "/index.html"):
            self._serve_media(self.server.preview_dir / "index.html")
        elif route == "/note":
            self._serve_note_form(parse_qs(parsed.query))
        elif route == "/api/status":
            self._serve_status()
        else:
            self._serve_media(self._resolve_media(route))

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/notes":
            self._send_json({"status": "error", "reason": "unknown endpoint"}, code=404)
            return
        try:
            payload = self._read_json_body()
        except ValueError as e:
            self._send_json({"status": "error", "reason": str(e)}, code=400)
            return

        priority = str(payload.get("priority") or "batch")
        item_id = payload.get("item_id") or None
        try:
            record = self.server.notes.append(
                str(payload.get("text") or ""),
                priority=priority,
                item_id=str(item_id) if item_id else None,
            )
        except ValueError as e:
            self._send_json({"status": "error", "reason": str(e)}, code=400)
            return
        self._send_json(
            {"status": "ok", "note_id": record["id"], "received_at": record["ts"]}
        )

    # -- handlers ------------------------------------------------------------

    def _serve_status(self) -> None:
        queue = self.server.notes.load_queue()
        self._send_json(
            {
                "status": "ok",
                "project": self.server.project_name,
                "uptime_sec": round(time.time() - self.server.started_at, 1),
                "queue": {k: len(v) for k, v in queue.items()},
            }
        )

    def _serve_note_form(self, query: dict[str, list[str]]) -> None:
        try:
            doc = _FORM_PATH.read_text(encoding="utf-8")
        except OSError:
            self._send_json({"status": "error", "reason": "form missing"}, code=500)
            return
        item = (query.get("item") or [""])[0]
        doc = doc.replace("{{PROJECT}}", self.server.project_name)
        doc = doc.replace("{{ITEM_ID}}", item)
        body = doc.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve_media(self, route: str) -> Path | None:
        """Map a URL path to a file inside preview_dir; None if it escapes."""
        name = route.lstrip("/")
        if not name or "\x00" in name:
            return None
        candidate = (self.server.preview_dir / name).resolve()
        preview_root = self.server.preview_dir.resolve()
        if preview_root not in candidate.parents and candidate != preview_root:
            return None
        return candidate

    def _serve_media(self, path: Path | None) -> None:
        if path is None or not path.is_file():
            self._send_json({"status": "error", "reason": "not found"}, code=404)
            return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        # Single-range support — iOS Safari refuses to play video without it.
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        is_partial = False
        if range_header:
            m = _RANGE_RE.match(range_header.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                else:  # suffix range: last N bytes
                    start = max(size - int(m.group(2)), 0)
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                is_partial = True

        length = end - start + 1
        self.send_response(206 if is_partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if is_partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if path.name == "index.html":
            self.send_header("Cache-Control", "no-store")
        self.end_headers()

        try:
            with path.open("rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(remaining, 64 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass  # phone scrubbed/closed the video mid-transfer; normal

    # -- plumbing ------------------------------------------------------------

    def _read_json_body(self) -> dict:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":
            raise ValueError("expected application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_NOTE_BODY_BYTES:
            raise ValueError("bad content length")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("invalid JSON body")
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload

    def _send_json(self, obj: dict, *, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Quiet by default; media GETs are too chatty for an agent-run console.
        if self.command == "POST":
            print(f"[serve] {self.address_string()} {fmt % args}", flush=True)


class GalleryServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, SO_REUSEADDR lets a second server silently double-bind the
    # same port — requests then hit whichever process bound first. Bind
    # exclusively so a duplicate `serve` fails loudly instead.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(
            exc,
            (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError),
        ):
            return  # phone closed/scrubbed mid-request; routine, not worth a traceback
        super().handle_error(request, client_address)

    def __init__(self, project_name: str, preview_dir: Path, host: str, port: int):
        self.project_name = project_name
        self.preview_dir = Path(preview_dir)
        self.notes = NotesLog(self.preview_dir.parent)
        self.started_at = time.time()
        super().__init__((host, port), GalleryRequestHandler)


def serve_gallery(project_name: str, *, host: str = "0.0.0.0", port: int = 8000) -> None:
    """Blocking entry point used by `cli.py serve`. Ctrl+C to stop."""
    from client.workspace import preview_dir_for_project

    preview_dir = preview_dir_for_project(project_name)
    if not (preview_dir / "index.html").is_file():
        print(
            f"WARNING: no gallery yet at {preview_dir / 'index.html'} — "
            f"run `gallery --project {project_name}` first (media still serves fine)."
        )
    server = GalleryServer(project_name, preview_dir, host, port)
    shown_host = lan_ip() if host in ("0.0.0.0", "") else host
    print(f"Serving {project_name} gallery from {preview_dir}")
    print(f"  phone URL:  http://{shown_host}:{port}/")
    print(f"  note form:  http://{shown_host}:{port}/note")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
