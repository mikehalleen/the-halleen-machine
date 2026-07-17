import json
from pathlib import Path

import pytest

from scripts import workflow_controls as wc

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"
KLING_WORKFLOW = WORKFLOWS_DIR / "THM-api_kling_flf.json"
SEEDANCE_WORKFLOW = WORKFLOWS_DIR / "THM-api_bytedace_seedance1_5_flf2v.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _partner_inputs(workflow) -> dict:
    nodes = wc.find_control_nodes(workflow, wc.PARTNER_NODE)
    assert len(nodes) == 1
    return nodes[0].node["inputs"]


def _ctx(**kwargs) -> wc.VideoInjectionContext:
    return wc.VideoInjectionContext(**kwargs)


def test_partner_node_tag_is_discovered():
    for path in (KLING_WORKFLOW, SEEDANCE_WORKFLOW):
        workflow = _load(path)
        caps = wc.discover_video_capabilities(workflow)
        assert caps.has_partner_node
    assert wc.discover_video_capabilities(_load(KLING_WORKFLOW)).partner_node_classes == [
        "KlingStartEndFrameNode"
    ]
    assert wc.discover_video_capabilities(_load(SEEDANCE_WORKFLOW)).partner_node_classes == [
        "ByteDanceFirstLastFrameNode"
    ]


def test_partner_node_not_reported_as_unknown_tag():
    workflow = _load(KLING_WORKFLOW)
    assert not any(
        cn.title.lower() == "thm-partnernode" for cn in wc.unknown_thm_tags(workflow)
    )


def test_kling_injection_writes_prompt_and_negative_only():
    workflow = _load(KLING_WORKFLOW)
    before = dict(_partner_inputs(workflow))
    ctx = _ctx(
        positive_prompt="a dancer spins",
        negative_prompt="blurry",
        frame_count=81,
        fps=16.0,
        width=1024,
        height=576,
    )
    wc.set_partner_node_fields(workflow, ctx)
    inputs = _partner_inputs(workflow)
    assert inputs["prompt"] == "a dancer spins"
    assert inputs["negative_prompt"] == "blurry"
    # Enum/opaque fields stay workflow-baked; no fields invented.
    assert inputs["aspect_ratio"] == before["aspect_ratio"]
    assert inputs["mode"] == before["mode"]
    assert inputs["cfg_scale"] == before["cfg_scale"]
    assert set(inputs.keys()) == set(before.keys())


def test_seedance_injection_skips_missing_negative_and_enum_fields():
    workflow = _load(SEEDANCE_WORKFLOW)
    before = dict(_partner_inputs(workflow))
    ctx = _ctx(
        positive_prompt="a capybara dances",
        negative_prompt="blurry",
        frame_count=81,
        fps=16.0,
    )
    wc.set_partner_node_fields(workflow, ctx)
    inputs = _partner_inputs(workflow)
    assert inputs["prompt"] == "a capybara dances"
    assert "negative_prompt" not in inputs
    assert inputs["duration"] == before["duration"]
    assert inputs["resolution"] == before["resolution"]
    assert inputs["aspect_ratio"] == before["aspect_ratio"]
    assert set(inputs.keys()) == set(before.keys())


def test_apply_video_injection_covers_partner_node():
    workflow = _load(SEEDANCE_WORKFLOW)
    ctx = _ctx(positive_prompt="a capybara dances", negative_prompt="")
    wc.apply_video_injection(workflow, ctx)
    assert _partner_inputs(workflow)["prompt"] == "a capybara dances"


def test_partner_frame_tags_present_on_samples():
    for path in (KLING_WORKFLOW, SEEDANCE_WORKFLOW):
        workflow = _load(path)
        assert wc.find_control_nodes(workflow, wc.VIDEO_START_FRAME)
        assert wc.find_control_nodes(workflow, wc.VIDEO_END_FRAME)


def test_derive_partner_metadata_kling_mode_string():
    meta = wc.derive_partner_metadata(_load(KLING_WORKFLOW))
    assert meta["provider_class"] == "KlingStartEndFrameNode"
    assert meta["duration_sec"] == 5.0
    assert meta["width"] is None
    assert meta["height"] is None
    assert meta["source_fields"]["aspect_ratio"] == "16:9"


def test_derive_partner_metadata_seedance_adaptive_aspect():
    workflow = _load(SEEDANCE_WORKFLOW)
    inputs = _partner_inputs(workflow)
    inputs["duration"] = 5
    inputs["resolution"] = "720p"
    inputs["aspect_ratio"] = "adaptive"
    meta = wc.derive_partner_metadata(workflow)
    assert meta["provider_class"] == "ByteDanceFirstLastFrameNode"
    assert meta["duration_sec"] == 5.0
    # aspect_ratio "adaptive": height from 720p, width unknown
    assert meta["height"] == 720
    assert meta["width"] is None


def test_derive_partner_metadata_resolution_with_ratio():
    workflow = _load(SEEDANCE_WORKFLOW)
    inputs = _partner_inputs(workflow)
    inputs["resolution"] = "720p"
    inputs["aspect_ratio"] = "16:9"
    meta = wc.derive_partner_metadata(workflow)
    assert meta["height"] == 720
    assert meta["width"] == 1280


def test_derive_partner_metadata_malformed_mode_does_not_raise():
    workflow = _load(KLING_WORKFLOW)
    _partner_inputs(workflow)["mode"] = "definitely not parseable"
    meta = wc.derive_partner_metadata(workflow)
    assert meta["duration_sec"] is None


def test_derive_partner_metadata_none_without_partner_node():
    workflow = _load(WORKFLOWS_DIR / "i2v_base.json")
    assert wc.derive_partner_metadata(workflow) is None


def test_suggested_image_size_for_video():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from workflow_capabilities import suggested_image_size_for_video

    # Nearest ~1MP /64 bucket by aspect ratio (model-safe for SDXL and Flux)
    assert suggested_image_size_for_video(1920, 1080) == (1344, 768)
    assert suggested_image_size_for_video(1080, 1920) == (768, 1344)
    assert suggested_image_size_for_video(1280, 720) == (1344, 768)
    assert suggested_image_size_for_video(720, 1280) == (768, 1344)
    assert suggested_image_size_for_video(1024, 1024) == (1024, 1024)
    assert suggested_image_size_for_video(2560, 1080) == (1536, 640)  # ultrawide
    assert suggested_image_size_for_video(None, 1080) is None
    assert suggested_image_size_for_video("adaptive", 720) is None
    assert suggested_image_size_for_video(0, 720) is None


def _frame_loader_ids(workflow) -> list[str]:
    return [
        cn.node_id
        for control in (wc.VIDEO_START_FRAME, wc.VIDEO_END_FRAME)
        for cn in wc.find_control_nodes(workflow, control)
    ]


def _partner_scale_nodes(workflow) -> list[tuple[str, dict]]:
    return [
        (nid, node)
        for nid, node in workflow.items()
        if isinstance(node, dict)
        and wc.node_title(node).startswith(wc.PARTNER_SCALE_TITLE_PREFIX)
    ]


def test_inject_partner_frame_scalers_seedance():
    workflow = _load(SEEDANCE_WORKFLOW)
    loader_ids = _frame_loader_ids(workflow)
    assert len(loader_ids) == 2

    inserted = wc.inject_partner_frame_scalers(workflow, 1920, 1080)
    assert inserted == 2

    scalers = _partner_scale_nodes(workflow)
    assert len(scalers) == 2
    scaled_sources = set()
    for _, node in scalers:
        inputs = node["inputs"]
        assert node["class_type"] == "ImageScale"
        assert inputs["upscale_method"] == "lanczos"
        assert inputs["crop"] == "center"
        assert inputs["width"] == 1920 and inputs["height"] == 1080
        scaled_sources.add(inputs["image"][0])
    assert scaled_sources == set(loader_ids)

    # Partner node frame inputs now come from the scalers, not the loaders.
    partner_inputs = _partner_inputs(workflow)
    scaler_ids = {nid for nid, _ in scalers}
    assert partner_inputs["first_frame"][0] in scaler_ids
    assert partner_inputs["last_frame"][0] in scaler_ids


def test_inject_partner_frame_scalers_idempotent():
    workflow = _load(SEEDANCE_WORKFLOW)
    assert wc.inject_partner_frame_scalers(workflow, 1920, 1080) == 2
    assert wc.inject_partner_frame_scalers(workflow, 1920, 1080) == 0
    assert len(_partner_scale_nodes(workflow)) == 2


def test_inject_partner_frame_scalers_requires_partner_node():
    workflow = _load(WORKFLOWS_DIR / "i2v_base.json")
    assert wc.inject_partner_frame_scalers(workflow, 1920, 1080) == 0


def test_inject_partner_frame_scalers_requires_valid_size():
    workflow = _load(SEEDANCE_WORKFLOW)
    assert wc.inject_partner_frame_scalers(workflow, None, 1080) == 0
    assert wc.inject_partner_frame_scalers(workflow, 0, 1080) == 0
    assert not _partner_scale_nodes(workflow)


def test_apply_video_injection_injects_scalers_from_partner_metadata():
    workflow = _load(SEEDANCE_WORKFLOW)
    inputs = _partner_inputs(workflow)
    inputs["resolution"] = "1080p"
    inputs["aspect_ratio"] = "16:9"
    ctx = _ctx(positive_prompt="dance")
    wc.apply_video_injection(workflow, ctx)
    scalers = _partner_scale_nodes(workflow)
    assert len(scalers) == 2
    assert all(n["inputs"]["width"] == 1920 and n["inputs"]["height"] == 1080 for _, n in scalers)


def test_apply_video_injection_no_scalers_when_adaptive():
    workflow = _load(SEEDANCE_WORKFLOW)
    inputs = _partner_inputs(workflow)
    inputs["resolution"] = "720p"
    inputs["aspect_ratio"] = "adaptive"  # width unknown -> no injection
    wc.apply_video_injection(workflow, _ctx(positive_prompt="dance"))
    assert not _partner_scale_nodes(workflow)


class _FakeResponse:
    ok = True

    def raise_for_status(self):
        pass

    def json(self):
        return {"prompt_id": "test"}


def _capture_post_payload(monkeypatch, module):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)
    return captured


def test_post_prompt_includes_api_key_when_env_set(monkeypatch):
    from scripts import run_video

    monkeypatch.setenv("THM_COMFY_API_KEY", "test-key-123")
    captured = _capture_post_payload(monkeypatch, run_video)
    run_video.post_prompt("http://localhost:8188", {}, project_name="p")
    extra = captured["payload"]["extra_data"]
    assert extra["api_key_comfy_org"] == "test-key-123"
    assert extra["machine_ui_project"] == "p"


def test_post_prompt_omits_api_key_when_env_unset(monkeypatch):
    from scripts import run_video

    monkeypatch.delenv("THM_COMFY_API_KEY", raising=False)
    captured = _capture_post_payload(monkeypatch, run_video)
    run_video.post_prompt("http://localhost:8188", {})
    assert "api_key_comfy_org" not in captured["payload"]["extra_data"]


def test_run_images_post_prompt_includes_api_key(monkeypatch):
    from scripts import run_images

    monkeypatch.setenv("THM_COMFY_API_KEY", "test-key-456")
    captured = _capture_post_payload(monkeypatch, run_images)
    run_images.post_prompt("http://localhost:8188", {})
    assert captured["payload"]["extra_data"]["api_key_comfy_org"] == "test-key-456"
