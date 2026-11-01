"""Workflow listing with capability summary, cached by file mtime."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from thm_core.enhancer import system_prompt_for
from thm_core.workflow_names import resolve_curated_workflow, workflow_kind_dirs

from ..deps import ServerState, state
from ..derive import workflow_info
from ..errors import ApiError
from ..schemas import WorkflowInfo

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
