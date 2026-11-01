"""LoRA names are checked against the files ComfyUI can load: a dropped extension or folder is
filled in when one file matches, and a name no file answers to is refused before it renders."""

import pytest

from thm_core import CommandRefused, execute
from thm_core.commands import Generate, RecallGeneration, SetBinField
from thm_core.loras import resolve_lora_name
from thm_core.models import Lora
from thm_core_fixtures import USER, add_bin, image_workflow, make_env, make_project, make_store

FILES = ["style.safetensors", "people/Huh-leen.safetensors", "a/dup.safetensors", "b/dup.safetensors", "old.ckpt"]


@pytest.fixture
def world(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path, {"img.json": image_workflow(2)})
    env.loras_dir = tmp_path / "loras"
    for name in FILES:
        (env.loras_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (env.loras_dir / name).write_bytes(b"x")
    return store, p, env


@pytest.mark.parametrize(
    "typed, spelled",
    [
        ("style.safetensors", "style.safetensors"),
        ("style", "style.safetensors"),
        ("STYLE", "style.safetensors"),
        ("Huh-leen", "people/Huh-leen.safetensors"),
        ("people\\Huh-leen.safetensors", "people/Huh-leen.safetensors"),
        ("huh-leen.safetensors", "people/Huh-leen.safetensors"),
        ("old", "old.ckpt"),
        ("a/dup", "a/dup.safetensors"),
        ("dup", None),  # two folders: a question, not a guess
        ("nope", None),
    ],
)
def test_resolve_lora_name(typed, spelled):
    assert resolve_lora_name(typed, FILES) == spelled


def test_set_field_spells_names_and_refuses_unknown(world):
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, SetBinField(bin=s, field="loras", value=[{"name": "style", "strength": 0.7}, {"name": "Huh-leen", "strength": 1}]), USER, env=env)
    assert [l.name for l in p.bins_by_id[s].loras] == ["style.safetensors", "people/Huh-leen.safetensors"]
    with pytest.raises(CommandRefused) as exc:
        execute(store, p, SetBinField(bin=s, field="loras", value=[{"name": "styel", "strength": 1}]), USER, env=env)
    assert exc.value.code == "unknown_lora" and "style.safetensors" in exc.value.detail


def test_prompt_tags_are_spelled_and_merged(world):
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, SetBinField(bin=s, field="loras", value=[{"name": "style.safetensors", "strength": 0.7}]), USER, env=env)
    execute(store, p, SetBinField(bin=s, field="prompt", value="<lora:style:0.4> a cat"), USER, env=env)
    assert [(l.name, l.strength) for l in p.bins_by_id[s].loras] == [("style.safetensors", 0.4)]
    with pytest.raises(CommandRefused) as exc:
        execute(store, p, SetBinField(bin=s, field="prompt", value="<lora:missing:1> a cat"), USER, env=env)
    assert exc.value.code == "unknown_lora"


def test_generate_refuses_unknown_and_renders_spelled(world):
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    # a recalled recipe lands even when a file has gone; the render is what refuses
    execute(store, p, RecallGeneration(bin=s, prompt="a cat", loras=[Lora(name="gone", strength=1)]), USER, env=env)
    with pytest.raises(CommandRefused) as exc:
        execute(store, p, Generate(bin=s), USER, env=env)
    assert exc.value.code == "unknown_lora"
    p.bins_by_id[s].loras = [Lora(name="style", strength=0.5)]  # written before names were checked
    g = execute(store, p, Generate(bin=s), USER, env=env).generation
    assert g.prompt_submitted.startswith("<lora:style.safetensors:0.5>")
    assert p.bins_by_id[s].loras[0].name == "style.safetensors"


def test_no_folder_checks_nothing(world):
    store, p, env = world
    env.loras_dir = None
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, SetBinField(bin=s, field="loras", value=[{"name": "anything", "strength": 1}]), USER, env=env)
    assert p.bins_by_id[s].loras[0].name == "anything"
