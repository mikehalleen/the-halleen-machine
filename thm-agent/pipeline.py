"""
THM agent pipeline — long-run generation, checkpoints, vision-QC workflow.

Detached scripts and agents share this module. Never uses file-size heuristics for QC.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from client.config_helpers import REPO_ROOT, resolve_agent_python

CLI_PATH = REPO_ROOT / "thm-agent" / "cli.py"


@dataclass
class VariantRecord:
    index: int
    success: bool
    main_path: str | None = None
    workspace_path: str | None = None
    seed: int | None = None


@dataclass
class BeatCheckpoint:
    seq: str
    kf: str
    layout: str = ""
    status: str = "pending"  # pending | generated | selected | failed
    variants: list[VariantRecord] = field(default_factory=list)
    selected_path: str | None = None
    qc_method: str | None = None  # vision | manual
    qc_rationale: str | None = None
    skip_reason: str | None = None


@dataclass
class PipelineCheckpoint:
    project_path: str
    updated_at: str = ""
    beats: list[BeatCheckpoint] = field(default_factory=list)
    phase: str = "keyframes"  # keyframes | videos | export | done

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_path": self.project_path,
            "updated_at": self.updated_at,
            "phase": self.phase,
            "beats": [
                {
                    **{k: v for k, v in asdict(b).items() if k != "variants"},
                    "variants": [asdict(v) for v in b.variants],
                }
                for b in self.beats
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineCheckpoint:
        beats = []
        for raw in data.get("beats") or []:
            variants = [VariantRecord(**v) for v in raw.get("variants") or []]
            beats.append(
                BeatCheckpoint(
                    seq=raw["seq"],
                    kf=raw["kf"],
                    layout=raw.get("layout") or "",
                    status=raw.get("status") or "pending",
                    variants=variants,
                    selected_path=raw.get("selected_path"),
                    qc_method=raw.get("qc_method"),
                    qc_rationale=raw.get("qc_rationale"),
                    skip_reason=raw.get("skip_reason"),
                )
            )
        return cls(
            project_path=data.get("project_path") or "",
            updated_at=data.get("updated_at") or "",
            beats=beats,
            phase=data.get("phase") or "keyframes",
        )


def checkpoint_path(project: Path) -> Path:
    name = project.stem
    return REPO_ROOT / "thm-agent" / "workspace" / name / "pipeline-checkpoint.json"


def log_path(project: Path) -> Path:
    name = project.stem
    return REPO_ROOT / "thm-agent" / "workspace" / name / "pipeline.log"


def load_checkpoint(project: Path) -> PipelineCheckpoint:
    path = checkpoint_path(project)
    if not path.is_file():
        return PipelineCheckpoint(project_path=str(project))
    return PipelineCheckpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_checkpoint(cp: PipelineCheckpoint, project: Path) -> None:
    cp.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    path = checkpoint_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cp.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def append_log(project: Path, msg: str) -> None:
    print(msg, flush=True)
    path = log_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run_cli(cmd: list[str], *, timeout: int | None = None, cwd: Path | None = None) -> tuple[int, str]:
    import os

    python = str(resolve_agent_python())
    full = [python, str(CLI_PATH), *cmd]
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    proc = subprocess.run(
        full,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(cwd or REPO_ROOT),
        env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def load_project_json(project: Path) -> dict:
    return json.loads(project.read_text(encoding="utf-8"))


def keyframe_list(data: dict) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for sid in data.get("sequence_order") or []:
        seq = data["sequences"][sid]
        for kid in seq.get("keyframe_order") or []:
            layout = seq["keyframes"][kid].get("layout", "")
            rows.append((sid, kid, layout))
    return rows


def keyframe_has_selection(data: dict, seq: str, kf: str) -> bool:
    path = (
        data.get("sequences", {})
        .get(seq, {})
        .get("keyframes", {})
        .get(kf, {})
        .get("selected_image_path")
    )
    return bool(path) and Path(str(path)).exists()


def generate_single_variant(
    project: Path,
    seq: str,
    kf: str,
    *,
    seed: int | None,
    variant_index: int,
) -> VariantRecord:
    cmd = [
        "generate", "keyframe",
        "--project", str(project),
        "--seq", seq,
        "--kf", kf,
        "--variants", "1",
        "--json",
    ]
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    code, out = run_cli(cmd, timeout=7200)
    if code != 0:
        return VariantRecord(index=variant_index, success=False)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return VariantRecord(index=variant_index, success=False)
    variants = payload.get("variants") or []
    if not variants:
        return VariantRecord(index=variant_index, success=False)
    v = variants[0]
    return VariantRecord(
        index=variant_index,
        success=bool(v.get("success")),
        main_path=v.get("main_path"),
        workspace_path=v.get("workspace_path"),
        seed=seed,
    )


VisionQcFn = Callable[[VariantRecord, str], bool]


def generate_beat_variants(
    project: Path,
    seq: str,
    kf: str,
    layout: str,
    *,
    max_variants: int = 5,
    base_seed: int | None = None,
    vision_qc: VisionQcFn | None = None,
    min_first_variant: int = 2,
) -> BeatCheckpoint:
    """
    Generate keyframe variants with optional vision-based early stop.

    Early stop rules (when vision_qc provided):
    - Variant 1: require at least min_first_variant generations before stopping on pass
    - Variant 2+: stop on first vision pass
    """
    beat = BeatCheckpoint(seq=seq, kf=kf, layout=layout, status="pending")
    for i in range(1, max_variants + 1):
        seed = base_seed + (i - 1) if base_seed is not None else None
        record = generate_single_variant(project, seq, kf, seed=seed, variant_index=i)
        beat.variants.append(record)

        if vision_qc and record.success and vision_qc(record, layout):
            if i == 1 and len(beat.variants) < min_first_variant:
                continue
            beat.status = "generated"
            return beat

    beat.status = "generated" if any(v.success for v in beat.variants) else "failed"
    return beat


def record_vision_selection(
    project: Path,
    seq: str,
    kf: str,
    image_path: str,
    *,
    rationale: str = "",
) -> BeatCheckpoint:
    """Record agent vision QC selection in checkpoint (no file-size heuristics)."""
    cp = load_checkpoint(project)
    beat = _find_or_create_beat(cp, seq, kf)
    beat.selected_path = image_path
    beat.qc_method = "vision"
    beat.qc_rationale = rationale or None
    beat.status = "selected"
    save_checkpoint(cp, project)
    return beat


def apply_checkpoint_selections(project: Path) -> list[str]:
    """Persist vision-selected paths from checkpoint into project JSON."""
    cp = load_checkpoint(project)
    data = load_project_json(project)
    applied: list[str] = []
    for beat in cp.beats:
        if beat.status != "selected" or not beat.selected_path:
            continue
        if beat.qc_method not in ("vision", "manual"):
            continue
        code, _ = run_cli([
            "select", "keyframe",
            "--project", str(project),
            "--seq", beat.seq,
            "--kf", beat.kf,
            "--image", beat.selected_path,
            "--json",
        ])
        if code == 0:
            applied.append(f"{beat.seq}/{beat.kf}")
    return applied


def _find_or_create_beat(cp: PipelineCheckpoint, seq: str, kf: str) -> BeatCheckpoint:
    for b in cp.beats:
        if b.seq == seq and b.kf == kf:
            return b
    beat = BeatCheckpoint(seq=seq, kf=kf)
    cp.beats.append(beat)
    return beat


def phase_keyframes_generate(
    project: Path,
    *,
    max_variants: int = 5,
    force: bool = False,
    vision_qc: VisionQcFn | None = None,
) -> bool:
    """Generate keyframe variants; checkpoint only — no heuristic auto-pick."""
    data = load_project_json(project)
    kfs = keyframe_list(data)
    cp = load_checkpoint(project)
    cp.project_path = str(project)
    cp.phase = "keyframes"

    append_log(project, f"=== KEYFRAMES: {len(kfs)} shots (max {max_variants} variants each) ===")

    for i, (seq, kf, layout) in enumerate(kfs):
        label = f"{seq}/{kf}"
        if not force and keyframe_has_selection(data, seq, kf):
            beat = _find_or_create_beat(cp, seq, kf)
            beat.status = "selected"
            beat.skip_reason = "already selected in project JSON"
            append_log(project, f"SKIP {label} (already selected)")
            continue

        existing = _find_or_create_beat(cp, seq, kf)
        if not force and existing.status == "selected" and existing.selected_path:
            append_log(project, f"SKIP {label} (checkpoint selected)")
            continue

        append_log(project, f"\n--- [{i+1}/{len(kfs)}] {label} ---")
        seed = 20000 + i * 317
        beat = generate_beat_variants(
            project, seq, kf, layout,
            max_variants=max_variants,
            base_seed=seed,
            vision_qc=vision_qc,
        )
        idx = next((j for j, b in enumerate(cp.beats) if b.seq == seq and b.kf == kf), None)
        if idx is not None:
            cp.beats[idx] = beat
        else:
            cp.beats.append(beat)

        if beat.status == "failed":
            append_log(project, f"FAIL no usable variant {label}")
            save_checkpoint(cp, project)
            return False

        ok_count = sum(1 for v in beat.variants if v.success)
        append_log(project, f"GENERATED {label}: {ok_count} variant(s) — pending vision QC")
        save_checkpoint(cp, project)

    return True


def all_beats_selected(project: Path) -> bool:
    data = load_project_json(project)
    for seq, kf, _ in keyframe_list(data):
        if not keyframe_has_selection(data, seq, kf):
            cp = load_checkpoint(project)
            beat = next((b for b in cp.beats if b.seq == seq and b.kf == kf), None)
            if not beat or beat.status != "selected":
                return False
    return True


def launch_detached(script: Path, project: Path, extra_args: list[str] | None = None) -> int:
    """Launch a pipeline script detached from the agent shell."""
    python = str(resolve_agent_python())
    log = log_path(project)
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [python, str(script), *(extra_args or [])]
    append_log(project, f">>> detached: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=open(log, "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if __import__("sys").platform == "win32" else 0,
    )
    append_log(project, f"Detached PID {proc.pid} — tail {log}")
    return proc.pid
