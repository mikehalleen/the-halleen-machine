"""UI-free helpers the runner scripts (scripts/run_images.py, scripts/run_video.py) need.

Moved here from src/helpers.py, which imports gradio at module scope; the runners are
launched by thm_server as plain subprocesses and must not pull the 1.x UI stack in.
Family / workflow-name logic lives in workflow_names.py and is reused, not copied.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from thm_core.workflow_names import (
    WORKFLOWS_DIR,
    _project_dict,
    effective_video_workflow_filename,
    is_default_image_family,
    is_default_video_family,
    resolve_curated_workflow,
)

# Seed-injection node titles baked into the default video workflow.
DEFAULT_VIDEO_SEED_TARGET_TITLE = "SlowMoPrimer"
DEFAULT_VIDEO_SEED_EXCLUDE_TITLE = "WanFixedSeed"


def project_controls_kf_sampler_settings(project_json_or_dict) -> bool:
    """True when project JSON should drive KSampler steps/cfg/sampler/scheduler at run time."""
    return is_default_image_family(project_json_or_dict)


def resolve_project_video_workflow(project_json_or_dict) -> str:
    """Absolute path of the project's effective video workflow.

    A name is looked up in workflows_video/ (and workflows_image/) first; a name that
    lives only in the legacy flat workflows/ folder still resolves there.
    """
    filename = effective_video_workflow_filename(project_json_or_dict)
    curated = resolve_curated_workflow(filename)
    if curated is not None:
        return str(curated)
    return str((WORKFLOWS_DIR / filename).resolve())


def resolve_project_video_seed_titles(project_json_or_dict) -> tuple[str, str]:
    """Effective (seed_target_title, seed_exclude_title) for the in-between run.

    The Default family flag overrides the stored workflow, so it must also override
    the stored seed-node titles: Default always runs the default video workflow, whose
    seed nodes are SlowMoPrimer / WanFixedSeed regardless of any last-used custom titles
    left in the project. Custom family uses whatever titles the project stores.
    """
    if is_default_video_family(project_json_or_dict):
        return DEFAULT_VIDEO_SEED_TARGET_TITLE, DEFAULT_VIDEO_SEED_EXCLUDE_TITLE
    ib = _project_dict(project_json_or_dict).get("inbetween_generation") or {}
    return (
        str(ib.get("seed_target_title") or "IterKSampler"),
        str(ib.get("seed_exclude_title") or DEFAULT_VIDEO_SEED_EXCLUDE_TITLE),
    )


def wait_for_file_stable(
    file_path: str,
    *,
    timeout_s: float = 60.0,
    poll_s: float = 0.25,
    stable_polls: int = 3,
    min_size: int = 1,
) -> bool:
    """Wait until a file exists and its size is unchanged (SaveImage flush / Windows)."""
    path = Path(file_path)
    deadline = time.time() + timeout_s
    last_size = -1
    stable = 0
    while time.time() < deadline:
        try:
            if not path.is_file():
                stable = 0
                last_size = -1
            else:
                size = path.stat().st_size
                if size >= min_size and size == last_size:
                    stable += 1
                    if stable >= stable_polls:
                        return True
                else:
                    stable = 0
                    last_size = size
        except OSError:
            stable = 0
            last_size = -1
        time.sleep(poll_s)
    return False


def inject_the_machine_snapshot(file_path: str, snapshot: dict) -> bool:
    """Write the_machine_snapshot via temp file + atomic replace (avoids Windows read races)."""
    if not file_path or not os.path.exists(file_path):
        return False
    path = Path(file_path)
    if path.suffix.lower() not in (".png",):
        return False

    tmp_path = path.with_name(path.name + ".thm.tmp")
    try:
        wait_for_file_stable(str(path), timeout_s=30.0)
        with Image.open(path) as img:
            img.load()
            metadata = PngInfo()
            for k, v in img.info.items():
                if k != "the_machine_snapshot":
                    metadata.add_text(k, str(v))
            metadata.add_text("the_machine_snapshot", json.dumps(snapshot, ensure_ascii=False))
            img.save(tmp_path, format="PNG", pnginfo=metadata)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        print(f"[METADATA] Error injecting snapshot: {e}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False
