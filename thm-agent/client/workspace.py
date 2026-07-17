# Copy ComfyUI outputs into repo workspace so the agent can Read them without drag-and-drop.

from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from client.config_helpers import REPO_ROOT

PathLike = str | Path

WORKSPACE_ROOT = REPO_ROOT / "thm-agent" / "workspace"
PREVIEW_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PREVIEW_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov"}
PREVIEW_MEDIA_SUFFIXES = PREVIEW_IMAGE_SUFFIXES | PREVIEW_VIDEO_SUFFIXES
PREVIEW_SESSION_FILE = "session.json"
ICON_SRC = REPO_ROOT / "icon.png"  # THM orange-"H" logo — favicon + dashboard header


def _icon_data_uri() -> str | None:
    """Inline the THM logo as a data: URI for the favicon + dashboard header.

    Inlined so the dashboard is self-contained: the same HTML resolves over
    file:// (local --open) and http (phone serve, which only serves files under
    preview_dir) without copying the logo into the user's preview folder.
    Returns None if the repo-root logo is missing.
    """
    try:
        raw = ICON_SRC.read_bytes()
    except OSError:
        return None
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _safe_segment(text: str) -> str:
    s = re.sub(r'[<>:"/\\|?*]', "_", str(text or "").strip())
    return s or "untitled"


def mirror_generation_output(
    src_path: PathLike,
    *,
    project_name: str,
    label: str,
    seed: int | None = None,
) -> str | None:
    """
    Copy a generated PNG/MP4 into thm-agent/workspace/{project}/{label}/.

    Returns absolute path to the copy, or None if src missing.
    Agent should Read this path (inside the open repo) for vision QC.
    """
    src = Path(src_path)
    if not src.is_file():
        return None

    parts = [_safe_segment(p) for p in str(label).replace("\\", "/").split("/") if p.strip()]
    dest_dir = WORKSPACE_ROOT / _safe_segment(project_name)
    for part in parts:
        dest_dir = dest_dir / part
    dest_dir.mkdir(parents=True, exist_ok=True)

    stem = src.stem
    suffix = src.suffix
    if seed is not None:
        dest_name = f"{stem}_seed{seed}{suffix}"
    else:
        dest_name = src.name

    dest = dest_dir / dest_name
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        return str(dest.resolve())

    shutil.copy2(src, dest)
    return str(dest.resolve())


def attach_workspace_paths(
    result_dict: dict,
    *,
    project_name: str,
    label: str,
    seed: int | None = None,
) -> dict:
    """Add workspace_path to a generation result dict when main_path exists."""
    main = result_dict.get("main_path")
    if main and result_dict.get("success"):
        ws = mirror_generation_output(main, project_name=project_name, label=label, seed=seed)
        if ws:
            result_dict["workspace_path"] = ws
    return result_dict


def copy_to_workspace(
    src_path: PathLike,
    *,
    dest_subdir: str = "generations",
) -> str:
    """Legacy helper — copy with timestamp prefix under dest_subdir."""
    src = Path(src_path)
    if not src.is_file():
        raise FileNotFoundError(f"Source not found: {src}")
    dest_dir = WORKSPACE_ROOT / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    return str(dest.resolve())


def reveal_in_explorer(path: PathLike, *, select_file: bool = False) -> str:
    """
    Open Windows Explorer on a folder, or highlight a file (/select).

    Returns the path that was opened. Raises FileNotFoundError if missing.
    """
    target = Path(path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Not found: {target}")

    if sys.platform == "win32":
        if select_file and target.is_file():
            subprocess.Popen(["explorer.exe", f"/select,{target}"])
        else:
            folder = target if target.is_dir() else target.parent
            subprocess.Popen(["explorer.exe", str(folder)])
    elif sys.platform == "darwin":
        args = ["open", "-R", str(target)] if select_file else ["open", str(target if target.is_dir() else target.parent)]
        subprocess.Popen(args)
    else:
        folder = target if target.is_dir() else target.parent
        subprocess.Popen(["xdg-open", str(folder)])

    return str(target)


def open_with_default_app(path: PathLike) -> str:
    """Open a file with the OS default application (Photos on Windows, Preview on macOS)."""
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {target}")

    if sys.platform == "win32":
        os.startfile(str(target))  # noqa: S606 — intentional shell open on Windows
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])

    return str(target)


def _resolve_configured_workspace_root() -> Path:
    """Configured project workspace root (config.toml [paths].workspace), absolute."""
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        if str(REPO_ROOT / "src") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "src"))
        from helpers import load_config  # noqa: WPS433 — src helper

        root = Path(load_config().get("workspace_root") or "./projects")
    except Exception:
        root = Path("./projects")
    if not root.is_absolute():
        root = REPO_ROOT / root
    return root


def project_files_dir(workspace_root: PathLike, project_name: str) -> Path:
    """Sibling churn folder for a project: {workspace_root}/{name}-files/."""
    return Path(workspace_root) / f"{_safe_segment(project_name)}-files"


def scaffold_project_files_dir(workspace_root: PathLike, project_name: str) -> Path:
    """Create {name}-files/previews/ + _about-this-folder.md. Idempotent."""
    base = project_files_dir(workspace_root, project_name)
    (base / "previews").mkdir(parents=True, exist_ok=True)
    about = base / "_about-this-folder.md"
    if not about.exists():
        about.write_text(
            (
                "# About this folder\n\n"
                "This folder holds everything The Halleen Machine and its agent "
                f"companion generate while working on **{project_name}** — preview "
                "images, QC staging, run logs, and any one-off scripts the agent "
                "writes for this project. None of it is required to open or edit "
                f"the project file itself — `{project_name}.json` next to this "
                "folder is the source of truth. Safe to delete and let the tools "
                "regenerate, except: if a `skill/` subfolder exists here, it holds "
                "project-specific guidance worth keeping.\n"
            ),
            encoding="utf-8",
        )
    return base


def preview_dir_for_project(project_name: str) -> Path:
    """Preview folder: {workspace_root}/{project}-files/previews/."""
    workspace_root = _resolve_configured_workspace_root()
    return project_files_dir(workspace_root, project_name) / "previews"


def gallery_window_name(project_name: str) -> str:
    """Stable browser window name for gallery --open (one tab per project)."""
    return f"thm-gallery-{_safe_segment(project_name)}"


def write_gallery_launcher(project_name: str) -> Path:
    """
    Write launcher.html that opens index.html in a named browser window.

    Repeated opens reuse the same window/tab for this project.
    """
    preview_dir = preview_dir_for_project(project_name)
    preview_dir.mkdir(parents=True, exist_ok=True)
    window_name = gallery_window_name(project_name)
    # Escape for JS single-quoted string (safe_segment removes quotes)
    doc = (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        "<title>THM Gallery Launcher</title></head><body>\n"
        "<script>\n"
        f"  var w = window.open('index.html', '{window_name}');\n"
        "  if (w) w.focus();\n"
        "  window.close();\n"
        "</script>\n"
        "</body></html>\n"
    )
    launcher_path = preview_dir / "launcher.html"
    launcher_path.write_text(doc, encoding="utf-8")
    return launcher_path


def _session_path(project_name: str) -> Path:
    return preview_dir_for_project(project_name) / PREVIEW_SESSION_FILE


GALLERY_MODES = ("review", "monitor")


def _default_gallery_session() -> dict[str, Any]:
    return {
        "mode": "review",
        "pending": "",
        "change": "",
        "context": "",
        "monitor": {},
        "groups": {},
        "items": {},
        # Elapsed active-work-time counter: starts fresh whenever the gallery
        # is cleared (or first built). "active_seconds" accumulates whole
        # completed active spans; the current span (if state == "active")
        # is added on top using "last_transition" at render time, so no
        # clock needs to run — see gallery_elapsed_seconds().
        "timer": {
            "state": "active",
            "active_seconds": 0.0,
            "last_transition": time.time(),
        },
    }


def load_gallery_session(project_name: str) -> dict[str, Any]:
    """Load session notes for a preview gallery (pending decisions, change hints)."""
    path = _session_path(project_name)
    if not path.is_file():
        return _default_gallery_session()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_gallery_session()
    base = _default_gallery_session()
    if isinstance(data, dict):
        for key in ("pending", "change", "context"):
            if isinstance(data.get(key), str):
                base[key] = data[key]
        if data.get("mode") in GALLERY_MODES:
            base["mode"] = data["mode"]
        if isinstance(data.get("monitor"), dict):
            base["monitor"] = data["monitor"]
        if isinstance(data.get("groups"), dict):
            base["groups"] = data["groups"]
        if isinstance(data.get("items"), dict):
            base["items"] = data["items"]
        if isinstance(data.get("timer"), dict):
            timer = data["timer"]
            if timer.get("state") in ("active", "paused"):
                base["timer"]["state"] = timer["state"]
            if isinstance(timer.get("active_seconds"), (int, float)):
                base["timer"]["active_seconds"] = float(timer["active_seconds"])
            if isinstance(timer.get("last_transition"), (int, float)):
                base["timer"]["last_transition"] = float(timer["last_transition"])
    return base


def gallery_elapsed_seconds(session: dict[str, Any]) -> float:
    """Total active work time: completed spans plus the current one if active."""
    timer = session.get("timer") or {}
    total = float(timer.get("active_seconds", 0.0))
    if timer.get("state") == "active":
        last = float(timer.get("last_transition", time.time()))
        total += max(0.0, time.time() - last)
    return total


def set_gallery_timer_state(project_name: str, state: str) -> dict[str, Any]:
    """Transition the elapsed-time counter between active/paused, folding
    the just-finished span into active_seconds. No-op if already in state."""
    if state not in ("active", "paused"):
        raise ValueError(f"timer state must be 'active' or 'paused', got {state!r}")
    session = load_gallery_session(project_name)
    timer = session.setdefault("timer", {"state": "active", "active_seconds": 0.0, "last_transition": time.time()})
    if timer.get("state") != state:
        now = time.time()
        if timer.get("state") == "active":
            last = float(timer.get("last_transition", now))
            timer["active_seconds"] = float(timer.get("active_seconds", 0.0)) + max(0.0, now - last)
        timer["state"] = state
        timer["last_transition"] = now
        save_gallery_session(project_name, session)
    return session


def save_gallery_session(project_name: str, session: dict[str, Any]) -> str:
    """Persist session notes beside preview media."""
    preview_dir = preview_dir_for_project(project_name)
    preview_dir.mkdir(parents=True, exist_ok=True)
    path = _session_path(project_name)
    path.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path.resolve())


def update_gallery_session(
    project_name: str,
    *,
    mode: str | None = None,
    pending: str | None = None,
    change: str | None = None,
    context: str | None = None,
    monitor_current: str | None = None,
    monitor_next: str | None = None,
    monitor_eta: str | None = None,
    monitor_progress: str | None = None,
    monitor_clip_done_at: str | float | None = None,
    monitor_batch_done_at: str | float | None = None,
    monitor_avg_sec: str | float | None = None,
    monitor_big: str | None = None,
    regen_add: str | None = None,
    regen_remove: str | None = None,
    group: str | None = None,
    group_note: str | None = None,
    group_pending: str | None = None,
    item_filename: str | None = None,
    item_note: str | None = None,
    item_status: str | None = None,
    bump_item_recency: bool = True,
) -> dict[str, Any]:
    """
    Merge session notes at session, group, or item level.

    Empty string clears a field. None leaves it unchanged.
    """
    session = load_gallery_session(project_name)

    if mode is not None:
        if mode not in GALLERY_MODES:
            raise ValueError(f"mode must be one of {GALLERY_MODES}, got {mode!r}")
        session["mode"] = mode
    if pending is not None:
        session["pending"] = pending
    if change is not None:
        session["change"] = change
    if context is not None:
        session["context"] = context

    monitor_fields = {
        "current": monitor_current,
        "next": monitor_next,
        "eta": monitor_eta,
        "progress": monitor_progress,
        "big": monitor_big,
    }
    monitor_numeric = {
        "clip_done_at": monitor_clip_done_at,
        "batch_done_at": monitor_batch_done_at,
        "avg_sec": monitor_avg_sec,
    }
    if any(
        v is not None
        for v in (*monitor_fields.values(), *monitor_numeric.values(),
                  regen_add, regen_remove)
    ):
        block = session.setdefault("monitor", {})
        for key, value in monitor_fields.items():
            if value is not None:
                block[key] = value
        for key, value in monitor_numeric.items():
            if value is None:
                continue
            if value == "":
                block.pop(key, None)
            else:
                block[key] = float(value)
        queue = block.setdefault("regen_queue", [])
        if regen_add:
            for name in (n.strip() for n in regen_add.split(",")):
                if name and name not in queue:
                    queue.append(name)
        if regen_remove:
            for name in (n.strip() for n in regen_remove.split(",")):
                if name in queue:
                    queue.remove(name)

    if group is not None:
        groups = session.setdefault("groups", {})
        entry = groups.setdefault(group, {})
        if group_note is not None:
            entry["note"] = group_note
        if group_pending is not None:
            entry["pending"] = group_pending

    if item_filename is not None:
        name = Path(item_filename).name
        items = session.setdefault("items", {})
        entry = items.setdefault(name, {})
        if item_note is not None:
            entry["note"] = item_note
        if item_status is not None:
            entry["status"] = item_status
        if bump_item_recency or "added_at" not in entry:
            entry["added_at"] = datetime.now(timezone.utc).isoformat()

    save_gallery_session(project_name, session)
    return session


def clear_preview_dir(project_name: str) -> list[str]:
    """
    Remove preview media, index.html, and session notes.

    User-only maintenance — agent must not call this. Asset galleries are additive;
    only the user deletes preview files when they choose.
    """
    preview_dir = preview_dir_for_project(project_name)
    if not preview_dir.is_dir():
        return []

    deleted: list[str] = []
    for path in preview_dir.iterdir():
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if (
            suffix in PREVIEW_MEDIA_SUFFIXES
            or path.name == "index.html"
            or path.name == PREVIEW_SESSION_FILE
        ):
            deleted.append(str(path.resolve()))
            path.unlink()
    return deleted


def add_preview_image(
    project_name: str,
    src_path: PathLike,
    dest_name: str,
    *,
    note: str | None = None,
    status: str | None = None,
) -> str | None:
    """
    Copy an output into {workspace_root}/{project}-files/previews/ under a short filename.

    Returns absolute path to the copy, or None if src missing.
    """
    src = Path(src_path)
    if not src.is_file():
        return None

    name = Path(dest_name).name
    if not name or name.startswith("."):
        raise ValueError(f"Invalid preview filename: {dest_name!r}")

    dest_dir = preview_dir_for_project(project_name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name

    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        copied = str(dest.resolve())
    else:
        shutil.copy2(src, dest)
        copied = str(dest.resolve())

    session = load_gallery_session(project_name)
    items = session.setdefault("items", {})
    entry = items.setdefault(name, {})
    entry["added_at"] = datetime.now(timezone.utc).isoformat()
    if note is not None:
        entry["note"] = note
    if status is not None:
        entry["status"] = status
    save_gallery_session(project_name, session)
    return copied


def group_key_from_filename(filename: str) -> str:
    """Derive gallery group from preview filename prefix (before first hyphen)."""
    stem = Path(filename).stem
    if "-" in stem:
        return stem.split("-", 1)[0]
    return "outputs"


def _file_recency_key(path: Path, session: dict[str, Any]) -> float:
    """Sort key — higher means more recent."""
    items = session.get("items", {})
    entry = items.get(path.name, {})
    added_at = entry.get("added_at")
    if isinstance(added_at, str):
        try:
            return datetime.fromisoformat(added_at).timestamp()
        except ValueError:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


_STATUS_LABELS = {
    "pass": "PASS",
    "fail": "FAILED",
    "in-progress": "HUMAN REVIEW",
}


def _infer_item_status(name: str, meta: dict[str, Any]) -> str | None:
    """Explicit item status wins; otherwise infer from note/filename markers."""
    status = meta.get("status")
    if status == "pending":  # common alias written by driver scripts
        return "in-progress"
    if status in _STATUS_LABELS:
        return status
    haystack = f"{meta.get('note', '')} {name}".upper()
    if "IN-PROGRESS" in haystack or "IN PROGRESS" in haystack:
        return "in-progress"
    if "FAIL" in haystack:
        return "fail"
    if "PASS" in haystack or "SELECTED" in haystack:
        return "pass"
    return None


def _monitor_media_tag(path: Path, *, hero: bool) -> str:
    safe_src = html.escape(path.name)
    if path.suffix.lower() in PREVIEW_VIDEO_SUFFIXES:
        if hero:
            return (
                f'<video src="{safe_src}" autoplay muted loop playsinline '
                f'controls></video>'
            )
        return (
            f'<video src="{safe_src}" muted loop playsinline controls '
            f'preload="metadata"></video>'
        )
    return f'<img src="{safe_src}" alt="" loading="lazy">'


def _video_duration_sec(path: Path) -> float | None:
    """Video duration via ffprobe; None when ffprobe is missing or the file is unreadable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        return float(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _hero_media_file(session: dict[str, Any], media_files: list[Path]) -> Path | None:
    """The file occupying the dashboard's hero slot — mirrors _render_monitor_dashboard:
    the featured final cut when the run is complete, otherwise the most recent item."""
    monitor = session.get("monitor") if isinstance(session.get("monitor"), dict) else {}
    final_cut = str((monitor or {}).get("final_export", "") or "").strip() if (monitor or {}).get("complete") else ""
    if final_cut:
        fc_file = next((p for p in media_files if p.name == final_cut), None)
        if fc_file is not None:
            return fc_file
    gen_media = [p for p in media_files if p.name != final_cut]
    if not gen_media:
        return None
    return max(gen_media, key=lambda p: _file_recency_key(p, session))


_LABEL_CANON = re.compile(r"^(seq\w+)-([A-Za-z]\w*)-take(\d+)$", re.IGNORECASE)
_LABEL_OUTPUT = re.compile(r"(seq\w+)_([A-Za-z]+\d+)_0*(\d+)_?$", re.IGNORECASE)


def _format_item_label(stem: str) -> str:
    """Render a tile/hero caption in the project's own vocabulary: 'seq · id · take N'.

    Reads the sequence / id (or vid) / take straight from the canonical preview name
    ({seq}-{id}-take{N}) or the real ComfyUI output stem ({proj}_{seq}_{id}_{index});
    falls back to the raw stem for anything that doesn't encode them.
    """
    m = _LABEL_CANON.match(stem) or _LABEL_OUTPUT.search(stem)
    if m:
        seq, node, take = m.group(1), m.group(2), int(m.group(3))
        return f"{seq} · {node} · take {take}"
    return stem


def _render_monitor_dashboard(
    session: dict[str, Any],
    media_files: list[Path],
) -> str:
    """Dashboard body: generating-now strip, hero row (latest item + info), approved row."""
    items_meta = session.get("items", {})
    if not isinstance(items_meta, dict):
        items_meta = {}
    monitor = session.get("monitor", {})
    if not isinstance(monitor, dict):
        monitor = {}
    run_complete = bool(monitor.get("complete"))

    def meta_for(path: Path) -> dict[str, Any]:
        meta = items_meta.get(path.name, {})
        return meta if isinstance(meta, dict) else {}

    parts: list[str] = []

    elapsed_sec = gallery_elapsed_seconds(session)
    elapsed_min = round(elapsed_sec / 60)
    if elapsed_min > 59:
        elapsed_label = f"{elapsed_min // 60}h {elapsed_min % 60}m worked"
    else:
        elapsed_label = f"{elapsed_min}m worked"
    timer_state = (session.get("timer") or {}).get("state", "active")
    if timer_state == "paused":
        elapsed_label += " (paused)"
    parts.append(f'<div class="elapsed-timer">{html.escape(elapsed_label)}</div>')

    current = str(monitor.get("current", "") or "").strip()
    next_up = str(monitor.get("next", "") or "").strip()
    eta = str(monitor.get("eta", "") or "").strip()
    progress = str(monitor.get("progress", "") or "").strip()
    clip_done_at = monitor.get("clip_done_at")
    batch_done_at = monitor.get("batch_done_at")
    avg_sec = float(monitor.get("avg_sec") or 480.0)
    regen_queue = monitor.get("regen_queue") or []
    if not isinstance(regen_queue, list):
        regen_queue = []

    # Planned regens push the batch ETA out the moment they are queued,
    # even mid-batch — the user asked for the impact to be immediate.
    effective_batch_done = None
    if isinstance(batch_done_at, (int, float)):
        effective_batch_done = float(batch_done_at) + len(regen_queue) * avg_sec
    elif regen_queue:
        effective_batch_done = time.time() + len(regen_queue) * avg_sec

    if current or next_up or eta or effective_batch_done or regen_queue:
        strip = ['<div class="gen-strip">']
        if current:
            strip.append(
                f'<span class="gen-current">GENERATING '
                f"{html.escape(current)}</span>"
            )
        else:
            strip.append('<span class="gen-current gen-idle">IDLE</span>')
        if isinstance(clip_done_at, (int, float)) and current:
            strip.append(
                f'<span class="gen-eta">clip ETA <span data-done-at='
                f'"{float(clip_done_at):.0f}">…</span></span>'
            )
        if effective_batch_done is not None:
            strip.append(
                f'<span class="gen-eta">batch ETA <span data-done-at='
                f'"{effective_batch_done:.0f}">…</span></span>'
            )
        elif eta:
            strip.append(f'<span class="gen-eta">ETA {html.escape(eta)}</span>')
        if regen_queue:
            names = html.escape(", ".join(str(n) for n in regen_queue))
            strip.append(
                f'<span class="gen-regens">+{len(regen_queue)} regen'
                f'{"s" if len(regen_queue) != 1 else ""} queued: {names}</span>'
            )
        if next_up:
            strip.append(f'<span class="gen-next">next: {html.escape(next_up)}</span>')
        strip.append("</div>")
        parts.append("\n".join(strip))

    # Big picture: project → round → batch context, newline-separated lines,
    # optional "Label: text" per line.
    big = str(monitor.get("big", "") or "").strip()
    if big:
        rows = []
        for line in big.splitlines():
            line = line.strip()
            if not line:
                continue
            label, sep, rest = line.partition(":")
            if sep and len(label) <= 24:
                rows.append(
                    '<span class="bp-line"><span class="bp-label">'
                    f"{html.escape(label.strip())}</span> "
                    f"{html.escape(rest.strip())}</span>"
                )
            else:
                rows.append(f'<span class="bp-line">{html.escape(line)}</span>')
        if rows:
            parts.append(
                '<div class="big-picture">\n' + "\n".join(rows) + "\n</div>"
            )

    if not media_files:
        parts.append('<p class="empty">No generations yet.</p>')
        return "\n".join(parts)

    # Final cut: when a run completes the stitched, debug-labeled export is the featured
    # hero at the top of the monitor; the per-item tiles stay below it.
    final_cut = str(monitor.get("final_export", "") or "").strip() if run_complete else ""
    fc_file = next((p for p in media_files if p.name == final_cut), None) if final_cut else None
    if fc_file is not None:
        parts.append(
            '<section class="final-cut" data-item-id="final-cut">\n'
            '<h3 class="group-title">Final cut &#8212; stitched, debug labels</h3>\n'
            f'<video src="{html.escape(fc_file.name)}" autoplay muted loop playsinline '
            "controls></video>\n"
            "</section>"
        )

    gen_media = [p for p in media_files if p.name != final_cut]
    if not gen_media:
        return "\n".join(parts)

    ordered = sorted(gen_media, key=lambda p: -_file_recency_key(p, session))
    hero = ordered[0]
    hero_meta = meta_for(hero)
    hero_status = _infer_item_status(hero.name, hero_meta)
    status_html = ""
    if hero_status:
        status_html = (
            f'<span class="status-badge status-{hero_status}">'
            f"{_STATUS_LABELS[hero_status]}</span>"
        )
    hero_note = str(hero_meta.get("note", "") or "").strip()
    blurb_html = f'<p class="hero-blurb">{html.escape(hero_note)}</p>' if hero_note else ""
    progress_html = (
        f'<p class="hero-progress">{html.escape(progress)} planned generations</p>'
        if progress
        else ""
    )
    # data-item-id on the whole hero row: on phones the autoplaying hero video
    # swallows taps into its native controls, so the info/title area must also
    # open the note composer, plus an explicit button that never fights video
    # controls.
    parts.append(
        f'<section class="hero-row" data-item-id="{html.escape(hero.stem)}">\n'
        f'<div class="hero-media" data-item-id="{html.escape(hero.stem)}">'
        f"{_monitor_media_tag(hero, hero=True)}</div>\n"
        '<div class="hero-info">\n'
        f'<h2 class="hero-label">{html.escape(_format_item_label(hero.stem))}</h2>\n'
        f"{status_html}\n{blurb_html}\n{progress_html}\n"
        f'<button type="button" class="hero-note-btn" '
        f'data-item-id="{html.escape(hero.stem)}">+ note on this item</button>\n'
        "</div>\n</section>"
    )

    # Every session item lands in exactly one row: pending (awaiting QC or any
    # non-verdict status), approved, or rejected. No caps - a batch project can
    # exceed 10 per bucket and nothing may disappear from review.
    pending = [
        p
        for p in ordered[1:]
        if _infer_item_status(p.name, meta_for(p)) not in ("pass", "fail")
    ]
    if pending:
        cards = "\n".join(
            f'<figure class="card" data-item-id="{html.escape(p.stem)}">'
            f'<a href="{html.escape(p.name)}" target="_blank" rel="noopener">'
            f"{_monitor_media_tag(p, hero=False)}</a>"
            f"<figcaption>{html.escape(_format_item_label(p.stem))}</figcaption>"
            "</figure>"
            for p in pending
        )
        parts.append(
            '<section class="pending-row">\n'
            '<h3 class="group-title">Human review (waiting on you)</h3>\n'
            f'<div class="group-row">\n{cards}\n</div>\n'
            "</section>"
        )

    approved = [
        p
        for p in ordered[1:]
        if _infer_item_status(p.name, meta_for(p)) == "pass"
    ]
    if approved:
        cards = "\n".join(
            f'<figure class="card" data-item-id="{html.escape(p.stem)}">'
            f'<a href="{html.escape(p.name)}" target="_blank" rel="noopener">'
            f"{_monitor_media_tag(p, hero=False)}</a>"
            f"<figcaption>{html.escape(_format_item_label(p.stem))}</figcaption>"
            "</figure>"
            for p in approved
        )
        parts.append(
            '<section class="approved-row">\n'
            '<h3 class="group-title">Approved this session</h3>\n'
            f'<div class="group-row">\n{cards}\n</div>\n'
            "</section>"
        )

    rejected = [
        p
        for p in ordered[1:]
        if _infer_item_status(p.name, meta_for(p)) == "fail"
    ]
    if rejected:
        cards = "\n".join(
            '<figure class="card card-rejected">'
            f'<a href="{html.escape(p.name)}" target="_blank" rel="noopener">'
            f"{_monitor_media_tag(p, hero=False)}</a>"
            f"<figcaption>{html.escape(_format_item_label(p.stem))}</figcaption>"
            "</figure>"
            for p in rejected
        )
        parts.append(
            '<section class="rejected-row">\n'
            '<h3 class="group-title group-title-rejected">Rejected this session</h3>\n'
            f'<div class="group-row">\n{cards}\n</div>\n'
            "</section>"
        )

    # Static ETA rendered once per page load/refresh — no per-second ticking
    # (the estimate isn't precise enough to justify a live countdown; the
    # page's own 30s meta-refresh is what keeps this current).
    parts.append(
        "<script>\n"
        "function fmtEta(sec) {\n"
        "  if (sec <= 0) return 'any moment…';\n"
        "  var totalMin = Math.round(sec / 60);\n"
        "  if (totalMin > 59) {\n"
        "    var h = Math.floor(totalMin / 60), m = totalMin % 60;\n"
        "    return '~' + h + 'h ' + m + 'm';\n"
        "  }\n"
        "  return '~' + totalMin + 'm';\n"
        "}\n"
        "var now = Date.now() / 1000;\n"
        "document.querySelectorAll('[data-done-at]').forEach(function (el) {\n"
        "  el.textContent = fmtEta(parseFloat(el.dataset.doneAt) - now);\n"
        "});\n"
        "</script>"
    )
    return "\n".join(parts)


def _note_mode_block() -> str:
    """
    Tap-to-note overlay for the remote interface (cli.py serve).

    Inert when the gallery is opened via file:// — the script bails unless the
    page is served over HTTP, so the local workflow is byte-for-byte identical
    in behavior. Over HTTP, tapping any [data-item-id] media opens a full-screen
    composer with the media autoplaying and the item ID pre-tagged; submit POSTs
    to /api/notes on the same origin.
    """
    return """  <div id="note-overlay" hidden>
    <div class="no-head">
      <span class="no-chip" id="no-chip"></span>
      <button type="button" id="no-close">&#10005;</button>
    </div>
    <div class="no-media" id="no-media"></div>
    <form id="no-form">
      <textarea id="no-text" placeholder="Note for the agent&#8230;"></textarea>
      <div class="no-row">
        <label><input type="radio" name="no-priority" value="urgent" checked> urgent &#8212; act now</label>
        <label><input type="radio" name="no-priority" value="batch"> next checkpoint</label>
      </div>
      <button type="submit" id="no-send">Send</button>
      <div id="no-result"></div>
    </form>
  </div>
  <style>
    #note-overlay {
      position: fixed;
      inset: 0;
      z-index: 1000;
      background: rgba(4, 6, 12, 0.96);
      display: flex;
      flex-direction: column;
      gap: 0.6rem;
      padding: 0.75rem;
    }
    #note-overlay[hidden] { display: none; }
    .no-head { display: flex; align-items: center; justify-content: space-between; }
    .no-chip {
      font-weight: 700;
      color: var(--thm-orange);
      border: 1px solid var(--thm-orange);
      border-radius: 999px;
      padding: 0.15rem 0.8rem;
    }
    #no-close {
      background: none;
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 8px;
      font-size: 1.1rem;
      padding: 0.3rem 0.8rem;
    }
    .no-media { display: flex; justify-content: center; min-height: 0; }
    .no-media video, .no-media img {
      max-height: 45vh;
      max-width: 100%;
      object-fit: contain;
      border-radius: 6px;
    }
    #no-form { display: flex; flex-direction: column; gap: 0.6rem; }
    #no-text {
      min-height: 5.5rem;
      padding: 0.6rem;
      font: inherit;
      background: var(--panel);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 8px;
    }
    .no-row { display: flex; gap: 1.25rem; color: var(--muted); }
    #no-send {
      padding: 0.7rem;
      font: inherit;
      font-weight: 700;
      background: var(--thm-orange);
      color: #000;
      border: none;
      border-radius: 8px;
    }
    #no-send:disabled { opacity: 0.5; }
    #no-result { min-height: 1.2rem; }
    .remote-on [data-item-id], .remote-on [data-item-id] a { cursor: pointer; }
  </style>
  <script>
    (function () {
      if (location.protocol !== "http:" && location.protocol !== "https:") return;
      document.body.classList.add("remote-on");
      var overlay = document.getElementById("note-overlay");
      var mediaSlot = document.getElementById("no-media");
      var textBox = document.getElementById("no-text");

      function openNote(itemId, src) {
        mediaSlot.innerHTML = "";
        if (src) {
          var el;
          if (/\\.(mp4|webm|mov)([?#]|$)/i.test(src)) {
            el = document.createElement("video");
            el.src = src;
            el.autoplay = true;
            el.muted = true;
            el.loop = true;
            el.playsInline = true;
            el.controls = true;
          } else {
            el = document.createElement("img");
            el.src = src;
          }
          mediaSlot.appendChild(el);
        }
        document.getElementById("no-chip").textContent = itemId || "general note";
        overlay.dataset.itemId = itemId || "";
        overlay.hidden = false;
        document.body.classList.add("note-open");
        textBox.focus();
      }

      function closeNote() {
        overlay.hidden = true;
        document.body.classList.remove("note-open");
        mediaSlot.innerHTML = "";
      }

      function mediaSrcForItem(itemId) {
        if (!itemId) return null;  // general note (no item) stays media-less
        var sel = (window.CSS && CSS.escape) ? CSS.escape(itemId) : itemId;
        var el = document.querySelector(
          '[data-item-id="' + sel + '"] video, [data-item-id="' + sel + '"] img'
        );
        return el ? el.getAttribute("src") : null;
      }

      document.addEventListener("click", function (e) {
        if (!overlay.hidden) return;
        if (!(e.target instanceof Element)) return;
        var host = e.target.closest("[data-item-id]");
        if (!host) return;
        e.preventDefault();
        e.stopPropagation();
        var itemId = host.dataset.itemId;
        // The host may be the media tile itself (contains the media) or a control
        // like the hero "+ note on this item" button (item-id but no child media).
        // Either way, resolve the item's media by id so every per-item note view
        // shows the media; a general note (no item-id) resolves to null.
        var media = host.querySelector("video, img");
        var src = media ? media.getAttribute("src") : mediaSrcForItem(itemId);
        openNote(itemId, src);
      }, true);

      document.getElementById("no-close").addEventListener("click", closeNote);
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !overlay.hidden) closeNote();
      });

      document.getElementById("no-form").addEventListener("submit", function (e) {
        e.preventDefault();
        var text = textBox.value.trim();
        if (!text) return;
        var send = document.getElementById("no-send");
        var result = document.getElementById("no-result");
        send.disabled = true;
        var priority = document.querySelector('input[name="no-priority"]:checked').value;
        fetch("/api/notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: text,
            priority: priority,
            item_id: overlay.dataset.itemId || null
          })
        }).then(function (r) { return r.json(); }).then(function (data) {
          send.disabled = false;
          if (data.status === "ok") {
            result.textContent = "\\u2713 sent";
            textBox.value = "";
            setTimeout(function () { result.textContent = ""; closeNote(); }, 900);
          } else {
            result.textContent = "\\u2717 " + (data.reason || "failed");
          }
        }).catch(function (err) {
          send.disabled = false;
          result.textContent = "\\u2717 " + err;
        });
      });
    })();
  </script>
"""


def build_preview_gallery(
    project_name: str,
    *,
    title: str | None = None,
    clear_first: bool = False,
    mode: str | None = None,
    pending: str | None = None,
    change: str | None = None,
    context: str | None = None,
    monitor_current: str | None = None,
    monitor_next: str | None = None,
    monitor_eta: str | None = None,
    monitor_progress: str | None = None,
    monitor_clip_done_at: str | float | None = None,
    monitor_batch_done_at: str | float | None = None,
    monitor_avg_sec: str | float | None = None,
    monitor_big: str | None = None,
    regen_add: str | None = None,
    regen_remove: str | None = None,
    group: str | None = None,
    group_note: str | None = None,
    group_pending: str | None = None,
) -> str:
    """
    Scan {workspace_root}/{project}-files/previews/ and write index.html for browser compare.

    Returns absolute path to index.html.
    """
    if clear_first:
        clear_preview_dir(project_name)

    if any(
        x is not None
        for x in (
            mode,
            pending,
            change,
            context,
            monitor_current,
            monitor_next,
            monitor_eta,
            monitor_progress,
            monitor_clip_done_at,
            monitor_batch_done_at,
            monitor_avg_sec,
            monitor_big,
            regen_add,
            regen_remove,
            group,
            group_note,
            group_pending,
        )
    ):
        update_gallery_session(
            project_name,
            mode=mode,
            pending=pending,
            change=change,
            context=context,
            monitor_current=monitor_current,
            monitor_next=monitor_next,
            monitor_eta=monitor_eta,
            monitor_progress=monitor_progress,
            monitor_clip_done_at=monitor_clip_done_at,
            monitor_batch_done_at=monitor_batch_done_at,
            monitor_avg_sec=monitor_avg_sec,
            monitor_big=monitor_big,
            regen_add=regen_add,
            regen_remove=regen_remove,
            group=group,
            group_note=group_note,
            group_pending=group_pending,
        )

    preview_dir = preview_dir_for_project(project_name)
    preview_dir.mkdir(parents=True, exist_ok=True)

    # Older builds copied icon.png into the preview folder; remove it so the
    # user's project folder holds only their media.
    stale_icon = preview_dir / "icon.png"
    if stale_icon.is_file():
        try:
            stale_icon.unlink()
        except OSError:
            pass

    session = load_gallery_session(project_name)
    media_files = [
        p
        for p in preview_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in PREVIEW_MEDIA_SUFFIXES
        and p.name.lower() != "icon.png"
    ]

    page_title = html.escape(title or f"{project_name} — previews")
    window_name = html.escape(gallery_window_name(project_name))

    # The branded, auto-refreshing monitor dashboard is the only view. The header
    # (logo + "The Halleen Machine" wordmark + project/status) is always present — it is
    # part of the UX, not optional. Refresh is faster while a run is live and drops to
    # 60s once the run completes and the stitched "final cut" is showing.
    icon_uri = _icon_data_uri()
    logo_html = (
        f'    <img class="thm-logo" src="{icon_uri}" alt="" width="26" height="26">\n'
        if icon_uri
        else ""
    )
    favicon_html = (
        f'  <link rel="icon" type="image/png" href="{icon_uri}">\n' if icon_uri else ""
    )
    header_html = (
        '  <header class="thm-header">\n'
        f"{logo_html}"
        '    <span class="thm-wordmark">The Halleen Machine</span>\n'
        f'    <span class="project-title">{page_title}</span>\n'
        "  </header>\n"
    )
    monitor = session.get("monitor") if isinstance(session.get("monitor"), dict) else {}
    run_complete = bool((monitor or {}).get("complete"))
    refresh_ms = 60000 if run_complete else 30000
    # A hero video longer than 28s would be cut off by the reload — refresh at its
    # length + 2s instead so it always plays through once.
    hero_file = _hero_media_file(session, media_files)
    if hero_file is not None and hero_file.suffix.lower() in PREVIEW_VIDEO_SUFFIXES:
        duration = _video_duration_sec(hero_file)
        if duration is not None and duration > 28.0:
            refresh_ms = int((duration + 2.0) * 1000)
    # JS reload instead of meta refresh so note mode can hold the page while composing.
    refresh_tag = (
        "<script>setInterval(function () {\n"
        "    if (!document.body.classList.contains('note-open')) location.reload();\n"
        "  }, " + str(refresh_ms) + ");</script>\n  "
    )
    body = _render_monitor_dashboard(session, media_files)

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_tag}<title>The Halleen Machine — {page_title}</title>
{favicon_html}  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <script>window.name = "{window_name}";</script>
  <style>
    :root {{
      color-scheme: dark;
      --thm-orange: #ff7c00;
      --thm-orange-dim: #d9a27c;
      --thm-black: #000000;
      --bg: #0b0f19;
      --panel: #171717;
      --panel-elevated: #1f1f1f;
      --text: #fafafa;
      --muted: rgba(250, 250, 250, 0.62);
      --border: #444444;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 16px/1.5 "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    .thm-header {{
      display: flex;
      align-items: center;
      gap: 0.55rem;
      padding: 0.45rem 1rem;
      background: var(--panel);
      border-bottom: 2px solid var(--thm-orange);
    }}
    .thm-logo {{ display: block; border-radius: 5px; flex: 0 0 auto; }}
    .thm-wordmark {{
      font-weight: 700;
      font-size: 0.95rem;
      letter-spacing: 0.01em;
      color: var(--text);
    }}
    .project-title {{
      margin: 0 0 0 auto;
      font-size: 0.8rem;
      font-weight: 500;
      color: var(--muted);
    }}
    .final-cut {{
      margin: 0 0 1rem 0;
      padding: 0.5rem 0.6rem;
      background: var(--panel);
      border: 1px solid var(--thm-orange);
      border-radius: 6px;
    }}
    .final-cut video {{
      display: block;
      width: 100%;
      max-height: 70vh;
      border-radius: 4px;
      background: var(--thm-black);
    }}
    main {{
      padding: 0.75rem;
      max-width: none;
      margin: 0;
    }}
    .group-section {{
      margin: 0 0 1.25rem 0;
    }}
    .group-title {{
      margin: 0 0 0.4rem 0;
      font-size: 0.85rem;
      font-weight: 600;
      color: var(--text);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .group-row {{
      display: flex;
      flex-wrap: nowrap;
      align-items: flex-start;
      gap: 0.5rem;
      overflow-x: auto;
      padding-bottom: 0.25rem;
    }}
    .card {{
      margin: 0;
      flex: 0 0 auto;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
    }}
    .card a {{
      display: block;
      background: var(--thm-black);
    }}
    .card a:hover img,
    .card a:hover video {{
      opacity: 0.94;
    }}
    .card img, .card video {{
      display: block;
      height: 360px;
      width: auto;
      max-width: 80vw;
      object-fit: contain;
      transition: opacity 0.15s ease;
    }}
    .card figcaption {{
      padding: 0.25rem 0.5rem;
      font-size: 0.75rem;
      color: var(--muted);
      text-align: center;
      border-top: 1px solid var(--border);
    }}
    .elapsed-timer {{
      font-size: 0.85rem;
      color: var(--muted);
      text-align: right;
      padding: 0.2rem 0.5rem 0.4rem 0.5rem;
    }}
    .gen-strip {{
      display: flex;
      align-items: baseline;
      gap: 1.25rem;
      padding: 0.5rem 0.75rem;
      margin: 0 0 0.75rem 0;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 6px;
    }}
    .gen-current {{
      font-size: 1.4rem;
      font-weight: 700;
      color: var(--thm-orange);
      letter-spacing: 0.03em;
    }}
    .gen-current.gen-idle {{ color: var(--muted); }}
    .gen-eta {{
      font-size: 1.1rem;
      font-weight: 600;
      color: var(--text);
    }}
    .gen-next {{
      font-size: 0.9rem;
      color: var(--muted);
    }}
    .gen-regens {{
      font-size: 1rem;
      font-weight: 600;
      color: #f87171;
    }}
    .big-picture {{
      display: flex;
      flex-wrap: wrap;
      column-gap: 2rem;
      row-gap: 0.15rem;
      padding: 0.4rem 0.75rem;
      margin: 0 0 0.75rem 0;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 0.95rem;
    }}
    .bp-label {{
      font-weight: 700;
      color: var(--thm-orange-dim);
      text-transform: uppercase;
      font-size: 0.8rem;
      letter-spacing: 0.05em;
    }}
    .hero-row {{
      display: flex;
      gap: 0.75rem;
      align-items: stretch;
      margin: 0 0 1rem 0;
    }}
    .hero-media {{
      flex: 0 1 auto;
      background: var(--thm-black);
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
    }}
    .hero-media video, .hero-media img {{
      display: block;
      height: 58vh;
      max-width: 62vw;
      width: auto;
      object-fit: contain;
    }}
    .hero-note-btn {{ display: none; margin-top: 10px; padding: 10px 16px;
      font-size: 15px; border-radius: 8px; border: 1px solid #777;
      background: #26262b; color: #eee; }}
    .remote-on .hero-note-btn {{ display: inline-block; cursor: pointer; }}
    .hero-info {{
      flex: 1 1 0;
      min-width: 0;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 1.25rem 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }}
    .hero-label {{
      margin: 0;
      font-size: 2.2rem;
      font-weight: 700;
      line-height: 1.15;
      overflow-wrap: anywhere;
    }}
    .status-badge {{
      align-self: flex-start;
      font-size: 1.05rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      padding: 0.25rem 0.75rem;
      border-radius: 999px;
      border: 1px solid var(--border);
    }}
    .status-in-progress {{ color: var(--thm-orange); border-color: var(--thm-orange); }}
    .status-pass {{ color: #4ade80; border-color: #4ade80; }}
    .status-fail {{ color: #f87171; border-color: #f87171; }}
    .card.selected {{ outline: 2px solid #4ade80; outline-offset: -2px; border-radius: 6px; }}
    .selected-flag {{
      color: #4ade80;
      font-weight: 700;
      font-size: 0.8em;
      letter-spacing: 0.06em;
      border: 1px solid #4ade80;
      border-radius: 999px;
      padding: 0.05rem 0.45rem;
      margin-left: 0.35rem;
    }}
    .hero-blurb {{
      margin: 0;
      font-size: 1.25rem;
      line-height: 1.45;
      color: var(--text);
    }}
    .hero-progress {{
      margin: auto 0 0 0;
      font-size: 1.3rem;
      font-weight: 600;
      color: var(--thm-orange-dim);
    }}
    .approved-row .card img, .approved-row .card video {{
      height: 21vh;
    }}
    .group-title-rejected {{
      color: #f87171;
    }}
    .rejected-row .card img, .rejected-row .card video {{
      height: 21vh;
    }}
    .card-rejected {{
      border-color: #f87171;
      opacity: 0.85;
    }}
    @media (max-width: 760px) {{
      /* Mobile: stack the hero row instead of side-by-side; source order
         already puts hero-media before hero-info, so this alone puts the
         video above the status panel. Desktop rules above are untouched. */
      .hero-row {{
        flex-direction: column;
      }}
      .hero-media video, .hero-media img {{
        height: auto;
        max-width: 100%;
        width: 100%;
      }}
    }}
    .empty {{
      color: var(--muted);
      padding: 2rem;
      text-align: center;
      background: var(--panel);
      border: 2px dashed var(--border);
      border-radius: 8px;
    }}
    .empty code {{
      color: var(--thm-orange-dim);
      font-family: ui-monospace, "Cascadia Code", "Segoe UI Mono", monospace;
      font-size: 0.9em;
    }}
    .note-fab {{
      display: none; /* revealed by note-mode JS when served over HTTP */
      position: fixed;
      right: 1rem;
      bottom: 1rem;
      z-index: 10;
      background: var(--thm-orange);
      color: var(--thm-black);
      font-weight: 700;
      font-size: 0.95rem;
      text-decoration: none;
      padding: 0.6rem 1.1rem;
      border-radius: 999px;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.4);
    }}
    .note-fab:active {{
      background: var(--thm-orange-dim);
    }}
    body.remote-on .note-fab {{ display: block; }}
  </style>
</head>
<body>
{header_html}  <a class="note-fab" href="/note">+ Note</a>
  <main>
{body}
  </main>
{_note_mode_block()}</body>
</html>
"""

    index_path = preview_dir / "index.html"
    index_path.write_text(doc, encoding="utf-8")
    return str(index_path.resolve())


def _project_name_from_preview_dir(preview_dir: Path) -> str | None:
    """Derive project name from {project}-files/previews/ parent folder name."""
    parent_name = preview_dir.parent.name
    suffix = "-files"
    if parent_name.endswith(suffix):
        return parent_name[: -len(suffix)]
    return None


def open_gallery_in_browser(path: PathLike, *, project_name: str | None = None) -> str:
    """
    Open the preview gallery in the default browser.

    Uses launcher.html + window.open(..., windowName) so repeated opens for the
    same project reuse one browser tab.
    """
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"Gallery not found: {target}")

    resolved_project = project_name
    if not resolved_project:
        resolved_project = _project_name_from_preview_dir(target.parent)
    if not resolved_project:
        webbrowser.open(target.as_uri())
        return str(target)

    launcher = write_gallery_launcher(resolved_project)
    webbrowser.open(launcher.as_uri())
    return str(launcher.resolve())


def resolve_image_path(
    project_data: dict,
    seq_id: str,
    kf_id: str,
    filename: str | None = None,
) -> str | None:
    """Resolve absolute path to a keyframe image (newest or by filename)."""
    from client.discover import list_keyframe_images

    paths = list_keyframe_images(project_data, seq_id, kf_id, include_previews=False)
    if not paths:
        return None
    if filename:
        for p in paths:
            if Path(p).name == filename:
                return p
        return None
    return paths[0]
