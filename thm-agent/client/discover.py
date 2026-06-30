# Forked from src/helpers.py list_* and src/run_helpers.check_comfyui_status patterns.

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

import requests

from client.config_helpers import REPO_ROOT, WORKFLOWS_DIR

DEFAULT_API_BASE = "http://127.0.0.1:8188"
MODEL_EXTS = {".safetensors", ".ckpt", ".pt", ".bin"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _load_toml_config() -> dict:
    config_path = REPO_ROOT / "config.toml"
    if not config_path.exists():
        return {}
    try:
        with config_path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def load_paths_config() -> dict[str, str]:
    """Return paths from config.toml (models, loras, workspace, api_base, output_root)."""
    toml_data = _load_toml_config()
    comfy = toml_data.get("comfyui", {})
    paths = toml_data.get("paths", {})
    return {
        "api_base": str(comfy.get("api_base") or DEFAULT_API_BASE).strip(),
        "output_root": str(comfy.get("output_root") or "").strip(),
        "models": str(paths.get("models") or "").strip(),
        "loras": str(paths.get("loras") or "").strip(),
        "workspace": str(paths.get("workspace") or "./projects").strip(),
    }


def _resolve_path(base: str) -> Path:
    p = Path(base)
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve()
    return p


def list_checkpoints() -> list[str]:
    cfg = load_paths_config()
    p = _resolve_path(cfg["models"]) if cfg["models"] else Path(".")
    if not p.exists():
        return []
    try:
        return sorted(
            fp.name for fp in p.iterdir() if fp.is_file() and fp.suffix.lower() in MODEL_EXTS
        )
    except OSError:
        return []


def list_loras() -> list[str]:
    cfg = load_paths_config()
    p = _resolve_path(cfg["loras"]) if cfg["loras"] else Path(".")
    names: list[str] = []
    if p.exists():
        try:
            names = sorted(
                fp.name for fp in p.iterdir() if fp.is_file() and fp.suffix.lower() in MODEL_EXTS
            )
        except OSError:
            pass
    registry = REPO_ROOT / "scripts" / "lora_registry.csv"
    if registry.exists():
        try:
            with registry.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    for col in ("high", "low", "name", "lora_high", "lora_low"):
                        val = (row.get(col) or "").strip()
                        if val and val not in names:
                            names.append(val)
        except Exception:
            pass
    return sorted(set(names))


def list_workflows() -> list[str]:
    if not WORKFLOWS_DIR.is_dir():
        return []
    try:
        return sorted(fp.name for fp in WORKFLOWS_DIR.glob("*.json") if fp.is_file())
    except OSError:
        return []


def list_projects() -> list[str]:
    cfg = load_paths_config()
    p = _resolve_path(cfg["workspace"])
    if not p.exists():
        return []
    try:
        return sorted(fp.name for fp in p.glob("*.json") if fp.is_file())
    except OSError:
        return []


def list_keyframe_images(
    project_data: dict,
    seq_id: str,
    kf_id: str,
    *,
    include_previews: bool = False,
) -> list[str]:
    proj = project_data.get("project", {})
    output_root = proj.get("comfy", {}).get("output_root", "")
    project_name = proj.get("name", "")
    if not output_root or not project_name:
        return []
    image_dir = Path(output_root) / project_name / seq_id / kf_id
    if not image_dir.exists():
        return []
    preview_kw = {"openposepreview", "shapepreview", "outlinepreview"}
    paths: list[str] = []
    for fp in image_dir.iterdir():
        if fp.suffix.lower() not in IMAGE_EXTS:
            continue
        if not include_previews and any(kw in fp.name for kw in preview_kw):
            continue
        paths.append(str(fp.resolve()))
    return sorted(paths, key=lambda p: Path(p).stat().st_mtime, reverse=True)


def list_video_files(project_data: dict, seq_id: str, vid_id: str) -> list[str]:
    proj = project_data.get("project", {})
    output_root = proj.get("comfy", {}).get("output_root", "")
    project_name = proj.get("name", "")
    if not output_root or not project_name:
        return []
    video_dir = Path(output_root) / project_name / seq_id / vid_id
    if not video_dir.exists():
        return []
    return sorted(
        (str(p.resolve()) for p in video_dir.glob("*.mp4")),
        key=lambda p: Path(p).stat().st_mtime,
        reverse=True,
    )


def comfy_status(api_base: str | None = None) -> dict[str, Any]:
    """Check ComfyUI reachability. Returns {online, api_base, message}."""
    cfg = load_paths_config()
    url = (api_base or cfg["api_base"] or DEFAULT_API_BASE).strip()
    if not url.startswith("http"):
        url = f"http://{url}"
    test_url = f"{url.rstrip('/')}/queue"
    try:
        r = requests.get(test_url, timeout=2)
        if r.status_code == 200:
            return {"online": True, "api_base": url, "message": f"ComfyUI online at {url}"}
    except Exception as e:
        return {"online": False, "api_base": url, "message": f"ComfyUI offline: {e}"}
    return {"online": False, "api_base": url, "message": f"ComfyUI unreachable at {url}"}
