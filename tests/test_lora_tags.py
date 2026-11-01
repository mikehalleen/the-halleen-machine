from scripts.lora_tags import LoraSpec, find_lora_tags, format_lora_tag, has_lora_named, scale_lora_spec, strip_lora_tags


def test_parse_two_segment_tag_mirrors_secondary():
    specs = find_lora_tags("__lora:style.safetensors:0.75__")
    assert len(specs) == 1
    assert specs[0].name == "style.safetensors"
    assert specs[0].strength == 0.75
    assert specs[0].strength_b is None
    assert specs[0].secondary_strength() == 0.75


def test_parse_three_segment_tag():
    specs = find_lora_tags("__lora:SDXL-LORA-mylora.safetensors:0.75:0.5__")
    assert len(specs) == 1
    assert specs[0].strength == 0.75
    assert specs[0].strength_b == 0.5
    assert specs[0].secondary_strength() == 0.5


def test_parse_angle_notation_and_mixed_text():
    text = "a cat <lora:style.safetensors:0.8> on a rail __lora:old.safetensors:1:0.5__ <lora:sub/two.safetensors:0.6:0.4>"
    specs = find_lora_tags(text)
    assert [s.name for s in specs] == ["style.safetensors", "old.safetensors", "sub/two.safetensors"]
    assert specs[0].strength == 0.8 and specs[0].strength_b is None
    assert specs[1].strength == 1.0 and specs[1].strength_b == 0.5
    assert specs[2].strength == 0.6 and specs[2].strength_b == 0.4


def test_format_lora_tag_writes_angle_notation_and_round_trips():
    two = LoraSpec("a.safetensors", 0.75)
    three = LoraSpec("a.safetensors", 0.75, 0.5)
    assert format_lora_tag(two) == "<lora:a.safetensors:0.75>"
    assert format_lora_tag(three) == "<lora:a.safetensors:0.75:0.5>"
    assert find_lora_tags(format_lora_tag(three)) == [three]


def test_strip_lora_tags_removes_both_notations():
    text = "hello __lora:a.safetensors:0.75__ world <lora:b.safetensors:1:0.5>"
    assert strip_lora_tags(text) == "hello world"
    assert strip_lora_tags("<lora:a:1> line one\nline two <lora:b:1>\n") == "line one\nline two"


def test_has_lora_named_matches_either_notation():
    assert has_lora_named("x __lora:a.safetensors:1__", "a.safetensors")
    assert has_lora_named("x <lora:a.safetensors:0.5>", "a.safetensors")
    assert not has_lora_named("x <lora:b.safetensors:0.5>", "a.safetensors")


def test_scale_lora_spec_applies_multiplier_to_both_strengths():
    spec = scale_lora_spec(LoraSpec("a.safetensors", 0.75, 0.5), 2.0)
    assert spec.strength == 1.5
    assert spec.strength_b == 1.0


def test_lora_specs_from_spec_merges_prompt_tags_over_structured_entries():
    from scripts.generation_spec import lora_specs_from_spec

    spec = {"loras": [{"name": "a.safetensors", "strength": 0.3}, {"name": "b.safetensors", "strength": 1.0}]}
    specs = lora_specs_from_spec(spec, "<lora:a.safetensors:0.9> text")
    assert {s.name: s.strength for s in specs} == {"a.safetensors": 0.9, "b.safetensors": 1.0}
    assert lora_specs_from_spec({}, "plain text") == []


def test_inject_loras_applies_split_clip_strength():
    from scripts import workflow_controls as wc

    workflow = {
        "15": {
            "_meta": {"title": "MainCheckpoint"},
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "base.safetensors"},
        },
        "42": {
            "_meta": {"title": "MainLora"},
            "class_type": "Power Lora Loader (rgthree)",
            "inputs": {"model": ["15", 0], "clip": ["15", 1]},
        },
        "3": {
            "_meta": {"title": "KSampler"},
            "class_type": "KSampler",
            "inputs": {"model": ["42", 0]},
        },
    }
    wc.inject_loras(workflow, [LoraSpec("char.safetensors", 0.75, 0.5)])
    injected = [n for n, node in workflow.items() if wc.node_title(node).startswith("Injected_")][0]
    assert workflow[injected]["inputs"]["strength_model"] == 0.75
    assert workflow[injected]["inputs"]["strength_clip"] == 0.5
    assert workflow[injected]["inputs"]["model"] == ["42", 0]
    assert workflow[injected]["inputs"]["clip"] == ["42", 1]


def test_inject_prompt_loras_high_low_strengths():
    from scripts.run_video import inject_prompt_loras

    graph = {
        "10": {
            "_meta": {"title": "HighNoiseUnet"},
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "high.safetensors"},
        },
        "20": {
            "_meta": {"title": "LowNoiseUnet"},
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "low.safetensors"},
        },
        "30": {
            "_meta": {"title": "SamplerHigh"},
            "class_type": "KSampler",
            "inputs": {"model": ["10", 0]},
        },
        "40": {
            "_meta": {"title": "SamplerLow"},
            "class_type": "KSampler",
            "inputs": {"model": ["20", 0]},
        },
    }

    def fake_resolve(name: str):
        return (f"high_{name}", f"low_{name}")

    import scripts.run_video as rv

    original = rv.resolve_lora_pair
    rv.resolve_lora_pair = fake_resolve
    try:
        inject_prompt_loras(graph, [LoraSpec("mylora.safetensors", 0.75, 0.5)])
    finally:
        rv.resolve_lora_pair = original

    high_nodes = [
        n
        for n, node in graph.items()
        if isinstance(node, dict) and node.get("_meta", {}).get("title", "").startswith("Injected_High_")
    ]
    low_nodes = [
        n
        for n, node in graph.items()
        if isinstance(node, dict) and node.get("_meta", {}).get("title", "").startswith("Injected_Low_")
    ]
    assert len(high_nodes) == 1
    assert len(low_nodes) == 1
    assert graph[high_nodes[0]]["inputs"]["strength_model"] == 0.75
    assert graph[low_nodes[0]]["inputs"]["strength_model"] == 0.5
    assert graph["30"]["inputs"]["model"][0] == high_nodes[0]
    assert graph["40"]["inputs"]["model"][0] == low_nodes[0]
