"""Live progress tracking: phase labels, queue position, and the socket message sequence."""

from scripts import comfy_progress as cp
from thm_core.runner import read_status

GRAPH = {
    "1": {"class_type": "UNETLoader"},
    "2": {"class_type": "CLIPTextEncode"},
    "3": {"class_type": "KSamplerAdvanced"},
    "4": {"class_type": "KSamplerAdvanced"},
    "5": {"class_type": "VAEDecode"},
    "6": {"class_type": "SaveImage"},
    "7": {"class_type": "KSamplerSelect"},
}


def test_phase_labels():
    assert cp.phase_for_class("KSampler") == "sampling"
    assert cp.phase_for_class("SamplerCustomAdvanced") == "sampling"
    assert cp.phase_for_class("KSamplerSelect") != "sampling"
    assert cp.phase_for_class("UNETLoader") == "loading models"
    assert cp.phase_for_class("CheckpointLoaderSimple") == "loading models"
    assert cp.phase_for_class("LoadImage") == "loading inputs"
    assert cp.phase_for_class("CLIPTextEncode") == "encoding prompt"
    assert cp.phase_for_class("VAEDecodeTiled") == "decoding"
    assert cp.phase_for_class("VHS_VideoCombine") == "saving"
    assert cp.phase_for_class("SaveVideo") == "saving"
    assert cp.phase_for_class("ImageResizeKJ") == "image resize kj"
    assert cp.phase_for_class(None) == "generating"


def test_queue_position_counts_running_and_thm_entries():
    ours = {"machine_ui_label": "gen_x", "machine_ui_project": "p"}
    queue = {
        "queue_running": [[0, "r1", {}, {}, []]],
        "queue_pending": [
            [3, "p3", {}, ours, []],
            [1, "p1", {}, ours, []],
            [2, "p2", {}, {}, []],
        ],
    }
    assert cp.queue_position(queue, "r1") == (0, 0)
    assert cp.queue_position(queue, "p1") == (1, 0)
    assert cp.queue_position(queue, "p2") == (2, 1)
    assert cp.queue_position(queue, "p3") == (3, 1)
    assert cp.queue_position(queue, "nope") == (None, None)
    assert cp.queue_position({}, "p1") == (None, None)


def test_tracker_follows_the_message_sequence():
    seen = []
    t = cp.PromptTracker("http://comfy:8188", GRAPH, seen.append)
    t.prompt_id = "pid"
    assert t.passes == 2
    assert t.task_line() == "queued"

    t.state.update(queue_ahead=2, queue_ahead_ours=1)
    assert t.task_line() == "queued - 2 ahead (1 yours, 1 external)"
    t.state.update(queue_ahead=1, queue_ahead_ours=1)
    assert t.task_line() == "queued - 1 ahead (yours)"
    t.state.update(queue_ahead=1, queue_ahead_ours=0)
    assert t.task_line() == "queued - 1 ahead (external)"
    t.state.update(queue_ahead=0, queue_ahead_ours=0)
    assert t.task_line() == "queued - next up"

    t._apply({"type": "execution_start", "data": {"prompt_id": "pid"}})
    assert t.state["phase"] == "starting" and t.state["queue_ahead"] == 0

    t._apply({"type": "executing", "data": {"prompt_id": "pid", "node": "1"}})
    assert t.task_line() == "loading models"

    # another client's prompt is ignored
    t._apply({"type": "executing", "data": {"prompt_id": "other", "node": "6"}})
    assert t.task_line() == "loading models"

    t._apply({"type": "executing", "data": {"prompt_id": "pid", "node": "3"}})
    t._apply({"type": "progress", "data": {"prompt_id": "pid", "node": "3", "value": 5, "max": 20}})
    assert t.task_line() == "pass 1/2 - 5/20"
    assert t.state["progress_percent"] == 12.5

    t._apply({"type": "executing", "data": {"prompt_id": "pid", "node": "4"}})
    t._apply({"type": "progress", "data": {"prompt_id": "pid", "node": "4", "value": 10, "max": 20}})
    assert t.task_line() == "pass 2/2 - 10/20"
    assert t.state["progress_percent"] == 75.0

    t._apply({"type": "executing", "data": {"prompt_id": "pid", "node": "5"}})
    assert t.task_line() == "decoding"
    t._apply({"type": "progress", "data": {"prompt_id": "pid", "node": "5", "value": 2, "max": 4}})
    assert t.task_line() == "decoding 2/4"

    t._apply({"type": "executing", "data": {"prompt_id": "pid", "node": None}})
    assert t.task_line() == "finishing"

    phases = [s["phase"] for s in seen]
    assert phases[0] == "queued" or "starting" in phases
    assert "sampling" in phases and "finishing" in phases


def test_single_sampler_task_line_has_no_pass():
    t = cp.PromptTracker("http://comfy:8188", {"1": {"class_type": "KSampler"}})
    t.prompt_id = "pid"
    t._apply({"type": "executing", "data": {"prompt_id": "pid", "node": "1"}})
    t._apply({"type": "progress", "data": {"prompt_id": "pid", "node": "1", "value": 14, "max": 30}})
    assert t.task_line() == "sampling 14/30"
    assert t.state["pass_no"] == 1 and t.state["passes"] == 1


def test_status_file_round_trip(tmp_path):
    """The runner writes the tracker snapshot beside its own fields; the server reads it back."""
    import json

    t = cp.PromptTracker("http://comfy:8188", GRAPH)
    t.prompt_id = "pid"
    t._apply({"type": "executing", "data": {"prompt_id": "pid", "node": "3"}})
    t._apply({"type": "progress", "data": {"prompt_id": "pid", "node": "3", "value": 5, "max": 20}})
    snap = t.snapshot()
    data = {"pid": 1, "status": "running", "current_task": snap["task"], "progress_percent": f"{snap['progress_percent']:.1f}"}
    data.update({k: v for k, v in snap.items() if k not in data})
    p = tmp_path / "status.json"
    p.write_text(json.dumps(data), encoding="utf-8")

    st = read_status(p)
    assert st is not None and st.task == "pass 1/2 - 5/20"
    assert st.live is not None
    assert (st.live.phase, st.live.step, st.live.steps, st.live.pass_no, st.live.passes) == ("sampling", 5, 20, 1, 2)
    assert st.live.queue_ahead == 0

    p.write_text(json.dumps({"pid": 1, "status": "running", "current_task": "Preparing graph"}), encoding="utf-8")
    assert read_status(p).live is None


# -- the wait: execution-time timeout, lost prompts, heartbeat ---------------------


class _Clock:
    """A fake time.time / time.sleep pair so a wait can be driven without real seconds passing."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _comfy(queue_by_time, history_after=None):
    """A GET that answers /queue from ``queue_by_time`` (the first entry whose start <= now) and
    /history/<pid> with the entry once ``history_after`` has passed."""

    def get(url, timeout=0, clock=None):
        now = clock.now
        if url.endswith("/queue"):
            payload = {}
            for start, q in queue_by_time:
                if now >= start:
                    payload = q
            return _Resp(payload)
        if "/history/" in url:
            if history_after is not None and now >= history_after:
                return _Resp({"pid": {"outputs": {}, "status": {"status_str": "success"}}})
            return _Resp({})
        return _Resp({}, 404)

    return get


def _run_wait(tracker, clock, get, monkeypatch, **kw):
    monkeypatch.setattr(cp.time, "time", clock.time)
    monkeypatch.setattr(cp.time, "sleep", clock.sleep)
    return tracker.wait("pid", 100.0, get=lambda url, timeout=0: get(url, timeout, clock=clock), **kw)


def test_queue_state():
    queue = {"queue_running": [[0, "r1", {}, {}, []]], "queue_pending": [[1, "p1", {}, {}, []]]}
    assert cp.queue_state(queue, "r1") == "running"
    assert cp.queue_state(queue, "p1") == "pending"
    assert cp.queue_state(queue, "zz") is None
    assert cp.queue_state({}, "r1") is None


def test_timeout_counts_from_execution_start_not_submission(monkeypatch):
    """Pending behind others for longer than the timeout is fine; the clock starts when ComfyUI
    runs the prompt. Here: 400s in the queue, then it runs and finishes 50s later (limit 100s)."""
    clock = _Clock()
    pending = {"queue_running": [[0, "other", {}, {}, []]], "queue_pending": [[1, "pid", {}, {}, []]]}
    running = {"queue_running": [[0, "pid", {}, {}, []]], "queue_pending": []}
    get = _comfy([(0, pending), (1400, running)], history_after=1450)
    t = cp.PromptTracker("http://comfy:8188", {"1": {"class_type": "KSampler"}})
    entry = _run_wait(t, clock, get, monkeypatch)
    assert entry is not None and t.gave_up is None
    assert t.exec_started_at is not None and 1400 <= t.exec_started_at < 1410


def test_timeout_fires_after_the_limit_of_execution(monkeypatch):
    clock = _Clock()
    pending = {"queue_running": [[0, "other", {}, {}, []]], "queue_pending": [[1, "pid", {}, {}, []]]}
    running = {"queue_running": [[0, "pid", {}, {}, []]], "queue_pending": []}
    get = _comfy([(0, pending), (1200, running)], history_after=None)
    t = cp.PromptTracker("http://comfy:8188", {"1": {"class_type": "KSampler"}})
    assert _run_wait(t, clock, get, monkeypatch) is None
    assert t.gave_up == "timed out after 100s of execution"
    assert 1300 <= clock.now < 1310  # not 1100: the 200s in the queue did not count


def test_socket_execution_start_starts_the_clock_too():
    t = cp.PromptTracker("http://comfy:8188", {"1": {"class_type": "KSampler"}})
    t.prompt_id = "pid"
    assert t.exec_started_at is None
    t._apply({"type": "execution_start", "data": {"prompt_id": "pid"}})
    first = t.exec_started_at
    assert first is not None
    t._apply({"type": "executing", "data": {"prompt_id": "pid", "node": "1"}})
    assert t.exec_started_at == first  # set once


def test_a_prompt_in_neither_queue_nor_history_is_given_up_on(monkeypatch):
    clock = _Clock()
    get = _comfy([(0, {"queue_running": [], "queue_pending": []})], history_after=None)
    t = cp.PromptTracker("http://comfy:8188", {"1": {"class_type": "KSampler"}})
    assert _run_wait(t, clock, get, monkeypatch, lost_after_s=30.0) is None
    assert t.gave_up is not None and "dropped by ComfyUI" in t.gave_up
    assert 1030 <= clock.now < 1040


def test_heartbeat_refreshes_an_unchanged_snapshot(monkeypatch):
    clock = _Clock()
    pending = {"queue_running": [[0, "other", {}, {}, []]], "queue_pending": [[1, "pid", {}, {}, []]]}
    get = _comfy([(0, pending)], history_after=1250)
    seen = []
    t = cp.PromptTracker("http://comfy:8188", {"1": {"class_type": "KSampler"}}, lambda snap: seen.append((clock.now, snap)))
    assert _run_wait(t, clock, get, monkeypatch, heartbeat_s=60.0) is not None
    lines = [(round(when), s["task"]) for when, s in seen]
    assert all(task == "queued - 1 ahead (external)" for _, task in lines[1:])
    beats = [when for when, _ in lines]
    assert len(beats) >= 4 and all(59 <= b - a <= 62 for a, b in zip(beats[1:], beats[2:]))
    assert all(s["prompt_id"] == "pid" for _, s in seen)
