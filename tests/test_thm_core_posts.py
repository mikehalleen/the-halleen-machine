"""Posts: a video with its words, drafted for YouTube and held unpublished."""

import pytest

from thm_core import CommandRefused, decide, execute
from thm_core.commands import CreatePost, DeleteBin, DeleteMedia, DeletePost, SetPostField, check_no_raw_ids, post_label
from thm_core.models import YT_DESCRIPTION_MAX_BYTES, YT_TAGS_MAX_CHARS, YT_TITLE_MAX
from thm_core_fixtures import AGENT, USER, add_bin, add_media, add_take, live_session, make_env, make_project, make_store


@pytest.fixture
def world(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path)
    exports = add_bin(p, "Rough cut Exports", workflow="")
    video = add_take(p, exports, type="video")
    p.bins_by_id[exports].selected = video
    stills = add_bin(p, "Mara")
    still = add_media(p, stills)
    # the project is connected to a channel the server knows (the Env hook stands in for the server)
    p.youtube_channel = CHANNEL["id"]
    env.youtube_channel = lambda cid: dict(CHANNEL) if cid == CHANNEL["id"] else None
    return store, p, env, exports, video, stills, still


CHANNEL = {"id": "UCabc", "title": "Halleen", "handle": "@halleen"}


def refused(code, fn):
    with pytest.raises(CommandRefused) as exc:
        fn()
    assert exc.value.code == code, f"expected {code}, got {exc.value.code}: {exc.value.detail}"
    return exc.value


def test_create_drafts_the_current_video_private_and_unsent(world):
    store, p, env, exports, video, stills, still = world
    r = execute(store, p, CreatePost(bin=exports, title="The Halleen Machine cuts a sequence"), USER, env=env)
    post = p.posts[0]
    assert r.post == post.id and post.id.startswith("pub_") and r.bin is None
    assert post.bin == exports and post.media == video and post.platform == "youtube" and post.channel == "UCabc"
    assert post.status == "draft" and post.privacy == "private" and post.synthetic is True and post.made_for_kids is False
    assert post.remote_id is None and post.studio_url is None and post.by == "user"
    assert r.change.description == 'drafted a youtube post for Rough cut Exports take 1: "The Halleen Machine cuts a sequence"'
    # without a title the line says so; the label falls back to the take
    r2 = execute(store, p, CreatePost(bin=exports), USER, env=env)
    assert r2.change.description == "drafted a youtube post for Rough cut Exports take 1 (no title yet)"
    assert post_label(p, p.posts[1]) == "the youtube post for Rough cut Exports take 1"
    # a newer take does not swap in: the post keeps its video
    newer = add_take(p, exports, type="video")
    p.bins_by_id[exports].selected = newer
    assert p.posts[0].media == video


def test_create_refusals(world):
    store, p, env, exports, video, stills, still = world
    refused("unknown_id", lambda: execute(store, p, CreatePost(bin="b_nope"), USER, env=env))
    refused("not_video", lambda: execute(store, p, CreatePost(bin=stills), USER, env=env))
    empty = add_bin(p, "Empty")
    refused("no_take", lambda: execute(store, p, CreatePost(bin=empty), USER, env=env))
    refused("not_in_bin", lambda: execute(store, p, CreatePost(bin=exports, media=still), USER, env=env))
    refused("not_video", lambda: execute(store, p, CreatePost(bin=stills, media=still), USER, env=env))
    refused("bad_value", lambda: execute(store, p, CreatePost(bin=exports, title="x" * (YT_TITLE_MAX + 1)), USER, env=env))
    refused("bad_value", lambda: execute(store, p, CreatePost(bin=exports, title="no <b>tags</b>"), USER, env=env))
    refused("bad_value", lambda: execute(store, p, CreatePost(bin=exports, description="y" * (YT_DESCRIPTION_MAX_BYTES + 1)), USER, env=env))
    refused("bad_value", lambda: execute(store, p, CreatePost(bin=exports, tags=["t" * YT_TAGS_MAX_CHARS, "more"]), USER, env=env))
    assert p.posts == []


def test_set_fields_with_youtube_limits(world):
    store, p, env, exports, video, stills, still = world
    pid = execute(store, p, CreatePost(bin=exports), USER, env=env).post
    r = execute(store, p, SetPostField(post=pid, field="title", value="  Two  spaces   collapse "), USER, env=env)
    assert p.posts[0].title == "Two spaces collapse" and r.change.description == 'the youtube post for Rough cut Exports take 1 - titled "Two spaces collapse"'
    r = execute(store, p, SetPostField(post=pid, field="description", value="line one\r\nline two\n"), USER, env=env)
    assert p.posts[0].description == "line one\nline two" and r.change.description == '"Two spaces collapse" - description written (17 characters)'
    r = execute(store, p, SetPostField(post=pid, field="tags", value="comfyui, ai video, comfyui, "), USER, env=env)
    assert p.posts[0].tags == ["comfyui", "ai video"] and r.change.description == '"Two spaces collapse" - tags: comfyui, ai video'
    execute(store, p, SetPostField(post=pid, field="tags", value=["a", "b"]), USER, env=env)
    assert p.posts[0].tags == ["a", "b"]
    refused("bad_value", lambda: execute(store, p, SetPostField(post=pid, field="tags", value=[1, 2]), USER, env=env))
    refused("bad_value", lambda: execute(store, p, SetPostField(post=pid, field="title", value="a" * (YT_TITLE_MAX + 1)), USER, env=env))
    refused("bad_value", lambda: execute(store, p, SetPostField(post=pid, field="title", value=3), USER, env=env))
    # a cleared title falls back to the take in the label
    r = execute(store, p, SetPostField(post=pid, field="title", value=""), USER, env=env)
    assert r.change.description == '"Two spaces collapse" - title cleared'
    # flags
    execute(store, p, SetPostField(post=pid, field="synthetic", value=False), USER, env=env)
    assert p.posts[0].synthetic is False
    refused("bad_value", lambda: execute(store, p, SetPostField(post=pid, field="made_for_kids", value="yes"), USER, env=env))
    # the video: another take of the same bin, never another bin's
    take2 = add_take(p, exports, type="video")
    r = execute(store, p, SetPostField(post=pid, field="media", value=take2), USER, env=env)
    assert p.posts[0].media == take2 and r.change.description.endswith("- now Rough cut Exports take 2")
    refused("not_in_bin", lambda: execute(store, p, SetPostField(post=pid, field="media", value=still), USER, env=env))
    # the thumbnail is a picture
    execute(store, p, SetPostField(post=pid, field="thumbnail", value=still), USER, env=env)
    assert p.posts[0].thumbnail == still
    refused("not_image", lambda: execute(store, p, SetPostField(post=pid, field="thumbnail", value=video), USER, env=env))
    execute(store, p, SetPostField(post=pid, field="thumbnail", value=None), USER, env=env)
    assert p.posts[0].thumbnail is None
    # extra merges, null removes
    execute(store, p, SetPostField(post=pid, field="extra", value={"category": "28", "playlist": "PL1"}), USER, env=env)
    execute(store, p, SetPostField(post=pid, field="extra", value={"playlist": None}), USER, env=env)
    assert p.posts[0].extra == {"category": "28"}
    refused("unknown_id", lambda: execute(store, p, SetPostField(post="pub_nope", field="title", value="x"), USER, env=env))


def test_privacy_and_schedule(world):
    store, p, env, exports, video, stills, still = world
    pid = execute(store, p, CreatePost(bin=exports, title="t"), USER, env=env).post
    refused("bad_value", lambda: execute(store, p, SetPostField(post=pid, field="publish_at", value="tomorrow"), USER, env=env))
    execute(store, p, SetPostField(post=pid, field="publish_at", value="2026-11-02T17:00:00Z"), USER, env=env)
    assert p.posts[0].publish_at == "2026-11-02T17:00:00Z"
    # scheduled stays private until it goes out
    refused("bad_value", lambda: execute(store, p, SetPostField(post=pid, field="privacy", value="unlisted"), USER, env=env))
    execute(store, p, SetPostField(post=pid, field="publish_at", value=None), USER, env=env)
    execute(store, p, SetPostField(post=pid, field="privacy", value="unlisted"), USER, env=env)
    refused("bad_value", lambda: execute(store, p, SetPostField(post=pid, field="publish_at", value="2026-11-02T17:00:00Z"), USER, env=env))
    refused("bad_value", lambda: execute(store, p, SetPostField(post=pid, field="privacy", value="secret"), USER, env=env))
    # the user may go public; the agent never does
    execute(store, p, SetPostField(post=pid, field="privacy", value="public"), USER, env=env)
    assert p.posts[0].privacy == "public"
    live_session(p)
    refused("bad_value", lambda: execute(store, p, SetPostField(post=pid, field="privacy", value="public"), AGENT, env=env))
    execute(store, p, SetPostField(post=pid, field="privacy", value="private"), AGENT, env=env)
    assert p.posts[0].privacy == "private"


def test_agent_drafts_land_as_lines_in_every_mode_and_revert(world):
    store, p, env, exports, video, stills, still = world
    sess = live_session(p)
    for mode in ("auto", "manual", "image_review", "video_review"):
        sess.mode = mode
        r = execute(store, p, CreatePost(bin=exports, title=f"in {mode}", reason="the cut passed QC"), AGENT, env=env)
        assert r.change.decision is None, mode
        assert r.messages[0].kind == "system" and r.messages[0].text.startswith("agent: drafted a youtube post")
    assert len(p.posts) == 4 and all(q.by == "agent" for q in p.posts)
    # a card when asked, and a rejection undoes it
    r = execute(store, p, SetPostField(post=p.posts[0].id, field="title", value="changed", card=True, reason="tighter"), AGENT, env=env)
    assert r.change.decision.status == "pending" and p.posts[0].title == "changed"
    decide(store, p, r.change.id, "reject", None, USER, env=env)
    assert p.posts[0].title == "in auto"
    # plan mode writes nothing (the revert re-made the project's records: take the session afresh)
    p.agent_session.mode = "plan"
    refused("plan_mode", lambda: execute(store, p, CreatePost(bin=exports), AGENT, env=env))


def test_uploaded_and_uploading_posts_are_closed_to_edits(world):
    store, p, env, exports, video, stills, still = world
    pid = execute(store, p, CreatePost(bin=exports, title="t"), USER, env=env).post
    p.posts[0].status = "uploading"
    refused("post_busy", lambda: execute(store, p, SetPostField(post=pid, field="title", value="u"), USER, env=env))
    refused("post_busy", lambda: execute(store, p, DeletePost(post=pid), USER, env=env))
    p.posts[0].status = "uploaded"
    p.posts[0].studio_url = "https://studio.youtube.com/video/abc/edit"
    exc = refused("post_uploaded", lambda: execute(store, p, SetPostField(post=pid, field="title", value="u"), USER, env=env))
    assert exc.extra["studio_url"].endswith("/edit")
    r = execute(store, p, DeletePost(post=pid), USER, env=env)
    assert r.change.description == 'forgot "t"; the video stays on the platform' and p.posts == []
    # a failed upload edited becomes a draft again
    pid = execute(store, p, CreatePost(bin=exports, title="t"), USER, env=env).post
    p.posts[0].status, p.posts[0].error = "failed", "quota"
    execute(store, p, SetPostField(post=pid, field="title", value="t2"), USER, env=env)
    assert p.posts[0].status == "draft" and p.posts[0].error is None
    r = execute(store, p, DeletePost(post=pid), USER, env=env)
    assert r.change.description == 'dropped the draft post "t2"'


def test_deleting_the_video_or_bin_takes_the_drafts_and_keeps_uploaded(world):
    store, p, env, exports, video, stills, still = world
    draft = execute(store, p, CreatePost(bin=exports, title="draft"), USER, env=env).post
    sent = execute(store, p, CreatePost(bin=exports, title="sent"), USER, env=env).post
    execute(store, p, SetPostField(post=draft, field="thumbnail", value=still), USER, env=env)
    p.posts[1].status = "uploaded"
    execute(store, p, DeleteMedia(media=still), USER, env=env)
    assert p.posts[0].thumbnail is None
    execute(store, p, DeleteMedia(media=video), USER, env=env)
    assert [q.id for q in p.posts] == [sent]
    execute(store, p, CreatePost(bin=exports, media=add_take(p, exports, type="video")), USER, env=env)
    r = execute(store, p, DeleteBin(bin=exports), USER, env=env)
    assert [q.id for q in p.posts] == [sent] and "1 draft post dropped" in r.change.description


def test_post_ids_are_never_shown_to_the_user(world):
    store, p, env, exports, video, stills, still = world
    pid = execute(store, p, CreatePost(bin=exports, title="The cut"), USER, env=env).post
    exc = refused("raw_id", lambda: check_no_raw_ids(p, f"uploaded {pid}"))
    assert exc.extra["ids"][pid] == 'the youtube post "The cut"'


def test_upload_is_a_card_from_the_agent_and_lands_from_the_user(world):
    from thm_core.commands import RecordPostUpload, UploadPost

    store, p, env, exports, video, stills, still = world
    pid = execute(store, p, CreatePost(bin=exports), USER, env=env).post
    refused("title_required", lambda: execute(store, p, UploadPost(post=pid), USER, env=env))
    execute(store, p, SetPostField(post=pid, field="title", value="The cut"), USER, env=env)
    # the file must be there
    gone = make_env.__globals__["Env"](workflows_dir=env.workflows_dir, isfile=lambda path: False)
    refused("missing_file", lambda: execute(store, p, UploadPost(post=pid), USER, env=gone))
    # the agent asks; a bare upload is refused
    sess = live_session(p)
    sess.mode = "auto"
    refused("propose_required", lambda: execute(store, p, UploadPost(post=pid), AGENT, env=env))
    from dataclasses import replace

    r = execute(store, p, UploadPost(post=pid, reason="your QC passed the export"), replace(AGENT, propose=True), env=env)
    assert r.change.decision.status == "proposed" and p.posts[0].status == "draft"
    assert r.change.description == 'proposed: upload "The cut" to youtube @halleen (private)'
    # the approval marks it uploading (the server then starts the transfer)
    decide(store, p, r.change.id, "approve", None, USER, env=env)
    assert p.posts[0].status == "uploading"
    refused("post_busy", lambda: execute(store, p, UploadPost(post=pid), USER, env=env))
    refused("post_busy", lambda: execute(store, p, SetPostField(post=pid, field="title", value="x"), USER, env=env))
    # the server lands the outcome
    done = RecordPostUpload(post=pid, status="uploaded", remote_id="abc123", remote_url="https://youtu.be/abc123", studio_url="https://studio.youtube.com/video/abc123/edit")
    r = execute(store, p, done, replace(AGENT, bypass_gates=True), env=env)
    post = p.posts[0]
    assert post.status == "uploaded" and post.remote_id == "abc123" and post.uploaded and post.error is None
    assert r.change.description == 'uploaded "The cut" to youtube @halleen (private); finish it in YouTube Studio: https://studio.youtube.com/video/abc123/edit'
    refused("post_uploaded", lambda: execute(store, p, UploadPost(post=pid), USER, env=env))
    # a failure is a draft again once edited, and the user's own upload needs no card
    pid2 = execute(store, p, CreatePost(bin=exports, title="Again"), USER, env=env).post
    execute(store, p, UploadPost(post=pid2), USER, env=env)
    assert p.posts[1].status == "uploading"
    r = execute(store, p, RecordPostUpload(post=pid2, status="failed", error="quotaExceeded"), replace(USER, bypass_gates=True), env=env)
    assert p.posts[1].status == "failed" and p.posts[1].error == "quotaExceeded" and r.change.description == 'upload of "Again" to youtube @halleen failed: quotaExceeded'
    execute(store, p, UploadPost(post=pid2, requested=True), AGENT, env=env)
    assert p.posts[1].status == "uploading" and p.posts[1].error is None
    # record_post_upload is the server's: not in the command table
    from thm_core.commands import COMMANDS

    assert "record_post_upload" not in COMMANDS and "upload_post" in COMMANDS


def test_the_channel_is_the_projects_pick_and_a_post_needs_one(world):
    from thm_core.commands import SetProjectSettings, UploadPost

    store, p, env, exports, video, stills, still = world
    # no channel on the project: no post can be drafted, and the hint points at the settings
    p.youtube_channel = ""
    exc = refused("no_channel", lambda: execute(store, p, CreatePost(bin=exports, title="t"), USER, env=env))
    assert "Project settings" in exc.detail
    # the user picks a connected channel in the settings; an unknown one is refused
    refused("unknown_channel", lambda: execute(store, p, SetProjectSettings(youtube_channel="UCnope"), USER, env=env))
    r = execute(store, p, SetProjectSettings(youtube_channel="UCabc"), USER, env=env)
    assert p.youtube_channel == "UCabc" and r.change.description == "project settings - youtube channel @halleen"
    pid = execute(store, p, CreatePost(bin=exports, title="t"), USER, env=env).post
    assert p.posts[0].channel == "UCabc"
    # the agent never picks the channel
    live_session(p).mode = "auto"
    refused("wrong_actor", lambda: execute(store, p, SetProjectSettings(youtube_channel="UCabc"), AGENT, env=env))
    # the token gone from the server: the upload is refused, the post stays a draft
    env.youtube_channel = lambda cid: None
    exc = refused("unknown_channel", lambda: execute(store, p, UploadPost(post=pid), USER, env=env))
    assert exc.extra["channel"] == "UCabc" and p.posts[0].status == "draft"
    refused("unknown_channel", lambda: execute(store, p, CreatePost(bin=exports, title="t2"), USER, env=env))
    # clearing the channel is a logged setting too; a post drafted before keeps its own
    env.youtube_channel = lambda cid: dict(CHANNEL) if cid == CHANNEL["id"] else None
    r = execute(store, p, SetProjectSettings(youtube_channel=""), USER, env=env)
    assert p.youtube_channel == "" and r.change.description == "project settings - youtube channel cleared" and p.posts[0].channel == "UCabc"
    # a core Env with no hook checks nothing and names the id
    bare = make_env.__globals__["Env"](workflows_dir=env.workflows_dir)
    assert bare.channel_known("UCabc") and not bare.channel_known("") and bare.channel_label("UCabc") == "UCabc"
