"""thm_core.decide: what approve, reject and reopen do to a card. The manual modes only make
renders into cards, so every structural change here asks for its card with ``card=True``."""

import pytest

from thm_core import Change, CommandContext, CommandRefused, decide, execute
from thm_core.commands import BindReference, CreateBin, DeleteMedia, Generate, MarkTake, SetBinField, UnbindReference
from thm_core.models import ChangeTarget, Decision
from thm_core_fixtures import (
    AGENT,
    USER,
    add_bin,
    add_media,
    add_take,
    bind,
    image_workflow,
    live_session,
    make_env,
    make_project,
    make_store,
    video_workflow,
)


@pytest.fixture
def world(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path, {"img.json": image_workflow(2), "vid.json": video_workflow()})
    live_session(p)
    return store, p, env


def refused(code, fn):
    with pytest.raises(CommandRefused) as exc:
        fn()
    assert exc.value.code == code, f"expected {code}, got {exc.value.code}: {exc.value.detail}"


def _status(store, p, change_id):
    return store.read_changes(p)[change_id].decision.status


def test_approve_pending_writes_decision_message(world):
    store, p, env = world
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    ch = execute(store, p, SetBinField(bin=s, field="length_s", value=8, card=True), AGENT, env=env).change
    r = decide(store, p, ch.id, "approve", "apply to the next ones too", USER, env=env)
    assert r.change.decision.status == "approved" and r.change.decision.note == "apply to the next ones too"
    assert _status(store, p, ch.id) == "approved"
    last = store.read_messages(p)[-1]
    assert last.kind == "decision" and last.change == ch.id and last.status == "approved" and last.note == "apply to the next ones too"
    assert p.bins_by_id[s].length_s == 8


def test_reject_settings_change_reverts_field(world):
    store, p, env = world
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    ch = execute(store, p, SetBinField(bin=s, field="length_s", value=8, card=True), AGENT, env=env).change
    r = decide(store, p, ch.id, "reject", "too long", USER, env=env)
    assert r.change.decision.status == "rejected"
    assert p.bins_by_id[s].length_s == 5.0
    assert store.load(p.slug).bins_by_id[s].length_s == 5.0


def test_reject_bind_removes_reference_keeps_media(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    m = add_media(p, mara)
    s = add_bin(p, "Image 1", "img.json")
    ch = execute(store, p, BindReference(bin=s, media=m, card=True), AGENT, env=env).change
    decide(store, p, ch.id, "reject", None, USER, env=env)
    assert p.bins_by_id[s].references == []
    assert m in p.media_by_id and m in p.bins_by_id[mara].media
    assert p.bins_by_id[s].counters == {}


def test_reject_rename_restores_every_referencing_bin(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1", "img.json")
    bind(p, s, add_media(p, mara))
    p.bins_by_id[s].prompt = ".mara waits"
    ch = execute(store, p, SetBinField(bin=mara, field="name", value="Mara Vane", card=True), AGENT, env=env).change
    assert p.bins_by_id[s].prompt == ".mara-vane waits"
    decide(store, p, ch.id, "reject", None, USER, env=env)
    assert p.bins_by_id[mara].name == "Mara" and p.bins_by_id[s].prompt == ".mara waits"
    assert p.bins_by_id[s].references[0].handle == "mara"


def test_reject_on_take_marks_fail_and_keeps_take(world):
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    t = add_take(p, s)
    ch = execute(store, p, MarkTake(media=t, status="pass", card=True), AGENT, env=env).change
    r = decide(store, p, ch.id, "reject", "hands wrong", USER, env=env)
    assert r.change.decision.status == "rejected"
    assert p.bins_by_id[s].media == [t]
    assert p.media_by_id[t].qc.status == "fail" and p.media_by_id[t].qc.note == "hands wrong" and p.media_by_id[t].qc.by == "user"


def test_reject_selected_change_marks_that_take(world):
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    t1, t2 = add_take(p, s), add_take(p, s)
    ch = execute(store, p, SetBinField(bin=s, field="selected", value=t2, card=True), AGENT, env=env).change
    decide(store, p, ch.id, "reject", "no", USER, env=env)
    assert p.bins_by_id[s].media == [t1, t2] and p.bins_by_id[s].selected == t2
    assert p.media_by_id[t2].qc.status == "fail" and p.media_by_id[t1].qc is None


def test_reopen_reapplies_and_returns_to_pending(world):
    store, p, env = world
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    ch = execute(store, p, SetBinField(bin=s, field="length_s", value=8, card=True), AGENT, env=env).change
    decide(store, p, ch.id, "reject", "too long", USER, env=env)
    r = decide(store, p, ch.id, "reopen", "too long", USER, env=env)
    assert r.change.decision.status == "pending" and r.change.decision.note == "too long"
    assert p.bins_by_id[s].length_s == 8.0
    decide(store, p, ch.id, "approve", None, USER, env=env)
    assert _status(store, p, ch.id) == "approved"


def test_superseded_when_user_moved_the_field(world):
    store, p, env = world
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    ch = execute(store, p, SetBinField(bin=s, field="length_s", value=8, card=True), AGENT, env=env).change
    execute(store, p, SetBinField(bin=s, field="length_s", value=10), USER, env=env)
    r = decide(store, p, ch.id, "reject", None, USER, env=env)
    assert r.change.decision.status == "superseded"
    assert p.bins_by_id[s].length_s == 10.0


def test_superseded_partial_reverts_only_untouched_fields(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1", "img.json")
    r1 = bind(p, s, add_media(p, mara))
    bind(p, s, add_media(p, mara))
    p.bins_by_id[s].prompt = ".mara with .mara.2"
    # unbind moves two recorded fields at once: the references, and the prompt that renumbering rewrote
    ch = execute(store, p, UnbindReference(bin=s, reference=r1.id, card=True), AGENT, env=env).change
    assert set(ch.after) == {"references", "prompt"}
    assert p.bins_by_id[s].prompt == ".mara with .mara"
    # the user then edits the prompt themselves, so only the references are still where the change left them
    p.bins_by_id[s].prompt = "a prompt of my own"
    r = decide(store, p, ch.id, "reject", None, USER, env=env)
    assert r.change.decision.status == "superseded"
    assert [x.handle for x in p.bins_by_id[s].references] == ["mara", "mara.2"]
    assert p.bins_by_id[s].prompt == "a prompt of my own"


def test_a_render_is_never_proposed(world):
    """The verdict on a render is on the take, after it lands: a manual mode makes the generate
    itself the card. Asking before the spend is refused in every mode."""
    store, p, env = world
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1", "img.json")
    bind(p, s, add_media(p, mara))
    p.bins_by_id[s].prompt = ".mara"
    cursor = store.message_cursor(p)
    with pytest.raises(CommandRefused) as exc:
        execute(store, p, Generate(bin=s), CommandContext(actor="agent", inbox_cursor=cursor, propose=True), env=env)
    assert exc.value.code == "propose_render"
    assert store.read_generations(p) == [] and store.read_changes(p) == {}


def test_reject_proposed_mutates_nothing_and_reopen_returns_to_proposed(world):
    store, p, env = world
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    ch = execute(store, p, SetBinField(bin=s, field="length_s", value=8), CommandContext(actor="agent", propose=True), env=env).change
    assert p.bins_by_id[s].length_s == 5.0
    decide(store, p, ch.id, "reject", "no", USER, env=env)
    assert p.bins_by_id[s].length_s == 5.0 and _status(store, p, ch.id) == "rejected"
    decide(store, p, ch.id, "reopen", None, USER, env=env)
    assert _status(store, p, ch.id) == "proposed" and p.bins_by_id[s].length_s == 5.0
    decide(store, p, ch.id, "approve", None, USER, env=env)
    assert p.bins_by_id[s].length_s == 8.0


def test_user_cannot_propose(world):
    store, p, env = world
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    refused("wrong_actor", lambda: execute(store, p, SetBinField(bin=s, field="length_s", value=8), CommandContext(actor="user", propose=True), env=env))


def test_decision_refusals(world):
    store, p, env = world
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    user_change = execute(store, p, SetBinField(bin=s, field="length_s", value=6), USER, env=env).change
    refused("not_agent_change", lambda: decide(store, p, user_change.id, "approve", None, USER, env=env))
    ch = execute(store, p, SetBinField(bin=s, field="length_s", value=8, card=True), AGENT, env=env).change
    refused("wrong_actor", lambda: decide(store, p, ch.id, "approve", None, AGENT, env=env))
    refused("unknown_id", lambda: decide(store, p, "c_nope", "approve", None, USER, env=env))
    refused("bad_transition", lambda: decide(store, p, ch.id, "reopen", None, USER, env=env))
    decide(store, p, ch.id, "reject", None, USER, env=env)
    refused("bad_transition", lambda: decide(store, p, ch.id, "approve", None, USER, env=env))
    refused("bad_transition", lambda: decide(store, p, ch.id, "reject", None, USER, env=env))


def _two_bin_library(p):
    b1 = add_bin(p, "Mara")
    b2 = add_bin(p, "Mara too")
    b3 = add_bin(p, "Untouched")
    m = add_media(p, b1)
    keep = add_media(p, b1)
    add_media(p, b3)
    p.bins_by_id[b2].media.append(m)
    p.bins_by_id[b1].selected = m
    return b1, b2, b3, m, keep


def test_reject_delete_media_restores_library_and_galleries_and_reopen_deletes_again(world):
    store, p, env = world
    b1, b2, b3, m, keep = _two_bin_library(p)
    untouched = p.bins_by_id[b3].model_dump()
    ch = execute(store, p, DeleteMedia(media=m, card=True), AGENT, env=env).change
    assert m not in p.media_by_id and m not in p.bins_by_id[b2].media
    r = decide(store, p, ch.id, "reject", None, USER, env=env)
    assert r.change.decision.status == "rejected"
    assert m in p.media_by_id
    assert p.bins_by_id[b1].media == [m, keep] and p.bins_by_id[b1].selected == m
    assert p.bins_by_id[b2].media == [m]
    assert p.bins_by_id[b3].model_dump() == untouched
    assert store.load(p.slug).bins_by_id[b2].media == [m]
    decide(store, p, ch.id, "reopen", None, USER, env=env)
    assert _status(store, p, ch.id) == "pending"
    assert m not in p.media_by_id and p.bins_by_id[b1].media == [keep] and p.bins_by_id[b2].media == []
    assert p.bins_by_id[b3].model_dump() == untouched


def test_reject_delete_media_is_superseded_where_a_bin_moved_since(world):
    store, p, env = world
    b1, b2, b3, m, keep = _two_bin_library(p)
    ch = execute(store, p, DeleteMedia(media=m, card=True), AGENT, env=env).change
    p.bins_by_id[b1].prompt = "a prompt of my own"
    r = decide(store, p, ch.id, "reject", None, USER, env=env)
    assert r.change.decision.status == "superseded"
    assert m in p.media_by_id and p.bins_by_id[b2].media == [m]
    assert p.bins_by_id[b1].prompt == "a prompt of my own" and p.bins_by_id[b1].media == [keep]


def test_reject_create_bin_removes_it_and_reopen_returns_it(world):
    store, p, env = world
    other = add_bin(p, "Other")
    c = CreateBin(workflow="img.json", card=True)
    ch = execute(store, p, c, AGENT, env=env).change
    decide(store, p, ch.id, "reject", None, USER, env=env)
    assert set(p.bins_by_id) == {other}
    decide(store, p, ch.id, "reopen", None, USER, env=env)
    assert set(p.bins_by_id) == {other, c._created} and p.bins_by_id[c._created].workflow == "img.json"


def test_legacy_full_snapshot_delete_still_reverts_without_losing_newer_media(world):
    """Changes logged before diffs were compacted carry every entry on both sides; merging
    them back restores what the delete took and leaves alone what came after."""
    store, p, env = world
    b1, b2, b3, m, keep = _two_bin_library(p)
    before = {"media_by_id": p.model_dump(mode="json")["media_by_id"], "bins_by_id": p.model_dump(mode="json")["bins_by_id"]}
    execute(store, p, DeleteMedia(media=m), USER, env=env)
    after = {"media_by_id": p.model_dump(mode="json")["media_by_id"], "bins_by_id": p.model_dump(mode="json")["bins_by_id"]}
    legacy = Change(
        id="c_legacy", ts="t", actor="agent", session=p.agent_session.id, command="delete_media",
        target=ChangeTarget(type="project", id=p.id), before=before, after=after,
        description="deleted", decision=Decision(status="pending"),
    )
    store.append_change(p, legacy)
    newer = add_media(p, b3)
    store.save(p)
    r = decide(store, p, "c_legacy", "reject", None, USER, env=env)
    # the snapshot names every bin, and b3 moved since, so the verdict is superseded; the rest reverts
    assert r.change.decision.status == "superseded"
    assert m in p.media_by_id and newer in p.media_by_id
    assert p.bins_by_id[b1].media == [m, keep] and p.bins_by_id[b2].media == [m]
    assert newer in p.bins_by_id[b3].media
