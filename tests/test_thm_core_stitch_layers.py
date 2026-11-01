"""Export with sound layers: the filter graph for every shape of mix, and one real render whose
length in frames matches the plan and whose tone layer runs through the cuts without a seam."""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from thm_core import MediaOrigin, adopt_media, execute
from thm_core.commands import AddSequenceItems, CreateSequence, SetItemMix, SetItemTrim, SetSequenceLayer
from thm_core.media import probe_video
from thm_core.models import ItemMix
from thm_core.stitch import PlanItem, PlanLayer, StitchPlan, build_args, build_plan, layer_envelope, stitch
from thm_core_fixtures import AGENT, USER, add_bin, live_session, make_env, make_project, make_store

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def three_items(mixes=(None, None, None)):
    return [
        PlanItem(bin="b1", kind="video", path="a.mp4", duration_s=2.0, frames=48, start_frame=0, mix=mixes[0]),
        PlanItem(bin="b2", kind="still", path="b.png", duration_s=1.5, frames=36, start_frame=48, mix=mixes[1]),
        PlanItem(bin="b3", kind="video", path="c.mp4", duration_s=2.0, frames=48, start_frame=84, mix=mixes[2]),
    ]


def plan_with(layers, mixes=(None, None, None), offset=0):
    return StitchPlan(items=three_items(mixes), width=320, height=180, fps=24.0, skipped=[], layers=layers, offset_frames=offset)


def graph_of(plan):
    args = build_args(plan, "out.mp4", audio_of=lambda p: p != "c.mp4")
    return args, args[args.index("-filter_complex") + 1]


def test_no_layers_is_the_plain_concat(tmp_path):
    args, graph = graph_of(plan_with([]))
    assert "amix" not in graph and "alimiter" not in graph and "adelay" not in graph
    assert "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[vout][aout]" in graph
    # frame-exact items: conformed after the fps filter, sound trimmed and padded to the same length
    assert graph.count("fps=24,format=yuv420p,tpad=stop_mode=clone:stop=") == 3
    assert "trim=end_frame=48,setpts=PTS-STARTPTS[v0]" in graph and "trim=end_frame=36,setpts=PTS-STARTPTS[v1]" in graph
    assert "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=0:2.000,asetpts=PTS-STARTPTS,apad=whole_dur=2.000[a0]" in graph
    assert "anullsrc=r=48000:cl=stereo,atrim=0:1.500,asetpts=PTS-STARTPTS[a1]" in graph
    assert "-map_metadata" in args and "-c:a" in args


def test_one_layer_is_one_continuous_input_mixed_under_the_clips():
    layer = PlanLayer(id="music", path="song.mp3", source_in_s=12.0, start_frame=0, end="picture", gain_db=-6.0, fade_in_s=0.5, fade_out_s=1.0)
    args, graph = graph_of(plan_with([layer]))
    assert args[args.index("-i", args.index("c.mp4")) + 1] == "song.mp3"  # the layer is the fourth input
    assert "concat=n=3:v=1:a=1[vout][aclips]" in graph
    # one atrim from the in point for the whole picture length (5.5 s), fades, gain, no envelope (no item changes it)
    assert "[3:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=start=12:end=17.5,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.5,afade=t=out:st=4.5:d=1,volume=-6dB[l0]" in graph
    assert graph.count("[3:a]") == 1 and "asplit" not in graph
    assert "[aclips][l0]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.891:level=false:attack=5:release=50[aout]" in graph


def test_two_layers_a_mute_and_ramps():
    music = PlanLayer(id="music", path="song.mp3", source_in_s=0.0, start_frame=0, end="picture")
    walla = PlanLayer(id="walla", path="walla.wav", source_in_s=3.0, start_frame=0, end="picture", gain_db=-12.0)
    mixes = (ItemMix(layers={"music": -8.0}, ramp_ms=30), ItemMix(layers={"music": None, "walla": 2.0}, ramp_ms=100), None)
    plan = plan_with([music, walla], mixes)
    _, graph = graph_of(plan)
    assert "[3:a]" in graph and "[4:a]" in graph and "amix=inputs=3:" in graph
    env = layer_envelope(plan, music)
    # item 1 at -8 dB from the start, item 2 muted with a 100 ms ramp at 2 s, item 3 back to unity with a 30 ms ramp at 3.5 s
    assert env == "if(lt(t,2),0.398107,if(lt(t,2.1),0.398107+(0-0.398107)*(t-2)/0.1,if(lt(t,3.5),0,if(lt(t,3.53),0+(1-0)*(t-3.5)/0.03,1))))"
    assert f"asetnsamples=n=240,volume=volume='{env}':eval=frame[l0]" in graph
    env_w = layer_envelope(plan, walla)
    assert env_w.startswith("if(lt(t,2),1,if(lt(t,2.1),1+(1.25893-1)*(t-2)/0.1,if(lt(t,3.5),1.25893,")
    # the item's own sound carries its edge fades and clip level before the concat
    plan2 = plan_with([music], (ItemMix(clip_db=-3.0, edge_fade_ms=20), None, None))
    _, graph2 = graph_of(plan2)
    assert "apad=whole_dur=2.000,afade=t=in:st=0:d=0.02,afade=t=out:st=1.98:d=0.02,volume=-3dB[a0]" in graph2


def test_layer_starting_mid_sequence_and_ending_with_its_source():
    layer = PlanLayer(id="music", path="song.mp3", source_in_s=4.0, start_frame=48, end="source", source_duration_s=6.5)
    plan = plan_with([layer], (ItemMix(layers={"music": -6.0}), None, None))
    _, graph = graph_of(plan)
    # starts under item 2 (2 s in): delayed, and the source has 2.5 s left of its 6.5 s after the in point
    assert "atrim=start=4:end=6.5,asetpts=PTS-STARTPTS" in graph and "adelay=2000|2000[l0]" in graph
    # item 1 sits before the layer starts: its -6 dB is a negative-time segment the envelope never reaches
    assert layer_envelope(plan, layer) is None
    # the same offset on item 2, which the layer does play under, is a plain constant from the layer's first sample
    plan_b = plan_with([layer], (None, ItemMix(layers={"music": -6.0}), None))
    assert layer_envelope(plan_b, layer) == "if(lt(t,1.5),0.501187,if(lt(t,1.53),0.501187+(1-0.501187)*(t-1.5)/0.03,1))"
    # a preview offset: the plan's first frame is sequence frame 48, so the layer starts at the plan's own start
    pre = StitchPlan(items=[PlanItem(bin="b2", kind="video", path="b.mp4", duration_s=1.5, frames=36, start_frame=0)], width=320, height=180, fps=24.0, skipped=[], layers=[layer], offset_frames=48)
    _, g = graph_of(pre)
    assert "atrim=start=4:end=5.5" in g and "adelay" not in g
    # a preview that begins after the layer did: the in point moves up by the difference, no delay
    pre2 = StitchPlan(items=[PlanItem(bin="b3", kind="video", path="c.mp4", duration_s=2.0, frames=48, start_frame=0)], width=320, height=180, fps=24.0, skipped=[], layers=[layer], offset_frames=84)
    _, g2 = graph_of(pre2)
    assert "atrim=start=5.5:end=6.5" in g2 and "adelay" not in g2


# -- a real render ---------------------------------------------------------------------------


def make_clip(path: Path, color: str, seconds: float, fps: int = 24, size="128x96") -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r={fps}", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, capture_output=True)
    return path


def make_tone(path: Path, seconds: float, hz: int = 440) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=48000:duration={seconds}", "-ac", "1", str(path)], check=True, capture_output=True)
    return path


def decode_mono(path: Path, sr: int = 48000) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


def count_frames(path: Path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return int(r.stdout.strip().strip(","))


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_export_with_a_tone_layer_is_frame_exact_and_seamless(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path)
    live_session(p)
    p.defaults.width, p.defaults.height = 128, 96
    # three silent clips of odd lengths (1.3 s, 0.7 s, 1.1 s at 24 fps -> 31, 17, 26 frames), a 10 s tone under them
    bins = []
    for i, (color, secs) in enumerate((("red", 1.3), ("blue", 0.7), ("green", 1.1))):
        b = add_bin(p, f"Shot {i + 1}", workflow="vid.json", length_s=secs)
        m = adopt_media(p, make_clip(tmp_path / f"{color}.mp4", color, secs), origin=MediaOrigin(type="upload", bin=b))
        p.bins_by_id[b].media.append(m.id)
        p.bins_by_id[b].selected = m.id
        bins.append(b)
    music_bin = add_bin(p, "Music", workflow="")
    tone = adopt_media(p, make_tone(tmp_path / "tone.wav", 10.0), origin=MediaOrigin(type="upload", bin=music_bin))
    p.bins_by_id[music_bin].media.append(tone.id)
    seq = execute(store, p, CreateSequence(sequence_name="Rough"), USER, env=env).sequence
    execute(store, p, AddSequenceItems(sequence=seq, bins=bins), USER, env=env)
    execute(store, p, SetItemTrim(sequence=seq, bin=bins[0], head_s=0.2, tail_s=0.0), USER, env=env)  # 1.1 s -> 26 frames
    execute(store, p, SetSequenceLayer(sequence=seq, layer={"id": "tone", "media": tone.id, "source_in_s": 1.0, "gain_db": -6}, summary="a tone under the whole sequence"), AGENT, env=env)
    s = next(x for x in p.sequences if x.id == seq)
    plan = build_plan(p, s, tmp_path / "work", now_tag="t")
    assert [it.frames for it in plan.items] == [26, 17, 26] and plan.total_frames == 69 and plan.fps == 24.0
    out = stitch(plan, tmp_path / "out.mp4")
    # exactly the planned frames, and the planned length
    assert count_frames(out) == 69
    info = probe_video(out)
    assert abs(info["duration_s"] - 69 / 24) < 0.05 and info["width"] == 128
    # the tone runs straight through both cuts: no sample jump above the surrounding signal
    x = decode_mono(out)
    sr = 48000
    for cut_frame in (26, 43):
        at = int(round(cut_frame / 24 * sr))
        window = x[at - 2400 : at + 2400]
        jumps = np.abs(np.diff(window))
        typical = float(np.percentile(np.abs(np.diff(x[sr // 2 : sr])), 99))  # a 440 Hz sine's own step size
        assert float(jumps.max()) < 1.6 * typical, (cut_frame, float(jumps.max()), typical)
        # and it is still a tone there, at roughly the same level on both sides of the cut
        before = float(np.sqrt(np.mean(np.square(x[at - 4800 : at - 100]))))
        after = float(np.sqrt(np.mean(np.square(x[at + 100 : at + 4800]))))
        assert before > 0.02 and abs(before - after) / before < 0.1
    # the same export with the tone muted under shot 2: silence there, a short ramp, tone around it
    execute(store, p, SetItemMix(sequence=seq, bin=bins[1], layers={"tone": None}, ramp_ms=30, summary="no tone under shot 2"), AGENT, env=env)
    plan2 = build_plan(p, s, tmp_path / "work", now_tag="t2")
    out2 = stitch(plan2, tmp_path / "out2.mp4")
    assert count_frames(out2) == 69
    y = decode_mono(out2)
    mid2 = int(round((26 + 8) / 24 * sr))
    assert float(np.sqrt(np.mean(np.square(y[mid2 - 2000 : mid2 + 2000])))) < 0.002
    assert float(np.sqrt(np.mean(np.square(y[sr // 4 : sr // 2])))) > 0.02
    assert float(np.sqrt(np.mean(np.square(y[int(2.4 * sr) : int(2.7 * sr)])))) > 0.02
