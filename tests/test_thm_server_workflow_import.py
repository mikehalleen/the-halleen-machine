"""The import routes: runs read from ComfyUI's history, files written into the kind and samples folders."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from thm_core import BackupSettings, CoreSettings
from thm_server import create_app

FIXTURES = Path(__file__).parent / "fixtures"


def _history() -> dict:
    run = json.loads((FIXTURES / "comfy_run_textgen_enhancer.json").read_text(encoding="utf-8"))
    mine = {"prompt": [3, "aaaa1111", run["api"], {"extra_pnginfo": {"workflow": run["ui"]}, "create_time": 2000}, ["9"]],
            "outputs": {"9": {"images": [{"filename": "moss_00001_.png", "type": "output"}]}}, "status": {"status_str": "success"}}
    thm = {"prompt": [2, "bbbb2222", run["api"], {"machine_ui_project": "secret-project", "machine_ui_label": "gen-1", "create_time": 3000}, ["9"]],
           "outputs": {}, "status": {"status_str": "success"}}
    return {"aaaa1111": mine, "bbbb2222": thm}


def make_client(tmp_path: Path, history: dict | None = None) -> tuple[TestClient, Path]:
    wdir = tmp_path / "workflows"
    wdir.mkdir(parents=True)
    settings = CoreSettings(projects_dir=tmp_path / "p", workflows_dir=wdir, backups=BackupSettings(throttle_seconds=0))
    app = create_app(settings, web_dist=tmp_path / "nodist")
    hist = _history() if history is None else history

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/history":
            return httpx.Response(200, json=hist)
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={pid: hist[pid]} if pid in hist else {})
        return httpx.Response(404, json={})

    app.state.thm.comfy_transport = httpx.MockTransport(handler)
    return TestClient(app), tmp_path


def test_runs_list_leaves_out_thm_renders(tmp_path):
    client, _ = make_client(tmp_path)
    runs = client.get("/api/workflows/comfy-runs").json()
    assert [r["id"] for r in runs] == ["aaaa1111"]
    assert runs[0]["has_ui"] and runs[0]["outputs"] == ["moss_00001_.png"] and runs[0]["kind"] == "image"




def test_refusals_carry_codes(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.post("/api/workflows/import", json={"name": "x", "run": "bbbb2222"}).json()["code"] == "thm_run"
    assert client.post("/api/workflows/import", json={"name": "x", "run": "zzz"}).json()["code"] == "unknown_run"
    assert client.post("/api/workflows/import", json={"name": "../x"}).json()["code"] == "bad_name"
    empty, _ = make_client(tmp_path / "e", history={})
    assert empty.post("/api/workflows/import", json={"name": "x"}).json()["code"] == "no_runs"


def test_unreachable_comfy_is_a_502(tmp_path):
    client, _ = make_client(tmp_path)

    def down(request):
        raise httpx.ConnectError("refused")

    client.app.state.thm.comfy_transport = httpx.MockTransport(down)
    r = client.get("/api/workflows/comfy-runs")
    assert r.status_code == 502 and r.json()["code"] == "comfy_unreachable"
