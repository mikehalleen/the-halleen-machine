"""HTTP client for the THM server, for harnesses that drive a project as the agent.

Standard library only, so it runs on any machine with Python 3.10+: JSON over
``urllib``. The agent never uploads a file, so there is no multipart here. Every
write goes through the server's command API with the actor, session, inbox
cursor, and propose headers (``thm_server/deps.py``); the client has no path to
a project file and no reason to know one (design section 6).

    client = ThmClient("http://127.0.0.1:8010")
    detail = client.wait_for_session()              # the user posted a brief
    client.connect(detail["slug"], "claude-code")
    messages, cursor = client.inbox(slug)           # read before every generate
    client.command(slug, "set_shot_field", {"shot": s, "field": "length_s", "value": 8})
    client.generate(slug, s)                        # refused with inbox_behind if unread items exist

``transport`` is injectable: tests hand in a callable over Starlette's TestClient.
"""

from __future__ import annotations

import base64
import difflib
import json
import mimetypes
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

Transport = Callable[[str, str, "bytes | None", dict[str, str]], tuple[int, bytes, dict[str, str]]]

HEADER_ACTOR = "x-thm-actor"
HEADER_SESSION = "x-thm-session"
HEADER_CURSOR = "x-thm-inbox-cursor"
HEADER_PROPOSE = "x-thm-propose"
# the session mode this agent last saw; the server refuses a changing command with mode_changed
# when the user has switched mode since, so a plan never runs on in a mode it was not made for
HEADER_MODE = "x-thm-mode"
HEADER_DOING = "x-thm-agent-doing"  # what the agent is doing, shown in the app's strip while it works
ALERT_PREFIX = "alert: "  # the server's own system lines (thm_server.jobs.ALERT_PREFIX): a stall, an outage, a restart
LIVE_SESSION = ("waiting", "live")
TERMINAL_GENERATION = ("done", "failed", "cancelled")

# The same token the server scans for (thm_core/slug.py TOKEN_RE), copied rather than imported:
# this client runs on machines with no thm_core. tests/test_thm_client.py asserts they match.
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9\-@])@([a-z0-9][a-z0-9-]*)\.([a-z][a-z0-9]{0,4})(?:\.(\d+))?(?![A-Za-z0-9\-])")

# What the runners read out of ``generate --params``, by kind (scripts/run_images.py and
# scripts/run_video.py). Anything else is accepted by the server, stored on the generation,
# and silently ignored by the runner -- so the client warns.
PARAM_KEYS = {
    "image": ("seed", "steps", "cfg", "sampler", "scheduler", "width", "height", "draft"),
    "video": ("seed", "steps", "fps", "length_s", "quarter", "width", "height", "draft"),
}


def handles_in(text: str) -> set[str]:
    out: set[str] = set()
    for m in TOKEN_RE.finditer(text or ""):
        out.add(m.group(1) + "." + m.group(2) + (f".{m.group(3)}" if m.group(3) else ""))
    return out


def check_tokens(prompt: str, negative: str, references: list[dict], addresses_by_token: bool) -> dict:
    """The handle check the server runs at render time, before the render: ``unknown`` tokens the
    prompt names that no reference answers to, ``unused`` bound handles the prompt never mentions
    (only on workflows that address references by token), and ``used``."""
    used = handles_in(prompt) | handles_in(negative)
    known = {str(r.get("handle") or "") for r in references}
    return {
        "unknown": sorted(used - known),
        "unused": sorted(known - used) if addresses_by_token else [],
        "used": sorted(used & known),
    }


def unknown_params(kind: str | None, params: dict | None) -> list[str]:
    """Param keys the runner for ``kind`` never reads."""
    allowed = PARAM_KEYS.get(kind or "", ())
    return sorted(k for k in (params or {}) if k not in allowed)


def prompt_diff(previous: str, current: str) -> str:
    """A unified diff of two typed prompts, by line, empty when they match."""
    if (previous or "") == (current or ""):
        return ""
    lines = difflib.unified_diff((previous or "").splitlines(), (current or "").splitlines(), "previous", "this take", lineterm="", n=1)
    return "\n".join(list(lines)[2:])


class ThmError(Exception):
    """The server refused: ``{code, detail, extra}`` with the HTTP status."""

    def __init__(self, status: int, code: str, detail: str = "", extra: dict | None = None):
        super().__init__(f"{status} {code}: {detail}" if detail else f"{status} {code}")
        self.status = status
        self.code = code
        self.detail = detail
        self.extra = extra or {}


def urllib_transport(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return int(resp.status), resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(), {k.lower(): v for k, v in exc.headers.items()}


class ThmClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8010",
        *,
        session: str | None = None,
        cursor: int | None = None,
        transport: Transport | None = None,
        doing: str | None = None,
        mode: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        # this client is the agent and only the agent: the private-project gate keys on the actor
        # header, so there is no way to send another one from here
        self.actor = "agent"
        self.session = session
        self.cursor = cursor
        # the session mode last read from a command answer or the inbox (``auto``, ``manual+drafts``)
        self.mode = mode
        self.transport = transport or urllib_transport
        # every request is a heartbeat the app shows the user; this names it ("reading your messages")
        self.doing = doing

    # -- transport --------------------------------------------------------------

    def _headers(self, *, json_body: bool, propose: bool, cursor: int | None) -> dict[str, str]:
        h = {"accept": "application/json", HEADER_ACTOR: self.actor}
        if json_body:
            h["content-type"] = "application/json"
        if self.session:
            h[HEADER_SESSION] = self.session
        if self.doing and self.actor == "agent":
            h[HEADER_DOING] = self.doing.encode("ascii", "replace").decode("ascii")
        if cursor is not None:
            h[HEADER_CURSOR] = str(int(cursor))
        if self.mode:
            h[HEADER_MODE] = self.mode
        if propose:
            h[HEADER_PROPOSE] = "1"
        return h

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: dict | None = None,
        propose: bool = False,
        cursor: int | None = None,
        raw: bool = False,
    ) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode("utf-8") if body is not None else None
        status, payload, _ = self.transport(method, url, data, self._headers(json_body=data is not None, propose=propose, cursor=cursor))
        if status >= 400:
            try:
                err = json.loads(payload.decode("utf-8")) if payload else {}
            except ValueError:
                err = {}
            if not isinstance(err, dict):
                err = {}
            raise ThmError(status, str(err.get("code") or "http_error"), str(err.get("detail") or payload[:200].decode("utf-8", "replace")), err.get("extra") or {})
        if raw:
            return payload
        return json.loads(payload.decode("utf-8")) if payload else None

    def get(self, path: str, params: dict | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Any, *, propose: bool = False, cursor: int | None = None) -> Any:
        return self.request("POST", path, body=body, propose=propose, cursor=cursor)

    # -- reads ------------------------------------------------------------------

    def health(self) -> dict:
        return self.get("/api/health")

    def projects(self) -> list[dict]:
        return self.get("/api/projects")

    def create_project(self, name: str, brief: str | None = None, settings: dict | None = None) -> dict:
        """Harness-first: a project, optionally with the brief the user gave the harness and its settings."""
        body: dict[str, Any] = {"name": name}
        if brief and brief.strip():
            body["brief"] = brief.strip()
        if settings:
            body["settings"] = dict(settings)
        return self.post("/api/projects", body)

    def project(self, slug: str) -> dict:
        return self.get(f"/api/projects/{_q(slug)}")

    def bin(self, slug: str, bin_id: str) -> dict:
        """One bin: its recipe, gallery (newest first), selected media, references with handles, capacity, active job."""
        return self.get(f"/api/projects/{_q(slug)}/bins/{_q(bin_id)}")

    def bins(self, slug: str) -> list[dict]:
        """Every bin, newest first, with what it holds and the media it leads with."""
        return self.get(f"/api/projects/{_q(slug)}/bins")

    def media(self, slug: str, bin: str | None = None, type: str | None = None, limit: int | None = None) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/media", {"bin": bin, "type": type, "limit": limit})

    def feed(self, slug: str, since: int = 0) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/feed", {"since": since})

    def workflows(self) -> list[dict]:
        """Every workflow the server offers: name, kind (image or video), slots, negative support."""
        return self.get("/api/workflows")

    def comfy_runs(self, limit: int = 10) -> list[dict]:
        """The user's recent ComfyUI runs, newest first (THM's own renders left out): what an import starts from."""
        return self.get("/api/workflows/comfy-runs", {"limit": limit})

    def import_workflow(
        self, name: str, *, run: str | None = None, png: bytes | None = None, kind: str | None = None,
        tags: dict[str, str] | None = None, untag: list[str] | None = None, system_prompt: str | None = None,
        keep_builtin_enhancer: bool = False, lora_marker: bool = True, dry_run: bool = False, replace: bool = False,
    ) -> dict:
        """Tag and save a workflow the user ran in ComfyUI: the API graph THM renders with, and a
        tagged UI copy in samples/workflows_openincomfy for the user to open. Returns the report."""
        body: dict = {"name": name, "tags": dict(tags or {}), "untag": list(untag or []), "keep_builtin_enhancer": keep_builtin_enhancer,
                      "lora_marker": lora_marker, "dry_run": dry_run, "replace": replace}
        if run:
            body["run"] = run
        if png is not None:
            body["png_b64"] = base64.b64encode(png).decode("ascii")
        if kind:
            body["kind"] = kind
        if system_prompt:
            body["system_prompt"] = system_prompt
        return self.post("/api/workflows/import", body)

    def loras(self) -> list[dict]:
        """Every LoRA file ComfyUI can load: name (exactly as a bin's loras field wants it), size."""
        return self.get("/api/loras")

    def jobs(self, slug: str) -> list[dict]:
        return self.get(f"/api/projects/{_q(slug)}/jobs")

    def generation(self, slug: str, gen_id: str) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/generations/{_q(gen_id)}")

    def change(self, slug: str, change_id: str) -> dict:
        """One change's latest record: a propose_shots card's items carry the outcome in ``payload``."""
        return self.get(f"/api/projects/{_q(slug)}/changes/{_q(change_id)}")

    # -- sequences --------------------------------------------------------------

    def sequences(self, slug: str) -> list[dict]:
        """Every sequence with its items joined to the takes that play, durations and the last export."""
        return self.get(f"/api/projects/{_q(slug)}/sequences")

    def sequence(self, slug: str, seq_id: str) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/sequences/{_q(seq_id)}")

    def export_status(self, slug: str, seq_id: str) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/sequences/{_q(seq_id)}/export")

    def preview_item(self, slug: str, seq_id: str, bin: str, *, handles_s: float = 0.0) -> bytes:
        """One item rendered with its layer slices and mix through the export's own graph, plus
        up to ``handles_s`` of the take either side: an mp4 for a QC listen, not media."""
        return self.request("POST", f"/api/projects/{_q(slug)}/sequences/{_q(seq_id)}/preview", body={"bin": bin, "handles_s": handles_s}, raw=True)

    def export_sequence(self, slug: str, seq_id: str, *, bin: str | None = None) -> dict:
        """Start the export; it lands in the sequence's output bin as the next take. ``bin``
        points the sequence at another bin first."""
        return self.post(f"/api/projects/{_q(slug)}/sequences/{_q(seq_id)}/export", {"bin": bin} if bin else {})

    def wait_export(self, slug: str, seq_id: str, *, poll_s: float = 1.0, timeout_s: float | None = None, sleep: Callable[[float], None] = time.sleep) -> dict:
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            status = self.export_status(slug, seq_id)
            if status.get("status") in ("done", "failed", "idle"):
                return status
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"export of {seq_id} still running after {timeout_s}s")
            sleep(poll_s)

    # -- posts -------------------------------------------------------------------

    def posts(self, slug: str, bin: str | None = None) -> list[dict]:
        """Every post (a video with its title and description, drafted for a platform), newest
        first, each with its bin's name and the video it names; ``bin`` narrows to one bin's."""
        return list(self.get(f"/api/projects/{_q(slug)}/posts", {"bin": bin} if bin else None) or [])

    def post_detail(self, slug: str, post_id: str) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/posts/{_q(post_id)}")

    def create_post(self, slug: str, bin: str, *, media: str | None = None, title: str = "", description: str = "", tags: list[str] | None = None, platform: str = "youtube", **flags) -> dict:
        """Draft a post for a bin's current video (or ``media``, a video in that bin); held
        private and unsent. ``flags`` are ``reason``, ``card``, ``quiet``, ``requested``."""
        payload: dict[str, Any] = {"bin": bin, "platform": platform, "title": title, "description": description}
        if media:
            payload["media"] = media
        if tags is not None:
            payload["tags"] = list(tags)
        payload.update({k: v for k, v in flags.items() if v})
        return self.command(slug, "create_post", payload, cursor=self.cursor)

    def set_post_field(self, slug: str, post_id: str, field: str, value: Any, **flags) -> dict:
        payload: dict[str, Any] = {"post": post_id, "field": field, "value": value}
        payload.update({k: v for k, v in flags.items() if v})
        return self.command(slug, "set_post_field", payload, cursor=self.cursor)

    def upload_post(self, slug: str, post_id: str, *, requested: bool = False, reason: str | None = None) -> dict:
        """Send the post's video to its platform, private. A card (proposed) unless ``requested``:
        the user asked for it in the feed, and it lands and starts at once."""
        payload: dict[str, Any] = {"post": post_id}
        if reason:
            payload["reason"] = reason
        if requested:
            payload["requested"] = True
        return self.command(slug, "upload_post", payload, propose=not requested, cursor=self.cursor)

    def upload_status(self, slug: str, post_id: str) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/posts/{_q(post_id)}/upload")

    def wait_upload(self, slug: str, post_id: str, *, poll_s: float = 2.0, timeout_s: float | None = None, sleep: Callable[[float], None] = time.sleep) -> dict:
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            status = self.upload_status(slug, post_id)
            if status.get("status") in ("done", "failed", "idle"):
                return status
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"upload of {post_id} still running after {timeout_s}s")
            sleep(poll_s)

    def publish_status(self, platform: str = "youtube") -> dict:
        """Whether this server can upload to the platform, and the fix when it cannot."""
        return self.get(f"/api/publish/{_q(platform)}")

    def delete_post(self, slug: str, post_id: str, **flags) -> dict:
        payload: dict[str, Any] = {"post": post_id}
        payload.update({k: v for k, v in flags.items() if v})
        return self.command(slug, "delete_post", payload, cursor=self.cursor)

    def generations(self, slug: str, bin: str | None = None, status: str | None = None) -> list[dict]:
        """Every generation in landing order, narrowed by bin and status when given."""
        return list(self.get(f"/api/projects/{_q(slug)}/generations", {"bin": bin, "status": status}) or [])

    def media_record(self, slug: str, media_id: str) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/media/{_q(media_id)}")

    def recipe(self, slug: str, media_id: str) -> dict:
        """How a media was made, with the generation's full params and status merged in: the
        recipe route keeps seed and length only, the generation record keeps everything. Media
        with no record whose file carries the ComfyUI graph that made it (an output fetched by
        hand after a timeout) gets its record rebuilt from the file on this read."""
        rec = self.get(f"/api/projects/{_q(slug)}/media/{_q(media_id)}/recipe")
        if not rec.get("found") or not rec.get("generation"):
            return rec
        gen = self.generation(slug, rec["generation"])
        return {**rec, "params": dict(gen.get("params") or {}), "status": gen.get("status"), "outputs": list(gen.get("outputs") or []),
                "warnings": list(gen.get("warnings") or []), "prompt_submitted": gen.get("prompt_submitted", "")}

    def workflow(self, name: str) -> dict:
        """One workflow with its prompt guide: the rules the Enhance button rewrites by."""
        return self.get(f"/api/workflows/{_q(name)}")

    def history(self, slug: str, bin: str, limit: int | None = None) -> list[dict]:
        """The bin's takes as an experiment log, newest first: take number, generation, status,
        params, the QC mark, and the typed prompt diffed against the take before it. Reading a
        bin's generations also gives any plain media in it whose file carries its render's
        graph the record it was missing, so a hand-uploaded output shows up as a take."""
        gens = self.generations(slug, bin=bin)
        detail = self.bin(slug, bin)
        media_by_id = {m["id"]: m for m in detail.get("media") or []}
        jobs_by_id = {j.get("job"): j for j in detail.get("active_jobs") or []}
        rows: list[dict] = []
        prev_prompt = ""
        for g in gens:  # landing order, so each diff is against the take before
            outputs = list(g.get("outputs") or [])
            media = media_by_id.get(outputs[0]) if outputs else None
            params = dict(g.get("params") or {})
            status = g.get("status")
            job = jobs_by_id.get(g.get("id"))
            if job and job.get("status"):
                status = job["status"]
            rows.append({
                "take": (media or {}).get("take"),
                "generation": g.get("id"),
                "ts": g.get("ts", ""),
                "status": status,
                "workflow": g.get("workflow", ""),
                "seed": params.get("seed"),
                "length_s": params.get("length_s"),
                "quarter": bool(params.get("quarter")),
                "draft": bool(params.get("draft")),
                "params": params,
                "media": outputs[0] if outputs else None,
                "media_state": "ok" if media else ("deleted" if outputs else "no outputs"),
                "qc": (media or {}).get("qc"),
                "prompt_typed": g.get("prompt_typed", ""),
                "negative_submitted": g.get("negative_submitted", ""),
                "prompt_diff": prompt_diff(prev_prompt, g.get("prompt_typed", "")) if rows else "",
                "warnings": list(g.get("warnings") or []),
                # the record was read back out of the file (a render THM never landed), not written by the render
                "recovered": bool(g.get("recovered_from")),
            })
            prev_prompt = g.get("prompt_typed", "")
        rows.reverse()
        return rows[:limit] if limit else rows

    def thumb(self, slug: str, media_id: str, size: str = "grid") -> bytes:
        return self.request("GET", f"/media/{_q(slug)}/{_q(media_id)}/thumb", params={"size": size}, raw=True)

    def frames(self, slug: str, media_id: str, n: int = 4, size: int = 1024) -> bytes:
        """A contact sheet of ``n`` evenly spaced frames of a video, times stamped."""
        return self.request("GET", f"/media/{_q(slug)}/{_q(media_id)}/frames", params={"n": n, "size": size}, raw=True)

    def media_file(self, slug: str, media_id: str) -> bytes:
        return self.request("GET", f"/media/{_q(slug)}/{_q(media_id)}/file", raw=True)

    def message_file(self, slug: str, file_id: str) -> bytes:
        """A file a note carries (``files`` on an inbox message): the user's, not project media."""
        return self.request("GET", f"/api/projects/{_q(slug)}/chat-files/{_q(file_id)}", raw=True)

    def keep_file(self, slug: str, file_id: str, *, bin: str | None = None, bin_name: str | None = None) -> dict:
        """Make a note's file project media: it lands in the bin like an upload."""
        return self.post(f"/api/projects/{_q(slug)}/chat-files/{_q(file_id)}/keep", {"bin": bin, "bin_name": bin_name})

    def gif(
        self,
        slug: str,
        media_id: str,
        *,
        bin: str | None = None,
        bin_name: str | None = None,
        start_s: float | None = None,
        end_s: float | None = None,
        fps: float | None = None,
        width: int | None = None,
    ) -> dict:
        """Export a video media as an animated GIF, made on the server and landed in a bin like
        an upload (origin ``gif``, ``source`` the video). The whole clip unless trimmed."""
        body = {"source": media_id, "mode": "gif", "bin": bin, "bin_name": bin_name, "start_s": start_s, "end_s": end_s, "fps": fps, "width": width}
        return self.post(f"/api/projects/{_q(slug)}/clips", {k: v for k, v in body.items() if v is not None})

    def upload(self, slug: str, path: str | Path, *, bin: str | None = None, bin_name: str | None = None, source: str | None = None, note: str | None = None, tags: list[str] | None = None) -> dict:
        """Put a local file into a bin, the way the browser does. ``source`` and ``note`` say
        where it came from when the agent made it from a take ("audio removed, ffmpeg -an");
        ``tags`` go on the bin when the upload mints it."""
        p = Path(path)
        data = p.read_bytes()
        fields = {"bin": bin, "bin_name": bin_name, "source": source, "note": note, "tags": ",".join(tags) if tags else None}
        boundary = f"----thm{uuid.uuid4().hex}"
        parts: list[bytes] = []
        for key, value in fields.items():
            if value:
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8"))
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{p.name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode("utf-8"))
        parts.append(data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        headers = self._headers(json_body=False, propose=False, cursor=None)
        headers["content-type"] = f"multipart/form-data; boundary={boundary}"
        status, payload, _ = self.transport("POST", self.base_url + f"/api/projects/{_q(slug)}/uploads", body, headers)
        if status >= 400:
            try:
                err = json.loads(payload.decode("utf-8")) if payload else {}
            except ValueError:
                err = {}
            if not isinstance(err, dict):
                err = {}
            raise ThmError(status, str(err.get("code") or "http_error"), str(err.get("detail") or payload[:200].decode("utf-8", "replace")), err.get("extra") or {})
        return json.loads(payload.decode("utf-8"))

    def sessions(self) -> list[dict]:
        """Every project with its session at a glance: status, mode, harness, blocker, pending."""
        out = []
        for p in self.projects():
            sess = p.get("agent_session") or {}
            out.append({
                "slug": p["slug"], "name": p.get("name", ""), "status": sess.get("status") or "none", "mode": sess.get("mode"),
                "harness": sess.get("harness"), "session": sess.get("id"), "blocker": (p.get("blocker") or {}).get("text"),
                "pending_changes": int(p.get("pending_changes") or 0),
                # live with nobody heard on it: the harness closed without end; connect to it
                "agent_gone": bool(p.get("agent_gone")),
            })
        return out

    def set_mode(self, slug: str, mode: str | None, *, drafts: bool | None = None, requested: bool = False) -> dict:
        payload: dict[str, Any] = {}
        if mode is not None:
            payload["mode"] = mode
        if drafts is not None:
            payload["drafts"] = drafts
        if requested:
            payload["requested"] = True
        return self.command(slug, "set_session_mode", payload)

    # -- session ----------------------------------------------------------------

    def wait_for_session(
        self, slug: str | None = None, *, poll_s: float = 5.0, timeout_s: float | None = None, sleep: Callable[[float], None] = time.sleep
    ) -> dict:
        """Block until a project has a waiting session; returns that project's detail. ``slug`` narrows the search."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            slugs = [slug] if slug else [p["slug"] for p in self.projects()]
            for s in slugs:
                detail = self.project(s)
                sess = detail.get("agent_session") or {}
                if sess.get("status") == "waiting":
                    return detail
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("no waiting session")
            sleep(poll_s)

    def peek_inbox(self, slug: str, cursor: int | None = None) -> tuple[list[dict], int]:
        """The inbox past ``cursor`` without moving the client's own cursor."""
        start = self.cursor if cursor is None else cursor
        out = self.get(f"/api/projects/{_q(slug)}/inbox", {"cursor": int(start or 0)})
        return list(out.get("messages") or []), int(out["cursor"])

    def open_notes(self, slug: str) -> list[dict]:
        """The user's notes this session has not accounted for, oldest first: the to-do list.
        Reading the inbox closes none of them; ``answer_notes`` does."""
        try:
            return list(self.get(f"/api/projects/{_q(slug)}/notes").get("notes") or [])
        except ThmError as exc:
            if exc.status == 404 and exc.code != "unknown_project":
                # a server still running the code from before notes were tracked: it cannot say
                # what is owed, which is not the same as nothing being owed
                self.notes_untracked = True
                return []
            raise

    def resume(self, slug: str) -> dict:
        """The reconnect summary: mode, open notes, live and interrupted renders, last post, rules."""
        out = self.get(f"/api/projects/{_q(slug)}/resume")
        self.mode = out.get("mode") or self.mode
        return out

    def answer_notes(self, slug: str, notes: list[str], did: str) -> dict:
        """Close notes with what was done about them, in a few words the user reads on the note.
        Sends the inbox cursor: a note that landed since refuses the answer with ``inbox_behind``
        (a note is never closed unread, and never closed after a post that was itself refused)."""
        return self.command(slug, "answer_notes", {"notes": list(notes), "did": did}, cursor=self.cursor)

    def withdraw(self, slug: str, change_id: str, why: str = "") -> dict:
        """Take back one of this session's own waiting cards; it closes with the reason on it."""
        return self.command(slug, "withdraw_change", {"change": change_id, "why": why})

    def cancel_job(self, slug: str, gen_id: str, *, requested: bool = False, reason: str = "") -> dict:
        """Stop a live render. On the agent's own judgement only in an auto session; in any
        other mode ``requested`` says the user asked for it (``requested_required`` otherwise)."""
        return self.post(f"/api/projects/{_q(slug)}/jobs/{_q(gen_id)}/cancel", {"requested": requested, "reason": reason})

    def restart_comfy(self, slug: str, *, requested: bool = False, reason: str = "") -> dict:
        """Stop and start ComfyUI through the server (the user's own restart script). Live
        renders are queued again and start when it answers. Same rule as ``cancel_job``: on its
        own only in an auto session on ``slug``, else only when the user asked."""
        return self.post("/api/comfy/restart", {"slug": slug, "requested": requested, "reason": reason})

    def enhance_requests(self, slug: str) -> list[dict]:
        """Prompt rewrites the app is waiting on the agent for. Each carries ``system_prompt``
        (the workflow's own instructions, or the app's default for its kind) and ``message``
        (the user's note and the prompt to rewrite)."""
        return list(self.get(f"/api/projects/{_q(slug)}/enhance/requests") or [])

    def enhance_ack(self, slug: str, req_id: str) -> dict:
        """Tell the app the agent picked this request up: its button reads "rewriting" from here."""
        return self.post(f"/api/projects/{_q(slug)}/enhance/requests/{_q(req_id)}/ack", {})

    def set_activity(self, slug: str, doing: str) -> dict:
        """Say what you are doing, in words, for the strip: a step that takes a while ("rewriting
        the prompt for Video 6") without a line in the feed."""
        return self.post(f"/api/projects/{_q(slug)}/agent/activity", {"doing": doing})

    def activity(self, slug: str) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/agent/activity")

    def enhance_reply(self, slug: str, req_id: str, *, prompt: str = "", error: str = "") -> dict:
        """Answer one enhance request: the rewritten prompt, or why there is none."""
        return self.post(f"/api/projects/{_q(slug)}/enhance/requests/{_q(req_id)}/reply", {"prompt": prompt, "error": error})

    def _peek(self, slug: str, cursor: int | None) -> tuple[list[dict], str | None]:
        """The inbox past ``cursor`` and the session mode now, without moving anything."""
        start = self.cursor if cursor is None else cursor
        out = self.get(f"/api/projects/{_q(slug)}/inbox", {"cursor": int(start or 0)})
        return list(out.get("messages") or []), out.get("mode")

    def _events(self, slug: str, cursor: int | None, kinds: tuple[str, ...], jobs: tuple[str, ...], mode: str | None) -> list[dict]:
        """Everything on one project that should wake the agent now: the user's messages and
        decisions, a session mode other than the one it last saw, a listed render that finished
        or failed, a pending enhance request."""
        messages, now_mode = self._peek(slug, cursor)
        # the server's own alerts (a stalled render, ComfyUI unreachable or restarting) are system
        # lines that wake the agent like a note; reading the inbox moves past them
        woke = [m for m in messages if m.get("kind") in kinds or (m.get("kind") == "system" and str(m.get("text") or "").startswith(ALERT_PREFIX))]
        if mode and now_mode and now_mode != mode:
            woke.append({"kind": "mode", "mode": now_mode, "text": f"MODE CHANGED: the session mode is now {now_mode} (you last saw {mode}): re-plan before your next step"})
        if jobs:
            for j in self.jobs(slug):
                if j.get("job") in jobs and j.get("status") not in ("queued", "running"):
                    detail = f" takes={j.get('takes')}" if j.get("takes") else (f" error={j.get('error')}" if j.get("error") else "")
                    woke.append({"kind": "job", "id": j["job"], "status": j.get("status"), "takes": j.get("takes") or [], "text": f"render {j['job']} in {j.get('bin_name') or j.get('bin')} {j.get('status')}{detail}"})
        for r in self.enhance_requests(slug):
            woke.append({"kind": "enhance", "id": r["id"], "ts": r.get("created", ""), "text": f"enhance request {r['id']} ({r.get('kind')}, {r.get('workflow') or 'default'}) -- run enhance-requests"})
        return woke

    def _retrying(self, attempt: Callable[[], Any], deadline: float | None, sleep: Callable[[float], None], poll_s: float) -> Any:
        """One poll, retried with backoff while the server is unreachable (a restart, a reboot):
        a wait never dies on a restart, it waits it out until its own deadline."""
        delay = poll_s
        while True:
            try:
                return attempt()
            except OSError:
                pass
            except ThmError as exc:
                if exc.status < 500:
                    raise
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("the server was unreachable until the timeout")
            sleep(delay)
            delay = min(30.0, delay * 2)

    def wait_for_inbox(
        self,
        slug: str,
        *,
        cursor: int | None = None,
        kinds: tuple[str, ...] = ("user", "decision", "blocker"),
        jobs: tuple[str, ...] = (),
        mode: str | None = None,
        poll_s: float = 3.0,
        timeout_s: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> list[dict]:
        """Block until something on this project needs the agent; returns what woke it.

        The one wait there is: a user message or decision past ``cursor``, the session mode
        moving off ``mode`` (the mode the agent last saw), any of ``jobs`` (generation ids)
        finishing or failing, or a pending enhance request. So waiting on a render never means
        missing a note: the first of them returns. Notes read earlier and never answered return
        too -- waiting on the user while they wait on you is the stall. The server going away
        (a restart) is waited out, not reported.

        The cursor is deliberately not advanced: ``inbox`` still owes the read, so "read before
        every generate" is unchanged.
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s

        def once() -> list[dict]:
            woke = self._events(slug, cursor, kinds, tuple(jobs), mode)
            if not woke:
                owed = self.open_notes(slug)
                if owed:
                    return [{**m, "open": True} for m in owed]
            return woke

        while True:
            woke = self._retrying(once, deadline, sleep, poll_s)
            if woke:
                return woke
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("nothing from the user")
            sleep(poll_s)

    def wait_for_any(
        self,
        cursors: dict[str, int | None],
        *,
        kinds: tuple[str, ...] = ("user", "decision", "blocker"),
        jobs: tuple[str, ...] = (),
        modes: dict[str, str | None] | None = None,
        sessions: dict[str, str | None] | None = None,
        poll_s: float = 3.0,
        timeout_s: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict:
        """Block until any project needs the agent: a session waiting for a connect, a live
        session whose agent is gone, a user message or decision past that project's cursor, a
        mode other than the one last seen there (``modes``), a listed render finishing, or an
        open enhance request. Returns ``{slug, why, messages}``. One list request per poll; the
        cards carry the session, so only projects with a live session get a closer look. The
        server going away (a restart) is waited out.

        ``cursors`` maps slug to the agent's saved inbox cursor there (None = never read).
        ``sessions`` maps slug to the session id this agent holds there: each poll on that
        project is made as that session, so the server keeps hearing its owner and the session
        never reads as unattended while the agent is only waiting.
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        modes = modes or {}
        sessions = sessions or {}

        def once() -> dict | None:
            for p in self.projects():
                slug = p["slug"]
                sess = p.get("agent_session") or {}
                status = sess.get("status")
                if status == "waiting":
                    return {"slug": slug, "why": "session waiting", "messages": []}
                if status != "live":
                    continue
                if p.get("agent_gone") and sessions.get(slug) != sess.get("id"):
                    # live, but the server has heard no agent on it for a while: the harness that
                    # had it closed without ``end``. Connecting again takes it over.
                    return {"slug": slug, "why": "agent gone", "session": sess.get("id"), "messages": []}
                held = self.session
                self.session = sessions.get(slug) or None  # heard as the session we hold there, if any
                try:
                    woke = self._events(slug, cursors.get(slug) or 0, kinds, tuple(jobs), modes.get(slug))
                finally:
                    self.session = held
                if woke:
                    return {"slug": slug, "why": f"{len(woke)} new item(s)", "messages": woke}
                if int(p.get("open_notes") or 0) > 0:
                    owed = self.open_notes(slug)
                    if owed:
                        return {"slug": slug, "why": f"{len(owed)} open note(s)", "messages": [{**m, "open": True} for m in owed]}
            return None

        while True:
            woke = self._retrying(once, deadline, sleep, poll_s)
            if woke:
                return woke
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("nothing from the user on any project")
            sleep(poll_s)

    def connect(self, slug: str, harness: str) -> dict:
        result = self.command(slug, "connect_session", {"harness": harness})
        sess = self.project(slug).get("agent_session") or {}
        self.session = sess.get("id") or self.session
        return result

    def end(self, slug: str) -> dict:
        return self.command(slug, "end_session", {})

    def inbox(self, slug: str, cursor: int | None = None) -> tuple[list[dict], int]:
        """Messages past the cursor (the client's own when ``cursor`` is None) and the new cursor, which the client keeps."""
        start = self.cursor if cursor is None else cursor
        out = self.get(f"/api/projects/{_q(slug)}/inbox", {"cursor": int(start or 0)})
        self.cursor = int(out["cursor"])
        self.mode = out.get("mode") or self.mode
        return list(out.get("messages") or []), self.cursor

    def post_message(self, slug: str, text: str, kind: str = "agent", attachments: list[str] | None = None) -> dict:
        """Sends the inbox cursor: a note that landed while the reply was written refuses the
        post with ``inbox_behind`` and hands it over, so it is answered rather than talked over."""
        return self.command(slug, "post_message", {"text": text, "kind": kind, "attachments": list(attachments or [])}, cursor=self.cursor)

    def recall(self, slug: str, media_id: str, *, bin: str | None = None, reason: str | None = None) -> dict:
        """Put a bin back to the recipe that made a take: workflow, prompt, negative, references
        with their handles, loras. The recipe is read from the media's record (or the file), so
        nothing has to be asked of anyone. Returns the command result with ``recipe`` attached;
        seed and length are the caller's to reuse (``generate --params``, ``set_bin_field``)."""
        rec = self.recipe(slug, media_id)
        if not rec.get("found"):
            raise ThmError(409, "not_a_take", f"{media_id} has no recipe: it was not generated", {})
        target = bin or rec.get("bin")
        if not target:
            raise ThmError(409, "no_bin", f"{media_id} was made in no bin still here; pass one", {})
        refs = [{"media": m, "handle": h} for m, h in zip(rec.get("references") or [], rec.get("handles") or [])]
        payload: dict[str, Any] = {
            "bin": target, "from_media": media_id, "workflow": rec.get("workflow"), "prompt": rec.get("prompt", ""),
            "negative": rec.get("negative", ""), "references": refs, "loras": list(rec.get("loras") or []),
        }
        if reason:
            payload["reason"] = reason
        result = self.command(slug, "recall_generation", payload)
        return {**result, "recipe": rec}

    # -- writes -----------------------------------------------------------------

    def command(self, slug: str, name: str, payload: dict | None = None, *, propose: bool = False, cursor: int | None = None) -> dict:
        out = self.post(f"/api/projects/{_q(slug)}/commands/{_q(name)}", dict(payload or {}), propose=propose, cursor=cursor)
        if isinstance(out, dict) and out.get("mode"):
            self.mode = out["mode"]
        return out

    def generate(self, slug: str, bin: str, *, propose: bool = False, params: dict | None = None, requested: bool = False, card: bool = False, reason: str | None = None) -> dict:
        """Queue a render of the bin's recipe. Sends the inbox cursor; the server refuses with ``inbox_behind`` when unread items exist."""
        payload: dict[str, Any] = {"bin": bin}
        if params:
            payload["params"] = dict(params)
        if reason:
            payload["reason"] = reason
        if requested:
            payload["requested"] = True
        if card:
            payload["card"] = True
        return self.command(slug, "generate", payload, propose=propose, cursor=self.cursor)

    def propose_shots(self, slug: str, items: list[dict], *, title: str | None = None, reason: str | None = None, requested: bool = False) -> dict:
        """The pre-generation card: a batch of intended renders the user keeps, edits or drops
        before any of them spends. Proposed unless ``requested`` (the user asked for the batch in
        the feed), which lands it at once: bins, recipes and queued renders."""
        payload: dict[str, Any] = {"items": [dict(i) for i in items]}
        if title:
            payload["title"] = title
        if reason:
            payload["reason"] = reason
        if requested:
            payload["requested"] = True
        return self.command(slug, "propose_shots", payload, propose=not requested, cursor=self.cursor)

    def wait_change(
        self, slug: str, change_id: str, *, poll_s: float = 3.0, timeout_s: float | None = None, sleep: Callable[[float], None] = time.sleep
    ) -> dict:
        """Poll a proposed change until the user decides it."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            change = self.change(slug, change_id)
            if ((change.get("decision") or {}).get("status") or "proposed") != "proposed":
                return change
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"change {change_id} still proposed")
            sleep(poll_s)

    def wait_generation(
        self, slug: str, gen_id: str, *, poll_s: float = 2.0, timeout_s: float | None = None, sleep: Callable[[float], None] = time.sleep
    ) -> dict:
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            gen = self.generation(slug, gen_id)
            if gen.get("status") in TERMINAL_GENERATION:
                return gen
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"generation {gen_id} still {gen.get('status')}")
            sleep(poll_s)


def _q(part: str) -> str:
    return urllib.parse.quote(str(part), safe="")
