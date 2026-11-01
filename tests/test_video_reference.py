"""THM-VideoReference discovery, injection, and capability reporting (THM phase 2)."""

import json
from pathlib import Path

from scripts import workflow_controls as wc
from thm_core.workflow_capabilities import scan_video_workflow_capabilities

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"


def node(title, class_type, inputs):
    return {"_meta": {"title": title}, "class_type": class_type, "inputs": dict(inputs)}


def _one_slot_workflow() -> dict:
    return {
        "1": node("THM-Prompt", "CLIPTextEncode", {"text": ""}),
        "7": node("THM-VideoReference", "LoadVideo", {"file": ""}),
    }


def test_video_reference_tag_not_reported_as_unknown():
    wf = _one_slot_workflow()
    assert wc.unknown_thm_tags(wf) == []
    assert [cn.node_id for cn in wc.find_control_nodes(wf, wc.VIDEO_REFERENCE)] == ["7"]
    assert wc.VIDEO_REFERENCE in wc.CONTROL_TAGS


def test_transposed_reference_tags_are_accepted():
    """``THM-ReferenceVideo`` / ``THM-ReferenceImage`` are the documented tags with the words
    swapped, a common slip; both spellings discover, mixed freely, ordered the same way."""
    wf = {
        "7": node("THM-ReferenceVideo", "VHS_LoadVideo", {"video": ""}),
        "9": node("THM-VideoReference", "LoadVideo", {"file": ""}),
        "12": node("THM-ReferenceImage", "LoadImage", {"image": ""}),
        "13": node("THM-ImageReference", "LoadImage", {"image": ""}),
    }
    assert [s.node_id for s in wc.discover_video_reference_slots(wf)] == ["7", "9"]
    assert sorted(s.node_id for s in wc.discover_image_reference_slots(wf)) == ["12", "13"]


def test_empty_reference_loaders_leave_the_graph():
    """A slot with no path this run is dropped and unlinked from its consumer; a filled one stays.
    A single-input node between the loader and the consumer goes with the loader."""
    wf = {
        "1": node("THM-ImageReference", "LoadImage", {"image": "a.png [output]"}),
        "2": node("THM-ImageReference", "LoadImage", {"image": ""}),
        "3": node("THM-VideoReference", "VHS_LoadVideo", {"video": "clip.mp4 [output]"}),
        "4": node("THM-VideoReference", "VHS_LoadVideo", {"video": ""}),
        "5": node("resize", "ImageScale", {"image": ["2", 0], "width": 512}),
        "9": node("gen", "MiniMaxH3ReferenceToVideo", {"prompt": "x", "ref_images.ref_image_0": ["1", 0], "ref_images.ref_image_1": ["5", 0], "ref_videos.ref_video_0": ["3", 0], "ref_videos.ref_video_1": ["4", 0]}),
        "10": node("save", "VHS_VideoCombine", {"images": ["9", 0]}),
    }
    assert wc.deactivate_empty_reference_loaders(wf) == 2
    assert set(wf) == {"1", "3", "9", "10"}
    assert wf["9"]["inputs"] == {"prompt": "x", "ref_images.ref_image_0": ["1", 0], "ref_videos.ref_video_0": ["3", 0]}


def test_discover_video_reference_slots_orders_by_numeric_node_id():
    wf = {}
    for node_id in ("30", "7", "102", "abc"):
        wf[node_id] = node("THM-VideoReference", "LoadVideo", {"file": ""})
    wf["5"] = node("THM-VideoReference-2", "VHS_LoadVideo", {"video": ""})
    slots = wc.discover_video_reference_slots(wf)
    assert [s.node_id for s in slots] == ["5", "7", "30", "102", "abc"]
    assert [s.ordinal for s in slots] == [1, 2, 3, 4, 5]
    assert all(s.binding_key == s.node_id for s in slots)
    assert slots[0].class_type == "VHS_LoadVideo"


def test_inject_video_reference_slot_paths_writes_first_known_key():
    wf = {
        "A": node("THM-VideoReference", "VHS_LoadVideo", {"video": "", "force_rate": 0}),
        "B": node("THM-VideoReference", "LoadVideo", {"file": ""}),
        "C": node("THM-VideoReference", "SomeLoader", {}),
    }
    slots = wc.discover_video_reference_slots(wf)
    written = wc.inject_video_reference_slot_paths(wf, slots, {"A": "C:/a.mp4", "B": "C:/b.mp4"})
    assert written == 3
    assert wf["A"]["inputs"]["video"] == "C:/a.mp4"
    assert wf["A"]["inputs"]["force_rate"] == 0
    assert wf["B"]["inputs"]["file"] == "C:/b.mp4"
    assert wf["C"]["inputs"]["video"] == ""

    assert wc.clear_tagged_video_reference_nodes(wf) == 3
    assert wf["A"]["inputs"]["video"] == ""
    assert wf["B"]["inputs"]["file"] == ""


def test_shipped_workflows_have_no_video_reference_slots():
    checked = 0
    for path in sorted(WORKFLOWS_DIR.glob("*.json")):
        try:
            wf = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(wf, dict):
            continue
        assert wc.discover_video_reference_slots(wf) == [], path.name
        assert wc.discover_video_capabilities(wf).video_reference_count == 0, path.name
        checked += 1
    assert checked > 0


def test_video_capabilities_and_scan_report_video_reference_slots():
    wf = _one_slot_workflow()
    caps = wc.discover_video_capabilities(wf)
    assert caps.video_reference_count == 1
    assert caps.video_reference_node_ids == ["7"]
    scan = scan_video_workflow_capabilities(wf)
    video = scan.to_dict()["video"]
    assert video["video_reference_count"] == 1
    assert video["video_reference_node_ids"] == ["7"]
