# Forked from src/helpers.py patterns — agent client only; no src/ imports.

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = (REPO_ROOT / "workflows").resolve()
DEFAULT_PROJECT_WORKFLOW_FILENAME = "pose_OPEN.json"

DEFAULT_KF_CN_SETTINGS = {
    "1": {"switch": "On", "strength": 0.9, "start_percent": 0.0, "end_percent": 0.9},
    "2": {"switch": "On", "strength": 0.5, "start_percent": 0.0, "end_percent": 0.5},
    "3": {"switch": "Off", "strength": 0.5, "start_percent": 0.0, "end_percent": 0.5},
}

TEST_CHARACTER_SETTING_PROMPT = (
    "a professionl photo studio, neutral background, infinity wall, indirect lighting, even soft lighting"
)
TEST_CHARACTER_PROMPT = "clear view for the character"
TEST_SETTING_LAYOUT_PROMPT = "((empty space))"
TEST_SETTING_ANCHOR_PROMPT = "empty environment, no people, no character, no subject"
TEST_CHARACTER_DEFAULT_NEGATIVE = (
    "blurry, low quality, watermark, deformed, extra limbs, cropped, duplicate, "
    "camera gear, production gear, visible behind the scenes"
)
TEST_SETTING_DEFAULT_NEGATIVE = "people, character, subject, figure, portrait, face, person"
TEST_STYLE_DEFAULT_NEGATIVE = "people, character, subject, cluttered background, busy scene"


def get_node_by_id(data: Dict[str, Any], node_id: str) -> Tuple[Dict[str, Any] | None, str | None]:
    seqs = data.get("sequences", {})
    if node_id in seqs:
        return seqs[node_id], "seq"
    for seq in seqs.values():
        if node_id in seq.get("keyframes", {}):
            return seq["keyframes"][node_id], "kf"
        if node_id in seq.get("videos", {}):
            vid = seq["videos"][node_id]
            if "sequence_id" not in vid:
                vid = {**vid, "sequence_id": seq.get("id")}
            return vid, "vid"
    return None, None


def resolve_context(data: dict, nid: str):
    node, kind = get_node_by_id(data, nid)
    if not node:
        return None, None, None, None
    if kind == "seq":
        return node, kind, node, node["id"]
    seq_id = node.get("sequence_id")
    if not seq_id:
        for s_id, seq in data.get("sequences", {}).items():
            if kind == "kf" and nid in seq.get("keyframes", {}):
                seq_id = s_id
                break
            if kind == "vid" and nid in seq.get("videos", {}):
                seq_id = s_id
                break
    parent = data.get("sequences", {}).get(seq_id)
    return node, kind, parent, seq_id


def is_custom_image_family(data: dict) -> bool:
    fam = (data.get("project", {}) or {}).get("image_model_family", "default")
    return str(fam).strip().lower() == "custom"


def is_default_image_family(data: dict) -> bool:
    return not is_custom_image_family(data)


def project_default_workflow_filename(data: dict) -> str:
    proj = data.get("project", {}) or {}
    wf = str(proj.get("default_workflow_json") or "").strip()
    return Path(wf).name if wf else DEFAULT_PROJECT_WORKFLOW_FILENAME


def resolve_project_default_workflow(data: dict) -> str:
    name = project_default_workflow_filename(data)
    return str((WORKFLOWS_DIR / name).resolve())


def resolve_session_workflow_path(session_workflow: str | None) -> str | None:
    name = str(session_workflow or "").strip()
    if not name:
        return None
    path = (WORKFLOWS_DIR / Path(name).name).resolve()
    return str(path) if path.is_file() else None


def workflow_for_asset_test(
    full_data: dict,
    *,
    pose_path: str | None = None,
    kind: str = "character",
    look_flat: dict | None = None,
    session_workflow: str | None = None,
) -> str:
    if look_flat and look_flat.get("default_workflow_json"):
        wf_name = Path(str(look_flat["default_workflow_json"])).name
        wf_path = (WORKFLOWS_DIR / wf_name).resolve()
        if wf_path.is_file():
            return str(wf_path)
    if is_custom_image_family(full_data):
        session_path = resolve_session_workflow_path(session_workflow)
        if session_path:
            return session_path
        return resolve_project_default_workflow(full_data)
    return str((WORKFLOWS_DIR / DEFAULT_PROJECT_WORKFLOW_FILENAME).resolve())


def asset_test_negative(user_neg: str, default_neg: str) -> str:
    parts = [p.strip() for p in (user_neg, default_neg) if p and p.strip()]
    return ", ".join(parts)


def asset_generator_prompt(asset: dict) -> str:
    return (asset.get("generator_prompt") or asset.get("prompt") or "").strip()


def asset_generator_negative(asset: dict) -> str:
    return (asset.get("generator_negative_prompt") or asset.get("negative_prompt") or "").strip()


def apply_look_context_to_temp_project(temp_data: dict, look_flat: dict | None) -> None:
    if not look_flat:
        return
    proj = temp_data.setdefault("project", {})
    if look_flat.get("image_model_family"):
        proj["image_model_family"] = look_flat["image_model_family"]
    if look_flat.get("default_workflow_json"):
        proj["default_workflow_json"] = look_flat["default_workflow_json"]
    if look_flat.get("model"):
        proj["model"] = look_flat["model"]
    if look_flat.get("style_prompt") is not None:
        proj["style_prompt"] = look_flat.get("style_prompt") or ""
    if look_flat.get("width"):
        proj["width"] = look_flat["width"]
    if look_flat.get("height"):
        proj["height"] = look_flat["height"]
    keygen = proj.setdefault("keyframe_generation", {})
    for src, dst in (
        ("steps", "steps"),
        ("cfg", "cfg"),
        ("sampler", "sampler_name"),
        ("scheduler", "scheduler"),
    ):
        if look_flat.get(src) is not None:
            keygen[dst] = look_flat[src]
    negs = proj.setdefault("negatives", {})
    for src, dst in (
        ("neg_global", "global"),
        ("neg_kf", "keyframes_all"),
        ("neg_i2v", "inbetween_all"),
        ("neg_heal", "heal_all"),
    ):
        if look_flat.get(src) is not None:
            negs[dst] = look_flat[src]
    lora = proj.setdefault("lora_normalization", {})
    for flat_key, field in (
        ("lora_normalization.fg_enabled", "fg_enabled"),
        ("lora_normalization.fg_max", "fg_max"),
        ("lora_normalization.bg_enabled", "bg_enabled"),
        ("lora_normalization.bg_max", "bg_max"),
    ):
        if look_flat.get(flat_key) is not None:
            lora[field] = look_flat[flat_key]


def mirror_project_sampler_globals(temp_data: dict, full_data: dict) -> None:
    if not is_default_image_family(full_data):
        return
    project_kf_globals = full_data.get("project", {}).get("keyframe_generation", {})
    if "keyframe_generation" not in temp_data["project"]:
        temp_data["project"]["keyframe_generation"] = {}
    temp_data["project"]["keyframe_generation"].update({
        "steps": project_kf_globals.get("steps", 30),
        "cfg": project_kf_globals.get("cfg", 4.0),
        "sampler_name": project_kf_globals.get("sampler_name", "dpmpp_2m_sde"),
        "scheduler": project_kf_globals.get("scheduler", "karras"),
    })


def get_temp_dir(data: dict) -> str | None:
    proj = data.get("project", {}) or {}
    output_root = proj.get("comfy", {}).get("output_root")
    name = proj.get("name")
    if output_root and name:
        return str(Path(output_root) / name)
    return None


def _conventional_venv_python() -> Path | None:
    """Repo-root venv used by start.bat and THM install convention."""
    if sys.platform == "win32":
        candidate = REPO_ROOT / "the-machine-ui-venv" / "Scripts" / "python.exe"
    else:
        candidate = REPO_ROOT / "the-machine-ui-venv" / "bin" / "python"
    return candidate if candidate.is_file() else None


def resolve_agent_python() -> Path:
    """
    Resolve the Python interpreter for thm-agent CLI and pipeline scripts.

    Priority: config.toml [agent].python → repo the-machine-ui-venv → fail fast.
    Never falls back to sys.executable (system python).
    """
    configured = ""
    try:
        from helpers import load_config  # noqa: WPS433 — src helper

        cfg = load_config()
        configured = str((cfg.get("agent") or {}).get("python") or "").strip()
    except Exception:
        configured = ""

    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        if path.is_file():
            return path

    conventional = _conventional_venv_python()
    if conventional:
        return conventional

    raise RuntimeError(
        "THM agent Python not found. Set [agent].python in config.toml "
        "(run setup.py) or create the-machine-ui-venv at the repo root."
    )


def agent_python_argv() -> list[str]:
    """Argv prefix for subprocess calls: [python_exe, cli_path, ...]."""
    return [str(resolve_agent_python())]
