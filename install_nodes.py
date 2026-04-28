#!/usr/bin/env python3
"""
install_nodes.py - Install ComfyUI custom nodes for The Halleen Machine

Clones required custom node repositories and installs their dependencies.

Usage:
    python install_nodes.py --all           # Install all nodes
    python install_nodes.py --core          # Install core nodes only
    python install_nodes.py --2char         # Install 2CHAR-specific nodes only
    python install_nodes.py --list          # Show available nodes
    python install_nodes.py --requirements  # Just install requirements for existing nodes
    python install_nodes.py --dry-run --all # Show what would be installed
"""

import sys
import os
import subprocess
from pathlib import Path
import argparse

# ---------------------------------------------------------------------------
# Node Registry: name -> (url, recursive, category, description)
# ---------------------------------------------------------------------------
NODE_REGISTRY = {
    # Core nodes (required for basic functionality)
    "ComfyUI-Manager": (
        "https://github.com/ltdrdata/ComfyUI-Manager.git",
        False, "core",
        "Node manager for ComfyUI - enables easy installation of additional nodes"
    ),
    "comfyui_controlnet_aux": (
        "https://github.com/Fannovel16/comfyui_controlnet_aux",
        False, "core",
        "ControlNet auxiliary preprocessors for pose detection"
    ),
    "rgthree-comfy": (
        "https://github.com/rgthree/rgthree-comfy",
        False, "core",
        "Quality of life nodes including context switches and power lora"
    ),
    "ComfyUI_Comfyroll_CustomNodes": (
        "https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes",
        False, "core",
        "Utility nodes for workflow building"
    ),
    "ComfyUI-Easy-Use": (
        "https://github.com/yolain/ComfyUI-Easy-Use",
        False, "core",
        "Simplified workflow nodes"
    ),
    "ComfyUI-Crystools": (
        "https://github.com/crystian/ComfyUI-Crystools",
        False, "core",
        "Crystal tools for debugging and workflow management"
    ),
    "ComfyUI-Custom-Scripts": (
        "https://github.com/pythongosssss/ComfyUI-Custom-Scripts",
        False, "core",
        "Custom scripts and UI enhancements"
    ),
    "ComfyUI-Impact-Pack": (
        "https://github.com/ltdrdata/ComfyUI-Impact-Pack",
        True, "core",  # Needs --recursive
        "Detection, segmentation, and inpainting nodes"
    ),
    "ComfyUI-KJNodes": (
        "https://github.com/kijai/ComfyUI-KJNodes",
        False, "core",
        "KJ's utility nodes"
    ),
    "ComfyUI-VideoHelperSuite": (
        "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
        False, "core",
        "Video loading, saving, and manipulation"
    ),
    "ComfyUI-Frame-Interpolation": (
        "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation",
        False, "core",
        "Frame interpolation for smoother video output"
    ),
    "comfyui-art-venture": (
        "https://github.com/sipherxyz/comfyui-art-venture",
        False, "core",
        "Art venture utility nodes"
    ),
    "ComfyUI-LogicUtils": (
        "https://github.com/aria1th/ComfyUI-LogicUtils",
        False, "core",
        "Logic and utility nodes"
    ),
    
    # 2CHAR-specific nodes (optional, for 2-character workflow)
    "ComfyUI_LayerStyle": (
        "https://github.com/chflame163/ComfyUI_LayerStyle",
        False, "2char",
        "Layer style and compositing nodes"
    ),
    "efficiency-nodes-comfyui": (
        "https://github.com/jags111/efficiency-nodes-comfyui",
        False, "2char",
        "Efficiency nodes for optimized workflows"
    ),
    "ComfyUI_essentials": (
        "https://github.com/cubiq/ComfyUI_essentials",
        False, "2char",
        "Essential utility nodes"
    ),
    "ComfyUI-Inpaint-CropAndStitch": (
        "https://github.com/lquesada/ComfyUI-Inpaint-CropAndStitch",
        False, "2char",
        "Improved inpainting with crop and stitch"
    ),
    "comfy_mtb": (
        "https://github.com/melMass/comfy_mtb",
        False, "2char",
        "MTB nodes for various utilities"
    ),
    "was-node-suite-comfyui": (
        "https://github.com/WASasquatch/was-node-suite-comfyui",
        False, "2char",
        "WAS node suite - comprehensive utility collection"
    ),
}

IS_WINDOWS = sys.platform == "win32"


def load_config() -> dict:
    """Load config.toml and return ComfyUI path."""
    config_path = Path("config.toml")
    
    if not config_path.exists():
        print("❌ config.toml not found!")
        print("   Run 'python setup.py' first to configure paths.")
        sys.exit(1)
    
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomli as tomllib
        except ImportError:
            print("❌ tomli not installed (required for Python < 3.11)")
            print("   Run: pip install tomli")
            sys.exit(1)
    
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        print(f"❌ Failed to parse config.toml: {e}")
        sys.exit(1)
    
    install_path = data.get("comfyui", {}).get("install_path", "")
    if not install_path:
        print("❌ [comfyui] install_path not set in config.toml")
        print("   Run 'python setup.py' to configure.")
        sys.exit(1)
    
    return {"install_path": Path(install_path)}


def get_comfy_pip(comfy_root: Path) -> Path:
    """Get path to ComfyUI's pip executable."""
    if IS_WINDOWS:
        pip_path = comfy_root / "venv" / "Scripts" / "pip.exe"
    else:
        pip_path = comfy_root / "venv" / "bin" / "pip"
    
    if not pip_path.exists():
        # Try without venv (system install)
        return Path("pip")
    
    return pip_path


def clone_node(name: str, url: str, recursive: bool, dest_dir: Path, dry_run: bool = False) -> bool:
    """Clone a node repository."""
    node_path = dest_dir / name
    
    if node_path.exists():
        print(f"  ✓ Already exists: {name}")
        return True
    
    if dry_run:
        rec_flag = " --recursive" if recursive else ""
        print(f"  Would clone: git clone{rec_flag} {url}")
        return True
    
    try:
        cmd = ["git", "clone"]
        if recursive:
            cmd.append("--recursive")
        cmd.extend([url, str(node_path)])
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ❌ Failed to clone {name}: {result.stderr.strip()}")
            return False
        
        print(f"  ✓ Cloned: {name}")
        return True
        
    except Exception as e:
        print(f"  ❌ Error cloning {name}: {e}")
        return False


def install_requirements(name: str, node_path: Path, pip_path: Path, dry_run: bool = False) -> bool:
    """Install requirements.txt for a node if it exists."""
    req_file = node_path / "requirements.txt"
    
    if not req_file.exists():
        return True  # No requirements, that's fine
    
    if dry_run:
        print(f"  Would install: {pip_path} install -r {req_file}")
        return True
    
    try:
        result = subprocess.run(
            [str(pip_path), "install", "-r", str(req_file)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"  ⚠️  Requirements install warning for {name}: {result.stderr.strip()[:200]}")
            # Don't fail on requirements - they often have version conflicts that still work
        return True
        
    except Exception as e:
        print(f"  ⚠️  Error installing requirements for {name}: {e}")
        return True  # Continue anyway


def install_node(name: str, comfy_root: Path, dry_run: bool = False) -> tuple[bool, bool]:
    """
    Install a single node.
    
    Returns:
        (cloned: bool, was_existing: bool)
    """
    if name not in NODE_REGISTRY:
        print(f"  ❌ Unknown node: {name}")
        return False, False
    
    url, recursive, category, description = NODE_REGISTRY[name]
    custom_nodes_dir = comfy_root / "custom_nodes"
    node_path = custom_nodes_dir / name
    pip_path = get_comfy_pip(comfy_root)
    
    was_existing = node_path.exists()
    
    if was_existing:
        print(f"  ✓ Already exists: {name}")
        # Still install requirements in case they were updated
        install_requirements(name, node_path, pip_path, dry_run)
        return True, True
    
    # Clone
    if not clone_node(name, url, recursive, custom_nodes_dir, dry_run):
        return False, False
    
    # Install requirements
    if not dry_run:
        install_requirements(name, node_path, pip_path, dry_run)
    
    return True, False


def list_nodes():
    """List all available nodes."""
    print()
    print("=" * 70)
    print("  Available Custom Nodes")
    print("=" * 70)
    print()
    
    print("CORE NODES (required for basic functionality):")
    print()
    for name, (url, recursive, category, desc) in NODE_REGISTRY.items():
        if category == "core":
            rec = " [recursive]" if recursive else ""
            print(f"  • {name}{rec}")
            print(f"    {desc}")
    print()
    
    print("2CHAR NODES (optional, for 2-character workflow):")
    print()
    for name, (url, recursive, category, desc) in NODE_REGISTRY.items():
        if category == "2char":
            print(f"  • {name}")
            print(f"    {desc}")
    print()
    
    print("-" * 70)
    print("Install commands:")
    print("  python install_nodes.py --all     # All nodes")
    print("  python install_nodes.py --core    # Core nodes only")
    print("  python install_nodes.py --2char   # 2CHAR nodes only")
    print()


def run_install(categories: list[str], dry_run: bool = False, requirements_only: bool = False) -> tuple[int, int, int]:
    """
    Run installation for specified categories.
    
    Returns:
        (installed_count, existing_count, failed_count)
    """
    config = load_config()
    comfy_root = config["install_path"]
    custom_nodes_dir = comfy_root / "custom_nodes"
    
    print()
    print("=" * 70)
    print("  The Halleen Machine - Custom Node Installer")
    print("=" * 70)
    
    if dry_run:
        print("  [DRY RUN - no changes will be made]")
    
    print()
    print(f"ComfyUI install:  {comfy_root}")
    print(f"Custom nodes dir: {custom_nodes_dir}")
    print(f"ComfyUI pip:      {get_comfy_pip(comfy_root)}")
    print()
    
    if not custom_nodes_dir.exists():
        if dry_run:
            print(f"Would create: {custom_nodes_dir}")
        else:
            custom_nodes_dir.mkdir(parents=True, exist_ok=True)
    
    # Build list of nodes to install
    nodes_to_install = [
        name for name, (url, rec, cat, desc) in NODE_REGISTRY.items()
        if cat in categories
    ]
    
    print("-" * 70)
    if requirements_only:
        print(f"Installing requirements for {len(nodes_to_install)} nodes...")
    else:
        print(f"Installing {len(nodes_to_install)} nodes...")
    print("-" * 70)
    print()
    
    installed = 0
    existing = 0
    failed = 0
    
    pip_path = get_comfy_pip(comfy_root)
    
    for name in nodes_to_install:
        if requirements_only:
            node_path = custom_nodes_dir / name
            if node_path.exists():
                print(f"  {name}")
                install_requirements(name, node_path, pip_path, dry_run)
                existing += 1
        else:
            success, was_existing = install_node(name, comfy_root, dry_run)
            if success:
                if was_existing:
                    existing += 1
                else:
                    installed += 1
            else:
                failed += 1
    
    return installed, existing, failed


def main():
    parser = argparse.ArgumentParser(
        description="Install ComfyUI custom nodes for The Halleen Machine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python install_nodes.py --all
  python install_nodes.py --core
  python install_nodes.py --2char
  python install_nodes.py --requirements
  python install_nodes.py --dry-run --all
        """
    )
    
    parser.add_argument("--core", action="store_true",
                        help="Install core nodes only")
    parser.add_argument("--2char", dest="twochar", action="store_true",
                        help="Install 2CHAR-specific nodes only")
    parser.add_argument("--all", action="store_true",
                        help="Install all nodes")
    parser.add_argument("--requirements", action="store_true",
                        help="Just install requirements for existing nodes")
    parser.add_argument("--list", action="store_true",
                        help="List available nodes")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be installed without installing")
    
    args = parser.parse_args()
    
    if args.list:
        list_nodes()
        return 0
    
    # Determine categories
    categories = []
    if args.all:
        categories = ["core", "2char"]
    else:
        if args.core:
            categories.append("core")
        if args.twochar:
            categories.append("2char")
    
    if not categories and not args.requirements:
        parser.print_help()
        return 1
    
    if args.requirements:
        categories = ["core", "2char"]  # Check all for requirements
    
    installed, existing, failed = run_install(
        categories,
        dry_run=args.dry_run,
        requirements_only=args.requirements
    )
    
    # Summary
    print()
    print("=" * 70)
    parts = []
    if installed > 0:
        parts.append(f"{installed} installed")
    if existing > 0:
        parts.append(f"{existing} already existed")
    if failed > 0:
        parts.append(f"{failed} failed")
    
    if parts:
        print(f"  Complete: {', '.join(parts)}")
    else:
        print("  Complete: Nothing to install")
    print("=" * 70)
    
    if failed > 0:
        print()
        print("Some nodes failed to install. Check the output above for details.")
        print("You may need to install them manually.")
        return 1
    
    if installed > 0 and not args.dry_run:
        print()
        print("Restart ComfyUI to load the new nodes.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())