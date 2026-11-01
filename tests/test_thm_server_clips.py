"""thm_server.routes.clips: cut a segment or lift a still frame from an existing video."""

from pathlib import Path

import pytest

from thm_core import MediaOrigin, adopt_media
from thm_server_fixtures import make_tiny_mp4, seeded_world


@pytest.fixture
def world(tmp_path):
    w = seeded_world(tmp_path)
    video_path = make_tiny_mp4(w.media_dir / "clip_source.mp4")
    if video_path is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    project = w.registry.get(w.slug)
    media = adopt_media(project, video_path, origin=MediaOrigin(type="upload", bin=w.mara))
    w.store.save(project)
    w.registry.invalidate(w.slug)
    w.video_media = media.id
    return w


def test_segment_clip_lands_in_a_new_bin(world):
    r = world.client.post(
        f"/api/projects/{world.slug}/clips",
        json={"source": world.video_media, "mode": "segment", "start_s": 0.1, "end_s": 0.6, "bin_name": "Clips"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bin"]["name"] == "Clips" and body["bin"]["selected"] == body["media"]["id"]
    assert body["media"]["type"] == "video"
    assert Path(body["media"]["path"]).is_file()
    assert body["media"]["origin"]["type"] == "clip"
    assert body["media"]["origin"]["source"] == world.video_media
    assert body["change"]["command"] == "add_media_to_bin"


def test_clip_without_a_bin_mints_the_next_bin(world):
    r = world.client.post(f"/api/projects/{world.slug}/clips", json={"source": world.video_media, "mode": "still", "at_s": 0.2})
    assert r.status_code == 200, r.text
    assert r.json()["bin"]["name"] == "bin-1"


def test_still_clip_lands_into_an_existing_bin(world):
    r = world.client.post(
        f"/api/projects/{world.slug}/clips", json={"source": world.video_media, "mode": "still", "at_s": 0.2, "bin": world.mara}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bin"]["id"] == world.mara
    assert body["media"]["type"] == "image"
    assert body["media"]["origin"]["type"] == "frame" and body["media"]["origin"]["at_s"] == 0.2


def test_still_at_the_very_end_takes_the_last_frame(world):
    """The frame slider's far end is the duration itself, where ffmpeg decodes nothing; the
    route asks for the last frame instead of crashing on a file that was never written."""
    duration = world.registry.get(world.slug).media_by_id[world.video_media].duration_s
    r = world.client.post(f"/api/projects/{world.slug}/clips", json={"source": world.video_media, "mode": "still", "at_s": duration, "bin": world.mara})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["media"]["type"] == "image" and Path(body["media"]["path"]).is_file()
    assert body["media"]["origin"]["at_s"] < duration
    assert body["media"]["id"] in world.client.get(f"/api/projects/{world.slug}/bins/{world.mara}").json()["bin"]["media"]


def test_clip_refuses_non_video_source(world):
    r = world.client.post(f"/api/projects/{world.slug}/clips", json={"source": world.m1, "mode": "still", "at_s": 0})
    assert r.status_code == 422 and r.json()["code"] == "bad_value"


def test_clip_refuses_bad_time_range(world):
    base = f"/api/projects/{world.slug}/clips"
    r = world.client.post(base, json={"source": world.video_media, "mode": "segment", "start_s": 0.5, "end_s": 0.5})
    assert r.status_code == 422 and r.json()["code"] == "bad_value"
    r = world.client.post(base, json={"source": world.video_media, "mode": "still", "at_s": -1})
    assert r.status_code == 422 and r.json()["code"] == "bad_value"


def test_clip_refuses_past_the_sources_duration(world):
    r = world.client.post(f"/api/projects/{world.slug}/clips", json={"source": world.video_media, "mode": "still", "at_s": 999})
    assert r.status_code == 422 and r.json()["code"] == "bad_value"


def test_clip_refuses_unknown_source(world):
    r = world.client.post(f"/api/projects/{world.slug}/clips", json={"source": "m_nope", "mode": "still", "at_s": 0})
    assert r.status_code == 404 and r.json()["code"] == "unknown_id"


def test_clip_refuses_without_ffmpeg(world, monkeypatch):
    import thm_server.routes.clips as clips_mod

    monkeypatch.setattr(clips_mod, "ffmpeg_available", lambda: False)
    r = world.client.post(f"/api/projects/{world.slug}/clips", json={"source": world.video_media, "mode": "still", "at_s": 0})
    assert r.status_code == 409 and r.json()["code"] == "ffmpeg_unavailable"
