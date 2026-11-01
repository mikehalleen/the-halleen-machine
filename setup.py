#!/usr/bin/env python3
"""
setup.py - Configuration wizard for The Halleen Machine 2.0

Run once after installation to write config.toml. Or copy config.toml.example
to config.toml and edit it by hand; every key is documented there.

THM does not install models or workflows for you. Bring your own ComfyUI
workflows, tag them as WORKFLOWS.md describes, and drop them in workflows_image/
or workflows_video/.

Usage:
    python setup.py              # interactive
    python setup.py --defaults   # auto-detect, no prompts
    python setup.py --check      # validate the existing config.toml
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "config.toml"

# Only auto-created when the user keeps this exact value; a custom path is theirs to manage.
WORKSPACE_DEFAULT = "./projects"
API_BASE_DEFAULT = "http://127.0.0.1:8188"
TIMEOUT_DEFAULT = 3600
VIDEO_TIMEOUT_DEFAULT = 4 * 3600
BACKUP_RETENTION_DEFAULT = 50
BACKUP_THROTTLE_DEFAULT = 300

COMFY_SEARCH_PATHS = [
    # Linux / RunPod
    "/workspace/ComfyUI",
    "/home/ComfyUI",
    str(Path.home() / "ComfyUI"),
    # Windows
    "D:/ComfyUI",
    "C:/ComfyUI",
    # Portable installs beside the repo
    "../ComfyUI",
    "./ComfyUI",
]

TOTAL_STEPS = 4


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------


def clear_screen() -> None:
    os.system("cls" if IS_WINDOWS else "clear")


def print_header(step: int | None = None, title: str | None = None) -> None:
    print()
    print("=" * 60)
    print("  The Halleen Machine 2.0 - Setup")
    print("=" * 60)
    if step is not None and title:
        print("-" * 60)
        print(f"STEP {step} of {TOTAL_STEPS}: {title}")
        print("-" * 60)
    print()


def prompt_yes_no(question: str, default: bool = True) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        answer = input(question + suffix).strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'")


def prompt_text(prompt: str, default: str = "") -> str:
    shown = f"{prompt} [{default}]: " if default else f"{prompt}: "
    answer = input(shown).strip()
    return answer or default


def prompt_path(prompt: str, default: str = "", must_exist: bool = False, allow_empty: bool = False) -> str:
    """A path, kept relative when the user types one (./projects), resolved otherwise."""
    while True:
        answer = prompt_text(prompt, default)
        if not answer:
            if allow_empty:
                return ""
            print("Path cannot be empty")
            continue
        if answer.startswith("./") or answer.startswith("../"):
            return answer
        path = Path(answer).expanduser().resolve()
        if must_exist and not path.exists():
            print(f"Path does not exist: {path}")
            if not prompt_yes_no("Use it anyway?", default=False):
                continue
        return str(path)


# ---------------------------------------------------------------------------
# ComfyUI detection
# ---------------------------------------------------------------------------


def find_comfyui() -> Path | None:
    for candidate in COMFY_SEARCH_PATHS:
        path = Path(candidate).resolve()
        if (path / "main.py").exists():
            return path
    return None


def validate_comfyui_path(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"Path does not exist: {path}"
    if not (path / "main.py").exists():
        return False, f"main.py not found in {path} - is this a ComfyUI installation?"
    return True, "OK"


def probe_api(api_base: str, timeout: float = 3.0) -> tuple[bool, str]:
    """GET /system_stats; a reachable ComfyUI answers it. Never raises."""
    url = api_base.rstrip("/") + "/system_stats"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status == 200:
                return True, "ComfyUI is answering"
            return False, f"HTTP {resp.status} from {url}"
    except urllib.error.URLError as e:
        return False, f"not reachable ({e.reason})"
    except Exception as e:  # noqa: BLE001 - a probe, any failure is just "no"
        return False, f"not reachable ({e})"


# ---------------------------------------------------------------------------
# config.toml
# ---------------------------------------------------------------------------


def norm(path: str) -> str:
    """TOML-friendly path: forward slashes, no trailing slash."""
    return str(path).replace("\\", "/").rstrip("/") if path else ""


def generate_config_toml(values: dict) -> str:
    return f'''# The Halleen Machine 2.0 configuration
# Generated by setup.py. Edit freely; config.toml.example documents every key.

[comfyui]
# ComfyUI's API address. Use the machine's LAN address to reach a ComfyUI on
# another box (e.g. "http://192.168.1.100:8188").
api_base = "{values["api_base"]}"

# Where ComfyUI writes its renders. THM reads every finished image and video
# from here, so this must be the folder ComfyUI itself is configured to use.
output_root = "{norm(values["output_root"])}"

# How long one prompt may execute before THM gives up on it (queue time does
# not count). Video gets its own, longer limit.
timeout_seconds = {int(values["timeout_seconds"])}
video_timeout_seconds = {int(values["video_timeout_seconds"])}

[paths]
# Where projects are stored. "./projects" keeps them inside the repo; an
# absolute path keeps them anywhere else. serve.py --projects-dir overrides it.
workspace = "{norm(values["workspace"])}"

# ComfyUI's models/loras folder, listed by the LoRA picker. Optional.
loras = "{norm(values["loras"])}"

[backups]
# Project files are backed up before each write, at most once per throttle
# window; the oldest beyond retention are dropped.
retention = {int(values["retention"])}
throttle_seconds = {int(values["throttle_seconds"])}
'''


def load_existing_config(path: Path) -> dict | None:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  Could not parse {path.name}: {e}")
        return None


def values_from_config(data: dict) -> dict:
    comfy = data.get("comfyui") or {}
    paths = data.get("paths") or {}
    backups = data.get("backups") or {}
    return {
        "api_base": str(comfy.get("api_base") or API_BASE_DEFAULT),
        "output_root": str(comfy.get("output_root") or ""),
        "timeout_seconds": int(comfy.get("timeout_seconds") or TIMEOUT_DEFAULT),
        "video_timeout_seconds": int(comfy.get("video_timeout_seconds") or VIDEO_TIMEOUT_DEFAULT),
        "workspace": str(paths.get("workspace") or WORKSPACE_DEFAULT),
        "loras": str(paths.get("loras") or ""),
        "retention": int(backups.get("retention") or BACKUP_RETENTION_DEFAULT),
        "throttle_seconds": int(backups.get("throttle_seconds") or BACKUP_THROTTLE_DEFAULT),
    }


def default_values(comfy_root: Path | None) -> dict:
    return {
        "api_base": API_BASE_DEFAULT,
        "output_root": str(comfy_root / "output") if comfy_root else "",
        "timeout_seconds": TIMEOUT_DEFAULT,
        "video_timeout_seconds": VIDEO_TIMEOUT_DEFAULT,
        "workspace": WORKSPACE_DEFAULT,
        "loras": str(comfy_root / "models" / "loras") if comfy_root else "",
        "retention": BACKUP_RETENTION_DEFAULT,
        "throttle_seconds": BACKUP_THROTTLE_DEFAULT,
    }


def write_config(values: dict) -> None:
    CONFIG_PATH.write_text(generate_config_toml(values), encoding="utf-8")
    if values["workspace"] == WORKSPACE_DEFAULT:
        (REPO_ROOT / "projects").mkdir(parents=True, exist_ok=True)


def validate_config(path: Path) -> tuple[bool, list[str]]:
    """(ok, messages). Missing output_root is the one hard error: nothing renders without it."""
    if not path.exists():
        return False, [f"{path.name} not found - run: python setup.py"]
    data = load_existing_config(path)
    if data is None:
        return False, [f"{path.name} does not parse"]
    v = values_from_config(data)
    problems: list[str] = []
    notes: list[str] = []

    if not v["output_root"]:
        problems.append("[comfyui] output_root is empty; nothing can render")
    elif not Path(v["output_root"]).exists():
        notes.append(f"[comfyui] output_root does not exist yet: {v['output_root']}")

    ok, msg = probe_api(v["api_base"])
    (notes if ok else notes).append(f"[comfyui] api_base {v['api_base']}: {msg}")

    ws = Path(v["workspace"])
    ws = ws if ws.is_absolute() else (REPO_ROOT / ws)
    if not ws.exists():
        notes.append(f"[paths] workspace does not exist yet: {ws} (the server creates it)")

    if v["loras"] and not Path(v["loras"]).exists():
        notes.append(f"[paths] loras does not exist: {v['loras']} (the LoRA picker will be empty)")

    return not problems, problems + notes


# ---------------------------------------------------------------------------
# Optional post-steps
# ---------------------------------------------------------------------------


def offer_agent_skill() -> None:
    skill_src = Path("thm-agent") / "skill" / "thm-agent"
    print()
    if prompt_yes_no("Install the THM agent skill into a directory now?", default=False):
        dest = input("Target skills directory (Enter to skip): ").strip()
        if dest:
            try:
                from install_skill import install_skill as _install_skill

                target = _install_skill(Path(dest), force=False)
                print(f"Installed skill to: {target}")
            except Exception as e:  # noqa: BLE001
                print(f"Skill install skipped: {e}")
                print("   Install later with: python install_skill.py --dest <dir>")
        else:
            print("Skipped.")
    print(f"Agent skill (client-neutral) lives at: {skill_src}")
    print(f"   To use it in your agent client, see {skill_src / 'SKILL.md'}")


def print_finish() -> None:
    print()
    print("=" * 60)
    print("Setup complete.")
    print()
    print("  Put your tagged ComfyUI workflows in workflows_image/ and workflows_video/")
    print("  (WORKFLOWS.md explains the tags), then:")
    print()
    print("      python serve.py")
    print()
    print("  and open http://127.0.0.1:8010")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def run_interactive() -> int:
    clear_screen()
    print_header()

    current: dict | None = None
    if CONFIG_PATH.exists():
        print("Found existing config.toml")
        data = load_existing_config(CONFIG_PATH)
        if data is not None:
            current = values_from_config(data)
            ok, messages = validate_config(CONFIG_PATH)
            for m in messages:
                print(f"  - {m}")
            print()
            print("[Enter] Keep it  |  [r] Reconfigure")
            if input("Choice: ").strip().lower() != "r":
                print("\nConfiguration unchanged.")
                return 0 if ok else 1
        elif not prompt_yes_no("Generate a new configuration?", default=True):
            return 1

    # ---- Step 1: ComfyUI location ------------------------------------------
    clear_screen()
    print_header(1, "ComfyUI")
    print("Where is ComfyUI installed? This only fills in the output and LoRA")
    print("folders below; it is not saved. Leave it empty if ComfyUI runs on")
    print("another machine; you then type the folders yourself.")
    print()

    comfy_root: Path | None = None
    have_folders = bool(current and current.get("output_root"))
    if have_folders:
        print(f"Current output folder: {current['output_root']}")
        print("(Enter a ComfyUI folder below only to re-derive the output and LoRA folders.)")
    if not have_folders:
        detected = find_comfyui()
        if detected:
            print(f"Found ComfyUI at: {detected}")
            if prompt_yes_no("Use this installation?"):
                comfy_root = detected
        else:
            print("Could not auto-detect ComfyUI.")
    if comfy_root is None:
        typed = prompt_path("ComfyUI folder (Enter to skip)", must_exist=True, allow_empty=True)
        comfy_root = Path(typed) if typed else None
    if comfy_root is not None:
        ok, msg = validate_comfyui_path(comfy_root)
        if not ok:
            print(msg)
            if not prompt_yes_no("Continue anyway?", default=False):
                return 1

    values = default_values(comfy_root)
    if current:
        # Keep what the user already had; a ComfyUI folder typed just now wins for the
        # two folders it derives, everything else fills only what was empty.
        for key in ("output_root", "loras"):
            values[key] = values[key] if comfy_root else (current.get(key) or values[key])
        for key in ("workspace", "api_base"):
            values[key] = current.get(key) or values[key]
        for key in ("timeout_seconds", "video_timeout_seconds", "retention", "throttle_seconds"):
            values[key] = current.get(key, values[key])

    # ---- Step 2: ComfyUI API and output ------------------------------------
    clear_screen()
    print_header(2, "ComfyUI API and output folder")
    values["api_base"] = prompt_text("ComfyUI API address", values["api_base"])
    ok, msg = probe_api(values["api_base"])
    print(f"  {msg}" if ok else f"  {msg} - fine if ComfyUI is not running yet")
    print()
    print("THM reads renders from ComfyUI's output folder. It must be the same")
    print("folder ComfyUI saves to (its --output-directory, or <ComfyUI>/output).")
    values["output_root"] = prompt_path("ComfyUI output folder", values["output_root"], must_exist=True)

    # ---- Step 3: Projects and LoRAs ----------------------------------------
    clear_screen()
    print_header(3, "Projects and LoRAs")
    print("Where should project files be saved? './projects' keeps them with the")
    print("app; an absolute path keeps them anywhere else.")
    print()
    values["workspace"] = prompt_path("Projects folder", values["workspace"])
    print()
    print("ComfyUI's models/loras folder fills the LoRA picker. Optional.")
    values["loras"] = prompt_path("LoRA folder (Enter to skip)", values["loras"], must_exist=True, allow_empty=True)

    # ---- Step 4: Write --------------------------------------------------------
    clear_screen()
    print_header(4, "Write config.toml")
    print(f"  api_base:      {values['api_base']}")
    print(f"  output_root:   {values['output_root']}")
    print(f"  workspace:     {values['workspace']}")
    print(f"  loras:         {values['loras'] or '(not set)'}")
    print(f"  timeouts:      {values['timeout_seconds']} s image / {values['video_timeout_seconds']} s video")
    print()
    if not prompt_yes_no("Write config.toml?"):
        print("Nothing written.")
        return 1
    write_config(values)
    print(f"Wrote {CONFIG_PATH}")

    offer_agent_skill()
    print_finish()
    return 0


def run_defaults() -> int:
    print("Running setup with auto-detected defaults...")
    comfy_root = find_comfyui()
    if comfy_root:
        print(f"  ComfyUI: {comfy_root}")
    else:
        print("  ComfyUI not found; output_root left empty. Edit config.toml before rendering.")
    write_config(default_values(comfy_root))
    print(f"Wrote {CONFIG_PATH}")
    ok, messages = validate_config(CONFIG_PATH)
    for m in messages:
        print(f"  - {m}")
    print_finish()
    return 0 if ok else 1


def run_check() -> int:
    ok, messages = validate_config(CONFIG_PATH)
    print("config.toml:", "OK" if ok else "PROBLEMS")
    for m in messages:
        print(f"  - {m}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="The Halleen Machine 2.0 setup")
    ap.add_argument("--defaults", action="store_true", help="auto-detect and write config.toml without prompts")
    ap.add_argument("--check", action="store_true", help="validate the existing config.toml")
    args = ap.parse_args()
    if args.check:
        return run_check()
    if args.defaults:
        return run_defaults()
    try:
        return run_interactive()
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
