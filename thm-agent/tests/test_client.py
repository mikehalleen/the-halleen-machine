"""Tests for thm-agent client (prep, discover, runner mocks)."""

from __future__ import annotations

import sys
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


@patch("client.runner._run_script")
def test_run_keyframe_collects_output(mock_run):
    mock_run.return_value = (0, "RESULT: /out/test.png\n", ["RESULT: /out/test.png"])
    data = builder.create_blank("run-kf")
    sid = data["sequence_order"][0]
    kf_id = data["sequences"][sid]["keyframe_order"][0]
    data["sequences"][sid]["keyframes"][kf_id]["layout"] = "test layout"
    data["project"]["comfy"]["output_root"] = str(Path(__file__).parent / "_fixtures_out")
    data["project"]["name"] = "run-kf"

    out_dir = Path(data["project"]["comfy"]["output_root"]) / "run-kf" / sid / kf_id
    out_dir.mkdir(parents=True, exist_ok=True)
    fake_png = out_dir / "run-kf_seq1_id1_001.png"
    fake_png.write_bytes(b"\x89PNG\r\n\x1a\n")

    result = runner.run_keyframe(data, sid, kf_id)
    assert result.success
    assert result.main_path
    mock_run.assert_called_once()
