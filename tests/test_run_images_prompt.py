"""Prompt assembly for run_images (reference prelude on force-gen)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from image_prompt import (
    DEFAULT_IMAGE_TEMPLATE_CUSTOM,
    compose_image_prompt,
    resolve_single_pass_prompt,
)


def test_resolve_single_pass_prompt_uses_cache_on_first_loop_pass_with_high_image_index():
    """Force-gen: iteration=0 but image_index=23 must keep prelude from simple_p_raw."""
    prelude = (
        "image1 is the setting and location reference.\n"
        "image2 defines the pose to follow."
    )
    project = {"style_prompt": "style line"}
    seq = {}
    kf = {"layout": "Man 2 runs in the forest"}
    char = {"prompt": "character prompt"}

    simple_p_raw = compose_image_prompt(
        DEFAULT_IMAGE_TEMPLATE_CUSTOM,
        project,
        seq,
        kf,
        char,
        0,
        reference_prelude=prelude,
    )
    assert prelude.splitlines()[0] in simple_p_raw

    # Bug path: i=23, iteration=0 — must use simple_p_raw, not recompose without prelude.
    sp = resolve_single_pass_prompt(
        iteration=0,
        image_index=23,
        simple_p_raw=simple_p_raw,
        prompt_template=DEFAULT_IMAGE_TEMPLATE_CUSTOM,
        project_data=project,
        sequence_data=seq,
        keyframe_data=kf,
        char_data=char,
        reference_prelude=prelude,
    )
    assert sp == simple_p_raw
    assert sp.startswith("image1 is the setting")

    # Second loop pass: recompose without prelude.
    sp2 = resolve_single_pass_prompt(
        iteration=1,
        image_index=24,
        simple_p_raw=simple_p_raw,
        prompt_template=DEFAULT_IMAGE_TEMPLATE_CUSTOM,
        project_data=project,
        sequence_data=seq,
        keyframe_data=kf,
        char_data=char,
        reference_prelude=prelude,
    )
    assert "image1 is the setting" not in sp2
    assert "Man 2 runs in the forest" in sp2
