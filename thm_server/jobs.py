"""Render jobs: launch the generation runners, watch their status files, land takes.

One job per generation, keyed by the generation id. ``prepare`` (inside the
project lock, right after the ``generate`` command landed) writes the spec and
appends the ``running`` record; ``launch`` (after the lock) starts
``scripts/run_images.py`` or ``run_video.py`` with ``--generation`` and a watcher
task that polls the status file. ``completed`` lands the outputs as takes under
the same lock every command uses, then publishes a ``project`` event and a
``job`` event; ``failed`` records the error. The child is not attached to the
server's console, so it survives a server restart; ``reconcile`` re-attaches
watchers to generations the log still shows as queued or running.

A failure that leaves the prompt with ComfyUI (the runner timed out, the server lost
its status file, the runner crashed) is not the end of it: a late collector keeps
asking ComfyUI for the prompt by id or label and lands the result under the original
generation when it turns up; ``reconcile`` starts one for every recent such failure.

Nothing here probes pids: ``os.kill(pid, 0)`` terminates processes on Windows.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool

from thm_core import (
    CoreSettings,
    Generation,
    JobPaths,
    LiveProgress,
    Project,
    ProjectNotFound,
    ProjectStore,
    RunnerStatus,
    build_generation_spec,
    job_paths,
    land_outputs,
    log_tail,
    read_status,
    resolve_comfy,
    runner_script,
    thumb_cache_dir,
)
from thm_core.commands import close_render_card
from thm_core.config import REPO_ROOT
from thm_core.ids import utc_now
from thm_core.models import Message
from thm_core import ids as core_ids
from scripts import comfy_history  # the runners' /history reader: outputs and error detail of an entry

from .comfy import LateState, cancel_prompts, kill_runner, late_prompt_state, restart_comfy as spawn_comfy_restart
from .derive import feed_entry
from .events import EventBus
from .registry import ProjectRegistry
from .schemas import JobOut, JobStatus

Launcher = Callable[[list[str], Path, Path], Any]
Killer = Callable[[Any, int | None], None]
ComfyCancel = Callable[[str, str, str], str]
LateLookup = Callable[[str, str, str, str | None], LateState]
LIVE: tuple[JobStatus, ...] = ("queued", "running")
# Failure details after which ComfyUI may still finish the prompt on its own (the runner's
# execution timeout, a lost status file after a restart, a runner that died mid-wait).
LATE_COLLECTABLE = ("timed out", "gave up", "restarted", "went stale", "exited with code")
# the failures that mean ComfyUI or the machine went away under the render, not that it was bad:
# the resume summary lists these so a reconnecting agent can re-run them
INTERRUPTED_MARKS = ("restarted", "rebooted", "went stale", "exited with code", "gave up", "could not start runner")
# the failures that mean ComfyUI (or the machine, or this server) went away under a render that
# was fine: once ComfyUI answers again and has no trace of the prompt, the render is queued
# again by itself, once, so nothing has to be re-queued by hand after a restart
REQUEUE_MARKS = ("dropped by ComfyUI", "server restarted", "rebooted", "ComfyUI restarted")
LATE_HORIZON_S = 24 * 3600.0  # how long a late collector keeps asking after the failure
LATE_LOOKBACK_S = 24 * 3600.0  # how far back ``reconcile`` looks for failures worth a late pass
# a running render whose progress has not moved for this long is stalled: the app gets an alert
# line in the project feed and the job carries ``stalled`` so the bar and the agent's watch see it
STALL_AFTER_S = 10 * 60.0
ALERT_PREFIX = "alert: "  # a server-posted system line the agent's watch wakes on
_PID_LINE = re.compile(r"^\[PID\]\s*(\S+)\s*$", re.MULTILINE)


def boot_time() -> datetime | None:
    """When this machine last booted, as naive local time (the status file's clock). None if unknown."""
    try:
        if sys.platform == "win32":
            import ctypes

            uptime_ms = ctypes.windll.kernel32.GetTickCount64()  # type: ignore[attr-defined]
            return datetime.now() - timedelta(milliseconds=int(uptime_ms))
        with open("/proc/uptime", "r", encoding="ascii") as f:
            return datetime.now() - timedelta(seconds=float(f.read().split()[0]))
    except Exception:
        return None


def comfy_reachable(api_base: str, timeout: float = 3.0) -> bool:
    """Whether ComfyUI answers at ``api_base`` right now (its /system_stats)."""
    import urllib.request

    try:
        with urllib.request.urlopen(api_base.rstrip("/") + "/system_stats", timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


# how often a render waiting for ComfyUI to come back asks again
COMFY_WAIT_POLL_S = 10.0
# after a restart this server started: how long a render waits to see ComfyUI go down before
# it takes an answering ComfyUI as the new instance anyway
RESTART_DOWN_GRACE_S = 90.0


def default_launch(cmd: list[str], cwd: Path, log_path: Path):
    """Start a runner with its output in ``log_path``; the child outlives the server."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    with open(log_path, "ab") as log:
        return subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kwargs
        )


@dataclass
class Job:
    id: str
    slug: str
    kind: str
    paths: JobPaths
    bin: str = ""
    bin_name: str = ""  # for the system bar, which names the bin without loading the project
    queued_by: str | None = None  # "user" | "agent"; None for a job this process re-attached
    status: JobStatus = "queued"
    progress: float | None = None
    task: str | None = None
    error: str | None = None
    comfy: LiveProgress | None = None  # live ComfyUI progress from the status file
    takes: list[str] = field(default_factory=list)
    started: str = field(default_factory=utc_now)
    finished: str | None = None
    exec_seconds: float | None = None  # ComfyUI's execution time, queue wait excluded; known once it lands
    stale_after: float = 3600.0
    prompt_id: str | None = None  # from the status file (or the log), for a late pickup
    api_base: str = ""  # the ComfyUI it renders on: a launch waits while that is unreachable
    proc: Any | None = None
    watcher: asyncio.Task | None = None
    collector: asyncio.Task | None = None  # the late pass, after a failure that left the prompt with ComfyUI
    watch_started: float = field(default_factory=time.monotonic)
    # stall watch: what the progress last read and when it last changed (monotonic)
    progress_mark: Any = None
    progress_at: float = field(default_factory=time.monotonic)
    stalled: bool = False
    # the late pass may queue this render again when ComfyUI has lost it; once only
    requeue: bool = False
    retried: bool = False

    @property
    def live(self) -> bool:
        return self.status in LIVE


def to_out(job: Job) -> JobOut:
    live = job.comfy if job.status == "running" and job.comfy is not None else LiveProgress()
    return JobOut(
        job=job.id, slug=job.slug, bin=job.bin, status=job.status, progress=job.progress, task=job.task,
        error=job.error, takes=list(job.takes), started=job.started, finished=job.finished, exec_seconds=job.exec_seconds,
        phase=live.phase, step=live.step, steps=live.steps, pass_no=live.pass_no, passes=live.passes,
        queue_ahead=live.queue_ahead, queue_ahead_ours=live.queue_ahead_ours,
        bin_name=job.bin_name, queued_by=job.queued_by if job.queued_by in ("user", "agent") else None,
        stalled=bool(job.stalled and job.status == "running"), retried=job.retried,
    )


# -- console echo -------------------------------------------------------------


def _say(line: str) -> None:
    """Print to the window the server was launched from. A prompt is user text and can hold
    anything; a console on cp1252 would raise on the exotic parts, so fall back to escapes
    rather than let an echo take down a render."""
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def _echo_prompt(project: Project, slug: str, label: str, gen: Generation, spec: dict) -> None:
    """What actually goes to ComfyUI, once the .tokens have been swapped for the workflow's own
    notation (<Picture 1>, image1, or the bin's plain name when the workflow has no token
    profile). The typed line is shown above it so the substitution is readable. A private
    project's words stay inside it: the console gets the job id and nothing it says."""
    if project.private:
        _say(f"[THM] {slug} | {gen.id} | private project: render queued, prompt not echoed")
        return
    _say(f"[THM] {slug} | {label} | {Path(spec['workflow']).name}")
    _say(f"[THM]   typed: {gen.prompt_typed}")
    _say(f"[THM]   sent : {spec['prompt']}")
    if spec.get("negative"):
        _say(f"[THM]   neg  : {spec['negative']}")
    for line in spec.get("prelude") or []:
        _say(f"[THM]   pre  : {line}")
    for slot, path in (spec.get("paths_by_slot") or {}).items():
        _say(f"[THM]   slot {slot}: {path}")


class JobManager:
    def __init__(
        self,
        *,
        store: ProjectStore,
        registry: ProjectRegistry,
        bus: EventBus,
        settings: CoreSettings,
        scripts_dir: Path | None = None,
        launch: Launcher = default_launch,
        poll_interval: float = 0.5,
        kill: Killer = kill_runner,
        comfy_cancel: ComfyCancel = cancel_prompts,
        late_lookup: LateLookup = late_prompt_state,
        late_poll_interval: float = 30.0,
        no_status_grace: float = 30.0,
        comfy_up: Callable[[str], bool] | None = None,
        stall_after: float = STALL_AFTER_S,
        comfy_restart: Callable[[str, str, Path], Any] | None = None,
    ):
        self.store = store
        self.registry = registry
        self.bus = bus
        self.settings = settings
        self.scripts_dir = Path(scripts_dir) if scripts_dir else REPO_ROOT / "scripts"
        self.launch_fn = launch
        self.kill_fn = kill
        self.comfy_cancel_fn = comfy_cancel
        self.late_lookup_fn = late_lookup
        self.poll_interval = poll_interval
        self.late_poll_interval = late_poll_interval
        # a re-attached job whose status file is missing gets this long before it is given up:
        # the file is replaced whole on every progress line, and a read can land mid-replace
        self.no_status_grace = no_status_grace
        # a render queued while ComfyUI is down (a restart, a reboot) waits for it rather than
        # failing; real runners ask the real ComfyUI, an injected launcher has no ComfyUI to ask
        self.comfy_up_fn = comfy_up or (comfy_reachable if launch is default_launch else (lambda _base: True))
        self.stall_after = stall_after
        self.comfy_restart_fn = comfy_restart or spawn_comfy_restart
        self._jobs: dict[str, Job] = {}
        # projects told that ComfyUI is unreachable, so the outage is one line per project, not
        # one per render; cleared (with a line) when it answers again
        self._outage_told: set[str] = set()
        self.comfy_restarted_at: str | None = None  # the last restart this server started (utc)

    # -- lookups --------------------------------------------------------------

    def get(self, gen_id: str) -> Job | None:
        return self._jobs.get(gen_id)

    def for_project(self, slug: str) -> list[Job]:
        return [j for j in self._jobs.values() if j.slug == slug]

    def live_for_bin(self, slug: str, bin_id: str) -> list[Job]:
        """Every live render on a bin, oldest first. A bin takes as many as you start: each
        generation draws its own seed, so three taps are three different takes."""
        return sorted((j for j in self._jobs.values() if j.slug == slug and j.bin == bin_id and j.live), key=lambda j: j.started)

    # -- starting -------------------------------------------------------------

    def _spec_target(self, project: Project, gen: Generation) -> tuple[str, str, str]:
        """``(kind, output_dir_id, label)`` for a generation: the bin's id is the path element
        under the output root (design section 4) and the generation id rides in the label, which
        is how cancel finds the prompt in ComfyUI's queue."""
        bin_ = project.bins_by_id[gen.bin]
        return gen.kind, bin_.id, f"{bin_.name} {gen.id}"

    @staticmethod
    def _bin_name(project: Project, gen: Generation) -> str:
        bin_ = project.bins_by_id.get(gen.bin)
        return bin_.name if bin_ is not None else ""

    def prepare(self, slug: str, project: Project, gen: Generation, *, queued_by: str | None = None) -> Job:
        """Write the spec and the ``running`` record. Call inside the project lock; then ``launch``
        outside it. ``queued_by`` is the actor whose command made the generation."""
        kind, output_dir_id, label = self._spec_target(project, gen)
        comfy = resolve_comfy(project, self.settings.comfy)
        paths = job_paths(self.store.files_dir(project), gen.id)
        spec = build_generation_spec(
            project, gen, output_dir_id=output_dir_id, label=label, comfy=comfy, workflows_dir=self.settings.workflows_dir
        )
        paths.spec.parent.mkdir(parents=True, exist_ok=True)
        paths.spec.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        _echo_prompt(project, slug, label, gen, spec)
        # the caller's record stays "queued" (it is the command's response); the log gets the running line
        self.store.append_generation(project, gen.model_copy(update={"status": "running"}))
        job = Job(
            id=gen.id, slug=slug, kind=kind, paths=paths, bin=gen.bin, bin_name=self._bin_name(project, gen),
            queued_by=queued_by, stale_after=float(comfy.timeout_for(kind)), api_base=comfy.api_base,
        )
        self._jobs[gen.id] = job
        return job

    def launch(self, job: Job, *, after_restart: bool = False) -> Job:
        """``after_restart``: the render waits for ComfyUI to go down and come back (a restart
        this server just started), rather than posting to the instance about to be killed."""
        if job.api_base and (after_restart or not self.comfy_up_fn(job.api_base)):
            # ComfyUI is down: the render waits for it, queued, and starts when it answers
            job.task = "waiting for ComfyUI to restart" if after_restart else "waiting for ComfyUI to come back"
            self._publish(job)
            job.watcher = asyncio.create_task(self._launch_when_up(job, require_down=after_restart))
            return job
        return self._start(job)

    async def _launch_when_up(self, job: Job, *, require_down: bool = False) -> None:
        """``require_down``: ComfyUI must be seen down once before an answer counts as back
        (the old instance may still answer for a moment after a restart is started); after
        ``RESTART_DOWN_GRACE_S`` with no outage seen, an answering ComfyUI is taken as the new one."""
        told = False
        seen_down = not require_down
        t0 = time.monotonic()
        while job.status == "queued":
            if not told:
                # one line per project while it lasts, not one per render; the first poll gets
                # it, so a restart the server itself started says so before anything waits
                await self._tell_outage(job)
                told = True
            await asyncio.sleep(COMFY_WAIT_POLL_S)
            if job.status != "queued":
                return  # cancelled while it waited
            up = await asyncio.to_thread(self.comfy_up_fn, job.api_base)
            if not up:
                seen_down = True
                continue
            if not seen_down and time.monotonic() - t0 < RESTART_DOWN_GRACE_S:
                continue  # still the instance about to go down
            job.task = None
            await self._tell_back(job)
            self._start(job)
            return

    # -- alerts: the server's own lines in a project's feed --------------------------

    async def _alert(self, slug: str, text: str) -> None:
        """A system line in the project feed, prefixed so the agent's watch wakes on it. The
        app shows it like any system line; the user reads it in the feed and on their phone."""
        async with self.registry.lock(slug):
            try:
                project = self.registry.get(slug)
                sess = project.agent_session
                msg = Message(
                    id=core_ids.new_id(core_ids.MESSAGE), ts=utc_now(), kind="system", text=ALERT_PREFIX + text,
                    session=sess.id if sess is not None and sess.status in ("waiting", "live") else None,
                )
                self.store.append_message(project, msg)
                version = self.registry.touch(slug)
            except ProjectNotFound:
                return
        try:
            self.bus.publish_event(slug, "message", feed_entry(project, msg, None, None, None).model_dump(mode="json"))
        except Exception:
            pass
        self.bus.publish(slug, version)

    async def _tell_outage(self, job: Job) -> None:
        if job.slug in self._outage_told:
            return
        self._outage_told.add(job.slug)
        waiting = [j for j in self._jobs.values() if j.slug == job.slug and j.status == "queued" and j.task]
        n = len(waiting) or 1
        await self._alert(
            job.slug,
            f"ComfyUI is not reachable at {job.api_base}; {n} render{'s' if n != 1 else ''} ({', '.join(j.bin_name or j.id for j in waiting) or job.bin_name or job.id}) "
            "wait for it and start by themselves when it answers. Restart ComfyUI, or wait",
        )

    async def _tell_back(self, job: Job) -> None:
        if job.slug not in self._outage_told:
            return
        self._outage_told.discard(job.slug)
        await self._alert(job.slug, f"ComfyUI is back; {job.bin_name or job.id} is starting")

    async def _tell_stall(self, job: Job) -> None:
        pct = f" at {job.progress:.1f}%" if job.progress is not None else ""
        minutes = int(round(self.stall_after / 60)) or 1
        await self._alert(
            job.slug,
            f"render of {job.bin_name or job.id} has made no progress for {minutes} min{pct}: ComfyUI may be hung. "
            "Cancel the render, or restart ComfyUI; a render ComfyUI loses in a restart is queued again by itself",
        )

    def _start(self, job: Job) -> Job:
        cmd = [
            sys.executable, "-u", str(runner_script(job.kind, self.scripts_dir)),
            "--generation", str(job.paths.spec), "--status-file", str(job.paths.status),
        ]
        try:
            job.proc = self.launch_fn(cmd, REPO_ROOT, job.paths.log)
        except Exception as exc:
            job.watcher = asyncio.create_task(self._finish_failed(job, f"could not start runner: {exc}"))
            return job
        self._publish(job)
        job.watcher = asyncio.create_task(self._watch(job))
        return job

    def attach(self, slug: str, project: Project, gen: Generation) -> Job:
        """Watch a generation whose runner was started by an earlier server process (status file only)."""
        kind, _, _ = self._spec_target(project, gen)
        try:
            stale_after = float(resolve_comfy(project, self.settings.comfy).timeout_for(kind))
        except ValueError:
            stale_after = 3600.0
        job = Job(
            id=gen.id, slug=slug, kind=kind, paths=job_paths(self.store.files_dir(project), gen.id),
            bin=gen.bin, bin_name=self._bin_name(project, gen), status="running", started=gen.ts, stale_after=stale_after,
        )
        self._jobs[gen.id] = job
        job.watcher = asyncio.create_task(self._watch(job))
        return job

    async def reconcile(self) -> None:
        """At startup: pick up generations the log still shows as queued or running, and give
        recent failures that may have finished in ComfyUI anyway one more look."""
        projects_dir = Path(self.store.projects_dir)
        if not projects_dir.is_dir():
            return
        for path in sorted(projects_dir.glob("*.json")):
            slug = path.stem
            try:
                project = self.store.load(slug)
            except Exception:
                continue
            for gen in self.store.read_generations(project):
                if gen.id in self._jobs or not gen.bin or gen.bin not in project.bins_by_id:
                    continue
                if gen.status in ("queued", "running"):
                    paths = job_paths(self.store.files_dir(project), gen.id)
                    if paths.spec.is_file() and not paths.status.exists() and not paths.log.exists():
                        # prepared but never started (it was waiting for ComfyUI when this
                        # server went down): start it now, through the same wait
                        self._relaunch(slug, project, gen, paths)
                    else:
                        self.attach(slug, project, gen)
                elif gen.status == "failed" and self._worth_a_late_pass(gen):
                    self.collect_late(slug, project, gen)

    def _relaunch(self, slug: str, project: Project, gen: Generation, paths) -> Job:
        kind, _, _ = self._spec_target(project, gen)
        try:
            comfy = resolve_comfy(project, self.settings.comfy)
            api_base, stale_after = comfy.api_base, float(comfy.timeout_for(kind))
        except ValueError:
            api_base, stale_after = "", 3600.0
        job = Job(
            id=gen.id, slug=slug, kind=kind, paths=paths, bin=gen.bin, bin_name=self._bin_name(project, gen),
            started=gen.ts, stale_after=stale_after, api_base=api_base,
        )
        self._jobs[gen.id] = job
        return self.launch(job)

    @staticmethod
    def _worth_a_late_pass(gen: Generation) -> bool:
        if gen.outputs:
            return False
        runner_notes = [w.get("detail") or "" for w in gen.warnings if isinstance(w, dict) and w.get("kind") == "runner"]
        if not any(any(mark in note for mark in LATE_COLLECTABLE) for note in runner_notes):
            return False
        if any(note.startswith(("ComfyUI later", "landed late")) for note in runner_notes):
            return False  # a late pass already had ComfyUI's final word on it (even if the landing added no take)
        try:
            then = datetime.fromisoformat(str(gen.ts).replace("Z", "+00:00"))
        except ValueError:
            return False
        return (datetime.now(then.tzinfo) - then).total_seconds() <= LATE_LOOKBACK_S

    def collect_late(self, slug: str, project: Project, gen: Generation) -> Job:
        """Start a late pass for a generation an earlier server process gave up on."""
        kind, _, _ = self._spec_target(project, gen)
        paths = job_paths(self.store.files_dir(project), gen.id)
        detail = next((w.get("detail") for w in reversed(gen.warnings) if isinstance(w, dict) and w.get("kind") == "runner"), None)
        job = Job(
            id=gen.id, slug=slug, kind=kind, paths=paths, bin=gen.bin, bin_name=self._bin_name(project, gen),
            status="failed", error=detail, started=gen.ts, finished=gen.ts,
        )
        status = read_status(paths.status)
        job.prompt_id = (status.prompt_id if status else None) or self._prompt_id_from_log(paths.log)
        self._jobs[gen.id] = job
        job.collector = asyncio.create_task(self._collect_late(job))
        return job

    @staticmethod
    def _prompt_id_from_log(path: Path) -> str | None:
        """The runner echoes ``[PID] <prompt_id>`` right after posting; the last one wins."""
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        hits = _PID_LINE.findall(text)
        return hits[-1] if hits else None

    # -- cancelling -----------------------------------------------------------

    async def cancel(self, gen_id: str) -> Job:
        """Stop a live render: the watcher, the runner process, then the prompt in ComfyUI.

        THM's side settles first and never waits on ComfyUI -- the generation is ``cancelled``
        and the bin is free again whether or not ComfyUI could be reached; what ComfyUI said
        goes into the job's ``error`` for the toast. ``KeyError`` for an unknown job,
        ``ValueError`` for one that already finished.
        """
        job = self._jobs.get(gen_id)
        if job is None:
            raise KeyError(gen_id)
        if not job.live:
            raise ValueError(job.status)
        if job.watcher is not None and not job.watcher.done():
            job.watcher.cancel()
            await asyncio.gather(job.watcher, return_exceptions=True)
        if not job.live:  # the watcher finished it in the same instant
            return job
        status = read_status(job.paths.status)
        pid = job.proc.pid if job.proc is not None else (status.pid if status else None)
        await run_in_threadpool(self.kill_fn, job.proc, pid)
        try:
            comfy = resolve_comfy(self.registry.get(job.slug), self.settings.comfy)
            detail = await run_in_threadpool(self.comfy_cancel_fn, comfy.api_base, job.slug, job.id)
        except Exception as exc:
            detail = f"runner stopped; ComfyUI not told ({exc})"
        async with self.registry.lock(job.slug):
            try:
                project = self.registry.get(job.slug)
                gen = self.store.read_generation(project, job.id)
                if gen is not None and gen.status in ("queued", "running"):
                    gen = gen.model_copy()
                    gen.status = "cancelled"
                    gen.warnings = list(gen.warnings) + [{"kind": "cancelled", "handle": "", "detail": detail}]
                    self.store.append_generation(project, gen)
                    # no take will come: its card owes nothing any more
                    close_render_card(self.store, project, gen.id, "generation cancelled")
            except ProjectNotFound:
                pass
        job.status, job.error, job.task, job.finished = "cancelled", detail, None, utc_now()
        self._publish(job)
        return job

    def forget(self, slug: str) -> None:
        """Drop a deleted project's finished job records. Callers refuse the delete while one is live."""
        for gen_id in [j.id for j in self._jobs.values() if j.slug == slug and not j.live]:
            job = self._jobs.pop(gen_id, None)
            if job is not None and job.collector is not None and not job.collector.done():
                job.collector.cancel()

    async def shutdown(self) -> None:
        """Stop watching. Runners keep going; ``reconcile`` finds them on the next start."""
        tasks = [t for j in self._jobs.values() for t in (j.watcher, j.collector) if t is not None and not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- watching -------------------------------------------------------------

    async def _watch(self, job: Job) -> None:
        try:
            while True:
                status = read_status(job.paths.status)
                if status is not None:
                    job.prompt_id = status.prompt_id or job.prompt_id
                    if status.status == "completed":
                        await self._finish_done(job, status.outputs, exec_seconds=status.exec_seconds)
                        return
                    if status.status == "failed":
                        await self._finish_failed(job, status.error or "runner reported failure", late=self._left_with_comfy(status.error))
                        return
                    changed = (
                        job.status != "running" or job.progress != status.progress or job.task != status.task or job.comfy != status.live
                    )
                    job.status, job.progress, job.task, job.comfy = "running", status.progress, status.task, status.live
                    if changed:
                        self._publish(job)
                    await self._watch_stall(job, status)
                if job.proc is not None and job.proc.poll() is not None:
                    final = read_status(job.paths.status)
                    if final is not None:
                        job.prompt_id = final.prompt_id or job.prompt_id
                    if final is not None and final.status == "completed":
                        await self._finish_done(job, final.outputs, exec_seconds=final.exec_seconds)
                        return
                    if final is not None and final.status == "failed":
                        await self._finish_failed(job, final.error or "runner reported failure", late=self._left_with_comfy(final.error))
                        return
                    tail = log_tail(job.paths.log)
                    code = getattr(job.proc, "returncode", None)
                    await self._finish_failed(job, f"runner exited with code {code}" + (f"\n{tail}" if tail else ""), late=True)
                    return
                if job.proc is None:
                    if status is None and time.monotonic() - job.watch_started < self.no_status_grace:
                        pass  # the file may be mid-replace, or the runner not yet started
                    else:
                        why = self._stale(job, status)
                        if why:
                            await self._finish_failed(job, why, late=True)
                            return
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a watcher must never leave a job live forever
            try:
                await self._finish_failed(job, f"job watcher error: {exc}")
            except Exception:
                job.status, job.error, job.finished = "failed", f"job watcher error: {exc}", utc_now()
                self._publish(job)

    async def _watch_stall(self, job: Job, status: RunnerStatus) -> None:
        """Progress that has not moved for ``stall_after`` is a stall: said once in the feed,
        carried on the job until it moves again. Only an executing prompt can stall; one still
        waiting in ComfyUI's queue is waiting, not stuck."""
        live = status.live
        mark = (status.progress, getattr(live, "phase", None), getattr(live, "step", None), getattr(live, "pass_no", None), getattr(live, "queue_ahead", None))
        now = time.monotonic()
        if mark != job.progress_mark:
            job.progress_mark, job.progress_at = mark, now
            if job.stalled:
                job.stalled = False
                self._publish(job)
            return
        if job.stalled or getattr(live, "phase", None) == "queued" or now - job.progress_at < self.stall_after:
            return
        job.stalled = True
        self._publish(job)
        await self._tell_stall(job)

    def _stale(self, job: Job, status: RunnerStatus | None) -> str | None:
        """Why a re-attached job (no process handle) should be given up, or None to keep waiting."""
        if status is None:
            return "server restarted while generating and the runner left no status file"
        if not status.last_update:
            return None
        try:
            then = datetime.fromisoformat(status.last_update)
        except ValueError:
            return None
        booted = boot_time()
        if booted is not None and then < booted:
            return "the machine rebooted while generating; the runner did not survive it"
        if (datetime.now() - then).total_seconds() > job.stale_after:
            return "server restarted while generating and the runner's status went stale"
        return None

    @staticmethod
    def _left_with_comfy(error: str | None) -> bool:
        """A runner failure after which ComfyUI may still finish the prompt (it gave up waiting,
        rather than ComfyUI refusing or failing the prompt)."""
        text = str(error or "")
        return any(mark in text for mark in LATE_COLLECTABLE) and "dropped by ComfyUI" not in text

    # -- the late pass ----------------------------------------------------------

    async def _collect_late(self, job: Job) -> None:
        """Keep asking ComfyUI for a failed job's prompt; land it under the same generation when
        it finishes. Stops when ComfyUI has no record of the prompt, when the entry reports an
        error, or after ``LATE_HORIZON_S``. One GET per ``late_poll_interval`` while the prompt
        is still queued or ComfyUI is unreachable."""
        t0 = time.monotonic()
        try:
            while True:
                try:
                    comfy = resolve_comfy(self.registry.get(job.slug), self.settings.comfy)
                except (ProjectNotFound, ValueError):
                    return
                try:
                    state, pid, entry = await run_in_threadpool(self.late_lookup_fn, comfy.api_base, job.slug, job.id, job.prompt_id)
                except Exception:
                    state, pid, entry = "unreachable", None, None
                if pid:
                    job.prompt_id = pid
                if state == "done":
                    err = comfy_history.history_error_detail(entry)
                    if err:
                        await self._note(job, f"ComfyUI later failed prompt {job.prompt_id}: {err}")
                        return
                    outputs = comfy_history.extract_history_outputs(entry, comfy.output_root, prompt_id=job.prompt_id or "", label=job.id)
                    if not outputs:
                        await self._note(job, f"ComfyUI later finished prompt {job.prompt_id} with no output files")
                        return
                    _say(f"[THM] {job.slug} | {job.id} | landing late: ComfyUI finished prompt {job.prompt_id} after THM gave up")
                    await self._finish_done(
                        job, outputs, note="landed late: ComfyUI finished the prompt after THM gave up on it",
                        exec_seconds=comfy_history.history_exec_seconds(entry),
                    )
                    return
                if state == "gone" and job.requeue and not job.retried:
                    # ComfyUI is answering and has lost the prompt: the render goes again
                    await self._requeue(job)
                    return
                if state == "gone" or time.monotonic() - t0 > LATE_HORIZON_S:
                    return
                await asyncio.sleep(self.late_poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a late pass is best effort; never take the server down over it
            _say(f"[THM] {job.slug} | {job.id} | late pass stopped: {exc}")

    # -- queued again -----------------------------------------------------------

    @staticmethod
    def _worth_a_requeue(error: str | None) -> bool:
        text = str(error or "")
        return any(mark in text for mark in REQUEUE_MARKS)

    async def _requeue(self, job: Job, *, why: str | None = None, after_restart: bool = False) -> Job:
        """Queue a render again on the same spec: the generation goes back to ``queued`` with the
        reason on its record, the old status file goes (the watcher must not read the failure
        again), and ``launch`` runs it -- or waits for ComfyUI first. Once per generation."""
        why = why or job.error or "ComfyUI lost the render"
        job.retried, job.requeue = True, False
        async with self.registry.lock(job.slug):
            try:
                project = self.registry.get(job.slug)
                gen = self.store.read_generation(project, job.id)
                if gen is None or gen.bin not in project.bins_by_id or gen.outputs:
                    return job
                gen = gen.model_copy()
                gen.status = "queued"
                gen.warnings = list(gen.warnings) + [{"kind": "runner", "handle": "", "detail": f"queued again: {why}"}]
                self.store.append_generation(project, gen)
                version = self.registry.touch(job.slug)
            except ProjectNotFound:
                return job
        try:
            job.paths.status.unlink(missing_ok=True)
        except OSError:
            pass
        job.status, job.error, job.task, job.finished, job.progress, job.comfy = "queued", None, None, None, None, None
        job.proc, job.prompt_id, job.stalled, job.collector = None, None, False, None
        job.watch_started, job.progress_at, job.progress_mark = time.monotonic(), time.monotonic(), None
        self.bus.publish(job.slug, version)
        _say(f"[THM] {job.slug} | {job.id} | queued again: {why}")
        return self.launch(job, after_restart=after_restart)

    # -- restarting ComfyUI -----------------------------------------------------

    async def restart_comfy(self, *, by: str, reason: str = "", slug: str | None = None) -> dict:
        """Stop and start ComfyUI through the user's own script (``[advanced] restart_script``).
        Every live render on it is stopped first and queued again, to start when ComfyUI answers;
        every project with one gets the line. Refuses with ValueError when no script is set."""
        script = str(getattr(self.settings.comfy, "restart_script", "") or "").strip()
        if not script:
            raise ValueError("no ComfyUI restart script: set [advanced] restart_script in config.toml (the script that stops and starts ComfyUI on this machine)")
        cwd = Path(str(getattr(self.settings.comfy, "install_path", "") or "") or Path(script).parent)
        live = [j for j in self._jobs.values() if j.live]
        requeued: list[str] = []
        # the restart line below is the outage line for these projects: no second "not reachable"
        self._outage_told.update(j.slug for j in live)
        for job in live:
            if job.watcher is not None and not job.watcher.done():
                job.watcher.cancel()
                await asyncio.gather(job.watcher, return_exceptions=True)
            if job.status == "running":
                status = read_status(job.paths.status)
                pid = job.proc.pid if job.proc is not None else (status.pid if status else None)
                await run_in_threadpool(self.kill_fn, job.proc, pid)
            if job.retried:
                await self._finish_failed(job, f"ComfyUI restarted by the {by} while this render ran a second time{': ' + reason if reason else ''}")
                continue
            await self._requeue(job, why=f"ComfyUI restarted by the {by}{': ' + reason if reason else ''}", after_restart=True)
            requeued.append(job.id)
        log = REPO_ROOT / "thm_runner" / "logs" / "comfyui-restart.log"
        try:
            await run_in_threadpool(self.comfy_restart_fn, script, str(cwd), log)
        except Exception as exc:
            raise RuntimeError(f"could not run {script}: {exc}") from None
        self.comfy_restarted_at = utc_now()
        told = {j.slug for j in live}
        if slug:
            told.add(slug)
        for s in told:
            n = sum(1 for j in live if j.slug == s and j.id in requeued)
            tail = f"; {n} render{'s' if n != 1 else ''} here queued again and start when it answers" if n else ""
            await self._alert(s, f"ComfyUI is restarting ({by}{': ' + reason if reason else ''}){tail}")
        return {"script": script, "requeued": requeued, "started": self.comfy_restarted_at, "log": str(log)}

    async def _note(self, job: Job, detail: str) -> None:
        """Append a warning to a finished generation's record without changing its status."""
        async with self.registry.lock(job.slug):
            try:
                project = self.registry.get(job.slug)
                gen = self.store.read_generation(project, job.id)
                if gen is not None:
                    gen = gen.model_copy()
                    gen.warnings = list(gen.warnings) + [{"kind": "runner", "handle": "", "detail": detail}]
                    self.store.append_generation(project, gen)
            except ProjectNotFound:
                pass

    # -- finishing ------------------------------------------------------------

    async def _finish_done(self, job: Job, outputs: list[dict], *, note: str | None = None, exec_seconds: float | None = None) -> None:
        """Land the outputs as takes. ``exec_seconds`` is ComfyUI's execution time for the prompt
        (the runner's status file, or the history entry on a late landing); it goes on the
        generation record and the job so the toast and the recipe can say how long the render took."""
        if exec_seconds is not None:
            job.exec_seconds = exec_seconds
        async with self.registry.lock(job.slug):
            project = self.registry.get(job.slug)
            target = project.bins_by_id.get(job.bin)
            gen = self.store.read_generation(project, job.id)
            if target is None or gen is None:
                landed, warnings, version = [], [f"bin {job.bin} or generation {job.id} vanished before landing"], None
            else:
                gen = gen.model_copy()  # land_outputs writes status and outputs onto it
                if job.exec_seconds is not None:
                    gen.exec_seconds = job.exec_seconds
                if note:
                    gen.warnings = list(gen.warnings) + [{"kind": "runner", "handle": "", "detail": note}]
                cache_dir = thumb_cache_dir(self.store, project)
                landed, warnings = await run_in_threadpool(land_outputs, project, target, gen, outputs, cache_dir=cache_dir)
                self.store.append_generation(project, gen)
                self.store.save(project)
                version = self.registry.touch(job.slug)
        job.takes = [m.id for m in landed]
        job.progress = 100.0
        job.task = None
        job.finished = utc_now()
        if landed:
            job.status, job.error = "done", None
        else:
            job.status, job.error = "failed", "; ".join(warnings) or "no output landed"
        if version is not None:
            self.bus.publish(job.slug, version)
        self._publish(job)

    async def _finish_failed(self, job: Job, error: str, *, late: bool = False) -> None:
        """Record the failure. With ``late``, the prompt may still be with ComfyUI: a late
        collector keeps looking for it and lands the result if it ever turns up."""
        async with self.registry.lock(job.slug):
            try:
                project = self.registry.get(job.slug)
                gen = self.store.read_generation(project, job.id)
                if gen is not None and gen.status != "failed":
                    gen = gen.model_copy()
                    gen.status = "failed"
                    gen.warnings = list(gen.warnings) + [{"kind": "runner", "handle": "", "detail": error}]
                    self.store.append_generation(project, gen)
            except ProjectNotFound:
                pass
        job.status, job.error, job.task, job.finished, job.stalled = "failed", error, None, utc_now(), False
        self._publish(job)
        # an interruption (ComfyUI, the machine or this server went away under it) earns the
        # render another go once ComfyUI is back and has no trace of it: the late pass decides
        job.requeue = not job.retried and self._worth_a_requeue(error)
        if late or job.requeue:
            if job.prompt_id is None:
                job.prompt_id = self._prompt_id_from_log(job.paths.log)
            job.collector = asyncio.create_task(self._collect_late(job))

    def _publish(self, job: Job) -> None:
        self.bus.publish_event(job.slug, "job", to_out(job).model_dump(mode="json"))
