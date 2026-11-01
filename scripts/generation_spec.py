"""THM generation spec shared by ``run_images.py --generation`` and ``run_video.py --generation``.

The THM server writes one JSON file per generation from a Generation record and
launches a runner on it. Nothing 1.x (sequences, characters, negatives dicts) is
read. Shape::

    {
      "id": "g_0a1", "project": "rail-dawn-promo",
      "workflow": "pixa-four-image_vague.json",         # name under workflows/ or absolute path
      "prompt": "<prompt_submitted>", "negative": "<negative_submitted>",
      "prelude": ["image1 is a character reference."],
      "paths_by_slot": {"198": "D:/.../a.png", "start": "...", "end": "...", "40": "D:/.../clip.mp4"},
      "params": {"seed": 1234, "steps": 30, "cfg": 3.5, "sampler": "euler", "scheduler": "simple",
                 "width": 1024, "height": 1024, "length_s": 5, "fps": 16},
      "loras": [{"name": "x.safetensors", "strength": 0.75}],   # optional; prompt <lora:...> tags are the usual source
      "output_dir": "D:/ComfyUI/output/rail-dawn-promo/s_01",   # must lie under comfy.output_root
      "filename_prefix": "s_01",
      "label": "Video 1 take",
      "comfy": {"api_base": "http://127.0.0.1:8188", "output_root": "D:/ComfyUI/output", "timeout_seconds": 3600}
    }

``comfy.timeout_seconds`` is execution time: how long ComfyUI may run the prompt before the
runner gives up (waiting in the queue behind other prompts does not count).

Status file: ``--status-file`` or ``<output_dir>/_generation_status.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lora_tags import LoraSpec, find_lora_tags

REQUIRED_KEYS = ("workflow", "prompt", "output_dir", "filename_prefix", "comfy")
REQUIRED_COMFY_KEYS = ("api_base", "output_root")


def load_generation_spec(spec_path) -> dict:
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, dict):
        raise ValueError("generation spec must be a JSON object")
    missing = [k for k in REQUIRED_KEYS if k not in spec]
    if missing:
        raise ValueError(f"generation spec missing keys: {', '.join(missing)}")
    comfy = spec.get("comfy") or {}
    missing = [k for k in REQUIRED_COMFY_KEYS if not comfy.get(k)]
    if missing:
        raise ValueError(f"generation spec missing comfy keys: {', '.join(missing)}")
    return spec


def generation_status_path(spec: dict, status_file_override=None) -> Path:
    path = Path(status_file_override) if status_file_override else Path(spec["output_dir"]) / "_generation_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def output_dir_relative_to_root(output_dir: str, output_root: str) -> str:
    """``output_dir`` relative to the ComfyUI output root; '' when equal; refuses outside paths."""
    rel = os.path.relpath(os.path.abspath(str(output_dir)), os.path.abspath(str(output_root)))
    if rel == ".":
        return ""
    if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
        raise ValueError(f"output_dir {output_dir} is not under comfy.output_root {output_root}")
    return rel


def paths_by_slot_from_spec(spec: dict) -> dict[str, str]:
    return {str(k): str(v) for k, v in (spec.get("paths_by_slot") or {}).items() if v}


def lora_specs_from_spec(spec: dict, prompt_text: str = "") -> list[LoraSpec]:
    """LoRAs for the render: tags found in ``prompt_text`` (either notation), plus any
    structured ``loras`` entries the spec still carries. One per name; a prompt tag wins
    over a structured entry of the same name."""
    by_name: dict[str, LoraSpec] = {}
    for entry in spec.get("loras") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        by_name[str(entry["name"])] = LoraSpec(name=str(entry["name"]), strength=float(entry.get("strength", 1.0)))
    for tag in find_lora_tags(prompt_text):
        by_name[tag.name] = tag
    return list(by_name.values())


def prompt_text_from_spec(spec: dict) -> str:
    """Prelude lines first, then the submitted prompt, newline joined."""
    lines = [str(x) for x in (spec.get("prelude") or []) if str(x).strip()]
    lines.append(str(spec.get("prompt") or ""))
    return "\n".join(lines)
