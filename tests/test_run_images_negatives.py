"""Character negative merge for run_images keyframe injection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_images import (
    _char_negative,
    _merged_negative_2char_side,
    _merged_negative_single,
    merge_negatives,
)


def test_char_negative_empty_when_missing():
    assert _char_negative(None) == ""
    assert _char_negative({}) == ""
    assert _char_negative({"negative_prompt": "  leash  "}) == "leash"


def test_merged_negative_single_includes_character():
    pneg = {"global": "global neg", "keyframes_all": "kf all"}
    char = {"negative_prompt": "person, leash"}
    kf = {"left": "kf left"}
    result = _merged_negative_single(pneg, kf, char)
    assert result == merge_negatives("global neg", "kf all", "person, leash", "kf left")


def test_merged_negative_single_order():
    pneg = {"global": "a", "keyframes_all": "b"}
    char = {"negative_prompt": "c"}
    kf = {"left": "d"}
    assert _merged_negative_single(pneg, kf, char) == "a, b, c, d"


def test_merged_negative_2char_sides_use_respective_characters():
    pneg = {"global": "g", "keyframes_all": "k"}
    kf = {"left": "L", "right": "R"}
    left = {"negative_prompt": "left char neg"}
    right = {"negative_prompt": "right char neg"}
    assert _merged_negative_2char_side(pneg, kf, "left", left) == "g, k, left char neg, L"
    assert _merged_negative_2char_side(pneg, kf, "right", right) == "g, k, right char neg, R"
