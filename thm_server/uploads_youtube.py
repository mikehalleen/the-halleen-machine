"""Post uploads: one transfer per post, run on the side, the way a sequence export is.

Not a render job: no GPU, no ComfyUI queue, never in the system bar. ``upload_post`` (landed by
the user, or the user's approval of the agent's card) marks the post ``uploading``; the command
route then calls ``UploadManager.start``, which reads the post and its file under the lock,
sends the bytes to YouTube off the lock with progress as a ``post`` event, and lands the
outcome with ``record_post_upload`` so the feed and the record say how it went. A failed upload
leaves the post ``failed`` with the reason; editing it or uploading again is the retry.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from starlette.concurrency import run_in_threadpool

from thm_core import CommandContext, Project, execute
from thm_core import youtube
from thm_core.commands import RecordPostUpload, _post_record
from thm_core.ids import utc_now

from .deps import ServerState
from .publish import publish_result
from .schemas import UploadStatusOut

UPLOAD_EVENT = "post"


@dataclass
class Upload:
    post: str
    slug: str
    status: str = "running"  # running | done | failed
    progress: float = 0.0
    remote_id: str | None = None
    remote_url: str | None = None
    studio_url: str | None = None
    error: str | None = None
    started: str = field(default_factory=utc_now)
    finished: str | None = None
    task: asyncio.Task | None = None

    def out(self) -> UploadStatusOut:
        return UploadStatusOut(
            post=self.post, status=self.status, progress=self.progress if self.status == "running" else None,  # type: ignore[arg-type]
            remote_id=self.remote_id, remote_url=self.remote_url, studio_url=self.studio_url, error=self.error, started=self.started, finished=self.finished,
        )


class UploadManager:
    """In memory, per server process: the running or last upload of each post."""

    def __init__(self, st: ServerState):
        self.st = st
        self._uploads: dict[str, Upload] = {}

    def status(self, post_id: str) -> UploadStatusOut:
        up = self._uploads.get(post_id)
        return up.out() if up is not None else UploadStatusOut(post=post_id, status="idle")

    def running(self, post_id: str) -> bool:
        up = self._uploads.get(post_id)
        return up is not None and up.status == "running"

    def _publish(self, up: Upload) -> None:
        # the payload names no title and no bin, so a private project gives nothing away
        self.st.bus.publish_event(up.slug, UPLOAD_EVENT, up.out().model_dump(mode="json"))

    def start(self, slug: str, project: Project, post_id: str, ctx: CommandContext) -> UploadStatusOut:
        """Called under the caller's lock, right after ``upload_post`` landed: read what the
        transfer needs, then run it off the lock."""
        post = _post_record(project, post_id)
        media = project.media_by_id.get(post.media)
        if media is None:
            raise ValueError(f"the video of post {post_id} is gone")
        up = Upload(post=post.id, slug=slug)
        self._uploads[post.id] = up
        up.task = asyncio.create_task(self._run(up, post.model_copy(deep=True), media.path, ctx))
        self._publish(up)
        return up.out()

    async def _run(self, up: Upload, post, path: str, ctx: CommandContext) -> None:
        loop = asyncio.get_running_loop()
        last_sent = -1.0

        def on_progress(fraction: float) -> None:
            nonlocal last_sent
            up.progress = max(0.0, min(0.99, fraction))
            if up.progress - last_sent >= 0.02:
                last_sent = up.progress
                loop.call_soon_threadsafe(self._publish, up)

        try:
            creds = youtube.load_creds(self.st.youtube_env, post.channel)
            transport = self.st.youtube_transport or youtube.urllib_transport
            result = await run_in_threadpool(youtube.upload_video, creds, post, path, transport=transport, on_progress=on_progress, mime=_mime(path))
            up.remote_id, up.remote_url, up.studio_url = result.video_id, result.url, result.studio_url
            await self._land(up, ctx, RecordPostUpload(post=post.id, status="uploaded", remote_id=result.video_id, remote_url=result.url, studio_url=result.studio_url))
            up.status = "done"
        except Exception as exc:  # noqa: BLE001 - the failure is the status, and the record
            up.status = "failed"
            up.error = (str(exc) or exc.__class__.__name__)[-2000:]
            try:
                await self._land(up, ctx, RecordPostUpload(post=post.id, status="failed", error=up.error))
            except Exception:  # noqa: BLE001 - the post may be gone; the status still says failed
                pass
        finally:
            up.finished = utc_now()
            self._publish(up)

    async def _land(self, up: Upload, ctx: CommandContext, record: RecordPostUpload) -> None:
        st = self.st
        slug = up.slug
        landing = CommandContext(actor=ctx.actor, session=ctx.session, bypass_gates=True)
        async with st.registry.lock(slug):
            project = st.registry.get(slug)
            try:
                result = await run_in_threadpool(execute, st.store, project, record, landing, env=st.env)
                version = st.registry.touch(slug)
            except Exception:
                st.registry.invalidate(slug)
                raise
        publish_result(st, slug, project, messages=result.messages, change=result.change, generation=None, version=version)

    async def shutdown(self) -> None:
        for up in list(self._uploads.values()):
            if up.task is not None and not up.task.done():
                up.task.cancel()


def _mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska", ".m4v": "video/mp4"}.get(ext, "video/*")
