# Copy ComfyUI outputs into repo workspace so the agent can Read them without drag-and-drop.

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
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


def _default_gallery_session() -> dict[str, Any]:
    return {
        "pending": "",
        "change": "",
        "context": "",
        "groups": {},
        "items": {},
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
        if isinstance(data.get("groups"), dict):
            base["groups"] = data["groups"]
        if isinstance(data.get("items"), dict):
            base["items"] = data["items"]
    return base


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
    pending: str | None = None,
    change: str | None = None,
    context: str | None = None,
    group: str | None = None,
    group_note: str | None = None,
    group_pending: str | None = None,
    item_filename: str | None = None,
    item_note: str | None = None,
) -> dict[str, Any]:
    """
    Merge session notes at session, group, or item level.

    Empty string clears a field. None leaves it unchanged.
    """
    session = load_gallery_session(project_name)

    if pending is not None:
        session["pending"] = pending
    if change is not None:
        session["change"] = change
    if context is not None:
        session["context"] = context

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
    save_gallery_session(project_name, session)
    return copied


def _format_group_title(group_key: str) -> str:
    return group_key.replace("-", " ").replace("_", " ").strip().title()


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


def _group_preview_files(
    files: list[Path],
    session: dict[str, Any],
) -> list[tuple[str, list[tuple[str, Path, dict[str, Any]]]]]:
    """
    Group preview media by stem prefix before the *first* hyphen.

    Groups and items are ordered newest-first.
    """
    groups: dict[str, list[tuple[str, Path, dict[str, Any]]]] = {}
    items_meta = session.get("items", {})

    for path in files:
        stem = path.stem
        if "-" in stem:
            group_key, label = stem.split("-", 1)
        else:
            group_key, label = "outputs", stem
        meta = items_meta.get(path.name, {}) if isinstance(items_meta, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        groups.setdefault(group_key, []).append((label, path, meta))

    def group_recency(group_key: str) -> float:
        items = groups.get(group_key, [])
        if not items:
            return 0.0
        return max(_file_recency_key(path, session) for _, path, _ in items)

    ordered_keys = sorted(groups.keys(), key=lambda k: (-group_recency(k), k.lower()))
    result: list[tuple[str, list[tuple[str, Path, dict[str, Any]]]]] = []
    for key in ordered_keys:
        items = sorted(
            groups[key],
            key=lambda item: (-_file_recency_key(item[1], session), item[0].lower()),
        )
        result.append((key, items))
    return result


def _render_note_block(text: str, *, kind: str = "note", prefix: str | None = None) -> str:
    if not text or not str(text).strip():
        return ""
    safe = html.escape(str(text).strip())
    label = {
        "pending": "Pending",
        "change": "Recent change",
        "context": "Context",
        "note": "Note",
        "tile": "Tile",
    }.get(kind, "Note")
    prefix_html = ""
    if prefix:
        prefix_html = f'<span class="hint-prefix">{html.escape(prefix)}</span> '
    return (
        f'<p class="hint hint-{kind}">'
        f'<span class="hint-label">{label}</span> {prefix_html}{safe}</p>'
    )


def _render_review_panel(
    session: dict[str, Any],
    groups: list[tuple[str, list[tuple[str, Path, dict[str, Any]]]]],
) -> str:
    """Render top review panel from session, group, and per-tile notes."""
    lines: list[str] = []

    context = str(session.get("context", "") or "").strip()
    pending = str(session.get("pending", "") or "").strip()
    change = str(session.get("change", "") or "").strip()
    if context:
        lines.append(_render_note_block(context, kind="context"))
    if pending:
        lines.append(_render_note_block(pending, kind="pending"))
    if change:
        lines.append(_render_note_block(change, kind="change"))

    groups_meta = session.get("groups", {})
    if isinstance(groups_meta, dict):
        for group_key, items in groups:
            gmeta = groups_meta.get(group_key, {})
            if not isinstance(gmeta, dict):
                continue
            group_title = _format_group_title(group_key)
            if gmeta.get("note"):
                lines.append(
                    _render_note_block(
                        gmeta["note"], kind="note", prefix=f"{group_title}:"
                    )
                )
            if gmeta.get("pending"):
                lines.append(
                    _render_note_block(
                        gmeta["pending"], kind="pending", prefix=f"{group_title}:"
                    )
                )

    for _group_key, items in groups:
        for label, _path, meta in items:
            item_note = (meta or {}).get("note", "")
            if isinstance(item_note, str) and item_note.strip():
                lines.append(
                    _render_note_block(item_note, kind="tile", prefix=f"{label}:")
                )

    if not lines:
        return ""

    body = "\n".join(lines)
    return (
        '<aside class="review-panel">\n'
        "<h2>Reviewing now</h2>\n"
        f'<div class="review-body">{body}</div>\n'
        "</aside>"
    )


def _render_preview_item(
    label: str,
    path: Path,
    *,
    meta: dict[str, Any] | None = None,
    is_latest: bool = False,
) -> str:
    del meta, is_latest  # session metadata kept in JSON
    safe_src = html.escape(path.name)
    suffix = path.suffix.lower()
    if suffix in PREVIEW_VIDEO_SUFFIXES:
        media = (
            f'<video src="{safe_src}" controls preload="metadata" '
            f'playsinline></video>'
        )
    else:
        media = f'<img src="{safe_src}" alt="" loading="lazy">'

    caption = (
        f'<figcaption>{html.escape(label)}</figcaption>' if label else ""
    )
    return (
        f'<figure class="card">'
        f'<a href="{safe_src}" target="_blank" rel="noopener">{media}</a>'
        f"{caption}"
        f"</figure>"
    )


def _render_group_section(
    group_key: str,
    items: list[tuple[str, Path, dict[str, Any]]],
) -> str:
    """One group's items rendered as a titled, single-line row."""
    cards = "\n".join(
        _render_preview_item(label, path, meta=meta) for label, path, meta in items
    )
    title = html.escape(_format_group_title(group_key))
    return (
        '<section class="group-section">\n'
        f'<h3 class="group-title">{title}</h3>\n'
        f'<div class="group-row">\n{cards}\n</div>\n'
        "</section>"
    )


def build_preview_gallery(
    project_name: str,
    *,
    title: str | None = None,
    clear_first: bool = False,
    pending: str | None = None,
    change: str | None = None,
    context: str | None = None,
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
        x is not None for x in (pending, change, context, group, group_note, group_pending)
    ):
        update_gallery_session(
            project_name,
            pending=pending,
            change=change,
            context=context,
            group=group,
            group_note=group_note,
            group_pending=group_pending,
        )

    preview_dir = preview_dir_for_project(project_name)
    preview_dir.mkdir(parents=True, exist_ok=True)

    session = load_gallery_session(project_name)
    media_files = [
        p
        for p in preview_dir.iterdir()
        if p.is_file() and p.suffix.lower() in PREVIEW_MEDIA_SUFFIXES
    ]
    groups = _group_preview_files(media_files, session)

    page_title = html.escape(title or f"{project_name} — previews")
    window_name = html.escape(gallery_window_name(project_name))

    if groups:
        body = "\n".join(
            _render_group_section(group_key, items) for group_key, items in groups
        )
    else:
        body = (
            '<p class="empty">No preview images yet. Copy outputs here as '
            f"<code>{html.escape(project_name)}-A.png</code> etc., then rebuild.</p>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>The Halleen Machine — {page_title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
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
      padding: 0.5rem 1rem;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
    }}
    .project-title {{
      margin: 0;
      font-size: 0.8rem;
      font-weight: 500;
      color: var(--muted);
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
  </style>
</head>
<body>
  <header class="thm-header">
    <p class="project-title">{page_title}</p>
  </header>
  <main>
{body}
  </main>
</body>
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
