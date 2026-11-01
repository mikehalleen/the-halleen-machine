"""propose_shots: the pre-generation cards. One card per shot, each decided on its own, approved into a bin and
renders in one step; the renders are child changes folded under it. Plus ``close`` and
``close_pending``, the verdicts that say only "seen"."""

import pytest

from thm_core import CommandContext, CommandRefused, close_pending, decide, execute
from thm_core.commands import Generate, MarkTake, ProposeShots, SetBinField, ShotItem
from thm_core.models import AgentSession
from thm_core_fixtures import (
    AGENT,
    USER,
    add_bin,
    add_media,
    add_take,
    image_workflow,
    live_session,
    make_env,
    make_project,
    make_store,
    video_workflow,
)

PROPOSE = CommandContext(actor="agent", propose=True, now="2026-09-16T10:00:00Z")


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
    return exc.value


def _requested(store, p, **kw):
    return CommandContext(actor="agent", inbox_cursor=store.message_cursor(p), now="2026-09-16T10:00:00Z", **kw)


def _shots(p, ref):
    return ProposeShots(
        title="Drive, round 3",
        reason="the shot ideas you asked for",
        items=[
            ShotItem(name="S01 hood neon", description="hood mount, neon rain", prompt="rain on the hood, @mara.png screaming", workflow="vid.json", references=[ref], length_s=4, quarter=True),
            ShotItem(name="S02 desert rabbit", description="a rabbit whips past the hood", workflow="vid.json", references=[ref]),
            ShotItem(name="S03 bus", description="a crossing bus fills the windows", prompt="the bus, @mara.png", workflow="vid.json", references=[ref]),
            ShotItem(name="S04 plate", description="the neon street, empty", workflow="img.json"),
        ],
    )


def test_propose_makes_one_card_per_shot_and_no_bins(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    ref = add_media(p, mara)
    before = set(p.bins_by_id)
    r = execute(store, p, _shots(p, ref), PROPOSE, env=env)
    # the batch's line once, then a card per shot, each its own proposal
    assert [m.kind for m in r.messages] == ["system"] + ["change"] * 4
    assert r.messages[0].text == "agent: 4 shots: Drive, round 3 -- the shot ideas you asked for"
    assert len(r.changes) == 4 and r.change is r.changes[0]
    assert all(c.decision.status == "proposed" and c.command == "propose_shots" and c.reason is None for c in r.changes)
    assert [c.description for c in r.changes] == ["proposed: shot S01 hood neon", "proposed: shot S02 desert rabbit", "proposed: shot S03 bus", "proposed: shot S04 plate"]
    assert [[i["name"] for i in c.payload["items"]] for c in r.changes] == [["S01 hood neon"], ["S02 desert rabbit"], ["S03 bus"], ["S04 plate"]]
    assert set(p.bins_by_id) == before and store.read_generations(p) == []
    assert {m.change for m in r.messages[1:]} == {c.id for c in r.changes}


def test_approve_with_items_mints_bins_and_queues_renders(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    ref = add_media(p, mara)
    p.agent_session.mode = "video_review"
    cards = execute(store, p, _shots(p, ref), PROPOSE, env=env).changes
    # each card decided on its own: kept, rewritten twice, and the last one closed
    r1 = decide(store, p, cards[0].id, "approve", None, USER, env=env, items=[{"index": 0, "enabled": True}])
    r2 = decide(store, p, cards[1].id, "approve", None, USER, env=env, items=[{"index": 0, "enabled": True, "description": "a jackrabbit whips past the hood, both flinch"}])  # no separate prompt: the edit is the prompt
    r3 = decide(store, p, cards[2].id, "approve", None, USER, env=env, items=[{"index": 0, "enabled": True, "description": "a bus, but from behind"}])  # over the agent's prompt: held for a reprompt
    r4 = decide(store, p, cards[3].id, "close", None, USER, env=env)
    assert r1.change.id == cards[0].id and r1.change.decision.status == "approved" and r1.change.decision.note is None
    assert r4.change.decision.status == "closed"
    bins = {b.name: b for b in p.bins_by_id.values()}
    assert {"S01 hood neon", "S02 desert rabbit", "S03 bus"} <= set(bins) and "S04 plate" not in bins
    # the recipes
    assert bins["S01 hood neon"].prompt == "rain on the hood, @mara.png screaming" and bins["S01 hood neon"].length_s == 4
    assert [x.handle for x in bins["S01 hood neon"].references] == ["mara.png"]
    assert bins["S02 desert rabbit"].prompt == "a jackrabbit whips past the hood, both flinch"
    assert bins["S02 desert rabbit"].description == "a jackrabbit whips past the hood, both flinch"
    assert bins["S03 bus"].prompt == "the bus, @mara.png" and bins["S03 bus"].description == "a bus, but from behind"
    # the outcome, per card
    i1, i2, i3 = (r.change.payload["items"][0] for r in (r1, r2, r3))
    assert [i["edited"] for i in (i1, i2, i3)] == [False, True, True]
    assert i1["bin_id"] == bins["S01 hood neon"].id and i1["change"] and i2["change"] and i3["change"] is None
    # a render each for the first two, its own child change under the card; video_review makes them cards
    assert len(r1.generations) == 1 and r1.generations[0].bin == bins["S01 hood neon"].id and r1.generations[0].params["quarter"] is True
    assert len(r2.generations) == 1 and r2.generations[0].bin == bins["S02 desert rabbit"].id and "quarter" not in r2.generations[0].params
    assert r3.generations == [] and r3.changes == []
    assert [c.batch for c in r1.changes] == [cards[0].id] and r1.changes[0].command == "generate" and r1.changes[0].decision.status == "pending"
    assert [m.kind for m in r1.messages] == ["change", "decision"]
    assert r1.change.description == "shot S01 hood neon" and r3.change.description == "shot S03 bus (held for a reprompt)"
    logged = store.read_changes(p)
    assert logged[cards[2].id].payload["items"][0]["change"] is None and r1.changes[0].id in logged


def test_approve_with_nothing_kept_closes(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    ref = add_media(p, mara)
    before = set(p.bins_by_id)
    ch = execute(store, p, _shots(p, ref), PROPOSE, env=env).change
    r = decide(store, p, ch.id, "approve", None, USER, env=env, items=[{"index": 0, "enabled": False}])
    assert r.change.decision.status == "closed" and set(p.bins_by_id) == before and r.generations == []
    # and a closed proposal reopens as a proposal
    decide(store, p, ch.id, "reopen", None, USER, env=env)
    assert store.read_changes(p)[ch.id].decision.status == "proposed"


def test_item_verdict_refusals(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    ref = add_media(p, mara)
    ch = execute(store, p, _shots(p, ref), PROPOSE, env=env).change
    refused("bad_value", lambda: decide(store, p, ch.id, "approve", None, USER, env=env, items=[{"index": 9}]))
    refused("bad_value", lambda: decide(store, p, ch.id, "approve", None, USER, env=env, items=[{"enabled": False}]))


def test_requested_batch_lands_at_once_as_a_done_card(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    ref = add_media(p, mara)
    p.agent_session.mode = "auto"
    cmd = _shots(p, ref)
    cmd.requested = True
    r = execute(store, p, cmd, _requested(store, p), env=env)
    cards = [c for c in r.changes if c.command == "propose_shots"]
    renders = [c for c in r.changes if c.command == "generate"]
    assert len(cards) == 4 and r.change is cards[0] and all(c.decision is None for c in cards)
    assert r.messages[0].kind == "system" and r.messages[1].kind == "change"  # the batch's line, then each card done
    assert len(r.generations) == 4 and [c.batch for c in renders] == [c.id for c in cards]
    assert all(c.decision is None for c in renders)  # auto: lines, not cards


def test_batch_refusals(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    ref = add_media(p, mara)
    cursor = _requested(store, p)
    refused("propose_required", lambda: execute(store, p, _shots(p, ref), cursor, env=env))
    refused("bad_value", lambda: execute(store, p, ProposeShots(items=[]), PROPOSE, env=env))
    refused("unknown_workflow", lambda: execute(store, p, ProposeShots(items=[ShotItem(name="x", description="y", workflow="nope.json")]), PROPOSE, env=env))
    refused("unknown_id", lambda: execute(store, p, ProposeShots(items=[ShotItem(name="x", description="y", workflow="img.json", references=["m_nope"])]), PROPOSE, env=env))
    refused("too_many_references", lambda: execute(store, p, ProposeShots(items=[ShotItem(name="x", description="y", workflow="img.json", references=[ref, ref, ref])]), PROPOSE, env=env))
    p.agent_session.mode = "plan"
    refused("plan_mode", lambda: execute(store, p, _shots(p, ref), PROPOSE, env=env))


def test_manual_mode_renders_only_through_the_card(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    ref = add_media(p, mara)
    vid = add_bin(p, "Video 1", "vid.json")
    p.agent_session.mode = "manual"
    refused("propose_first", lambda: execute(store, p, Generate(bin=vid), _requested(store, p), env=env))
    # what the user asked for out loud still goes
    assert execute(store, p, Generate(bin=vid, requested=True), _requested(store, p), env=env).generation is not None
    cards = execute(store, p, _shots(p, ref), PROPOSE, env=env).changes
    rs = [decide(store, p, c.id, "approve", None, USER, env=env) for c in cards]
    # the pre-generation card was the ask: the takes land as lines, not cards
    assert [len(r.generations) for r in rs] == [1] * 4 and all(c.decision is None for r in rs for c in r.changes)
    assert [m.kind for m in rs[0].messages] == ["system", "decision"]


def test_existing_bin_target_and_a_failed_child(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    ref = add_media(p, mara)
    vid = add_bin(p, "Video 1", "vid.json")
    p.bins_by_id[vid].prompt = "old"
    cmd = ProposeShots(items=[ShotItem(name="Video 1", bin=vid, description="the same, at four seconds", prompt="new @mara.png", references=[ref], length_s=4)])
    ch = execute(store, p, cmd, PROPOSE, env=env).change
    r = decide(store, p, ch.id, "approve", None, USER, env=env)
    b = p.bins_by_id[vid]
    assert b.prompt == "new @mara.png" and b.length_s == 4 and [x.handle for x in b.references] == ["mara.png"]
    assert r.change.payload["items"][0]["bin_id"] == vid and len(r.generations) == 1
    assert r.changes[0].batch == ch.id


def test_a_retake_card_without_a_prompt_keeps_the_bin_prompt(world):
    """The card's one-liner is display text: approving a retake that names no prompt renders the
    bin's own recipe, it never writes the description over it."""
    store, p, env = world
    vid = add_bin(p, "Video 1", "vid.json")
    p.bins_by_id[vid].prompt = "Dev: Get broker."
    cmd = ProposeShots(items=[ShotItem(name="Video 1", bin=vid, description="re-roll take 46, seed sweep (m_ab12 recipe)")])
    ch = execute(store, p, cmd, PROPOSE, env=env).change
    r = decide(store, p, ch.id, "approve", None, USER, env=env)
    b = p.bins_by_id[vid]
    assert b.prompt == "Dev: Get broker." and b.description == "re-roll take 46, seed sweep (m_ab12 recipe)"
    assert len(r.generations) == 1 and r.generations[0].prompt_typed == "Dev: Get broker."


def test_approved_batch_reject_keeps_the_bins(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    ref = add_media(p, mara)
    ch = execute(store, p, _shots(p, ref), PROPOSE, env=env).change
    decide(store, p, ch.id, "approve", None, USER, env=env)
    n = len(p.bins_by_id)
    r = decide(store, p, ch.id, "reject", "not these", USER, env=env)
    assert r.change.decision.status == "rejected" and len(p.bins_by_id) == n
    decide(store, p, ch.id, "reopen", None, USER, env=env)
    assert store.read_changes(p)[ch.id].decision.status == "pending" and len(p.bins_by_id) == n


def test_close_says_only_seen(world):
    store, p, env = world
    b = add_bin(p, "Image 1", "img.json")
    ch = execute(store, p, SetBinField(bin=b, field="prompt", value="x", card=True), AGENT, env=env).change
    r = decide(store, p, ch.id, "close", "ignored", USER, env=env)
    assert r.change.decision.status == "closed" and r.change.decision.note is None
    assert p.bins_by_id[b].prompt == "x"  # applied stays applied
    last = store.read_messages(p)[-1]
    assert last.kind == "decision" and last.change == ch.id and last.status == "closed" and last.note is None
    refused("bad_transition", lambda: decide(store, p, ch.id, "close", None, USER, env=env))
    # reopen brings the verdict back
    decide(store, p, ch.id, "reopen", None, USER, env=env)
    assert store.read_changes(p)[ch.id].decision.status == "pending"
    # a proposed card closed never runs
    prop = execute(store, p, SetBinField(bin=b, field="prompt", value="y"), PROPOSE, env=env).change
    decide(store, p, prop.id, "close", None, USER, env=env)
    assert p.bins_by_id[b].prompt == "x" and store.read_changes(p)[prop.id].decision.status == "closed"


def test_close_pending_clears_the_live_sessions_cards_with_one_line(world):
    store, p, env = world
    b = add_bin(p, "Image 1", "img.json")
    take = add_take(p, b)
    c1 = execute(store, p, SetBinField(bin=b, field="prompt", value="x", card=True), AGENT, env=env).change
    c2 = execute(store, p, MarkTake(media=take, status="pass", card=True), AGENT, env=env).change
    c3 = execute(store, p, SetBinField(bin=b, field="negative", value="blur"), PROPOSE, env=env).change
    done = execute(store, p, SetBinField(bin=b, field="tags", value=["look"], requested=True), AGENT, env=env).change
    refused("wrong_actor", lambda: close_pending(store, p, AGENT))
    r = close_pending(store, p, USER)
    assert {c.id for c in r.changes} == {c1.id, c2.id, c3.id}
    assert [(m.kind, m.text) for m in r.messages] == [("system", "you: closed 3 cards")]
    logged = store.read_changes(p)
    assert all(logged[c].decision.status == "closed" for c in (c1.id, c2.id, c3.id)) and logged[done.id].decision is None
    assert p.bins_by_id[b].negative == ""  # the proposal never ran
    assert close_pending(store, p, USER).messages == []


def test_old_mode_names_load_as_the_review_modes():
    assert AgentSession(id="as_1", mode="manual_video").mode == "video_review"
    assert AgentSession(id="as_1", mode="manual_all").mode == "image_review"
    assert AgentSession(id="as_1", mode="manual").mode == "manual"
