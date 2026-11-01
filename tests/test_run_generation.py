"""THM entry mode: ``run_images.py --generation`` and ``run_video.py --generation`` offline.

HTTP is stubbed: ``requests.post`` captures the posted graph and returns a prompt id,
``requests.get`` returns a synthetic history entry. Reference images live under the
fake ComfyUI output root so the loader annotation rewrites them instead of uploading.
"""

import json
import os
from pathlib import Path

import pytest

from scripts import run_images, run_video
from thm_core_fixtures import image_workflow, node


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code == 200
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def _stub_http(monkeypatch, module, history_entry):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse({"prompt_id": "pid-1"})

    def fake_get(url, timeout=None, **kwargs):
        captured.setdefault("gets", []).append(url)
        return _FakeResponse({"pid-1": history_entry})

    monkeypatch.setattr(module.requests, "post", fake_post)
    monkeypatch.setattr(module.comfy_history.requests, "get", fake_get)
    return captured


def _world(tmp_path):
    output_root = tmp_path / "comfy_out"
    output_dir = output_root / "p_x" / "s_01"
    output_dir.mkdir(parents=True)
    refs = output_root / "refs"
    refs.mkdir()
    a = refs / "a.png"
    b = refs / "b.png"
    a.write_bytes(b"png")
    b.write_bytes(b"png")
    return output_root, output_dir, str(a), str(b)


def _spec(tmp_path, workflow, output_root, output_dir, **overrides):
    wf_path = tmp_path / "wf.json"
    wf_path.write_text(json.dumps(workflow), encoding="utf-8")
    spec = {
        "id": "g_0a1",
        "project": "p_x",
        "workflow": str(wf_path),
        "prompt": "a cat on a rail",
        "negative": "blurry",
        "prelude": [],
        "paths_by_slot": {},
        "params": {},
        "loras": [],
        "output_dir": str(output_dir),
        "filename_prefix": "take",
        "label": "Video 1 take",
        "comfy": {"api_base": "http://comfy.test:8188", "output_root": str(output_root), "timeout_seconds": 5},
    }
    spec.update(overrides)
    spec_path = tmp_path / "gen.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return spec_path


def _history(node_id, key, filename, subfolder, *, error=None):
    status = {"status_str": "success", "completed": True, "messages": []}
    if error:
        status = {
            "status_str": "error",
            "completed": False,
            "messages": [["execution_error", {"prompt_id": "pid-1", "exception_message": error}]],
        }
    return {
        "outputs": {node_id: {key: [{"filename": filename, "subfolder": subfolder, "type": "output"}]}},
        "status": status,
    }


# -- images -------------------------------------------------------------------


def _image_graph():
    wf = image_workflow(2)
    wf["2"] = node("THM-NegativePrompt", "CLIPTextEncode", {"text": ""})
    wf["5"] = node("THM-Seed", "KSampler", {"seed": 0, "steps": 20, "cfg": 7.0})
    wf["9"] = node("THM-SaveImage", "SaveImage", {"filename_prefix": "", "images": ["5", 0]})
    return wf


def test_run_images_generation_posts_graph_and_writes_outputs(tmp_path, monkeypatch, capsys):
    output_root, output_dir, ref_a, _ = _world(tmp_path)
    out_file = output_dir / "take_00001_.png"
    out_file.write_bytes(b"png")
    captured = _stub_http(monkeypatch, run_images, _history("9", "images", out_file.name, "p_x/s_01"))
    monkeypatch.setattr(run_images, "wait_for_file_stable", lambda path, timeout_s=0: True)
    injected = []
    monkeypatch.setattr(run_images, "inject_metadata_png", lambda path, snap: injected.append((path, snap)))

    spec_path = _spec(
        tmp_path, _image_graph(), output_root, output_dir,
        prelude=["image1 is a character reference."],
        paths_by_slot={"10": ref_a},
        params={"seed": 1234, "steps": 8},
    )
    status_file = tmp_path / "status.json"
    run_images.run_generation(str(spec_path), status_file_override=str(status_file))

    graph = captured["payload"]["prompt"]
    assert graph["1"]["inputs"]["text"] == "image1 is a character reference.\na cat on a rail"
    assert graph["2"]["inputs"]["text"] == "blurry"
    assert graph["10"]["inputs"]["image"].endswith("[output]") and "a.png" in graph["10"]["inputs"]["image"]
    assert graph["11"]["inputs"]["image"] == "" or graph["11"].get("mode") == 2
    assert graph["5"]["inputs"]["seed"] == 1234
    assert graph["5"]["inputs"]["steps"] == 8
    assert graph["9"]["inputs"]["filename_prefix"] == os.path.join("p_x", "s_01", "take")
    assert captured["payload"]["extra_data"]["machine_ui_label"] == "Video 1 take"
    assert captured["payload"]["extra_data"]["machine_ui_project"] == "p_x"

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["outputs"] == [{"path": str(out_file), "node_id": "9", "prompt_id": "pid-1", "label": "Video 1 take"}]
    assert injected and injected[0][0] == str(out_file)
    assert injected[0][1]["thm"]["generation"] == "g_0a1"
    assert f"RESULT: {out_file}" in capsys.readouterr().out


def test_run_images_generation_takes_loras_from_prompt_tags(tmp_path, monkeypatch):
    """A <lora:...> tag in the prompt (either notation) becomes a loader node and leaves the text."""
    output_root, output_dir, _, _ = _world(tmp_path)
    out_file = output_dir / "take_00001_.png"
    out_file.write_bytes(b"png")
    captured = _stub_http(monkeypatch, run_images, _history("9", "images", out_file.name, "p_x/s_01"))
    monkeypatch.setattr(run_images, "wait_for_file_stable", lambda path, timeout_s=0: True)
    monkeypatch.setattr(run_images, "inject_metadata_png", lambda path, snap: None)
    wf = _image_graph()
    wf["15"] = {"_meta": {"title": "MainCheckpoint"}, "class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}}
    wf["42"] = {"_meta": {"title": "MainLora"}, "class_type": "Power Lora Loader (rgthree)", "inputs": {"model": ["15", 0], "clip": ["15", 1]}}
    wf["5"]["inputs"]["model"] = ["42", 0]
    spec_path = _spec(
        tmp_path, wf, output_root, output_dir,
        prompt="<lora:style.safetensors:0.8> a cat on a rail __lora:old.safetensors:0.5:0.25__",
        prelude=["<lora:global.safetensors:1> image1 is a character reference."],
        params={"seed": 1},
    )
    run_images.run_generation(str(spec_path), status_file_override=str(tmp_path / "status.json"))

    graph = captured["payload"]["prompt"]
    assert graph["1"]["inputs"]["text"] == "image1 is a character reference.\na cat on a rail"
    injected = {node["inputs"]["lora_name"]: node["inputs"] for node in graph.values() if node.get("class_type") == "LoraLoader"}
    assert set(injected) == {"style.safetensors", "old.safetensors", "global.safetensors"}
    assert injected["style.safetensors"]["strength_model"] == 0.8
    assert injected["old.safetensors"]["strength_model"] == 0.5 and injected["old.safetensors"]["strength_clip"] == 0.25


def test_run_images_generation_fails_on_history_error(tmp_path, monkeypatch, capsys):
    output_root, output_dir, ref_a, _ = _world(tmp_path)
    _stub_http(monkeypatch, run_images, _history("9", "images", "x.png", "p_x/s_01", error="boom in KSampler"))
    spec_path = _spec(tmp_path, _image_graph(), output_root, output_dir, params={"seed": 1})
    status_file = tmp_path / "status.json"
    with pytest.raises(SystemExit) as exc:
        run_images.run_generation(str(spec_path), status_file_override=str(status_file))
    assert exc.value.code == 1
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "failed" and "boom in KSampler" in status["error"]
    assert status["outputs"] == []
    assert "RESULT:" not in capsys.readouterr().out


def test_run_images_generation_refuses_output_dir_outside_root(tmp_path, monkeypatch):
    output_root, output_dir, _, _ = _world(tmp_path)
    _stub_http(monkeypatch, run_images, _history("9", "images", "x.png", ""))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    spec_path = _spec(tmp_path, _image_graph(), output_root, outside)
    status_file = tmp_path / "status.json"
    with pytest.raises(SystemExit):
        run_images.run_generation(str(spec_path), status_file_override=str(status_file))
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "failed" and "output_root" in status["error"]


# -- video --------------------------------------------------------------------


def _video_graph():
    return {
        "1": node("THM-Prompt", "CLIPTextEncode", {"text": ""}),
        "2": node("THM-NegativePrompt", "CLIPTextEncode", {"text": ""}),
        "20": node("THM-StartFrame", "LoadImage", {"image": ""}),
        "21": node("THM-EndFrame", "LoadImage", {"image": ""}),
        "30": node("THM-VideoGenerator", "WanFirstLastFrameToVideo", {
            "start_image": ["20", 0], "end_image": ["21", 0], "length": 1, "width": 0, "height": 0,
        }),
        "31": node("THM-Seed", "PrimitiveInt", {"value": 0}),
        "32": node("THM-FrameCount", "PrimitiveInt", {"value": 0}),
        "33": node("THM-FrameRate", "PrimitiveFloat", {"value": 24.0}),
        "34": node("THM-SaveVideo", "SaveVideo", {"filename_prefix": "", "video": ["30", 0]}),
        "40": node("THM-VideoReference", "LoadVideo", {"file": ""}),
        "41": node("THM-VideoReference", "LoadVideo", {"file": ""}),
    }


def test_run_video_generation_wires_anchors_references_and_timing(tmp_path, monkeypatch, capsys):
    output_root, output_dir, ref_a, ref_b = _world(tmp_path)
    out_file = output_dir / "take_00001.mp4"
    out_file.write_bytes(b"mp4")
    captured = _stub_http(monkeypatch, run_video, _history("9", "gifs", out_file.name, "p_x/s_01"))
    injected = []
    monkeypatch.setattr(run_video, "inject_metadata_mp4", lambda path, snap: injected.append((path, snap)))

    spec_path = _spec(
        tmp_path, _video_graph(), output_root, output_dir,
        paths_by_slot={"start": ref_a, "end": ref_b, "40": "C:/clips/clip.mp4"},
        params={"seed": 7, "length_s": 5, "fps": 16, "width": 832, "height": 480},
    )
    status_file = tmp_path / "status.json"
    run_video.run_generation(str(spec_path), status_file_override=str(status_file))

    graph = captured["payload"]["prompt"]
    assert graph["1"]["inputs"]["text"] == "a cat on a rail"
    assert graph["2"]["inputs"]["text"] == "blurry"
    assert graph["32"]["inputs"]["value"] == 81
    assert graph["33"]["inputs"]["value"] == 16.0
    assert graph["31"]["inputs"]["value"] == 7
    assert graph["30"]["inputs"]["width"] == 832 and graph["30"]["inputs"]["height"] == 480
    assert graph["40"]["inputs"]["file"] == "C:/clips/clip.mp4"
    assert "41" not in graph  # the unused video slot leaves the graph rather than loading ""
    assert graph["20"]["inputs"]["image"].endswith("[output]") and "a.png" in graph["20"]["inputs"]["image"]
    assert graph["21"]["inputs"]["image"].endswith("[output]") and "b.png" in graph["21"]["inputs"]["image"]
    assert graph["34"]["inputs"]["filename_prefix"] == os.path.join(str(output_dir), "take")

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["outputs"] == [{"path": str(out_file), "node_id": "9", "prompt_id": "pid-1", "label": "Video 1 take"}]
    assert injected and injected[0][0] == str(out_file)
    assert injected[0][1]["thm"]["clip_type"] == "SE"
    assert f"RESULT: {out_file}" in capsys.readouterr().out


def test_run_video_generation_open_end_deactivates_end_branch(tmp_path, monkeypatch):
    output_root, output_dir, ref_a, _ = _world(tmp_path)
    captured = _stub_http(monkeypatch, run_video, _history("9", "gifs", "take_00001.mp4", "p_x/s_01"))
    monkeypatch.setattr(run_video, "inject_metadata_mp4", lambda path, snap: None)
    spec_path = _spec(
        tmp_path, _video_graph(), output_root, output_dir,
        paths_by_slot={"start": ref_a},
        params={"seed": 7, "length_s": 2, "fps": 16},
    )
    run_video.run_generation(str(spec_path), status_file_override=str(tmp_path / "status.json"))
    graph = captured["payload"]["prompt"]
    assert "end_image" not in graph["30"]["inputs"]
    assert "start_image" in graph["30"]["inputs"]
    assert graph["20"]["inputs"]["image"].endswith("[output]")
    assert "21" not in graph or not graph["21"]["inputs"].get("image")


def _legacy_wan_graph():
    """Untagged, like workflows/i2v_base.json: loaders feed StartImage/EndImage scalers into the WAN node."""
    return {
        "1": node("THM-Prompt", "CLIPTextEncode", {"text": ""}),
        "2": node("THM-NegativePrompt", "CLIPTextEncode", {"text": ""}),
        "202": node("Load Image", "LoadImage", {"image": "example.png"}),
        "203": node("Load Image", "LoadImage", {"image": "example.png"}),
        "196": node("StartImage", "ImageScale", {"upscale_method": "lanczos", "width": 512, "height": 512, "crop": "center", "image": ["202", 0]}),
        "111": node("EndImage", "ImageScale", {"upscale_method": "lanczos", "width": 512, "height": 512, "crop": "center", "image": ["203", 0]}),
        "183": node("WanFirstLastFrameToVideo", "WanFirstLastFrameToVideo", {
            "width": 512, "height": 512, "length": 17, "batch_size": 1, "start_image": ["196", 0], "end_image": ["111", 0],
        }),
        "5": node("KSampler", "KSampler", {"seed": 0, "steps": 4, "cfg": 1.0, "latent_image": ["183", 0]}),
        "200": node("Save Video", "SaveVideo", {"filename_prefix": "video/ComfyUI", "video": ["5", 0]}),
    }


def test_run_video_generation_legacy_wan_writes_anchors_and_length(tmp_path, monkeypatch):
    output_root, output_dir, ref_a, ref_b = _world(tmp_path)
    captured = _stub_http(monkeypatch, run_video, _history("200", "images", "take_00001.mp4", "p_x/s_01"))
    monkeypatch.setattr(run_video, "inject_metadata_mp4", lambda path, snap: None)
    spec_path = _spec(
        tmp_path, _legacy_wan_graph(), output_root, output_dir,
        paths_by_slot={"start": ref_a, "end": ref_b},
        params={"seed": 7, "length_s": 5, "fps": 16},
    )
    run_video.run_generation(str(spec_path), status_file_override=str(tmp_path / "status.json"))
    graph = captured["payload"]["prompt"]
    assert graph["202"]["inputs"]["image"].endswith("[output]") and "a.png" in graph["202"]["inputs"]["image"]
    assert graph["203"]["inputs"]["image"].endswith("[output]") and "b.png" in graph["203"]["inputs"]["image"]
    assert graph["183"]["inputs"]["start_image"] == ["196", 0] and graph["183"]["inputs"]["end_image"] == ["111", 0]
    assert graph["196"]["inputs"]["width"] == 512 and graph["111"]["inputs"]["height"] == 512
    assert graph["183"]["inputs"]["length"] == 81
    assert graph["5"]["inputs"]["seed"] == 7
    assert graph["200"]["inputs"]["filename_prefix"] == os.path.join(str(output_dir), "take")


def test_run_video_generation_legacy_wan_start_only_drops_end(tmp_path, monkeypatch):
    output_root, output_dir, ref_a, _ = _world(tmp_path)
    captured = _stub_http(monkeypatch, run_video, _history("200", "images", "take_00001.mp4", "p_x/s_01"))
    monkeypatch.setattr(run_video, "inject_metadata_mp4", lambda path, snap: None)
    spec_path = _spec(
        tmp_path, _legacy_wan_graph(), output_root, output_dir,
        paths_by_slot={"start": ref_a}, params={"seed": 7, "length_s": 2, "fps": 16, "width": 832, "height": 480},
    )
    run_video.run_generation(str(spec_path), status_file_override=str(tmp_path / "status.json"))
    graph = captured["payload"]["prompt"]
    assert "a.png" in graph["202"]["inputs"]["image"]
    assert "b.png" not in graph["203"]["inputs"]["image"]  # the unused loader is left unwired, not fed
    assert "end_image" not in graph["183"]["inputs"] and graph["183"]["inputs"]["start_image"] == ["196", 0]
    assert graph["196"]["inputs"]["width"] == 832 and graph["196"]["inputs"]["height"] == 480
    assert graph["183"]["inputs"]["length"] == 33


# -- parser -------------------------------------------------------------------


@pytest.mark.parametrize("module", [run_images, run_video])
def test_config_and_generation_are_mutually_exclusive(module):
    ap = module.build_arg_parser()
    with pytest.raises(SystemExit):
        ap.parse_args(["--config", "a.json", "--generation", "b.json"])
    with pytest.raises(SystemExit):
        ap.parse_args([])
    assert ap.parse_args(["--generation", "b.json"]).generation == "b.json"
    assert ap.parse_args(["--config", "a.json", "--status-file", "s.json"]).status_file == "s.json"
