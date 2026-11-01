"""thm_core: the session mode decides what an agent change owes -- auto lands silent, the
manual modes owe a card per render they ask about, plan only proposes the brief -- and
set_bin_field warns about unbound handles."""

import pytest

from thm_core import CommandContext, CommandRefused, decide, execute
from thm_core.commands import ConnectSession, Generate, PostMessage, SetBinField, SetProjectSettings, SetSessionMode, StartSession
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
    # and the session stops there: the next video waits for the verdict; a still does not
    refused("verdict_owed", lambda: _gen(store, p, env, vid))
    assert _gen(store, p, env, img).change.decision is None
    # requested still lands anything done, verdict or no verdict; card still asks about anything
    r = _gen(store, p, env, vid, requested=True)
    assert r.change.decision is None and [m.kind for m in r.messages] == ["change"]
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
    assert [(m.kind, m.text) for m in r.messages] == [("system", "session mode video_review")]
    r = execute(store, p, SetSessionMode(mode="auto", requested=True), AGENT, env=env)
    assert p.agent_session.mode == "auto" and r.change.decision is None
    assert [(m.kind, m.text) for m in r.messages] == [("system", "session mode auto")]


def test_plan_mode_gate_and_brief_approval(world):
    store, p, env = world
    b = add_bin(p, "Image 1", "img.json")
    _live(p, "plan")
    refused("plan_mode", lambda: execute(store, p, SetBinField(bin=b, field="prompt", value="x"), AGENT, env=env))
    execute(store, p, PostMessage(text="reading the brief", kind="agent"), AGENT, env=env)
    refused("plan_mode", lambda: execute(store, p, SetProjectSettings(brief="revised"), AGENT, env=env))
    propose = CommandContext(actor="agent", session=p.agent_session.id, propose=True, now="2026-09-06T10:00:00Z")
    first = execute(store, p, SetProjectSettings(brief="revised brief: one bin"), propose, env=env)
    assert first.change.decision.status == "proposed" and p.brief != "revised brief: one bin"
    # a chat reply is not a verdict: the agent cannot talk its way out of plan, only the user's
    # hand on the panel changes the mode before an approval
    refused("plan_mode", lambda: execute(store, p, SetSessionMode(mode="auto", requested=True), AGENT, env=env))
    execute(store, p, SetSessionMode(mode="plan"), USER, env=env)
    # the answer to a reply is a revised proposal; the earlier one leaves the table
    r = execute(store, p, SetProjectSettings(brief="revised brief: two bins"), propose, env=env)
    assert r.change.decision.status == "proposed"
    changes = store.read_changes(p)
    assert changes[first.change.id].decision.status == "superseded"
    assert [(m.kind, m.status) for m in r.messages] == [("decision", "superseded"), ("change", None)]
    refused("bad_transition", lambda: decide(store, p, first.change.id, "approve", None, USER, env=env))

    res = decide(store, p, r.change.id, "approve", "go", USER, env=env)
    assert p.brief == "revised brief: two bins" and p.agent_session.mode == "auto"
    assert res.change.decision.status == "approved"
    assert any(m.kind == "system" and m.text == "brief approved, session mode auto" for m in res.messages)
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
    r = execute(store, p, SetBinField(bin=b, field="prompt", value=".ghost at the rail"), USER, env=env)
    assert {(w["kind"], w["handle"]) for w in r.warnings} == {("unknown_token", "ghost"), ("unused_handle", "mara")}
    r = execute(store, p, SetBinField(bin=b, field="prompt", value=".mara at the rail"), USER, env=env)
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
    r = execute(store, p, SetProjectSettings(private=False), USER, env=env)
    assert r.change.description == "project settings - not private" and p.agent_session.status == "ended"
