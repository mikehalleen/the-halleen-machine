#!/usr/bin/env python3
"""
download_models.py - Download models for The Halleen Machine

Downloads model files to the appropriate directories based on config.toml.

Usage:
    python download_models.py --checkpoints sdXL_v10VAEFix.safetensors
    python download_models.py --controlnet diffusion_pytorch_model_promax.safetensors
    python download_models.py --all                    # Download all default models
    python download_models.py --list                   # Show available models
    python download_models.py --dry-run --all          # Show what would be downloaded
"""

import sys
import os
from pathlib import Path
from typing import Optional
import argparse

# ---------------------------------------------------------------------------
# Model Registry: filename -> (url, subfolder, description)
# ---------------------------------------------------------------------------
MODEL_REGISTRY = {
    # Checkpoints
    "sdXL_v10VAEFix.safetensors": (
        "https://madels-for-machine.s3.us-west-2.amazonaws.com/sdXL_v10VAEFix.safetensors",
        "checkpoints",
        "SDXL base model with VAE fix. Used as default project model and fast pose model."
    ),
    "obsessionIllustrious_v21.safetensors": (
        "https://madels-for-machine.s3.us-west-2.amazonaws.com/obsessionIllustrious_v21.safetensors",
        "checkpoints",
        "Illustrious style model. Used for enhanced pose generation."
    ),
    "juggernautxl-inpainting.safetensors": (
        "https://madels-for-machine.s3.us-west-2.amazonaws.com/juggernautxl-inpainting.safetensors",
        "checkpoints",
        "Juggernaut XL inpainting model. Used for 2CHAR heal process."
    ),
    
    # ControlNet
    "diffusion_pytorch_model_promax.safetensors": (
        "https://madels-for-machine.s3.us-west-2.amazonaws.com/diffusion_pytorch_model_promax.safetensors",
        "controlnet",
        "ControlNet ProMax model. Used for pose-based keyframe generation."
    ),
    
    # Upscale
    "4x_NMKD-Siax_200k.pth": (
        "https://madels-for-machine.s3.us-west-2.amazonaws.com/4x_NMKD-Siax_200k.pth",
        "upscale_models",
        "4x NMKD Siax upscaler. Used for enhancing output resolution."
    ),
    
    # ==========================================================================
    # WAN 2.2 Video Generation Models (for i2v_base.json / i2v_bridge.json)
    # ==========================================================================
    
    # CLIP / Text Encoder
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "text_encoders",
        "UMT5-XXL text encoder for WAN 2.2 video generation."
    ),
    
    # VAE
    "wan_2.1_vae.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors",
        "vae",
        "WAN 2.1 VAE for video encoding/decoding."
    ),
    
    # Diffusion Models (UNETs)
    "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "diffusion_models",
        "WAN 2.2 Image-to-Video high noise model (14B params, FP8)."
    ),
    "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "diffusion_models",
        "WAN 2.2 Image-to-Video low noise model (14B params, FP8)."
    ),
    
    # LoRAs for WAN 2.2
    "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        "loras",
        "WAN 2.2 LightX2V 4-step LoRA (high noise). Enables fast 4-step generation."
    ),
    "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors": (
        "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        "loras",
        "WAN 2.2 LightX2V 4-step LoRA (low noise). Enables fast 4-step generation."
    ),
}

# Default models to download with --all
DEFAULT_MODELS = [
    "sdXL_v10VAEFix.safetensors",
    "obsessionIllustrious_v21.safetensors",
    "juggernautxl-inpainting.safetensors",
    "diffusion_pytorch_model_promax.safetensors",
    "4x_NMKD-Siax_200k.pth",
]

# Video generation models (--video flag)
VIDEO_MODELS = [
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "wan_2.1_vae.safetensors",
    "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
]


def load_config() -> dict:
    """Load config.toml and return paths."""
    config_path = Path("config.toml")
    
    if not config_path.exists():
        print("❌ config.toml not found!")
        print("   Run 'python setup.py' first to configure paths.")
        sys.exit(1)
    
    # Load TOML
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
    
    # Extract paths
    install_path = data.get("comfyui", {}).get("install_path", "")
    if not install_path:
        print("❌ [comfyui] install_path not set in config.toml")
        print("   Run 'python setup.py' to configure.")
        sys.exit(1)
    
    install_path = Path(install_path)
    
    return {
        "install_path": install_path,
        "checkpoints": Path(data.get("paths", {}).get("models", install_path / "models" / "checkpoints")),
        "controlnet": Path(data.get("paths", {}).get("controlnet", install_path / "models" / "controlnet")),
        "upscale_models": Path(data.get("paths", {}).get("upscale_models", install_path / "models" / "upscale_models")),
        "loras": Path(data.get("paths", {}).get("loras", install_path / "models" / "loras")),
        "text_encoders": install_path / "models" / "text_encoders",
        "vae": install_path / "models" / "vae",
        "diffusion_models": install_path / "models" / "diffusion_models",
    }


def get_file_size_str(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def download_file(url: str, dest_path: Path, dry_run: bool = False) -> bool:
    """
    Download a file with progress display and resume support.
    
    Returns:
        True if successful, False otherwise
    """
    if not url:
        print(f"  ⚠️  No URL configured for this model")
        return False
    
    if dry_run:
        print(f"  Would download: {url}")
        print(f"  To: {dest_path}")
        return True
    
    # Check if file exists
    if dest_path.exists():
        print(f"  ✓ Already exists: {dest_path.name}")
        return True
    
    # Ensure directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Temp file for partial downloads
    temp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    
    try:
        import urllib.request
        import urllib.error
        
        # Check for existing partial download
        resume_pos = 0
        if temp_path.exists():
            resume_pos = temp_path.stat().st_size
            print(f"  Resuming from {get_file_size_str(resume_pos)}...")
        
        # Create request with range header for resume
        request = urllib.request.Request(url)
        if resume_pos > 0:
            request.add_header("Range", f"bytes={resume_pos}-")
        
        # Open connection
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as e:
            if e.code == 416:  # Range not satisfiable - file complete
                temp_path.rename(dest_path)
                print(f"  ✓ Download complete: {dest_path.name}")
                return True
            raise
        
        # Get total size
        content_length = response.headers.get("Content-Length")
        if content_length:
            total_size = int(content_length) + resume_pos
        else:
            total_size = None
        
        # Download with progress
        block_size = 8192 * 16  # 128KB blocks
        downloaded = resume_pos
        
        mode = "ab" if resume_pos > 0 else "wb"
        with open(temp_path, mode) as f:
            while True:
                block = response.read(block_size)
                if not block:
                    break
                f.write(block)
                downloaded += len(block)
                
                # Progress display
                if total_size:
                    percent = (downloaded / total_size) * 100
                    progress = get_file_size_str(downloaded)
                    total = get_file_size_str(total_size)
                    print(f"\r  Downloading: {progress} / {total} ({percent:.1f}%)  ", end="", flush=True)
                else:
                    print(f"\r  Downloading: {get_file_size_str(downloaded)}  ", end="", flush=True)
        
        print()  # Newline after progress
        
        # Rename temp to final
        temp_path.rename(dest_path)
        print(f"  ✓ Downloaded: {dest_path.name}")
        return True
        
    except KeyboardInterrupt:
        print("\n  ⚠️  Download interrupted. Run again to resume.")
        return False
    except urllib.error.HTTPError as e:
        print(f"\n  ❌ Download failed: HTTP {e.code} - {e.reason}")
        # Clean up temp file on 404 (file doesn't exist, retry won't help)
        if e.code == 404 and temp_path.exists():
            temp_path.unlink()
        return False
    except urllib.error.URLError as e:
        print(f"\n  ❌ Download failed: {e.reason}")
        return False
    except Exception as e:
        print(f"\n  ❌ Download failed: {e}")
        return False


def download_model(filename: str, paths: dict, dry_run: bool = False) -> bool:
    """Download a single model by filename."""
    if filename not in MODEL_REGISTRY:
        print(f"❌ Unknown model: {filename}")
        print("   Run 'python download_models.py --list' to see available models.")
        return False
    
    url, subfolder, description = MODEL_REGISTRY[filename]
    
    # Determine destination
    if subfolder in paths:
        dest_dir = paths[subfolder]
    else:
        dest_dir = paths["install_path"] / "models" / subfolder
    
    dest_path = dest_dir / filename
    
    print(f"\n{filename}")
    print(f"  {description}")
    print(f"  Destination: {dest_dir}/")
    
    return download_file(url, dest_path, dry_run=dry_run)


def list_models():
    """List all available models."""
    print()
    print("=" * 70)
    print("  Available Models")
    print("=" * 70)
    print()
    
    # Group by subfolder
    by_folder = {}
    for filename, (url, subfolder, desc) in MODEL_REGISTRY.items():
        if subfolder not in by_folder:
            by_folder[subfolder] = []
        by_folder[subfolder].append((filename, url, desc))
    
    # Define display order
    folder_order = ["checkpoints", "controlnet", "upscale_models", 
                    "text_encoders", "vae", "diffusion_models", "loras"]
    
    for folder in folder_order:
        if folder not in by_folder:
            continue
        models = by_folder[folder]
        print(f"[{folder}]")
        for filename, url, desc in models:
            status = "✓" if url else "⚠️ (no URL)"
            print(f"  {status} {filename}")
            print(f"      {desc}")
        print()
    
    print("-" * 70)
    print("Download commands:")
    print("  python download_models.py --all                    # Core models")
    print("  python download_models.py --video                  # Video generation models")
    print("  python download_models.py --checkpoints <name>     # Specific checkpoint")
    print("  python download_models.py --controlnet <name>      # Specific controlnet")
    print("  python download_models.py --upscale <name>         # Specific upscale model")
    print()
    print("See MODELS.md for download links and more info.")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Download models for The Halleen Machine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python download_models.py --all
  python download_models.py --video
  python download_models.py --checkpoints sdXL_v10VAEFix.safetensors
  python download_models.py --checkpoints sdXL_v10VAEFix.safetensors obsessionIllustrious_v21.safetensors
  python download_models.py --dry-run --all
        """
    )
    
    parser.add_argument("--checkpoints", nargs="+", metavar="FILE",
                        help="Checkpoint model(s) to download")
    parser.add_argument("--controlnet", nargs="+", metavar="FILE",
                        help="ControlNet model(s) to download")
    parser.add_argument("--upscale", nargs="+", metavar="FILE",
                        help="Upscale model(s) to download")
    parser.add_argument("--video", action="store_true",
                        help="Download WAN 2.2 video generation models")
    parser.add_argument("--all", action="store_true",
                        help="Download all default models")
    parser.add_argument("--list", action="store_true",
                        help="List available models")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be downloaded without downloading")
    
    args = parser.parse_args()
    
    # Handle --list
    if args.list:
        list_models()
        return 0
    
    # Check if any download requested
    if not any([args.checkpoints, args.controlnet, args.upscale, args.video, args.all]):
        parser.print_help()
        return 1
    
    # Load config
    paths = load_config()
    
    print()
    print("=" * 70)
    print("  The Halleen Machine - Model Downloader")
    print("=" * 70)
    
    if args.dry_run:
        print("  [DRY RUN - no files will be downloaded]")
    
    print()
    print(f"ComfyUI install: {paths['install_path']}")
    print(f"Checkpoints:     {paths['checkpoints']}")
    print(f"ControlNet:      {paths['controlnet']}")
    print(f"Upscale:         {paths['upscale_models']}")
    
    # Build list of models to download
    models_to_download = []
    
    if args.all:
        models_to_download = DEFAULT_MODELS.copy()
    
    if args.video:
        models_to_download.extend(VIDEO_MODELS)
    
    if args.checkpoints:
        models_to_download.extend(args.checkpoints)
    if args.controlnet:
        models_to_download.extend(args.controlnet)
    if args.upscale:
        models_to_download.extend(args.upscale)
    
    # Dedupe while preserving order
    models_to_download = list(dict.fromkeys(models_to_download))
    
    # Separate models with URLs from those without
    auto_download = []
    manual_download = []
    
    for filename in models_to_download:
        if filename not in MODEL_REGISTRY:
            print(f"\n❌ Unknown model: {filename}")
            continue
        
        url, subfolder, desc = MODEL_REGISTRY[filename]
        
        # Check if already exists
        if subfolder in paths:
            dest_dir = paths[subfolder]
        else:
            dest_dir = paths["install_path"] / "models" / subfolder
        
        dest_path = dest_dir / filename
        if dest_path.exists():
            print(f"\n✓ Already exists: {filename}")
            continue
        
        if url:
            auto_download.append(filename)
        else:
            manual_download.append((filename, subfolder, desc))
    
    # Download models with URLs
    success_count = 0
    fail_count = 0
    
    if auto_download:
        print()
        print("-" * 70)
        print("Downloading models...")
        print("-" * 70)
        
        for filename in auto_download:
            if download_model(filename, paths, dry_run=args.dry_run):
                success_count += 1
            else:
                fail_count += 1
                # Add failed downloads to manual list
                url, subfolder, desc = MODEL_REGISTRY[filename]
                manual_download.append((filename, subfolder, desc))
    
    # Report models that need manual download
    if manual_download:
        print()
        print("=" * 70)
        print("  MANUAL DOWNLOAD REQUIRED")
        print("=" * 70)
        print()
        print("The following models need to be downloaded manually:")
        print()
        
        for filename, subfolder, desc in manual_download:
            if subfolder in paths:
                dest_dir = paths[subfolder]
            else:
                dest_dir = paths["install_path"] / "models" / subfolder
            
            print(f"  • {filename}")
            print(f"    {desc}")
            print(f"    Save to: {dest_dir}/")
            print()
        
        print("See MODELS.md for download links and more info.")
        print()
    
    # Summary
    print()
    print("=" * 70)
    total_manual = len(manual_download)
    parts = []
    if success_count > 0:
        parts.append(f"{success_count} downloaded")
    if total_manual > 0:
        parts.append(f"{total_manual} need manual download")
    if parts:
        print(f"  Complete: {', '.join(parts)}")
    else:
        print("  Complete: Nothing to download")
    print("=" * 70)
    
    if not args.dry_run and success_count > 0:
        print()
        print("Re-run 'python setup.py' to configure the downloaded models.")
    
    return 0 if total_manual == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
