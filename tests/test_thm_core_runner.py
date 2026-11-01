"""thm_core.runner: spec building, status-file reading, landing outputs as takes. No ComfyUI, no processes."""

import json
from pathlib import Path

import pytest

from thm_core import (
    ComfyTarget,
    Generation,
    MediaOrigin,
    adopt_media,
    build_generation_spec,
    filter_outputs,
    job_paths,
    land_outputs,
    log_tail,
    map_status,
    read_status,
    resolve_comfy,
    runner_script,
)
from thm_core.config import ComfySettings as ConfigComfy
from thm_core_fixtures import add_bin, add_media, make_project
from thm_server_fixtures import write_png


def _gen(bin_id: str, **overrides) -> Generation:
    base = dict(id="g_test0001", bin=bin_id, kind="image", ts="2026-09-07T10:00:00Z", workflow="img.json",
                prompt_typed="@mara.png at dawn", prompt_submitted="<Picture 1> at dawn", negative_submitted="blurry",
                prelude=["image1: mara (character)"], paths_by_slot={"10": "D:/refs/mara.png"})
    base.update(overrides)
    return Generation(**base)


# -- comfy target ---------------------------------------------------------------


def test_resolve_comfy_prefers_project_then_config():
    p = make_project()
    fallback = ConfigComfy(api_base="http://cfg:8188", output_root="D:/cfg/out", timeout_seconds=120)
    t = resolve_comfy(p, fallback)
    assert (t.api_base, t.output_root, t.timeout_seconds) == ("http://cfg:8188", "D:/cfg/out", 120)
    p.comfy.api_base = "http://proj:8188"
    p.comfy.output_root = "E:/proj/out"
    t = resolve_comfy(p, fallback)
    assert (t.api_base, t.output_root) == ("http://proj:8188", "E:/proj/out")
    assert t.as_spec() == {"api_base": "http://proj:8188", "output_root": "E:/proj/out", "timeout_seconds": 120}


def test_resolve_comfy_refuses_empty_output_root():
    with pytest.raises(ValueError, match="output_root"):
        resolve_comfy(make_project(), ConfigComfy(api_base="http://x", output_root=""))


# -- spec -----------------------------------------------------------------------


def test_build_generation_spec_shape(tmp_path):
    p = make_project()
    bin_id = add_bin(p, "Image 1", "img.json")
    bin_ = p.bins_by_id[bin_id]
    gen = _gen(bin_id)
    comfy = ComfyTarget(api_base="http://x:8188", output_root=str(tmp_path / "out"), timeout_seconds=99)
    spec = build_generation_spec(
        p, gen, output_dir_id=bin_id, label=f"{bin_.name} {gen.id}", comfy=comfy, workflows_dir=tmp_path / "wf"
    )

    assert Path(spec["workflow"]).is_absolute() and spec["workflow"].endswith("wf/img.json")
    assert spec["prompt"] == "<Picture 1> at dawn" and spec["prelude"] == ["image1: mara (character)"]
    assert spec["negative"] == "blurry"
    assert spec["paths_by_slot"] == {"10": "D:/refs/mara.png"}
    assert "loras" not in spec  # tags in the prompt text carry them
    assert spec["output_dir"] == (tmp_path / "out" / p.slug / bin_id).as_posix()
    assert spec["filename_prefix"] == bin_id
    assert spec["label"] == f"{bin_.name} {gen.id}" and spec["label"].isascii()
    assert spec["comfy"] == {"api_base": "http://x:8188", "output_root": str(tmp_path / "out"), "timeout_seconds": 99}
    assert spec["id"] == gen.id and spec["project"] == p.slug
    # the seed is minted once and written back so the running record reproduces the render
    assert isinstance(spec["params"]["seed"], int) and gen.params["seed"] == spec["params"]["seed"]
    again = build_generation_spec(
        p, gen, output_dir_id=bin_id, label=f"{bin_.name} {gen.id}", comfy=comfy, workflows_dir=tmp_path / "wf"
    )
    assert again["params"]["seed"] == spec["params"]["seed"]
    # the spec round-trips through the scripts' loader (conftest puts scripts/ on sys.path)
    from generation_spec import load_generation_spec

    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert load_generation_spec(path)["workflow"] == spec["workflow"]


def test_build_generation_spec_keeps_video_params_and_absolute_workflow(tmp_path):
    p = make_project()
    bin_id = add_bin(p, "Video 1", "vid.json", length_s=5)
    bin_ = p.bins_by_id[bin_id]
    gen = _gen(bin_id, kind="video", workflow=str(tmp_path / "abs" / "vid.json"), params={"length_s": 5, "seed": 42})
    spec = build_generation_spec(
        p, gen, output_dir_id=bin_id, label=bin_.name, comfy=ComfyTarget("http://x", "D:/out"), workflows_dir=tmp_path
    )
    assert spec["params"] == {"length_s": 5, "seed": 42}
    assert spec["workflow"] == (tmp_path / "abs" / "vid.json").resolve().as_posix()


def test_video_prompts_get_their_own_execution_timeout(tmp_path):
    """The runner's timeout is execution time; video flows may run past an hour, so a video
    generation's spec carries the video limit and an image's the plain one."""
    fallback = ConfigComfy(api_base="http://cfg:8188", output_root="D:/cfg/out", timeout_seconds=3600, video_timeout_seconds=14400)
    t = resolve_comfy(make_project(), fallback)
    assert (t.timeout_for("image"), t.timeout_for("video")) == (3600, 14400)
    assert t.as_spec("video")["timeout_seconds"] == 14400 and t.as_spec()["timeout_seconds"] == 3600
    # no video limit configured: both kinds share the one timeout
    assert ComfyTarget("http://x", "D:/out", timeout_seconds=99).timeout_for("video") == 99

    p = make_project()
    bin_id = add_bin(p, "Video 1", "vid.json", length_s=5)
    gen = _gen(bin_id, kind="video", workflow=str(tmp_path / "vid.json"), params={"length_s": 5, "seed": 1})
    spec = build_generation_spec(p, gen, output_dir_id=bin_id, label="v", comfy=t, workflows_dir=tmp_path)
    assert spec["comfy"]["timeout_seconds"] == 14400


def test_build_generation_spec_keeps_a_migrated_bins_old_id_as_the_path(tmp_path):
    """A bin upgraded from a schema-3 item keeps its it_ id, so its output folder stays put."""
    p = make_project()
    gen = _gen("it_001")
    comfy = ComfyTarget(api_base="http://x", output_root=str(tmp_path / "out"))
    spec = build_generation_spec(p, gen, output_dir_id="it_001", label="Mara g_test0001", comfy=comfy, workflows_dir=tmp_path / "wf")
    assert spec["output_dir"] == (tmp_path / "out" / p.slug / "it_001").as_posix()
    assert spec["filename_prefix"] == "it_001" and spec["label"] == "Mara g_test0001"


def test_runner_script_and_job_paths(tmp_path):
    assert runner_script("image", tmp_path).name == "run_images.py"
    assert runner_script("video", tmp_path).name == "run_video.py"
    paths = job_paths(tmp_path / "x-files", "g_1")
    assert paths.spec == tmp_path / "x-files" / "jobs" / "g_1.json"
    assert paths.status.name == "g_1.status.json" and paths.log.name == "g_1.log"


# -- status file ----------------------------------------------------------------


def test_read_status_coerces_and_tolerates_bad_files(tmp_path):
    path = tmp_path / "s.json"
    assert read_status(path) is None
    path.write_text("{not json", encoding="utf-8")
    assert read_status(path) is None
    path.write_text(json.dumps({"pid": "123", "status": "running", "current_task": "Queued", "progress_percent": "5.0",
                                "error": None, "last_update": "2026-09-07T10:00:00", "outputs": []}), encoding="utf-8")
    st = read_status(path)
    assert st.status == "running" and st.task == "Queued" and st.progress == 5.0 and st.pid == 123 and st.error is None
    path.write_text(json.dumps({"status": "completed", "progress_percent": None,
                                "outputs": [{"path": "D:/a.png", "node_id": "9"}, {"nope": 1}, "junk"]}), encoding="utf-8")
    st = read_status(path)
    assert st.progress is None and st.outputs == [{"path": "D:/a.png", "node_id": "9"}] and st.prompt_id is None
    assert st.exec_seconds is None  # a runner from before the timing was kept
    path.write_text(json.dumps({"status": "failed", "error": "prompt abc timed out after 3600s of execution", "prompt_id": "abc"}), encoding="utf-8")
    assert read_status(path).prompt_id == "abc"
    # the final write carries ComfyUI's execution time; a bad value reads as unknown, not a crash
    path.write_text(json.dumps({"status": "completed", "prompt_id": "abc", "exec_seconds": 42.3}), encoding="utf-8")
    assert read_status(path).exec_seconds == 42.3
    path.write_text(json.dumps({"status": "completed", "exec_seconds": "fast"}), encoding="utf-8")
    assert read_status(path).exec_seconds is None
    path.write_text(json.dumps({"status": ""}), encoding="utf-8")
    assert read_status(path) is None


def test_map_status():
    assert map_status("running") == "running"
    assert map_status("completed") == "done"
    assert map_status("failed") == "failed"
    assert map_status("weird") == "running"


# -- landing --------------------------------------------------------------------


def test_filter_outputs_by_generation_kind():
    outs = [{"path": "D:/x/take.mp4"}, {"path": "D:/x/frame_001.png"}, {"path": "D:/x/frame_002.png"}]
    assert filter_outputs("video", outs) == (["D:/x/take.mp4"], [])
    assert filter_outputs("image", outs) == (["D:/x/frame_001.png", "D:/x/frame_002.png"], [])
    paths, warnings = filter_outputs("video", outs[1:])
    assert paths == ["D:/x/frame_001.png", "D:/x/frame_002.png"] and "no video output" in warnings[0]
    assert filter_outputs("video", []) == ([], [])


def test_land_outputs_appends_takes_and_selects_newest(tmp_path):
    p = make_project()
    bin_id = add_bin(p, "Image 1", "img.json")
    bin_ = p.bins_by_id[bin_id]
    gen = _gen(bin_id)
    a = write_png(tmp_path / "out" / "s_00001_.png", (64, 48), (10, 20, 30))
    b = write_png(tmp_path / "out" / "s_00002_.png", (64, 48), (40, 50, 60))
    cache = tmp_path / "cache"

    landed, warnings = land_outputs(p, bin_, gen, [{"path": str(a)}, {"path": str(b)}], cache_dir=cache, now="2026-09-07T10:05:00Z")

    assert [m.path for m in landed] == [Path(a).resolve().as_posix(), Path(b).resolve().as_posix()]
    assert bin_.media == [m.id for m in landed] and bin_.selected == landed[-1].id  # the first take leads: nothing was chosen yet
    assert gen.status == "done" and gen.outputs == bin_.media and warnings == []
    assert all(m.origin.type == "generation" and m.origin.generation == gen.id and m.origin.bin == bin_id for m in landed)
    assert landed[0].width == 64 and landed[0].created == "2026-09-07T10:05:00Z"
    assert any(cache.glob("*_grid.webp"))
    assert [m.take for m in landed] == [1, 2] and bin_.counters["take"] == 2
    assert bin_.unseen == bin_.media  # new until the user looks


def test_landing_after_uploads_keeps_the_gallery_order_and_numbers_only_takes(tmp_path):
    p = make_project()
    bin_id = add_bin(p, "Mara", "img.json")
    existing = add_media(p, bin_id)
    bin_ = p.bins_by_id[bin_id]
    a = write_png(tmp_path / "out" / "it_001_00001_.png", (64, 48), (10, 20, 30))
    landed, _ = land_outputs(p, bin_, _gen(bin_id), [{"path": str(a)}], cache_dir=tmp_path / "cache")
    assert bin_.media == [existing, landed[0].id] and bin_.selected == landed[0].id  # no winner was chosen yet
    assert p.media_by_id[existing].take is None and landed[0].take == 1


def test_take_numbers_survive_deletes(tmp_path):
    from thm_core.commands import _cascade_delete_media

    p = make_project()
    bin_id = add_bin(p, "Image 1", "img.json")
    bin_ = p.bins_by_id[bin_id]
    a = write_png(tmp_path / "out" / "t_00001_.png", (64, 48), (10, 20, 30))
    b = write_png(tmp_path / "out" / "t_00002_.png", (64, 48), (40, 50, 60))
    c = write_png(tmp_path / "out" / "t_00003_.png", (64, 48), (70, 80, 90))
    first, _ = land_outputs(p, bin_, _gen(bin_id), [{"path": str(a)}, {"path": str(b)}], cache_dir=tmp_path / "c")
    _cascade_delete_media(p, first[0].id)
    assert p.media_by_id[first[1].id].take == 2  # take 2 is still take 2 with take 1 gone
    third, _ = land_outputs(p, bin_, _gen(bin_id), [{"path": str(c)}], cache_dir=tmp_path / "c")
    assert third[0].take == 3 and bin_.media == [first[1].id, third[0].id]


def test_land_outputs_dedupes_identical_bytes_and_reports_missing(tmp_path):
    p = make_project()
    bin_id = add_bin(p, "Image 1", "img.json")
    bin_ = p.bins_by_id[bin_id]
    first = write_png(tmp_path / "first.png", (32, 32), (1, 2, 3))
    existing = adopt_media(p, first, origin=MediaOrigin(type="generation", generation="g_old", bin=bin_id))
    existing.take = 1
    bin_.media.append(existing.id)
    same_bytes = tmp_path / "again.png"
    same_bytes.write_bytes(Path(first).read_bytes())
    fresh = write_png(tmp_path / "fresh.png", (32, 32), (9, 9, 9))
    gen = _gen(bin_id)

    landed, warnings = land_outputs(
        p, bin_, gen, [{"path": str(same_bytes)}, {"path": str(tmp_path / "gone.png")}, {"path": str(fresh)}], cache_dir=tmp_path / "c"
    )

    assert [m.path for m in landed] == [Path(fresh).resolve().as_posix()]
    assert bin_.media == [existing.id, landed[0].id] and bin_.selected == landed[0].id
    assert gen.status == "done" and gen.outputs == [landed[0].id]
    assert any("byte-identical to take 1" in w for w in warnings) and any("gone.png" in w for w in warnings)
    assert [w["kind"] for w in gen.warnings] == ["landing", "landing"]


def test_land_outputs_with_nothing_usable_fails_the_generation(tmp_path):
    p = make_project()
    bin_id = add_bin(p, "Video 1", "vid.json", length_s=5)
    gen = _gen(bin_id, kind="video", workflow="vid.json")
    landed, warnings = land_outputs(p, p.bins_by_id[bin_id], gen, [{"path": str(tmp_path / "missing.mp4")}], cache_dir=tmp_path / "c")
    assert landed == [] and gen.status == "failed" and gen.outputs == []
    assert p.bins_by_id[bin_id].media == [] and warnings[-1] == "no output landed as a take"


def test_log_tail(tmp_path):
    assert log_tail(tmp_path / "nope.log") == ""
    log = tmp_path / "r.log"
    log.write_text("\n".join(f"line {i}" for i in range(30)) + "\n\n", encoding="utf-8")
    tail = log_tail(log, lines=3)
    assert tail == "line 27\nline 28\nline 29"


def test_a_later_take_lands_as_new_not_as_the_winner(tmp_path):
    """Once a bin has a winner, a landing take is new and waits to be chosen; it never moves the
    winner by itself."""
    p = make_project()
    bin_id = add_bin(p, "Mara", "img.json")
    bin_ = p.bins_by_id[bin_id]
    first, _ = land_outputs(p, bin_, _gen(bin_id), [{"path": str(write_png(tmp_path / "a.png", (32, 32), (1, 2, 3)))}], cache_dir=tmp_path / "c")
    assert bin_.selected == first[0].id
    second, _ = land_outputs(p, bin_, _gen(bin_id), [{"path": str(write_png(tmp_path / "b.png", (32, 32), (4, 5, 6)))}], cache_dir=tmp_path / "c")
    assert bin_.selected == first[0].id and second[0].id in bin_.unseen
