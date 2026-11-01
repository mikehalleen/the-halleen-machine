"""One card per take: a mark the agent asks to be a card, on a take whose render card is still
waiting for the user's verdict, lands quietly instead of opening a second card; and the take a
card is approved for is its bin's current one at once."""

from thm_core import CommandContext, execute
from thm_core.commands import MarkTake, decide
from thm_core.models import Change, ChangeTarget, Decision
from thm_core_fixtures import USER, add_bin, add_take, image_workflow, live_session, make_env, make_project, make_store


def _pending(store, project):
    return [c for c in store.read_changes(project).values() if c.decision is not None and c.decision.status == "pending"]


def test_marking_a_take_with_a_waiting_card_adds_no_card(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path, {"img.json": image_workflow(2)})
    live_session(p).mode = "image_review"
    s = add_bin(p, "the smart kids", "img.json")
    take = add_take(p, s)  # made by generation g_x
    store.append_change(p, Change(
        id="c_render", ts="2026-09-06T10:00:00Z", actor="agent", command="generate", target=ChangeTarget(type="bin", id=s),
        description="the smart kids - generate", decision=Decision(status="pending"), generation="g_x",
    ))
    agent = CommandContext(actor="agent", now="2026-09-06T10:01:00Z")
    r = execute(store, p, MarkTake(media=take, status="in_progress", note="eyeline holds", card=True), agent, env=env)
    assert [c.id for c in _pending(store, p)] == ["c_render"]
    assert any(w["kind"] == "card_exists" for w in r.warnings)
    assert p.media_by_id[take].qc.status == "in_progress"

    # with no card waiting, the asked-for card is made
    store.append_change(p, store.read_changes(p)["c_render"].model_copy(update={"decision": Decision(status="approved")}))
    execute(store, p, MarkTake(media=take, status="pass", note="done", card=True), agent, env=env)
    assert [c.command for c in _pending(store, p)] == ["mark_take"]
    execute(store, p, MarkTake(media=take, status="pass", note="still done", card=True), USER, env=env)


def test_approving_a_take_card_makes_it_current(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path, {"img.json": image_workflow(2)})
    live_session(p).mode = "image_review"
    s = add_bin(p, "the smart kids", "img.json")
    first = add_take(p, s)
    take = add_take(p, s)
    p.bins_by_id[s].selected = first
    store.append_change(p, Change(
        id="c_mark", ts="2026-09-06T10:00:00Z", actor="agent", command="mark_take", target=ChangeTarget(type="media", id=take),
        description="the smart kids - take 2 pass", decision=Decision(status="pending"),
    ))
    r = decide(store, p, "c_mark", "approve", None, USER, env=env)
    assert p.bins_by_id[s].selected == take
    assert [c.command for c in r.changes] == ["set_bin_field"]
    # already current: approving again changes nothing more
    assert not decide(store, p, "c_mark", "approve", None, USER, env=env).changes
