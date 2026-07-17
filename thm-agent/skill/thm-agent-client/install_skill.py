#!/usr/bin/env python3
"""Install the thm-agent-client skill into an agent client's skills directory.

Copies this skill folder to ``<dest>/thm-agent-client/``, excluding material that is
private to skill maintenance and should not ship to a fresh install:

  - ``_archived-notes/``           (merged staging + audit history, carries private
                                    project recipes)
  - ``skill-improvement-notes*``   (any active staging file)
  - ``INSTALL.md`` and this script (installer plumbing, not skill content)

By default it also rewrites the guides' ``../../../`` repo-relative links to absolute
repo paths so they resolve no matter how deep the install directory sits. Pass
``--keep-relative`` to leave them untouched (only correct when the install sits 3 levels
below the repo root, the same depth as the source). The skill drives the THM CLI/builder,
so it is meant to live inside this repo on the same machine — absolute repo links are fine.

Usage:
    python install_skill.py --dest <your client's skills dir>
    python install_skill.py --dest .claude/skills --prune
    python install_skill.py --dest .claude/skills --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
REPO_ROOT = SRC.parents[2]  # thm-agent-client -> skill -> thm-agent -> repo root
SKILL_NAME = SRC.name  # "thm-agent-client"

EXCLUDE_DIRS = {"_archived-notes", "__pycache__"}
EXCLUDE_FILES = {"install_skill.py", "INSTALL.md"}
EXCLUDE_SUFFIXES = {".zip", ".pyc"}  # packaging/build artifacts, not skill content


def _is_staging(name: str) -> bool:
    return name.startswith("skill-improvement-notes") and name.endswith(".md")


def _iter_src_files():
    """Yield skill files (relative to SRC) that should be installed."""
    for path in SRC.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(SRC)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name in EXCLUDE_FILES or _is_staging(rel.name):
            continue
        if rel.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        yield rel


def _rewrite_links(text: str) -> str:
    return text.replace("../../../", REPO_ROOT.as_posix() + "/")


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the thm-agent-client skill folder.")
    ap.add_argument(
        "--dest",
        required=True,
        type=Path,
        help="Your client's skills directory; the skill installs to <dest>/thm-agent-client/",
    )
    ap.add_argument(
        "--keep-relative",
        action="store_true",
        help="Do not rewrite ../../../ links (only correct if installed 3 levels below repo root)",
    )
    ap.add_argument(
        "--prune",
        action="store_true",
        help="Remove files in the destination that are no longer part of the skill",
    )
    ap.add_argument("--dry-run", action="store_true", help="Show what would happen; change nothing")
    args = ap.parse_args()

    dest_root = args.dest.resolve() / SKILL_NAME
    if dest_root == SRC:
        print("ERROR: --dest points at the source folder itself; choose a different directory.")
        return 1

    src_files = sorted(_iter_src_files(), key=str)
    copied: list[Path] = []
    rewritten: list[Path] = []
    unchanged: list[Path] = []

    for rel in src_files:
        src_path = SRC / rel
        dst_path = dest_root / rel
        is_md = rel.suffix.lower() == ".md"
        if is_md and not args.keep_relative:
            original = src_path.read_text(encoding="utf-8")
            new_text = _rewrite_links(original)
            data = new_text.encode("utf-8")
            did_rewrite = new_text != original
        else:
            data = src_path.read_bytes()
            did_rewrite = False

        if dst_path.exists() and dst_path.read_bytes() == data:
            unchanged.append(rel)
            continue

        copied.append(rel)
        if did_rewrite:
            rewritten.append(rel)
        if not args.dry_run:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            dst_path.write_bytes(data)

    # Detect (and optionally remove) files in the destination that are no longer ours.
    stale: list[Path] = []
    if dest_root.exists():
        managed = {str(rel) for rel in src_files}
        for path in sorted(dest_root.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(dest_root)
            if str(rel) not in managed:
                stale.append(rel)
                if args.prune and not args.dry_run:
                    path.unlink()

    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}Install target: {dest_root}")
    print(f"{prefix}Copied/updated: {len(copied)} file(s)")
    for rel in copied:
        print(f"  + {rel.as_posix()}")
    print(f"{prefix}Unchanged: {len(unchanged)} file(s)")
    if rewritten:
        print(f"{prefix}Rewrote ../../../ links in {len(rewritten)} guide(s) -> {REPO_ROOT.as_posix()}/")
    elif args.keep_relative:
        print(f"{prefix}Left ../../../ links relative (--keep-relative)")
    if stale:
        verb = "Removed" if (args.prune and not args.dry_run) else "Stale (pass --prune to remove)"
        print(f"{prefix}{verb}: {len(stale)} file(s)")
        for rel in stale:
            print(f"  - {rel.as_posix()}")
    print(f"{prefix}Done. Confirm '{SKILL_NAME}' appears in your client's skills list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
