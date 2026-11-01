"""thm_server media: pagination, Range file serving, thumbnails, posters."""

import json
from pathlib import Path

import pytest

from thm_core import MediaOrigin, adopt_media
from thm_server_fixtures import make_tiny_audio, make_tiny_mp4, seeded_world


@pytest.fixture
def world(tmp_path):
    return seeded_world(tmp_path)


def test_media_pagination_and_filters(world):
    base = f"/api/projects/{world.slug}/media"
    page = world.client.get(base, params={"limit": 2}).json()
    # newest first: the take landed after both uploads
    assert [m["id"] for m in page["items"]] == [world.take, world.m2] and page["next_cursor"] == "2" and page["total"] == 3
    rest = world.client.get(base, params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [m["id"] for m in rest["items"]] == [world.m1] and rest["next_cursor"] is None
    by_bin = world.client.get(base, params={"bin": world.mara}).json()
    assert [m["id"] for m in by_bin["items"]] == [world.m2, world.m1]
    assert world.client.get(base, params={"type": "video"}).json()["total"] == 0
    assert world.client.get(base, params={"bin": "b_nope"}).status_code == 404
    assert world.client.get(base, params={"cursor": "x"}).status_code == 422
    assert world.client.get(base, params={"limit": 0}).status_code == 422
    one = world.client.get(f"{base}/{world.m1}").json()
    assert one["id"] == world.m1 and one["width"] == 640 and one["sha256"]


def test_file_range_returns_206(world):
    url = f"/media/{world.slug}/{world.m1}/file"
    full = world.client.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "image/png"
    assert full.headers.get("accept-ranges") == "bytes"
    r = world.client.get(url, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-99/")
    assert len(r.content) == 100
    assert r.headers["etag"].strip('"') == full.headers["etag"].strip('"')
    assert "immutable" in r.headers["cache-control"]
    assert r.headers["content-disposition"].startswith("inline")


def test_file_missing_404(world):
    project = world.store.load(world.slug)
    project.media_by_id[world.m2].path = str(world.media_dir / "gone.png")
    world.store.save(project)
    world.registry.invalidate(world.slug)
    r = world.client.get(f"/media/{world.slug}/{world.m2}/file")
    assert r.status_code == 404 and r.json()["code"] == "file_missing"
    r = world.client.get(f"/media/{world.slug}/{world.m2}/thumb")
    assert r.status_code == 404 and r.json()["code"] == "file_missing"
    assert world.client.get(f"/media/{world.slug}/m_nope/file").status_code == 404


def test_thumb_creates_cache_file_webp(world):
    r = world.client.get(f"/media/{world.slug}/{world.m1}/thumb", params={"size": "grid"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    sha = world.client.get(f"/api/projects/{world.slug}/media/{world.m1}").json()["sha256"]
    cache = Path(world.settings.projects_dir) / f"{world.slug}-files" / "cache" / "thumbs"
    assert (cache / f"{sha}_grid.webp").is_file()
    r = world.client.get(f"/media/{world.slug}/{world.m1}/thumb", params={"size": "detail"})
    assert r.status_code == 200 and (cache / f"{sha}_detail.webp").is_file()
    r = world.client.get(f"/media/{world.slug}/{world.m1}/thumb", params={"size": "poster"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert world.client.get(f"/media/{world.slug}/{world.m1}/thumb", params={"size": "bogus"}).status_code == 422


def test_thumb_persists_missing_sha(world):
    project = world.store.load(world.slug)
    project.media_by_id[world.m1].sha256 = None
    world.store.save(project)
    world.registry.invalidate(world.slug)
    r = world.client.get(f"/media/{world.slug}/{world.m1}/thumb")
    assert r.status_code == 200
    raw = json.loads(world.store.project_path(world.slug).read_text(encoding="utf-8"))
    assert raw["media_by_id"][world.m1]["sha256"]


def test_thumb_video_poster(world, tmp_path):
    clip = make_tiny_mp4(tmp_path / "clips" / "tiny.mp4")
    if clip is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    project = world.store.load(world.slug)
    m = adopt_media(project, clip, origin=MediaOrigin(type="upload", bin=world.mara))
    project.bins_by_id[world.mara].media.append(m.id)
    world.store.save(project)
    world.registry.invalidate(world.slug)
    r = world.client.get(f"/media/{world.slug}/{m.id}/thumb", params={"size": "poster"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    r = world.client.get(f"/media/{world.slug}/{m.id}/thumb", params={"size": "grid"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    r = world.client.get(f"/media/{world.slug}/{m.id}/file", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"
    assert world.client.get(f"/api/projects/{world.slug}/media", params={"type": "video"}).json()["total"] == 1


def test_audio_upload_plays_seeks_and_has_a_waveform_tile(world, tmp_path):
    song = make_tiny_audio(tmp_path / "song.wav", seconds=2.0)
    if song is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    # the same path a browser or `cli.py upload` takes: multipart into a named bin
    with open(song, "rb") as f:
        r = world.client.post(f"/api/projects/{world.slug}/uploads", files={"file": ("song.wav", f, "audio/wav")}, data={"bin_name": "Music"})
    assert r.status_code == 200, r.text
    body = r.json()
    media = body["media"]
    assert media["type"] == "audio" and media["sample_rate"] == 44100 and media["channels"] == 1
    assert media["width"] is None and media["fps"] is None and abs(media["duration_s"] - 2.0) < 0.1
    assert body["bin"]["name"] == "Music" and media["id"] in body["bin"]["media"]
    # served with its mime type, seekable by Range
    url = f"/media/{world.slug}/{media['id']}/file"
    full = world.client.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "audio/wav" and full.headers.get("accept-ranges") == "bytes"
    part = world.client.get(url, headers={"Range": "bytes=100-199"})
    assert part.status_code == 206 and len(part.content) == 100
    # the tile is a waveform: grid square, detail a wide strip, poster = detail
    for size, (w, h) in (("grid", (320, 320)), ("detail", (1280, 256)), ("poster", (1280, 256))):
        t = world.client.get(f"/media/{world.slug}/{media['id']}/thumb", params={"size": size})
        assert t.status_code == 200 and t.headers["content-type"] == "image/webp", size
        from io import BytesIO

        from PIL import Image

        assert Image.open(BytesIO(t.content)).size == (w, h), size
    # no frames of an audio file, by code
    r = world.client.get(f"/media/{world.slug}/{media['id']}/frames")
    assert r.status_code == 422 and r.json()["code"] == "not_video"
    # it survives a reload from disk with its probed fields
    world.registry.invalidate(world.slug)
    again = world.client.get(f"/api/projects/{world.slug}/media/{media['id']}").json()
    assert again["type"] == "audio" and again["sample_rate"] == 44100
    assert world.client.get(f"/api/projects/{world.slug}/media", params={"type": "audio"}).json()["total"] == 1
