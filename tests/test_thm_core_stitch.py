"""The stitcher: the filter graph is built pure; one test runs ffmpeg when it is there."""

import shutil

import pytest
from PIL import Image

from thm_core import execute
from thm_core.commands import AddSequenceItems, CreateSequence, SetItemStill, SetItemTrim
from thm_core.media import probe_video
from thm_core.stitch import PlanItem, StitchPlan, build_args, build_plan, parse_progress_line, stitch, title_card
from thm_core_fixtures import USER, add_bin, add_media, add_take, make_env, make_project, make_store
from thm_server_fixtures import make_tiny_mp4, write_png


def test_title_card_is_a_png_of_the_canvas_size(tmp_path):
    out = title_card("the quiet kids", 640, 360, tmp_path / "card.png", number=11)
    with Image.open(out) as im:
        assert im.size == (640, 360) and im.mode == "RGB"
        # the fill is the design's card colour at a corner, ink somewhere in the middle row
        assert im.getpixel((2, 2)) == (0xE9, 0xE4, 0xD8)
        assert any(px != (0xE9, 0xE4, 0xD8) for px in im.getdata())


def test_build_args_one_input_per_item_and_a_concat(tmp_path):
    plan = StitchPlan(
        items=[
            PlanItem(bin="b1", kind="video", path="a.mp4", duration_s=4.5, head_s=0.5),
            PlanItem(bin="b2", kind="still", path="b.png", duration_s=3.0),
            PlanItem(bin="b3", kind="video", path="c.mp4", duration_s=2.0),
        ],
        width=1280, height=720, fps=24.0, skipped=[],
    )
    args = build_args(plan, tmp_path / "out.mp4", exe="ffmpeg", audio_of=lambda p: p == "a.mp4")
    text = " ".join(args)
    assert args[0] == "ffmpeg" and "-nostdin" in args
    # the video is seeked and cut, the still looped for its length
    # one frame more than the item is read, so the conform step always has a frame to trim or clone
    assert "-ss 0.500 -t 4.542 -i a.mp4" in text
    assert "-loop 1 -t 3.000 -r 24 -i b.png" in text
    graph = args[args.index("-filter_complex") + 1]
    assert "tpad=stop_mode=clone:stop=108,trim=end_frame=108,setpts=PTS-STARTPTS[v0]" in graph
    assert graph.count("scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720") == 3
    assert "fps=24,format=yuv420p" in graph
    # sound: a.mp4 has it, b.png never does, c.mp4 is silent -> silence of the right length
    assert "[0:a]aresample=48000" in graph
    assert graph.count("anullsrc=r=48000:cl=stereo,atrim=0:3.000") == 1
    assert graph.count("anullsrc=r=48000:cl=stereo,atrim=0:2.000") == 1
    assert "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[vout][aout]" in graph
    assert text.endswith(f"-progress pipe:1 {tmp_path / 'out.mp4'}")
    assert "-crf 18" in text and "+faststart" in text
    assert "-map_metadata -1" in text  # the first clip's ComfyUI graph must not ride along


def test_parse_progress_line():
    assert parse_progress_line("out_time_ms=2500000") == 2.5
    assert parse_progress_line("frame=12") is None
    assert parse_progress_line("out_time_ms=junk") is None


def test_build_plan_uses_trims_stills_and_title_cards(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    p.defaults.width, p.defaults.height = 640, 360
    env = make_env(tmp_path)
    r = execute(store, p, CreateSequence(sequence_name="Rough cut"), USER, env=env)
    sid = r.sequence
    vid = add_bin(p, "shot", "vid.json")
    clip = tmp_path / "fake.mp4"  # exists on disk; the plan only checks presence
    clip.write_bytes(b"not really a video")
    m = add_take(p, vid, type="video", path=str(clip))
    p.media_by_id[m].duration_s, p.media_by_id[m].fps = 5.0, 30.0
    p.bins_by_id[vid].selected = m
    still = add_bin(p, "card")
    add_media(p, still, path=str(write_png(tmp_path / "card.png")))
    later = add_bin(p, "the quiet kids", "vid.json", length_s=4.0)
    p.bins_by_id[later].shot = 11
    gone = add_bin(p, "gone", "vid.json")
    add_take(p, gone, type="video", path=str(tmp_path / "missing.mp4"))
    execute(store, p, AddSequenceItems(sequence=sid, bins=[vid, still, later, gone]), USER, env=env)
    execute(store, p, SetItemTrim(sequence=sid, bin=vid, head_s=0.5, tail_s=1.0), USER, env=env)
    execute(store, p, SetItemStill(sequence=sid, bin=still, still_s=2.5), USER, env=env)
    plan = build_plan(p, p.sequences[0], tmp_path / "work", now_tag="t")
    assert (plan.width, plan.height, plan.fps) == (640, 360, 30.0)
    assert [(i.kind, i.duration_s, i.head_s) for i in plan.items] == [("video", 3.5, 0.5), ("still", 2.5, 0.0), ("title", 4.0, 0.0)]
    assert plan.items[2].path.endswith("card_t_03.png") and plan.total_s == 10.0
    assert plan.skipped and "file missing" in plan.skipped[0]


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not on PATH")
def test_stitch_renders_a_playable_file(tmp_path):
    clip = make_tiny_mp4(tmp_path / "clip.mp4")
    card = write_png(tmp_path / "card.png", size=(100, 50))
    plan = StitchPlan(
        items=[
            PlanItem(bin="b1", kind="video", path=str(clip), duration_s=0.5, head_s=0.25),
            PlanItem(bin="b2", kind="still", path=str(card), duration_s=0.5),
        ],
        width=128, height=96, fps=8.0, skipped=[],
    )
    seen: list[float] = []
    out = stitch(plan, tmp_path / "out" / "cut.mp4", on_progress=seen.append)
    info = probe_video(out)
    assert (info["width"], info["height"]) == (128, 96)
    assert info["duration_s"] is not None and 0.8 <= info["duration_s"] <= 1.3
    assert seen and seen[-1] == 1.0
