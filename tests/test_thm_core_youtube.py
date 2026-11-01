"""The YouTube module against a fake transport: credentials, refresh, the resumable upload."""

import json

import pytest

from thm_core import youtube
from thm_core.models import Post


def creds_files(tmp_path, *, channels=(("UCabc", "Halleen", "@halleen", "rt"),)):
    """The client file plus one token file per channel in the token directory beside it."""
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}), encoding="utf-8")
    folder = tmp_path / youtube.TOKEN_DIR_NAME
    folder.mkdir(exist_ok=True)
    for cid, title, handle, refresh in channels:
        (folder / f"{cid}.json").write_text(json.dumps({"refresh_token": refresh, "scope": youtube.SCOPES, "channel": {"id": cid, "title": title, "handle": handle}, "connected": "2026-10-31T00:00:00Z"}), encoding="utf-8")
    return {youtube.ENV_CLIENT: str(client)}


def test_credentials_and_channels_from_the_environment(tmp_path):
    ok, why = youtube.configured({})
    assert not ok and youtube.ENV_CLIENT in why and "project's settings" in why
    assert youtube.channels({}) == []
    env = creds_files(tmp_path, channels=(("UCabc", "Halleen", "@halleen", "rt"), ("UCdino", "Crash Test Dinos", "@crashtestdinos", "rt2")))
    assert youtube.configured(env) == (True, "")
    assert youtube.token_dir(env) == tmp_path / youtube.TOKEN_DIR_NAME
    chans = youtube.channels(env)
    assert [(c.id, c.handle, c.label) for c in chans] == [("UCdino", "@crashtestdinos", "@crashtestdinos"), ("UCabc", "@halleen", "@halleen")]
    assert youtube.channel_info(env, "UCdino") == {"id": "UCdino", "title": "Crash Test Dinos", "handle": "@crashtestdinos", "connected": "2026-10-31T00:00:00Z"}
    assert youtube.channel_info(env, "UCnope") is None and youtube.channel_info(env, "") is None
    c = youtube.load_creds(env, "UCdino")
    assert (c.client_id, c.client_secret, c.refresh_token) == ("cid", "sec", "rt2")
    with pytest.raises(youtube.YouTubeUnconfigured) as exc:
        youtube.load_creds(env, "UCnope")
    assert "not connected" in str(exc.value) and "@halleen" in str(exc.value) and "settings" in str(exc.value)
    with pytest.raises(youtube.YouTubeUnconfigured):
        youtube.load_creds(env, "")
    # a stray or broken file in the directory is not a channel
    (tmp_path / youtube.TOKEN_DIR_NAME / "junk.json").write_text("{}", encoding="utf-8")
    (tmp_path / youtube.TOKEN_DIR_NAME / "notes.json").write_text("not json", encoding="utf-8")
    assert len(youtube.channels(env)) == 2
    # the directory can be elsewhere; a missing one is simply empty
    other = {**env, youtube.ENV_TOKEN_DIR: str(tmp_path / "elsewhere")}
    assert youtube.channels(other) == [] and youtube.configured(other)[0]
    # forgetting a channel removes its file; a second time there is nothing to remove
    assert youtube.remove_channel(env, "UCabc") and not youtube.remove_channel(env, "UCabc")
    assert [c.id for c in youtube.channels(env)] == ["UCdino"]


def test_consent_helpers_against_a_fake_google(tmp_path):
    env = creds_files(tmp_path, channels=())
    url = youtube.auth_url("cid", "http://127.0.0.1:5/", "st8")
    assert url.startswith(youtube.AUTH_URL + "?") and "access_type=offline" in url and "prompt=consent" in url and "state=st8" in url
    assert "youtube.upload" in url and "youtube.readonly" in url

    def transport(method, url_, body, headers):
        if url_ == youtube.TOKEN_URL:
            fields = dict(p.split("=", 1) for p in body.decode().split("&"))
            assert fields["grant_type"] == "authorization_code" and fields["code"] == "c0de" and fields["client_id"] == "cid"
            return 200, json.dumps({"access_token": "at", "refresh_token": "rt9", "scope": youtube.SCOPES}).encode(), {}
        assert url_ == youtube.CHANNELS_URL and headers["Authorization"] == "Bearer at" and method == "GET"
        return 200, json.dumps({"items": [{"id": "UCnew", "snippet": {"title": "New Handle", "customUrl": "@newhandle"}}]}).encode(), {}

    token = youtube.exchange_code("cid", "sec", "c0de", "http://127.0.0.1:5/", transport)
    info = youtube.fetch_channel(token["access_token"], transport)
    assert info == {"id": "UCnew", "title": "New Handle", "handle": "@newhandle"}
    ch = youtube.save_channel_token(env, token["refresh_token"], token["scope"], info, connected="2026-10-31T01:00:00Z")
    assert ch.id == "UCnew" and ch.label == "@newhandle" and youtube.channels(env)[0].path == ch.path
    assert youtube.load_creds(env, "UCnew").refresh_token == "rt9"
    # no refresh token in the answer, and an empty channel list, are named failures
    with pytest.raises(youtube.YouTubeError) as exc:
        youtube.exchange_code("cid", "sec", "c0de", "http://127.0.0.1:5/", lambda *a: (200, b'{"access_token": "at"}', {}))
    assert "refresh_token" in str(exc.value)
    with pytest.raises(youtube.YouTubeError) as exc:
        youtube.fetch_channel("at", lambda *a: (200, b'{"items": []}', {}))
    assert "chooser" in str(exc.value)
    with pytest.raises(youtube.YouTubeError) as exc:
        youtube.exchange_code("cid", "sec", "bad", "http://127.0.0.1:5/", lambda *a: (400, b'{"error": "invalid_grant", "error_description": "Bad Request"}', {}))
    assert exc.value.status == 400 and "invalid_grant" in str(exc.value)


def post(**kw):
    base = dict(id="pub_1", bin="b_1", media="m_1", title="The cut", description="d", tags=["a", "b c"], extra={"category": "28"})
    base.update(kw)
    return Post(**base)


def test_metadata_body():
    body = youtube.metadata_body(post(publish_at="2026-11-02T17:00:00Z"))
    assert body == {
        "snippet": {"title": "The cut", "description": "d", "tags": ["a", "b c"], "categoryId": "28"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True, "publishAt": "2026-11-02T17:00:00Z"},
    }
    assert "tags" not in youtube.metadata_body(post(tags=[], extra={}))["snippet"]


class FakeYouTube:
    """Answers the token refresh, the session open, and chunked PUTs; drops one chunk when told."""

    def __init__(self, size, *, drop_at=None, refresh_status=200):
        self.size, self.calls, self.got = size, [], 0
        self.drop_at, self.refresh_status = drop_at, refresh_status
        self.dropped = False

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url, dict(headers)))
        if url == youtube.TOKEN_URL:
            if self.refresh_status != 200:
                return self.refresh_status, json.dumps({"error": "invalid_grant", "error_description": "Token has been expired or revoked."}).encode(), {}
            return 200, json.dumps({"access_token": "at"}).encode(), {}
        if url == youtube.UPLOAD_URL:
            assert headers["Authorization"] == "Bearer at" and headers["X-Upload-Content-Length"] == str(self.size)
            assert json.loads(body)["status"]["privacyStatus"] == "private"
            return 200, b"", {"Location": "https://upload.example/session"}
        assert url == "https://upload.example/session"
        rng = headers["Content-Range"]
        if rng.startswith("bytes */"):
            return (308, b"", {"Range": f"bytes=0-{self.got - 1}"}) if self.got else (308, b"", {})
        start, end = (int(x) for x in rng.split(" ")[1].split("/")[0].split("-"))
        if self.drop_at is not None and start == self.drop_at and not self.dropped:
            self.dropped = True
            return 503, b"backend error", {}
        assert start == self.got, (start, self.got)
        self.got = end + 1
        if self.got >= self.size:
            return 200, json.dumps({"id": "vid123"}).encode(), {}
        return 308, b"", {"Range": f"bytes=0-{end}"}


def test_upload_video_chunks_and_resumes(tmp_path):
    f = tmp_path / "cut.mp4"
    f.write_bytes(b"x" * 25)
    seen = []
    fake = FakeYouTube(25, drop_at=20)
    r = youtube.upload_video(youtube.Creds("c", "s", "r"), post(), f, transport=fake, on_progress=seen.append, chunk_bytes=10)
    assert (r.video_id, r.url, r.studio_url) == ("vid123", "https://youtu.be/vid123", "https://studio.youtube.com/video/vid123/edit")
    puts = [h["Content-Range"] for m, u, h in fake.calls if m == "PUT"]
    # three chunks, one dropped, the session asked where it stood, then the rest
    assert puts == ["bytes 0-9/25", "bytes 10-19/25", "bytes 20-24/25", "bytes */25", "bytes 20-24/25"]
    assert seen[-1] == 1.0 and all(0 <= x <= 1 for x in seen)


def test_refresh_failure_names_the_fix(tmp_path):
    f = tmp_path / "cut.mp4"
    f.write_bytes(b"x" * 5)
    with pytest.raises(youtube.YouTubeError) as exc:
        youtube.upload_video(youtube.Creds("c", "s", "r"), post(), f, transport=FakeYouTube(5, refresh_status=400))
    assert exc.value.status == 400 and "invalid_grant" in str(exc.value) and "connect the channel again" in str(exc.value)


def test_refused_upload_carries_youtubes_reason(tmp_path):
    f = tmp_path / "cut.mp4"
    f.write_bytes(b"x" * 5)

    def transport(method, url, body, headers):
        if url == youtube.TOKEN_URL:
            return 200, json.dumps({"access_token": "at"}).encode(), {}
        return 403, json.dumps({"error": {"message": "The request cannot be completed because you have exceeded your quota.", "errors": [{"reason": "quotaExceeded"}]}}).encode(), {}

    with pytest.raises(youtube.YouTubeError) as exc:
        youtube.upload_video(youtube.Creds("c", "s", "r"), post(), f, transport=transport)
    assert exc.value.status == 403 and "quotaExceeded" in str(exc.value)
