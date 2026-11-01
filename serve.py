"""Start the THM server (FastAPI + uvicorn).

    python serve.py                      # 0.0.0.0:8010, serves web/dist when built
    python serve.py --reload             # dev: restart on thm_server/ or thm_core/ changes
    python serve.py --projects-dir X     # use another projects folder

The 1.x Gradio app (run.py, port 7860) is untouched and can run alongside.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Backstop for Ctrl+C: uvicorn's graceful shutdown waits for in-flight responses and cancels
# (with a traceback each) whatever is still running when this expires. The SSE streams every
# browser tab holds (routes/events.py) never end on their own, so ``Server.shutdown`` below
# closes the event bus first and they finish cleanly; this cap only bounds anything else that
# was mid-flight when Ctrl+C landed.
SHUTDOWN_GRACE_SECONDS = 3


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="THM server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--reload", action="store_true", help="restart on source changes (dev)")
    ap.add_argument("--projects-dir", default=None, help="override [paths].workspace from config.toml")
    args = ap.parse_args(argv)

    if args.projects_dir:
        os.environ["THM_PROJECTS_DIR"] = str(Path(args.projects_dir).resolve())

    print(f"[THM] server on http://127.0.0.1:{args.port}  (LAN: http://{lan_ip()}:{args.port})")
    print(f"[THM] API docs at http://127.0.0.1:{args.port}/docs")
    if not (ROOT / "web" / "dist" / "index.html").is_file():
        print("[THM] web/dist not built: API only. For the UI run 'cd web && npm run dev' (port 5173) or 'npm run build'.")

    import uvicorn

    options = dict(
        host=args.host,
        port=args.port,
        log_level="info",
        timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS,
    )
    if args.reload:  # dev only: uvicorn's reload supervisor runs the server in a child process
        uvicorn.run(
            "thm_server:create_app", factory=True, reload=True,
            reload_dirs=[str(ROOT / "thm_server"), str(ROOT / "thm_core")], **options,
        )
        return 0

    class Server(uvicorn.Server):
        async def shutdown(self, sockets=None) -> None:
            st = thm_state(self.config.loaded_app)
            if st is not None:
                st.bus.close()  # every SSE stream ends now, before uvicorn waits on connections
            await super().shutdown(sockets)

    server = Server(uvicorn.Config("thm_server:create_app", factory=True, **options))
    try:
        server.run()
    except KeyboardInterrupt:  # a second Ctrl+C during shutdown, re-raised by uvicorn on exit
        pass
    return 0 if server.started else 3  # uvicorn's own STARTUP_FAILURE code


def thm_state(app):
    """The ``ServerState`` behind uvicorn's loaded app: unwrap its middleware (each holds the
    next as ``.app``) down to the FastAPI app. None when there is no such app (tests, odd wrappers)."""
    while app is not None and not hasattr(app, "state"):
        app = getattr(app, "app", None)
    return getattr(getattr(app, "state", None), "thm", None)


if __name__ == "__main__":
    sys.exit(main())
