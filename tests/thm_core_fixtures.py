"""Builders shared by the thm_core tests. Not a test module."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from thm_core.workflow_names import workflow_kind_dirs
from thm_core import (
    AgentSession,
    BackupSettings,
    Bin,
    CommandContext,
    Env,
    Media,
    MediaOrigin,
    Project,
    ProjectStore,
    Reference,
    assign_handle,
    media_ext,
)

USER = CommandContext(actor="user", now="2026-09-06T10:00:00Z")
AGENT = CommandContext(actor="agent", now="2026-09-06T10:00:00Z")

# names that land in workflows_video/ so their kind reads as video
VIDEO_WORKFLOWS = ("vid.json", "vidref.json")


def node(title, class_type, inputs):
    return {"_meta": {"title": title}, "class_type": class_type, "inputs": dict(inputs)}


def image_workflow(n_slots: int = 2) -> dict:
    """A graph with ``n_slots`` bare THM-ImageReference loaders and a prompt node."""
    wf = {"1": node("THM-Prompt", "CLIPTextEncode", {"text": ""})}
    for i in range(n_slots):
        wf[str(10 + i)] = node("THM-ImageReference", "LoadImage", {"image": ""})
    return wf


def video_workflow(start: bool = True, end: bool = True) -> dict:
    # a canvas THM sets (width/height), as a real video workflow has: quarter size halves it
    wf = {
        "1": node("THM-Prompt", "CLIPTextEncode", {"text": ""}),
        "30": node("THM-Width", "PrimitiveInt", {"value": 720}),
        "31": node("THM-Height", "PrimitiveInt", {"value": 1280}),
    }
    if start:
        wf["20"] = node("THM-StartFrame", "LoadImage", {"image": ""})
    if end:
        wf["21"] = node("THM-EndFrame", "LoadImage", {"image": ""})
    return wf


def ref_stack_workflow(n_slots: int = 4) -> dict:
    """Bare THM-ImageReference loaders on a linear ReferenceLatent stack (Klein style).

    Loaders are ``"10"``..; derivation reads this as ``image{n}`` notation.
    """
    wf = {
        "1": node("THM-Prompt", "CLIPTextEncode", {"text": ""}),
        "20": node("Zero", "ConditioningZeroOut", {"conditioning": ["99", 0]}),
        "99": node("VAE", "VAELoader", {"vae_name": "x"}),
    }
    prev = "20"
    for i in range(n_slots):
        load, enc, ref = str(10 + i), str(21 + i), str(30 + i)
        wf[load] = node("THM-ImageReference", "LoadImage", {"image": ""})
        wf[enc] = node(f"Enc{i + 1}", "VAEEncode", {"pixels": [load, 0], "vae": ["99", 0]})
        wf[ref] = node(f"Ref{i + 1}", "ReferenceLatent", {"conditioning": [prev, 0], "latent": [enc, 0]})
        prev = ref
    return wf


def video_ref_workflow(n_video: int = 2, start: bool = True, end: bool = True) -> dict:
    """A video workflow with start/end anchors plus ``n_video`` THM-VideoReference loaders (``"40"``..)."""
    wf = video_workflow(start, end)
    for i in range(n_video):
        wf[str(40 + i)] = node("THM-VideoReference", "LoadVideo", {"file": ""})
    return wf


# An untagged partner node class, like workflows/THM-video_minimax_h3_r2v.json.
PARTNER_WORKFLOW = {
    "1": node("THM-Prompt", "PrimitiveStringMultiline", {"value": ""}),
    "20": node("THM-StartFrame", "LoadImage", {"image": ""}),
    "21": node("THM-EndFrame", "LoadImage", {"image": ""}),
    "50": node("MiniMax", "MiniMaxH3ReferenceToVideo", {"prompt": ""}),
}


IMAGE_SIDECAR = '[tokens]\nimage = "<Picture {n}>"\n'
TOKEN_SIDECAR = '[tokens]\nimage = "<Picture {n}>"\nvideo = "<Video {n}>"\n\n[limits]\nimage_slots = 9\nvideo_slots = 3\n'


def workflows_dir(tmp_path: Path, workflows: dict[str, dict], sidecars: dict[str, str] | None = None, *, video: Sequence[str] = VIDEO_WORKFLOWS) -> Path:
    """The workflows anchor dir. Files go into its siblings workflows_image/ and
    workflows_video/, because kind is the folder a workflow sits in and nothing resolves out of
    the flat folder any more. Everything is an image workflow unless named in ``video``; a
    sidecar lands beside the workflow whose stem it shares."""
    d = tmp_path / "workflows"
    d.mkdir(exist_ok=True)
    kinds = workflow_kind_dirs(d)
    for kind_dir in kinds.values():
        kind_dir.mkdir(parents=True, exist_ok=True)
    video_names = set(video)

    def target(name: str) -> Path:
        return kinds["video"] if name in video_names else kinds["image"]

    for name, wf in workflows.items():
        (target(name) / name).write_text(json.dumps(wf), encoding="utf-8")
    for name, text in (sidecars or {}).items():
        stem = name.split(".", 1)[0]
        owner = next((w for w in workflows if Path(w).stem == stem), name)
        (target(owner) / name).write_text(text, encoding="utf-8")
    return d


def sidecar_home(wdir: Path, *, video: bool = False) -> Path:
    """Where a sidecar written by hand after ``workflows_dir`` has to land: beside its workflow,
    which means inside the kind folder, not the flat anchor dir."""
    return workflow_kind_dirs(wdir)["video" if video else "image"]


def make_env(tmp_path: Path, workflows: dict[str, dict] | None = None, sidecars: dict[str, str] | None = None, *, isfile=lambda p: True) -> Env:
    wfs = workflows if workflows is not None else {"img.json": image_workflow(2), "vid.json": video_workflow()}
    return Env(workflows_dir=workflows_dir(tmp_path, wfs, sidecars), isfile=isfile)


def make_store(tmp_path: Path, *, retention: int = 3, throttle: float = 0.0, clock=None) -> ProjectStore:
    kwargs = {"backups": BackupSettings(retention_count=retention, throttle_seconds=throttle)}
    if clock is not None:
        kwargs["clock"] = clock
    return ProjectStore(tmp_path / "projects", **kwargs)


def make_project(store: ProjectStore | None = None, name: str = "Rail Dawn Promo") -> Project:
    if store is None:
        return Project(id="p_test", name=name, slug="rail-dawn-promo")
    project = store.new_project(name)
    store.save(project)
    return project


_n = {"bin": 0, "media": 0, "ref": 0}


def add_bin(project: Project, name: str | None = None, workflow: str = "img.json", *, length_s: float | None = None) -> str:
    """A bin straight into the project, bypassing the command layer. ``vid.json`` and friends
    are video workflows (see ``VIDEO_WORKFLOWS``), so a length rides along by default."""
    _n["bin"] += 1
    if length_s is None and workflow in VIDEO_WORKFLOWS:
        length_s = 5.0
    bin_ = Bin(id=f"b_{_n['bin']:03d}", name=name or f"bin-{_n['bin']}", workflow=workflow, length_s=length_s, created="2026-09-06T09:00:00Z")
    project.bins_by_id[bin_.id] = bin_
    return bin_.id


def add_media(project: Project, bin_id: str | None, *, type: str = "image", path: str = "", origin: MediaOrigin | dict | None = None) -> str:
    """An upload into ``bin_id`` (or an orphan with the given origin when ``bin_id`` is None)."""
    _n["media"] += 1
    mid = f"m_{_n['media']:03d}"
    if origin is None:
        origin = MediaOrigin(type="upload", bin=bin_id)
    project.media_by_id[mid] = Media(id=mid, type=type, path=path or f"C:/fake/{mid}.{'mp4' if type == 'video' else 'png'}", origin=origin)
    if bin_id:
        project.bins_by_id[bin_id].media.append(mid)
    return mid


def add_take(project: Project, bin_id: str, *, type: str = "image", path: str = "") -> str:
    """A generated media landed in ``bin_id``, numbered like the runner numbers it."""
    bin_ = project.bins_by_id[bin_id]
    mid = add_media(project, bin_id, type=type, path=path, origin=MediaOrigin(type="generation", generation="g_x", bin=bin_id))
    bin_.counters["take"] = int(bin_.counters.get("take", 0)) + 1
    project.media_by_id[mid].take = bin_.counters["take"]
    return mid


def bin_of(project: Project, media_id: str) -> Bin:
    media = project.media_by_id[media_id]
    if media.origin.bin and media.origin.bin in project.bins_by_id:
        return project.bins_by_id[media.origin.bin]
    return next(b for b in project.bins_by_id.values() if media_id in b.media)


def bind(project: Project, bin_id: str, media_id: str) -> Reference:
    """Bind directly through the handle rule, bypassing the command layer."""
    bin_ = project.bins_by_id[bin_id]
    ref_bin = bin_of(project, media_id)
    _n["ref"] += 1
    ref = Reference(id=f"r_{_n['ref']:03d}", media=media_id, bin=ref_bin.id, handle=assign_handle(bin_, ref_bin, media_ext(project.media_by_id[media_id].path)))
    bin_.references.append(ref)
    return ref


def waiting_session(project: Project, brief: str = "make a promo") -> AgentSession:
    project.brief = brief
    # image_review: the tests that grew up on these fixtures describe the card-per-render
    # behaviour; auto mode has its own tests
    project.agent_session = AgentSession(id="as_test", status="waiting", mode="image_review", started="2026-09-06T09:00:00Z")
    return project.agent_session


def live_session(project: Project) -> AgentSession:
    sess = waiting_session(project)
    sess.status = "live"
    sess.harness = "claude-code"
    sess.connected = "2026-09-06T09:01:00Z"
    return sess
