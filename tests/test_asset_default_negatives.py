"""Asset test default negative prompt merge."""

from single_gen_helpers import (
    TEST_CHARACTER_DEFAULT_NEGATIVE,
    TEST_SETTING_DEFAULT_NEGATIVE,
    TEST_STYLE_DEFAULT_NEGATIVE,
    _asset_test_negative,
    _create_temp_json_for_character_test,
    _create_temp_json_for_setting_asset_test,
    _create_temp_json_for_style_asset_test,
)


def test_asset_test_negative_user_only():
    assert _asset_test_negative("my custom neg", "") == "my custom neg"


def test_asset_test_negative_default_only():
    assert _asset_test_negative("", TEST_SETTING_DEFAULT_NEGATIVE) == TEST_SETTING_DEFAULT_NEGATIVE


def test_asset_test_negative_user_then_default():
    merged = _asset_test_negative("foo", TEST_SETTING_DEFAULT_NEGATIVE)
    assert merged.startswith("foo,")
    assert TEST_SETTING_DEFAULT_NEGATIVE in merged


def test_asset_test_negative_both_empty():
    assert _asset_test_negative("", "") == ""


def test_setting_asset_test_merges_negative_on_keyframe():
    full_data = {"project": {"name": "test", "characters": [], "settings": [], "styles": []}}
    setting = {"id": "s1", "name": "Office", "prompt": "modern office", "negative_prompt": "foo"}
    temp_data, seq_id, kf_id = _create_temp_json_for_setting_asset_test(full_data, setting)
    kf = temp_data["sequences"][seq_id]["keyframes"][kf_id]
    neg = kf["negatives"]["left"]
    assert neg.startswith("foo,")
    assert TEST_SETTING_DEFAULT_NEGATIVE in neg
    assert setting["negative_prompt"] == "foo"


def test_character_asset_test_merges_negative_without_mutating_source():
    full_data = {"project": {"name": "test", "characters": [], "settings": [], "styles": []}}
    char = {
        "id": "c1",
        "name": "Hero",
        "prompt": "tall hero",
        "negative_prompt": "bar",
    }
    temp_data, seq_id, kf_id = _create_temp_json_for_character_test(full_data, char, pose_path="")
    test_char = temp_data["project"]["characters"][0]
    assert test_char["negative_prompt"].startswith("bar,")
    assert TEST_CHARACTER_DEFAULT_NEGATIVE in test_char["negative_prompt"]
    assert char["negative_prompt"] == "bar"


def test_style_asset_test_merges_negative_on_keyframe():
    full_data = {"project": {"name": "test", "characters": [], "settings": [], "styles": []}}
    style = {"id": "st1", "name": "Noir", "prompt": "high contrast", "negative_prompt": "baz"}
    temp_data, seq_id, kf_id = _create_temp_json_for_style_asset_test(full_data, style)
    kf = temp_data["sequences"][seq_id]["keyframes"][kf_id]
    neg = kf["negatives"]["left"]
    assert neg.startswith("baz,")
    assert TEST_STYLE_DEFAULT_NEGATIVE in neg
