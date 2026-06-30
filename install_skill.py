#!/usr/bin/env python
"""Install the generic `thm-agent-client` Agent Skill into a directory you choose.

This is deliberately client-agnostic: it does NOT detect your editor, does NOT
manage `.claude/`, `.cursor/`, or any client config, and does NOT guess where your
agent looks for skills. You (or your agent) tell it the destination; it copies the
self-contained skill there.

Usage:
    python install_skill.py --print-path          # show the canonical skill path, do nothing
    python install_skill.py --dest <DIR>          # copy the skill into <DIR>/thm-agent-client/
    python install_skill.py --dest <DIR> --force  # overwrite an existing install

See thm-agent/skill/thm-agent-client/INSTALL.md for where each client discovers skills.
"""

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SKILL_NAME = "thm-agent-client"
CANONICAL_SKILL_DIR = REPO_ROOT / "thm-agent" / "skill" / SKILL_NAME

# Outbound links in the guides use this repo-relative prefix, which only resolves
# when the copy sits 3 levels below the repo root. Rewrite it to an absolute path
# so the installed copy works wherever it lands.
OUTBOUND_PREFIX = "](../../../"
EXCLUDE = {"_archived-notes", "__pycache__"}


def _rewrite_outbound_links(md_path: Path) -> None:
    """Repoint `](../../../foo)` links to absolute repo-root paths, in place."""
    text = md_path.read_text(encoding="utf-8")
    if OUTBOUND_PREFIX not in text:
        return
    abs_prefix = "](" + REPO_ROOT.as_posix() + "/"
    md_path.write_text(text.replace(OUTBOUND_PREFIX, abs_prefix), encoding="utf-8")


def install_skill(dest: Path, force: bool = False) -> Path:
    """Copy the skill folder into dest/thm-agent-client/. Returns the install path."""
    if not CANONICAL_SKILL_DIR.is_dir():
        raise FileNotFoundError(f"Canonical skill not found at {CANONICAL_SKILL_DIR}")

    dest = Path(dest).expanduser().resolve()
    target = dest / SKILL_NAME

    if target.exists():
        if not force:
            raise FileExistsError(
                f"{target} already exists. Re-run with --force to overwrite/refresh."
            )
        shutil.rmtree(target)

    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        CANONICAL_SKILL_DIR,
        target,
        ignore=shutil.ignore_patterns(*EXCLUDE),
    )

    # If the copy is NOT at the canonical 3-levels-below-repo-root depth, the
    # `../../../` links would dangle, so rewrite them to absolute paths. Doing it
    # unconditionally is harmless (a same-depth install just gets absolute links).
    for md_path in target.rglob("*.md"):
        _rewrite_outbound_links(md_path)

    return target


def _print_path() -> None:
    print("THM agent skill (canonical source):")
    print(f"  {CANONICAL_SKILL_DIR}")
    print("")
    print("To install into your agent client's skill directory:")
    print(f"  python install_skill.py --dest <YOUR_CLIENT_SKILLS_DIR>")
    print("See thm-agent/skill/thm-agent-client/INSTALL.md for details.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the generic thm-agent-client Agent Skill into a directory you choose."
    )
    parser.add_argument("--dest", help="Destination directory; skill copied to <dest>/thm-agent-client/")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing install")
    parser.add_argument("--print-path", action="store_true", help="Print the canonical skill path and exit")
    args = parser.parse_args(argv)

    if not args.dest or args.print_path:
        _print_path()
        return 0

    try:
        target = install_skill(Path(args.dest), force=args.force)
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Installed skill to: {target}")
    print("Open SKILL.md there, or confirm it now appears in your client's skills list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
