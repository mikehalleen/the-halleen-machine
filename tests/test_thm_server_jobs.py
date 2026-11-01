"""thm_server.jobs: a fake runner writes the status file; the manager lands takes and publishes job events."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from thm_core import CommandContext, Generation, execute
from thm_core.commands import Generate
from thm_server import create_app
from thm_server.jobs import JobManager
from thm_server_fixtures import seeded_world, write_png

USER = CommandContext(actor="user")


def _write_status(path: Path, status: str, *, outputs=None, error=None, task=None, pct=None, last_update="2026-09-07T10:00:00", exec_seconds=None):
    data = {"pid": 4242, "status": status, "current_task": task, "sub_task": None, "error": error,
            "progress_percent": f"{pct:.1f}" if pct is not None else None, "last_update": last_update}
    if outputs is not None:
        data["outputs"] = outputs
    if exec_seconds is not None:
        data["exec_seconds"] = exec_seconds
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _status_arg(cmd: list[str]) -> Path:
    return Path(cmd[cmd.index("--status-file") + 1])


def _spec_arg(cmd: list[str]) -> dict:
    return json.loads(Path(cmd[cmd.index("--generation") + 1]).read_text(encoding="utf-8"))


def _complete_on_disk(spec: dict, status_path: Path, color) -> Path:
    """What a finished runner leaves behind: one PNG in output_dir and a completed status file."""
    out = write_png(Path(spec["output_dir"]) / f"{spec['filename_prefix']}_00001_.png", (48, 32), color)
    _write_status(status_path, "completed", task="Done", pct=100.0, exec_seconds=42.3,
                  outputs=[{"path": str(out), "node_id": "9", "prompt_id": "pid-1", "label": spec["label"]}])
    return out


class _Proc:
    """A fake Popen: alive on the first poll, then finishes its work and exits 0."""

    def __init__(self, on_exit):
        self.pid = 4242
        self.returncode = None
        self._polls = 0
        self._on_exit = on_exit

    def poll(self):
        self._polls += 1
        if self._polls < 2:
            return None
        if self.returncode is None:
            self._on_exit()
            self.returncode = 0
        return self.returncode


def completing_launcher(color=(200, 100, 50)):
    """A runner that reports running, then renders one PNG into the spec's output_dir and completes."""
    seen: list[list[str]] = []

    def launch(cmd, cwd, log_path):
        seen.append(cmd)
        spec = _spec_arg(cmd)
        _write_status(_status_arg(cmd), "running", task="Queued", pct=5.0, outputs=[])
        Path(log_path).write_text("[PROMPT] queued\n", encoding="utf-8")
        return _Proc(lambda: _complete_on_disk(spec, _status_arg(cmd), color))

    launch.seen = seen
    return launch


def failing_launcher(cmd, cwd, log_path):
    _write_status(_status_arg(cmd), "failed", error="ConnectionError: comfy is down")
    return SimpleNamespace(pid=4242, poll=lambda: 1, returncode=1)


def crashing_launcher(cmd, cwd, log_path):
    Path(log_path).write_text("Traceback\nKeyError: 'nope'\n", encoding="utf-8")
    return SimpleNamespace(pid=4242, poll=lambda: 1, returncode=1)


def nothing_in_comfy(api_base, slug, gen_id, prompt_id):
    """A late lookup that finds no trace of the prompt: the late pass ends at once."""
    return ("gone", None, None)


def _manager(world, launch, **kw) -> JobManager:
    st = world.app.state.thm
    kw.setdefault("late_lookup", nothing_in_comfy)
    kw.setdefault("no_status_grace", 0.0)
    kw.setdefault("late_poll_interval", 0.01)
    return JobManager(store=st.store, registry=st.registry, bus=st.bus, settings=world.settings, launch=launch, poll_interval=0.01, **kw)


def _queued_generation(world, bin_id: str) -> Generation:
    project = world.registry.get(world.slug)
    result = execute(world.store, project, Generate(bin=bin_id), USER, env=world.env)
    world.registry.touch(world.slug)
    return result.generation


async def _drain(q: asyncio.Queue) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_job_lands_take_and_publishes_events(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    launch = completing_launcher()

    async def scenario():
        mgr = _manager(world, launch)
        q = world.app.state.thm.bus.subscribe(world.slug)
        gen = _queued_generation(world, world.image)
        project = world.registry.get(world.slug)
        job = mgr.prepare(world.slug, project, gen)
        assert job.status == "queued" and mgr.live_for_bin(world.slug, world.image) == [job]
        mgr.launch(job)
        await asyncio.wait_for(job.watcher, 5)
        return mgr, job, await _drain(q), gen

    mgr, job, events, gen = asyncio.run(scenario())

    assert job.status == "done" and job.error is None and len(job.takes) == 1 and job.finished
    assert job.exec_seconds == 42.3  # ComfyUI's execution time, from the runner's final status write
    assert mgr.live_for_bin(world.slug, world.image) == []
    cmd = launch.seen[0]
    assert cmd[2].endswith("run_images.py") and "--generation" in cmd and "--status-file" in cmd
    spec = _spec_arg(cmd)
    assert spec["output_dir"].startswith((tmp_path / "comfy_out").as_posix()) and "seed" in spec["params"]

    world.registry.invalidate(world.slug)
    project = world.registry.get(world.slug)
    bin_ = project.bins_by_id[world.image]
    assert bin_.media[-1] == job.takes[0] and bin_.selected == job.takes[0]
    assert project.media_by_id[job.takes[0]].origin.generation == gen.id and project.media_by_id[job.takes[0]].take == 2

    records = [g for g in world.store.read_generations(project) if g.id == gen.id]
    assert len(records) == 1 and records[0].status == "done" and records[0].outputs == job.takes
    assert records[0].params["seed"] == spec["params"]["seed"] and records[0].exec_seconds == 42.3
    raw = (world.store.files_dir(project) / "generations.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(ln)["status"] for ln in raw if json.loads(ln)["id"] == gen.id] == ["queued", "running", "done"]

    kinds = [(e[0], e[1].get("status")) for e in events]
    assert kinds == [("job", "queued"), ("job", "running"), ("project", None), ("job", "done")]
    assert events[1][1]["progress"] == 5.0 and events[1][1]["task"] == "Queued"
    done = events[-1]
    assert done[2] is None and done[1]["takes"] == job.takes and done[1]["bin"] == world.image and done[1]["job"] == gen.id
    assert done[1]["exec_seconds"] == 42.3 and events[1][1]["exec_seconds"] is None  # known only once it lands
    assert all(e[2] is None for e in events if e[0] == "job")


def test_job_lands_after_uploads_in_a_bin_that_holds_them(tmp_path):
    """A bin that already holds uploads takes the new render as its next take and leads with it."""
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    launch = completing_launcher()

    async def scenario():
        mgr = _manager(world, launch)
        q = world.app.state.thm.bus.subscribe(world.slug)
        gen = _queued_generation(world, world.mara)
        project = world.registry.get(world.slug)
        job = mgr.prepare(world.slug, project, gen)
        assert job.bin == world.mara
        mgr.launch(job)
        await asyncio.wait_for(job.watcher, 5)
        return job, await _drain(q), gen

    job, events, gen = asyncio.run(scenario())

    assert job.status == "done" and len(job.takes) == 1
    cmd = launch.seen[0]
    spec = _spec_arg(cmd)
    assert spec["output_dir"].endswith(f"{world.slug}/{world.mara}") and spec["filename_prefix"] == world.mara
    assert spec["label"] == f"Mara {gen.id}"

    world.registry.invalidate(world.slug)
    project = world.registry.get(world.slug)
    mara = project.bins_by_id[world.mara]
    assert mara.media == [world.m1, world.m2, job.takes[0]] and mara.selected == job.takes[0]
    assert project.media_by_id[job.takes[0]].origin.bin == world.mara and project.media_by_id[job.takes[0]].take == 1
    assert project.bins_by_id[world.image].media == [world.take]  # untouched

    rec = next(g for g in world.store.read_generations(project) if g.id == gen.id)
    assert rec.status == "done" and rec.outputs == job.takes and rec.bin == world.mara
    done = next(e for e in events if e[0] == "job" and e[1]["status"] == "done")
    assert done[1]["bin"] == world.mara


def test_failed_status_marks_generation_failed(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    async def scenario():
        mgr = _manager(world, failing_launcher)
        q = world.app.state.thm.bus.subscribe(world.slug)
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await asyncio.wait_for(job.watcher, 5)
        return job, await _drain(q), gen

    job, events, gen = asyncio.run(scenario())
    assert job.status == "failed" and "comfy is down" in job.error
    assert events[-1][0] == "job" and events[-1][1]["status"] == "failed" and "comfy is down" in events[-1][1]["error"]
    rec = world.store.read_generation(world.slug, gen.id)
    assert rec.status == "failed" and rec.warnings[-1]["kind"] == "runner"
    assert world.registry.get(world.slug).bins_by_id[world.image].media == [world.take]


class _HangingProc:
    """A runner that never finishes on its own: alive until killed."""

    def __init__(self):
        self.pid = 4343
        self.returncode = None
        self.killed = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def hanging_launcher(cmd, cwd, log_path):
    _write_status(_status_arg(cmd), "running", task="Queued", pct=5.0, outputs=[])
    return _HangingProc()


def test_cancel_kills_runner_tells_comfy_and_marks_generation_cancelled(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    told: list[tuple] = []
    killed: list[tuple] = []

    def fake_comfy_cancel(api_base, slug, gen_id):
        told.append((api_base, slug, gen_id))
        return "removed from ComfyUI's queue"

    def fake_kill(proc, pid):
        killed.append((proc, pid))
        if proc is not None:
            proc.kill()

    async def scenario():
        mgr = _manager(world, hanging_launcher, kill=fake_kill, comfy_cancel=fake_comfy_cancel)
        q = world.app.state.thm.bus.subscribe(world.slug)
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await asyncio.sleep(0.05)
        assert mgr.live_for_bin(world.slug, world.image) == [job]
        out = await mgr.cancel(gen.id)
        events = await _drain(q)
        with pytest.raises(ValueError):
            await mgr.cancel(gen.id)
        with pytest.raises(KeyError):
            await mgr.cancel("g_nope")
        return out, events, gen, mgr

    job, events, gen, mgr = asyncio.run(scenario())
    assert job.status == "cancelled" and job.error == "removed from ComfyUI's queue" and job.finished
    assert job.proc.killed and killed == [(job.proc, 4343)]
    assert told == [(world.settings.comfy.api_base, world.slug, gen.id)]
    assert events[-1][0] == "job" and events[-1][1]["status"] == "cancelled"
    rec = world.store.read_generation(world.slug, gen.id)
    assert rec.status == "cancelled" and rec.warnings[-1]["kind"] == "cancelled"
    assert mgr.live_for_bin(world.slug, world.image) == []
    assert world.registry.get(world.slug).bins_by_id[world.image].media == [world.take]


def test_cancel_settles_even_when_comfy_is_unreachable(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    def down(api_base, slug, gen_id):
        raise ConnectionError("refused")

    async def scenario():
        mgr = _manager(world, hanging_launcher, comfy_cancel=down)
        gen = _queued_generation(world, world.video)
        mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await asyncio.sleep(0.05)
        return await mgr.cancel(gen.id)

    job = asyncio.run(scenario())
    assert job.status == "cancelled" and "ComfyUI not told" in job.error and job.proc.killed


def test_cancel_route(tmp_path):
    from starlette.testclient import TestClient

    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    st = world.app.state.thm
    st.jobs.launch_fn = hanging_launcher
    st.jobs.comfy_cancel_fn = lambda api_base, slug, gen_id: "interrupted in ComfyUI"
    with TestClient(world.app) as client:
        r = client.post(f"/api/projects/{world.slug}/commands/generate", json={"bin": world.image})
        assert r.status_code == 200, r.text
        gen_id = r.json()["generation"]["id"]
        assert client.post(f"/api/projects/{world.slug}/jobs/g_nope/cancel").status_code == 404
        r = client.post(f"/api/projects/{world.slug}/jobs/{gen_id}/cancel")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled" and r.json()["error"] == "interrupted in ComfyUI"
        r = client.post(f"/api/projects/{world.slug}/jobs/{gen_id}/cancel")
        assert r.status_code == 409 and r.json()["code"] == "job_not_live"
        assert client.get(f"/api/projects/{world.slug}/generations/{gen_id}").json()["status"] == "cancelled"
        # the bin is free again
        assert client.post(f"/api/projects/{world.slug}/commands/generate", json={"bin": world.image}).status_code == 200


def test_exit_without_status_uses_log_tail(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    async def scenario():
        mgr = _manager(world, crashing_launcher)
        gen = _queued_generation(world, world.video)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await asyncio.wait_for(job.watcher, 5)
        return job

    job = asyncio.run(scenario())
    assert job.status == "failed" and "exited with code 1" in job.error and "KeyError: 'nope'" in job.error


def test_launch_error_fails_cleanly(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    def boom(cmd, cwd, log_path):
        raise OSError("no such interpreter")

    async def scenario():
        mgr = _manager(world, boom)
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await asyncio.wait_for(job.watcher, 5)
        return job

    job = asyncio.run(scenario())
    assert job.status == "failed" and "could not start runner" in job.error


def test_reconcile_lands_a_completed_orphan_and_fails_a_missing_one(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    async def scenario():
        # a previous server process launched two renders; one completed, one vanished
        first = _manager(world, crashing_launcher)
        done_gen = _queued_generation(world, world.image)
        done_job = first.prepare(world.slug, world.registry.get(world.slug), done_gen)
        _complete_on_disk(json.loads(done_job.paths.spec.read_text(encoding="utf-8")), done_job.paths.status, (1, 2, 3))
        lost_gen = _queued_generation(world, world.video)
        first.prepare(world.slug, world.registry.get(world.slug), lost_gen)
        # a fresh manager, as after a restart
        mgr = _manager(world, crashing_launcher)
        await mgr.reconcile()
        jobs = mgr.for_project(world.slug)
        await asyncio.wait_for(asyncio.gather(*[j.watcher for j in jobs]), 5)
        return {j.id: j for j in jobs}, done_gen, lost_gen

    jobs, done_gen, lost_gen = asyncio.run(scenario())
    assert jobs[done_gen.id].status == "done" and len(jobs[done_gen.id].takes) == 1
    assert jobs[lost_gen.id].status == "failed" and "restarted" in jobs[lost_gen.id].error
    world.registry.invalidate(world.slug)
    project = world.registry.get(world.slug)
    assert jobs[done_gen.id].takes[0] in project.bins_by_id[world.image].media
    assert world.store.read_generation(project, lost_gen.id).status == "failed"


def test_reconcile_fails_a_runner_from_before_the_last_boot(tmp_path, monkeypatch):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    from datetime import datetime, timedelta

    import thm_server.jobs as jobs_mod

    async def scenario():
        first = _manager(world, crashing_launcher)
        gen = _queued_generation(world, world.image)
        job = first.prepare(world.slug, world.registry.get(world.slug), gen)
        _write_status(job.paths.status, "running", task="Queued", pct=5.0, outputs=[])  # fresh, still "running"
        # the machine booted a minute after that status line was written
        monkeypatch.setattr(jobs_mod, "boot_time", lambda: datetime.fromisoformat("2026-09-07T10:01:00"))
        mgr = _manager(world, crashing_launcher)
        await mgr.reconcile()
        (attached,) = mgr.for_project(world.slug)
        await asyncio.wait_for(attached.watcher, 5)
        # a fresh status written after boot, with a boot before it, keeps waiting
        keep = _manager(world, crashing_launcher)
        job2 = keep.attach(world.slug, world.registry.get(world.slug), gen)
        _write_status(job2.paths.status, "running", task="Queued", pct=5.0, outputs=[], last_update=datetime.now().isoformat())
        monkeypatch.setattr(jobs_mod, "boot_time", lambda: datetime.now() - timedelta(days=30))
        await asyncio.sleep(0.05)
        waiting = job2.status
        job2.watcher.cancel()
        return attached, waiting

    attached, waiting = asyncio.run(scenario())
    assert attached.status == "failed" and "rebooted" in attached.error
    assert waiting == "running"


# -- over HTTP ------------------------------------------------------------------


def _wait_for(client, url, pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(url).json()
        if pred(body):
            return body
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting on {url}: {body}")


def test_generate_over_http_lands_take(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    app = create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": completing_launcher((5, 5, 250)), "poll_interval": 0.01})
    base = f"/api/projects/{world.slug}"
    with TestClient(app) as client:
        before = client.get(f"{base}/bins/{world.image}").json()
        assert before["active_jobs"] == [] and len(before["media"]) == 1 and before["bin"]["unseen"] == []

        r = client.post(f"{base}/commands/generate", json={"bin": world.image})
        assert r.status_code == 200, r.text
        gen = r.json()["generation"]
        assert gen["status"] == "queued" and r.json()["change"]["generation"] == gen["id"]

        jobs = _wait_for(client, f"{base}/jobs", lambda js: any(j["job"] == gen["id"] and j["status"] in ("done", "failed") for j in js))
        job = next(j for j in jobs if j["job"] == gen["id"])
        assert job["status"] == "done" and len(job["takes"]) == 1 and job["bin"] == world.image

        after = client.get(f"{base}/bins/{world.image}").json()
        assert after["active_jobs"] == []
        assert [t["id"] for t in after["media"]][0] == job["takes"][0] and after["selected"] == job["takes"][0]  # newest first
        assert after["media"][0]["take"] == 2
        # a landed take is new until the user looks at it; the explorer's card says so too
        assert after["bin"]["unseen"] == job["takes"]
        card = next(b for b in client.get(f"{base}/bins").json() if b["id"] == world.image)
        assert card["unseen"] == job["takes"] and card["generating"] == []
        seen = client.post(f"{base}/media/{job['takes'][0]}/seen")
        assert seen.status_code == 200 and seen.json()["cleared"] == [world.image]
        assert client.get(f"{base}/bins/{world.image}").json()["bin"]["unseen"] == []
        again = client.post(f"{base}/media/{job['takes'][0]}/seen").json()
        assert again["cleared"] == [] and again["project_version"] == seen.json()["project_version"]
        assert client.post(f"{base}/media/m_nope/seen").status_code == 404
        rec = client.get(f"{base}/generations/{gen['id']}").json()
        assert rec["status"] == "done" and rec["outputs"] == job["takes"]
        assert client.get(f"{base}/generations/g_nope").status_code == 404
        card = next(c for c in client.get("/api/projects").json() if c["slug"] == world.slug)
        assert card["image_count"] == 4 and card["hero"]["id"] == job["takes"][0]


def test_generations_stack_on_a_bin_and_need_an_output_root(tmp_path):
    """A bin takes as many generations as you start, each with its own seed; what it refuses
    is rendering with nowhere to write, and deleting the bin while a render is live."""
    world = seeded_world(tmp_path)
    base = f"/api/projects/{world.slug}"

    r = world.client.post(f"{base}/commands/generate", json={"bin": world.image})
    assert r.status_code == 409 and r.json()["code"] == "comfy_unconfigured"
    assert world.store.read_generations(world.slug) and all(g.status == "done" for g in world.store.read_generations(world.slug))

    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    def never_finishes(cmd, cwd, log_path):
        _write_status(_status_arg(cmd), "running", task="Queued", pct=5.0, outputs=[])
        return SimpleNamespace(pid=1, poll=lambda: None, returncode=None)

    app = create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": never_finishes, "poll_interval": 0.01})
    with TestClient(app) as client:
        first = client.post(f"{base}/commands/generate", json={"bin": world.video})
        assert first.status_code == 200, first.text
        second = client.post(f"{base}/commands/generate", json={"bin": world.video})
        assert second.status_code == 200, second.text
        ids = [first.json()["generation"]["id"], second.json()["generation"]["id"]]
        seeds = {client.get(f"{base}/generations/{g}").json()["params"]["seed"] for g in ids}
        assert len(seeds) == 2
        other = client.post(f"{base}/commands/generate", json={"bin": world.image})
        assert other.status_code == 200
        detail = _wait_for(client, f"{base}/bins/{world.video}", lambda d: len(d["active_jobs"]) == 2 and all(j["status"] == "running" for j in d["active_jobs"]))
        assert [j["job"] for j in detail["active_jobs"]] == ids  # oldest first
        assert detail["active_jobs"][0]["progress"] == 5.0 and detail["active_jobs"][0]["task"] == "Queued"
        card = next(b for b in client.get(f"{base}").json()["bins"] if b["id"] == world.video)
        assert card["generating"] == ids
        gone = client.post(f"{base}/commands/delete_bin", json={"bin": world.video})
        assert gone.status_code == 409 and gone.json()["code"] == "bin_busy" and gone.json()["extra"]["job"] == ids[0]
        overlay = client.get(f"{base}/generations/{ids[0]}").json()
        assert overlay["status"] == "running"


def test_held_generations_wait_for_release(tmp_path):
    """Save for later: a generate with hold starts no runner and sits under status=held; a bin
    edit after the save leaves the snapshot alone; release starts every held one in save order
    through the ordinary path, and the takes land; the held list is then empty."""
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    launch = completing_launcher((5, 5, 250))
    app = create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": launch, "poll_interval": 0.01})
    base = f"/api/projects/{world.slug}"
    with TestClient(app) as client:
        client.post(f"{base}/commands/set_bin_field", json={"bin": world.image, "field": "prompt", "value": "first"})
        one = client.post(f"{base}/commands/generate", json={"bin": world.image, "hold": True})
        assert one.status_code == 200, one.text
        assert one.json()["generation"]["status"] == "held" and "saved for later" in one.json()["change"]["description"]
        client.post(f"{base}/commands/set_bin_field", json={"bin": world.image, "field": "prompt", "value": "second"})
        two = client.post(f"{base}/commands/generate", json={"bin": world.video, "hold": True})
        assert two.status_code == 200, two.text
        ids = [one.json()["generation"]["id"], two.json()["generation"]["id"]]
        assert launch.seen == [] and client.get(f"{base}/jobs").json() == []
        held = client.get(f"{base}/generations", params={"status": "held"}).json()
        assert [g["id"] for g in held] == ids and held[0]["prompt_submitted"] == "first"
        assert client.get(f"{base}/bins/{world.image}").json()["active_jobs"] == []

        r = client.post(f"{base}/held/release")
        assert r.status_code == 200, r.text
        assert [j["job"] for j in r.json()] == ids and all(j["queued_by"] == "user" for j in r.json())
        assert [_spec_arg(cmd)["prompt"] for cmd in launch.seen][0] == "first"
        assert client.get(f"{base}/generations", params={"status": "held"}).json() == []
        jobs = _wait_for(client, f"{base}/jobs", lambda js: len(js) == 2 and all(j["status"] in ("done", "failed") for j in js))
        assert {j["job"]: j["status"] for j in jobs} == {ids[0]: "done", ids[1]: "done"}
        # nothing left to release: a no-op, not an error
        assert client.post(f"{base}/held/release").json() == []


def test_held_generation_discard_and_refusals(tmp_path):
    """A discarded save is cancelled and never released; release needs ComfyUI configured and
    starts nothing without it; a save whose bin was deleted is discarded at release; a server
    restart leaves held records alone."""
    world = seeded_world(tmp_path)
    base = f"/api/projects/{world.slug}"
    r = world.client.post(f"{base}/commands/generate", json={"bin": world.image, "hold": True})
    assert r.status_code == 409 and r.json()["code"] == "comfy_unconfigured"  # a save still needs somewhere to render

    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    launch = completing_launcher((5, 5, 250))
    app = create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": launch, "poll_interval": 0.01})
    with TestClient(app) as client:
        keep = client.post(f"{base}/commands/generate", json={"bin": world.image, "hold": True}).json()["generation"]["id"]
        drop = client.post(f"{base}/commands/generate", json={"bin": world.image, "hold": True}).json()["generation"]["id"]
        orphan = client.post(f"{base}/commands/generate", json={"bin": world.video, "hold": True}).json()["generation"]["id"]
        gone = client.post(f"{base}/held/{drop}/discard")
        assert gone.status_code == 200 and gone.json()["status"] == "cancelled"
        assert client.post(f"{base}/held/{drop}/discard").status_code == 409
        assert client.post(f"{base}/held/g_nope/discard").status_code == 404
        assert client.post(f"{base}/held/{keep}/discard").status_code == 200  # keep it held for the restart below
        assert client.get(f"{base}/generations", params={"status": "held"}).json()[0]["id"] == orphan
        assert client.post(f"{base}/commands/delete_bin", json={"bin": world.video}).status_code == 200

    # a restart: reconcile attaches queued/running only, a held record is not a job
    world.registry.invalidate(world.slug)
    app2 = create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": launch, "poll_interval": 0.01})
    with TestClient(app2) as client:
        assert client.get(f"{base}/jobs").json() == []
        assert [g["id"] for g in client.get(f"{base}/generations", params={"status": "held"}).json()] == [orphan]
        assert client.post(f"{base}/held/release").json() == []  # the orphan's bin is gone: discarded, not launched
        assert launch.seen == []
        rec = client.get(f"{base}/generations/{orphan}").json()
        assert rec["status"] == "cancelled" and rec["warnings"][-1]["kind"] == "discarded"


def test_a_proposed_generate_is_refused_and_starts_nothing(tmp_path):
    """A render is judged after it lands, never approved before: proposing one is refused in
    every mode, and no job or generation comes of it."""
    from thm_core_fixtures import live_session

    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    base = f"/api/projects/{world.slug}"
    project = world.store.load(world.slug)
    session = live_session(project).id
    world.store.save(project)
    world.registry.invalidate(world.slug)

    app = create_app(world.settings, web_dist=tmp_path / "nodist", jobs_kwargs={"launch": hanging_launcher, "poll_interval": 0.01})
    with TestClient(app) as client:
        cursor = client.get(f"{base}/inbox", headers={"X-THM-Actor": "agent", "X-THM-Session": session}).json()["cursor"]
        before = len(client.get(f"{base}/generations").json())
        r = client.post(
            f"{base}/commands/generate",
            json={"bin": world.video, "params": {"seed": 10000}},
            headers={"X-THM-Actor": "agent", "X-THM-Session": session, "X-THM-Inbox-Cursor": str(cursor), "X-THM-Propose": "1"},
        )
        assert r.status_code == 409 and r.json()["code"] == "propose_render", r.text
        assert client.get(f"{base}/bins/{world.video}").json()["active_jobs"] == []
        assert len(client.get(f"{base}/generations").json()) == before


# -- the late pass ---------------------------------------------------------------


def _history_entry(spec: dict, out_path: Path, *, error=False) -> dict:
    """What ComfyUI's /history/<pid> says once the prompt has run."""
    extra = {"machine_ui_project": spec["project"], "machine_ui_label": spec["label"]}
    rel = Path(out_path).relative_to(Path(spec["comfy"]["output_root"])).as_posix()
    entry = {
        "prompt": [1, "pid-late", {}, extra, []],
        "outputs": {"9": {"images": [{"filename": Path(rel).name, "subfolder": str(Path(rel).parent), "type": "output"}]}},
        "status": {"status_str": "success", "completed": True, "messages": [
            ["execution_start", {"prompt_id": "pid-late", "timestamp": 1_700_000_000_000}],
            ["execution_success", {"prompt_id": "pid-late", "timestamp": 1_700_003_725_000}],  # 3725s later
        ]},
    }
    if error:
        entry["outputs"] = {}
        entry["status"] = {"status_str": "error", "completed": False, "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]}
    return entry


class _ComfyLater:
    """A ComfyUI that is still running the prompt for the first ``still_running`` lookups, then
    has it in history. Records what the server asked for."""

    def __init__(self, still_running: int, entry_for):
        self.still_running = still_running
        self.entry_for = entry_for
        self.asked: list[tuple] = []

    def __call__(self, api_base, slug, gen_id, prompt_id):
        self.asked.append((api_base, slug, gen_id, prompt_id))
        if len(self.asked) <= self.still_running:
            return ("queued", prompt_id or "pid-late", None)
        return ("done", prompt_id or "pid-late", self.entry_for())


def timing_out_launcher(cmd, cwd, log_path):
    """A runner that posted the prompt, then gave up on it (its execution timeout) while ComfyUI kept going."""
    _write_status(_status_arg(cmd), "failed", error="prompt pid-late timed out after 3600s of execution", outputs=[])
    data = json.loads(_status_arg(cmd).read_text(encoding="utf-8"))
    data["prompt_id"] = "pid-late"
    _status_arg(cmd).write_text(json.dumps(data), encoding="utf-8")
    Path(log_path).write_text("[PID] pid-late\n[FATAL] prompt pid-late timed out after 3600s of execution\n", encoding="utf-8")
    return SimpleNamespace(pid=4242, poll=lambda: 1, returncode=1)


def test_runner_timeout_is_landed_late_when_comfy_finishes_anyway(tmp_path):
    """The runner gave up after its execution timeout; ComfyUI finished later. The late pass
    lands the file as a take under the original generation and the job goes failed -> done."""
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    holder: dict = {}
    comfy = _ComfyLater(2, lambda: _history_entry(holder["spec"], holder["out"]))

    async def scenario():
        mgr = _manager(world, timing_out_launcher, late_lookup=comfy)
        q = world.app.state.thm.bus.subscribe(world.slug)
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        holder["spec"] = json.loads(job.paths.spec.read_text(encoding="utf-8"))
        holder["out"] = write_png(Path(holder["spec"]["output_dir"]) / f"{holder['spec']['filename_prefix']}_00001_.png", (48, 32), (9, 9, 9))
        await asyncio.wait_for(job.watcher, 5)
        failed_status = job.status
        assert job.collector is not None
        await asyncio.wait_for(job.collector, 5)
        return mgr, job, failed_status, await _drain(q), gen

    mgr, job, failed_status, events, gen = asyncio.run(scenario())
    assert failed_status == "failed"
    assert job.status == "done" and len(job.takes) == 1 and job.error is None
    assert job.prompt_id == "pid-late"
    assert comfy.asked[0][1:] == (world.slug, gen.id, "pid-late") and len(comfy.asked) == 3
    assert mgr.live_for_bin(world.slug, world.image) == []

    world.registry.invalidate(world.slug)
    project = world.registry.get(world.slug)
    take = project.media_by_id[job.takes[0]]
    assert take.origin.generation == gen.id and take.id in project.bins_by_id[world.image].media
    rec = world.store.read_generation(project, gen.id)
    assert rec.status == "done" and rec.outputs == job.takes
    assert rec.exec_seconds == 3725.0 and job.exec_seconds == 3725.0  # ComfyUI's clock, read off the history entry
    notes = [w["detail"] for w in rec.warnings if w["kind"] == "runner"]
    assert any("timed out" in n for n in notes) and any("landed late" in n for n in notes)
    statuses = [e[1]["status"] for e in events if e[0] == "job"]
    assert statuses[-2:] == ["failed", "done"]


def test_late_pass_stops_when_comfy_reports_an_error(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    holder: dict = {}
    errored = _ComfyLater(0, lambda: _history_entry(holder["spec"], tmp_path / "comfy_out" / "none.png", error=True))

    async def scenario():
        mgr = _manager(world, timing_out_launcher, late_lookup=errored)
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        holder["spec"] = json.loads(job.paths.spec.read_text(encoding="utf-8"))
        await asyncio.wait_for(job.watcher, 5)
        await asyncio.wait_for(job.collector, 5)
        return job, gen

    job, gen = asyncio.run(scenario())
    assert job.status == "failed" and job.takes == []
    rec = world.store.read_generation(world.slug, gen.id)
    assert rec.status == "failed" and "CUDA out of memory" in rec.warnings[-1]["detail"]


def test_plain_runner_failure_starts_no_late_pass(tmp_path):
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    asked = []

    async def scenario():
        mgr = _manager(world, failing_launcher, late_lookup=lambda *a: asked.append(a) or ("gone", None, None))
        gen = _queued_generation(world, world.image)
        job = mgr.launch(mgr.prepare(world.slug, world.registry.get(world.slug), gen))
        await asyncio.wait_for(job.watcher, 5)
        return job

    job = asyncio.run(scenario())
    assert job.status == "failed" and job.collector is None and asked == []


def test_reconcile_gives_a_recent_timeout_failure_a_late_pass(tmp_path):
    """A generation an earlier server process marked failed on the runner's timeout is looked
    up again at startup; the prompt id comes from the runner's log when the status file has none."""
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")
    holder: dict = {}
    comfy = _ComfyLater(0, lambda: _history_entry(holder["spec"], holder["out"]))

    async def scenario():
        first = _manager(world, timing_out_launcher)
        gen = _queued_generation(world, world.image)
        job = first.launch(first.prepare(world.slug, world.registry.get(world.slug), gen))
        await asyncio.wait_for(job.watcher, 5)
        await asyncio.wait_for(job.collector, 5)  # nothing_in_comfy: the first process found no trace
        assert world.store.read_generation(world.slug, gen.id).status == "failed"
        holder["spec"] = json.loads(job.paths.spec.read_text(encoding="utf-8"))
        holder["out"] = write_png(Path(holder["spec"]["output_dir"]) / f"{holder['spec']['filename_prefix']}_00001_.png", (48, 32), (3, 4, 5))
        job.paths.status.unlink()  # only the log names the prompt now
        # an old failure of the same shape is left alone
        old = _queued_generation(world, world.video)
        project = world.registry.get(world.slug)
        stale = old.model_copy(update={"status": "failed", "ts": "2020-01-01T00:00:00Z",
                                       "warnings": [{"kind": "runner", "handle": "", "detail": "prompt x timed out after 3600s of execution"}]})
        world.store.append_generation(project, stale)
        # so is one a late pass already landed, even when the landing added no take (a duplicate)
        done_with = _queued_generation(world, world.video)
        landed = done_with.model_copy(update={"status": "failed", "warnings": [
            {"kind": "runner", "handle": "", "detail": "prompt y timed out after 3600s of execution"},
            {"kind": "runner", "handle": "", "detail": "landed late: ComfyUI finished the prompt after THM gave up on it"},
            {"kind": "landing", "handle": "", "detail": "no output landed as a take"},
        ]})
        world.store.append_generation(project, landed)
        world.registry.invalidate(world.slug)

        mgr = _manager(world, crashing_launcher, late_lookup=comfy)
        await mgr.reconcile()
        jobs = mgr.for_project(world.slug)
        await asyncio.wait_for(asyncio.gather(*[j.collector for j in jobs if j.collector]), 5)
        return {j.id: j for j in jobs}, gen, old

    jobs, gen, old = asyncio.run(scenario())
    assert list(jobs) == [gen.id]
    assert jobs[gen.id].status == "done" and len(jobs[gen.id].takes) == 1 and jobs[gen.id].prompt_id == "pid-late"
    assert comfy.asked == [(world.settings.comfy.api_base, world.slug, gen.id, "pid-late")]
    world.registry.invalidate(world.slug)
    project = world.registry.get(world.slug)
    assert world.store.read_generation(project, gen.id).status == "done"
    assert world.store.read_generation(project, old.id).status == "failed"
    assert jobs[gen.id].takes[0] in project.bins_by_id[world.image].media


def test_attach_waits_out_a_missing_status_file_before_giving_up(tmp_path):
    """A re-attached job whose status file is mid-replace (or not yet written) is not failed on
    the first poll; it fails once the grace runs out with the file still missing."""
    world = seeded_world(tmp_path)
    world.settings.comfy.output_root = str(tmp_path / "comfy_out")

    async def scenario():
        first = _manager(world, crashing_launcher)
        gen = _queued_generation(world, world.image)
        first.prepare(world.slug, world.registry.get(world.slug), gen)
        mgr = _manager(world, crashing_launcher, no_status_grace=0.3)
        job = mgr.attach(world.slug, world.registry.get(world.slug), gen)
        await asyncio.sleep(0.1)
        early = job.status
        from datetime import datetime

        _write_status(job.paths.status, "running", task="sampling 3/20", pct=15.0, outputs=[], last_update=datetime.now().isoformat())
        await asyncio.sleep(0.4)
        after = (job.status, job.task)
        job.watcher.cancel()

        gen2 = _queued_generation(world, world.video)
        first.prepare(world.slug, world.registry.get(world.slug), gen2)
        job2 = mgr.attach(world.slug, world.registry.get(world.slug), gen2)
        await asyncio.wait_for(job2.watcher, 5)
        return early, after, job2

    early, after, job2 = asyncio.run(scenario())
    assert early == "running"
    assert after == ("running", "sampling 3/20")
    assert job2.status == "failed" and "left no status file" in job2.error and job2.collector is not None


# -- late lookup in ComfyUI ---------------------------------------------------------


def _comfy_transport(history: dict, queue: dict):
    import httpx

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/history":
            return httpx.Response(200, json=history)
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={pid: history[pid]} if pid in history else {})
        if path == "/queue":
            return httpx.Response(200, json=queue)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def test_late_prompt_state_by_id_and_by_label():
    from thm_server.comfy import late_prompt_state

    ours = {"machine_ui_project": "p", "machine_ui_label": "Video 1 g_abc"}
    older = {"prompt": [1, "old", {}, ours, []], "outputs": {}, "status": {"status_str": "error"}}
    newer = {"prompt": [2, "new", {}, ours, []], "outputs": {"9": {}}, "status": {"status_str": "success"}}
    history = {"old": older, "x": {"prompt": [3, "x", {}, {"machine_ui_project": "p", "machine_ui_label": "Video 1 g_zzz"}, []]}, "new": newer}
    queue = {"queue_running": [[0, "run", {}, {"machine_ui_project": "p", "machine_ui_label": "Image 1 g_run"}, []]],
             "queue_pending": [[1, "pend", {}, {"machine_ui_project": "q", "machine_ui_label": "Image 1 g_pend"}, []]]}
    tr = _comfy_transport(history, queue)

    assert late_prompt_state("http://c:8188", "p", "g_abc", "new", transport=tr) == ("done", "new", newer)
    assert late_prompt_state("http://c:8188", "p", "g_abc", None, transport=tr) == ("done", "new", newer)  # newest match by label
    assert late_prompt_state("http://c:8188", "p", "g_run", None, transport=tr) == ("queued", "run", None)
    assert late_prompt_state("http://c:8188", "p", "g_pend", "pend", transport=tr) == ("queued", "pend", None)
    assert late_prompt_state("http://c:8188", "p", "g_pend", None, transport=tr) == ("gone", None, None)  # other project's
    assert late_prompt_state("http://c:8188", "p", "g_nope", None, transport=tr) == ("gone", None, None)
    assert late_prompt_state("http://c:8188", "p", "g_abc", "vanished", transport=tr) == ("gone", None, None)
