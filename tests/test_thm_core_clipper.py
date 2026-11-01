"""thm_core.clipper: ffmpeg segment/still extraction. Skips when ffmpeg/ffprobe are missing."""

import pytest

from thm_core import extract_segment, extract_still
from thm_core.media import probe_image, probe_video
from thm_server_fixtures import make_tiny_mp4


@pytest.fixture
def source_video(tmp_path):
    path = make_tiny_mp4(tmp_path / "source.mp4")
    if path is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    return path


def test_extract_segment_cuts_a_playable_clip(source_video, tmp_path):
    dest = tmp_path / "out" / "clip.mp4"
    extract_segment(str(source_video), 0.1, 0.6, dest)
    assert dest.is_file() and dest.stat().st_size > 0
    info = probe_video(dest)
    assert info["duration_s"] is not None and 0.3 <= info["duration_s"] <= 0.7


def test_extract_still_lifts_a_frame(source_video, tmp_path):
    dest = tmp_path / "out" / "frame.png"
    extract_still(str(source_video), 0.2, dest)
    assert dest.is_file() and dest.stat().st_size > 0
    w, h = probe_image(dest)
    assert w == 64 and h == 64


def test_extract_segment_without_ffmpeg_raises(tmp_path, monkeypatch):
    import thm_core.clipper as clipper_mod

    monkeypatch.setattr(clipper_mod.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        extract_segment("nope.mp4", 0, 1, tmp_path / "x.mp4")
    with pytest.raises(RuntimeError, match="ffmpeg"):
        extract_still("nope.mp4", 0, tmp_path / "x.png")
