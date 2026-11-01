"""Audio as a media type: probe, waveform thumbnails, adopt, and the places that refuse it
(a sequence item, a generation reference)."""

import shutil

import pytest
from PIL import Image

from thm_core import CommandRefused, MediaOrigin, adopt_media, ensure_thumbnails, execute, probe_audio
from thm_core.commands import AddSequenceItems, BindReference, CreateSequence, RebindReference, StartFromMedia, item_duration, item_kind
from thm_core.media import WAVE_SIZES, thumb_paths
from thm_core.stitch import build_plan
from thm_core_fixtures import USER, add_bin, add_media, add_take, bind, make_env, make_project, make_store
from thm_server_fixtures import make_tiny_audio

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture
def song(tmp_path):
    out = make_tiny_audio(tmp_path / "song.wav", seconds=1.5)
    if out is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    return out


def refused(code, fn):
    with pytest.raises(CommandRefused) as exc:
        fn()
    assert exc.value.code == code, f"expected {code}, got {exc.value.code}: {exc.value.detail}"
    return exc.value


# -- probe, thumbnails, adopt ----------------------------------------------------


def test_probe_audio_fields(song):
    out = probe_audio(song)
    assert out["sample_rate"] == 44100 and out["channels"] == 1
    assert out["duration_s"] is not None and abs(out["duration_s"] - 1.5) < 0.1


def test_probe_audio_without_ffprobe(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert probe_audio(tmp_path / "x.wav") == {"duration_s": None, "sample_rate": None, "channels": None}


def test_adopt_audio_fills_probe_fields_and_no_picture_fields(song):
    p = make_project()
    b = add_bin(p, "Music", workflow="")
    m = adopt_media(p, song, origin=MediaOrigin(type="upload", bin=b))
    assert m.type == "audio" and m.sample_rate == 44100 and m.channels == 1
    assert m.duration_s and abs(m.duration_s - 1.5) < 0.1
    assert m.width is None and m.height is None and m.fps is None and m.frames is None
    # the same bytes again dedupe to the same record
    assert adopt_media(p, song, origin=MediaOrigin(type="upload", bin=b)) is m


def test_audio_thumbnails_are_waveforms(song, tmp_path):
    p = make_project()
    b = add_bin(p, "Music", workflow="")
    m = adopt_media(p, song, origin=MediaOrigin(type="upload", bin=b))
    cache = tmp_path / "cache"
    made = ensure_thumbnails(m, cache)
    paths = thumb_paths(cache, m.sha256)
    # grid and detail are webp waveforms; poster means detail, as for an image
    assert set(made) == {"grid", "detail"}
    assert paths["grid"].is_file() and paths["detail"].is_file() and not paths["poster"].is_file()
    with Image.open(paths["grid"]) as im:
        assert im.size == WAVE_SIZES["grid"] and im.format == "WEBP"
        px = list(im.convert("RGB").getdata())
    with Image.open(paths["detail"]) as im:
        assert im.size == WAVE_SIZES["detail"]
    # a sine fills the middle band in the accent colour over the dark surface: both are present
    # (webp is lossy, so the surface is read as "dark and neutral", not as an exact triple)
    assert any(max(pxl) < 48 and max(pxl) - min(pxl) < 12 for pxl in px)
    assert any(pxl[2] > 200 and pxl[0] < 120 for pxl in px)
    # idempotent: a second call makes nothing new and asks for the same files
    before = {k: v.stat().st_mtime_ns for k, v in paths.items() if v.is_file()}
    ensure_thumbnails(m, cache, sizes=("poster",))
    assert {k: v.stat().st_mtime_ns for k, v in paths.items() if v.is_file()} == before


# -- refusals -------------------------------------------------------------------


@pytest.fixture
def world(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path)
    return store, p, env


def audio_bin(p, name="Music"):
    b = add_bin(p, name, workflow="")
    m = add_media(p, b, type="audio", path="C:/fake/song.mp3")
    p.bins_by_id[b].selected = m
    return b, m


def test_an_audio_bin_is_refused_as_a_sequence_item(world):
    store, p, env = world
    b, m = audio_bin(p)
    seq = execute(store, p, CreateSequence(sequence_name="Rough"), USER, env=env).sequence
    exc = refused("audio_item", lambda: execute(store, p, AddSequenceItems(sequence=seq, bins=[b]), USER, env=env))
    assert exc.extra == {"type": "bin", "id": b, "media": m}
    assert p.sequences[0].items == []


def test_a_bin_whose_current_take_turns_to_audio_plays_as_a_title_card(world, tmp_path):
    store, p, env = world
    b = add_bin(p, "Shot", workflow="vid.json", length_s=4.0)
    clip = add_take(p, b, type="video")
    p.media_by_id[clip].duration_s = 6.0
    seq = execute(store, p, CreateSequence(sequence_name="Rough"), USER, env=env).sequence
    execute(store, p, AddSequenceItems(sequence=seq, bins=[b]), USER, env=env)
    item = p.sequences[0].items[0]
    assert item_kind(p, item) == "video" and item_duration(p, item) == 6.0
    # an mp3 dropped into the bin and made current: the item is no longer a picture
    song = add_media(p, b, type="audio", path="C:/fake/song.mp3")
    p.bins_by_id[b].selected = song
    assert item_kind(p, item) == "title" and item_duration(p, item) == 4.0
    plan = build_plan(p, p.sequences[0], tmp_path, now_tag="t")
    assert [i.kind for i in plan.items] == ["title"]


def test_audio_is_refused_as_a_generation_reference(world):
    store, p, env = world
    b, m = audio_bin(p)
    shot = add_bin(p, "Shot", workflow="img.json")
    still = add_media(p, add_bin(p, "Plate", workflow="img.json"), type="image")
    exc = refused("audio_reference", lambda: execute(store, p, BindReference(bin=shot, media=m), USER, env=env))
    assert exc.extra == {"type": "media", "id": m}
    refused("audio_reference", lambda: execute(store, p, StartFromMedia(media=m, bin_name="From song"), USER, env=env))
    ref = bind(p, shot, still)
    refused("audio_reference", lambda: execute(store, p, RebindReference(bin=shot, reference=ref.id, media=m), USER, env=env))
    assert p.bins_by_id[shot].references[0].media == still
