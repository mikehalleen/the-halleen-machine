"""Generate, the pure half: spec building, status-file reading, and landing outputs.

The server (``thm_server/jobs.py``) launches ``scripts/run_images.py --generation``
or ``run_video.py --generation`` on a spec written by :func:`build_generation_spec`
and polls the status file those scripts maintain. When the file says
``completed`` the server calls :func:`land_outputs`, which adopts each output as
a take in the bin. Nothing here starts processes, sleeps, or touches asyncio,
so it is testable with a Pillow PNG and a hand-written status file.

Status file contract (``scripts/run_images.py:_write_status``): ``pid``, ``status``
(``running`` / ``completed`` / ``failed``), ``current_task``, ``sub_task``,
``error``, ``progress_percent`` (a string like ``"5.0"`` or null), ``last_update``
(ISO local time), and ``outputs`` as ``[{path, node_id, prompt_id, label}]``. While the
prompt is in ComfyUI the runner also writes what ``scripts/comfy_progress.py`` reports:
``phase``, ``step``/``steps``, ``pass_no``/``passes``, ``queue_ahead``/``queue_ahead_ours``
(all optional; see that module's docstring), plus ``prompt_id`` once the prompt is posted and,
on the final write, ``exec_seconds``: how long ComfyUI ran the prompt, queue wait excluded.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ids import utc_now
from .media import adopt_media, ensure_thumbnails, media_type_for
from .models import Bin, Generation, GenerationStatus, Kind, Media, MediaOrigin, Project, stamp_take
from .workflow_names import resolve_curated_workflow

SCRIPT_BY_KIND = {"image": "run_images.py", "video": "run_video.py"}
RUNNER_STATUS_MAP: dict[str, GenerationStatus] = {"running": "running", "completed": "done", "failed": "failed"}
JOBS_DIR = "jobs"


# -- comfy target ---------------------------------------------------------------


@dataclass
class ComfyTarget:
    api_base: str
    output_root: str
    timeout_seconds: int = 3600  # execution time an image prompt may take; queue time excluded
    video_timeout_seconds: int | None = None  # video prompts; None = same as timeout_seconds

    def timeout_for(self, kind: Kind) -> int:
        if kind == "video" and self.video_timeout_seconds:
            return int(self.video_timeout_seconds)
        return int(self.timeout_seconds)

    def as_spec(self, kind: Kind = "image") -> dict[str, Any]:
        return {"api_base": self.api_base, "output_root": self.output_root, "timeout_seconds": self.timeout_for(kind)}


def resolve_comfy(project: Project, fallback) -> ComfyTarget:
    """Project settings when set, else the server's ``[comfyui]`` config. Refuses an empty output root."""
    api_base = (project.comfy.api_base or "").strip() or str(getattr(fallback, "api_base", "") or "").strip()
    output_root = (project.comfy.output_root or "").strip() or str(getattr(fallback, "output_root", "") or "").strip()
    timeout = int(getattr(fallback, "timeout_seconds", 3600) or 3600)
    video_timeout = int(getattr(fallback, "video_timeout_seconds", 0) or 0) or None
    if not output_root:
        raise ValueError("no ComfyUI output_root: set [comfyui] output_root in config.toml or the project's comfy settings")
    if not api_base:
        raise ValueError("no ComfyUI api_base: set [comfyui] api_base in config.toml or the project's comfy settings")
    return ComfyTarget(api_base=api_base, output_root=output_root, timeout_seconds=timeout, video_timeout_seconds=video_timeout)


# -- job files ------------------------------------------------------------------


@dataclass
class JobPaths:
    spec: Path
    status: Path
    log: Path


def job_paths(files_dir: Path, gen_id: str) -> JobPaths:
    d = Path(files_dir) / JOBS_DIR
    return JobPaths(spec=d / f"{gen_id}.json", status=d / f"{gen_id}.status.json", log=d / f"{gen_id}.log")


# -- spec -----------------------------------------------------------------------


def build_generation_spec(
    project: Project, gen: Generation, *, output_dir_id: str, label: str, comfy: ComfyTarget, workflows_dir
) -> dict[str, Any]:
    """The JSON ``scripts/generation_spec.py`` documents, from the record alone.

    ``output_dir_id`` and ``label`` are the target bin's id and name (design
    section 4: the bin id is the path element under the output root). LoRAs are
    not a spec field: they travel inside the prompt text as
    ``<lora:name:strength>`` tags and the runner lifts them out.

    Fills ``gen.params["seed"]`` when absent so the running record is reproducible;
    the runner would otherwise pick one silently. The workflow is sent as an
    absolute path because the runners resolve bare names against the repo's
    ``workflows/`` only — they know nothing of the configured directory, nor of the
    workflows_image/ and workflows_video/ folders a name now most likely lives in,
    so the name is resolved here and the answer sent whole. An unresolvable name
    still gets a path, so the runner reports a missing file rather than nothing.
    """
    if gen.params.get("seed") is None:
        gen.params["seed"] = random.randint(0, 2**31 - 1)
    workflow = Path(str(gen.workflow))
    if not workflow.is_absolute():
        resolved = resolve_curated_workflow(gen.workflow, Path(workflows_dir))
        workflow = resolved if resolved is not None else Path(workflows_dir) / gen.workflow
    output_dir = Path(comfy.output_root) / project.slug / output_dir_id
    return {
        "id": gen.id,
        "project": project.slug,
        "workflow": workflow.resolve().as_posix(),
        "prompt": gen.prompt_submitted,
        "negative": gen.negative_submitted,
        "prelude": list(gen.prelude),
        "paths_by_slot": dict(gen.paths_by_slot),
        "params": dict(gen.params),
        "output_dir": output_dir.as_posix(),
        "filename_prefix": output_dir_id,
        "label": label,
        "comfy": comfy.as_spec(gen.kind),
    }


def runner_script(kind: Kind, scripts_dir: Path) -> Path:
    return Path(scripts_dir) / SCRIPT_BY_KIND[kind]


# -- status file ----------------------------------------------------------------


@dataclass
class RunnerStatus:
    status: str
    task: str | None = None
    error: str | None = None
    progress: float | None = None
    outputs: list[dict] = field(default_factory=list)
    pid: int | None = None
    last_update: str | None = None
    live: LiveProgress | None = None
    prompt_id: str | None = None  # the ComfyUI prompt, once posted; how a late pickup finds the entry
    exec_seconds: float | None = None  # ComfyUI's execution time for the prompt, queue wait excluded


@dataclass(frozen=True)
class LiveProgress:
    """What ComfyUI is doing with the prompt right now (``scripts/comfy_progress.py``)."""

    phase: str | None = None
    step: int | None = None
    steps: int | None = None
    pass_no: int | None = None
    passes: int | None = None
    queue_ahead: int | None = None
    queue_ahead_ours: int | None = None


LIVE_INT_FIELDS = ("step", "steps", "pass_no", "passes", "queue_ahead", "queue_ahead_ours")


def _live_progress(data: dict) -> LiveProgress | None:
    if not any(k in data for k in ("phase",) + LIVE_INT_FIELDS):
        return None
    ints: dict[str, int | None] = {}
    for k in LIVE_INT_FIELDS:
        v = data.get(k)
        try:
            ints[k] = int(v) if v is not None else None
        except (TypeError, ValueError):
            ints[k] = None
    phase = data.get("phase")
    return LiveProgress(phase=str(phase) if phase else None, **ints)


def read_status(path) -> RunnerStatus | None:
    """The runner's status file, or None when it is missing, half-written, or locked mid-replace."""
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("status"):
        return None
    progress: float | None = None
    pct = data.get("progress_percent")
    if pct not in (None, ""):
        try:
            progress = float(pct)
        except (TypeError, ValueError):
            progress = None
    pid = data.get("pid")
    try:
        pid = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid = None
    outputs = [o for o in (data.get("outputs") or []) if isinstance(o, dict) and o.get("path")]
    exec_seconds: float | None = None
    if data.get("exec_seconds") not in (None, ""):
        try:
            exec_seconds = float(data["exec_seconds"])
        except (TypeError, ValueError):
            exec_seconds = None
    return RunnerStatus(
        status=str(data["status"]),
        task=data.get("current_task") or None,
        error=data.get("error") or None,
        progress=progress,
        outputs=outputs,
        pid=pid,
        last_update=data.get("last_update") or None,
        live=_live_progress(data),
        prompt_id=str(data["prompt_id"]) if data.get("prompt_id") else None,
        exec_seconds=exec_seconds,
    )


def map_status(runner_status: str) -> GenerationStatus:
    return RUNNER_STATUS_MAP.get(str(runner_status), "running")


# -- landing --------------------------------------------------------------------


def filter_outputs(kind: Kind, outputs: list[dict]) -> tuple[list[str], list[str]]:
    """Output paths whose media type matches the generation's kind. Keeps everything, with a warning, if none match."""
    paths = [str(o["path"]) for o in outputs if isinstance(o, dict) and o.get("path")]
    matching = [p for p in paths if media_type_for(p) == kind]
    if matching or not paths:
        return matching, []
    return paths, [f"no {kind} output among {len(paths)} reported file(s); keeping all"]


def land_outputs(project: Project, bin_: Bin, gen: Generation, outputs: list[dict], *, cache_dir, now: str | None = None) -> tuple[list[Media], list[str]]:
    """Adopt each output as a take in ``bin_``, select the newest, and close the generation record.

    Returns the newly landed media and human-readable warnings. Byte-identical
    files reuse an existing media record (``adopt_media`` dedupes by hash); a
    record already in the bin's gallery is skipped rather than listed twice.
    Thumbnails are best-effort.
    """
    paths, warnings = filter_outputs(gen.kind, outputs)
    # a bin with no winner yet takes its first take as the winner; after that a take is chosen
    had_winner = bool(bin_.selected and bin_.selected in bin_.media)
    landed: list[Media] = []
    ts = now or utc_now()
    for path in paths:
        try:
            media = adopt_media(project, path, origin=MediaOrigin(type="generation", generation=gen.id, bin=bin_.id, exec_seconds=gen.exec_seconds), now=ts)
        except (FileNotFoundError, ValueError, OSError) as exc:
            warnings.append(f"could not adopt {path}: {exc}")
            continue
        if media.id in bin_.media:
            label = f"take {media.take}" if media.take else "media"
            warnings.append(f"{Path(path).name} is byte-identical to {label} already in {bin_.name} ({media.id}); not added again")
            continue
        bin_.media.append(media.id)
        stamp_take(bin_, media)
        # new from the moment it is in the gallery: the thumbnails below take a while, and a
        # read in between must never see the take without its flag
        if media.id not in bin_.unseen:
            bin_.unseen = [*bin_.unseen, media.id]
        landed.append(media)
        try:
            ensure_thumbnails(media, cache_dir)
        except Exception as exc:  # thumbnails are a cache; never fail a landing over them
            warnings.append(f"thumbnails for {media.id}: {exc}")
    if landed:
        # a take lands as new, not as the winner: once a bin has one, its selected take changes
        # only when someone chooses it (the user's second tap, a card approval, the agent's
        # set_bin_field). The exception is the bin's first: with nothing chosen yet, it leads.
        if not had_winner:
            bin_.selected = landed[-1].id
        bin_.updated = ts
        gen.status = "done"
    else:
        gen.status = "failed"
        warnings.append("no output landed as a take")
    gen.outputs = [m.id for m in landed]
    if warnings:
        gen.warnings = list(gen.warnings) + [{"kind": "landing", "handle": "", "detail": w} for w in warnings]
    return landed, warnings


def log_tail(path, lines: int = 20) -> str:
    """The last ``lines`` of a runner log, for a failure detail. Empty when unreadable."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = [ln for ln in text.splitlines() if ln.strip()][-lines:]
    return "\n".join(tail)
