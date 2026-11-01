"""The LoRA files ComfyUI can load, for the picker behind a shot's or the creator's LoRA row."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from thm_core.loras import list_lora_names

from ..deps import state
from ..schemas import LoraFile

router = APIRouter(prefix="/api/loras", tags=["loras"])


def list_lora_files(loras_dir: Path | None) -> list[LoraFile]:
    """Every LoRA under ``[paths] loras`` (see ``list_lora_names``), with its size."""
    return [LoraFile(name=name, size=(Path(loras_dir) / name).stat().st_size) for name in list_lora_names(loras_dir)]


@router.get("", response_model=list[LoraFile])
async def list_loras(request: Request):
    return await run_in_threadpool(list_lora_files, state(request).settings.loras_dir)
