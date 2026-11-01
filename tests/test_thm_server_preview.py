"""The item preview: one item with its layer slices and mix, rendered through the export's own
plan and graph, served as a scratch mp4 that is not media."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from thm_core import MediaOrigin, adopt_media, execute
from thm_core.commands import AddSequenceItems, CreateSequence, SetItemMix, SetItemTrim, SetSequenceLayer
from thm_core_fixtures import AGENT, USER, add_bin, live_session
from thm_server_fixtures import seeded_world

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
import cli  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
BASE = "http://thm.test"
AGENT_HEADERS = {"x-thm-actor": "agent"}


def make_clip(path: Path, color: str, seconds: float) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=128x96:r=24", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, capture_output=True)
    return path


def make_tone(path: Path, seconds: float) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-ac", "1", str(path)], check=True, capture_output=True)
    return path


def count_frames(path: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return int(r.stdout.strip().strip(","))


def decode_mono(path: Path) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", "1", "-ar", "48000", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


@pytest.fixture
def world(tmp_path):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    w = seeded_world(tmp_path)
    p = w.registry.get(w.slug)
    live_session(p)
    p.defaults.width, p.defaults.height = 128, 96
    bins = []
    for i, (color, secs) in enumerate((("red", 2.0), ("blue", 2.0), ("green", 2.0))):
        b = add_bin(p, f"Shot {i + 1}", workflow="vid.json", length_s=secs)
        m = adopt_media(p, make_clip(tmp_path / f"{color}.mp4", color, secs), origin=MediaOrigin(type="upload", bin=b))
        p.bins_by_id[b].media.append(m.id)
        p.bins_by_id[b].selected = m.id
        bins.append(b)
    music_bin = add_bin(p, "Music", workflow="")
    tone = adopt_media(p, make_tone(tmp_path / "tone.wav", 12.0), origin=MediaOrigin(type="upload", bin=music_bin))
    p.bins_by_id[music_bin].media.append(tone.id)
    seq = execute(w.store, p, CreateSequence(sequence_name="Rough"), USER, env=w.env).sequence
    execute(w.store, p, AddSequenceItems(sequence=seq, bins=bins), USER, env=w.env)
    # shot 2 shows 1 s of its 2 s take (0.5 s off each end), so handles have somewhere to come from
    execute(w.store, p, SetItemTrim(sequence=seq, bin=bins[1], head_s=0.5, tail_s=0.5), USER, env=w.env)
    execute(w.store, p, SetSequenceLayer(sequence=seq, layer={"id": "tone", "media": tone.id, "source_in_s": 1.0}, summary="a tone under the whole sequence"), AGENT, env=w.env)
    execute(w.store, p, SetItemMix(sequence=seq, bin=bins[2], layers={"tone": None}, summary="no tone under shot 3"), AGENT, env=w.env)
    w.store.save(p)
    w.registry.invalidate(w.slug)
    w.seq, w.bins, w.tone = seq, bins, tone.id
    return w


def test_preview_renders_one_item_with_its_layers_through_the_export_graph(world, tmp_path):
    w = world
    r = w.client.post(f"/api/projects/{w.slug}/sequences/{w.seq}/preview", json={"bin": w.bins[1]})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "video/mp4" and r.headers["x-thm-frames"] == "24" and r.headers["x-thm-offset-frame"] == "48" and r.headers["x-thm-fps"] == "24"
    out = tmp_path / "p.mp4"
    out.write_bytes(r.content)
    assert count_frames(out) == 24
    # the tone under shot 2 is the layer's seconds 3-4 (source in 1 s + 2 s of picture): present, steady
    x = decode_mono(out)
    assert float(np.sqrt(np.mean(np.square(x[4800:43200])))) > 0.02
    # with handles: 0.5 s of the take either side, and the layer shifted back with it
    r = w.client.post(f"/api/projects/{w.slug}/sequences/{w.seq}/preview", json={"bin": w.bins[1], "handles_s": 0.5})
    assert r.status_code == 200 and r.headers["x-thm-frames"] == "48" and r.headers["x-thm-offset-frame"] == "36"
    out2 = tmp_path / "p2.mp4"
    out2.write_bytes(r.content)
    assert count_frames(out2) == 48
    # the muted item previews silent under the layer
    r = w.client.post(f"/api/projects/{w.slug}/sequences/{w.seq}/preview", json={"bin": w.bins[2]})
    assert r.status_code == 200 and r.headers["x-thm-frames"] == "48"
    out3 = tmp_path / "p3.mp4"
    out3.write_bytes(r.content)
    y = decode_mono(out3)
    assert float(np.sqrt(np.mean(np.square(y[9600:86400])))) < 0.002
    # nothing became media, nothing joined a bin, nothing was logged as a change
    project = w.registry.get(w.slug)
    assert len(project.media_by_id) == 3 + 3 + 1  # the seed's three, the three clips, the tone
    assert all(not c["command"].startswith("preview") for c in w.client.get(f"/api/projects/{w.slug}/changes").json())


def test_preview_refusals(world):
    w = world
    assert w.client.post(f"/api/projects/{w.slug}/sequences/{w.seq}/preview", json={"bin": "b_nope"}).json()["code"] == "not_in_sequence"
    assert w.client.post(f"/api/projects/{w.slug}/sequences/sq_nope/preview", json={"bin": w.bins[0]}).status_code == 404
    assert w.client.post(f"/api/projects/{w.slug}/sequences/{w.seq}/preview", json={"bin": w.bins[0], "handles_s": 99}).json()["code"] == "bad_value"
    assert w.client.post(f"/api/projects/{w.slug}/sequences/{w.seq}/preview", json={"bin": w.bins[0], "extra": 1}).status_code == 422
    # the agent needs a live session, like an export
    project = w.registry.get(w.slug)
    project.agent_session.status = "ended"
    w.store.save(project)
    w.registry.invalidate(w.slug)
    r = w.client.post(f"/api/projects/{w.slug}/sequences/{w.seq}/preview", json={"bin": w.bins[0]}, headers=AGENT_HEADERS)
    assert r.status_code == 409 and r.json()["code"] == "no_session"
    # and a private project refuses the agent at the door
    project = w.registry.get(w.slug)
    project.private = True
    w.store.save(project)
    w.registry.invalidate(w.slug)
    r = w.client.post(f"/api/projects/{w.slug}/sequences/{w.seq}/preview", json={"bin": w.bins[0]}, headers=AGENT_HEADERS)
    assert r.status_code == 403 and r.json()["code"] == "private_project"


def _transport(client):
    def send(method, url, body, headers):
        assert url.startswith(BASE)
        r = client.request(method, url[len(BASE):], content=body, headers=headers)
        return r.status_code, r.content, dict(r.headers)

    return send


def test_sequence_preview_verb_writes_the_file_and_the_review_frame_lands(world, tmp_path, monkeypatch, capsys):
    w = world
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    send = _transport(w.client)
    project = w.registry.get(w.slug)
    project.agent_session.status = "waiting"
    w.store.save(project)
    w.registry.invalidate(w.slug)
    run = lambda *a: cli.main(["--server", BASE, "--project", w.slug, *a], transport=send)  # noqa: E731
    assert run("connect", "--harness", "claude-code") == 0
    capsys.readouterr()
    out = tmp_path / "shot2.mp4"
    assert run("sequence", "preview", w.seq, w.bins[1], "--out", str(out), "--handles", "0.5") == 0
    text = capsys.readouterr().out
    assert "wrote" in text and str(out) in text and "sequence mix" in text and count_frames(out) == 48
    assert run("sequence", "mix", w.seq, w.bins[1], "--reviewed", "48") == 0
    assert "reviewed at frame 48" in capsys.readouterr().out
    assert run("--json", "sequence", "show", w.seq) == 0
    seq = json.loads(capsys.readouterr().out)
    assert seq["items"][1]["mix"]["reviewed_start_frame"] == 48 and seq["items"][1]["layer_moved"] is False
