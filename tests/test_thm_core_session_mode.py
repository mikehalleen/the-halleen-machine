"""thm_core: the session mode decides what an agent change owes -- auto lands silent, the
manual modes owe a card per render they ask about, plan only proposes the brief -- and
set_bin_field warns about unbound handles."""

import pytest

from thm_core import CommandContext, CommandRefused, decide, execute
from thm_core.commands import ConnectSession, EndSession, Generate, PostMessage, SetBinField, SetProjectSettings, SetSessionMode, StartSession
from thm_core_fixtures import (
    AGENT,
    IMAGE_SIDECAR,
    USER,
    add_bin,
    add_media,
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
    env = make_env(tmp_path, {"img.json": image_workflow(2), "vid.json": video_workflow()}, {"img.thm.toml": IMAGE_SIDECAR})
    return store, p, env


def _live(p, mode):
    sess = live_session(p)
    sess.mode = mode
    return sess


def refused(code, fn):
    with pytest.raises(CommandRefused) as exc:
        fn()
    assert exc.value.code == code, f"expected {code}, got {exc.value.code}: {exc.value.detail}"
    return exc.value


def test_start_session_records_the_mode_and_defaults_to_auto(world):
    store, p, env = world
    r = execute(store, p, StartSession(), USER, env=env)
    assert p.agent_session.mode == "auto" and r.change.description == "session started"
    p.agent_session = None
    r = execute(store, p, StartSession(mode="plan"), USER, env=env)
    assert p.agent_session.mode == "plan" and r.change.description == "session started (plan)"


def test_auto_mode_lands_done_with_a_line_and_never_a_card(world):
    store, p, env = world
    b = add_bin(p, "Image 1", "img.json")
    _live(p, "auto")
    r = execute(store, p, SetBinField(bin=b, field="prompt", value="a rail at dawn", reason="the brief wants dawn"), AGENT, env=env)
    assert r.change is not None and r.change.decision is None
    # no card, but never silent: the experiment shows as a line with the reason, like "you: ..." does for the user
    assert [(m.kind, m.text, m.change) for m in r.messages] == [("system", "agent: Image 1 - prompt edited -- the brief wants dawn", r.change.id)]
    assert p.bins_by_id[b].prompt == "a rail at dawn"
    # card is the one exception: the finished candidate is presented as a card, even in auto
    r = execute(store, p, SetBinField(bin=b, field="prompt", value="a rail at dusk", card=True), AGENT, env=env)
    assert r.change.decision.status == "pending" and [m.kind for m in r.messages] == ["change"]
    # quiet is redundant in auto and still fine
    r = execute(store, p, SetBinField(bin=b, field="negative", value="blur", quiet=True), AGENT, env=env)
    assert r.change.decision is None and r.messages == []
    # quiet wins over card: no line, and no verdict owed either -- a pending decision the feed
    # cannot show is a count the user cannot clear
    r = execute(store, p, SetBinField(bin=b, field="negative", value="grain", quiet=True, card=True), AGENT, env=env)
    assert r.change.decision is None and r.messages == []
    _live(p, "image_review")
    r = execute(store, p, SetBinField(bin=b, field="negative", value="noise", quiet=True, card=True), AGENT, env=env)
    assert r.change.decision is None and r.messages == []


def _gen(store, p, env, b, **kw):
    ctx = CommandContext(actor="agent", inbox_cursor=store.message_cursor(p), now="2026-09-06T10:00:00Z")
    return execute(store, p, Generate(bin=b, reason="one change: the light", **kw), ctx, env=env)


def test_video_review_lands_everything_but_a_video_render(world):
    store, p, env = world
    img = add_bin(p, "Image 1", "img.json")
    vid = add_bin(p, "Video 1", "vid.json", length_s=5)
    _live(p, "video_review")
    # a prompt edit is the agent's own work: no card, a feed line with the reason, as in auto
    r = execute(store, p, SetBinField(bin=img, field="prompt", value="x", reason="the brief wants dawn"), AGENT, env=env)
    assert r.change.decision is None
    assert [(m.kind, m.text) for m in r.messages] == [("system", "agent: Image 1 - prompt edited -- the brief wants dawn")]
    # so is a still
    r = _gen(store, p, env, img)
    assert r.change.decision is None and r.generation is not None and [m.kind for m in r.messages] == ["system"]
    # a video render is the card
    r = _gen(store, p, env, vid)
    card = r.change
    assert card.decision.status == "pending" and r.generation is not None and [m.kind for m in r.messages] == ["change"]
    # while it is still rendering there is nothing to judge, so nothing waits on it yet
    assert _gen(store, p, env, img).generation is not None
    # once its take is in: nothing *else* waits on that verdict -- a still, a video in another
    # bin, all land -- only this bin's next take (it would answer the card for the user) and a
    # render that binds the pending take are held (test below)
    _landed(store, p, card.generation, add_media(p, vid, type="video", origin={"type": "generation", "generation": card.generation, "bin": vid}))
    refused("verdict_owed", lambda: _gen(store, p, env, vid))
    assert _gen(store, p, env, img).change.decision is None
    vid2 = add_bin(p, "Video 2", "vid.json", length_s=5)
    assert _gen(store, p, env, vid2).change.decision.status == "pending"
    # requested waves nothing through here: the card is the review of the take, not of the spend
    vid3 = add_bin(p, "Video 3", "vid.json", length_s=5)
    r = _gen(store, p, env, vid3, requested=True)
    assert r.change.decision.status == "pending" and [m.kind for m in r.messages] == ["change"]
    # the verdict releases the bin's next take
    decide(store, p, card.id, "approve", None, USER, env=env)
    assert _gen(store, p, env, vid).change.decision.status == "pending"
    r = execute(store, p, SetBinField(bin=img, field="prompt", value="y", card=True), AGENT, env=env)
    assert r.change.decision.status == "pending" and [m.kind for m in r.messages] == ["change"]


def test_image_review_makes_every_render_a_card(world):
    store, p, env = world
    img = add_bin(p, "Image 1", "img.json")
    vid = add_bin(p, "Video 1", "vid.json", length_s=5)
    _live(p, "image_review")
    r = execute(store, p, SetBinField(bin=img, field="prompt", value="x"), AGENT, env=env)
    assert r.change.decision is None and [m.kind for m in r.messages] == ["system"]
    r = _gen(store, p, env, img)
    assert r.change.decision.status == "pending" and [m.kind for m in r.messages] == ["change"]
    r = _gen(store, p, env, vid)
    assert r.change.decision.status == "pending" and [m.kind for m in r.messages] == ["change"]


def test_set_session_mode_is_a_system_line_for_either_actor(world):
    store, p, env = world
    refused("no_session", lambda: execute(store, p, SetSessionMode(mode="video_review"), USER, env=env))
    _live(p, "auto")
    r = execute(store, p, SetSessionMode(mode="video_review"), USER, env=env)
    assert p.agent_session.mode == "video_review" and r.change.decision is None
    # the note names the mode and says what it means
    assert [(m.kind, m.text) for m in r.messages] == [
        ("system", "session mode video cards -- generations start on their own; the agent checks stills itself and every video take comes back as a card")
    ]
    r = execute(store, p, SetSessionMode(mode="auto", requested=True), AGENT, env=env)
    assert p.agent_session.mode == "auto" and r.change.decision is None
    assert [m.kind for m in r.messages] == ["system"] and r.messages[0].text.startswith("session mode full auto -- the agent works toward the goal")


def test_plan_mode_gate_and_brief_approval(world):
    store, p, env = world
    b = add_bin(p, "Image 1", "img.json")
    _live(p, "plan")
    refused("plan_mode", lambda: execute(store, p, SetBinField(bin=b, field="prompt", value="x"), AGENT, env=env))
    execute(store, p, PostMessage(text="reading the brief", kind="agent"), CommandContext(actor="agent", inbox_cursor=store.message_cursor(p), now="2026-09-06T10:00:00Z"), env=env)
    refused("plan_mode", lambda: execute(store, p, SetProjectSettings(brief="revised"), AGENT, env=env))
    propose = CommandContext(actor="agent", session=p.agent_session.id, propose=True, now="2026-09-06T10:00:00Z")
    first = execute(store, p, SetProjectSettings(brief="revised brief: one bin"), propose, env=env)
    assert first.change.decision.status == "proposed" and p.brief != "revised brief: one bin"
    # a chat reply is not a verdict: the agent cannot talk its way out of plan, only the user's
    # hand on the panel changes the mode before an approval
    refused("plan_mode", lambda: execute(store, p, SetSessionMode(mode="auto", requested=True), AGENT, env=env))
    execute(store, p, SetSessionMode(mode="plan"), USER, env=env)
    # every proposal is its own card: a second brief does not take the first off the table
    r = execute(store, p, SetProjectSettings(brief="revised brief: two bins"), propose, env=env)
    assert r.change.decision.status == "proposed"
    changes = store.read_changes(p)
    assert changes[first.change.id].decision.status == "proposed"
    assert [(m.kind, m.status) for m in r.messages] == [("change", None)]
    # a note on a card is a reply, not a go: nothing written, the agent answers with a revised card
    rep = decide(store, p, first.change.id, "approve", "closer to the first one", USER, env=env)
    assert rep.change.decision.status == "replied" and p.brief != "revised brief: one bin"

    res = decide(store, p, r.change.id, "approve", None, USER, env=env)
    assert p.brief == "revised brief: two bins" and p.agent_session.mode == "plan"
    assert res.change.decision.status == "approved"
    assert not any(m.kind == "system" and "session mode" in (m.text or "") for m in res.messages)
    # still planning: the agent's edits stay refused until the user changes the mode in the panel
    refused("plan_mode", lambda: execute(store, p, SetBinField(bin=b, field="prompt", value="x"), AGENT, env=env))
    execute(store, p, SetSessionMode(mode="auto"), USER, env=env)
    r = execute(store, p, SetBinField(bin=b, field="prompt", value="x"), AGENT, env=env)
    assert r.change.decision is None


def test_set_project_settings_brief_alone_is_a_change(world):
    store, p, env = world
    refused("bad_value", lambda: execute(store, p, SetProjectSettings(brief="  "), USER, env=env))
    r = execute(store, p, SetProjectSettings(brief="the goal"), USER, env=env)
    assert p.brief == "the goal" and r.change.description == "project settings - brief revised"


def test_set_bin_field_prompt_warns_about_tokens(world):
    store, p, env = world
    mara = add_bin(p, "Mara", "img.json")
    face = add_media(p, mara)
    b = add_bin(p, "Image 1", "img.json")
    bind(p, b, face)
    r = execute(store, p, SetBinField(bin=b, field="prompt", value="@ghost.png at the rail"), USER, env=env)
    assert {(w["kind"], w["handle"]) for w in r.warnings} == {("unknown_token", "ghost.png"), ("unused_handle", "mara.png")}
    r = execute(store, p, SetBinField(bin=b, field="prompt", value="@mara.png at the rail"), USER, env=env)
    assert r.warnings == []


def test_private_project_refuses_a_session(world):
    store, p, env = world
    p.private = True
    refused("private_project", lambda: execute(store, p, StartSession(), USER, env=env))
    assert p.agent_session is None
    p.private = False
    execute(store, p, StartSession(), USER, env=env)
    p.private = True
    refused("private_project", lambda: execute(store, p, ConnectSession(harness="claude-code"), AGENT, env=env))
    assert p.agent_session.status == "waiting"
    # going private ends whatever session was waiting or live, and says so
    p.private = False
    r = execute(store, p, SetProjectSettings(private=True), USER, env=env)
    assert p.private and p.agent_session.status == "ended" and r.change.description == "project settings - private (session ended)"
    ended = p.agent_session.id
    # back to public: the session that going private ended reopens, waiting, so the agent's
    # watch finds the project again without being told
    r = execute(store, p, SetProjectSettings(private=False), USER, env=env)
    assert r.change.description == "project settings - not private (session reopened)"
    assert p.agent_session.status == "waiting" and p.agent_session.id != ended
    # a session the user ended stays ended through a private round trip of its own
    execute(store, p, EndSession(), USER, env=env)
    execute(store, p, SetProjectSettings(private=True), USER, env=env)
    r = execute(store, p, SetProjectSettings(private=False), USER, env=env)
    assert r.change.description == "project settings - not private" and p.agent_session.status == "ended"


def _landed(store, p, gen_id, media_id):
    """The render's take is in: its generation record names it, as a landing writes it."""
    gen = store.read_generation(p, gen_id)
    store.append_generation(p, gen.model_copy(update={"status": "done", "outputs": [media_id]}))


def test_a_reply_releases_the_verdict_gate(world):
    """A note on a take card is the instruction: the agent may render again to act on it."""
    store, p, env = world
    v = add_bin(p, "Video 1", "vid.json", length_s=5)
    _live(p, "video_review")
    agent = lambda: CommandContext(actor="agent", inbox_cursor=store.message_cursor(p), now="2026-09-06T10:00:00Z")
    ch = execute(store, p, Generate(bin=v), agent(), env=env).change
    assert ch.decision.status == "pending"
    # a second bin renders *from* that pending take: it waits; a reply on the card releases it
    take = add_media(p, v, type="video", origin={"type": "generation", "generation": ch.generation, "bin": v})
    _landed(store, p, ch.generation, take)
    w = add_bin(p, "Video 2", "vid.json", length_s=5)
    bind(p, w, take)
    refused("verdict_owed", lambda: execute(store, p, Generate(bin=w), agent(), env=env))
    refused("verdict_owed", lambda: execute(store, p, Generate(bin=v), agent(), env=env))  # and the bin's own next take
    decide(store, p, ch.id, "reply", "tighter on her face", USER, env=env)
    assert execute(store, p, Generate(bin=w), agent(), env=env).generation is not None
    assert execute(store, p, Generate(bin=v), agent(), env=env).generation is not None


def test_a_render_with_no_take_never_blocks_and_a_requested_one_passes(world):
    """A card owes a verdict only once its take is in: a cancelled (or failed, or still running)
    render blocks nothing -- the app shows no card for it. And a render the user asked for out
    loud passes the gate: the request is their answer."""
    store, p, env = world
    v = add_bin(p, "Video 1", "vid.json", length_s=5)
    _live(p, "video_review")
    agent = lambda: CommandContext(actor="agent", inbox_cursor=store.message_cursor(p), now="2026-09-06T10:00:00Z")
    ch = execute(store, p, Generate(bin=v), agent(), env=env).change
    gen = store.read_generation(p, ch.generation)
    store.append_generation(p, gen.model_copy(update={"status": "cancelled"}))
    assert ch.decision.status == "pending"  # a record stuck from before the cancel closed its card
    # the user cancelled it: nothing to judge, nothing owed, the next take runs
    assert execute(store, p, Generate(bin=v), agent(), env=env).generation is not None
    # a take that is in does owe; a requested render goes through anyway
    ch2 = store.read_changes(p)
    second = [c for c in ch2.values() if c.command == "generate" and c.generation != ch.generation][-1]
    take = add_media(p, v, type="video", origin={"type": "generation", "generation": second.generation, "bin": v})
    _landed(store, p, second.generation, take)
    refused("verdict_owed", lambda: execute(store, p, Generate(bin=v), agent(), env=env))
    assert execute(store, p, Generate(bin=v, requested=True), agent(), env=env).generation is not None


def test_cancelling_a_render_closes_its_card(world):
    from thm_core.commands import close_render_card

    store, p, env = world
    v = add_bin(p, "Video 1", "vid.json", length_s=5)
    _live(p, "video_review")
    ctx = CommandContext(actor="agent", inbox_cursor=store.message_cursor(p), now="2026-09-06T10:00:00Z")
    ch = execute(store, p, Generate(bin=v), ctx, env=env).change
    closed = close_render_card(store, p, ch.generation, "generation cancelled")
    assert closed is not None and store.read_changes(p)[ch.id].decision.status == "closed"
    assert close_render_card(store, p, ch.generation, "again") is None  # nothing owed twice
