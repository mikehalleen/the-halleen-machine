"""Project settings applied at generation time: global prompts folded by the assembler, canvas in params."""

from thm_core import CommandContext, assemble, execute, merge_negatives, resolve_references
from thm_core.commands import Generate
from thm_core_fixtures import USER, add_bin, make_env, make_project, make_store


def _assembled(p, bin_id, env):
    bin_ = p.bins_by_id[bin_id]
    resolved = resolve_references(p, bin_, isfile=env.isfile)
    return assemble(p, bin_, resolved, workflows_dir=env.workflows_dir, capacity=env.capacity(bin_.workflow))


def test_merge_negatives_dedupes_case_insensitively():
    assert merge_negatives("blurry, Extra limbs", "extra limbs, text,, watermark") == "blurry, Extra limbs, text, watermark"
    assert merge_negatives("", "text") == "text" and merge_negatives("", "") == ""


def test_globals_untouched_when_empty(tmp_path):
    env = make_env(tmp_path)
    p = make_project()
    s = add_bin(p, "Image 1", "img.json")
    p.bins_by_id[s].prompt = "a cat"
    p.bins_by_id[s].negative = "blurry"
    out = _assembled(p, s, env)
    assert out.prompt_submitted == "a cat" and out.negative_submitted == "blurry"


def test_globals_fold_per_workflow_kind(tmp_path):
    env = make_env(tmp_path)
    p = make_project()
    p.defaults.image_prompt = "35mm grain"
    p.defaults.image_negative = "text, blurry"
    p.defaults.video_prompt = "slow dolly"
    p.defaults.video_negative = "jitter"
    img = add_bin(p, "Image 1", "img.json")
    vid = add_bin(p, "Video 1", "vid.json")
    p.bins_by_id[img].prompt = "a cat"
    p.bins_by_id[img].negative = "blurry, extra limbs"
    out = _assembled(p, img, env)
    assert out.prompt_submitted == "a cat\n35mm grain"
    assert out.negative_submitted == "blurry, extra limbs, text"
    assert out.prompt_typed == "a cat"  # the record keeps what the user typed
    out = _assembled(p, vid, env)
    assert out.prompt_submitted == "slow dolly" and out.negative_submitted == "jitter"  # an empty bin prompt: the global stands alone


def test_generate_seeds_canvas_below_caller_params(tmp_path):
    env = make_env(tmp_path)
    store = make_store(tmp_path)
    p = make_project(store)
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    r = execute(store, p, Generate(bin=s), USER, env=env)
    assert "width" not in r.generation.params  # unconfigured: nothing seeded
    assert r.generation.kind == "video"
    p.defaults.width, p.defaults.height = 720, 1280
    r = execute(store, p, Generate(bin=s, params={"height": 720}), CommandContext(actor="user"), env=env)
    assert r.generation.params == {"width": 720, "height": 720, "length_s": 5.0}
