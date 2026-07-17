"""Tests for thm-agent client (prep, discover, runner mocks)."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_AGENT_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _AGENT_DIR.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

for _mod in ("gradio", "gradio.components", "PIL", "PIL.Image", "PIL.PngImagePlugin"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import builder  # noqa: E402
from client import prep, runner  # noqa: E402
from client import discover  # noqa: E402


def test_prep_keyframe_isolates_sequence():
    data = builder.create_blank("prep-test")
    sid = data["sequence_order"][0]
    kf_id = data["sequences"][sid]["keyframe_order"][0]
    data["sequences"][sid]["keyframes"][kf_id]["layout"] = "wide shot test"

    temp = prep.prep_keyframe_run(data, sid, kf_id, seed=42)
    assert list(temp["sequences"].keys()) == [sid]
    seq = temp["sequences"][sid]
    assert list(seq["keyframes"].keys()) == [kf_id]
    assert seq["keyframes"][kf_id]["force_generate"] is True
    assert seq["keyframes"][kf_id]["image_iterations_override"] == 1
    assert seq["videos"] == {}
    assert temp["project"]["keyframe_generation"]["sampler_seed_start"] == 42


def test_prep_video_prunes_to_one_clip():
    data = builder.create_blank("vid-prep")
    data, char_id = builder.add_character(data, "Hero", "person")
    beats = [
        builder.BeatSpec("wide entrance", "walks in", 4, (char_id, "")),
        builder.BeatSpec("close at counter", "places item", 4, (char_id, "")),
    ]
    data, seq_id = builder.build_narrative_sequence(
        data, beats=beats, seq_id=data["sequence_order"][0]
    )
    seq = data["sequences"][seq_id]
    vid_id = seq["video_order"][0]
    kf_ids = seq["keyframe_order"]
    seq["keyframes"][kf_ids[0]]["selected_image_path"] = "/fake/start.png"
    seq["keyframes"][kf_ids[1]]["selected_image_path"] = "/fake/end.png"

    temp = prep.prep_video_run(data, seq_id, vid_id, seed=99)
    assert list(temp["sequences"].keys()) == [seq_id]
    tseq = temp["sequences"][seq_id]
    assert list(tseq["videos"].keys()) == [vid_id]
    assert tseq["videos"][vid_id]["force_generate"] is True
    assert len(tseq["keyframes"]) <= 2


def test_prep_asset_character():
    data = builder.create_blank("char-asset")
    data, char_id = builder.add_character(data, "Shopper", "casual clothes")
    temp, seq_id, kf_id = prep.prep_asset_run(data, "character", char_id, seed=1)
    assert temp["project"]["name"] == "__test_cache_character__"
    assert len(temp["project"]["characters"]) == 1
    assert seq_id == kf_id
    assert temp["sequences"][seq_id]["keyframes"][kf_id]["force_generate"] is True


def test_prep_asset_layout_override():
    data = builder.create_blank("layout-asset")
    data, setting_id = builder.add_setting(data, "Shelf", "grocery aisle")
    custom_layout = "straight-on product shelf, eye level"
    temp, seq_id, kf_id = prep.prep_asset_run(
        data, "setting", setting_id, seed=1, layout_override=custom_layout
    )
    layout = temp["sequences"][seq_id]["keyframes"][kf_id]["layout"]
    assert custom_layout in layout


def test_list_workflows_non_empty():
    workflows = discover.list_workflows()
    assert isinstance(workflows, list)
    assert any(w.endswith(".json") for w in workflows)


def test_validate_video_prerequisites_missing_selection():
    data = builder.create_blank("vid-pre")
    data, char_id = builder.add_character(data, "Hero", "person")
    beats = [builder.BeatSpec("wide", "walks", 4, (char_id, ""))]
    data, seq_id = builder.build_narrative_sequence(
        data, beats=beats, seq_id=data["sequence_order"][0]
    )
    vid_id = data["sequences"][seq_id]["video_order"][0]
    issues = runner.validate_video_prerequisites(data, seq_id, vid_id)
    assert issues


def test_mirror_generation_output(tmp_path, monkeypatch):
    from client import workspace as ws

    monkeypatch.setattr(ws, "WORKSPACE_ROOT", tmp_path / "workspace")
    src = tmp_path / "out.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    mirrored = ws.mirror_generation_output(
        src, project_name="ugc-test", label="assets/character/abc", seed=1000
    )
    assert mirrored
    assert Path(mirrored).is_file()
    assert "ugc-test" in mirrored
    assert "assets" in mirrored
    assert mirrored.endswith("_seed1000.png")


def test_clear_preview_dir(tmp_path, monkeypatch):
    from client import workspace as ws

    monkeypatch.setattr(ws, "_resolve_configured_workspace_root", lambda: tmp_path)
    preview_dir = ws.preview_dir_for_project("ugc-test")
    preview_dir.mkdir(parents=True)
    (preview_dir / "creator-A.png").write_bytes(b"\x89PNG\r\n\x1a\nA")
    (preview_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (preview_dir / "launcher.html").write_text("<html>launcher</html>", encoding="utf-8")
    (preview_dir / "session.json").write_text("{}", encoding="utf-8")

    deleted = ws.clear_preview_dir("ugc-test")
    assert len(deleted) == 3
    assert not (preview_dir / "creator-A.png").exists()
    assert not (preview_dir / "index.html").exists()
    assert not (preview_dir / "session.json").exists()
    assert (preview_dir / "launcher.html").exists()


def test_build_preview_gallery(tmp_path, monkeypatch):
    from client import workspace as ws

    monkeypatch.setattr(ws, "_resolve_configured_workspace_root", lambda: tmp_path)
    preview_dir = ws.preview_dir_for_project("ugc-test")
    preview_dir.mkdir(parents=True)
    (preview_dir / "creator-A.png").write_bytes(b"\x89PNG\r\n\x1a\nA")
    (preview_dir / "creator-B.png").write_bytes(b"\x89PNG\r\n\x1a\nB")

    index_path = ws.build_preview_gallery("ugc-test")
    html_text = Path(index_path).read_text(encoding="utf-8")
    assert Path(index_path).is_file()
    assert "creator-A.png" in html_text
    assert "creator-B.png" in html_text
    assert 'window.name = "thm-gallery-ugc-test"' in html_text
    assert "#ff7c00" in html_text
    assert "Reviewing now" not in html_text
    assert "item-label" not in html_text


def test_gallery_window_name():
    from client import workspace as ws

    assert ws.gallery_window_name("ugc-skincare/lotion") == "thm-gallery-ugc-skincare_lotion"


def test_write_gallery_launcher(tmp_path, monkeypatch):
    from client import workspace as ws

    monkeypatch.setattr(ws, "_resolve_configured_workspace_root", lambda: tmp_path)
    launcher = ws.write_gallery_launcher("ugc-test")
    text = launcher.read_text(encoding="utf-8")
    assert launcher.name == "launcher.html"
    assert "window.open('index.html', 'thm-gallery-ugc-test')" in text
    assert "window.close()" in text


def test_add_preview_image(tmp_path, monkeypatch):
    from client import workspace as ws

    monkeypatch.setattr(ws, "_resolve_configured_workspace_root", lambda: tmp_path)
    src = tmp_path / "long_mirror_name_seed1000.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    copied = ws.add_preview_image("ugc-test", src, "creator-A.png", note="grey crewneck")
    assert copied
    assert Path(copied).name == "creator-A.png"
    assert "ugc-skincare-lotion-files" not in copied
    assert copied.endswith("ugc-test-files\\previews\\creator-A.png") or copied.endswith(
        "ugc-test-files/previews/creator-A.png"
    )
    session = ws.load_gallery_session("ugc-test")
    assert session["items"]["creator-A.png"]["note"] == "grey crewneck"


def test_gallery_recency_and_session_notes(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from client import workspace as ws

    monkeypatch.setattr(ws, "_resolve_configured_workspace_root", lambda: tmp_path)
    preview_dir = ws.preview_dir_for_project("ugc-test")
    preview_dir.mkdir(parents=True)

    (preview_dir / "keyframe-shot1.png").write_bytes(b"\x89PNG\r\n\x1a\nkf")
    (preview_dir / "video-shot1.mp4").write_bytes(b"fake-mp4-a")
    (preview_dir / "video-shot2.mp4").write_bytes(b"fake-mp4-b")

    now = datetime.now(timezone.utc)
    session = ws.load_gallery_session("ugc-test")
    session["pending"] = "Pick best video motion before locking keyframe"
    session["change"] = "Latest keyframe: one hand holding phone"
    session["groups"] = {"video": {"note": "Lip movement forbidden in all clips"}}
    session["items"] = {
        "keyframe-shot1.png": {
            "note": "One hand on phone - recent correction",
            "added_at": (now - timedelta(hours=2)).isoformat(),
        },
        "video-shot1.mp4": {"added_at": (now - timedelta(minutes=1)).isoformat()},
        "video-shot2.mp4": {"added_at": now.isoformat()},
    }
    ws.save_gallery_session("ugc-test", session)

    index_path = ws.build_preview_gallery("ugc-test")
    html_text = Path(index_path).read_text(encoding="utf-8")

    assert "Reviewing now" not in html_text
    assert "item-note" not in html_text
    assert "Latest</span>" not in html_text
    assert "shot2" in html_text
    assert html_text.index("shot2") < html_text.index("shot1")


def test_gallery_header_branding_and_favicon_always_present(tmp_path, monkeypatch):
    from client import workspace as ws

    monkeypatch.setattr(ws, "_resolve_configured_workspace_root", lambda: tmp_path)
    preview_dir = ws.preview_dir_for_project("brand-test")
    preview_dir.mkdir(parents=True)
    (preview_dir / "seq1-id1-take1.png").write_bytes(b"\x89PNG\r\n\x1a\nkf")
    # Stale copy from an older build — must be cleaned up, not shown as a tile.
    (preview_dir / "icon.png").write_bytes(b"stale")

    html_text = Path(ws.build_preview_gallery("brand-test")).read_text(encoding="utf-8")
    # Branded header is non-optional (a past run dropped it in monitor mode).
    assert "The Halleen Machine</span>" in html_text
    assert 'class="thm-wordmark"' in html_text
    assert 'class="thm-logo"' in html_text
    assert '<link rel="icon"' in html_text
    # Logo is inlined as a data URI; nothing is copied into the project folder.
    assert "data:image/png;base64," in html_text
    assert not (preview_dir / "icon.png").exists()


def test_gallery_complete_shows_final_cut_and_slower_refresh(tmp_path, monkeypatch):
    from client import workspace as ws

    monkeypatch.setattr(ws, "_resolve_configured_workspace_root", lambda: tmp_path)
    preview_dir = ws.preview_dir_for_project("done-test")
    preview_dir.mkdir(parents=True)
    (preview_dir / "seq1-id1-take1.mp4").write_bytes(b"clip")
    (preview_dir / "final-cut.mp4").write_bytes(b"stitched")

    # Active run: 30s refresh, no final-cut section.
    session = ws.load_gallery_session("done-test")
    session["monitor"] = {"complete": False}
    ws.save_gallery_session("done-test", session)
    active = Path(ws.build_preview_gallery("done-test")).read_text(encoding="utf-8")
    assert "30000" in active and "60000" not in active
    assert 'class="final-cut"' not in active

    # Completed run: 60s refresh, stitched final cut featured.
    session = ws.load_gallery_session("done-test")
    session["monitor"] = {"complete": True, "final_export": "final-cut.mp4"}
    ws.save_gallery_session("done-test", session)
    done = Path(ws.build_preview_gallery("done-test")).read_text(encoding="utf-8")
    assert "60000" in done and "30000" not in done
    assert 'class="final-cut"' in done
    assert "final-cut.mp4" in done


def test_gallery_long_hero_video_slows_refresh_to_play_through(tmp_path, monkeypatch):
    from client import workspace as ws

    monkeypatch.setattr(ws, "_resolve_configured_workspace_root", lambda: tmp_path)
    preview_dir = ws.preview_dir_for_project("long-hero-test")
    preview_dir.mkdir(parents=True)
    (preview_dir / "seq1-vid1-take1.mp4").write_bytes(b"clip")
    (preview_dir / "final-cut.mp4").write_bytes(b"stitched")

    # Hero video > 28s: refresh becomes length + 2s so it plays through once.
    monkeypatch.setattr(ws, "_video_duration_sec", lambda p: 45.0)
    session = ws.load_gallery_session("long-hero-test")
    session["monitor"] = {"complete": True, "final_export": "final-cut.mp4"}
    ws.save_gallery_session("long-hero-test", session)
    done = Path(ws.build_preview_gallery("long-hero-test")).read_text(encoding="utf-8")
    assert "47000" in done and "60000" not in done

    # Applies to the newest-item hero during an active run too.
    session = ws.load_gallery_session("long-hero-test")
    session["monitor"] = {"complete": False}
    ws.save_gallery_session("long-hero-test", session)
    active = Path(ws.build_preview_gallery("long-hero-test")).read_text(encoding="utf-8")
    assert "47000" in active and "30000" not in active

    # Short hero video: default cadence stands.
    monkeypatch.setattr(ws, "_video_duration_sec", lambda p: 5.0)
    short = Path(ws.build_preview_gallery("long-hero-test")).read_text(encoding="utf-8")
    assert "30000" in short and "47000" not in short


def test_format_item_label_seq_id_take():
    from client import workspace as ws

    assert ws._format_item_label("seq3-id7-take4") == "seq3 · id7 · take 4"
    assert ws._format_item_label("seq12-vid2-take10") == "seq12 · vid2 · take 10"
    # real ComfyUI output stem: {proj}_{seq}_{id}_{index}
    assert ws._format_item_label("california-home_seq5_id2_00004_") == "seq5 · id2 · take 4"
    # non-conforming (invented) names fall back to the raw stem
    assert ws._format_item_label("vid-tight-womanportrait-A") == "vid-tight-womanportrait-A"


def test_stitch_mp4_fallback_extracts_from_mp4(tmp_path, monkeypatch):
    scripts_dir = _REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import run_stitch

    vid_dir = tmp_path / "seq1" / "vid1"
    vid_dir.mkdir(parents=True)
    mp4 = vid_dir / "proj_seq1_vid1_00003_.mp4"
    mp4.write_bytes(b"fake-mp4")  # no _lossless.mkv, no frames_00003/ folder

    calls: dict = {}

    def fake_extract(src, temp_dir):
        calls["src"] = Path(src)
        Path(temp_dir).mkdir(parents=True, exist_ok=True)
        return Path(temp_dir)

    monkeypatch.setattr(run_stitch, "extract_frames_to_temp", fake_extract)
    result = run_stitch.frames_dir_from_selected_video_path(str(mp4))
    # Partner-clip fallback: frames extracted straight from the MP4 itself.
    assert calls.get("src") == mp4
    assert result is not None and Path(result).is_dir()


@patch("client.runner._run_script")
def test_run_keyframe_collects_output(mock_run):
    data = builder.create_blank("run-kf")
    sid = data["sequence_order"][0]
    kf_id = data["sequences"][sid]["keyframe_order"][0]
    data["sequences"][sid]["keyframes"][kf_id]["layout"] = "test layout"
    data["project"]["comfy"]["output_root"] = str(Path(__file__).parent / "_fixtures_out")
    data["project"]["name"] = "run-kf"

    out_dir = Path(data["project"]["comfy"]["output_root"]) / "run-kf" / sid / kf_id
    out_dir.mkdir(parents=True, exist_ok=True)
    fake_png = out_dir / "run-kf_seq1_id1_001.png"

    def _side_effect(script_name, temp_data):
        # A real render writes the output file during the run (mtime > submitted_at).
        fake_png.write_bytes(b"\x89PNG\r\n\x1a\n")
        return (0, "RESULT: /out/test.png\n", ["RESULT: /out/test.png"])

    mock_run.side_effect = _side_effect

    result = runner.run_keyframe(data, sid, kf_id)
    assert result.success
    assert result.is_new_output
    assert result.main_path
    mock_run.assert_called_once()


@patch("client.runner._run_script")
def test_run_keyframe_rejects_stale_output(mock_run):
    # A pre-existing file that predates the run must NOT count as this run's output.
    mock_run.return_value = (0, "", [])
    data = builder.create_blank("stale-kf")
    sid = data["sequence_order"][0]
    kf_id = data["sequences"][sid]["keyframe_order"][0]
    data["project"]["comfy"]["output_root"] = str(Path(__file__).parent / "_fixtures_out")
    data["project"]["name"] = "stale-kf"

    stale_root = Path(data["project"]["comfy"]["output_root"]) / "stale-kf"
    out_dir = stale_root / sid / kf_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stale_png = out_dir / "stale-kf_seq1_id1_001.png"
    stale_png.write_bytes(b"\x89PNG\r\n\x1a\n")
    old = time.time() - 3600
    os.utime(stale_png, (old, old))  # newest file predates the run

    try:
        result = runner.run_keyframe(data, sid, kf_id)
        assert result.success is False
        assert result.is_new_output is False
        assert result.main_path is None
        assert "stale_output" in result.log
    finally:
        shutil.rmtree(stale_root, ignore_errors=True)
