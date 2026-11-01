"""Shared helpers for routes that write a file into a project's ``imports/`` (design section 8):
uploads and clips both land here, adopt the same way, and join a bin through the ordinary
``add_media_to_bin``/``create_bin`` commands.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from thm_core import Bin, CommandContext, Env, Message, Project, ProjectStore, execute
from thm_core.commands import CreateBin

from .errors import ApiError

IMPORTS_DIR = "imports"


def safe_name(filename: str | None) -> str:
    name = Path(str(filename or "")).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (name or "file")[:120]


def fresh_import_path(files_dir: Path, name: str) -> Path:
    """A fresh path under imports/: timestamp prefix, ``-2``, ``-3`` on collision."""
    d = Path(files_dir) / IMPORTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = d / f"{stamp}_{name}"
    if not base.exists():
        return base
    n = 2
    while True:
        cand = d / f"{base.stem}-{n}{base.suffix}"
        if not cand.exists():
            return cand
        n += 1


def find_or_create_bin(
    store: ProjectStore,
    project: Project,
    ctx: CommandContext,
    env: Env,
    bin_id: str | None,
    bin_name: str | None,
) -> tuple[Bin, list[Message]]:
    """An explicit bin id, an existing bin matched by name (case-insensitive), or a freshly
    minted one: named as given, else the next ``bin-n``. A bin minted here takes no workflow:
    it exists to hold files, and gets one when someone wants to generate into it."""
    if bin_id:
        b = project.bins_by_id.get(bin_id)
        if b is None:
            raise ApiError(404, "unknown_id", f"no bin {bin_id}", {"type": "bin", "id": bin_id})
        return b, []
    name = (bin_name or "").strip()
    for b in project.bins_by_id.values():
        if name and b.name.strip().lower() == name.lower():
            return b, []
    cmd = CreateBin(bin_name=name or None, workflow="")
    result = execute(store, project, cmd, ctx, env=env)
    return project.bins_by_id[cmd._created], list(result.messages)
