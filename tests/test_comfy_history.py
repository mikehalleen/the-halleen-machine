"""History-based output discovery (THM phase 2): extractor, error detail, poller."""

import json
from pathlib import Path

import pytest

from scripts import comfy_history as ch

FIXTURES = Path(__file__).resolve().parent / "fixtures"
IMAGE_FIXTURE = FIXTURES / "history_image.json"
VIDEO_FIXTURE = FIXTURES / "history_video.json"
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def _entry(path: Path) -> dict:
    if not path.is_file():
        pytest.skip(f"fixture not captured yet: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return next(iter(payload.values()))


def _expected_output_paths(entry: dict, root: str) -> set:
    import os

    paths = set()
    for node_out in entry["outputs"].values():
        for key in ch.HISTORY_MEDIA_KEYS:
            for item in node_out.get(key) or []:
                if item.get("type") == "output" and item.get("filename"):
                    paths.add(os.path.normcase(os.path.abspath(os.path.join(root, item.get("subfolder") or "", item["filename"]))))
    return paths


def test_image_fixture_outputs():
    import os

    entry = _entry(IMAGE_FIXTURE)
    root = "D:/ComfyUI/output"
    outs = ch.extract_history_outputs(entry, root, prompt_id="p1", label="seq|kf")
    assert len(outs) >= 1
    abs_root = os.path.normcase(os.path.abspath(root))
    for o in outs:
        assert os.path.normcase(o["path"]).startswith(abs_root)
        assert o["path"].lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        assert o["node_id"] in entry["outputs"]
        assert (o["prompt_id"], o["label"]) == ("p1", "seq|kf")
    assert {os.path.normcase(o["path"]) for o in outs} == _expected_output_paths(entry, root)


def test_video_fixture_outputs():
    import os

    entry = _entry(VIDEO_FIXTURE)
    root = "D:/ComfyUI/output"
    outs = ch.extract_history_outputs(entry, root)
    assert any(o["path"].lower().endswith(VIDEO_EXTS) for o in outs)
    assert {os.path.normcase(o["path"]) for o in outs} == _expected_output_paths(entry, root)


def test_type_filtering_and_all_media_keys(tmp_path):
    import os

    root = str(tmp_path)
    entry = {
        "outputs": {
            "5": {"images": [
                {"filename": "a.png", "subfolder": "p/s", "type": "output"},
                {"filename": "b.png", "subfolder": "", "type": "temp"},
            ]},
            "9": {"gifs": [{"filename": "v.mp4", "subfolder": "p/s", "type": "output", "format": "video/h264-mp4"}]},
            "12": {"videos": [{"filename": "w.webm", "subfolder": "", "type": "output"}]},
            "13": {"text": ["x"]},
            "14": {"images": [{"filename": "c.png", "subfolder": "", "type": "input"}]},
            "6": {"images": [{"filename": "a.png", "subfolder": "p/s", "type": "output"}]},
        },
        "status": {"status_str": "success", "completed": True, "messages": []},
    }
    outs = ch.extract_history_outputs(entry, root, prompt_id="pid", label="L")
    assert [o["node_id"] for o in outs] == ["5", "9", "12"]
    assert [o["path"] for o in outs] == [
        os.path.abspath(os.path.join(root, "p/s", "a.png")),
        os.path.abspath(os.path.join(root, "p/s", "v.mp4")),
        os.path.abspath(os.path.join(root, "", "w.webm")),
    ]
    assert all((o["prompt_id"], o["label"]) == ("pid", "L") for o in outs)

    folder = os.path.join(root, "p", "s")
    assert ch.outputs_in_folder(outs, folder, (".png",)) == [outs[0]["path"]]
    assert ch.outputs_in_folder(outs, folder, VIDEO_EXTS, prefix="V.") == [outs[1]["path"]]
    assert ch.outputs_in_folder(outs, folder, VIDEO_EXTS, prefix="nope") == []
    assert ch.outputs_in_folder(outs, root, VIDEO_EXTS) == [outs[2]["path"]]


def test_extract_handles_empty():
    assert ch.extract_history_outputs(None, "x") == []
    assert ch.extract_history_outputs({"outputs": None}, "x") == []
    assert ch.extract_history_outputs({"outputs": {"1": {"images": [{"type": "output"}]}}}, "x") == []


def test_history_error_detail():
    err = {
        "status": {
            "status_str": "error",
            "completed": False,
            "messages": [
                ["execution_start", {"prompt_id": "p"}],
                ["execution_error", {"prompt_id": "p", "exception_message": "boom", "node_type": "KSampler"}],
            ],
        }
    }
    assert "boom" in ch.history_error_detail(err)
    assert ch.history_error_detail({"status": {"status_str": "success"}}) is None
    assert ch.history_error_detail(None) is None
    assert ch.history_error_detail({"status": {"status_str": "error", "messages": []}}) == "error"


def test_history_exec_seconds_is_comfys_own_clock():
    """execution_start to the end mark, in seconds; the queue wait before it is not counted."""
    entry = {
        "status": {
            "status_str": "success",
            "messages": [
                ["execution_start", {"prompt_id": "p", "timestamp": 1_700_000_000_000}],
                ["execution_cached", {"prompt_id": "p", "nodes": [], "timestamp": 1_700_000_000_100}],
                ["execution_success", {"prompt_id": "p", "timestamp": 1_700_000_042_300}],
            ],
        }
    }
    assert ch.history_exec_seconds(entry) == 42.3
    # a failed run still has a duration: start to the error mark
    failed = {"status": {"status_str": "error", "messages": [
        ["execution_start", {"prompt_id": "p", "timestamp": 1000}],
        ["execution_error", {"prompt_id": "p", "exception_message": "boom", "timestamp": 6500}],
    ]}}
    assert ch.history_exec_seconds(failed) == 5.5
    # no marks, an older ComfyUI without timestamps, or nonsense: None rather than a guess
    assert ch.history_exec_seconds({"status": {"status_str": "success", "messages": []}}) is None
    assert ch.history_exec_seconds({"status": {"messages": [["execution_start", {"prompt_id": "p"}], ["execution_success", {"prompt_id": "p"}]]}}) is None
    assert ch.history_exec_seconds({"status": {"messages": [["execution_start", {"timestamp": 5000}], ["execution_success", {"timestamp": 1000}]]}}) is None
    assert ch.history_exec_seconds(None) is None
    assert ch.history_exec_seconds({"status": "weird"}) is None


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_poll_history_entry():
    assert ch.poll_history_entry("http://x", "pid", 0, poll_s=0, get=lambda url, timeout: _Resp(404)) is None

    calls = []

    def ok(url, timeout):
        calls.append(url)
        return _Resp(200, {"pid": {"outputs": {}}})

    assert ch.poll_history_entry("http://x/", "pid", 5, poll_s=0, get=ok) == {"outputs": {}}
    assert calls == ["http://x/history/pid"]

    flaky = iter([None, _Resp(200, {"pid": {"outputs": {"1": {}}}})])

    def flaky_get(url, timeout):
        r = next(flaky)
        if r is None:
            raise ConnectionError("down")
        return r

    assert ch.poll_history_entry("http://x", "pid", 5, poll_s=0, get=flaky_get) == {"outputs": {"1": {}}}
