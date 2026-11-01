"""Sequence exports: one ffmpeg process per sequence, run on the side.

Not a render job: an export spends no GPU time and does not queue behind ComfyUI, so it is kept
apart from ``JobManager`` and never reaches the system bar. Its progress goes out as an
``export`` event on the project's stream and is readable at ``GET .../sequences/{id}/export``;
the sequence screen shows it. The finished file lands in the sequence's output bin as the next
take, through ``add_media_to_bin`` like an upload does, so it is ordinary media from then on.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from thm_core import CommandContext, MediaOrigin, Project, adopt_media, ensure_thumbnails, execute, thumb_cache_dir
from thm_core.commands import AddMediaToBin, _fmt_clock, _sequence, ensure_output_bin
from thm_core.ids import utc_now
from thm_core.models import Sequence, stamp_take
from thm_core.slug import slugify
from thm_core.stitch import StitchPlan, build_args, build_plan, parse_progress_line

from .deps import ServerState
from .imports import fresh_import_path
from .publish import publish_result
from .schemas import ExportStatusOut

EXPORT_EVENT = "export"
WORK_DIR = "cache/exports"


@dataclass
class Export:
    sequence: str
    slug: str
    status: str = "running"  # running | done | failed
    progress: float = 0.0
    media: str | None = None
    take: int | None = None
    error: str | None = None
    started: str = field(default_factory=utc_now)
    finished: str | None = None
    task: asyncio.Task | None = None

    def out(self) -> ExportStatusOut:
        return ExportStatusOut(
            sequence=self.sequence, status=self.status, progress=self.progress if self.status == "running" else None,  # type: ignore[arg-type]
            media=self.media, take=self.take, error=self.error, started=self.started, finished=self.finished,
        )


class ExportManager:
    """In memory, per server process: the running or last export of each sequence."""

    def __init__(self, st: ServerState):
        self.st = st
        self._exports: dict[str, Export] = {}

    def status(self, seq_id: str) -> ExportStatusOut:
        ex = self._exports.get(seq_id)
        return ex.out() if ex is not None else ExportStatusOut(sequence=seq_id, status="idle")

    def running(self, seq_id: str) -> bool:
        ex = self._exports.get(seq_id)
        return ex is not None and ex.status == "running"

    def _publish(self, ex: Export) -> None:
        # the payload names no bin and no media title, so a private project gives nothing away
        self.st.bus.publish_event(ex.slug, EXPORT_EVENT, ex.out().model_dump(mode="json"))

    def start(self, slug: str, project: Project, seq: Sequence, ctx: CommandContext) -> ExportStatusOut:
        """Plan under the caller's lock (it reads the project), then render off the lock."""
        files_dir = self.st.store.files_dir(project)
        tag = datetime.now().strftime("%Y%m%d-%H%M%S")
        plan = build_plan(project, seq, files_dir / WORK_DIR, now_tag=tag)
        if not plan.items:
            raise ValueError("nothing to export: every item was skipped")
        dest = fresh_import_path(files_dir, f"{slugify(seq.name) or 'sequence'}_{plan.width}x{plan.height}_{plan.fps:g}fps.mp4")
        ex = Export(sequence=seq.id, slug=slug)
        self._exports[seq.id] = ex
        ex.task = asyncio.create_task(self._run(ex, plan, dest, seq.id, ctx, len(seq.items)))
        self._publish(ex)
        return ex.out()

    async def _run(self, ex: Export, plan: StitchPlan, dest: Path, seq_id: str, ctx: CommandContext, n_items: int) -> None:
        try:
            await self._ffmpeg(ex, plan, dest)
            await self._land(ex, dest, seq_id, ctx, plan, n_items)
            ex.status = "done"
        except Exception as exc:  # noqa: BLE001 - the failure is the status
            ex.status = "failed"
            ex.error = str(exc)[-2000:] or exc.__class__.__name__
            dest.unlink(missing_ok=True)
        finally:
            ex.finished = utc_now()
            self._publish(ex)

    async def _ffmpeg(self, ex: Export, plan: StitchPlan, dest: Path) -> None:
        import shutil

        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("ffmpeg not found on PATH")
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = build_args(plan, dest, exe=exe)
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        total = plan.total_s or 1.0
        assert proc.stdout is not None
        last_sent = -1.0
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            t = parse_progress_line(line.decode("utf-8", "replace"))
            if t is None:
                continue
            ex.progress = max(0.0, min(0.99, t / total))
            if ex.progress - last_sent >= 0.02:
                last_sent = ex.progress
                self._publish(ex)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError((err or b"").decode("utf-8", "replace").strip()[-2000:] or f"ffmpeg exited {proc.returncode}")
        if not dest.is_file() or dest.stat().st_size == 0:
            raise RuntimeError("ffmpeg produced no output")

    async def _land(self, ex: Export, dest: Path, seq_id: str, ctx: CommandContext, plan: StitchPlan, n_items: int) -> None:
        """The file becomes the output bin's next take: adopt, stamp, add, publish."""
        st = self.st
        slug = ex.slug
        async with st.registry.lock(slug):
            project = st.registry.get(slug)
            seq = _sequence(project, seq_id)
            # the output bin is made here, at the first export, unless the user chose one
            out = ensure_output_bin(project, ctx, seq)
            note = f"{n_items} item{'s' if n_items != 1 else ''} · {_fmt_clock(plan.total_s)}" + (f" · {len(plan.skipped)} skipped" if plan.skipped else "")
            if plan.layers:
                note += " · " + ", ".join(f"{lay.id} from {lay.source_in_s:g}s" for lay in plan.layers)
            used = [{"id": lay.id, "media": lay.media, "source_in_s": float(lay.source_in_s), "start_frame": int(lay.start_frame)} for lay in seq.layers] or None
            try:
                media = adopt_media(project, dest, origin=MediaOrigin(type="export", bin=out.id, source=seq.id, note=note, layers=used))
                if Path(media.path) != dest.resolve():
                    dest.unlink(missing_ok=True)  # byte-identical to an export already there
                if media.take is None and media.origin.type == "export":
                    stamp_take(out, media)
                # new until the user watches it, like a landed take: the past-exports list says so
                if media.id not in out.unseen:
                    out.unseen = [*out.unseen, media.id]
                try:
                    await run_in_threadpool(ensure_thumbnails, media, thumb_cache_dir(st.store, project))
                except Exception:
                    pass
                if media.id in out.media:
                    await run_in_threadpool(st.store.save, project)
                    version = st.registry.touch(slug)
                    messages, change = [], None
                else:
                    result = await run_in_threadpool(execute, st.store, project, AddMediaToBin(bin=out.id, media=media.id, reason=f"exported {seq.name}"), ctx, env=st.env)
                    change, messages = result.change, result.messages
                    version = st.registry.touch(slug)
            except Exception:
                st.registry.invalidate(slug)
                raise
            ex.media, ex.take = media.id, media.take
        publish_result(st, slug, project, messages=messages, change=change, generation=None, version=version)

    async def shutdown(self) -> None:
        for ex in list(self._exports.values()):
            if ex.task is not None and not ex.task.done():
                ex.task.cancel()
