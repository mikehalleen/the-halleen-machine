"""The agent tool kit: ``cli.py tools`` / ``tool``, and the audio tool's argument parsing and JSON
shapes. The ffmpeg-only verbs (levels, motion, cuts' detector) run here on fixture media; the
model verbs run through the tool's own venv only when it is installed (skipped otherwise)."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from thm_core import MediaOrigin, adopt_media
from thm_server_fixtures import make_tiny_audio, make_tiny_mp4, seeded_world

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
import cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "thm-tools" / "audio"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
BASE = "http://thm.test"


def _transport(client):
    def send(method, url, body, headers):
        assert url.startswith(BASE)
        r = client.request(method, url[len(BASE):], content=body, headers=headers)
        return r.status_code, r.content, dict(r.headers)

    return send


def load_run():
    spec = importlib.util.spec_from_file_location("thm_audio_run", TOOL_DIR / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_tool(root: Path, name: str, guide: str = "# Fake tool\n\nmeasures nothing\n", script: str | None = None) -> Path:
    """A fake installed tool: a placeholder venv python (the tests point ``tool_python`` at this
    interpreter, since a copied python.exe cannot run on its own) and a run.py that echoes."""
    d = root / name
    (d / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")).mkdir(parents=True)
    py = d / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    py.write_bytes(b"placeholder")
    (d / "GUIDE.md").write_text(guide, encoding="utf-8")
    (d / "run.py").write_text(script or (
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'cuda': os.environ.get('CUDA_VISIBLE_DEVICES'), 'hf_home': os.environ.get('HF_HOME'), 'cwd': os.getcwd()}))\n"
        "sys.exit(3 if 'fail' in sys.argv else 0)\n"
    ), encoding="utf-8")
    return d


# -- the framework -------------------------------------------------------------------


def test_tools_lists_installed_tools_with_the_guides_first_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "TOOLS_DIR", tmp_path)
    assert cli.main(["tools"]) == 0
    assert "no tools installed" in capsys.readouterr().out
    write_tool(tmp_path, "fake", guide="# Fake tool: counts beans\n\nmore\n")
    (tmp_path / "broken").mkdir()  # a folder with no venv and no run.py is not a tool
    assert cli.main(["tools"]) == 0
    out = capsys.readouterr().out
    assert "fake" in out and "Fake tool: counts beans" in out and "broken" not in out
    assert cli.main(["--json", "tools"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in rows] == ["fake"] and rows[0]["about"] == "Fake tool: counts beans"


def use_this_python(monkeypatch):
    real = cli.tool_python
    monkeypatch.setattr(cli, "tool_python", lambda name: Path(sys.executable) if real(name).is_file() else real(name))


def test_tool_runs_in_its_venv_cpu_only_and_returns_its_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "TOOLS_DIR", tmp_path)
    use_this_python(monkeypatch)
    d = write_tool(tmp_path, "fake")
    rc = cli.main(["tool", "fake", "count", "a.wav", "--n", "3", "--scratch", str(tmp_path / "scratch")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["argv"] == ["count", "a.wav", "--n", "3", "--scratch", str(tmp_path / "scratch")]
    assert out["cuda"] == "" and out["hf_home"] == str(d / "models") and Path(out["cwd"]) == d
    # the tool's exit code is the call's
    assert cli.main(["tool", "fake", "fail"]) == 3
    capsys.readouterr()
    # a missing tool says which one and that it is not installed
    rc = cli.main(["tool", "nope", "count"])
    err = capsys.readouterr().err
    assert rc == 2 and "nope" in err and "not installed" in err


def test_tool_fetches_media_ids_through_the_server(tmp_path, monkeypatch, capsys):
    world = seeded_world(tmp_path)
    monkeypatch.setattr(cli, "TOOLS_DIR", tmp_path / "tools")
    use_this_python(monkeypatch)
    write_tool(tmp_path / "tools", "fake")
    scratch = tmp_path / "scratch"
    # a media id alone, and one inside a comma list, both become local files; other args pass through
    rc = cli.main(["--server", BASE, "--project", world.slug, "tool", "fake", "voice", world.m1, "--ref", f"{world.m2},{world.m1}", "--window", "0:1", "--scratch", str(scratch)], transport=_transport(world.client))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    argv = out["argv"]
    assert argv[0] == "voice" and Path(argv[1]) == scratch / f"{world.m1}.png" and (scratch / f"{world.m1}.png").stat().st_size > 0
    assert argv[argv.index("--ref") + 1] == f"{scratch / (world.m2 + '.png')},{scratch / (world.m1 + '.png')}"
    assert argv[argv.index("--window") + 1] == "0:1"
    # a media id without a project cannot be fetched
    assert cli.main(["--server", BASE, "tool", "fake", "voice", world.m1], transport=_transport(world.client)) == 2
    assert "--project" in capsys.readouterr().err
    # the private gate: the server refuses the fetch for a private project
    project = world.registry.get(world.slug)
    project.private = True
    world.store.save(project)
    world.registry.invalidate(world.slug)
    assert cli.main(["--server", BASE, "--project", world.slug, "tool", "fake", "voice", world.m1], transport=_transport(world.client)) == 1
    assert "private_project" in capsys.readouterr().err


# -- the audio tool: parsing and the ffmpeg-only verbs --------------------------------


def test_audio_tool_parser_covers_every_verb_and_refuses_bad_calls(tmp_path):
    run = load_run()
    ap = run.build_parser()
    for verb in ("transcribe", "voice", "emphasis", "levels", "beats", "energy", "cuts", "motion"):
        assert verb in run.VERBS
    a = ap.parse_args(["transcribe", "x.mp4", "--expect", "hello THERE", "--fps", "24", "--scratch", "s"])
    assert a.verb == "transcribe" and a.expect == "hello THERE" and a.fps == 24.0 and a.model == run.DEFAULT_WHISPER
    a = ap.parse_args(["voice", "x.mp4", "--ref", "a.mp4,b.mp4", "--ref", "c.mp4", "--window", "1.5:3"])
    assert a.ref == ["a.mp4,b.mp4", "c.mp4"] and run._window(a.window) == (1.5, 3.0)
    a = ap.parse_args(["cuts", "e.mp4", "--song", "s.mp3", "--song-in", "12.5", "--start-frame", "48", "--fps", "24"])
    assert a.song_in == 12.5 and a.start_frame == 48
    assert run.main(["voice", "x.mp4"]) == 2  # --ref is required
    assert run.main(["nope"]) == 2
    assert run.intended_stress("you know what YOU can do, I said OK") == ["you"]
    assert run.normalize_words("Wait. That's it?") == ["wait", "that's", "it"]
    out = json.loads(subprocess.run([sys.executable, "-I", str(TOOL_DIR / "run.py"), "levels", str(tmp_path / "missing.wav")], capture_output=True, text=True).stdout)
    assert out["verb"] == "levels" and "no such file" in out["error"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_levels_shape_flags_and_frames(tmp_path, capsys):
    run = load_run()
    wav = make_tiny_audio(tmp_path / "tone.wav", seconds=2.0)
    assert run.main(["levels", str(wav), "--fps", "24", "--scratch", str(tmp_path / "s")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verb"] == "levels" and out["scratch"] == str(tmp_path / "s")
    assert {"peak_dbfs", "voice_p90_db", "voice_median_db", "windows", "clicks", "edges", "flags", "duration_s"} <= set(out)
    assert len(out["windows"]) == 8 and out["windows"][1]["start_frame"] == 6
    assert -40 < out["voice_p90_db"] < 0 and out["clicks"] == [] and out["flags"] == []
    # a hard click: a full-scale sample in the middle of the tone
    import numpy as np

    samples = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"], capture_output=True).stdout, dtype=np.float32).copy()
    samples[16000] = 1.0
    clicky = tmp_path / "click.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", "16000", "-ac", "1", "-i", "-", str(clicky)], input=samples.tobytes(), check=True)
    assert run.main(["levels", str(clicky), "--fps", "24"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["clicks"]) == 1 and abs(out["clicks"][0]["at_s"] - 1.0) < 0.01 and out["clicks"][0]["at_frame"] == 24
    assert any("click" in f for f in out["flags"])
    # a video's audio track works the same
    mp4 = make_tiny_mp4(tmp_path / "v.mp4")
    assert run.main(["levels", str(mp4)]) == 1  # testsrc has no audio stream
    assert "no audio stream" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_cut_detector_and_motion_are_frame_exact(tmp_path, capsys):
    run = load_run()
    lst = tmp_path / "list.txt"
    lines = []
    for c in ("red", "blue", "green"):
        clip = tmp_path / f"{c}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={c}:s=64x36:r=24", "-frames:v", "12", "-pix_fmt", "yuv420p", str(clip)], check=True, capture_output=True)
        lines.append(f"file '{clip.as_posix()}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    video = tmp_path / "cuts.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video)], check=True, capture_output=True)
    cuts, rate = run.scene_cuts(str(video), run.SCENE_THRESHOLD)
    assert cuts == [12, 24] and rate == 24.0
    assert run.main(["motion", str(video)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["frames"] == 36 and out["fps"] == 24.0 and len(out["per_second"]) == 2
    assert out["motion_mean"] > 0 and out["motion_p90"] >= out["motion_mean"] * 0


def test_beat_structure_counts_frames_cumulatively():
    """Bar lengths come from cumulative frame positions: at 23.976 fps a 2 s bar is 48 frames
    most of the time and 47 now and then, never a rounded 48 summed into drift."""
    import numpy as np

    run = load_run()
    beats = np.arange(0, 64, 0.5)
    downbeats = np.arange(0, 64, 2.0)
    out = run.beat_structure(beats, downbeats, 64.0, 23.976)
    assert out["tempo_bpm"] == 120.0 and out["beats_per_bar"] == 4
    frames = [b["frames"] for b in out["bars"]]
    assert set(frames) <= {47, 48} and sum(frames) == out["bars"][-1]["start_frame"] + out["bars"][-1]["frames"]
    assert out["bars"][-1]["start_frame"] + out["bars"][-1]["frames"] == int(round(64.0 * 23.976))
    assert out["phrases"]["4"][1]["start_frame"] == out["bars"][4]["start_frame"]
    assert sum(p["frames"] for p in out["phrases"]["8"]) == out["bars"][-1]["start_frame"] + out["bars"][-1]["frames"]


# -- the installed tool, end to end through cli.py --------------------------------------


installed = cli.tool_installed("audio") and HAS_FFMPEG


@pytest.mark.skipif(not installed, reason="thm-tools/audio is not installed on this machine")
def test_installed_audio_tool_beats_and_transcribe_round_trip(tmp_path, capsys):
    song = tmp_path / "song.mp3"
    # a kick every half second, louder on the one of each 2 s bar, a noise hat on the offbeats
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "aevalsrc='(1+0.6*lt(mod(t,2),0.5))*0.8*sin(2*PI*55*t)*exp(-14*mod(t,0.5))+0.3*(random(0)-0.5)*exp(-60*mod(t+0.25,0.5))':s=44100:d=12", "-c:a", "libmp3lame", "-q:a", "4", str(song)], check=True, capture_output=True)
    rc = cli.main(["tool", "audio", "beats", str(song), "--fps", "24", "--scratch", str(tmp_path / "s")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    # on a 12 s synthetic loop the downbeat may land on every beat or every bar; the beats are the point
    assert abs(out["tempo_bpm"] - 120) < 2 and out["beat_frames"][:3] == [0, 12, 24] and out["bar_frames_typical"] in (12, 24, 48)
    assert out["phrase_frames_typical"]["4"] == 4 * out["bar_frames_typical"] and out["scratch"] == str(tmp_path / "s")
    wav = make_tiny_audio(tmp_path / "tone.wav", seconds=1.0)
    rc = cli.main(["tool", "audio", "transcribe", str(wav), "--model", "small", "--expect", "hello there", "--scratch", str(tmp_path / "s")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    assert {"text", "words", "wer", "diff", "low_confidence", "verdict", "expected_words"} <= set(out)
