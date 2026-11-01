"""thm_server: a file that carries its render's graph gets its record back -- on upload, on a
recipe read, on a bin's generation list -- and a failed generation the file turns out to be
the output of is completed rather than a plain upload left beside it."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thm_core import Generation, MediaOrigin, adopt_media
from thm_core.commands import AddMediaToBin
from thm_core import execute
from thm_core_fixtures import USER
from thm_server_fixtures import seeded_world, write_png

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_thm_core_provenance import mp4_with_graph, png_with_graph, rendered_graph, upload_name  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "thm-agent"))
from thm_client import ThmClient  # noqa: E402

BASE = "http://thm.test"


def _transport(client: TestClient):
    def send(method, url, body, headers):
        r = client.request(method, url[len(BASE):], content=body, headers=headers)
        return r.status_code, r.content, dict(r.headers)

    return send


def _upload(world, path: Path, bin_id: str) -> dict:
    with open(path, "rb") as f:
        r = world.client.post(f"/api/projects/{world.slug}/uploads", files={"file": (path.name, f, "application/octet-stream")}, data={"bin": bin_id, "note": "fetched by hand"})
    assert r.status_code == 200, r.text
    return r.json()


def _failed(world, seed: int) -> Generation:
    """A render of Video 1 the server gave up on (a timeout), the way jobs.py leaves it."""
    gen = Generation(id="g_timedout", bin=world.video, kind="video", ts="2026-09-15T10:00:00Z", workflow="vid.json",
                     prompt_typed=".mara at the rail", prompt_submitted=".mara at the rail",
                     params={"width": 1280, "height": 720, "length_s": 5.0, "seed": seed}, status="failed",
                     warnings=[{"kind": "runner", "handle": "", "detail": "prompt x timed out after 3600s"}])
    world.store.append_generation(world.project, gen)
    return gen


@pytest.fixture
def world(tmp_path):
    w = seeded_world(tmp_path)
    return w


def test_upload_of_a_rendered_output_lands_as_a_take_with_its_recipe(world):
    """A PNG straight out of ComfyUI, uploaded into Image 1: the record is rebuilt from its graph."""
    g = rendered_graph({"10": upload_name(world.media_dir / "a.png")}, seed=4242, prompt="mara on the rail")
    png = png_with_graph(world.media_dir / "recovered.png", g)
    out = _upload(world, png, world.image)
    media = out["media"]
    assert media["origin"]["type"] == "generation" and media["origin"]["note"] == "fetched by hand" and media["take"] == 2
    gen_id = media["origin"]["generation"]

    rec = world.client.get(f"/api/projects/{world.slug}/media/{media['id']}/recipe").json()
    assert rec["found"] and rec["recovered"] and rec["generation"] == gen_id and rec["workflow"] == "img.json" and rec["seed"] == 4242
    assert rec["references"] == [world.m1] and rec["handles"] == ["mara"] and rec["prompt"] == "mara on the rail"

    gen = world.client.get(f"/api/projects/{world.slug}/generations/{gen_id}").json()
    assert gen["status"] == "done" and gen["outputs"] == [media["id"]] and gen["recovered_from"] == media["path"]
    listed = world.client.get(f"/api/projects/{world.slug}/generations", params={"bin": world.image}).json()
    assert [g["id"] for g in listed][-1] == gen_id

    # recall works on it like on any take
    r = world.client.post(f"/api/projects/{world.slug}/commands/recall_generation", json={
        "bin": world.image, "from_media": media["id"], "workflow": rec["workflow"], "prompt": rec["prompt"], "negative": rec["negative"],
        "references": [{"media": m, "handle": h} for m, h in zip(rec["references"], rec["handles"])], "loras": rec["loras"],
    })
    assert r.status_code == 200, r.text
    detail = world.client.get(f"/api/projects/{world.slug}/bins/{world.image}").json()
    assert detail["bin"]["prompt"] == "mara on the rail" and [x["handle"] for x in detail["references"]] == ["mara"]


def test_upload_completes_the_failed_generation_it_came_from(world):
    _failed(world, seed=777)
    g = rendered_graph({}, seed=777, prompt=".mara at the rail")
    png = png_with_graph(world.media_dir / "late.png", g)
    out = _upload(world, png, world.video)
    media = out["media"]
    assert media["origin"]["generation"] == "g_timedout" and media["take"] == 1
    gen = world.client.get(f"/api/projects/{world.slug}/generations/g_timedout").json()
    assert gen["status"] == "done" and gen["outputs"] == [media["id"]] and gen["warnings"][-1]["kind"] == "recovered"
    assert gen["prompt_typed"] == ".mara at the rail" and gen["params"]["seed"] == 777
    rec = world.client.get(f"/api/projects/{world.slug}/media/{media['id']}/recipe").json()
    assert rec["found"] and rec["generation"] == "g_timedout" and rec["recovered"]
    # the agent's history reads it as the take it is, and says it was recovered
    agent = ThmClient(BASE, transport=_transport(world.client))
    rows = agent.history(world.slug, world.video)
    assert rows[0]["generation"] == "g_timedout" and rows[0]["status"] == "done" and rows[0]["take"] == 1 and rows[0]["recovered"]


def test_plain_media_already_in_the_project_recovers_on_read(world):
    """Media that landed as an upload before recovery existed: the recipe read (or the bin's
    generation list) rebuilds the record from the file. Media with no graph stays plain."""
    _failed(world, seed=31)
    g = rendered_graph({}, seed=31, prompt=".mara at the rail")
    png = png_with_graph(world.media_dir / "old.png", g)
    project = world.store.load(world.slug)
    media = adopt_media(project, png, origin=MediaOrigin(type="upload", bin=world.video))
    execute(world.store, project, AddMediaToBin(bin=world.video, media=media.id), USER, env=world.env)
    world.registry.invalidate(world.slug)
    assert world.client.get(f"/api/projects/{world.slug}/media/{media.id}").json()["origin"]["type"] == "upload"

    rec = world.client.get(f"/api/projects/{world.slug}/media/{media.id}/recipe").json()
    assert rec["found"] and rec["generation"] == "g_timedout" and rec["recovered"]
    assert world.client.get(f"/api/projects/{world.slug}/media/{media.id}").json()["origin"] == {
        "type": "generation", "generation": "g_timedout", "bin": world.video, "source": None, "start_s": None, "end_s": None, "at_s": None, "note": None,
    }
    saved = json.loads(world.store.project_path(world.slug).read_text(encoding="utf-8"))
    assert saved["media_by_id"][media.id]["origin"]["generation"] == "g_timedout" and saved["media_by_id"][media.id]["take"] == 1

    # the seeded uploads carry no graph: nothing found, and reading again is free
    assert world.client.get(f"/api/projects/{world.slug}/media/{world.m1}/recipe").json()["found"] is False
    assert world.client.get(f"/api/projects/{world.slug}/media/{world.m1}").json()["origin"]["type"] == "upload"


def test_bin_generation_list_sweeps_plain_media(world):
    g = rendered_graph({}, seed=5, prompt="x")
    png = png_with_graph(world.media_dir / "swept.png", g)
    project = world.store.load(world.slug)
    media = adopt_media(project, png, origin=MediaOrigin(type="upload", bin=world.image))
    execute(world.store, project, AddMediaToBin(bin=world.image, media=media.id), USER, env=world.env)
    world.registry.invalidate(world.slug)
    listed = world.client.get(f"/api/projects/{world.slug}/generations", params={"bin": world.image}).json()
    assert listed[-1]["outputs"] == [media.id] and listed[-1]["recovered_from"] == media.path and listed[-1]["params"]["seed"] == 5
    assert world.client.get(f"/api/projects/{world.slug}/media/{media.id}").json()["take"] == 2


def test_upload_of_a_video_output(world):
    _failed(world, seed=910)
    g = rendered_graph({}, seed=910, prompt=".mara at the rail", frames=41, fps=8.0)
    mp4 = mp4_with_graph(world.media_dir / "late.mp4", g)
    if mp4 is None:
        pytest.skip("ffmpeg not on PATH")
    out = _upload(world, mp4, world.video)
    assert out["media"]["origin"]["generation"] == "g_timedout" and out["media"]["take"] == 1
    gen = world.client.get(f"/api/projects/{world.slug}/generations/g_timedout").json()
    assert gen["status"] == "done" and gen["params"]["fps"] == 8.0 and gen["params"]["length_s"] == 5.0
