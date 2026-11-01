"""thm_core.provenance: a generation record read back out of the media itself. The graph a
ComfyUI output carries is parsed, matched to a curated workflow, its loaders mapped to project
media, and the record rebuilt -- or a failed generation completed -- without ComfyUI."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from thm_core import Generation, MediaOrigin, adopt_media
from thm_core.provenance import (
    Embedded,
    match_workflow,
    parse_graph,
    read_embedded,
    reconstruct,
    recover_generation,
    resolve_loader,
)
from thm_core_fixtures import IMAGE_SIDECAR, add_bin, add_media, bind, image_workflow, make_project, make_store, node, workflows_dir
from thm_server_fixtures import write_png


# -- builders -------------------------------------------------------------------


def rendered_graph(loaders: dict[str, str], *, seed=4242, width=640, height=400, steps=8, prompt="<Picture 1> on the rail", negative="", frames=None, fps=None, injected_lora=None, video_loaders=None):
    """What the runner posted for img.json: the workflow's nodes plus the sampler wiring a
    real graph has, seed and size behind primitives the way THM-tagged workflows do it."""
    g = image_workflow(2)
    g["1"]["inputs"]["text"] = prompt
    for nid, value in loaders.items():
        g[nid]["inputs"]["image"] = value
    g["30"] = node("THM-Seed", "PrimitiveInt", {"value": seed})
    g["31"] = node("THM-Width", "PrimitiveInt", {"value": width})
    g["32"] = node("THM-Height", "PrimitiveInt", {"value": height})
    g["33"] = node("x", "PrimitiveInt", {"value": steps})
    g["34"] = node("KSampler", "KSampler", {"seed": ["30", 0], "steps": ["33", 0], "cfg": 3.5})
    g["35"] = node("Latent", "EmptyLatentImage", {"width": ["31", 0], "height": ["32", 0]})
    if negative:
        g["36"] = node("THM-NegativePrompt", "CLIPTextEncode", {"text": negative})
    if frames is not None:
        g["40"] = node("THM-FrameCount", "PrimitiveInt", {"value": frames})
    if fps is not None:
        g["41"] = node("THM-FrameRate", "PrimitiveFloat", {"value": fps})
    if injected_lora:
        name, strength = injected_lora
        g["50"] = node(f"Injected_{name}", "LoraLoaderModelOnly", {"lora_name": name, "strength_model": strength, "model": ["34", 0]})
    for nid, value in (video_loaders or {}).items():
        g[nid] = node("THM-VideoReference", "VHS_LoadVideo", {"video": value})
    return g


def png_with_graph(path: Path, graph: dict, snapshot: dict | None = None, size=(640, 400)) -> Path:
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", json.dumps(graph))
    if snapshot is not None:
        info.add_text("the_machine_snapshot", json.dumps({"thm": snapshot}))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (30, 60, 90)).save(path, pnginfo=info)
    return path


def mp4_with_graph(path: Path, graph: dict) -> Path | None:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=8", "-pix_fmt", "yuv420p",
         "-movflags", "use_metadata_tags", "-metadata", f"prompt={json.dumps(graph)}", str(path)],
        check=True, capture_output=True, timeout=60,
    )
    return path


def upload_name(path: Path) -> str:
    """The name the runner gives a file it uploads into ComfyUI's input dir (workflow_controls)."""
    info = os.stat(path)
    digest = hashlib.sha1(f"{os.path.abspath(path)}|{info.st_mtime_ns}|{info.st_size}".encode("utf-8")).hexdigest()[:10]
    return f"thm-refs/{path.stem[:48]}_{digest}{path.suffix} [input]"


@pytest.fixture
def world(tmp_path):
    """A project with img.json (two slots, <Picture n> tokens), a Mara bin holding two uploads, and
    a Shot bin that references both as @mara.png and @mara.png.2."""
    store = make_store(tmp_path)
    wdir = workflows_dir(tmp_path, {"img.json": image_workflow(2), "other.json": image_workflow(4)}, {"img.thm.toml": IMAGE_SIDECAR})
    project = store.new_project("Rail Dawn Promo")
    project.defaults.width, project.defaults.height = 1280, 720
    project.defaults.image_prompt = "crisp photo"
    mara = add_bin(project, "Mara")
    a = write_png(tmp_path / "out" / "rail-dawn-promo" / mara / f"{mara}_00001_.png")
    b = write_png(tmp_path / "media" / "b.png", (400, 640))
    m1 = add_media(project, mara, path=a.as_posix())
    m2 = add_media(project, mara, path=b.as_posix())
    shot = add_bin(project, "Shot")
    bind(project, shot, m1)
    bind(project, shot, m2)
    store.save(project)
    return type("W", (), dict(store=store, project=project, wdir=wdir, mara=mara, shot=shot, m1=m1, m2=m2, a=a, b=b, out_root=(tmp_path / "out").as_posix(), tmp=tmp_path))


# -- parsing --------------------------------------------------------------------


def test_parse_graph_reads_tagged_controls_through_links():
    g = rendered_graph({"10": "x [output]", "11": "y [output]"}, seed=7, width=512, height=256, steps=12, negative="blurry", frames=49, fps=16.0, injected_lora=("style.safetensors", 0.8), video_loaders={"45": "thm-refs/clip_0123456789.mp4 [input]"})
    g["60"] = node("THM-Lora", "LoraLoaderModelOnly", {"lora_name": "stock.safetensors", "strength_model": 1.0})
    rec = parse_graph(g)
    assert (rec.seed, rec.width, rec.height, rec.steps, rec.frames, rec.fps) == (7, 512, 256, 12, 49, 16.0)
    assert rec.prompt == "<Picture 1> on the rail" and rec.negative == "blurry"
    assert rec.image_loaders == {"10": "x [output]", "11": "y [output]"} and rec.video_loaders == {"45": "thm-refs/clip_0123456789.mp4 [input]"}
    assert [(l.name, l.strength) for l in rec.loras] == [("style.safetensors", 0.8)]
    assert list(rec.marker_loras) == ["60"]
    assert ("34", "KSampler") in rec.nodes


def test_parse_graph_falls_back_to_plain_inputs_when_untagged():
    g = {"3": node("KSampler", "KSampler", {"seed": 99, "steps": 20}), "5": node("Latent", "EmptyLatentImage", {"width": 768, "height": 512})}
    rec = parse_graph(g)
    assert (rec.seed, rec.steps, rec.width, rec.height) == (99, 20, 768, 512)
    assert rec.prompt == "" and rec.image_loaders == {}


def test_parse_graph_pairs_dual_lane_loras():
    g = {
        "1": node("Injected_High_a_high_noise.safetensors", "LoraLoaderModelOnly", {"lora_name": "a_high_noise.safetensors", "strength_model": 0.9}),
        "2": node("Injected_Low_a_low_noise.safetensors", "LoraLoaderModelOnly", {"lora_name": "a_low_noise.safetensors", "strength_model": 0.4}),
    }
    assert [(l.name, l.strength, l.strength_b) for l in parse_graph(g).loras] == [("a_high_noise.safetensors", 0.9, 0.4)]


# -- reading files ---------------------------------------------------------------


def test_read_embedded_png_and_missing(tmp_path):
    g = rendered_graph({})
    p = png_with_graph(tmp_path / "t.png", g, {"generation": "g_1", "workflow": "img.json"})
    emb = read_embedded(p)
    assert emb.graph["34"]["class_type"] == "KSampler" and emb.snapshot == {"generation": "g_1", "workflow": "img.json"}
    assert read_embedded(write_png(tmp_path / "plain.png")).empty
    assert read_embedded(tmp_path / "gone.png").empty
    assert read_embedded(tmp_path / "t.txt").empty


def test_read_embedded_ignores_1x_snapshot(tmp_path):
    info = PngImagePlugin.PngInfo()
    info.add_text("the_machine_snapshot", json.dumps({"generation": {"seed": 1}, "project": {}}))
    Image.new("RGB", (8, 8)).save(tmp_path / "old.png", pnginfo=info)
    assert read_embedded(tmp_path / "old.png").empty


def test_read_embedded_mp4(tmp_path):
    p = mp4_with_graph(tmp_path / "t.mp4", rendered_graph({}, seed=5))
    if p is None:
        pytest.skip("ffmpeg not on PATH")
    emb = read_embedded(p)
    assert emb.graph is not None and parse_graph(emb.graph).seed == 5 and emb.snapshot is None


# -- matching --------------------------------------------------------------------


def test_match_workflow_by_node_overlap_and_prefer(world):
    g = rendered_graph({"10": "x", "11": "y"})  # img.json's nodes plus the runner's
    m = match_workflow(g, world.wdir)
    assert m.name == "img.json" and m.kind == "image" and 0 < m.score < 1.0
    del g["11"]  # a loader nobody bound is dropped by the runner; still img.json
    assert match_workflow(g, world.wdir).name == "img.json"
    four = image_workflow(4)
    assert match_workflow(four, world.wdir).name == "other.json"
    tie = image_workflow(2)  # exactly img.json's nodes; other.json holds two more: img.json wins outright
    assert match_workflow(tie, world.wdir).name == "img.json"
    assert match_workflow({"1": node("Foo", "Bar", {})}, world.wdir) is None
    (world.wdir.parent / "workflows_image" / "twin.json").write_text(json.dumps(image_workflow(2)), encoding="utf-8")
    assert match_workflow(tie, world.wdir).name == "img.json"  # a tie falls to the name
    assert match_workflow(tie, world.wdir, prefer=["", "twin.json"]).name == "twin.json"  # unless the bin says otherwise


# -- resolving loaders -------------------------------------------------------------


def test_resolve_loader_output_rel_input_upload_and_absolute(world):
    p = world.project
    rel = f"rail-dawn-promo/{world.mara}/{world.mara}_00001_.png [output]"
    assert resolve_loader(rel, p, world.out_root).id == world.m1
    assert resolve_loader(rel, p, None).id == world.m1  # no output root configured: the path suffix still says
    assert resolve_loader(rel, p, "D:/elsewhere").id == world.m1  # a moved root, likewise
    assert resolve_loader(upload_name(world.b), p).id == world.m2
    assert resolve_loader(world.b.as_posix().upper(), p).id == world.m2
    assert resolve_loader("rail-dawn-promo/b_zz/b_zz_00001_.png [output]", p) is None
    assert resolve_loader("stock.png", p) is None
    assert resolve_loader("thm-refs/nope_0123456789.png [input]", p) is None
    assert resolve_loader("", p) is None


def test_resolve_loader_digest_settles_lookalike_uploads(world):
    twin = write_png(world.tmp / "elsewhere" / "b.png", (401, 641))
    m3 = add_media(world.project, world.mara, path=twin.as_posix())
    assert resolve_loader(upload_name(world.b), world.project).id == world.m2
    assert resolve_loader(upload_name(twin), world.project).id == m3


# -- the record -------------------------------------------------------------------


def _upload(world, path: Path, bin_id: str):
    media = adopt_media(world.project, path, origin=MediaOrigin(type="upload", bin=bin_id, note="fetched by hand"))
    world.project.bins_by_id[bin_id].media.append(media.id)
    return media


def test_reconstruct_fresh_record_from_graph(world):
    g = rendered_graph(
        {"10": f"rail-dawn-promo/{world.mara}/{world.mara}_00001_.png [output]", "11": upload_name(world.b)},
        seed=4242, width=640, height=360, steps=8, prompt="<Picture 1> and <Picture 2> at the rail\ncrisp photo", negative="blurry",
        injected_lora=("style.safetensors", 0.7),
    )
    take = _upload(world, png_with_graph(world.tmp / "in" / "take.png", g), world.shot)
    rec = reconstruct(world.project, take, read_embedded(take.path), generations=[], workflows_dir=world.wdir, output_root=world.out_root)
    gen = rec.generation
    assert not rec.attached and rec.workflow.name == "img.json"
    assert gen.bin == world.shot and gen.kind == "image" and gen.status == "done" and gen.outputs == [take.id]
    assert gen.workflow == "img.json" and gen.recovered_from == take.path
    # quarter: the graph's canvas is half the project's on both sides
    assert gen.params == {"seed": 4242, "width": 1280, "height": 720, "quarter": True, "steps": 8}
    assert [(r.media, r.handle, r.slot, r.ordinal) for r in gen.references] == [(world.m1, "mara.png", "10", 1), (world.m2, "mara.png.2", "11", 2)]
    assert gen.references[0].ref.startswith("r_")  # the bin's own reference, since it is still bound there
    assert gen.paths_by_slot == {"10": world.a.as_posix(), "11": world.b.as_posix()}
    assert gen.prompt_submitted == "<Picture 1> and <Picture 2> at the rail\ncrisp photo"
    assert gen.prompt_typed == "@mara.png and @mara.png.2 at the rail"  # tokens back to handles, global prompt off the end
    assert gen.negative_submitted == "blurry"
    assert [(l.name, l.strength) for l in gen.loras] == [("style.safetensors", 0.7)]
    assert gen.warnings[0]["kind"] == "recovered"


def test_reconstruct_mints_handles_for_media_the_bin_never_bound(world):
    """Uploaded into a bin that references nothing: handles come from the media's bin name, and
    a loader that names no project media is a warning, not a failure."""
    other = add_bin(world.project, "Other")
    g = rendered_graph({"10": upload_name(world.b), "11": "stock.png"}, prompt="<Picture 1> alone")
    take = _upload(world, png_with_graph(world.tmp / "in" / "t2.png", g), other)
    rec = reconstruct(world.project, take, read_embedded(take.path), generations=[], workflows_dir=world.wdir)
    gen = rec.generation
    assert [(r.media, r.handle, r.ref) for r in gen.references] == [(world.m2, "mara.png", "")]
    assert gen.prompt_typed == "@mara.png alone"
    assert any("stock.png" in w for w in rec.warnings) and gen.params["width"] == 640 and "quarter" not in gen.params


def test_reconstruct_length_from_frames_and_rate(world):
    g = rendered_graph({}, frames=121, fps=24.0)
    take = _upload(world, png_with_graph(world.tmp / "in" / "t3.png", g), world.shot)
    take.type = "video"  # a video record reads its length off the frame count the runner set (length_s * fps + 1)
    gen = reconstruct(world.project, take, read_embedded(take.path), generations=[], workflows_dir=world.wdir).generation
    assert gen.params["length_s"] == 5.0 and gen.params["fps"] == 24.0 and gen.kind == "video"


def test_reconstruct_attaches_to_the_failed_generation_by_seed_and_bin(world):
    """The timed-out render's record keeps its handles and typed prompt; the file completes it."""
    failed = Generation(id="g_timedout", bin=world.shot, kind="image", ts="2026-09-15T10:00:00Z", workflow="img.json",
                        prompt_typed="@mara.png at the rail", prompt_submitted="<Picture 1> at the rail\ncrisp photo",
                        params={"width": 1280, "height": 720, "seed": 4242}, status="failed",
                        warnings=[{"kind": "runner", "handle": "", "detail": "prompt x timed out after 3600s"}])
    older = failed.model_copy(update={"id": "g_older", "ts": "2026-09-14T10:00:00Z", "prompt_submitted": "something else"})
    other_bin = failed.model_copy(update={"id": "g_elsewhere", "bin": world.mara})
    done = failed.model_copy(update={"id": "g_done", "status": "done", "outputs": ["m_x"]})
    g = rendered_graph({"10": f"rail-dawn-promo/{world.mara}/{world.mara}_00001_.png [output]"}, seed=4242, prompt="<Picture 1> at the rail\ncrisp photo")
    take = _upload(world, png_with_graph(world.tmp / "in" / "t4.png", g), world.shot)
    rec = reconstruct(world.project, take, read_embedded(take.path), generations=[older, other_bin, done, failed], workflows_dir=world.wdir)
    gen = rec.generation
    assert rec.attached and gen.id == "g_timedout" and gen.status == "done" and gen.outputs == [take.id]
    assert gen.prompt_typed == "@mara.png at the rail" and gen.params == {"width": 1280, "height": 720, "seed": 4242, "steps": 8}
    assert gen.warnings[-1]["kind"] == "recovered" and gen.recovered_from == take.path
    # a different seed is a different render: a fresh record
    g2 = rendered_graph({}, seed=1)
    take2 = _upload(world, png_with_graph(world.tmp / "in" / "t5.png", g2), world.shot)
    assert not reconstruct(world.project, take2, read_embedded(take2.path), generations=[failed], workflows_dir=world.wdir).attached


def test_reconstruct_attaches_by_snapshot_generation_id(world):
    """THM's own snapshot names the generation: a take deleted and re-uploaded goes back onto it."""
    done = Generation(id="g_landed", bin=world.shot, kind="image", ts="2026-09-15T10:00:00Z", workflow="img.json", status="done", outputs=["m_gone"], params={"seed": 9})
    g = rendered_graph({}, seed=9)
    take = _upload(world, png_with_graph(world.tmp / "in" / "t6.png", g, {"generation": "g_landed", "workflow": "img.json", "seed": 9, "params": {"seed": 9, "width": 1280, "height": 720}}), world.shot)
    rec = reconstruct(world.project, take, read_embedded(take.path), generations=[done], workflows_dir=world.wdir)
    assert rec.attached and rec.generation.id == "g_landed" and rec.generation.outputs == ["m_gone", take.id]
    # already this generation's output: nothing to do
    done.outputs.append(take.id)
    assert reconstruct(world.project, take, read_embedded(take.path), generations=[done], workflows_dir=world.wdir) is None


def test_reconstruct_snapshot_workflow_and_paths_win_over_the_graph(world):
    snap = {"generation": "g_unknown", "workflow": "img.json", "prompt": "<Picture 1> dusk", "negative": "haze", "seed": 3,
            "params": {"seed": 3, "width": 1280, "height": 720, "quarter": True}, "paths_by_slot": {"10": world.b.as_posix()}}
    g = rendered_graph({"10": "somewhere/else.png [output]"}, seed=3, prompt="<Picture 1> dusk")
    take = _upload(world, png_with_graph(world.tmp / "in" / "t7.png", g, snap), world.shot)
    gen = reconstruct(world.project, take, read_embedded(take.path), generations=[], workflows_dir=world.wdir).generation
    assert gen.workflow == "img.json" and gen.negative_submitted == "haze"
    assert [(r.media, r.handle) for r in gen.references] == [(world.m2, "mara.png.2")]
    assert gen.params == {"seed": 3, "width": 1280, "height": 720, "quarter": True, "steps": 8}


def test_reconstruct_nothing_for_a_file_without_a_graph(world):
    take = _upload(world, write_png(world.tmp / "in" / "plain.png"), world.shot)
    assert reconstruct(world.project, take, read_embedded(take.path), generations=[], workflows_dir=world.wdir) is None
    assert reconstruct(world.project, take, Embedded(), generations=[], workflows_dir=world.wdir) is None


def test_recover_generation_writes_the_cache(world):
    g = rendered_graph({"10": upload_name(world.b)}, seed=77)
    take = _upload(world, png_with_graph(world.tmp / "in" / "t8.png", g), world.shot)
    rec = recover_generation(world.store, world.project, take, workflows_dir=world.wdir, output_root=world.out_root)
    gen = rec.generation
    assert take.origin.type == "generation" and take.origin.generation == gen.id and take.origin.bin == world.shot
    assert take.origin.note == "fetched by hand"  # the upload's provenance stays
    assert take.take == 1 and world.project.bins_by_id[world.shot].counters["take"] == 1
    assert world.store.read_generation(world.project, gen.id).outputs == [take.id]
    # now it has a record: a second call reads nothing
    assert recover_generation(world.store, world.project, take, workflows_dir=world.wdir) is None
    # a file with no graph: nothing, and the media stays an upload
    plain = _upload(world, write_png(world.tmp / "in" / "plain2.png"), world.shot)
    assert recover_generation(world.store, world.project, plain, workflows_dir=world.wdir) is None and plain.origin.type == "upload"
