import json
from pathlib import Path

import pytest


WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"

IMAGE_DIMENSION_CLASSES = {
    "EmptyLatentImage",
    "ImageScale",
    "ImageCrop",
    "Image Blank",
    "EmptyFlux2LatentImage",
}


def load_workflow(name: str) -> dict:
    with (WORKFLOWS / name).open("r", encoding="utf-8") as f:
        return json.load(f)


def titles(workflow: dict) -> set[str]:
    return {
        node.get("_meta", {}).get("title")
        for node in workflow.values()
        if isinstance(node, dict) and node.get("_meta", {}).get("title")
    }


def classes(workflow: dict) -> set[str]:
    return {
        node.get("class_type")
        for node in workflow.values()
        if isinstance(node, dict) and node.get("class_type")
    }


def nodes_by_title(workflow: dict, title: str) -> list[dict]:
    return [
        node
        for node in workflow.values()
        if isinstance(node, dict) and node.get("_meta", {}).get("title") == title
    ]


def nodes_by_any_title(workflow: dict, title_options: list[str]) -> list[dict]:
    nodes = []
    for title in title_options:
        nodes.extend(nodes_by_title(workflow, title))
    return nodes


def nodes_by_class(workflow: dict, class_type: str) -> list[dict]:
    return [
        node
        for node in workflow.values()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


@pytest.mark.parametrize("workflow_path", sorted(WORKFLOWS.glob("*.json")))
def test_bundled_workflows_are_valid_json_objects(workflow_path):
    workflow = load_workflow(workflow_path.name)

    assert isinstance(workflow, dict)
    assert workflow


@pytest.mark.parametrize(
    "workflow_name",
    [
        "pose_1CHAR.json",
        "pose_2CHAR.json",
        "pose_OPEN.json",
        "pose_factory.json",
        "image_z_image_turbo.json",
    ],
)
def test_core_image_workflows_expose_generation_control_points(workflow_name):
    workflow = load_workflow(workflow_name)
    workflow_titles = titles(workflow)
    workflow_classes = classes(workflow)

    assert workflow_titles & {"THM-SaveImage", "Save Image"}
    assert "SaveImage" in workflow_classes
    assert "KSampler" in workflow_classes
    assert workflow_classes & IMAGE_DIMENSION_CLASSES

    save_image_nodes = nodes_by_any_title(workflow, ["THM-SaveImage", "Save Image"])
    assert any("filename_prefix" in node.get("inputs", {}) for node in save_image_nodes)


def test_pose_2char_workflow_exposes_role_specific_control_titles():
    workflow = load_workflow("pose_2CHAR.json")
    workflow_titles = titles(workflow)

    assert workflow_titles >= {
        "LeftLora",
        "RightLora",
        "LeftPrompt",
        "RightPrompt",
        "HealPosPrompt",
        "LeftNegPrompt",
        "RightNegPrompt",
        "HealNegPrompt",
    }


def test_pose_1char_workflow_exposes_prompt_model_and_lora_control_points():
    workflow = load_workflow("pose_1CHAR.json")
    workflow_titles = titles(workflow)

    assert workflow_titles & {"THM-Checkpoint", "MainCheckpoint"}
    assert workflow_titles & {"THM-Prompt", "MainPrompt"}
    assert workflow_titles & {"THM-NegativePrompt", "MainNegPrompt"}
    assert workflow_titles & {"THM-Lora", "MainLora"}

    checkpoint = nodes_by_any_title(workflow, ["THM-Checkpoint", "MainCheckpoint"])[0]
    prompt = nodes_by_any_title(workflow, ["THM-Prompt", "MainPrompt"])[0]
    negative_prompt = nodes_by_any_title(workflow, ["THM-NegativePrompt", "MainNegPrompt"])[0]

    assert checkpoint["class_type"] == "CheckpointLoaderSimple"
    assert "ckpt_name" in checkpoint.get("inputs", {})
    assert prompt["class_type"] == "CLIPTextEncode"
    assert "text" in prompt.get("inputs", {})
    assert negative_prompt["class_type"] == "CLIPTextEncode"
    assert "text" in negative_prompt.get("inputs", {})


@pytest.mark.parametrize("workflow_name", ["i2v_base.json", "i2v_bridge.json"])
def test_core_video_workflows_expose_video_control_points(workflow_name):
    workflow = load_workflow(workflow_name)
    workflow_titles = titles(workflow)
    workflow_classes = classes(workflow)

    prompt_nodes = nodes_by_any_title(workflow, ["THM-Prompt", "PosPrompt", "MainPrompt"])
    negative_nodes = nodes_by_any_title(
        workflow, ["THM-NegativePrompt", "NegPrompt", "MainNegPrompt"]
    )
    assert prompt_nodes, f"{workflow_name} missing prompt control"
    assert negative_nodes, f"{workflow_name} missing negative prompt control"
    assert any("text" in node.get("inputs", {}) for node in prompt_nodes)
    assert any("text" in node.get("inputs", {}) for node in negative_nodes)

    save_videos = (
        nodes_by_any_title(workflow, ["THM-SaveVideo"])
        + nodes_by_class(workflow, "SaveVideo")
        + nodes_by_class(workflow, "VHS_VideoCombine")
    )
    assert save_videos, f"{workflow_name} missing save-video node"
    assert any("filename_prefix" in node.get("inputs", {}) for node in save_videos)

    generators = nodes_by_any_title(
        workflow, ["THM-VideoGenerator", "WanFirstLastFrameToVideo", "WanFunInpaintToVideo"]
    )
    legacy_video = workflow_titles & {"Create Video", "WanFirstLastFrameToVideo"}
    assert generators or legacy_video, f"{workflow_name} missing video generator hook"

    if workflow_name == "i2v_bridge.json":
        assert "IterKSampler" in workflow_titles
        assert "WanFixedSeed" in workflow_titles
        iter_sampler = nodes_by_title(workflow, "IterKSampler")[0]
        assert "noise_seed" in iter_sampler.get("inputs", {})
        save_images = nodes_by_any_title(workflow, ["THM-SaveImage", "Save Image"])
        assert save_images
        assert any("filename_prefix" in node.get("inputs", {}) for node in save_images)
        assert "KSamplerAdvanced" in workflow_classes or "KSampler" in workflow_classes
