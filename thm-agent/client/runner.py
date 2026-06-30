# Forked from src/single_gen_helpers.run_image_generation_task and single_video_helpers.

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from client.config_helpers import REPO_ROOT, WORKFLOWS_DIR, get_temp_dir
from client.prep import AssetType, prep_asset_run, prep_keyframe_run, prep_video_run

SCRIPTS_DIR = REPO_ROOT / "scripts"
PREVIEW_KEYWORDS = {"openposepreview", "shapepreview", "outlinepreview"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class GenerationResult:
    success: bool
    main_path: str | None = None
    workspace_path: str | None = None
    preview_paths: dict = field(default_factory=dict)
    log: str = ""
    result_lines: list = field(default_factory=list)
    seq_id: str = ""
    node_id: str = ""
    project_name: str = ""


def _run_script(script_name: str, temp_data: dict) -> tuple[int, str, list[str]]:
    temp_dir = get_temp_dir(temp_data) or str(REPO_ROOT / "projects")
    os.makedirs(temp_dir, exist_ok=True)
    unique_suffix = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    temp_path = Path(temp_dir) / f"__temp_agent_{unique_suffix}.json"
    result_lines: list[str] = []
    log = ""
    try:
        temp_path.write_text(json.dumps(temp_data, indent=2, ensure_ascii=False), encoding="utf-8")
        script_path = SCRIPTS_DIR / script_name
        command = [sys.executable, "-u", str(script_path), "--config", str(temp_path)]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            log += line
            if line.strip().startswith("RESULT:"):
                result_lines.append(line.strip())
        exit_code = process.wait()
        return exit_code, log, result_lines
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _find_latest_image(image_dir: Path) -> tuple[str | None, dict]:
    previews: dict = {}
    if not image_dir.exists():
        return None, previews
    image_files = [str(p) for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS]
    if not image_files:
        return None, previews

    def find_latest(suffix_key: str) -> str | None:
        candidates = [f for f in image_files if suffix_key in Path(f).name]
        return max(candidates, key=os.path.getmtime) if candidates else None

    previews["openpose"] = find_latest("openposepreview")
    previews["shape"] = find_latest("shapepreview")
    previews["outline"] = find_latest("outlinepreview")
    main_candidates = [
        f for f in image_files if not any(kw in Path(f).name for kw in PREVIEW_KEYWORDS)
    ]
    main_path = max(main_candidates, key=os.path.getmtime) if main_candidates else None
    return main_path, previews


def _find_latest_video(video_dir: Path) -> str | None:
    if not video_dir.exists():
        return None
    for _ in range(5):
        video_files = list(video_dir.glob("*.mp4"))
        if video_files:
            return str(max(video_files, key=lambda p: p.stat().st_mtime))
        time.sleep(0.5)
    return None


def run_keyframe(
    project_data: dict,
    seq_id: str,
    kf_id: str,
    *,
    seed: Optional[int] = None,
) -> GenerationResult:
    temp_data = prep_keyframe_run(project_data, seq_id, kf_id, seed=seed)
    project_name = temp_data.get("project", {}).get("name", "")
    exit_code, log, result_lines = _run_script("run_images.py", temp_data)

    output_root = temp_data.get("project", {}).get("comfy", {}).get("output_root", "")
    image_dir = Path(output_root) / project_name / seq_id / kf_id
    main_path, previews = _find_latest_image(image_dir)
    if exit_code != 0 and not log.strip():
        log = f"run_images.py exited with code {exit_code}"
    if exit_code == 0 and not main_path:
        log += f"\n\nError: no image found in {image_dir}"

    return GenerationResult(
        success=bool(main_path),
        main_path=main_path,
        preview_paths=previews,
        log=log,
        result_lines=result_lines,
        seq_id=seq_id,
        node_id=kf_id,
        project_name=project_name,
    )


def run_video(
    project_data: dict,
    seq_id: str,
    vid_id: str,
    *,
    seed: Optional[int] = None,
) -> GenerationResult:
    temp_data = prep_video_run(project_data, seq_id, vid_id, seed=seed)
    project_name = temp_data.get("project", {}).get("name", "")
    exit_code, log, result_lines = _run_script("run_video.py", temp_data)

    output_root = temp_data.get("project", {}).get("comfy", {}).get("output_root", "")
    video_dir = Path(output_root) / project_name / seq_id / vid_id
    main_path = _find_latest_video(video_dir)
    if exit_code != 0 and not log.strip():
        log = f"run_video.py exited with code {exit_code}"
    if exit_code == 0 and not main_path:
        log += f"\n\nError: no video found in {video_dir}"

    return GenerationResult(
        success=bool(main_path),
        main_path=main_path,
        log=log,
        result_lines=result_lines,
        seq_id=seq_id,
        node_id=vid_id,
        project_name=project_name,
    )


def run_asset(
    project_data: dict,
    asset_type: AssetType,
    asset_id: str,
    *,
    session_workflow: str | None = None,
    seed: Optional[int] = None,
    layout_override: str | None = None,
) -> GenerationResult:
    temp_data, seq_id, kf_id = prep_asset_run(
        project_data,
        asset_type,
        asset_id,
        session_workflow=session_workflow,
        seed=seed,
        layout_override=layout_override,
    )
    project_name = temp_data.get("project", {}).get("name", "")
    exit_code, log, result_lines = _run_script("run_images.py", temp_data)

    output_root = temp_data.get("project", {}).get("comfy", {}).get("output_root", "")
    image_dir = Path(output_root) / project_name / seq_id / kf_id
    main_path, previews = _find_latest_image(image_dir)
    if exit_code != 0 and not log.strip():
        log = f"run_images.py exited with code {exit_code}"
    if exit_code == 0 and not main_path:
        log += f"\n\nError: no image found in {image_dir}"

    return GenerationResult(
        success=bool(main_path),
        main_path=main_path,
        preview_paths=previews,
        log=log,
        result_lines=result_lines,
        seq_id=seq_id,
        node_id=kf_id,
        project_name=project_name,
    )


def validate_video_prerequisites(project_data: dict, seq_id: str, vid_id: str) -> list[str]:
    """Return list of missing selected_image_path on required keyframes."""
    issues: list[str] = []
    seq = project_data.get("sequences", {}).get(seq_id, {})
    vid = seq.get("videos", {}).get(vid_id, {})
    keyframes = seq.get("keyframes", {})

    start_id = vid.get("start_keyframe_id")
    end_id = vid.get("end_keyframe_id")
    ctype = (
        "SE" if (start_id and end_id)
        else "OE" if end_id
        else "SO" if start_id
        else None
    )
    if not ctype:
        return issues

    supports_start = False
    supports_end = False
    try:
        import sys
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        if str(REPO_ROOT / "scripts") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "scripts"))
        if str(REPO_ROOT / "src") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "src"))
        import workflow_controls as wc
        from helpers import resolve_project_video_workflow

        wf_path = Path(resolve_project_video_workflow(project_data))
        if wf_path.is_file():
            workflow = json.loads(wf_path.read_text(encoding="utf-8"))
            frame_support = wc.discover_video_frame_input_support(workflow)
            supports_start = frame_support.supports_start_frame
            supports_end = frame_support.supports_end_frame
    except Exception:
        supports_start = True
        supports_end = True

    checks: list[tuple[str, str | None]] = []
    if supports_start and ctype in ("SE", "SO") and start_id:
        checks.append(("start", start_id))
    if supports_end and ctype in ("SE", "OE") and end_id:
        checks.append(("end", end_id))

    for label, kf_id in checks:
        kf = keyframes.get(kf_id, {})
        if not kf.get("selected_image_path"):
            issues.append(f"{label} keyframe {kf_id} missing selected_image_path")
    return issues
