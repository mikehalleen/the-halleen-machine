"""``create --audio``: the project and the song it is built from, in one harness-first call,
before any session exists; and the gate that closes again once a project has had one."""

import json
import sys
from pathlib import Path

import pytest

from thm_server_fixtures import make_tiny_audio, seeded_world

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
import cli  # noqa: E402

BASE = "http://thm.test"
AGENT = {"x-thm-actor": "agent"}


def _transport(client):
    def send(method, url, body, headers):
        assert url.startswith(BASE)
        r = client.request(method, url[len(BASE):], content=body, headers=headers)
        return r.status_code, r.content, dict(r.headers)

    return send


def test_create_with_audio_lands_the_song_in_a_tagged_music_bin(tmp_path, monkeypatch, capsys):
    w = seeded_world(tmp_path)
    song = make_tiny_audio(tmp_path / "neon.mp3", seconds=3.0)
    if song is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    rc = cli.main(["--server", BASE, "--json", "create", "--name", "Neon run", "--brief", "a run cut to the song", "--audio", str(song)], transport=_transport(w.client))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    slug = out["slug"]
    media = out["audio"]["media"]
    assert media["type"] == "audio" and abs(media["duration_s"] - 3.0) < 0.2 and media["origin"]["note"] == "the song the project is built from"
    music = out["audio"]["bin"]
    assert music["name"] == "Music" and music["tags"] == ["music"] and music["media"] == [media["id"]] and music["workflow"] == ""
    detail = w.client.get(f"/api/projects/{slug}").json()
    assert detail["brief"] == "a run cut to the song" and detail["agent_session"] is None
    assert [b["name"] for b in detail["bins"]] == ["Music"] and detail["bins"][0]["media_types"] == ["audio"]
    # the landing is on the record as the agent's, with no session to gate it
    changes = w.client.get(f"/api/projects/{slug}/changes").json()
    assert [(c["command"], c["actor"]) for c in changes] == [("create_bin", "agent"), ("add_media_to_bin", "agent")]
    # the human line names the song and the next step
    rc = cli.main(["--server", BASE, "create", "--name", "Neon run 2", "--audio", str(song)], transport=_transport(w.client))
    text = capsys.readouterr().out
    assert rc == 0 and "song m_" in text and "tool audio beats" in text


def test_agent_upload_gate_opens_only_before_the_first_session(tmp_path):
    w = seeded_world(tmp_path)
    song = make_tiny_audio(tmp_path / "bed.wav")
    if song is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    url = f"/api/projects/{w.slug}/uploads"
    # never had a session: the agent may upload (harness-first), and tags land on a minted bin
    with open(song, "rb") as f:
        r = w.client.post(url, files={"file": ("bed.wav", f, "audio/wav")}, data={"bin_name": "Beds", "tags": "music, bed"}, headers=AGENT)
    assert r.status_code == 200, r.text
    assert r.json()["bin"]["tags"] == ["music", "bed"]
    # tags do not touch an existing bin
    with open(song, "rb") as f:
        r = w.client.post(url, files={"file": ("bed2.wav", f, "audio/wav")}, data={"bin_name": "Beds", "tags": "other"}, headers=AGENT)
    assert r.status_code == 200 and r.json()["bin"]["tags"] == ["music", "bed"]
    # a session that has ended: the agent is refused until it connects to a live one
    w.client.post(f"/api/projects/{w.slug}/commands/start_session", json={"notes": "go", "mode": "auto"})
    w.client.post(f"/api/projects/{w.slug}/commands/end_session", json={}, headers={**AGENT, "x-thm-session": w.registry.get(w.slug).agent_session.id}) if False else None
    project = w.registry.get(w.slug)
    project.agent_session.status = "ended"
    w.store.save(project)
    w.registry.invalidate(w.slug)
    with open(song, "rb") as f:
        r = w.client.post(url, files={"file": ("bed3.wav", f, "audio/wav")}, data={"bin_name": "Beds"}, headers=AGENT)
    assert r.status_code == 409 and r.json()["code"] == "no_session"
    # the user's own upload never needed one
    with open(song, "rb") as f:
        r = w.client.post(url, files={"file": ("bed4.wav", f, "audio/wav")}, data={"bin_name": "Beds"})
    assert r.status_code == 200
