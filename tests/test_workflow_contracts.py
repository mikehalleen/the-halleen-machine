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








