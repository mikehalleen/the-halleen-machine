"""Tests for thm-agent pipeline module."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_AGENT_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _AGENT_DIR.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import pipeline  # noqa: E402
from pipeline import BeatCheckpoint, PipelineCheckpoint, VariantRecord  # noqa: E402


def test_checkpoint_round_trip(tmp_path):
    project = tmp_path / "demo.json"
    project.write_text("{}", encoding="utf-8")
    cp = PipelineCheckpoint(
        project_path=str(project),
        beats=[
            BeatCheckpoint(
                seq="seq1",
                kf="id1",
                layout="wide shot",
                status="generated",
                variants=[VariantRecord(index=1, success=True, main_path="/a.png")],
            )
        ],
    )
    with patch.object(pipeline, "checkpoint_path", return_value=tmp_path / "cp.json"):
        pipeline.save_checkpoint(cp, project)
        loaded = pipeline.load_checkpoint(project)
    assert loaded.beats[0].seq == "seq1"
    assert loaded.beats[0].variants[0].main_path == "/a.png"


def test_generate_beat_variants_early_stop_with_vision(tmp_path):
    project = tmp_path / "proj.json"
    project.write_text("{}", encoding="utf-8")
    calls = {"n": 0}

    def fake_gen(project, seq, kf, *, seed, variant_index):
        calls["n"] += 1
        return VariantRecord(index=variant_index, success=True, main_path=f"/v{variant_index}.png")

    def vision_pass(record, layout):
        return record.index >= 2

    with patch.object(pipeline, "generate_single_variant", side_effect=fake_gen):
        beat = pipeline.generate_beat_variants(
            project, "seq1", "id1", "layout",
            max_variants=5,
            vision_qc=vision_pass,
            min_first_variant=2,
        )
    assert calls["n"] == 2
    assert len(beat.variants) == 2


def test_record_vision_selection(tmp_path):
    project = tmp_path / "proj.json"
    project.write_text("{}", encoding="utf-8")
    cp_path = tmp_path / "cp.json"
    with patch.object(pipeline, "checkpoint_path", return_value=cp_path):
        beat = pipeline.record_vision_selection(
            project, "seq1", "id1", "/picked.png", rationale="intent match",
        )
    assert beat.qc_method == "vision"
    assert beat.selected_path == "/picked.png"
    data = json.loads(cp_path.read_text(encoding="utf-8"))
    assert data["beats"][0]["status"] == "selected"
