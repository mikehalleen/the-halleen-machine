import pytest
from pydantic import ValidationError

from thm_core import COMMANDS, Change, Decision, Generation, Message, Project, Reference
from thm_core.models import ChangeTarget, Media, MediaOrigin
from thm_core_fixtures import add_bin, add_media, add_take, bind, live_session, make_project


def test_project_round_trips_with_every_record_type():
    p = make_project()
    mara = add_bin(p, "Mara")
    m = add_media(p, mara)
    v = add_bin(p, "Video 1", "vid.json")
    bind(p, v, m)
    t = add_take(p, v, type="video")
    p.bins_by_id[v].selected = t
    live_session(p)
    data = p.model_dump(mode="json")
    again = Project.model_validate(data)
    assert again.model_dump(mode="json") == data
    assert again.bins_by_id[v].references[0].handle == "mara.png"
    assert again.schema_version == 6


@pytest.mark.parametrize(
    "origin",
    [
        {"type": "clip", "start_s": 0, "end_s": 1},
        {"type": "frame", "source": "m_1"},
        {"type": "generation"},
        {"type": "generation", "generation": "g_1"},
        {"type": "upload"},
    ],
)
def test_origin_variants_need_their_keys(origin):
    with pytest.raises(ValidationError):
        MediaOrigin.model_validate(origin)


def test_origin_variants_accept_complete_records():
    MediaOrigin(type="clip", source="m_1", start_s=0.5, end_s=2.0, bin="b_1")
    MediaOrigin(type="frame", source="m_1", at_s=1.0, bin="b_1")
    MediaOrigin(type="generation", generation="g_1", bin="b_1")
    MediaOrigin(type="upload", bin="b_1")


def test_schema_3_keys_fold_into_bin():
    """The logs are never rewritten, so every record that appears in one reads the old
    ``shot``/``item`` keys as ``bin`` and drops them."""
    o = MediaOrigin.model_validate({"type": "generation", "generation": "g_1", "shot": "s_1"})
    assert o.bin == "s_1" and "shot" not in o.model_dump()
    assert MediaOrigin.model_validate({"type": "upload", "item": "it_1"}).bin == "it_1"
    r = Reference.model_validate({"id": "r_1", "media": "m_1", "item": "shot:s_9", "handle": "video-1"})
    assert r.bin == "s_9" and "item" not in r.model_dump()
    assert Reference.model_validate({"id": "r_1", "media": "m_1", "item": "it_1", "handle": "x"}).bin == "it_1"
    assert ChangeTarget.model_validate({"type": "shot", "id": "s_1"}).type == "bin"
    assert ChangeTarget.model_validate({"type": "item", "id": "it_1"}).type == "bin"
    g = Generation.model_validate({"id": "g_1", "shot": "s_1", "kind": "video", "ts": "t", "workflow": "w"})
    assert g.bin == "s_1" and g.kind == "video"
    g = Generation.model_validate({"id": "g_1", "item": "it_1", "kind": None, "ts": "t", "workflow": "w"})
    assert g.bin == "it_1" and g.kind == "image"
    assert "shot" not in g.model_dump() and "item" not in g.model_dump()


MINIMAL = {
    "create_bin": {},
    "duplicate_bin": {"bin": "b_1"},
    "set_bin_field": {"bin": "b_1", "field": "prompt", "value": "x"},
    "bind_reference": {"bin": "b_1", "media": "m_1"},
    "unbind_reference": {"bin": "b_1", "reference": "r_1"},
    "reorder_references": {"bin": "b_1", "order": []},
    "rebind_reference": {"bin": "b_1", "reference": "r_1", "media": "m_1"},
    "generate": {"bin": "b_1"},
    "mark_take": {"media": "m_1", "status": "pass"},
    "add_media_to_bin": {"bin": "b_1", "media": "m_1"},
    "remove_media_from_bin": {"bin": "b_1", "media": "m_1"},
    "recall_generation": {"bin": "b_1"},
    "start_from_media": {"media": "m_1"},
    "delete_media": {"media": "m_1"},
    "delete_bin": {"bin": "b_1"},
    "propose_shots": {"items": [{"name": "x", "description": "y"}]},
    "post_message": {"text": "hi"},
    "answer_notes": {"notes": ["msg_1"], "did": "re-render queued"},
    "withdraw_change": {"change": "ch_1"},
    "set_project_settings": {"width": 720, "height": 1280},
    "start_session": {},
    "set_session_mode": {"mode": "auto"},
    "connect_session": {"harness": "claude-code"},
    "end_session": {},
    "add_project_rule": {"text": "the plate is in every shot"},
    "remove_project_rule": {"rule": "rule_x"},
    "create_sequence": {},
    "set_sequence_field": {"sequence": "sq_1", "field": "name", "value": "Rough cut"},
    "clone_sequence": {"sequence": "sq_1"},
    "delete_sequence": {"sequence": "sq_1"},
    "add_sequence_items": {"sequence": "sq_1", "bins": ["b_1"]},
    "remove_sequence_item": {"sequence": "sq_1", "bin": "b_1"},
    "reorder_sequence": {"sequence": "sq_1", "order": ["b_1"]},
    "set_item_trim": {"sequence": "sq_1", "bin": "b_1", "head_s": 0.5},
    "set_item_still": {"sequence": "sq_1", "bin": "b_1", "still_s": 3.0},
    "set_item_agent": {"sequence": "sq_1", "bin": "b_1", "props": {"gain_db": -6}},
    "set_sequence_agent": {"sequence": "sq_1", "props": {"walla_bed": "v5", "summary": "walla bed"}},
    "set_sequence_layer": {"sequence": "sq_1", "layer": {"id": "music", "media": "m_1"}, "summary": "the song under it all"},
    "remove_sequence_layer": {"sequence": "sq_1", "layer": "music", "summary": ""},
    "set_item_mix": {"sequence": "sq_1", "bin": "b_1", "layers": {"music": -6}, "summary": "music -6 dB under the line"},
    "create_post": {"bin": "b_1"},
    "set_post_field": {"post": "pub_1", "field": "title", "value": "The cut"},
    "delete_post": {"post": "pub_1"},
    "upload_post": {"post": "pub_1"},
}


def test_every_command_has_a_minimal_payload_in_this_test():
    assert set(MINIMAL) == set(COMMANDS)


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_commands_refuse_unknown_fields(name):
    cls = COMMANDS[name]
    cls.model_validate(MINIMAL[name])
    with pytest.raises(ValidationError):
        cls.model_validate({**MINIMAL[name], "invented_key": 1})


def test_retired_command_names_are_gone():
    for name in ("create_shot", "set_shot_field", "delete_shot", "reorder_shots", "generate_reference", "create_item", "rename_item", "set_item_cover", "delete_item"):
        assert name not in COMMANDS


def test_set_bin_field_refuses_unknown_field_name():
    with pytest.raises(ValidationError):
        COMMANDS["set_bin_field"].model_validate({"bin": "b_1", "field": "hue", "value": 1})


@pytest.mark.parametrize("status", ["proposed", "pending", "approved", "rejected", "superseded", "closed", "replied"])
def test_decision_statuses(status):
    assert Decision(status=status).status == status


@pytest.mark.parametrize("kind", ["user", "agent", "system", "change", "decision", "blocker"])
def test_message_kinds(kind):
    assert Message(id="msg_1", ts="t", kind=kind).kind == kind


def test_records_tolerate_unknown_keys_on_disk():
    m = Media.model_validate({"id": "m_1", "type": "image", "path": "x", "origin": {"type": "upload", "bin": "b_1"}, "future_key": 1})
    assert m.id == "m_1"
    c = Change.model_validate({"id": "c_1", "ts": "t", "actor": "user", "command": "x", "target": {"type": "bin", "id": "b_1"}, "later": 2})
    assert c.decision is None
