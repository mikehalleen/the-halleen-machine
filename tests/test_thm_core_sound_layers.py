"""Sound layers on a sequence (agent only): the three mix commands, the exact frame timing every
item gets, what each layer does under each item, and the "moved since review" signal."""

import json
import sys
from pathlib import Path

import pytest

from thm_core import CommandRefused, execute
from thm_core.commands import (
    AddSequenceItems,
    CreateSequence,
    RemoveSequenceItem,
    RemoveSequenceLayer,
    SetItemMix,
    SetItemTrim,
    SetSequenceLayer,
    _revert,
    item_frame_spans,
    sequence_fps,
)
from thm_core.models import SoundLayer
from thm_core_fixtures import AGENT, USER, add_bin, add_media, add_take, live_session, make_env, make_project, make_store
from thm_server_fixtures import make_tiny_audio, seeded_world

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
import cli  # noqa: E402

BASE = "http://thm.test"


@pytest.fixture
def world(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path)
    live_session(p)
    return store, p, env


def refused(code, fn):
    with pytest.raises(CommandRefused) as exc:
        fn()
    assert exc.value.code == code, f"expected {code}, got {exc.value.code}: {exc.value.detail}"
    return exc.value


def video_take(p, bin_id, seconds, fps=24.0):
    mid = add_take(p, bin_id, type="video")
    p.media_by_id[mid].duration_s = seconds
    p.media_by_id[mid].fps = fps
    p.bins_by_id[bin_id].selected = mid
    return mid


def song(p, seconds=30.0):
    b = add_bin(p, "Music", workflow="")
    m = add_media(p, b, type="audio", path="C:/fake/song.mp3")
    p.media_by_id[m].duration_s = seconds
    return m


def built(store, p, env, *lengths, fps=24.0):
    seq = execute(store, p, CreateSequence(sequence_name="Rough"), USER, env=env).sequence
    bins = []
    for i, n in enumerate(lengths):
        b = add_bin(p, f"Shot {i + 1}", workflow="vid.json", length_s=n)
        video_take(p, b, n, fps)
        bins.append(b)
    execute(store, p, AddSequenceItems(sequence=seq, bins=bins), USER, env=env)
    return seq, bins


def the_seq(p, seq_id):
    return next(s for s in p.sequences if s.id == seq_id)


# -- commands -------------------------------------------------------------------------


def test_set_layer_adds_replaces_and_needs_the_users_line(world):
    store, p, env = world
    seq, bins = built(store, p, env, 4.0, 2.0)
    m = song(p)
    lay = {"id": "music", "media": m, "source_in_s": 12.0, "gain_db": -6, "fade_out_s": 1.0}
    # agent only, and the summary is the price of admission
    refused("wrong_actor", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer=lay, summary="song"), USER, env=env))
    refused("summary_required", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer=lay), AGENT, env=env))
    r = execute(store, p, SetSequenceLayer(sequence=seq, layer=lay, summary="the song under the whole sequence from its first chorus, -6 dB"), AGENT, env=env)
    assert r.change.description == "Rough - added layer music: Music file 1 from 12s at frame 0, -6 dB, fade 0s/1s"
    s = the_seq(p, seq)
    assert [x.id for x in s.layers] == ["music"] and s.layers[0].source_in_s == 12.0 and s.layers[0].end == "picture"
    assert s.agent["summary"].startswith("the song under")
    # the same id replaces, and the summary is kept current
    r = execute(store, p, SetSequenceLayer(sequence=seq, layer={**lay, "source_in_s": 20.0, "start_frame": 48}, summary="the song from the second chorus, in at shot 2"), AGENT, env=env)
    assert r.change.description.startswith("Rough - replaced layer music: Music file 1 from 20s at frame 48")
    assert len(the_seq(p, seq).layers) == 1 and the_seq(p, seq).layers[0].start_frame == 48
    # the summary is checked like every agent summary
    refused("bad_value", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer=lay, summary="music under the cut"), AGENT, env=env))
    # the whole edit reverts as one project diff
    assert _revert(p, r.change)
    assert the_seq(p, seq).layers[0].source_in_s == 12.0 and the_seq(p, seq).layers[0].start_frame == 0
    # a second layer sits beside the first
    execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "walla", "media": m}, summary="song and a walla bed"), AGENT, env=env)
    assert [x.id for x in the_seq(p, seq).layers] == ["music", "walla"]


def test_set_layer_refusals(world):
    store, p, env = world
    seq, bins = built(store, p, env, 4.0)
    m = song(p, 30.0)
    still = add_media(p, bins[0], type="image")
    ok = dict(summary="song under it all")
    refused("unknown_id", lambda: execute(store, p, SetSequenceLayer(sequence="sq_nope", layer={"id": "music", "media": m}, **ok), AGENT, env=env))
    refused("unknown_id", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": "m_nope"}, **ok), AGENT, env=env))
    exc = refused("no_audio", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": still}, **ok), AGENT, env=env))
    assert exc.extra == {"type": "media", "id": still}
    for bad_id in ("", "Music", "a b", "x" * 30):
        refused("bad_value", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": bad_id, "media": m}, **ok), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": m, "source_in_s": 31.0}, **ok), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": m, "source_in_s": -1}, **ok), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": m, "start_frame": -1}, **ok), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": m, "gain_db": 40}, **ok), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": m, "fade_in_s": -0.5}, **ok), AGENT, env=env))
    with pytest.raises(Exception):
        execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": m, "end": "never"}, **ok), AGENT, env=env)
    assert the_seq(p, seq).layers == []


def test_a_video_with_sound_can_be_a_layer_a_silent_one_cannot(world, tmp_path):
    store, p, env = world
    seq, bins = built(store, p, env, 4.0)
    from thm_core import MediaOrigin, adopt_media
    from thm_server_fixtures import make_tiny_mp4

    wav = make_tiny_audio(tmp_path / "bed.wav")
    silent = make_tiny_mp4(tmp_path / "silent.mp4")
    if wav is None or silent is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    b = add_bin(p, "Beds", workflow="")
    bed = adopt_media(p, wav, origin=MediaOrigin(type="upload", bin=b))
    mute = adopt_media(p, silent, origin=MediaOrigin(type="upload", bin=b))
    p.bins_by_id[b].media += [bed.id, mute.id]
    execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "bed", "media": bed.id}, summary="a bed under it"), AGENT, env=env)
    refused("no_audio", lambda: execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "tone", "media": mute.id}, summary="x"), AGENT, env=env))


def test_remove_layer_clears_item_offsets_and_keeps_the_line_honest(world):
    store, p, env = world
    seq, bins = built(store, p, env, 4.0, 2.0)
    m = song(p)
    execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": m}, summary="song under it all"), AGENT, env=env)
    execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "walla", "media": m}, summary="song and walla"), AGENT, env=env)
    execute(store, p, SetItemMix(sequence=seq, bin=bins[0], layers={"music": -8, "walla": None}, summary="music down, no walla under the line"), AGENT, env=env)
    refused("wrong_actor", lambda: execute(store, p, RemoveSequenceLayer(sequence=seq, layer="walla", summary="x"), USER, env=env))
    refused("unknown_layer", lambda: execute(store, p, RemoveSequenceLayer(sequence=seq, layer="bed", summary="x"), AGENT, env=env))
    # a layer remains: the line must be refreshed
    refused("summary_required", lambda: execute(store, p, RemoveSequenceLayer(sequence=seq, layer="walla"), AGENT, env=env))
    r = execute(store, p, RemoveSequenceLayer(sequence=seq, layer="walla", summary="song only now"), AGENT, env=env)
    assert r.change.description == "Rough - removed layer walla"
    s = the_seq(p, seq)
    assert [x.id for x in s.layers] == ["music"] and s.items[0].mix.layers == {"music": -8.0} and s.agent["summary"] == "song only now"
    # the last layer goes: the line can be cleared with "", or kept with words
    refused("summary_required", lambda: execute(store, p, RemoveSequenceLayer(sequence=seq, layer="music"), AGENT, env=env))
    execute(store, p, RemoveSequenceLayer(sequence=seq, layer="music", summary=""), AGENT, env=env)
    s = the_seq(p, seq)
    assert s.layers == [] and "summary" not in s.agent and s.items[0].mix.layers == {}


def test_item_mix_merges_validates_and_needs_a_line_when_it_departs(world):
    store, p, env = world
    seq, bins = built(store, p, env, 4.0, 2.0)
    m = song(p)
    execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": m}, summary="song under it all"), AGENT, env=env)
    refused("wrong_actor", lambda: execute(store, p, SetItemMix(sequence=seq, bin=bins[0], clip_db=-3, summary="x"), USER, env=env))
    refused("not_in_sequence", lambda: execute(store, p, SetItemMix(sequence=seq, bin="b_nope", clip_db=-3, summary="x"), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetItemMix(sequence=seq, bin=bins[0]), AGENT, env=env))
    exc = refused("unknown_layer", lambda: execute(store, p, SetItemMix(sequence=seq, bin=bins[0], layers={"bed": -3}, summary="x"), AGENT, env=env))
    assert exc.extra["layers"] == ["music"]
    refused("bad_value", lambda: execute(store, p, SetItemMix(sequence=seq, bin=bins[0], layers={"music": -70}, summary="x"), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetItemMix(sequence=seq, bin=bins[0], edge_fade_ms=900, summary="x"), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetItemMix(sequence=seq, bin=bins[0], ramp_ms=-1, summary="x"), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetItemMix(sequence=seq, bin=bins[0], reviewed_start_frame=-5), AGENT, env=env))
    # departing from the defaults needs the item's line, in item scope
    refused("summary_required", lambda: execute(store, p, SetItemMix(sequence=seq, bin=bins[0], layers={"music": -8}), AGENT, env=env))
    refused("scope_item", lambda: execute(store, p, SetItemMix(sequence=seq, bin=bins[0], layers={"music": -8}, summary="music down across the whole sequence"), AGENT, env=env))
    r = execute(store, p, SetItemMix(sequence=seq, bin=bins[0], layers={"music": -8}, summary="music -8 dB under the line"), AGENT, env=env)
    assert r.change.description == "Shot 1 - mix: music -8 dB"
    item = the_seq(p, seq).items[0]
    assert item.mix.layers == {"music": -8.0} and item.mix.clip_db == 0.0 and item.mix.edge_fade_ms == 10 and item.mix.ramp_ms == 30
    assert item.agent["summary"] == "music -8 dB under the line"
    # merged key by key: a mute keeps the clip level, and the review frame rides alone
    execute(store, p, SetItemMix(sequence=seq, bin=bins[0], clip_db=2, layers={"music": None}, summary="no music under the line, clip +2 dB"), AGENT, env=env)
    item = the_seq(p, seq).items[0]
    assert item.mix.layers == {"music": None} and item.mix.clip_db == 2.0
    r = execute(store, p, SetItemMix(sequence=seq, bin=bins[0], reviewed_start_frame=0), AGENT, env=env)
    assert r.change.description == "Shot 1 - mix: reviewed at frame 0" and the_seq(p, seq).items[0].mix.reviewed_start_frame == 0
    # the default mix needs no line
    execute(store, p, SetItemMix(sequence=seq, bin=bins[1], reviewed_start_frame=96), AGENT, env=env)
    assert the_seq(p, seq).items[1].mix.reviewed_start_frame == 96 and "summary" not in the_seq(p, seq).items[1].agent


# -- derived timing ----------------------------------------------------------------------


def test_frames_are_rounded_once_per_item_and_summed(world):
    store, p, env = world
    # 23.976 fps: 1.5 s is 35.964 frames -> 36 each; summing rounded items never drifts a whole second
    seq, bins = built(store, p, env, 1.5, 1.5, 1.5, 1.5, fps=23.976)
    s = the_seq(p, seq)
    assert sequence_fps(p, s) == 23.976
    spans = item_frame_spans(p, s)
    assert spans == [(0, 36), (36, 36), (72, 36), (108, 36)]
    # a trim changes one item's frames and every start after it
    execute(store, p, SetItemTrim(sequence=seq, bin=bins[1], head_s=0.5, tail_s=0.0), USER, env=env)
    assert item_frame_spans(p, s) == [(0, 36), (36, 24), (60, 36), (96, 36)]
    # a still or a title card counts in frames too; no video at all means 24 fps
    other = execute(store, p, CreateSequence(sequence_name="Cards"), USER, env=env).sequence
    card = add_bin(p, "Card", workflow="vid.json", length_s=2.0)
    execute(store, p, AddSequenceItems(sequence=other, bins=[card]), USER, env=env)
    assert sequence_fps(p, the_seq(p, other)) == 24.0 and item_frame_spans(p, the_seq(p, other)) == [(0, 48)]


def test_sequence_out_reports_frames_layer_spans_and_moves(tmp_path):
    w = seeded_world(tmp_path)
    p = w.registry.get(w.slug)
    live_session(p)
    store, env, slug = w.store, w.env, w.slug
    seq, bins = built(store, p, env, 4.0, 2.0, 3.0)
    m = song(p, 10.0)
    # the song starts under shot 2 at second 6 of the source and runs to the end of the source
    execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "music", "media": m, "source_in_s": 6.0, "start_frame": 96, "end": "source"}, summary="song in at shot 2 until it runs out"), AGENT, env=env)
    execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "walla", "media": m}, summary="song in at shot 2, walla throughout"), AGENT, env=env)
    execute(store, p, SetItemMix(sequence=seq, bin=bins[1], layers={"walla": None, "music": -4}, reviewed_start_frame=96, summary="no walla under the line, music -4 dB"), AGENT, env=env)
    store.save(p)
    w.registry.invalidate(slug)
    out = w.client.get(f"/api/projects/{slug}/sequences/{seq}").json()
    assert out["fps"] == 24.0 and out["total_frames"] == 216 and [x["id"] for x in out["layers"]] == ["music", "walla"]
    rows = out["items"]
    assert [(r["start_frame"], r["frames"]) for r in rows] == [(0, 96), (96, 48), (144, 72)]
    # shot 1: the song has not started; walla plays its first 4 s
    assert rows[0]["layers"]["music"] == {"source_in_s": None, "source_out_s": None, "db": 0.0, "muted": False}
    assert rows[0]["layers"]["walla"]["source_in_s"] == 0.0 and rows[0]["layers"]["walla"]["source_out_s"] == 4.0
    # shot 2: the song's seconds 6-8 at -4 dB; walla muted here, still spanning 4-6 s
    assert rows[1]["layers"]["music"] == {"source_in_s": 6.0, "source_out_s": 8.0, "db": -4.0, "muted": False}
    assert rows[1]["layers"]["walla"] == {"source_in_s": 4.0, "source_out_s": 6.0, "db": None, "muted": True}
    assert rows[1]["mix"]["reviewed_start_frame"] == 96 and rows[1]["layer_moved"] is False
    # shot 3: the 10 s song runs out after 2 of the 3 s
    assert rows[2]["layers"]["music"] == {"source_in_s": 8.0, "source_out_s": 10.0, "db": 0.0, "muted": False}
    # an edit before shot 2 moves it: the row and the resume summary say so
    r = w.client.post(f"/api/projects/{slug}/commands/set_item_trim", json={"sequence": seq, "bin": bins[0], "head_s": 1.0, "tail_s": 0.0})
    assert r.status_code == 200, r.text
    out = w.client.get(f"/api/projects/{slug}/sequences/{seq}").json()
    assert out["items"][1]["start_frame"] == 72 and out["items"][1]["layer_moved"] is True and out["items"][2]["layer_moved"] is False
    moved = w.client.get(f"/api/projects/{slug}/resume").json()["moved"]
    assert [(x["position"], x["reviewed_start_frame"], x["start_frame"]) for x in moved] == [(2, 96, 72)]
    # removing the item before it moves it back: nothing owed
    execute(store, w.registry.get(slug), RemoveSequenceItem(sequence=seq, bin=bins[0]), USER, env=env)
    store.save(w.registry.get(slug))
    w.registry.invalidate(slug)
    assert w.client.get(f"/api/projects/{slug}/resume").json()["moved"] == [] or w.client.get(f"/api/projects/{slug}/sequences/{seq}").json()["items"][0]["start_frame"] == 0


# -- the CLI ------------------------------------------------------------------------------


def _transport(client):
    def send(method, url, body, headers):
        assert url.startswith(BASE)
        r = client.request(method, url[len(BASE):], content=body, headers=headers)
        return r.status_code, r.content, dict(r.headers)

    return send


def test_layer_and_mix_verbs_round_trip(tmp_path, monkeypatch, capsys):
    w = seeded_world(tmp_path)
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    send = _transport(w.client)
    slug = w.slug
    p = w.registry.get(slug)
    seq, bins = built(w.store, p, w.env, 4.0, 2.0)
    m = song(p, 20.0)
    w.store.save(p)
    w.registry.invalidate(slug)
    w.client.post(f"/api/projects/{slug}/commands/start_session", json={"notes": "cut it", "mode": "auto"})
    assert cli.main(["--server", BASE, "--project", slug, "connect", "--harness", "claude-code"], transport=send) == 0
    capsys.readouterr()
    run = lambda *a: cli.main(["--server", BASE, "--project", slug, *a], transport=send)  # noqa: E731

    assert run("sequence", "layer-add", seq, "music", "--media", m, "--in", "12", "--gain", "-6", "--fade-out", "1", "--summary", "the song from its first chorus, -6 dB", "--reason", "the brief wants music") == 0
    assert "added layer music: Music file 1 from 12s at frame 0, -6 dB, fade 0s/1s" in capsys.readouterr().out
    assert run("sequence", "mix", seq, bins[0], "--layer", "music=-8", "--clip-db", "1.5", "--summary", "music -8 dB under the line") == 0
    assert "Shot 1 - mix: clip +1.5 dB, music -8 dB" in capsys.readouterr().out
    assert run("sequence", "mix", seq, bins[1], "--layer", "music=mute", "--reviewed", "96", "--summary", "no music under the reaction") == 0
    assert "music muted, reviewed at frame 96" in capsys.readouterr().out
    assert run("sequence", "mix", seq, bins[1], "--layer", "music=loud") == 2
    capsys.readouterr()

    assert run("sequence", "show", seq) == 0
    text = capsys.readouterr().out
    assert "24 fps, 144 frames" in text
    assert "layer music: " in text and "from 12s at frame 0, to the end of the picture, -6 dB, fade 0s/1s" in text
    assert "export only, whole sequence: the song from its first chorus, -6 dB" in text
    assert "f0-96" in text and "f96-144" in text and "[mix: clip +1.5 dB, music -8 dB]" in text and "[mix: music muted]" in text
    assert "moved since review" not in text

    # the user trims shot 1 in the app: shot 2 moved under the song, show and resume both say so
    w.client.post(f"/api/projects/{slug}/commands/set_item_trim", json={"sequence": seq, "bin": bins[0], "head_s": 0.5, "tail_s": 0.0})
    assert run("sequence", "show", seq) == 0
    text = capsys.readouterr().out
    assert "f84-132" in text and "[MOVED since your review at frame 96: listen again]" in text and "moved since review (re-QC against the layers with `sequence preview`): #2 Shot 2" in text
    assert run("resume") == 0
    assert "MOVED Rough #2 Shot 2: reviewed at frame 96, now starts at 84" in capsys.readouterr().out

    assert run("sequence", "layer-drop", seq, "music") == 1
    assert "summary_required" in capsys.readouterr().err
    assert run("sequence", "layer-drop", seq, "music", "--summary", "") == 0
    assert "removed layer music" in capsys.readouterr().out
    assert run("--json", "sequence", "show", seq) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["layers"] == [] and out["items"][0]["mix"]["layers"] == {} and out["agent"] == {}
