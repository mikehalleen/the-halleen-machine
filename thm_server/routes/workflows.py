"""Workflow listing with capability summary, cached by file mtime; importing one from ComfyUI."""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from thm_core import workflow_import as wi
from thm_core.enhancer import system_prompt_for
from thm_core.workflow_names import resolve_curated_workflow, workflow_kind_dirs

from .. import comfy
from ..deps import ServerState, state
from ..derive import workflow_info
from ..errors import ApiError
from ..schemas import ComfyRun, WorkflowImportIn, WorkflowImportOut, WorkflowInfo

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


def list_workflow_infos(st: ServerState) -> list[WorkflowInfo]:
    """Everything in workflows_image/ and workflows_video/, kind taken from the folder it is in.

    The flat legacy workflows/ folder is deliberately not listed: it holds whatever has ever been
    exported, in whatever format, and the two kind folders are the curated set. Names there still
    *resolve* for projects that reference them — see ``workflow_search_dirs``.
    """
    wdir = Path(st.settings.workflows_dir)
    out: list[WorkflowInfo] = []
    for kind_dir in workflow_kind_dirs(wdir).values():
        if not kind_dir.is_dir():
            continue
        for path in sorted(kind_dir.glob("*.json")):
            if path.name.startswith("_") or not path.is_file():
                continue
            mtime = path.stat().st_mtime_ns
            cached = st.workflows_cache.get(str(path))
            if cached is not None and cached[0] == mtime:
                out.append(cached[1])
                continue
            # by full path, so a name that exists in both folders is read from this one
            info = workflow_info(str(path), wdir)
            if info is None:
                continue
            st.workflows_cache[str(path)] = (mtime, info)
            out.append(info)
    out.sort(key=lambda w: w.name.lower())
    return out


@router.get("", response_model=list[WorkflowInfo])
async def list_workflows(request: Request):
    return await run_in_threadpool(list_workflow_infos, state(request))


def samples_dir(workflows_dir) -> Path:
    """Where the Comfy-openable copies live: ``samples/workflows_openincomfy`` beside the
    workflows folders, so a test install keeps its own."""
    return Path(workflows_dir).parent / "samples" / "workflows_openincomfy"


def _runs(st: ServerState, limit: int) -> list[tuple[str, dict]]:
    try:
        return comfy.recent_runs(st.settings.comfy.api_base, max_items=max(limit * 4, 20), transport=st.comfy_transport)
    except (httpx.HTTPError, ValueError) as exc:
        raise ApiError(502, "comfy_unreachable", f"ComfyUI at {st.settings.comfy.api_base} did not answer: {exc}") from None


def list_comfy_runs(st: ServerState, limit: int) -> list[ComfyRun]:
    out = []
    for pid, entry in _runs(st, limit):
        summary = wi.run_summary(pid, entry)
        if summary is not None:
            out.append(ComfyRun(**summary))
        if len(out) >= limit:
            break
    return out


@router.get("/comfy-runs", response_model=list[ComfyRun])
async def comfy_runs(request: Request, limit: int = 10):
    """The user's recent ComfyUI runs, newest first: what a workflow import can start from."""
    return await run_in_threadpool(list_comfy_runs, state(request), max(1, min(int(limit), 50)))


def _harvest(st: ServerState, body: WorkflowImportIn) -> wi.Harvest:
    if body.png_b64:
        try:
            return wi.harvest_png(base64.b64decode(body.png_b64, validate=False))
        except binascii.Error:
            raise ApiError(422, "bad_png", "png_b64 is not base64") from None
    runs = _runs(st, 50)
    if body.run:
        hit = [(pid, e) for pid, e in runs if pid == body.run] or [(pid, e) for pid, e in runs if pid.startswith(body.run)]
        if not hit:
            entry = comfy.run_entry(st.settings.comfy.api_base, body.run, transport=st.comfy_transport)
            hit = [(body.run, entry)] if entry else []
        if len(hit) > 1:
            raise ApiError(409, "ambiguous_run", f"{len(hit)} runs start with {body.run}; give more of the id")
        if not hit:
            raise ApiError(404, "unknown_run", f"no ComfyUI run {body.run} in history")
        return wi.harvest_history_entry(*hit[0])
    for pid, entry in runs:
        if wi.run_summary(pid, entry) is not None:
            return wi.harvest_history_entry(pid, entry)
    raise ApiError(404, "no_runs", "ComfyUI has no run of yours in its history: run the workflow once in ComfyUI first")


def import_workflow(st: ServerState, body: WorkflowImportIn) -> WorkflowImportOut:
    wdir = Path(st.settings.workflows_dir)
    try:
        harvest = _harvest(st, body)
        result = wi.build_import(
            harvest, name=body.name, kind=body.kind, tags=body.tags, untag=body.untag, system_prompt=body.system_prompt,
            keep_builtin_enhancer=body.keep_builtin_enhancer, lora_marker=body.lora_marker,
        )
    except wi.ImportRefused as exc:
        raise ApiError(422, exc.code, exc.detail) from None
    api_path = workflow_kind_dirs(wdir)[result.kind] / f"{result.name}.json"
    ui_path = samples_dir(wdir) / f"THM-{result.name}.json"
    taken = resolve_curated_workflow(f"{result.name}.json", wdir)
    if not body.replace and (taken or (result.ui is not None and ui_path.exists())):
        raise ApiError(409, "workflow_exists", f"{result.name} already exists ({taken or ui_path}); import with replace to overwrite it")
    files = {"api": str(api_path)}
    if result.ui is not None:
        files["ui"] = str(ui_path)
    if not body.dry_run:
        if taken and taken != api_path.resolve():
            taken.unlink()  # replacing a workflow that moved kind
        api_path.parent.mkdir(parents=True, exist_ok=True)
        api_path.write_text(json.dumps(result.api, indent=2, ensure_ascii=False), encoding="utf-8")
        if result.ui is not None:
            ui_path.parent.mkdir(parents=True, exist_ok=True)
            ui_path.write_text(json.dumps(result.ui, indent=2, ensure_ascii=False), encoding="utf-8")
    return WorkflowImportOut(source=harvest.source, dry_run=body.dry_run, files=files, **result.report())


@router.post("/import", response_model=WorkflowImportOut)
async def import_from_comfy(body: WorkflowImportIn, request: Request):
    """Tag and save a workflow the user ran in ComfyUI: the API graph into the kind folder, the
    UI graph into the samples folder for the user to open in ComfyUI. Nothing is written to ComfyUI."""
    return await run_in_threadpool(import_workflow, state(request), body)


def _with_guide(name: str, workflows_dir) -> WorkflowInfo | None:
    """One workflow with its prompt guide: the same text the Enhance button hands the rewriter,
    so an agent writing its own prompt reads the rules it will be judged by."""
    info = workflow_info(name, workflows_dir)
    if info is None:
        return None
    path = resolve_curated_workflow(name, Path(workflows_dir))
    text, source = system_prompt_for(path, info.kind)
    return info.model_copy(update={"guide": text, "guide_source": source})


@router.get("/{name}", response_model=WorkflowInfo)
async def get_workflow(name: str, request: Request):
    st = state(request)
    info = await run_in_threadpool(_with_guide, name, st.settings.workflows_dir)
    if info is None:
        raise ApiError(404, "unknown_workflow", f"no workflow {name}")
    return info
