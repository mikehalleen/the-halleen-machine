import copy
import json
from pathlib import Path

from scripts.run_video import _apply_express_sampler_steps, find_nodes_by_title





def _legacy_sampler_inputs(graph: dict, title: str) -> dict:
    for _, node in find_nodes_by_title(graph, title):
        return dict(node.get("inputs", {}))
    raise AssertionError(f"{title} not found")












