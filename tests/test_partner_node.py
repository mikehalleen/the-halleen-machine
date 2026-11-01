import json
from pathlib import Path

import pytest

from scripts import workflow_controls as wc

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / "workflows"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _partner_inputs(workflow) -> dict:
    nodes = wc.find_control_nodes(workflow, wc.PARTNER_NODE)
    assert len(nodes) == 1
    return nodes[0].node["inputs"]


def _ctx(**kwargs) -> wc.VideoInjectionContext:
    return wc.VideoInjectionContext(**kwargs)












# --- Seedance 2.5 reference-to-video: dynamic-combo node, fields live under "model." ---






def test_partner_input_key_never_matches_group_entries():
    inputs = {"model.reference_images.image_1": ["12", 0], "model.prompt": ""}
    assert wc._partner_input_key(inputs, ("image_1",)) is None
    assert wc._partner_input_key(inputs, ("prompt",)) == "model.prompt"
    assert wc._partner_input_key({"prompt": "", "model.prompt": ""}, ("prompt",)) == "prompt"




























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
