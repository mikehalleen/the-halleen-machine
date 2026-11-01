"""Settings for thm_core, read from the same config.toml the 1.x app uses.

Every constructor in the package also accepts these values as parameters, so
tests never touch config.toml. The key mapping mirrors load_config() in
src/helpers.py; only the keys thm_core needs are read here.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class BackupSettings:
    retention_count: int = 10
    throttle_seconds: float = 60.0


@dataclass
class ComfySettings:
    api_base: str = "http://127.0.0.1:8188"
    output_root: str = ""
    # How long a prompt may *execute* in ComfyUI before the runner gives up on it (time spent
    # waiting in the queue does not count). Video flows with a bound reference video run past an
    # hour at full size, so they get their own, longer limit.
    timeout_seconds: int = 3600
    video_timeout_seconds: int = 4 * 3600


@dataclass
class CoreSettings:
    projects_dir: Path = REPO_ROOT / "projects"
    workflows_dir: Path = REPO_ROOT / "workflows"
    loras_dir: Path | None = None  # [paths] loras: ComfyUI's models/loras, listed for the LoRA picker
    backups: BackupSettings = field(default_factory=BackupSettings)
    comfy: ComfySettings = field(default_factory=ComfySettings)


def load_core_settings(path: str | Path | None = None, *, repo_root: Path = REPO_ROOT) -> CoreSettings:
    """Read config.toml if present; otherwise return defaults. Never raises on a missing file."""
    config_path = Path(path) if path else repo_root / "config.toml"
    settings = CoreSettings(projects_dir=repo_root / "projects", workflows_dir=repo_root / "workflows")
    if not config_path.is_file():
        return settings
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    comfy = data.get("comfyui") or {}
    paths = data.get("paths") or {}
    backups = data.get("backups") or {}
    settings.comfy = ComfySettings(
        api_base=str(comfy.get("api_base") or settings.comfy.api_base),
        output_root=str(comfy.get("output_root") or ""),
        timeout_seconds=int(comfy.get("timeout_seconds") or settings.comfy.timeout_seconds),
        video_timeout_seconds=int(comfy.get("video_timeout_seconds") or settings.comfy.video_timeout_seconds),
    )
    workspace = str(paths.get("workspace") or "").strip()
    if workspace:
        ws = Path(workspace)
        settings.projects_dir = ws if ws.is_absolute() else (repo_root / ws).resolve()
    loras = str(paths.get("loras") or "").strip()
    if loras:
        lp = Path(loras)
        settings.loras_dir = lp if lp.is_absolute() else (repo_root / lp).resolve()
    settings.backups = BackupSettings(
        retention_count=int(backups.get("retention", settings.backups.retention_count)),
        throttle_seconds=float(backups.get("throttle_seconds", settings.backups.throttle_seconds)),
    )
    return settings
