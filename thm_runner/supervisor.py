"""Keep one THM agent running against this server, always.

    python thm_runner/supervisor.py                      # server on 127.0.0.1:8010
    python thm_runner/supervisor.py --server http://127.0.0.1:8011 --once   # one child, no restart

The agent is a headless Claude Code session (``claude -p``) handed the thm-agent skill as its
standing instructions and told to work every project on the server: connect to every waiting
or unattended session, then ``watch`` forever. This loop only keeps that child alive:

- waits for the server to answer ``/api/health`` before starting the child, and again after
  the server goes away (a restart of the server is not a restart of the agent);
- restarts the child when it exits, with a backoff that resets after a healthy hour;
- restarts the child when the server has not heard from any agent for ``--stale`` seconds
  while the child is running (a wedged harness);
- writes the child's stream to ``thm_runner/logs/agent-<stamp>.log``, and the pids to
  ``thm_runner/agent.pid`` and ``thm_runner/supervisor.pid``: a new supervisor stops the
  previous one and its agent first, so ``start_server.ps1`` can start it every time.

Permissions are an allowlist, not a bypass: the child may run ``thm-agent/cli.py``, ffmpeg
and ffprobe, and read files; it may not edit or write files, and anything else it tries is
denied without a prompt (``--permission-mode dontAsk``). Nothing here reads a project.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
SKILL = ROOT / "thm-agent" / "skill" / "thm-agent" / "SKILL.md"
PID_FILE = HERE / "agent.pid"
OWN_PID_FILE = HERE / "supervisor.pid"
LOG_DIR = HERE / "logs"
PY = ROOT / "venv" / "Scripts" / "python.exe"

ALLOWED_TOOLS = [
    f"Bash({PY.as_posix()} thm-agent/cli.py *)",
    "Bash(venv/Scripts/python.exe thm-agent/cli.py *)",
    "Bash(python thm-agent/cli.py *)",
    "Bash(ffmpeg *)",
    "Bash(ffprobe *)",
    "Read",
    "Glob",
    "Grep",
    "Skill",
]
DISALLOWED_TOOLS = ["Edit", "Write", "MultiEdit", "NotebookEdit"]

PROMPT = (
    "You are the THM agent for this server, started by the supervisor; there is no other agent. "
    "Your standing instructions are the thm-agent skill in the system prompt (its sibling files are "
    "under thm-agent/skill/thm-agent/: read them when the skill says to). The CLI is "
    "`{py} thm-agent/cli.py --server {server} ...`. Start with `sessions`, connect to every session "
    "that is waiting or whose agent is gone, read each project's inbox and notes, do the work owed, "
    "then `watch` with no --project. A quiet return from watch is not an event: run it again at "
    "once, forever. Never exit, never ask whether to keep going, never write to the THM code."
)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def server_up(server: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(f"{server}/api/health", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def agent_idle_s(server: str) -> float | None:
    """Seconds since the server last heard from any agent; None when unknown or the server is away."""
    try:
        with urllib.request.urlopen(f"{server}/api/agent/status", timeout=3.0) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    idle = data.get("idle_s")
    return float(idle) if idle is not None else None


def claude_exe(explicit: str | None) -> str:
    exe = explicit or shutil.which("claude") or shutil.which("claude.exe")
    if not exe:
        raise SystemExit("claude CLI not found on PATH; pass --claude <path>")
    return exe


def child_command(args, server: str) -> list[str]:
    py = PY.as_posix() if PY.is_file() else "python"
    cmd = [
        claude_exe(args.claude),
        "-p", PROMPT.format(py=py, server=server),
        "--permission-mode", "dontAsk",
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DISALLOWED_TOOLS,
        "--append-system-prompt-file", str(SKILL),
        "--output-format", "stream-json",
        "--verbose",
        "--name", "thm-agent",
    ]
    if args.model:
        cmd += ["--model", args.model]
    return cmd


def start_child(args, server: str) -> tuple[subprocess.Popen, Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"agent-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", THM_SERVER=server)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    out = open(log_path, "ab")
    proc = subprocess.Popen(
        child_command(args, server), cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        env=env, creationflags=flags,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    log(f"agent started (pid {proc.pid}) -> {log_path.name}")
    return proc, log_path


def stop_child(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def _kill_pid_file(path: Path, what: str) -> None:
    if not path.is_file():
        return
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        pid = 0
    if pid and pid != os.getpid():
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        log(f"stopped previous {what} (pid {pid})")
    path.unlink(missing_ok=True)


def stop_previous() -> None:
    """One supervisor per server: the previous one and its agent make way for this one."""
    _kill_pid_file(OWN_PID_FILE, "supervisor")
    _kill_pid_file(PID_FILE, "agent")
    OWN_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="keep one THM agent running")
    ap.add_argument("--server", default=os.environ.get("THM_SERVER") or "http://127.0.0.1:8010")
    ap.add_argument("--claude", default=None, help="path to the claude CLI (default: on PATH)")
    ap.add_argument("--model", default=None, help="model for the agent session (default: the CLI's)")
    ap.add_argument("--stale", type=float, default=900.0, help="restart the agent when the server has not heard from it for this many seconds (0 = never)")
    ap.add_argument("--poll", type=float, default=10.0, help="seconds between checks")
    ap.add_argument("--once", action="store_true", help="run one agent and exit when it does")
    args = ap.parse_args(argv)

    stop_previous()
    backoff = 10.0
    healthy_since: float | None = None
    proc: subprocess.Popen | None = None
    try:
        while True:
            if not server_up(args.server):
                if proc is not None and proc.poll() is None:
                    log("server away; agent keeps waiting")
                else:
                    log(f"waiting for the server at {args.server}")
                    time.sleep(args.poll)
                    continue
            if proc is None or proc.poll() is not None:
                if proc is not None:
                    log(f"agent exited with code {proc.returncode}")
                    if args.once:
                        return proc.returncode or 0
                    if healthy_since is not None and time.monotonic() - healthy_since > 3600:
                        backoff = 10.0
                    log(f"restarting in {backoff:.0f}s")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 300.0)
                proc, _ = start_child(args, args.server)
                healthy_since = time.monotonic()
            elif args.stale and server_up(args.server):
                idle = agent_idle_s(args.server)
                running_for = time.monotonic() - (healthy_since or time.monotonic())
                # a child that has never called the server is given the stale window from its start
                if running_for > args.stale and (idle is None or idle > args.stale):
                    log(f"no agent call for {int(idle or running_for)}s: restarting the agent")
                    stop_child(proc)
                    continue
            time.sleep(args.poll)
    except KeyboardInterrupt:
        log("stopping")
        return 0
    finally:
        if proc is not None:
            stop_child(proc)
        PID_FILE.unlink(missing_ok=True)
        OWN_PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
