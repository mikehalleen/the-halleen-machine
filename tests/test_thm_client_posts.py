"""The agent's ``publish`` verbs, driven through the CLI against the TestClient."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from thm_server_fixtures import make_tiny_mp4, seeded_world

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
import cli  # noqa: E402

BASE = "http://thm.test"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _transport(client):
    def send(method, url, body, headers):
        assert url.startswith(BASE)
        r = client.request(method, url[len(BASE):], content=body, headers=headers)
        return r.status_code, r.content, dict(r.headers)

    return send


def _user_post(client, slug, name, body):
    r = client.post(f"/api/projects/{slug}/commands/{name}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe needed for a video take")
def test_publish_verbs_round_trip(tmp_path, monkeypatch, capsys):
    from test_thm_server_posts import _creds

    w = seeded_world(tmp_path)
    clip = make_tiny_mp4(tmp_path / "media" / "cut.mp4")
    r = w.client.post(f"/api/projects/{w.slug}/uploads", files={"file": ("cut.mp4", clip.read_bytes(), "video/mp4")}, data={"bin": w.video})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    send = _transport(w.client)
    slug = w.slug
    _user_post(w.client, slug, "start_session", {"notes": "post the cut", "mode": "auto"})
    assert cli.main(["--server", BASE, "--project", slug, "connect", "--harness", "claude-code"], transport=send) == 0
    capsys.readouterr()

    # no channel on the project yet: the check says so, and a draft is refused with the hint
    w.app.state.thm.youtube_env = _creds(tmp_path)
    assert cli.main(["--server", BASE, "--project", slug, "publish", "check"], transport=send) == 0
    text = capsys.readouterr().out
    assert "ready to upload" in text and "@halleen (Halleen)" in text and "this project has no channel yet" in text
    assert cli.main(["--server", BASE, "--project", slug, "publish", "new", "--bin", w.video, "--title", "x"], transport=send) == 1
    assert "no_channel" in capsys.readouterr().err
    _user_post(w.client, slug, "set_project_settings", {"youtube_channel": "UCabc"})
    assert cli.main(["--server", BASE, "--project", slug, "publish", "check"], transport=send) == 0
    assert "this project posts to @halleen" in capsys.readouterr().out

    assert cli.main(["--server", BASE, "--project", slug, "publish"], transport=send) == 0
    assert "no posts" in capsys.readouterr().out

    desc = tmp_path / "desc.txt"
    desc.write_text("The Halleen Machine cuts a sequence.\n\nMade in ComfyUI.\n", encoding="utf-8")
    assert cli.main(["--server", BASE, "--project", slug, "--json", "publish", "new", "--bin", w.video, "--title", "Rail Dawn, the cut", "--description-file", str(desc), "--tags", "comfyui, ai video", "--reason", "your QC passed the file"], transport=send) == 0
    out = json.loads(capsys.readouterr().out)
    pid = out["post"]
    assert pid.startswith("pub_") and out["change"]["description"] == 'drafted a youtube post for Video 1 file 1: "Rail Dawn, the cut"'

    assert cli.main(["--server", BASE, "--project", slug, "publish", "show", pid], transport=send) == 0
    text = capsys.readouterr().out
    assert "draft, not sent" in text and "private" in text and "Made in ComfyUI." in text and "tags: comfyui, ai video" in text and "altered or synthetic content: yes" in text
    assert "@halleen" in text

    # several fields in one call, one change each; the agent cannot go public; a long title is refused with the limit
    assert cli.main(["--server", BASE, "--project", slug, "publish", "set", pid, "--title", "Rail Dawn", "--synthetic", "off", "--kids", "off", "--privacy", "unlisted"], transport=send) == 0
    text = capsys.readouterr().out
    assert 'titled "Rail Dawn"' in text and "altered or synthetic content: no" in text and '"Rail Dawn" - unlisted' in text
    assert "no_change set_post_field changed nothing" in text  # kids was off already: no line, no card
    assert cli.main(["--server", BASE, "--project", slug, "publish", "set", pid, "--title", "x" * 101], transport=send) == 1
    assert "bad_value" in capsys.readouterr().err
    assert cli.main(["--server", BASE, "--project", slug, "publish", "set", pid], transport=send) == 2
    assert "at least one field" in capsys.readouterr().err

    assert cli.main(["--server", BASE, "--project", slug, "publish", "list", "--bin", w.image], transport=send) == 0
    assert "no posts" in capsys.readouterr().out
    assert cli.main(["--server", BASE, "--project", slug, "publish", "list"], transport=send) == 0
    assert "Rail Dawn" in capsys.readouterr().out

    assert cli.main(["--server", BASE, "--project", slug, "publish", "delete", pid, "--reason", "the user dropped the idea"], transport=send) == 0
    assert 'dropped the draft post "Rail Dawn"' in capsys.readouterr().out
    assert w.client.get(f"/api/projects/{slug}/posts").json() == []


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe needed for a video take")
def test_publish_upload_check_and_status(tmp_path, monkeypatch, capsys):
    from test_thm_server_posts import FakeYouTube, _creds

    w = seeded_world(tmp_path)
    clip = make_tiny_mp4(tmp_path / "media" / "cut.mp4")
    r = w.client.post(f"/api/projects/{w.slug}/uploads", files={"file": ("cut.mp4", clip.read_bytes(), "video/mp4")}, data={"bin": w.video})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    send = _transport(w.client)
    slug = w.slug
    st = w.app.state.thm
    st.youtube_env = _creds(tmp_path)
    _user_post(w.client, slug, "set_project_settings", {"youtube_channel": "UCabc"})
    with w.client:  # the lifespan: the upload task runs on the app's loop
        _run_publish_upload(w, send, slug, st, tmp_path, capsys)


def _run_publish_upload(w, send, slug, st, tmp_path, capsys):
    from test_thm_server_posts import FakeYouTube, _creds

    _user_post(w.client, slug, "start_session", {"notes": "post the cut", "mode": "auto"})
    assert cli.main(["--server", BASE, "--project", slug, "connect", "--harness", "claude-code"], transport=send) == 0
    capsys.readouterr()
    assert cli.main(["--server", BASE, "--project", slug, "--json", "publish", "new", "--bin", w.video, "--title", "Rail Dawn"], transport=send) == 0
    pid = json.loads(capsys.readouterr().out)["post"]
    # unconfigured (the client file gone): refused with the fix; the check says so without a project too
    st.youtube_env = {}
    assert cli.main(["--server", BASE, "publish", "check"], transport=send) == 0
    assert "not configured" in capsys.readouterr().out
    assert cli.main(["--server", BASE, "--project", slug, "publish", "upload", pid, "--requested"], transport=send) == 1
    assert "youtube_unconfigured" in capsys.readouterr().err
    st.youtube_env = _creds(tmp_path)
    st.youtube_transport = FakeYouTube()
    assert cli.main(["--server", BASE, "--project", slug, "publish", "check"], transport=send) == 0
    assert "ready to upload" in capsys.readouterr().out
    # a card by default
    assert cli.main(["--server", BASE, "--project", slug, "publish", "upload", pid, "--reason", "you passed it"], transport=send) == 0
    assert "card sent" in capsys.readouterr().out
    # requested: lands, and --wait follows it to the links
    assert cli.main(["--server", BASE, "--project", slug, "publish", "upload", pid, "--requested", "--wait", "--poll", "0.02"], transport=send) == 0
    text = capsys.readouterr().out
    assert "uploaded, private: https://youtu.be/vid123" in text and "studio.youtube.com/video/vid123/edit" in text
    assert cli.main(["--server", BASE, "--project", slug, "publish", "status", pid], transport=send) == 0
    assert "uploaded, private" in capsys.readouterr().out
    assert cli.main(["--server", BASE, "--project", slug, "publish", "list"], transport=send) == 0
    assert "on the platform" in capsys.readouterr().out
