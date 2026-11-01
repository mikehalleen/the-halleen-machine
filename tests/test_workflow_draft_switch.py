"""THM-IsDraft: a workflow's own draft setting, found by its tag and set per render."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import workflow_controls as wc  # noqa: E402


def _graph():
    return {
        "190": {"class_type": "PrimitiveInt", "inputs": {"value": 12}, "_meta": {"title": "Full quality steps"}},
        "198": {"class_type": "PrimitiveInt", "inputs": {"value": 6}, "_meta": {"title": "draft quality steps"}},
        "199": {"class_type": "ComfySwitchNode", "inputs": {"switch": False, "on_false": ["190", 0], "on_true": ["198", 0]}, "_meta": {"title": "THM-IsDraft"}},
    }


def test_the_switch_is_found_and_set_both_ways():
    g = _graph()
    assert wc.supports_draft(g)
    assert wc.set_draft(g, True) == 1 and g["199"]["inputs"]["switch"] is True
    assert wc.set_draft(g, False) == 1 and g["199"]["inputs"]["switch"] is False
    # the tag is a known one, matched like the others regardless of case
    assert not [n for n in wc.unknown_thm_tags(g)]
    g["199"]["_meta"]["title"] = "thm-isdraft"
    assert wc.supports_draft(g)


def test_a_workflow_without_one_is_untouched():
    g = {k: v for k, v in _graph().items() if k != "199"}
    assert not wc.supports_draft(g)
    assert wc.set_draft(g, True) == 0


def test_a_boolean_primitive_works_too():
    g = {"5": {"class_type": "PrimitiveBoolean", "inputs": {"value": False}, "_meta": {"title": "THM-IsDraft"}}}
    wc.set_draft(g, True)
    assert g["5"]["inputs"]["value"] is True
