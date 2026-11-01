"""Builders shared by the thm_server tests. Not a test module."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from starlette.testclient import TestClient

from thm_core import BackupSettings, CoreSettings, Env, Generation, MediaOrigin, adopt_media, execute, ids
from thm_core.commands import AddMediaToBin, BindReference, CreateBin, SetBinField
from thm_core.ids import utc_now
from thm_core_fixtures import USER, image_workflow, make_store, video_workflow, workflows_dir
from thm_server import create_app


def write_png(path: Path, size=(640, 400), color=(120, 80, 200), mode="RGB") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path)
    return path


def make_tiny_mp4(path: Path) -> Path | None:
    """A one second 64x64 clip via ffmpeg, or None when ffmpeg/ffprobe are missing."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=8", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=60,
    )
    return path


def make_tiny_audio(path: Path, *, seconds: float = 1.0, hz: int = 440) -> Path | None:
    """A short mono sine at 44.1 kHz via ffmpeg (``.wav`` or ``.mp3`` by the suffix), or None
    when ffmpeg/ffprobe are missing."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}", "-ac", "1", "-ar", "44100", str(path)],
        check=True, capture_output=True, timeout=60,
    )
    return path


def seeded_world(tmp_path: Path) -> SimpleNamespace:
    """A saved project with three bins: Mara (two uploads), Video 1 (vid.json, 5s, both uploads
    bound), and Image 1 (img.json, one take, selected)."""
    store = make_store(tmp_path)
    wdir = workflows_dir(tmp_path, {"img.json": image_workflow(2), "vid.json": video_workflow()})
    env = Env(workflows_dir=wdir, isfile=os.path.isfile)
    project = store.new_project("Rail Dawn Promo")
    store.save(project)

    media_dir = tmp_path / "media"
    a = write_png(media_dir / "a.png", (640, 400), (200, 60, 60))
    b = write_png(media_dir / "b.png", (400, 640), (60, 200, 60))
    c = write_png(media_dir / "c.png", (800, 600), (60, 60, 200))

    create_mara = CreateBin(bin_name="Mara", workflow="img.json")
    execute(store, project, create_mara, USER, env=env)
    mara = create_mara._created
    m1 = adopt_media(project, a, origin=MediaOrigin(type="upload", bin=mara))
    execute(store, project, AddMediaToBin(bin=mara, media=m1.id), USER, env=env)
    m2 = adopt_media(project, b, origin=MediaOrigin(type="upload", bin=mara))
    execute(store, project, AddMediaToBin(bin=mara, media=m2.id), USER, env=env)

    create_video = CreateBin(bin_name="Video 1", workflow="vid.json", length_s=5)
    execute(store, project, create_video, USER, env=env)
    video = create_video._created
    create_image = CreateBin(bin_name="Image 1", workflow="img.json")
    execute(store, project, create_image, USER, env=env)
    image = create_image._created
    execute(store, project, SetBinField(bin=video, field="prompt", value="@mara.png at the rail"), USER, env=env)
    execute(store, project, BindReference(bin=video, media=m1.id), USER, env=env)
    execute(store, project, BindReference(bin=video, media=m2.id), USER, env=env)

    gen = Generation(id=ids.new_id(ids.GENERATION), bin=image, kind="image", ts=utc_now(), workflow="img.json", status="done")
    take = adopt_media(project, c, origin=MediaOrigin(type="generation", generation=gen.id, bin=image))
    gen.outputs = [take.id]
    store.append_generation(project, gen)
    image_bin = project.bins_by_id[image]
    image_bin.media.append(take.id)
    image_bin.counters["take"] = 1
    take.take = 1
    store.save(project)
    execute(store, project, SetBinField(bin=image, field="selected", value=take.id), USER, env=env)

    settings = CoreSettings(projects_dir=tmp_path / "projects", workflows_dir=wdir, backups=BackupSettings(throttle_seconds=0))
    app = create_app(settings, web_dist=tmp_path / "nodist")
    client = TestClient(app)
    return SimpleNamespace(
        store=store, project=project, slug=project.slug, env=env, settings=settings, app=app, client=client,
        mara=mara, m1=m1.id, m2=m2.id, video=video, image=image, take=take.id, media_dir=media_dir, wdir=wdir,
        registry=app.state.thm.registry,
    )
