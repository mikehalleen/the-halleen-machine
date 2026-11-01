"""The LoRA files ComfyUI can load, for the picker behind a shot's or the creator's LoRA row."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from ..deps import state
from ..schemas import LoraFile

router = APIRouter(prefix="/api/loras", tags=["loras"])

LORA_SUFFIXES = (".safetensors", ".pt", ".ckpt")


def list_lora_files(loras_dir: Path | None) -> list[LoraFile]:
    """Every LoRA under ``[paths] loras``, named the way ComfyUI's loader wants it: the path
    relative to that folder with forward slashes. Empty when the folder is unset or missing."""
    if loras_dir is None or not Path(loras_dir).is_dir():
        return []
    root = Path(loras_dir)
    out: list[LoraFile] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in LORA_SUFFIXES or path.name.startswith("."):
            continue
        out.append(LoraFile(name=path.relative_to(root).as_posix(), size=path.stat().st_size))
    out.sort(key=lambda f: f.name.lower())
    return out


@router.get("", response_model=list[LoraFile])
async def list_loras(request: Request):
    return await run_in_threadpool(list_lora_files, state(request).settings.loras_dir)
