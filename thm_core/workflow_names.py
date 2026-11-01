"""Workflow file names and model-family helpers, copied from src/helpers.py.

These are the UI-free names the capability scanner needs. They are copied rather
than imported because src/helpers.py imports gradio at module scope. Keep them in
step with src/helpers.py lines 47-160 until the wider helpers split happens.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = (REPO_ROOT / "workflows").resolve()

# A workflow's kind is the folder it sits in, not anything read out of its graph: whatever is
# copied into workflows_image/ is offered as an image model, workflows_video/ as a video one.
# Both are siblings of whichever workflows dir is in play, so a test or a second install keeps
# its own set rather than reaching into the repo's.
WORKFLOW_KIND_DIRNAMES = {"image": "workflows_image", "video": "workflows_video"}


def workflow_kind_dirs(workflows_dir: Path | str | None = None) -> dict[str, Path]:
    """``{"image": .../workflows_image, "video": .../workflows_video}``. Neither need exist."""
    base = Path(workflows_dir or WORKFLOWS_DIR)
    return {kind: base.parent / name for kind, name in WORKFLOW_KIND_DIRNAMES.items()}


def resolve_curated_workflow(workflow_name: str | None, workflows_dir: Path | str | None = None) -> Path | None:
    """2.0's resolver: an absolute path, or a name found in workflows_image/ or workflows_video/.

    The flat workflows/ folder is the 1.x app's and is never consulted here, so a 2.0 record
    naming something that only lives there does not resolve at all. A bare name present in both
    kind folders resolves to the image one — keep names unique across the two.
    """
    name = str(workflow_name or "").strip()
    if not name:
        return None
    candidate = Path(name)
    if candidate.is_file():
        return candidate.resolve()
    for base in workflow_kind_dirs(workflows_dir).values():
        joined = base / name
        if joined.is_file():
            return joined.resolve()
        if not name.endswith(".json"):
            with_json = base / f"{name}.json"
            if with_json.is_file():
                return with_json.resolve()
    return None


def workflow_kind_for_path(path: Path | str, workflows_dir: Path | str | None = None) -> str | None:
    """``"image"``/``"video"`` for a file in a kind folder; ``None`` for one in the legacy flat
    folder, whose kind still has to be guessed from the graph."""
    parent = Path(path).resolve().parent
    for kind, kind_dir in workflow_kind_dirs(workflows_dir).items():
        if parent == kind_dir.resolve():
            return kind
    return None


# Names in the curated kind folders, not in the flat workflows/ folder these used to name
# (pose_OPEN.json and i2v_base.json).
DEFAULT_PROJECT_WORKFLOW_FILENAME = "klein.json"
DEFAULT_VIDEO_WORKFLOW_FILENAME = "ltx2.5.json"
IMAGE_MODEL_FAMILY_DEFAULT = "default"
IMAGE_MODEL_FAMILY_CUSTOM = "custom"
VIDEO_MODEL_FAMILY_DEFAULT = "default"
VIDEO_MODEL_FAMILY_CUSTOM = "custom"


def _project_dict(project_json_or_dict) -> dict:
    if isinstance(project_json_or_dict, dict):
        return project_json_or_dict.get("project", project_json_or_dict) or {}
    if isinstance(project_json_or_dict, str) and project_json_or_dict.strip():
        try:
            data = json.loads(project_json_or_dict)
            return data.get("project", data) if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def image_model_family(project_json_or_dict) -> str:
    fam = str(_project_dict(project_json_or_dict).get("image_model_family") or "").strip().lower()
    if fam == IMAGE_MODEL_FAMILY_CUSTOM:
        return IMAGE_MODEL_FAMILY_CUSTOM
    return IMAGE_MODEL_FAMILY_DEFAULT


def is_default_image_family(project_json_or_dict) -> bool:
    return image_model_family(project_json_or_dict) == IMAGE_MODEL_FAMILY_DEFAULT


def is_custom_image_family(project_json_or_dict) -> bool:
    return image_model_family(project_json_or_dict) == IMAGE_MODEL_FAMILY_CUSTOM


def video_model_family(project_json_or_dict) -> str:
    proj = _project_dict(project_json_or_dict)
    fam = str(proj.get("video_model_family") or "").strip().lower()
    if fam == VIDEO_MODEL_FAMILY_CUSTOM:
        return VIDEO_MODEL_FAMILY_CUSTOM
    if fam == VIDEO_MODEL_FAMILY_DEFAULT:
        return VIDEO_MODEL_FAMILY_DEFAULT
    return infer_video_model_family(project_json_or_dict)


def infer_video_model_family(project_json_or_dict) -> str:
    """Infer family from stored workflow when video_model_family is absent."""
    stored = stored_video_workflow_filename(project_json_or_dict)
    if stored and stored != DEFAULT_VIDEO_WORKFLOW_FILENAME:
        return VIDEO_MODEL_FAMILY_CUSTOM
    return VIDEO_MODEL_FAMILY_DEFAULT


def is_default_video_family(project_json_or_dict) -> bool:
    return video_model_family(project_json_or_dict) == VIDEO_MODEL_FAMILY_DEFAULT


def is_custom_video_family(project_json_or_dict) -> bool:
    return video_model_family(project_json_or_dict) == VIDEO_MODEL_FAMILY_CUSTOM


def stored_video_workflow_filename(project_json_or_dict) -> str:
    """Basename of project.inbetween_generation.video_workflow_json."""
    stored = str(
        (_project_dict(project_json_or_dict).get("inbetween_generation") or {}).get("video_workflow_json")
        or ""
    ).strip()
    if stored:
        return Path(stored).name
    return DEFAULT_VIDEO_WORKFLOW_FILENAME


def effective_video_workflow_filename(project_json_or_dict) -> str:
    """Workflow used for in-between runs and capability scans."""
    if is_default_video_family(project_json_or_dict):
        return DEFAULT_VIDEO_WORKFLOW_FILENAME
    return stored_video_workflow_filename(project_json_or_dict)
