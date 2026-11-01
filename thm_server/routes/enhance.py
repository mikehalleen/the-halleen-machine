"""Prompt enhancement: an LLM rewrites a prompt under the workflow's own instructions.

Two engines. ``local`` runs ``workflows/prompt_enhancer.json`` on the project's ComfyUI,
queued at the front so a rewrite does not wait behind a render. ``agent`` hands the same
two texts to the live agent session: the request sits in a server-side queue, the agent's
watch loop sees it, a subagent writes the prompt, and the agent posts it back while the
app's request is still waiting.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from thm_core import CommandContext, Project, execute
from thm_core.commands import PostMessage
from thm_core.enhancer import (
    build_enhancer_graph,
    build_user_message,
    clean_output,
    enhancer_available,
    enhancer_path,
    extract_text,
    history_error,
    load_workflow,
    output_problem,
    system_prompt_for,
)
from thm_core.ids import utc_now
from thm_core.workflow_names import resolve_curated_workflow

from ..deps import ServerState, actor_context, state
from ..errors import ApiError
from ..publish import publish_result
from ..schemas import EnhanceBody, EnhanceOut, EnhanceReplyBody, EnhanceRequestOut, EnhanceStatusOut
from scripts import comfy_progress  # the runners' prompt tracker: phase, step/steps, queue position

log = logging.getLogger("thm.enhance")
router = APIRouter(prefix="/api/projects/{slug}/enhance", tags=["enhance"])

PING_CACHE_S = 30.0
ENHANCE_TIMEOUT_S = 180.0
AGENT_TIMEOUT_S = 300.0  # a subagent turn plus the agent's own 3 s watch poll
POLL_S = 0.5
_ping_cache: dict[str, tuple[float, bool]] = {}
_seen: set[tuple[str, str, str]] = set()  # inputs already enhanced once this server run


def comfy_api_base(project: Project, st: ServerState) -> str:
    return ((project.comfy.api_base or "").strip() or str(st.settings.comfy.api_base or "").strip()).rstrip("/")


def comfy_reachable(api_base: str) -> bool:
    """A cheap ``/system_stats`` ping, remembered for a short while so the status poll stays free."""
    now = time.monotonic()
    hit = _ping_cache.get(api_base)
    if hit and now - hit[0] < PING_CACHE_S:
        return hit[1]
    ok = False
    try:
        with httpx.Client(timeout=1.5) as client:
            ok = client.get(f"{api_base}/system_stats").status_code == 200
    except Exception:
        ok = False
    _ping_cache[api_base] = (now, ok)
    return ok


def agent_live(project: Project) -> bool:
    """A live session unlocks the agent engine; a private project never has one to offer, so
    its prompts stay on this machine."""
    if project.private:
        return False
    return project.agent_session is not None and project.agent_session.status == "live"


def enhance_status(project: Project, st: ServerState) -> EnhanceStatusOut:
    wdir = Path(st.settings.workflows_dir)
    if not enhancer_available(wdir):
        return EnhanceStatusOut(local=False, agent=agent_live(project), detail=f"no {enhancer_path(wdir).name} in {wdir}")
    api_base = comfy_api_base(project, st)
    if not api_base or not comfy_reachable(api_base):
        return EnhanceStatusOut(local=False, agent=agent_live(project), detail=f"ComfyUI not reachable at {api_base or '(unset)'}")
    return EnhanceStatusOut(local=True, agent=agent_live(project))


def _rejection_detail(r: httpx.Response) -> str:
    detail = r.text[:400]
    try:
        j = r.json()
        detail = str(j.get("error", {}).get("message") or j.get("error") or detail)
        node_errors = j.get("node_errors") or {}
        if node_errors:
            first = next(iter(node_errors.values()))
            errs = first.get("errors") or []
            if errs:
                detail = f"{detail}: {errs[0].get('message')} ({errs[0].get('details')})"
    except Exception:
        pass
    return detail


Progress = Callable[[dict[str, Any]], None]


def run_graph_once(client: httpx.Client, api_base: str, graph: dict[str, Any], slug: str, deadline: float, on_progress: Progress | None = None) -> str:
    """Queue the graph at the front, wait for its history entry, return the raw generated text.
    ``on_progress`` gets the tracker's snapshots (phase, step/steps, queue position) as they change."""
    with comfy_progress.PromptTracker(api_base, graph, on_progress) as tracker:
        payload = {
            "prompt": graph,
            "client_id": tracker.client_id,
            "front": True,
            "extra_data": {"machine_ui_project": slug, "machine_ui_label": "enhance"},
        }
        r = client.post(f"{api_base}/prompt", json=payload)
        if r.status_code != 200:
            raise ApiError(502, "comfy_rejected", f"ComfyUI rejected the enhancer: {_rejection_detail(r)}")
        prompt_id = str(r.json().get("prompt_id") or "")
        if not prompt_id:
            raise ApiError(502, "comfy_rejected", "ComfyUI returned no prompt_id")
        entry = tracker.wait(prompt_id, max(0.0, deadline - time.monotonic()), history_poll_s=POLL_S)
    if entry is None:
        raise ApiError(504, "enhance_timeout", f"no answer from the enhancer within {int(ENHANCE_TIMEOUT_S)}s")
    err = history_error(entry)
    if err:
        raise ApiError(502, "enhance_failed", f"enhancer failed in ComfyUI: {err}")
    return extract_text(entry, graph) or ""


def run_local(project: Project, st: ServerState, body: EnhanceBody, on_progress: Progress | None = None) -> EnhanceOut:
    wdir = Path(st.settings.workflows_dir)
    enhancer = load_workflow(enhancer_path(wdir))
    if not enhancer:
        raise ApiError(503, "enhancer_unavailable", f"no {enhancer_path(wdir).name} in {wdir}")
    api_base = comfy_api_base(project, st)
    if not api_base:
        raise ApiError(503, "enhancer_unavailable", "no ComfyUI api_base configured")
    wf_path = resolve_curated_workflow(body.workflow, wdir) if body.workflow else None
    system_prompt, source = system_prompt_for(wf_path, body.kind)
    message = build_user_message(body.prompt, body.note, [(r.handle, r.type) for r in body.references])

    # The workflow's saved seed is the tested one and the reliable one, so it goes first -- but
    # only the first time this exact input is seen. Sending the same prompt and note again is a
    # request for another take, and the saved seed would just replay ComfyUI's cached reply.
    fingerprint = (project.slug, system_prompt, message)
    first_seed = None if fingerprint not in _seen else random.randint(1, 2**31 - 1)
    _seen.add(fingerprint)

    started = time.monotonic()
    deadline = started + ENHANCE_TIMEOUT_S
    problem = "no attempt made"
    try:
        with httpx.Client(timeout=30.0) as client:
            # one re-roll if the first reply is unusable
            for seed in (first_seed, random.randint(1, 2**31 - 1)):
                try:
                    graph = build_enhancer_graph(enhancer, system_prompt, message, seed=seed)
                except ValueError as exc:
                    raise ApiError(503, "enhancer_unavailable", str(exc)) from None
                text = clean_output(run_graph_once(client, api_base, graph, project.slug, deadline, on_progress))
                problem = output_problem(text, system_prompt, body.prompt)
                if problem is None:
                    return EnhanceOut(prompt=text, engine="local", system_source=source, elapsed_s=round(time.monotonic() - started, 1))
                log.info("enhance reply unusable (%s): %r", problem, text[:120])
    except httpx.HTTPError as exc:
        raise ApiError(502, "comfy_unreachable", f"ComfyUI at {api_base}: {type(exc).__name__}") from None
    raise ApiError(502, "enhance_failed", f"the enhancer's reply was unusable ({problem}) -- try again or adjust the note")


# -- the agent engine -------------------------------------------------------------


@dataclass
class AgentRequest:
    id: str
    slug: str
    session: str
    kind: str
    workflow: str
    system_prompt: str
    message: str
    prompt: str
    created: str
    done: asyncio.Event = field(default_factory=asyncio.Event)
    loop: asyncio.AbstractEventLoop | None = None  # the waiter's loop: the answer may land on another
    result: str | None = None
    error: str | None = None
    # queued: nobody has looked; seen: the agent's watch listed it; working: the agent picked it up
    state: str = "queued"

    def answer(self, prompt: str | None, error: str | None) -> None:
        self.result, self.error = prompt, error
        if self.loop is not None and self.loop is not asyncio.get_running_loop():
            self.loop.call_soon_threadsafe(self.done.set)
        else:
            self.done.set()

    def out(self) -> EnhanceRequestOut:
        return EnhanceRequestOut(id=self.id, kind=self.kind, workflow=self.workflow, system_prompt=self.system_prompt, message=self.message, created=self.created)


async def post_to_feed(st: ServerState, slug: str, text: str, ctx: CommandContext) -> None:
    """Record one side of the exchange in the session feed, exactly as a typed message would land."""
    kind = "user" if ctx.actor == "user" else "agent"
    async with st.registry.lock(slug):
        project = st.registry.get(slug)
        result = await run_in_threadpool(execute, st.store, project, PostMessage(text=text, kind=kind), ctx, env=st.env)
        version = st.registry.touch(slug)
    publish_result(st, slug, project, messages=result.messages, change=result.change, generation=None, version=version)


def request_text(project: Project, body: EnhanceBody) -> str:
    """The user's side of the exchange, phrased as they would have asked in the chat."""
    bin_ = project.bins_by_id.get(body.bin) if body.bin else None
    where = f"the prompt on {bin_.name}" if bin_ is not None else f"a {body.kind} prompt"
    wf = body.workflow.replace(".json", "") if body.workflow else "default workflow"
    lines = [f"Rewrite {where} ({wf})."]
    if body.note.strip():
        lines.append(f"Note: {body.note.strip()}")
    lines.append(f"Prompt: {body.prompt.strip() or '(empty)'}")
    return "\n\n".join(lines)


def pending_for(st: ServerState, slug: str) -> list[AgentRequest]:
    return [r for r in st.enhance_requests.values() if r.slug == slug and not r.done.is_set()]


def publish_agent(st: ServerState, req: AgentRequest, status: str) -> None:
    """The agent engine's progress as ``enhance`` SSE events, the same channel the local engine
    uses: queued (waiting for the agent's watch), seen (its watch listed the request), working
    (it picked the request up), then done or failed. The button counts along."""
    task = {
        "queued": "waiting for the agent to notice",
        "seen": "the agent has seen it",
        "working": "the agent is rewriting",
        "done": "rewritten",
        "failed": "no rewrite",
    }.get(status, status)
    st.bus.publish_event(req.slug, "enhance", {"status": status, "engine": "agent", "request": req.id, "task": task, "created": req.created})


async def run_agent(project: Project, st: ServerState, body: EnhanceBody) -> EnhanceOut:
    """Queue the rewrite for the live session and wait for its answer."""
    wdir = Path(st.settings.workflows_dir)
    wf_path = resolve_curated_workflow(body.workflow, wdir) if body.workflow else None
    system_prompt, source = system_prompt_for(wf_path, body.kind)
    req = AgentRequest(
        id=f"enh_{uuid.uuid4().hex[:8]}",
        slug=project.slug,
        session=project.agent_session.id,  # type: ignore[union-attr]  # checked live by the caller
        kind=body.kind,
        workflow=body.workflow,
        system_prompt=system_prompt,
        message=build_user_message(body.prompt, body.note),
        prompt=body.prompt,
        created=utc_now(),
        loop=asyncio.get_running_loop(),
    )
    st.enhance_requests[req.id] = req
    # the ask lands in the feed first, so the agent's watch sees a user message as well as the request
    await post_to_feed(st, project.slug, request_text(project, body), CommandContext(actor="user"))
    publish_agent(st, req, "queued")
    started = time.monotonic()
    try:
        await asyncio.wait_for(req.done.wait(), AGENT_TIMEOUT_S)
    except asyncio.TimeoutError:
        publish_agent(st, req, "failed")
        raise ApiError(504, "enhance_timeout", f"the agent did not answer within {int(AGENT_TIMEOUT_S)}s -- is its session still running?") from None
    finally:
        st.enhance_requests.pop(req.id, None)
    if req.error or not req.result:
        publish_agent(st, req, "failed")
        raise ApiError(502, "enhance_failed", f"the agent could not rewrite the prompt: {req.error or 'empty reply'}")
    text = clean_output(req.result)
    problem = output_problem(text, system_prompt, body.prompt)
    if problem:
        publish_agent(st, req, "failed")
        raise ApiError(502, "enhance_failed", f"the agent's reply was unusable ({problem})")
    publish_agent(st, req, "done")
    return EnhanceOut(prompt=text, engine="agent", system_source=source, elapsed_s=round(time.monotonic() - started, 1))


@router.get("/requests", response_model=list[EnhanceRequestOut])
async def list_requests(slug: str, request: Request):
    """What the app is waiting on the agent for. Polled by the agent's watch loop; the first
    agent listing moves a request from queued to seen, so the user knows the watch is alive."""
    st = state(request)
    st.registry.get(slug)
    pending = pending_for(st, slug)
    if actor_context(request).actor == "agent":
        for r in pending:
            if r.state == "queued":
                r.state = "seen"
                publish_agent(st, r, "seen")
    return [r.out() for r in pending]


@router.post("/requests/{req_id}/ack", response_model=EnhanceRequestOut)
async def ack_request(slug: str, req_id: str, request: Request):
    """The agent picked the request up and is writing: the button reads "rewriting" from here."""
    st = state(request)
    st.registry.get(slug)
    if actor_context(request).actor != "agent":
        raise ApiError(403, "wrong_actor", "only the agent acknowledges enhance requests")
    req = st.enhance_requests.get(req_id)
    if req is None or req.slug != slug or req.done.is_set():
        raise ApiError(404, "unknown_request", f"no pending enhance request {req_id} (it may have timed out)")
    if req.state != "working":
        req.state = "working"
        publish_agent(st, req, "working")
    return req.out()


@router.post("/requests/{req_id}/reply", response_model=EnhanceRequestOut)
async def reply_request(slug: str, req_id: str, body: EnhanceReplyBody, request: Request):
    """The agent's answer. Only the agent, only for its own session, only once."""
    st = state(request)
    project = st.registry.get(slug)
    ctx = actor_context(request)
    if ctx.actor != "agent":
        raise ApiError(403, "wrong_actor", "only the agent answers enhance requests")
    req = st.enhance_requests.get(req_id)
    if req is None or req.slug != slug:
        raise ApiError(404, "unknown_request", f"no pending enhance request {req_id} (it may have timed out)")
    if not agent_live(project) or (ctx.session and ctx.session != req.session):
        raise ApiError(409, "wrong_session", "this request belongs to a different session")
    if req.done.is_set():
        raise ApiError(409, "already_answered", f"{req_id} was already answered")
    if not body.prompt.strip() and not body.error.strip():
        raise ApiError(400, "empty_reply", "send a prompt or an error")
    prompt, error = body.prompt.strip() or None, body.error.strip() or None
    # the answer goes into the feed under the agent's name before the app is released
    await post_to_feed(st, slug, prompt if prompt else f"I could not rewrite that prompt: {error}", ctx)
    req.answer(prompt, error)
    return req.out()


@router.get("/status", response_model=EnhanceStatusOut)
async def get_status(slug: str, request: Request):
    st = state(request)
    project = st.registry.get(slug)
    return await run_in_threadpool(enhance_status, project, st)


@router.post("", response_model=EnhanceOut)
async def enhance(slug: str, body: EnhanceBody, request: Request):
    st = state(request)
    project = st.registry.get(slug)
    if not body.prompt.strip() and not body.note.strip():
        raise ApiError(400, "empty_prompt", "nothing to enhance: the prompt and the note are both empty")
    if body.engine == "agent":
        if not agent_live(project):
            raise ApiError(409, "no_agent_session", "the agent engine needs a live agent session")
        return await run_agent(project, st, body)
    # the local engine runs off-loop; its progress reaches the app as ``enhance`` SSE events,
    # the same phase/step fields a render job carries, so the button can count along
    loop = asyncio.get_running_loop()
    keys = ("phase", "step", "steps", "pass_no", "passes", "queue_ahead", "queue_ahead_ours", "task")

    def publish(status: str, fields: dict[str, Any] | None = None) -> None:
        data = {"status": status, "engine": "local", **{k: (fields or {}).get(k) for k in keys}}
        loop.call_soon_threadsafe(st.bus.publish_event, slug, "enhance", data)

    try:
        out = await run_in_threadpool(run_local, project, st, body, lambda f: publish("running", f))
    except BaseException:
        publish("failed")
        raise
    publish("done")
    return out
