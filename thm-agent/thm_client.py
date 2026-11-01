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
HEADER_DOING = "x-thm-agent-doing"  # what the agent is doing, shown in the app's strip while it works
LIVE_SESSION = ("waiting", "live")
TERMINAL_GENERATION = ("done", "failed", "cancelled")

# The same token the server scans for (thm_core/slug.py TOKEN_RE), copied rather than imported:
# this client runs on machines with no thm_core. tests/test_thm_client.py asserts they match.
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9\-.])\.([a-z0-9][a-z0-9-]*)(?:\.(\d+))?(?![A-Za-z0-9\-])")

# What the runners read out of ``generate --params``, by kind (scripts/run_images.py and
# scripts/run_video.py). Anything else is accepted by the server, stored on the generation,
# and silently ignored by the runner -- so the client warns.
PARAM_KEYS = {
    "image": ("seed", "steps", "cfg", "sampler", "scheduler", "width", "height"),
    "video": ("seed", "steps", "fps", "length_s", "quarter", "width", "height"),
}


def handles_in(text: str) -> set[str]:
    out: set[str] = set()
    for m in TOKEN_RE.finditer(text or ""):
        out.add(m.group(1) + (f".{m.group(2)}" if m.group(2) else ""))
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
    ):
        self.base_url = base_url.rstrip("/")
        # this client is the agent and only the agent: the private-project gate keys on the actor
        # header, so there is no way to send another one from here
        self.actor = "agent"
        self.session = session
        self.cursor = cursor
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

    def jobs(self, slug: str) -> list[dict]:
        return self.get(f"/api/projects/{_q(slug)}/jobs")

    def generation(self, slug: str, gen_id: str) -> dict:
        return self.get(f"/api/projects/{_q(slug)}/generations/{_q(gen_id)}")

    def change(self, slug: str, change_id: str) -> dict:
        """One change's latest record: a propose_shots card's items carry the outcome in ``payload``."""
        return self.get(f"/api/projects/{_q(slug)}/changes/{_q(change_id)}")

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

    def upload(self, slug: str, path: str | Path, *, bin: str | None = None, bin_name: str | None = None, source: str | None = None, note: str | None = None) -> dict:
        """Put a local file into a bin, the way the browser does. ``source`` and ``note`` say
        where it came from when the agent made it from a take ("audio removed, ffmpeg -an")."""
        p = Path(path)
        data = p.read_bytes()
        fields = {"bin": bin, "bin_name": bin_name, "source": source, "note": note}
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
            })
        return out

    def set_mode(self, slug: str, mode: str, *, requested: bool = False) -> dict:
        payload: dict[str, Any] = {"mode": mode}
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

    def wait_for_inbox(
        self,
        slug: str,
        *,
        cursor: int | None = None,
        kinds: tuple[str, ...] = ("user", "decision", "blocker"),
        poll_s: float = 3.0,
        timeout_s: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> list[dict]:
        """Block until the user says something past ``cursor``; returns the waking messages.

        The agent has no other way to know the user wrote: the server's SSE stream is for the
        browser, and a harness that is not looking is simply deaf. Blocking here and returning
        is what wakes it, exactly as ``wait_for_session`` does for a kickoff.

        Only the user's own kinds wake it -- messages, decisions, blockers -- so a long render
        does not tick it awake every few seconds. The cursor is deliberately not advanced:
        ``inbox`` still owes the read, so "read before every generate" is unchanged.

        A pending enhance request wakes it too, as a synthetic ``enhance`` message: the app is
        holding a request open for the answer, so it cannot wait for the next real message.
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            messages, _ = self.peek_inbox(slug, cursor)
            woke = [m for m in messages if m.get("kind") in kinds]
            for r in self.enhance_requests(slug):
                woke.append({"kind": "enhance", "id": r["id"], "ts": r.get("created", ""), "text": f"enhance request {r['id']} ({r.get('kind')}, {r.get('workflow') or 'default'}) -- run enhance-requests"})
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
        poll_s: float = 3.0,
        timeout_s: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict:
        """Block until any project needs the agent: a session waiting for a connect, a user
        message or decision past that project's cursor on a live session, or an open enhance
        request. Returns ``{slug, why, messages}``. One list request per poll; the cards carry
        the session, so only projects with a live session get an inbox peek.

        ``cursors`` maps slug to the agent's saved inbox cursor there (None = never read).
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            for p in self.projects():
                slug = p["slug"]
                sess = p.get("agent_session") or {}
                status = sess.get("status")
                if status == "waiting":
                    return {"slug": slug, "why": "session waiting", "messages": []}
                if status != "live":
                    continue
                messages, _ = self.peek_inbox(slug, cursors.get(slug) or 0)
                woke = [m for m in messages if m.get("kind") in kinds]
                for r in self.enhance_requests(slug):
                    woke.append({"kind": "enhance", "id": r["id"], "ts": r.get("created", ""), "text": f"enhance request {r['id']} ({r.get('kind')}, {r.get('workflow') or 'default'}) -- run enhance-requests"})
                if woke:
                    return {"slug": slug, "why": f"{len(woke)} new item(s)", "messages": woke}
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
        return list(out.get("messages") or []), self.cursor

    def post_message(self, slug: str, text: str, kind: str = "agent", attachments: list[str] | None = None) -> dict:
        return self.command(slug, "post_message", {"text": text, "kind": kind, "attachments": list(attachments or [])})

    # -- writes -----------------------------------------------------------------

    def command(self, slug: str, name: str, payload: dict | None = None, *, propose: bool = False, cursor: int | None = None) -> dict:
        return self.post(f"/api/projects/{_q(slug)}/commands/{_q(name)}", dict(payload or {}), propose=propose, cursor=cursor)

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
