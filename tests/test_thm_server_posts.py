"""Posts over HTTP: the read shapes, the commands through the command route, the private gate."""

import shutil

import pytest

from thm_server_fixtures import make_tiny_mp4, seeded_world

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
AGENT = {"x-thm-actor": "agent", "x-thm-session": "as_test", "x-thm-inbox-cursor": "0"}


@pytest.fixture
def world(tmp_path):
    """The seeded world plus one video file uploaded into Video 1, the way the user would."""
    w = seeded_world(tmp_path)
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe needed for a video take")
    clip = make_tiny_mp4(tmp_path / "media" / "cut.mp4")
    r = w.client.post(f"/api/projects/{w.slug}/uploads", files={"file": ("cut.mp4", clip.read_bytes(), "video/mp4")}, data={"bin": w.video})
    assert r.status_code == 200, r.text
    w.clip = r.json()["media"]["id"]
    # the server knows one channel, and the project is connected to it (the user's pick in settings)
    w.app.state.thm.youtube_env = _creds(tmp_path)
    cmd(w, "set_project_settings", {"youtube_channel": "UCabc"})
    with w.client:  # the lifespan: an upload task runs on the app's loop, like an export
        yield w


def cmd(w, name, body, headers=None, status=200):
    r = w.client.post(f"/api/projects/{w.slug}/commands/{name}", json=body, headers=headers or {})
    assert r.status_code == status, r.text
    return r.json()


def test_post_read_shapes(world):
    w = world
    assert w.client.get(f"/api/projects/{w.slug}").json()["posts"] == []
    out = cmd(w, "create_post", {"bin": w.video, "title": "Rail Dawn, the cut", "description": "made with THM", "tags": ["comfyui"]})
    pid = out["post"]
    assert pid.startswith("pub_") and out["bin"] is None and out["sequence"] is None
    assert out["change"]["description"] == 'drafted a youtube post for Video 1 file 1: "Rail Dawn, the cut"'
    posts = w.client.get(f"/api/projects/{w.slug}/posts").json()
    assert len(posts) == 1
    p = posts[0]
    assert p["id"] == pid and p["bin_name"] == "Video 1" and p["media_record"]["id"] == w.clip and p["media_record"]["type"] == "video"
    assert p["status"] == "draft" and p["privacy"] == "private" and p["synthetic"] is True and p["remote_id"] is None
    assert w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()["title"] == "Rail Dawn, the cut"
    assert w.client.get(f"/api/projects/{w.slug}/posts?bin={w.image}").json() == []
    assert [q["id"] for q in w.client.get(f"/api/projects/{w.slug}/posts?bin={w.video}").json()] == [pid]
    assert w.client.get(f"/api/projects/{w.slug}/posts/pub_nope").status_code == 404
    detail = w.client.get(f"/api/projects/{w.slug}").json()
    assert [q["id"] for q in detail["posts"]] == [pid]
    # refusals come through with their codes
    r = w.client.post(f"/api/projects/{w.slug}/commands/create_post", json={"bin": w.image})
    assert r.status_code in (409, 422) and r.json()["code"] == "not_video"
    r = w.client.post(f"/api/projects/{w.slug}/commands/set_post_field", json={"post": pid, "field": "title", "value": "x" * 101})
    assert r.json()["code"] == "bad_value" and r.json()["extra"]["max"] == 100
    cmd(w, "delete_post", {"post": pid})
    assert w.client.get(f"/api/projects/{w.slug}/posts").json() == []


def test_agent_drafts_land_as_feed_lines(world):
    w = world
    cmd(w, "start_session", {"notes": "post the cut", "mode": "auto"})
    cmd(w, "connect_session", {"harness": "claude-code"}, {"x-thm-actor": "agent"})
    sess = w.client.get(f"/api/projects/{w.slug}").json()["agent_session"]
    headers = {**AGENT, "x-thm-session": sess["id"]}
    out = cmd(w, "create_post", {"bin": w.video, "title": "The cut", "reason": "your QC passed take 1"}, headers)
    assert out["change"]["decision"] is None
    assert [m["text"] for m in out["messages"]] == ['agent: drafted a youtube post for Video 1 file 1: "The cut" -- your QC passed take 1']
    pid = out["post"]
    r = w.client.post(f"/api/projects/{w.slug}/commands/set_post_field", json={"post": pid, "field": "privacy", "value": "public"}, headers=headers)
    assert r.json()["code"] == "bad_value"
    # the post is on the record the agent reads
    assert w.client.get(f"/api/projects/{w.slug}/posts", headers=headers).json()[0]["by"] == "agent"


def test_private_project_refuses_the_agent_on_posts(world):
    w = world
    pid = cmd(w, "create_post", {"bin": w.video, "title": "t"})["post"]
    project = w.registry.get(w.slug)
    project.private = True
    w.store.save(project)
    w.registry.invalidate(w.slug)
    for path in (f"/api/projects/{w.slug}/posts", f"/api/projects/{w.slug}/posts/{pid}"):
        r = w.client.get(path, headers={"x-thm-actor": "agent"})
        assert r.status_code == 403 and r.json()["code"] == "private_project", path
    r = w.client.post(f"/api/projects/{w.slug}/commands/create_post", json={"bin": w.video}, headers={"x-thm-actor": "agent"})
    assert r.status_code == 403
    # the user still reads them
    assert w.client.get(f"/api/projects/{w.slug}/posts").json()[0]["id"] == pid


# -- the upload ------------------------------------------------------------------------------------


class FakeYouTube:
    """The token refresh, the session open, and chunked PUTs, answered in memory."""

    def __init__(self, *, fail_with: tuple[int, bytes] | None = None, channel: dict | None = None):
        self.calls = []
        self.got = 0
        self.size = None
        self.body = None
        self.fail_with = fail_with
        self.channel = channel or {"id": "UCnew", "title": "Second Channel", "customUrl": "@second"}
        self.refreshed_with = []  # the refresh tokens the uploads used

    def __call__(self, method, url, body, headers):
        import json as _json
        import urllib.parse as _up

        from thm_core import youtube

        self.calls.append((method, url))
        if url == youtube.TOKEN_URL:
            fields = dict(_up.parse_qsl(body.decode()))
            if fields.get("grant_type") == "authorization_code":  # the consent's code exchange
                return 200, _json.dumps({"access_token": "at", "refresh_token": "rt-" + fields["code"], "scope": youtube.SCOPES}).encode(), {}
            self.refreshed_with.append(fields.get("refresh_token"))
            return 200, _json.dumps({"access_token": "at"}).encode(), {}
        if url == youtube.CHANNELS_URL:
            return 200, _json.dumps({"items": [{"id": self.channel["id"], "snippet": {"title": self.channel["title"], "customUrl": self.channel["customUrl"]}}]}).encode(), {}
        if url == youtube.UPLOAD_URL:
            if self.fail_with:
                return self.fail_with[0], self.fail_with[1], {}
            self.size = int(headers["X-Upload-Content-Length"])
            self.body = _json.loads(body)
            return 200, b"", {"Location": "https://upload.example/s"}
        start, end = (int(x) for x in headers["Content-Range"].split(" ")[1].split("/")[0].split("-"))
        self.got = end + 1
        if self.got >= self.size:
            return 200, _json.dumps({"id": "vid123"}).encode(), {}
        return 308, b"", {"Range": f"bytes=0-{end}"}


QUOTA_ERROR = b'{"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}}'


def _creds(tmp_path):
    """The client file and one connected channel (@halleen) in the token directory beside it."""
    import json as _json

    client = tmp_path / "client.json"
    client.write_text(_json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}), encoding="utf-8")
    folder = tmp_path / "youtube_tokens"
    folder.mkdir(exist_ok=True)
    (folder / "UCabc.json").write_text(_json.dumps({"refresh_token": "rt", "channel": {"id": "UCabc", "title": "Halleen", "handle": "@halleen"}, "connected": "2026-10-31T00:00:00Z"}), encoding="utf-8")
    return {"THM_YOUTUBE_CLIENT_FILE": str(client)}


def _wait_connect(w, tries=200):
    import time

    for _ in range(tries):
        s = w.client.get(f"/api/projects/{w.slug}/youtube/connect").json()
        if s["status"] in ("done", "failed"):
            return s
        time.sleep(0.02)
    raise AssertionError("connect did not finish")


def _wait_upload(w, pid, tries=200):
    import time

    for _ in range(tries):
        s = w.client.get(f"/api/projects/{w.slug}/posts/{pid}/upload").json()
        if s["status"] in ("done", "failed"):
            return s
        time.sleep(0.02)
    raise AssertionError("upload did not finish")


def test_unconfigured_server_refuses_the_upload_with_the_fix(world):
    w = world
    st = w.app.state.thm
    pid = cmd(w, "create_post", {"bin": w.video, "title": "t"})["post"]
    st.youtube_env = {}
    status = w.client.get("/api/publish/youtube").json()
    assert status["configured"] is False and status["channels"] == [] and status["can_connect"] is False
    r = w.client.post(f"/api/projects/{w.slug}/commands/upload_post", json={"post": pid})
    assert r.status_code == 409 and r.json()["code"] == "youtube_unconfigured" and "THM_YOUTUBE_CLIENT_FILE" in r.json()["detail"]
    assert w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()["status"] == "draft"
    # a connect cannot start either
    st.connects._hosts = {"testclient"}
    r = w.client.post(f"/api/projects/{w.slug}/youtube/connect")
    assert r.status_code == 409 and r.json()["code"] == "youtube_unconfigured"


def test_user_upload_lands_private_with_the_studio_link(world, tmp_path):
    w = world
    st = w.app.state.thm
    st.youtube_env = _creds(tmp_path)
    st.youtube_transport = fake = FakeYouTube()
    status = w.client.get("/api/publish/youtube").json()
    assert status["configured"] is True and status["detail"] == "" and [c["handle"] for c in status["channels"]] == ["@halleen"]
    pid = cmd(w, "create_post", {"bin": w.video, "title": "Rail Dawn", "description": "d", "tags": ["x"]})["post"]
    p = w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()
    assert p["channel"] == "UCabc" and p["channel_handle"] == "@halleen"
    out = cmd(w, "upload_post", {"post": pid})
    assert out["post"] == pid and out["change"]["description"] == 'upload "Rail Dawn" to youtube @halleen (private)'
    status = _wait_upload(w, pid)
    assert status["status"] == "done" and status["remote_id"] == "vid123" and status["studio_url"] == "https://studio.youtube.com/video/vid123/edit"
    p = w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()
    assert p["status"] == "uploaded" and p["remote_url"] == "https://youtu.be/vid123" and p["uploaded"] and p["error"] is None
    assert fake.body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}
    assert fake.body["snippet"]["title"] == "Rail Dawn" and fake.got == fake.size
    # closed to edits now; the change log has the landing
    r = w.client.post(f"/api/projects/{w.slug}/commands/set_post_field", json={"post": pid, "field": "title", "value": "x"})
    assert r.json()["code"] == "post_uploaded" and r.json()["extra"]["studio_url"].endswith("/edit")
    changes = w.client.get(f"/api/projects/{w.slug}/changes").json()
    assert [c["command"] for c in changes][-2:] == ["upload_post", "record_post_upload"]
    assert changes[-1]["description"].startswith('uploaded "Rail Dawn" to youtube @halleen (private); finish it in YouTube Studio: https://studio.youtube.com/video/vid123/edit')
    assert fake.refreshed_with == ["rt"]  # the channel's own token


def test_failed_upload_is_recorded_and_can_run_again(world, tmp_path):
    w = world
    st = w.app.state.thm
    st.youtube_env = _creds(tmp_path)
    st.youtube_transport = FakeYouTube(fail_with=(403, QUOTA_ERROR))
    pid = cmd(w, "create_post", {"bin": w.video, "title": "Rail Dawn"})["post"]
    cmd(w, "upload_post", {"post": pid})
    status = _wait_upload(w, pid)
    assert status["status"] == "failed" and "quotaExceeded" in status["error"]
    p = w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()
    assert p["status"] == "failed" and "quotaExceeded" in p["error"]
    st.youtube_transport = FakeYouTube()
    cmd(w, "upload_post", {"post": pid})
    assert _wait_upload(w, pid)["status"] == "done"
    assert w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()["status"] == "uploaded"


def test_agent_upload_is_a_card_the_user_approves(world, tmp_path):
    w = world
    st = w.app.state.thm
    st.youtube_env = _creds(tmp_path)
    st.youtube_transport = FakeYouTube()
    cmd(w, "start_session", {"notes": "post the cut", "mode": "auto"})
    cmd(w, "connect_session", {"harness": "claude-code"}, {"x-thm-actor": "agent"})
    sess = w.client.get(f"/api/projects/{w.slug}").json()["agent_session"]
    headers = {**AGENT, "x-thm-session": sess["id"]}
    pid = cmd(w, "create_post", {"bin": w.video, "title": "The cut", "reason": "QC passed"}, headers)["post"]
    r = w.client.post(f"/api/projects/{w.slug}/commands/upload_post", json={"post": pid}, headers=headers)
    assert r.status_code in (409, 422) and r.json()["code"] == "propose_required"
    out = cmd(w, "upload_post", {"post": pid, "reason": "the export you passed"}, {**headers, "x-thm-propose": "1"})
    assert out["change"]["decision"]["status"] == "proposed"
    assert w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()["status"] == "draft"
    assert w.client.get(f"/api/projects/{w.slug}/posts/{pid}/upload").json()["status"] == "idle"
    # the user says yes: the transfer starts
    r = w.client.post(f"/api/projects/{w.slug}/changes/{out['change']['id']}/decision", json={"action": "approve"})
    assert r.status_code == 200, r.text
    status = _wait_upload(w, pid)
    assert status["status"] == "done"
    p = w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()
    assert p["status"] == "uploaded" and p["remote_id"] == "vid123"
    feed = w.client.get(f"/api/projects/{w.slug}/feed").json()["entries"]
    texts = [e["message"]["text"] for e in feed]
    assert any(t.startswith('agent: uploaded "The cut" to youtube @halleen (private); finish it in YouTube Studio') for t in texts), texts
    # requested: the user asked in the feed, so it lands and starts without a card
    pid2 = cmd(w, "create_post", {"bin": w.video, "title": "Again"}, headers)["post"]
    out = cmd(w, "upload_post", {"post": pid2, "requested": True}, headers)
    assert out["change"]["decision"] is None
    assert _wait_upload(w, pid2)["status"] == "done"


def test_closing_or_rejecting_the_card_never_uploads_and_one_card_per_post(world, tmp_path):
    w = world
    st = w.app.state.thm
    st.youtube_env = _creds(tmp_path)
    st.youtube_transport = fake = FakeYouTube()
    cmd(w, "start_session", {"notes": "post the cut", "mode": "auto"})
    cmd(w, "connect_session", {"harness": "claude-code"}, {"x-thm-actor": "agent"})
    sess = w.client.get(f"/api/projects/{w.slug}").json()["agent_session"]
    headers = {**AGENT, "x-thm-session": sess["id"]}
    propose = {**headers, "x-thm-propose": "1"}
    pid = cmd(w, "create_post", {"bin": w.video, "title": "The cut"}, headers)["post"]
    first = cmd(w, "upload_post", {"post": pid, "reason": "ready"}, propose)["change"]["id"]
    # a second card for the same post while the first waits is refused
    r = w.client.post(f"/api/projects/{w.slug}/commands/upload_post", json={"post": pid, "reason": "again"}, headers=propose)
    assert r.status_code in (409, 422) and r.json()["code"] == "card_exists" and r.json()["extra"]["change"] == first
    # the user closes it: nothing is sent, the post is a draft
    r = w.client.post(f"/api/projects/{w.slug}/changes/{first}/decision", json={"action": "close"})
    assert r.status_code == 200, r.text
    assert w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()["status"] == "draft"
    assert w.client.get(f"/api/projects/{w.slug}/posts/{pid}/upload").json()["status"] == "idle"
    assert not any(u == "https://upload.example/s" for _, u in fake.calls)
    # a new card may go now; a reply is a change to the text, not a go
    second = cmd(w, "upload_post", {"post": pid, "reason": "ready"}, propose)["change"]["id"]
    r = w.client.post(f"/api/projects/{w.slug}/changes/{second}/decision", json={"action": "reply", "note": "shorter title"})
    assert r.status_code == 200, r.text
    assert w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()["status"] == "draft"
    # the agent edits the draft, sends one more card, and this time the user presses Upload:
    # the post goes up as it stands now
    cmd(w, "set_post_field", {"post": pid, "field": "title", "value": "Cut"}, headers)
    third = cmd(w, "upload_post", {"post": pid, "reason": "the short title"}, propose)["change"]["id"]
    r = w.client.post(f"/api/projects/{w.slug}/changes/{third}/decision", json={"action": "reject"})
    assert r.status_code == 200 and w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()["status"] == "draft"
    fourth = cmd(w, "upload_post", {"post": pid, "reason": "the short title"}, propose)["change"]["id"]
    r = w.client.post(f"/api/projects/{w.slug}/changes/{fourth}/decision", json={"action": "approve"})
    assert r.status_code == 200, r.text
    assert _wait_upload(w, pid)["status"] == "done"
    assert fake.body["snippet"]["title"] == "Cut"
    assert sum(1 for _, u in fake.calls if u == "https://upload.example/s") >= 1
    assert sum(1 for m, u in fake.calls if m == "POST" and "uploadType=resumable" in u) == 1


# -- the channel -----------------------------------------------------------------------------------


def test_connect_a_channel_at_the_pc_then_pick_it_from_anywhere(world, tmp_path):
    import urllib.error
    import urllib.parse
    import urllib.request

    from thm_core import youtube

    w = world
    st = w.app.state.thm
    st.youtube_transport = fake = FakeYouTube()
    connect = f"/api/projects/{w.slug}/youtube/connect"
    # not at the PC: a new channel cannot be connected from here, and the status says so
    st.connects._hosts = set()
    assert w.client.get("/api/publish/youtube").json()["can_connect"] is False
    r = w.client.post(connect)
    assert r.status_code == 409 and r.json()["code"] == "pc_only"
    assert w.client.get(connect).json()["status"] == "idle"
    # at the PC: the start answers Google's URL and a loopback redirect
    st.connects._hosts = {"testclient"}
    status = w.client.get("/api/publish/youtube").json()
    assert status["can_connect"] is True and [c["id"] for c in status["channels"]] == ["UCabc"]
    r = w.client.post(connect, headers={"x-thm-actor": "agent"})
    assert r.status_code == 403 and r.json()["code"] == "wrong_actor"
    out = w.client.post(connect).json()
    assert out["status"] == "waiting" and out["auth_url"].startswith(youtube.AUTH_URL)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(out["auth_url"]).query)
    redirect, state = q["redirect_uri"][0], q["state"][0]
    assert redirect.startswith("http://127.0.0.1:") and "youtube.readonly" in q["scope"][0]
    assert w.client.get(connect).json()["status"] == "waiting"
    # a hit that is not Google's answer to this consent is refused and changes nothing
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(redirect + "?state=wrong&code=x", timeout=5)
    assert exc.value.code == 400
    assert w.client.get(connect).json()["status"] == "waiting"
    # Google sends the browser back: the code is traded, the channel read, its token written.
    # The project is untouched: it points at a channel only once the user picks it and saves
    before = len(w.client.get(f"/api/projects/{w.slug}/changes").json())
    with urllib.request.urlopen(f"{redirect}?state={state}&code=c0de", timeout=5) as resp:
        assert b"THM has the channel" in resp.read()
    s = _wait_connect(w)
    assert s["status"] == "done" and s["channel"]["handle"] == "@second" and s["channel"]["id"] == "UCnew", s
    assert (tmp_path / "youtube_tokens" / "UCnew.json").is_file()
    detail = w.client.get(f"/api/projects/{w.slug}").json()
    assert detail["youtube_channel"] == "UCabc" and detail["youtube_channel_record"]["handle"] == "@halleen"
    assert len(w.client.get(f"/api/projects/{w.slug}/changes").json()) == before
    assert [c["handle"] for c in w.client.get("/api/publish/youtube").json()["channels"]] == ["@halleen", "@second"]
    # the user picks it in the settings and saves
    out = cmd(w, "set_project_settings", {"youtube_channel": "UCnew"})
    assert out["change"]["description"] == "project settings - youtube channel @second"
    # posts drafted now go to the new channel, with its own token
    pid = cmd(w, "create_post", {"bin": w.video, "title": "To the second"})["post"]
    p = w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()
    assert p["channel"] == "UCnew" and p["channel_handle"] == "@second"
    cmd(w, "upload_post", {"post": pid})
    assert _wait_upload(w, pid)["status"] == "done" and fake.refreshed_with == ["rt-c0de"]
    # from anywhere: a project picks an already connected channel from the list; an unknown one is refused
    st.connects._hosts = set()
    out = cmd(w, "set_project_settings", {"youtube_channel": "UCabc"})
    assert out["change"]["description"] == "project settings - youtube channel @halleen"
    assert w.client.get(f"/api/projects/{w.slug}").json()["youtube_channel_record"]["handle"] == "@halleen"
    r = w.client.post(f"/api/projects/{w.slug}/commands/set_project_settings", json={"youtube_channel": "UCnope"})
    assert r.status_code in (409, 422) and r.json()["code"] == "unknown_channel"
    # forgetting a channel: the project keeps the id, shows no record, and cannot draft to it
    r = w.client.delete("/api/publish/youtube/channels/UCabc")
    assert r.status_code == 200 and [c["id"] for c in r.json()["channels"]] == ["UCnew"]
    detail = w.client.get(f"/api/projects/{w.slug}").json()
    assert detail["youtube_channel"] == "UCabc" and detail["youtube_channel_record"] is None
    r = w.client.post(f"/api/projects/{w.slug}/commands/create_post", json={"bin": w.video, "title": "x"})
    assert r.status_code in (409, 422) and r.json()["code"] == "unknown_channel"
    assert w.client.delete("/api/publish/youtube/channels/UCabc").status_code == 404
    # the uploaded post still names the channel it went to
    assert w.client.get(f"/api/projects/{w.slug}/posts/{pid}").json()["channel_handle"] == "@second"


def test_a_waiting_connect_can_be_cancelled_or_replaced(world):
    w = world
    st = w.app.state.thm
    st.connects._hosts = {"testclient"}
    connect = f"/api/projects/{w.slug}/youtube/connect"
    first = w.client.post(connect).json()
    assert first["status"] == "waiting"
    second = w.client.post(connect).json()  # a new start drops the one still waiting
    assert second["status"] == "waiting" and second["auth_url"] != first["auth_url"]
    r = w.client.delete(connect)
    assert r.status_code == 200 and r.json()["status"] == "failed" and r.json()["error"] == "cancelled"
    assert w.client.get(connect).json()["status"] == "failed"
    # the project is untouched
    assert w.client.get(f"/api/projects/{w.slug}").json()["youtube_channel"] == "UCabc"
    # the no-channel case: a project with none cannot draft, and the hint names the settings
    cmd(w, "set_project_settings", {"youtube_channel": ""})
    detail = w.client.get(f"/api/projects/{w.slug}").json()
    assert detail["youtube_channel"] == "" and detail["youtube_channel_record"] is None
    r = w.client.post(f"/api/projects/{w.slug}/commands/create_post", json={"bin": w.video, "title": "x"})
    assert r.status_code in (409, 422) and r.json()["code"] == "no_channel" and "settings" in r.json()["detail"]
