"""The draft mode: drafts run on their own, the step up to full quality is asked about first,
and the user's answer can be "later" (approved, the render saved held)."""

from types import SimpleNamespace

import pytest

from thm_core import CommandContext, CommandRefused, decide, execute
from thm_core.commands import Generate, ProposeShots, ShotItem, full_quality
from thm_core.commands import Env
from thm_core_fixtures import IMAGE_SIDECAR, add_bin, image_workflow, live_session, make_project, make_store, node, video_workflow, workflows_dir


def _with_draft_switch(wf: dict) -> dict:
    """A workflow with a draft setting of its own: a THM-IsDraft switch (ComfyUI's Switch node)."""
    return {**wf, "99": node("THM-IsDraft", "ComfySwitchNode", {"switch": False, "on_false": ["90", 0], "on_true": ["91", 0]})}


def seeded(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    wfs = {"img.json": image_workflow(2), "vid.json": video_workflow(), "img_d.json": _with_draft_switch(image_workflow(2)), "vid_d.json": _with_draft_switch(video_workflow())}
    env = Env(workflows_dir=workflows_dir(tmp_path, wfs, {"img.thm.toml": IMAGE_SIDECAR}, video=("vid.json", "vid_d.json")), isfile=lambda p: True)
    image = add_bin(p, "Still 1", "img.json")
    video = add_bin(p, "Video 1", "vid.json", length_s=5.0)
    image_d = add_bin(p, "Still 2", "img_d.json")
    video_d = add_bin(p, "Video 2", "vid_d.json", length_s=5.0)
    store.save(p)
    return SimpleNamespace(store=store, project=p, env=env, image=image, video=video, image_d=image_d, video_d=video_d)


def _agent(store, project):
    return CommandContext(actor="agent", session=project.agent_session.id, inbox_cursor=store.message_cursor(project))


def _draft_world(tmp_path):
    w = seeded(tmp_path)
    sess = live_session(w.project)
    sess.mode = "auto"
    sess.drafts = True
    w.store.save(w.project)
    return w


def test_full_quality_is_anything_short_of_every_cheap_setting_the_render_has(tmp_path):
    w = seeded(tmp_path)
    p, env = w.project, w.env
    wf = {k: p.bins_by_id[getattr(w, k)].workflow for k in ("image", "video", "image_d", "video_d")}
    assert env.capacity(wf["video_d"]).supports_draft and not env.capacity(wf["video"]).supports_draft
    # a video is a draft at quarter size
    assert full_quality(p, wf["video"], {}, env) is True
    assert full_quality(p, wf["video"], {"quarter": True}, env) is False
    # and, where the workflow has a draft setting, with it on too
    assert full_quality(p, wf["video_d"], {"quarter": True}, env) is True
    assert full_quality(p, wf["video_d"], {"draft": True}, env) is True
    assert full_quality(p, wf["video_d"], {"quarter": True, "draft": True}, env) is False
    # a still has one tier unless its workflow has a draft setting
    assert full_quality(p, wf["image"], {}, env) is False
    assert full_quality(p, wf["image_d"], {}, env) is True
    assert full_quality(p, wf["image_d"], {"draft": True}, env) is False


def test_a_draft_session_runs_drafts_and_asks_before_full_quality(tmp_path):
    w = _draft_world(tmp_path)
    store, p, env = w.store, w.project, w.env

    # a still and a quarter-size video go straight through, silent like auto
    res = execute(store, p, Generate(bin=w.image), _agent(store, p), env=env)
    assert res.generation is not None and res.change.decision is None
    res = execute(store, p, Generate(bin=w.video, params={"quarter": True}), _agent(store, p), env=env)
    assert res.generation.status == "queued" and res.change.decision is None

    # the full-size video is the ask
    with pytest.raises(CommandRefused) as exc:
        execute(store, p, Generate(bin=w.video), _agent(store, p), env=env)
    assert exc.value.code == "full_quality_ask"
    # unless the user asked for it out loud
    assert execute(store, p, Generate(bin=w.video, requested=True), _agent(store, p), env=env).generation is not None

    # a workflow with a draft setting owes both: quarter alone is still the ask
    with pytest.raises(CommandRefused) as exc:
        execute(store, p, Generate(bin=w.video_d, params={"quarter": True}), _agent(store, p), env=env)
    assert exc.value.code == "full_quality_ask"
    res = execute(store, p, Generate(bin=w.video_d, params={"quarter": True, "draft": True}), _agent(store, p), env=env)
    assert res.generation.params["draft"] is True and res.generation.params["quarter"] is True
    with pytest.raises(CommandRefused):
        execute(store, p, Generate(bin=w.image_d), _agent(store, p), env=env)
    assert execute(store, p, Generate(bin=w.image_d, params={"draft": True}), _agent(store, p), env=env).generation is not None


def test_later_approves_the_shot_and_saves_the_render_held(tmp_path):
    w = _draft_world(tmp_path)
    store, p, env = w.store, w.project, w.env
    user = CommandContext(actor="user")

    def propose():
        ctx = _agent(store, p)
        ctx.propose = True
        return execute(store, p, ProposeShots(items=[ShotItem(bin=w.video, description="the full-size pass")]), ctx, env=env).change

    card = propose()
    res = decide(store, p, card.id, "approve", None, user, env=env, hold=True)
    assert res.change.decision.status == "approved" and res.change.payload["hold"] is True
    assert [g.status for g in res.generations] == ["held"]

    # a plain yes queues it
    card = propose()
    res = decide(store, p, card.id, "approve", None, user, env=env)
    assert [g.status for g in res.generations] == ["queued"] and res.change.payload["hold"] is False

    # hold is the user's answer, never the agent's to send
    with pytest.raises(CommandRefused) as exc:
        ctx = _agent(store, p)
        ctx.propose = True
        execute(store, p, ProposeShots(items=[ShotItem(bin=w.video, description="x")], hold=True, requested=True), _agent(store, p), env=env)
    assert exc.value.code == "wrong_actor"


def test_a_retake_card_replaces_the_bins_references_instead_of_stacking_them(tmp_path):
    """img.json has two image slots. A bin that already holds two references, proposed again
    with two, used to be refused on approval (four over a two-slot workflow)."""
    from thm_core_fixtures import add_media, bind

    w = seeded(tmp_path)
    store, p, env = w.store, w.project, w.env
    refs = add_bin(p, "Mara", "img.json")
    a, b, c = (add_media(p, refs, path=f"/m/{n}.png") for n in "abc")
    bind(p, w.image, a)
    bind(p, w.image, b)
    live_session(p).mode = "manual"
    store.save(p)

    ctx = _agent(store, p)
    ctx.propose = True
    card = execute(store, p, ProposeShots(items=[ShotItem(bin=w.image, description="retake", references=[b, c])]), ctx, env=env).change
    res = decide(store, p, card.id, "approve", None, CommandContext(actor="user"), env=env)
    assert res.change.payload["items"][0]["error"] is None and len(res.generations) == 1
    assert [r.media for r in p.bins_by_id[w.image].references] == [b, c]

    # no references on the card: the bin keeps the ones it has
    ctx = _agent(store, p)
    ctx.propose = True
    card = execute(store, p, ProposeShots(items=[ShotItem(bin=w.image, description="again")]), ctx, env=env).change
    decide(store, p, card.id, "approve", None, CommandContext(actor="user"), env=env)
    assert [r.media for r in p.bins_by_id[w.image].references] == [b, c]


def test_a_take_the_agent_fails_closes_its_own_pending_card(tmp_path):
    """In a review mode a landed take is a card for the user. When the agent's own QC marks
    it fail, "is this image done?" is the wrong question: the card closes by itself."""
    from thm_core.commands import MarkTake
    from thm_core_fixtures import add_media

    w = seeded(tmp_path)
    store, p, env = w.store, w.project, w.env
    live_session(p).mode = "image_review"
    store.save(p)
    res = execute(store, p, Generate(bin=w.image), _agent(store, p), env=env)
    card, gen = res.change, res.generation
    assert card.decision is not None and card.decision.status == "pending"
    take = add_media(p, w.image, origin={"type": "generation", "generation": gen.id, "bin": w.image})

    res = execute(store, p, MarkTake(media=take, status="fail", note="third arm"), _agent(store, p), env=env)
    assert [c.id for c in res.changes] == [card.id]
    closed = store.read_changes(p)[card.id].decision
    assert closed.status == "closed" and closed.note == "the agent failed this take: third arm"
    # the closed card says why, so the agent's fail must carry one
    from thm_core.commands import CommandRefused
    import pytest
    with pytest.raises(CommandRefused):
        execute(store, p, MarkTake(media=take, status="fail", note="  "), _agent(store, p), env=env)
    # a pass leaves the card for the user's verdict
    res2 = execute(store, p, Generate(bin=w.image), _agent(store, p), env=env)
    take2 = add_media(p, w.image, origin={"type": "generation", "generation": res2.generation.id, "bin": w.image})
    execute(store, p, MarkTake(media=take2, status="pass"), _agent(store, p), env=env)
    assert store.read_changes(p)[res2.change.id].decision.status == "pending"


def test_a_shot_names_the_reference_it_waits_on_and_reads_ready_once_it_exists(tmp_path):
    """The card carries the dependency: "waits on a French over from car coverage"; once that
    bin leads with a take the same card reads ready, without the agent touching it."""
    from thm_core.commands import ShotNeed
    from thm_core_fixtures import add_media
    from thm_server.derive import shots_out

    w = seeded(tmp_path)
    store, p, env = w.store, w.project, w.env
    live_session(p).mode = "manual"
    store.save(p)
    ctx = _agent(store, p)
    ctx.propose = True
    item = ShotItem(bin=w.image, description="French over onto Sally", needs=[ShotNeed(bin_name="car coverage", what="French over onto Sally")])
    card = execute(store, p, ProposeShots(items=[item]), ctx, env=env).change
    need = shots_out(p, card, env)[0].needs[0]
    assert (need.ready, need.bin, need.bin_name, need.what) == (False, None, "car coverage", "French over onto Sally")

    coverage = add_bin(p, "car coverage", "img.json")
    assert shots_out(p, card, env)[0].needs[0].ready is False  # the bin exists but holds nothing yet
    take = add_media(p, coverage)
    need = shots_out(p, card, env)[0].needs[0]
    assert (need.ready, need.bin, need.media) == (True, coverage, take)


def test_a_stored_draft_mode_reads_as_auto_with_drafts():
    from thm_core.commands import SetSessionMode
    from thm_core.models import AgentSession

    old = AgentSession(id="s_1", mode="draft")
    assert old.mode == "auto" and old.drafts is True
    cmd = SetSessionMode(mode="draft")
    assert cmd.mode is None and cmd.drafts is True


def test_an_edited_workflow_is_scanned_again(tmp_path):
    """A workflow saved over while the server runs (a THM-IsDraft switch added in ComfyUI) shows
    its new capability on the next look, not after a restart."""
    import json
    import os
    import time

    w = seeded(tmp_path)
    env = w.env
    assert not env.capacity("vid.json").supports_draft
    path = env.workflows_dir.parent / "workflows_video" / "vid.json"
    assert path.is_file()
    time.sleep(0.01)
    path.write_text(json.dumps(_with_draft_switch(video_workflow())), encoding="utf-8")
    os.utime(path, None)
    assert env.capacity("vid.json").supports_draft


def test_draft_or_quarter_on_a_workflow_that_cannot_is_dropped_with_a_warning(tmp_path):
    """Never a silent full-quality render under a draft label: a cheap setting the workflow
    lacks (draft with no THM-IsDraft, quarter with no canvas) is dropped from the record with a
    warning, and the render runs at the one tier it has -- a drafts-first agent asks for every
    cheap setting by rule, and the workflow decides which exist (the klein case)."""
    w = seeded(tmp_path)
    store, p, env = w.store, w.project, w.env
    user = CommandContext(actor="user")
    res = execute(store, p, Generate(bin=w.video, params={"draft": True, "quarter": True}), user, env=env)
    assert "draft" not in res.generation.params and res.generation.params.get("quarter") is True
    assert [w_["kind"] for w_ in res.warnings] == ["draft_unsupported"] and "dropped" in res.warnings[0]["detail"]
    assert any(w_["kind"] == "draft_unsupported" for w_ in res.generation.warnings)
    assert execute(store, p, Generate(bin=w.video_d, params={"draft": True}), user, env=env).generation.params.get("draft") is True
    # a still with no draft setting is one tier: draft dropped, the render runs
    res = execute(store, p, Generate(bin=w.image, params={"draft": True}), user, env=env)
    assert res.generation is not None and "draft" not in res.generation.params
    # a workflow with no width/height node (a partner API node, say) has no quarter size
    bare = {"1": node("THM-Prompt", "CLIPTextEncode", {"text": ""}), "2": node("THM-PartnerNode", "KlingNode", {"prompt": ""})}
    (env.workflows_dir.parent / "workflows_video" / "api.json").write_text(__import__("json").dumps(bare), encoding="utf-8")
    api = add_bin(p, "Api", "api.json", length_s=5.0)
    assert not env.capacity("api.json").supports_size and env.capacity("vid.json").supports_size
    res = execute(store, p, Generate(bin=api, params={"quarter": True}), user, env=env)
    assert "quarter" not in res.generation.params and [w_["kind"] for w_ in res.warnings] == ["quarter_unsupported"]


def test_a_video_with_no_cheap_tier_is_full_quality_and_a_drafts_session_asks(tmp_path):
    """A partner API video (no canvas, no draft switch) is a spend every time: ``full_quality``
    says so, and a drafts-first session refuses the bare render with ``full_quality_ask`` --
    the hole the MiniMax H3 re-render went through."""
    w = _draft_world(tmp_path)
    store, p, env = w.store, w.project, w.env
    bare = {"1": node("THM-Prompt", "CLIPTextEncode", {"text": ""}), "2": node("THM-PartnerNode", "KlingNode", {"prompt": ""})}
    (env.workflows_dir.parent / "workflows_video" / "api.json").write_text(__import__("json").dumps(bare), encoding="utf-8")
    api = add_bin(p, "Api", "api.json", length_s=5.0)
    store.save(p)
    assert full_quality(p, "api.json", {}, env) is True
    with pytest.raises(CommandRefused) as exc:
        execute(store, p, Generate(bin=api), _agent(store, p), env=env)
    assert exc.value.code == "full_quality_ask"
    # the user's word lets it through, as for any full-quality render
    assert execute(store, p, Generate(bin=api, requested=True), _agent(store, p), env=env).generation is not None
