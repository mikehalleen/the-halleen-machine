"""The agent's ``sequence`` verbs, driven through the CLI against the TestClient."""

import json
import sys
from pathlib import Path

from thm_server_fixtures import seeded_world

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
import cli  # noqa: E402

BASE = "http://thm.test"


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


def test_sequence_verbs_round_trip(tmp_path, monkeypatch, capsys):
    w = seeded_world(tmp_path)
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path / "state")
    send = _transport(w.client)
    slug = w.slug
    _user_post(w.client, slug, "start_session", {"notes": "cut it together", "mode": "auto"})
    assert cli.main(["--server", BASE, "--project", slug, "connect", "--harness", "claude-code"], transport=send) == 0
    capsys.readouterr()

    # nothing yet
    assert cli.main(["--server", BASE, "--project", slug, "sequence"], transport=send) == 0
    assert "no sequences" in capsys.readouterr().out

    assert cli.main(["--server", BASE, "--project", slug, "--json", "sequence", "new", "--name", "Rough cut", "--reason", "the cut the brief asks for"], transport=send) == 0
    out = json.loads(capsys.readouterr().out)
    sid = out["sequence"]
    assert sid and out["change"]["description"] == "created sequence Rough cut"

    assert cli.main(["--server", BASE, "--project", slug, "sequence", "add", sid, w.image, w.video, w.mara, "--reason", "in script order"], transport=send) == 0
    assert "added Image 1, Video 1, Mara to Rough cut" in capsys.readouterr().out
    assert cli.main(["--server", BASE, "--project", slug, "sequence", "order", sid, w.mara, w.image, w.video], transport=send) == 0
    assert "moved Mara to #1" in capsys.readouterr().out
    assert cli.main(["--server", BASE, "--project", slug, "sequence", "still", sid, w.image, "4.5"], transport=send) == 0
    capsys.readouterr()
    assert cli.main(["--server", BASE, "--project", slug, "sequence", "set", sid, w.mara, "--props", json.dumps({"sound": "room tone", "gain_db": -6})], transport=send) == 1
    assert "summary_required" in capsys.readouterr().err
    assert cli.main(["--server", BASE, "--project", slug, "sequence", "set", sid, w.mara, "--props", json.dumps({"sound": "room tone", "gain_db": -6, "summary": "room tone, -6 dB"})], transport=send) == 0
    assert "agent settings: sound room tone, gain_db -6" in capsys.readouterr().out

    assert cli.main(["--server", BASE, "--project", slug, "sequence", "show", sid], transport=send) == 0
    text = capsys.readouterr().out
    lines = text.splitlines()
    assert lines[0].startswith("Rough cut (") and "3 items" in lines[0] and "1 without a take" in lines[0]
    assert lines[1].startswith("#1") and '"Mara"' in lines[1] and "[export only: room tone, -6 dB; sound=room tone, gain_db=-6]" in lines[1]
    assert lines[2].startswith("#2") and "4.5s" in lines[2]
    assert lines[3].startswith("#3") and "no take: title card" in lines[3]
    assert "last export: none yet" in text

    # the user's drag in the app is an ordinary change the agent reads back
    _user_post(w.client, slug, "reorder_sequence", {"sequence": sid, "order": [w.video, w.mara, w.image]})
    assert cli.main(["--server", BASE, "--project", slug, "--json", "sequence", "show", sid], transport=send) == 0
    seq = json.loads(capsys.readouterr().out)
    assert [i["bin"] for i in seq["items"]] == [w.video, w.mara, w.image]
    assert seq["items"][1]["agent"] == {"sound": "room tone", "gain_db": -6, "summary": "room tone, -6 dB"}

    # export status reads idle before any export; the export itself needs ffmpeg and is covered server-side
    assert cli.main(["--server", BASE, "--project", slug, "sequence", "status", sid], transport=send) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "idle"

    assert cli.main(["--server", BASE, "--project", slug, "sequence", "remove", sid, w.image], transport=send) == 0
    assert "removed Image 1 from Rough cut" in capsys.readouterr().out
    # a bin in the cut cannot take the exports; one just removed from it can
    assert cli.main(["--server", BASE, "--project", slug, "sequence", "target", sid, w.video], transport=send) == 1
    capsys.readouterr()
    assert cli.main(["--server", BASE, "--project", slug, "sequence", "target", sid, w.image], transport=send) == 0
    assert "exports land in Image 1" in capsys.readouterr().out
    assert cli.main(["--server", BASE, "--project", slug, "sequence", "delete", sid], transport=send) == 0
    assert "kept bin Image 1" in capsys.readouterr().out
