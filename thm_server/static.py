"""Serve the built SPA from ``web/dist`` with a history-API fallback."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .errors import error_body


def mount_spa(app: FastAPI, dist: Path) -> bool:
    """Mount ``dist`` at ``/``. Returns False (and installs an API-only landing) when there is no build."""
    dist = Path(dist)
    index = dist / "index.html"
    if not index.is_file():
        @app.get("/", include_in_schema=False)
        async def _api_only():
            return {"thm": "api only", "hint": "cd web && npm run dev, then open http://127.0.0.1:5173", "docs": "/docs"}

        return False

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa(path: str):
        if path == "api" or path.startswith("api/") or path.startswith("media/"):
            return JSONResponse(error_body("not_found", path), status_code=404)
        if path:
            try:
                candidate = (dist / path).resolve()
                if candidate.is_file() and root in candidate.parents:
                    return FileResponse(str(candidate))
            except OSError:
                pass
        return FileResponse(str(index), headers={"Cache-Control": "no-store"})

    return True
