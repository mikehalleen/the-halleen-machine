"""Contract checks on *your* workflows: whatever is in workflows_image/ and workflows_video/.

THM ships no workflows, so these tests collect one case per file you have added and skip when
the folders are empty. They check only what every THM workflow needs, whatever the model:

- the file is a ComfyUI graph in API format, and every link points at a node that exists
  (also after THM strips its metadata nodes, which it does before every render);
- every ``THM-*`` title is one THM knows (a typo'd tag is silently ignored at render time);
- THM's own scan, the one behind the UI's workflow list, reads it without an error and gives
  it the kind of the folder it sits in;
- THM has a prompt to write: a ``THM-Prompt`` node (or a legacy prompt title), or a
  ``THM-PartnerNode``.

And, when ``workflows/prompt_enhancer.json`` is present, that the Enhance button's local engine
can use it. None of this talks to ComfyUI: a pass means THM can drive the graph, not that the
models are installed. Model-specific tests (wiring, frames, references, samplers) are yours to
write; ``tests/WORKFLOW_TESTS.md`` is the brief for a coding agent.

Point the tests at another install with ``THM_WORKFLOWS_ROOT=<folder holding workflows_image/>``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

import workflow_controls as wc
from thm_core import enhancer
from thm_server.derive import workflow_info

ROOT = Path(os.environ.get("THM_WORKFLOWS_ROOT") or Path(__file__).resolve().parents[1])
KIND_DIRS = {"image": ROOT / "workflows_image", "video": ROOT / "workflows_video"}


def _cases() -> list:
    cases = [
        pytest.param(kind, path, id=f"{kind}/{path.name}")
        for kind, folder in KIND_DIRS.items()
        if folder.is_dir()
        for path in sorted(folder.glob("*.json"))
        if not path.name.startswith("_")  # the server does not list these either
    ]
    if not cases:
        return [pytest.param(None, None, id="no-workflows",
                             marks=pytest.mark.skip(reason=f"no workflows in {KIND_DIRS['image']} or {KIND_DIRS['video']}"))]
    return cases


CASES = _cases()


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _nodes(graph: dict) -> dict:
    return {k: v for k, v in graph.items() if isinstance(v, dict)}


def _dangling_links(graph: dict) -> list[str]:
    nodes = _nodes(graph)
    bad = []
    for node_id, node in nodes.items():
        for key, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int) and isinstance(value[0], (str, int)):
                if str(value[0]) not in nodes:
                    bad.append(f"node {node_id} input {key!r} -> missing node {value[0]}")
    return bad


@pytest.mark.parametrize("kind,path", CASES)
def test_is_api_format(kind, path):
    graph = _load(path)
    assert isinstance(graph, dict) and graph, f"{path.name}: not a JSON object"
    assert "nodes" not in graph or not isinstance(graph.get("nodes"), list), (
        f"{path.name} is ComfyUI's editor format; export it with Workflow > Export (API)")
    nodes = _nodes(graph)
    assert nodes, f"{path.name}: no nodes"
    for node_id, node in nodes.items():
        assert node.get("class_type"), f"{path.name}: node {node_id} has no class_type"
        assert isinstance(node.get("inputs", {}), dict), f"{path.name}: node {node_id} inputs is not an object"


@pytest.mark.parametrize("kind,path", CASES)
def test_links_resolve_before_and_after_strip(kind, path):
    graph = _load(path)
    assert _dangling_links(graph) == []
    stripped = copy.deepcopy(graph)
    wc.strip_prompt_metadata(stripped)  # what the runners do right before posting to ComfyUI
    assert _dangling_links(stripped) == []


@pytest.mark.parametrize("kind,path", CASES)
def test_no_unknown_thm_tags(kind, path):
    unknown = [
        f"node {c.node_id}: {c.title!r}"
        for c in wc.unknown_thm_tags(_load(path))
        if not c.title.startswith(enhancer.ENHANCER_TITLE_PREFIX)  # the enhancer's own nodes, stripped before a render
    ]
    assert unknown == [], "THM does not know these titles (see WORKFLOWS.md); check the spelling"


@pytest.mark.parametrize("kind,path", CASES)
def test_thm_scan_reads_it(kind, path):
    info = workflow_info(str(path), ROOT / "workflows")
    assert info is not None, f"{path.name}: THM does not resolve it"
    assert info.error is None, f"{path.name}: {info.error}"
    assert info.kind == kind, f"{path.name} sits in {KIND_DIRS[kind].name}/ but scans as {info.kind}"


@pytest.mark.parametrize("kind,path", CASES)
def test_has_a_prompt_to_write(kind, path):
    graph = _load(path)
    prompt = wc.find_control_nodes(graph, wc.PROMPT)
    partner = wc.find_control_nodes(graph, wc.PARTNER_NODE)
    assert prompt or partner, f"{path.name}: no THM-Prompt (or THM-PartnerNode) node, so THM cannot write your prompt"


ENHANCER = ROOT / "workflows" / enhancer.ENHANCER_WORKFLOW_FILENAME


@pytest.mark.skipif(not ENHANCER.is_file(), reason=f"no {ENHANCER}")
def test_prompt_enhancer_is_usable():
    assert enhancer.enhancer_available(ENHANCER.parent), (
        f"{ENHANCER.name} needs nodes titled {enhancer.SYSTEM_PROMPT_TITLE} and {enhancer.USER_PROMPT_TITLE}")
    graph = enhancer.build_enhancer_graph(_load(ENHANCER), "SYSTEM-TEXT", "USER-TEXT")
    texts = json.dumps(graph)
    assert "SYSTEM-TEXT" in texts and "USER-TEXT" in texts
    assert _dangling_links(graph) == []
