"""Sequences: an ordered list of bins that plays each bin's selected take."""

import pytest

from thm_core import CommandRefused, execute
from thm_core.commands import (
    AddSequenceItems,
    CloneSequence,
    CreateSequence,
    DeleteBin,
    DeleteSequence,
    RemoveSequenceItem,
    ReorderSequence,
    SetBinField,
    SetItemAgent,
    SetItemStill,
    SetItemTrim,
    SetSequenceField,
    item_duration,
    trim_dropped,
)
from thm_core.models import STILL_DEFAULT_S
from thm_core_fixtures import AGENT, USER, add_bin, add_media, add_take, live_session, make_env, make_project, make_store


@pytest.fixture
def world(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path)
    return store, p, env


def refused(code, fn):
    with pytest.raises(CommandRefused) as exc:
        fn()
    assert exc.value.code == code, f"expected {code}, got {exc.value.code}: {exc.value.detail}"
    return exc.value


def new_sequence(store, p, env, name=None):
    r = execute(store, p, CreateSequence(sequence_name=name), USER, env=env)
    return r.sequence


def video_take(p, bin_id, seconds):
    mid = add_take(p, bin_id, type="video")
    p.media_by_id[mid].duration_s = seconds
    p.bins_by_id[bin_id].selected = mid
    return mid


def test_create_names_in_order_and_makes_no_bin_yet(world):
    store, p, env = world
    bins_before = set(p.bins_by_id)
    r = execute(store, p, CreateSequence(), USER, env=env)
    seq = p.sequences[0]
    assert r.sequence == seq.id and r.bin is None
    assert seq.name == "Sequence 1" and seq.output_bin is None and set(p.bins_by_id) == bins_before
    assert r.change.description == "created sequence Sequence 1"
    # the output bin comes at the first export, named for the sequence, unless one is chosen
    from thm_core.commands import ensure_output_bin

    out = ensure_output_bin(p, USER, seq)
    assert out.name == "Sequence 1 Exports" and out.tags == ["exports"] and out.shot is None and out.workflow == "" and seq.output_bin == out.id
    assert ensure_output_bin(p, USER, seq) is out
    execute(store, p, CreateSequence(), USER, env=env)
    assert p.sequences[1].name == "Sequence 2"
    # the name is unique among sequences; the bin name among bins
    execute(store, p, CreateSequence(sequence_name="Sequence 2"), USER, env=env)
    assert p.sequences[2].name == "Sequence 2 2"


def test_rename_and_target_bin(world):
    store, p, env = world
    sid = new_sequence(store, p, env)
    r = execute(store, p, SetSequenceField(sequence=sid, field="name", value="Rough cut"), USER, env=env)
    seq = p.sequences[0]
    assert seq.name == "Rough cut" and r.change.description == "renamed sequence Sequence 1 to Rough cut"
    refused("bad_value", lambda: execute(store, p, SetSequenceField(sequence=sid, field="name", value="  "), USER, env=env))
    # exports can be pointed at any bin that is not in the cut; null means a new bin at export
    a, dest = add_bin(p, "a"), add_bin(p, "Deliverables")
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a]), USER, env=env)
    r = execute(store, p, SetSequenceField(sequence=sid, field="output_bin", value=dest), USER, env=env)
    assert seq.output_bin == dest and r.change.description == "Rough cut - exports land in Deliverables"
    refused("bad_value", lambda: execute(store, p, SetSequenceField(sequence=sid, field="output_bin", value=a), USER, env=env))
    refused("unknown_id", lambda: execute(store, p, SetSequenceField(sequence=sid, field="output_bin", value="b_nope"), USER, env=env))
    refused("bad_value", lambda: execute(store, p, AddSequenceItems(sequence=sid, bins=[dest]), USER, env=env))
    r = execute(store, p, SetSequenceField(sequence=sid, field="output_bin", value=None), USER, env=env)
    assert seq.output_bin is None and r.change.description == "Rough cut - exports land in a new bin at the next export"


def test_add_remove_and_the_one_instance_rule(world):
    store, p, env = world
    sid = new_sequence(store, p, env)
    a, b = add_bin(p, "a"), add_bin(p, "b")
    r = execute(store, p, AddSequenceItems(sequence=sid, bins=[a, b]), USER, env=env)
    assert [i.bin for i in p.sequences[0].items] == [a, b]
    assert r.change.description == "added a, b to Sequence 1"
    refused("already_in_sequence", lambda: execute(store, p, AddSequenceItems(sequence=sid, bins=[a]), USER, env=env))
    refused("unknown_id", lambda: execute(store, p, AddSequenceItems(sequence=sid, bins=["b_nope"]), USER, env=env))
    r = execute(store, p, RemoveSequenceItem(sequence=sid, bin=a), USER, env=env)
    assert [i.bin for i in p.sequences[0].items] == [b] and a in p.bins_by_id
    assert r.change.description == "removed a from Sequence 1"
    refused("not_in_sequence", lambda: execute(store, p, RemoveSequenceItem(sequence=sid, bin=a), USER, env=env))


def test_reorder_is_a_multiset_and_reverts_whole(world):
    store, p, env = world
    sid = new_sequence(store, p, env)
    a, b, c = add_bin(p, "a"), add_bin(p, "b"), add_bin(p, "c")
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a, b, c]), USER, env=env)
    refused("bad_order", lambda: execute(store, p, ReorderSequence(sequence=sid, order=[a, b]), USER, env=env))
    refused("bad_order", lambda: execute(store, p, ReorderSequence(sequence=sid, order=[a, b, b]), USER, env=env))
    r = execute(store, p, ReorderSequence(sequence=sid, order=[b, c, a]), USER, env=env)
    assert [i.bin for i in p.sequences[0].items] == [b, c, a]
    assert r.change.description == "Sequence 1 - moved a to #3"
    r2 = execute(store, p, ReorderSequence(sequence=sid, order=[a, c, b]), USER, env=env)
    assert r2.change.description == "Sequence 1 - reordered"
    # the change holds the whole list: reverting puts the order back
    from thm_core.commands import _revert

    assert _revert(p, r2.change)
    assert [i.bin for i in p.sequences[0].items] == [b, c, a]


def test_durations_video_still_and_title_card(world):
    store, p, env = world
    sid = new_sequence(store, p, env)
    vid = add_bin(p, "shot", "vid.json")
    video_take(p, vid, 5.0)
    still = add_bin(p, "card")
    add_media(p, still)
    empty = add_bin(p, "later", "vid.json", length_s=4.0)
    execute(store, p, AddSequenceItems(sequence=sid, bins=[vid, still, empty]), USER, env=env)
    items = p.sequences[0].items
    assert item_duration(p, items[0]) == 5.0
    assert item_duration(p, items[1]) == STILL_DEFAULT_S
    assert item_duration(p, items[2]) == 4.0  # a title card for the bin's recipe length


def test_trim_applies_to_the_item_and_drops_on_a_length_change(world):
    store, p, env = world
    sid = new_sequence(store, p, env)
    vid = add_bin(p, "shot", "vid.json")
    t1 = video_take(p, vid, 5.0)
    execute(store, p, AddSequenceItems(sequence=sid, bins=[vid]), USER, env=env)
    r = execute(store, p, SetItemTrim(sequence=sid, bin=vid, head_s=0.5, tail_s=1.0), USER, env=env)
    item = p.sequences[0].items[0]
    assert (item.head_s, item.tail_s, item.trim_len_s) == (0.5, 1.0, 5.0)
    assert item_duration(p, item) == 3.5 and not trim_dropped(p, item)
    assert r.change.description == "trimmed shot head 0.5s tail 1s"
    assert p.media_by_id[t1].duration_s == 5.0  # the take is untouched
    # a take of the same length keeps the trim
    t2 = video_take(p, vid, 5.0)
    execute(store, p, SetBinField(bin=vid, field="selected", value=t2), USER, env=env)
    assert not trim_dropped(p, item) and item_duration(p, item) == 3.5
    # a take of another length drops it: the full take plays, the row warns, the values stay until re-applied
    t3 = video_take(p, vid, 6.0)
    execute(store, p, SetBinField(bin=vid, field="selected", value=t3), USER, env=env)
    assert trim_dropped(p, item) and item_duration(p, item) == 6.0
    execute(store, p, SetItemTrim(sequence=sid, bin=vid, head_s=0.5, tail_s=1.0), USER, env=env)
    assert not trim_dropped(p, item) and item.trim_len_s == 6.0
    # full take clears everything
    r = execute(store, p, SetItemTrim(sequence=sid, bin=vid), USER, env=env)
    assert (item.head_s, item.tail_s, item.trim_len_s) == (0.0, 0.0, None)
    assert r.change.description == "shot - full take"


def test_trim_refusals(world):
    store, p, env = world
    sid = new_sequence(store, p, env)
    vid = add_bin(p, "shot", "vid.json")
    video_take(p, vid, 3.0)
    still = add_bin(p, "card")
    add_media(p, still)
    execute(store, p, AddSequenceItems(sequence=sid, bins=[vid, still]), USER, env=env)
    refused("bad_value", lambda: execute(store, p, SetItemTrim(sequence=sid, bin=vid, head_s=2.0, tail_s=1.0), USER, env=env))
    refused("bad_value", lambda: execute(store, p, SetItemTrim(sequence=sid, bin=vid, head_s=-1), USER, env=env))
    refused("wrong_kind", lambda: execute(store, p, SetItemTrim(sequence=sid, bin=still, head_s=0.5), USER, env=env))


def test_still_length(world):
    store, p, env = world
    sid = new_sequence(store, p, env)
    still = add_bin(p, "card")
    add_media(p, still)
    vid = add_bin(p, "shot", "vid.json")
    video_take(p, vid, 3.0)
    execute(store, p, AddSequenceItems(sequence=sid, bins=[still, vid]), USER, env=env)
    r = execute(store, p, SetItemStill(sequence=sid, bin=still, still_s=3.5), USER, env=env)
    assert p.sequences[0].items[0].still_s == 3.5 and item_duration(p, p.sequences[0].items[0]) == 3.5
    assert r.change.description == "card shown 3s to 3.5s"
    refused("bad_value", lambda: execute(store, p, SetItemStill(sequence=sid, bin=still, still_s=0.25), USER, env=env))
    refused("wrong_kind", lambda: execute(store, p, SetItemStill(sequence=sid, bin=vid, still_s=2.0), USER, env=env))


def test_agent_settings_are_the_agents_and_survive_every_user_edit(world):
    store, p, env = world
    live_session(p)
    sid = new_sequence(store, p, env)
    a, b = add_bin(p, "a", "vid.json"), add_bin(p, "b")
    video_take(p, a, 5.0)
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a, b]), USER, env=env)
    refused("wrong_actor", lambda: execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"gain_db": -6}), USER, env=env))
    r = execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"gain_db": -6, "sound": "room tone", "summary": "room tone, -6 dB"}, reason="the room was dead"), AGENT, env=env)
    item = p.sequences[0].items[0]
    assert item.agent == {"gain_db": -6, "sound": "room tone", "summary": "room tone, -6 dB"}
    assert r.change.description == "a - agent settings: gain_db -6, sound room tone, summary room tone, -6 dB"
    execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"sound": None, "dissolve_out_s": 0.5}), AGENT, env=env)
    assert item.agent == {"gain_db": -6, "dissolve_out_s": 0.5, "summary": "room tone, -6 dB"}
    # the user's edits never touch it
    execute(store, p, SetItemTrim(sequence=sid, bin=a, head_s=0.5), USER, env=env)
    execute(store, p, ReorderSequence(sequence=sid, order=[b, a]), USER, env=env)
    assert p.sequences[0].items[1].agent == {"gain_db": -6, "dissolve_out_s": 0.5, "summary": "room tone, -6 dB"}


def test_clone_copies_items_and_has_no_output_bin_yet(world):
    store, p, env = world
    live_session(p)
    sid = new_sequence(store, p, env, "Rough cut")
    a = add_bin(p, "a", "vid.json")
    video_take(p, a, 5.0)
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a]), USER, env=env)
    execute(store, p, SetItemTrim(sequence=sid, bin=a, head_s=0.5), USER, env=env)
    execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"gain_db": -6, "summary": "-6 dB"}), AGENT, env=env)
    dest = add_bin(p, "Deliverables")
    execute(store, p, SetSequenceField(sequence=sid, field="output_bin", value=dest), USER, env=env)
    r = execute(store, p, CloneSequence(sequence=sid), USER, env=env)
    copy_ = p.sequences[1]
    assert r.sequence == copy_.id and copy_.name == "Rough cut copy"
    assert [(i.bin, i.head_s, i.agent) for i in copy_.items] == [(a, 0.5, {"gain_db": -6, "summary": "-6 dB"})]
    assert copy_.output_bin is None and p.sequences[0].output_bin == dest
    assert r.change.description == "cloned Rough cut as Rough cut copy"


def test_delete_keeps_or_takes_the_output_bin(world):
    store, p, env = world
    sid = new_sequence(store, p, env, "Rough cut")
    a = add_bin(p, "a")
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a]), USER, env=env)
    from thm_core.commands import ensure_output_bin

    out = ensure_output_bin(p, USER, p.sequences[0]).id
    m = add_media(p, out, type="video")
    r = execute(store, p, DeleteSequence(sequence=sid), USER, env=env)
    assert p.sequences == [] and out in p.bins_by_id and m in p.media_by_id and a in p.bins_by_id
    assert r.change.description == "deleted sequence Rough cut, kept bin Rough cut Exports"
    # a sequence that never exported has no bin to speak of
    sid0 = new_sequence(store, p, env, "Never")
    assert execute(store, p, DeleteSequence(sequence=sid0), USER, env=env).change.description == "deleted sequence Never"
    sid2 = new_sequence(store, p, env, "Other")
    out2 = ensure_output_bin(p, USER, p.sequences[0]).id
    m2 = add_media(p, out2, type="video")
    r = execute(store, p, DeleteSequence(sequence=sid2, delete_exports=True), USER, env=env)
    assert p.sequences == [] and out2 not in p.bins_by_id and m2 not in p.media_by_id
    assert r.change.description == "deleted sequence Other and bin Other Exports (1 export)"


def test_deleting_a_bin_drops_it_from_every_sequence(world):
    store, p, env = world
    s1 = new_sequence(store, p, env, "One")
    s2 = new_sequence(store, p, env, "Two")
    a, b = add_bin(p, "a"), add_bin(p, "b")
    execute(store, p, AddSequenceItems(sequence=s1, bins=[a, b]), USER, env=env)
    execute(store, p, AddSequenceItems(sequence=s2, bins=[a]), USER, env=env)
    r = execute(store, p, DeleteBin(bin=a), USER, env=env)
    assert [i.bin for i in p.sequences[0].items] == [b] and p.sequences[1].items == []
    assert r.change.description.endswith("; removed from One, Two")


def test_sequences_round_trip_through_the_store(world):
    store, p, env = world
    sid = new_sequence(store, p, env, "Rough cut")
    a = add_bin(p, "a", "vid.json")
    video_take(p, a, 5.0)
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a]), USER, env=env)
    execute(store, p, SetItemTrim(sequence=sid, bin=a, head_s=0.5), USER, env=env)
    store.save(p)
    again = store.load(p.slug)
    assert again.sequences[0].name == "Rough cut" and again.sequences[0].items[0].head_s == 0.5
    assert store.list_projects() == [p.slug]


def test_add_at_a_position(world):
    store, p, env = world
    sid = new_sequence(store, p, env)
    a, b, c = add_bin(p, "a"), add_bin(p, "b"), add_bin(p, "c")
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a, c]), USER, env=env)
    r = execute(store, p, AddSequenceItems(sequence=sid, bins=[b], at=1), USER, env=env)
    assert [i.bin for i in p.sequences[0].items] == [a, b, c]
    assert r.change.description == "added b to Sequence 1 at #2"
    d = add_bin(p, "d")
    execute(store, p, AddSequenceItems(sequence=sid, bins=[d], at=99), USER, env=env)
    assert [i.bin for i in p.sequences[0].items] == [a, b, c, d]


def test_agent_settings_need_a_summary_for_the_user(world):
    store, p, env = world
    live_session(p)
    sid = new_sequence(store, p, env)
    a = add_bin(p, "a", "vid.json")
    video_take(p, a, 5.0)
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a]), USER, env=env)
    # settings without a summary are refused, with the keys named
    exc = refused("summary_required", lambda: execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"gain_db": -6}), AGENT, env=env))
    assert exc.extra == {"keys": ["gain_db"]}
    execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"gain_db": -6, "summary": "-6 dB, pop removal"}), AGENT, env=env)
    item = p.sequences[0].items[0]
    assert item.agent == {"gain_db": -6, "summary": "-6 dB, pop removal"}
    # a later merge keeps the summary; clearing it while settings remain is refused
    execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"walla": "v5"}), AGENT, env=env)
    assert item.agent["summary"] == "-6 dB, pop removal"
    refused("summary_required", lambda: execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"summary": None}), AGENT, env=env))
    # clearing everything needs no summary
    execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"gain_db": None, "walla": None, "summary": None}), AGENT, env=env)
    assert item.agent == {}
    refused("bad_value", lambda: execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"summary": "x" * 200, "gain_db": 1}), AGENT, env=env))


def test_sequence_wide_settings_and_item_scope(world):
    from thm_core.commands import SetSequenceAgent

    store, p, env = world
    live_session(p)
    sid = new_sequence(store, p, env, "Rough cut")
    a = add_bin(p, "a", "vid.json")
    video_take(p, a, 5.0)
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a]), USER, env=env)
    # a layer spans the sequence: its own slot, its own summary, the user never sets it
    refused("wrong_actor", lambda: execute(store, p, SetSequenceAgent(sequence=sid, props={"walla_bed": "v5", "summary": "walla bed"}), USER, env=env))
    refused("summary_required", lambda: execute(store, p, SetSequenceAgent(sequence=sid, props={"walla_bed": "v5"}), AGENT, env=env))
    r = execute(store, p, SetSequenceAgent(sequence=sid, props={"walla_bed": "v5", "summary": "walla bed, 0.3s fade in at the start, 0.8s fade out at the end"}), AGENT, env=env)
    assert p.sequences[0].agent["walla_bed"] == "v5"
    assert r.change.description.startswith("Rough cut - sequence settings: walla_bed v5")
    # an item's summary is about that item alone, in the user's word
    refused("scope_item", lambda: execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"walla": "v5", "summary": "walla under the whole sequence"}), AGENT, env=env))
    refused("scope_item", lambda: execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"walla": "v5", "summary": "walla throughout"}), AGENT, env=env))
    refused("bad_value", lambda: execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"walla": "v5", "summary": "walla fades out at the end of the cut"}), AGENT, env=env))
    execute(store, p, SetItemAgent(sequence=sid, bin=a, props={"walla": "v5", "summary": "walla fades out over 1.5s from 4.8s"}), AGENT, env=env)
    # the clone carries the layer
    execute(store, p, CloneSequence(sequence=sid), USER, env=env)
    assert p.sequences[1].agent == p.sequences[0].agent


def test_deleting_the_exports_bin_clears_the_target(world):
    store, p, env = world
    sid = new_sequence(store, p, env, "Rough cut")
    a, dest = add_bin(p, "a"), add_bin(p, "Deliverables")
    execute(store, p, AddSequenceItems(sequence=sid, bins=[a]), USER, env=env)
    execute(store, p, SetSequenceField(sequence=sid, field="output_bin", value=dest), USER, env=env)
    r = execute(store, p, DeleteBin(bin=dest), USER, env=env)
    assert p.sequences[0].output_bin is None
    assert r.change.description.endswith("; Rough cut has no exports bin now")
