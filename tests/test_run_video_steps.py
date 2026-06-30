import copy
import json
from pathlib import Path

from scripts.run_video import _apply_express_sampler_steps, find_nodes_by_title

I2V_BASE = Path(__file__).resolve().parent.parent / "workflows" / "i2v_base.json"


def _load_i2v_base() -> dict:
    return json.loads(I2V_BASE.read_text(encoding="utf-8"))


def _legacy_sampler_inputs(graph: dict, title: str) -> dict:
    for _, node in find_nodes_by_title(graph, title):
        return dict(node.get("inputs", {}))
    raise AssertionError(f"{title} not found")


def test_apply_express_sampler_steps_scales_total_steps_to_10():
    graph = _load_i2v_base()
    t_steps = _apply_express_sampler_steps(graph, express_video=False, total_steps=10)
    assert t_steps == 10

    primer = _legacy_sampler_inputs(graph, "SlowMoPrimer")
    assert primer["steps"] == 10
    assert primer["start_at_step"] == 0
    assert primer["end_at_step"] == 2

    iters = _legacy_sampler_inputs(graph, "IterKSampler")
    assert iters["steps"] == 10
    assert iters["start_at_step"] == 2
    assert iters["end_at_step"] == 6

    wan = _legacy_sampler_inputs(graph, "WanFixedSeed")
    assert wan["steps"] == 10
    assert wan["start_at_step"] == 6
    assert wan["end_at_step"] == 10


def test_apply_express_sampler_steps_regression_at_14():
    graph = _load_i2v_base()
    t_steps = _apply_express_sampler_steps(graph, express_video=False, total_steps=14)
    assert t_steps == 14

    primer = _legacy_sampler_inputs(graph, "SlowMoPrimer")
    assert primer["end_at_step"] == 2

    iters = _legacy_sampler_inputs(graph, "IterKSampler")
    assert iters["start_at_step"] == 2
    assert iters["end_at_step"] == 8

    wan = _legacy_sampler_inputs(graph, "WanFixedSeed")
    assert wan["start_at_step"] == 8
    assert wan["end_at_step"] == 14


def test_apply_express_sampler_steps_express_mode_halves_budget():
    graph = _load_i2v_base()
    t_steps = _apply_express_sampler_steps(graph, express_video=True, total_steps=14)
    assert t_steps == 7

    iters = _legacy_sampler_inputs(graph, "IterKSampler")
    assert iters["end_at_step"] == 7

    wan = _legacy_sampler_inputs(graph, "WanFixedSeed")
    assert wan["start_at_step"] == 7
    assert wan["end_at_step"] == 7


def test_apply_express_sampler_steps_does_not_mutate_source_workflow():
    original = _load_i2v_base()
    before = copy.deepcopy(original)
    graph = copy.deepcopy(original)
    _apply_express_sampler_steps(graph, express_video=False, total_steps=10)
    assert before == original


LTX2_I2V = Path(__file__).resolve().parent.parent / "workflows" / "THM_video_ltx2_i2v.json"


def test_set_steps_on_ltx2_thm_steps_scheduler():
    from scripts import workflow_controls as wc

    graph = json.loads(LTX2_I2V.read_text(encoding="utf-8"))
    assert wc.video_project_controls_steps(graph) is True
    assert wc.set_steps(graph, 12) == 1
    steps_node = graph["106"]
    assert steps_node["inputs"]["steps"] == 12
