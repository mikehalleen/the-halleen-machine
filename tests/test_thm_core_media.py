"""thm_core.media: hash, probe, thumbnails, adopt (THM phase 3)."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from thm_core import MediaOrigin, adopt_media, ensure_thumbnails, media_type_for, probe_image, probe_video, sha256_file, thumb_paths
from thm_core import media as media_mod
from thm_core_fixtures import make_project


@pytest.fixture(scope="module")
def pngs(tmp_path_factory):
    d = tmp_path_factory.mktemp("png")
    big = d / "big.png"
    Image.new("RGB", (1600, 900), (200, 80, 40)).save(big)
    small = d / "small.png"
    Image.new("RGBA", (64, 64), (10, 200, 90, 128)).save(small)
    return big, small


@pytest.fixture(scope="session")
def tiny_mp4(tmp_path_factory):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    out = tmp_path_factory.mktemp("vid") / "tiny.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=8", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True, timeout=60,
    )
    return out


def _upload(bin_="b_x"):
    return MediaOrigin(type="upload", bin=bin_)


def test_sha256_matches_hashlib_and_is_stable(pngs):
    big, _ = pngs
    expected = hashlib.sha256(big.read_bytes()).hexdigest()
    assert sha256_file(big) == expected
    assert sha256_file(big, chunk=7) == expected


def test_media_type_for_extensions():
    assert media_type_for("a.PNG") == "image"
    assert media_type_for(Path("b.MOV")) == "video"
    assert media_type_for("c.txt") is None
    assert media_type_for("noext") is None
    for ext in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".MP3"):
        assert media_type_for(f"song{ext}") == "audio", ext


def test_probe_image_size(pngs):
    big, small = pngs
    assert probe_image(big) == (1600, 900)
    assert probe_image(small) == (64, 64)


def test_ensure_thumbnails_image_sizes_and_idempotent(tmp_path, pngs):
    big, small = pngs
    p = make_project()
    m = adopt_media(p, big, origin=_upload())
    cache = tmp_path / "thumbs"
    made = ensure_thumbnails(m, cache)
    assert set(made) == {"grid", "detail"}
    assert made["grid"] == cache / f"{m.sha256}_grid.webp"
    with Image.open(made["grid"]) as im:
        assert max(im.size) == 320
    with Image.open(made["detail"]) as im:
        assert max(im.size) == 1280
    stamps = {s: path.stat().st_mtime_ns for s, path in made.items()}
    again = ensure_thumbnails(m, cache)
    assert {s: path.stat().st_mtime_ns for s, path in again.items()} == stamps

    # poster for an image means detail
    only = ensure_thumbnails(m, cache, sizes=("poster",))
    assert list(only) == ["detail"]

    tiny = adopt_media(p, small, origin=_upload())
    made_small = ensure_thumbnails(tiny, cache, sizes=("grid",))
    with Image.open(made_small["grid"]) as im:
        assert im.size == (64, 64)


def test_adopt_media_fills_probe_fields_and_dedupes(tmp_path, pngs):
    big, _ = pngs
    p = make_project()
    m = adopt_media(p, big, origin=_upload("b_a"), now="2026-09-06T12:00:00Z")
    assert (m.type, m.width, m.height, m.created) == ("image", 1600, 900, "2026-09-06T12:00:00Z")
    assert m.sha256 and "\\" not in m.path and m.path.endswith("/big.png")
    assert m.origin.bin == "b_a"
    assert p.media_by_id[m.id] is m

    same = adopt_media(p, big, origin=_upload("b_b"))
    assert same is m and len(p.media_by_id) == 1 and m.origin.bin == "b_a"

    copy = tmp_path / "copy.png"
    copy.write_bytes(big.read_bytes())
    assert adopt_media(p, copy, origin=_upload()) is m
    assert len(p.media_by_id) == 1


def test_adopt_media_missing_file_and_unknown_type(tmp_path):
    p = make_project()
    with pytest.raises(FileNotFoundError):
        adopt_media(p, tmp_path / "nope.png", origin=_upload())
    txt = tmp_path / "notes.txt"
    txt.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        adopt_media(p, txt, origin=_upload())


def test_probe_video_fields(tiny_mp4):
    info = probe_video(tiny_mp4)
    assert (info["width"], info["height"]) == (64, 64)
    assert info["fps"] == 8.0
    assert abs(info["duration_s"] - 1.0) < 0.2
    assert 7 <= info["frames"] <= 9


def test_video_poster_and_thumbs(tmp_path, tiny_mp4):
    p = make_project()
    m = adopt_media(p, tiny_mp4, origin=_upload())
    assert m.type == "video" and m.frames
    cache = tmp_path / "thumbs"
    made = ensure_thumbnails(m, cache)
    assert set(made) == {"grid", "detail", "poster"}
    assert made["poster"].suffix == ".jpg" and made["poster"].stat().st_size > 0
    for s in ("grid", "detail"):
        with Image.open(made[s]) as im:
            assert max(im.size) <= 64
    assert thumb_paths(cache, m.sha256)["poster"] == made["poster"]


def test_probe_video_without_ffprobe(monkeypatch, tmp_path):
    monkeypatch.setattr(media_mod.shutil, "which", lambda name: None)
    assert probe_video(tmp_path / "x.mp4") == {"width": None, "height": None, "duration_s": None, "fps": None, "frames": None}
    assert media_mod.ffprobe_available() is False


def test_ensure_thumbnails_missing_source_raises(tmp_path):
    p = make_project()
    from thm_core import Media
    m = Media(id="m_gone", type="image", path=str(tmp_path / "gone.png"), origin=_upload())
    with pytest.raises(FileNotFoundError):
        ensure_thumbnails(m, tmp_path / "thumbs")
