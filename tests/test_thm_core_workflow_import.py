"""Importing a workflow the user ran in ComfyUI: tags proposed from the graph, both formats kept."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from thm_core import workflow_import as wi
from thm_core.enhancer import SYSTEM_PROMPT_TITLE, system_prompt_for, workflow_system_prompt

FIXTURES = Path(__file__).parent / "fixtures"
SYSTEM = "You are a format compiler for this model. Keep every @tag."


def _n(cls, title=None, **inputs):
    return {"inputs": inputs, "class_type": cls, "_meta": {"title": title or cls}}


def wan_flf() -> dict:
    return {
        "1": _n("UNETLoader", unet_name="wan.safetensors", weight_dtype="default"),
        "2": _n("CLIPLoader", clip_name="umt5.safetensors", type="wan"),
        "3": _n("VAELoader", vae_name="wan_vae.safetensors"),
        "4": _n("CLIPTextEncode", "Positive", text="a cat", clip=["2", 0]),
        "5": _n("CLIPTextEncode", "Negative", text="blur", clip=["2", 0]),
        "6": _n("LoadImage", image="a.png"),
        "7": _n("LoadImage", image="b.png"),
        "8": _n("WanFirstLastFrameToVideo", positive=["4", 0], negative=["5", 0], vae=["3", 0], start_image=["6", 0], end_image=["7", 0], width=832, height=480, length=81, batch_size=1),
        "9": _n("KSampler", model=["1", 0], positive=["8", 0], negative=["8", 1], latent_image=["8", 2], seed=5, steps=20, cfg=5.0, sampler_name="euler", scheduler="simple", denoise=1.0),
        "10": _n("VAEDecode", samples=["9", 0], vae=["3", 0]),
        "11": _n("CreateVideo", images=["10", 0], fps=16),
        "12": _n("SaveVideo", video=["11", 0], filename_prefix="video/out", format="auto", codec="auto"),
    }


def kling() -> dict:
    return {
        "1": _n("LoadImage", image="a.png"),
        "2": _n("LoadImage", image="b.png"),
        "3": _n("KlingStartEndFrameNode", prompt="a cat", negative_prompt="", start_frame=["1", 0], end_frame=["2", 0], mode="5s"),
        "4": _n("SaveVideo", video=["3", 0], filename_prefix="video/out", format="auto", codec="auto"),
    }


def sdxl(**titles) -> dict:
    g = {
        "1": _n("CheckpointLoaderSimple", ckpt_name="sdxl.safetensors"),
        "2": _n("CLIPTextEncode", titles.get("pos"), text="a cat", clip=["1", 1]),
        "3": _n("CLIPTextEncode", titles.get("neg"), text="blur", clip=["1", 1]),
        "4": _n("EmptyLatentImage", width=1024, height=1024, batch_size=1),
        "5": _n("KSampler", model=["1", 0], positive=["2", 0], negative=["3", 0], latent_image=["4", 0], seed=1, steps=20, cfg=6.0, sampler_name="euler", scheduler="normal", denoise=1.0),
        "6": _n("VAEDecode", samples=["5", 0], vae=["1", 2]),
        "7": _n("SaveImage", titles.get("save"), images=["6", 0], filename_prefix="out"),
    }
    return g


def tags_of(res) -> dict[str, str]:
    return {t.node: t.tag for t in res.tags}


def test_builtin_enhancer_is_taken_out_and_its_system_prompt_kept():
    """The real MOSS run: TextGenerate rewrites the user text through a switch and a text preview."""
    run = json.loads((FIXTURES / "comfy_run_textgen_enhancer.json").read_text(encoding="utf-8"))
    system_text = run["api"]["27"]["inputs"]["value"]
    res = wi.build_import(wi.Harvest(run["api"], run["ui"], "run:x"), name="moss")
    assert res.enhancer["mode"] == "builtin_detached" and res.enhancer["node"] == "27"
    assert res.enhancer["removed"] == ["28", "34", "35"]  # the LLM, the text preview, the refine switch
    for gone in ("28", "34", "35"):
        assert gone not in res.api
    assert res.api["30"]["inputs"]["text"] == ["26", 0]  # the encoder reads the raw user text again
    assert workflow_system_prompt(res.api) == system_text.strip()
    t = tags_of(res)
    assert t["26"] == "THM-Prompt" and t["37"] == "THM-NegativePrompt" and t["9"] == "THM-SaveImage"
    assert t["36"] == "THM-ImageSize" and t["32"] == "THM-Seed"
    # the UI copy: the LLM branch muted (not deleted), the encoder rewired, the titles match by id
    ui = {n["id"]: n for n in res.ui["nodes"]}
    assert [i for i in (28, 34, 35) if ui[i]["mode"] == 2] == [28, 34, 35]
    link = next(i["link"] for i in ui[30]["inputs"] if i["name"] == "text")
    assert next(lk for lk in res.ui["links"] if lk[0] == link)[1] == 26
    assert ui[27]["title"] == SYSTEM_PROMPT_TITLE and ui[26]["title"] == "THM-Prompt"
    # nothing feeds the system prompt node any more in the UI, and it feeds nothing
    assert not any(lk[1] == 27 for lk in res.ui["links"])


def test_keep_builtin_enhancer_leaves_the_graph_flow_alone():
    run = json.loads((FIXTURES / "comfy_run_textgen_enhancer.json").read_text(encoding="utf-8"))
    res = wi.build_import(wi.Harvest(run["api"], run["ui"], "run:x"), name="moss", keep_builtin_enhancer=True)
    assert res.enhancer["mode"] == "builtin_kept" and "28" in res.api
    assert workflow_system_prompt(res.api)  # copied into a THM node of its own
    assert res.enhancer["node"] not in ("27", "28")


def test_video_first_last_frame_graph():
    res = wi.build_import(wi.Harvest(wan_flf(), None, "run:x"), name="wan-flf", system_prompt=SYSTEM)
    assert res.kind == "video"
    t = tags_of(res)
    assert (t["4"], t["5"], t["6"], t["7"], t["8"], t["9"], t["12"]) == (
        "THM-Prompt", "THM-NegativePrompt", "THM-StartFrame", "THM-EndFrame", "THM-VideoGenerator", "THM-Seed", "THM-SaveVideo",
    )
    marker = res.lora["marker"]
    assert t[marker] == "THM-LoraAfterThisNode" and res.api["9"]["inputs"]["model"] == [marker, 0]
    assert res.api[marker]["inputs"]["model"] == ["1", 0]
    assert res.capabilities["supports_start_frame"] and res.capabilities["supports_end_frame"]
    assert any("frame rate" in b for b in res.baked)
    assert any("ui graph" in n.lower() for n in res.notes)


def test_partner_node_graph_tags_the_partner_and_its_frames():
    res = wi.build_import(wi.Harvest(kling(), None, "png"), name="kling", system_prompt=SYSTEM)
    t = tags_of(res)
    assert t == {"1": "THM-StartFrame", "2": "THM-EndFrame", "3": "THM-PartnerNode", "4": "THM-SaveVideo"}
    assert res.lora["marker"] is None and res.capabilities["has_negative"]


def test_user_tags_win_and_are_reported_as_kept():
    res = wi.build_import(wi.Harvest(sdxl(pos="THM-Prompt", save="THM-SaveImage"), None, "x"), name="sdxl", system_prompt=SYSTEM)
    t = tags_of(res)
    assert "2" not in t and "7" not in t
    assert {k["node"] for k in res.kept} == {"2", "7"}
    assert t["3"] == "THM-NegativePrompt" and t["4"] == "THM-ImageSize"


def test_overrides_add_and_drop_tags():
    res = wi.build_import(wi.Harvest(sdxl(), None, "x"), name="sdxl", system_prompt=SYSTEM, tags={"5": "THM-KSampler"}, untag=["4"], lora_marker=False)
    t = tags_of(res)
    assert t["5"] == "THM-KSampler" and "4" not in t
    assert res.api["5"]["_meta"]["title"] == "THM-KSampler" and res.api["4"]["_meta"]["title"] == "EmptyLatentImage"
    with pytest.raises(wi.ImportRefused) as e:
        wi.build_import(wi.Harvest(sdxl(), None, "x"), name="sdxl", system_prompt=SYSTEM, tags={"99": "THM-Seed"})
    assert e.value.code == "unknown_node"


def test_no_enhancer_needs_a_system_prompt_and_uses_it():
    with pytest.raises(wi.ImportRefused) as e:
        wi.build_import(wi.Harvest(sdxl(), None, "x"), name="sdxl")
    assert e.value.code == "system_prompt_needed"
    res = wi.build_import(wi.Harvest(sdxl(), None, "x"), name="sdxl", system_prompt=SYSTEM)
    assert res.enhancer["mode"] == "supplied" and workflow_system_prompt(res.api) == SYSTEM


def test_reimport_keeps_the_graphs_own_system_prompt(tmp_path):
    first = wi.build_import(wi.Harvest(sdxl(), None, "x"), name="sdxl", system_prompt=SYSTEM)
    again = wi.build_import(wi.Harvest(first.api, None, "x"), name="sdxl")
    assert again.enhancer["mode"] == "existing" and again.tags == []  # every tag is the user's now
    path = tmp_path / "sdxl.json"
    path.write_text(json.dumps(again.api), encoding="utf-8")
    assert system_prompt_for(path, "image") == (SYSTEM, "workflow")


def test_preview_image_becomes_a_save_node_and_no_output_refuses():
    g = sdxl()
    g["7"]["class_type"] = "PreviewImage"
    del g["7"]["inputs"]["filename_prefix"]
    res = wi.build_import(wi.Harvest(g, None, "x"), name="sdxl", system_prompt=SYSTEM)
    assert res.api["7"]["class_type"] == "SaveImage" and tags_of(res)["7"] == "THM-SaveImage"
    del g["7"]
    with pytest.raises(wi.ImportRefused) as e:
        wi.build_import(wi.Harvest(g, None, "x"), name="sdxl", system_prompt=SYSTEM)
    assert e.value.code == "no_save_node"


def test_subgraph_node_titles_are_set_inside_the_definition():
    api = sdxl()
    api["50:2"] = api.pop("2")
    api["5"]["inputs"]["positive"] = ["50:2", 0]
    ui = {
        "nodes": [{"id": 50, "type": "sg-1", "mode": 0, "inputs": [], "outputs": []}],
        "links": [],
        "definitions": {"subgraphs": [{"id": "sg-1", "nodes": [{"id": 2, "type": "CLIPTextEncode", "mode": 0, "inputs": [], "outputs": []}]}]},
    }
    res = wi.build_import(wi.Harvest(api, ui, "x"), name="sg", system_prompt=SYSTEM, lora_marker=False)
    assert tags_of(res)["50:2"] == "THM-Prompt"
    assert res.ui["definitions"]["subgraphs"][0]["nodes"][0]["title"] == "THM-Prompt"


def test_harvest_from_history_and_png_never_keeps_extra_data():
    api, ui = sdxl(), {"nodes": [], "links": []}
    entry = {"prompt": [1, "p1", api, {"extra_pnginfo": {"workflow": ui}, "api_key_comfy_org": "secret"}, ["7"]]}
    h = wi.harvest_history_entry("p1", entry)
    assert h.api == api and h.ui == ui and h.source == "run:p1"
    res = wi.build_import(h, name="sdxl", system_prompt=SYSTEM)
    assert "secret" not in json.dumps(res.api) + json.dumps(res.ui)
    # THM's own renders are not the user's workflows, and never offered
    thm = {"prompt": [1, "p2", api, {"machine_ui_project": "x", "machine_ui_label": "gen-1"}, []]}
    assert wi.run_summary("p2", thm) is None
    with pytest.raises(wi.ImportRefused):
        wi.harvest_history_entry("p2", thm)
    assert wi.run_summary("p1", entry)["kind"] == "image"

    info = PngInfo()
    info.add_text("prompt", json.dumps(api))
    info.add_itxt("workflow", json.dumps(ui))
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "PNG", pnginfo=info)
    p = wi.harvest_png(buf.getvalue())
    assert p.api == api and p.ui == ui
    with pytest.raises(wi.ImportRefused):
        wi.harvest_png(b"not a png")


@pytest.mark.parametrize("name", ["", "../x", "THM-x", "_x", "a/b"])
def test_bad_names_refuse(name):
    with pytest.raises(wi.ImportRefused):
        wi.check_name(name)
    assert wi.check_name("krea-portrait.json") == "krea-portrait"
