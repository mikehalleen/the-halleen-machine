# single_gen_helpers.py
import gradio as gr
import subprocess
import os
import json
import copy
from datetime import datetime
from typing import Dict, Any, Tuple
import random 
from pathlib import Path
from PIL import Image

from helpers import (
    WORKFLOWS_DIR,
    DEFAULT_KF_USE_ANIMAL_POSE,
    DEFAULT_KF_CN_SETTINGS,
    DEFAULT_PROJECT_WORKFLOW_FILENAME,
    IMAGE_MODEL_FAMILY_DEFAULT,
    parse_nid,
    _get_temp_dir,
    _rows_with_times,
    STYLE_PRESETS,
    TEST_LAYOUT_PROMPT,
    TEST_SETTING_PROMPT,
    save_to_project_folder,
    get_node_by_id,
    get_png_metadata,
    inject_the_machine_snapshot,
    image_model_family,
    image_family_json_to_label,
    is_custom_image_family,
    is_default_image_family,
    project_default_workflow_filename,
    resolve_project_default_workflow,
    effective_default_workflow_filename,
    video_model_family,
    video_family_json_to_label,
)


def _looks_prompt_slug(style_prompt: str, max_len: int = 30) -> str:
    """Sanitize style prompt fragment for look filenames (same rules as legacy save)."""
    return (
        "".join(c if c.isalnum() or c == " " else "" for c in (style_prompt or "")[:max_len])
        .replace(" ", "_")
        .strip("_")
    )


def look_project_context_from_project(project_dict: dict) -> dict:
    """Project fields stored in look PNG snapshots (save + generation)."""
    proj = project_dict.get("project", project_dict) if isinstance(project_dict, dict) else {}
    keygen = proj.get("keyframe_generation", {}) or {}
    negs = proj.get("negatives", {}) or {}
    lora = proj.get("lora_normalization", {}) or {}
    fam = image_model_family({"project": proj})
    return {
        "style_prompt": proj.get("style_prompt"),
        "model": proj.get("model"),
        "width": proj.get("width"),
        "height": proj.get("height"),
        "steps": keygen.get("steps"),
        "cfg": keygen.get("cfg"),
        "sampler": keygen.get("sampler_name"),
        "scheduler": keygen.get("scheduler"),
        "image_model_family": fam,
        "video_model_family": video_model_family({"project": proj}),
        "default_workflow_json": project_default_workflow_filename({"project": proj}),
        "negatives": {
            "global": negs.get("global"),
            "keyframes_all": negs.get("keyframes_all"),
            "inbetween_all": negs.get("inbetween_all"),
            "heal_all": negs.get("heal_all"),
        },
        "lora_normalization": {
            "fg_enabled": lora.get("fg_enabled"),
            "fg_max": lora.get("fg_max"),
            "bg_enabled": lora.get("bg_enabled"),
            "bg_max": lora.get("bg_max"),
        },
    }


def looks_save_basename(project_dict: dict) -> str:
    """Auto filename for saved looks (default vs custom naming)."""
    data = project_dict if isinstance(project_dict, dict) else {}
    proj = data.get("project", {}) or {}
    prompt_clean = _looks_prompt_slug(proj.get("style_prompt", ""))

    if is_custom_image_family(data):
        wf_stem = Path(project_default_workflow_filename(data)).stem
        base = f"custom_{wf_stem}"
        return f"{base}-{prompt_clean}" if prompt_clean else base

    keygen = proj.get("keyframe_generation", {}) or {}
    model_name = Path(proj.get("model", "unknown") or "unknown").stem
    steps = keygen.get("steps", "")
    cfg = keygen.get("cfg", "")
    sampler = keygen.get("sampler_name", "")
    if prompt_clean:
        return f"{model_name}-{steps}-{cfg}-{sampler}-{prompt_clean}"
    return f"{model_name}-{steps}-{cfg}-{sampler}"


def inject_look_metadata_from_project(file_path: str, project_dict: dict) -> None:
    """Merge current project look fields into PNG snapshot after save/copy."""
    snapshot = get_png_metadata(file_path) or {}
    snapshot["project_context"] = {
        **(snapshot.get("project_context") or {}),
        **look_project_context_from_project(project_dict),
    }
    snapshot.setdefault("meta", {})["look_saved_at"] = datetime.now().isoformat()
    inject_the_machine_snapshot(file_path, snapshot)


def _flat_look_fields_from_context(pc: dict) -> dict:
    """Map project_context snapshot to flat recall keys for the Project form."""
    negs = pc.get("negatives", {}) or {}
    lora = pc.get("lora_normalization", {}) or {}
    fam = str(pc.get("image_model_family") or "").strip().lower()
    if fam not in ("default", "custom"):
        fam = IMAGE_MODEL_FAMILY_DEFAULT
    vfam = str(pc.get("video_model_family") or "").strip().lower()
    if vfam not in ("default", "custom"):
        vfam = "default"
    wf = str(pc.get("default_workflow_json") or "").strip()
    if wf:
        wf = Path(wf).name
    else:
        wf = DEFAULT_PROJECT_WORKFLOW_FILENAME
    return {
        "width": pc.get("width"),
        "height": pc.get("height"),
        "style_prompt": pc.get("style_prompt"),
        "model": pc.get("model"),
        "steps": pc.get("steps"),
        "cfg": pc.get("cfg"),
        "sampler": pc.get("sampler"),
        "scheduler": pc.get("scheduler"),
        "image_model_family": fam,
        "video_model_family": vfam,
        "default_workflow_json": wf,
        "neg_global": negs.get("global"),
        "neg_kf": negs.get("keyframes_all"),
        "neg_i2v": negs.get("inbetween_all"),
        "neg_heal": negs.get("heal_all"),
        "lora_normalization.fg_enabled": lora.get("fg_enabled"),
        "lora_normalization.fg_max": lora.get("fg_max"),
        "lora_normalization.bg_enabled": lora.get("bg_enabled"),
        "lora_normalization.bg_max": lora.get("bg_max"),
    }


def _mirror_project_sampler_globals(temp_data: Dict, full_data: Dict) -> None:
    """Copy project KSampler fields into temp JSON only when Default image model family."""
    if not is_default_image_family(full_data):
        return
    project_kf_globals = full_data.get("project", {}).get("keyframe_generation", {})
    if "keyframe_generation" not in temp_data["project"]:
        temp_data["project"]["keyframe_generation"] = {}
    temp_data["project"]["keyframe_generation"].update({
        "steps": project_kf_globals.get("steps", 30),
        "cfg": project_kf_globals.get("cfg", 4.0),
        "sampler_name": project_kf_globals.get("sampler_name", "dpmpp_2m_sde"),
        "scheduler": project_kf_globals.get("scheduler", "karras"),
    })

import sys

SCRIPT_DIRECTORY = str(Path(__file__).parent / "../scripts")
# TEST_CHARACTER_SETTING_PROMPT = "a professionl photo studio, infinity wall, ring lighting, neutral background"
# TEST_CHARACTER_PROMPT = "clear view for the character"
TEST_CHARACTER_SETTING_PROMPT = "a professionl photo studio, neutral background, infinity wall, indirect lighting, even soft lighting"
TEST_CHARACTER_PROMPT = "clear view for the character"

TEST_SETTING_LAYOUT_PROMPT = "((empty space))"
TEST_SETTING_ANCHOR_PROMPT = "empty environment, no people, no character, no subject"

TEST_STYLE_LAYOUT_PROMPT = "((default scene))"
TEST_STYLE_ANCHOR_PROMPT = "an empty modern interior, no people, no character, no subject"

TEST_CHARACTER_DEFAULT_NEGATIVE = (
    "blurry, low quality, watermark, deformed, extra limbs, cropped, duplicate, camera gear, production gear, visible behind the scenes"
)
TEST_SETTING_DEFAULT_NEGATIVE = (
    "people, character, subject, figure, portrait, face, person"
)
TEST_STYLE_DEFAULT_NEGATIVE = (
    "people, character, subject, cluttered background, busy scene"
)


def _resolve_session_asset_workflow_path(session_workflow: str | None) -> str | None:
    name = str(session_workflow or "").strip()
    if not name:
        return None
    path = (WORKFLOWS_DIR / Path(name).name).resolve()
    return str(path) if path.is_file() else None


def _workflow_for_asset_test(
    full_data: Dict,
    *,
    pose_path: str | None = None,
    kind: str = "character",
    look_flat: dict | None = None,
) -> str:
    """Resolve workflow path for Assets tab generation keyframes."""
    if look_flat and look_flat.get("default_workflow_json"):
        wf_name = Path(str(look_flat["default_workflow_json"])).name
        wf_path = (WORKFLOWS_DIR / wf_name).resolve()
        if wf_path.is_file():
            return str(wf_path)
    if is_custom_image_family(full_data):
        return resolve_project_default_workflow(full_data)
    # Default family: fixed pose workflows only.
    return str((WORKFLOWS_DIR / DEFAULT_PROJECT_WORKFLOW_FILENAME).resolve())





def _asset_test_negative(user_neg: str, default_neg: str) -> str:
    """Merge user negative with asset-test default (user first, then default)."""
    parts = [p.strip() for p in (user_neg, default_neg) if p and p.strip()]
    return ", ".join(parts)


def _asset_generator_prompt(asset: dict) -> str:
    """Assets-tab positive prompt; falls back to keyframe prompt."""
    return (asset.get("generator_prompt") or asset.get("prompt") or "").strip()


def _asset_generator_negative(asset: dict) -> str:
    """Assets-tab negative prompt; falls back to keyframe negative."""
    return (asset.get("generator_negative_prompt") or asset.get("negative_prompt") or "").strip()


def apply_look_context_to_temp_project(temp_data: dict, look_flat: dict | None) -> None:
    """Overlay session look fields onto temp project for asset test run."""
    if not look_flat:
        return
    proj = temp_data.setdefault("project", {})
    if look_flat.get("image_model_family"):
        proj["image_model_family"] = look_flat["image_model_family"]
    if look_flat.get("video_model_family"):
        proj["video_model_family"] = look_flat["video_model_family"]
    if look_flat.get("default_workflow_json"):
        proj["default_workflow_json"] = look_flat["default_workflow_json"]
    if look_flat.get("model"):
        proj["model"] = look_flat["model"]
    if look_flat.get("style_prompt") is not None:
        proj["style_prompt"] = look_flat.get("style_prompt") or ""
    if look_flat.get("width"):
        proj["width"] = look_flat["width"]
    if look_flat.get("height"):
        proj["height"] = look_flat["height"]
    keygen = proj.setdefault("keyframe_generation", {})
    for src, dst in (
        ("steps", "steps"),
        ("cfg", "cfg"),
        ("sampler", "sampler_name"),
        ("scheduler", "scheduler"),
    ):
        if look_flat.get(src) is not None:
            keygen[dst] = look_flat[src]
    negs = proj.setdefault("negatives", {})
    for src, dst in (
        ("neg_global", "global"),
        ("neg_kf", "keyframes_all"),
        ("neg_i2v", "inbetween_all"),
        ("neg_heal", "heal_all"),
    ):
        if look_flat.get(src) is not None:
            negs[dst] = look_flat[src]
    lora = proj.setdefault("lora_normalization", {})
    for flat_key, field in (
        ("lora_normalization.fg_enabled", "fg_enabled"),
        ("lora_normalization.fg_max", "fg_max"),
        ("lora_normalization.bg_enabled", "bg_enabled"),
        ("lora_normalization.bg_max", "bg_max"),
    ):
        if look_flat.get(flat_key) is not None:
            lora[field] = look_flat[flat_key]


def flat_look_fields_from_project(project_dict: dict) -> dict:
    """Project tab settings as flat look fields (same shape as PNG recall)."""
    return _flat_look_fields_from_context(look_project_context_from_project(project_dict))


def effective_asset_test_workflow_filename(
    full_data: dict,
    look_flat: dict | None = None,
) -> str:
    """Workflow filename used for Assets tab test generation."""
    if look_flat and look_flat.get("default_workflow_json"):
        return Path(str(look_flat["default_workflow_json"])).name
    data = full_data if isinstance(full_data, dict) else {}
    if is_custom_image_family(data):
        return project_default_workflow_filename(data)
    return DEFAULT_PROJECT_WORKFLOW_FILENAME


def _look_detail_lines(look_flat: dict) -> list[str]:
    """Shared readout lines for a flat look dict."""
    fam = image_family_json_to_label(look_flat.get("image_model_family", IMAGE_MODEL_FAMILY_DEFAULT))
    lines = [f"**Family:** {fam}"]
    if look_flat.get("model") and not is_custom_image_family({"project": look_flat}):
        lines.append(f"**Model:** `{Path(str(look_flat['model'])).name}`")
    sampler_parts = [
        x for x in (
            look_flat.get("steps"),
            look_flat.get("cfg"),
            look_flat.get("sampler"),
            look_flat.get("scheduler"),
        )
        if x not in (None, "")
    ]
    if sampler_parts:
        lines.append(f"**Sampler:** {', '.join(str(x) for x in sampler_parts)}")
    sp = (look_flat.get("style_prompt") or "").strip()
    if sp:
        preview = sp[:80] + ("…" if len(sp) > 80 else "")
        lines.append(f"**Style prompt:** {preview}")
    return lines


def _look_accordion_detail_lines(
    look_flat: dict,
    project_dict: dict | None,
    *,
    session: bool,
) -> list[str]:
    """Model Settings accordion body: workflow, family, model, sampler, style."""
    data = project_dict if isinstance(project_dict, dict) else {}
    wf = effective_asset_test_workflow_filename(data, look_flat if session else None)
    wf_line = f"**Workflow:** `{wf}`"
    if not session:
        if is_custom_image_family(data):
            wf_line += " _(from Project tab)_"
        else:
            wf_line += " _(from Project tab; Default family pose workflow)_"
    lines = [wf_line]
    fam = image_family_json_to_label(look_flat.get("image_model_family", IMAGE_MODEL_FAMILY_DEFAULT))
    lines.append(f"**Family:** {fam}")
    fam_key = str(look_flat.get("image_model_family") or IMAGE_MODEL_FAMILY_DEFAULT).strip().lower()
    if fam_key != "custom":
        model = look_flat.get("model") or (data.get("project") or {}).get("model")
        if model:
            lines.append(f"**Model:** `{Path(str(model)).name}`")
    sampler_parts = [
        x for x in (
            look_flat.get("steps"),
            look_flat.get("cfg"),
            look_flat.get("sampler"),
            look_flat.get("scheduler"),
        )
        if x not in (None, "")
    ]
    if sampler_parts:
        lines.append(f"**Sampler:** {', '.join(str(x) for x in sampler_parts)}")
    sp = (look_flat.get("style_prompt") or "").strip()
    if sp:
        preview = sp[:80] + ("…" if len(sp) > 80 else "")
        lines.append(f"**Style prompt:** {preview}")
    return lines


def format_asset_look_indicator(
    look_flat: dict | None,
    project_dict: dict | None,
) -> str:
    """Top-line model indicator above Model Settings accordion."""
    data = project_dict if isinstance(project_dict, dict) else {}
    session = bool(look_flat)
    flat = look_flat if look_flat else flat_look_fields_from_project(data)
    fam = str(flat.get("image_model_family") or IMAGE_MODEL_FAMILY_DEFAULT).strip().lower()
    suffix = "" if session else " _(Project Default)_"
    if fam == "custom":
        wf = effective_asset_test_workflow_filename(data, look_flat)
        return f"**Model:** Custom workflow `{wf}`{suffix}"
    model = flat.get("model") or (data.get("project") or {}).get("model") or "unknown"
    return f"**Model:** `{Path(str(model)).name}`{suffix}"


def format_asset_look_accordion_details(
    look_flat: dict | None,
    project_dict: dict | None,
    look_gallery_paths: list | None = None,
) -> str:
    """Model Settings accordion body only; exclusive empty / unselected / selected states."""
    data = project_dict if isinstance(project_dict, dict) else {}
    paths = [p for p in (look_gallery_paths or []) if p]
    if look_flat:
        return "\n\n".join(_look_accordion_detail_lines(look_flat, data, session=True))
    if not paths:
        return "_No saved looks. Create them on the Project tab Look Library._"
    return "_Select a look above to apply its model and custom workflow for this session._"


def format_asset_look_status_parts(
    look_flat: dict | None,
    project_dict: dict | None,
    look_gallery_paths: list | None = None,
) -> tuple[str, str]:
    """Assets tab look UI: (model indicator, Model Settings accordion detail lines)."""
    data = project_dict if isinstance(project_dict, dict) else {}
    indicator = format_asset_look_indicator(look_flat, data)
    details = format_asset_look_accordion_details(look_flat, data, look_gallery_paths)
    return indicator, details


def format_asset_look_status_markdown(
    look_flat: dict | None,
    project_dict: dict | None,
    look_gallery_paths: list | None = None,
) -> str:
    """Single-block look status (legacy callers)."""
    indicator, details = format_asset_look_status_parts(
        look_flat, project_dict, look_gallery_paths
    )
    parts = [indicator]
    if details:
        parts.append(details)
    return "\n\n".join(parts)


def format_look_summary_markdown(look_flat: dict | None) -> str:
    """Compact read-only summary for a flat look dict (legacy / Project tab)."""
    if not look_flat:
        return "_No look selected._"
    wf = look_flat.get("default_workflow_json") or ""
    lines = _look_detail_lines(look_flat)
    if wf:
        lines.insert(1, f"**Workflow:** `{wf}`")
    return "\n\n".join(lines)


def _merge_snapshot_negatives(snapshot: dict) -> str:
    """Merge item + project negatives from a generation snapshot for generator recall."""
    parts: list[str] = []
    item_negs = (snapshot.get("item_data") or {}).get("negatives") or {}
    if isinstance(item_negs, dict):
        for key in ("left", "right", "heal"):
            val = item_negs.get(key)
            if val and str(val).strip():
                parts.append(str(val).strip())
    proj_negs = (snapshot.get("project_context") or {}).get("negatives") or {}
    if isinstance(proj_negs, dict):
        for key in ("global", "keyframes_all", "heal_all"):
            val = proj_negs.get(key)
            if val and str(val).strip():
                parts.append(str(val).strip())
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique.append(part)
    return ", ".join(unique)


def recall_asset_generation_from_reference(image_path: str):
    """
    Read the_machine_snapshot from a reference gallery PNG.
    Returns (look_flat, generator_prompt, generator_negative, summary_md, status_msg).
    """
    if not image_path or not os.path.exists(image_path):
        return None, "", "", format_asset_look_status_markdown(None, None), "Image not found."
    meta = get_png_metadata(image_path)
    if not meta:
        return None, "", "", format_asset_look_status_markdown(None, None), "No metadata found."
    gen_prompt = ((meta.get("generation") or {}).get("executed_prompt") or "").strip()
    gen_neg = _merge_snapshot_negatives(meta)
    pc = meta.get("project_context") or {}
    look_flat = _flat_look_fields_from_context(pc) if pc else None
    summary = format_asset_look_status_markdown(look_flat, None)
    return look_flat, gen_prompt, gen_neg, summary, "Generation settings loaded."


def list_style_test_options(project_dict: dict):
    """
    Parses project dictionary to build a list of options for the Style Test dropdown.
    Returns a list of (Label, Value) tuples.
    """
    options = []
    
    # 1. Add Presets (Imported from helpers)
    for label in STYLE_PRESETS.keys():
        options.append((f"[Preset] {label}", f"PRESET:{label}"))

    # 2. Add Existing Keyframes (using shared timeline logic)
    try:
        data = project_dict if isinstance(project_dict, dict) else {}
        # _rows_with_times returns [(Label, ID), ...] 
        timeline_rows = _rows_with_times(data)
        
        for label, node_id in timeline_rows:
            # Check type using ID lookup
            node, kind = get_node_by_id(data, node_id)
            if kind == "kf":
                options.append((label, node_id))
                
    except Exception:
        pass

    return options

def recall_project_globals(file_path: str):
    """
    Reads Look metadata from image.
    Tries 'the_machine_snapshot' first (new format), falls back to 'comment' (old format).
    Returns flat dict with 17 look fields for UI compatibility.
    """
    import json
    from pathlib import Path
    from PIL import Image
    try:
        import piexif
    except ImportError:
        piexif = None

    try:
        img = Image.open(file_path)
        img.load()
        ext = Path(file_path).suffix.lower()

        # Try new format: the_machine_snapshot (comprehensive metadata)
        if ext == ".png":
            snapshot_str = img.info.get("the_machine_snapshot")
            if snapshot_str:
                snapshot = json.loads(snapshot_str)
                # Extract look fields from nested structure
                pc = snapshot.get("project_context", {})
                gen = snapshot.get("generation", {})

                print(f"[RECALL DEBUG] snapshot keys: {list(snapshot.keys())}")
                print(f"[RECALL DEBUG] project_context: {pc}")
                print(f"[RECALL DEBUG] generation: {gen}")

                return _flat_look_fields_from_context(pc), "Success"
        
        # Fallback to old format: comment field (backward compatibility)
        raw_data = None
        if ext == ".png":
            raw_data = img.info.get("comment")
        elif ext in [".jpg", ".jpeg"] and piexif:
            exif_data = piexif.load(img.info.get("exif", b""))
            user_comment = exif_data.get("Exif", {}).get(piexif.ExifIFD.UserComment)
            if user_comment:
                raw_data = user_comment.decode('utf-8')

        if raw_data:
            data = json.loads(raw_data)
            if isinstance(data, dict):
                data.setdefault("image_model_family", IMAGE_MODEL_FAMILY_DEFAULT)
                data.setdefault("default_workflow_json", DEFAULT_PROJECT_WORKFLOW_FILENAME)
            return data, "Success (legacy format)"
        
        return None, "No metadata found in image."
    except Exception as e:
        return None, f"Error reading metadata: {e}"
    


def _create_temp_json_for_sequence_batch(full_data: Dict, target_nid: str) -> Tuple[Dict | None, str | None]:
    """
    Creates a minimal version of the project JSON for a single sequence batch job.
    V2: Creates a dictionary-based structure for the isolated sequence.
    """
    if not full_data:
        return None, "Error creating config: Project data is empty or None."

    sequences = full_data.get("sequences", None)

    # Verbose diagnostics (safe, text-only)
    seq_type = type(sequences).__name__
    seq_keys = []
    if isinstance(sequences, dict):
        seq_keys = list(sequences.keys())

    # Verify sequences container
    if not isinstance(sequences, dict):
        return None, (
            "Error creating config: full_data['sequences'] is not a dict.\n"
            f"target_nid={target_nid}\n"
            f"sequences_type={seq_type}\n"
        )

    # Verify ID
    if target_nid not in sequences:
        preview_keys = ", ".join(str(k) for k in seq_keys[:25])
        return None, (
            "Error creating config: target sequence id not found.\n"
            f"target_nid={target_nid}\n"
            f"sequence_count={len(seq_keys)}\n"
            f"sequence_keys_preview={preview_keys}\n"
        )

    # Start with a deep copy
    temp_data = copy.deepcopy(full_data)

    # Isolate the target sequence
    target_seq = temp_data["sequences"][target_nid]
    seq_id = target_seq.get("id")

    if not seq_id:
        return None, (
            "Error creating config: target sequence missing 'id' field.\n"
            f"target_nid={target_nid}\n"
            f"target_seq_keys={list(target_seq.keys())}\n"
        )

    # Prune sequences to only this one
    temp_data["sequences"] = {seq_id: target_seq}

    return temp_data, seq_id





def _create_temp_json_for_sequence_test(full_data: Dict, target_nid: str) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a sequence-level test.
    Returns (temp_data, seq_id, kf_id)
    """
    # Verify ID
    original_seq = full_data.get("sequences", {}).get(target_nid)
    if not original_seq:
        return None, None, None
    
    unique_id = f"id_seq_test_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__test_cache_sequence__"

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    # Get the prompts from the original sequence
    setting_prompt = original_seq.get("setting_prompt", "")
    style_prompt = original_seq.get("style_prompt", "")

    # This test uses no character and an empty layout
    test_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "pose": "",
        "layout": TEST_LAYOUT_PROMPT,
        "template": "",
        "workflow_json": str(Path(WORKFLOWS_DIR) / "pose_OPEN.json"),
        "negatives": {"left":"", "right":"", "heal":""},
        "characters": ["", ""], # No characters
        "selected_image_path": None,
        "use_animal_pose": False,
        "image_iterations_override": 1,
        "force_generate": True
    }

    test_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_prompt": setting_prompt, 
        "style_prompt": style_prompt, 
        "action_prompt": "",
        "video_plan": {"open_start": False, "open_end": True},
        # V2 Structure
        "keyframes": {unique_id: test_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }

    temp_data["sequences"] = {unique_id: test_seq}

    return temp_data, unique_id, unique_id


def _create_temp_json_for_character_test(
    full_data: Dict,
    selected_char: Dict,
    pose_path: str,
    look_context: dict | None = None,
) -> Tuple[Dict | None, str | None, str | None]:

    """
    Creates a minimal V2 project JSON for a character test.
    """
    unique_id = f"id_char_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__test_cache_character__"
    
    # Inherit Global Project Styles
    temp_data["project"]["style_prompt"] = full_data["project"].get("style_prompt", "")
    temp_data["project"]["model"] = full_data["project"].get("model", "")
    
    _mirror_project_sampler_globals(temp_data, full_data)

    # --- Pose Logic ---
    pose_path = pose_path or ""
    use_animal_pose = "_ANIMAL" in pose_path

    char_name = selected_char.get("name", "character")
    apply_look_context_to_temp_project(temp_data, look_context)
    workflow_json = _workflow_for_asset_test(
        full_data, pose_path=pose_path, kind="character", look_flat=look_context
    )

    if "_2CHAR" in pose_path:
        characters = [char_name, char_name]
    else:
        characters = [char_name, ""]

    # Put *only* the selected character into the temp project (copy; merge test negative)
    char_for_test = copy.deepcopy(selected_char)
    char_for_test["prompt"] = _asset_generator_prompt(selected_char)
    char_for_test["negative_prompt"] = _asset_test_negative(
        _asset_generator_negative(selected_char),
        TEST_CHARACTER_DEFAULT_NEGATIVE,
    )
    temp_data["project"]["characters"] = [char_for_test]

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    final_layout_prompt = f"(({TEST_CHARACTER_PROMPT}))".strip().strip(",")

    test_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "pose": pose_path,
        "layout": final_layout_prompt,
        "template": "",
        "workflow_json": workflow_json,
        "negatives": {"left":"", "right":"", "heal":""},
        "characters": characters,
        "selected_image_path": None,
        "use_animal_pose": use_animal_pose,
        "controlnet_settings": copy.deepcopy(DEFAULT_KF_CN_SETTINGS),
        "image_iterations_override": 1,
        "force_generate": True
    }


    test_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_prompt": TEST_CHARACTER_SETTING_PROMPT,
        "style_prompt": "", 
        "action_prompt": "",
        "video_plan": {"open_start": False, "open_end": True},
        # V2 Structure
        "keyframes": {unique_id: test_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }

    temp_data["sequences"] = {unique_id: test_seq}

    return temp_data, unique_id, unique_id


def _create_temp_json_for_setting_asset_test(
    full_data: Dict,
    selected_setting: Dict,
    look_context: dict | None = None,
) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a setting asset test.
    - Focus on the setting prompt
    - Inherit global project styles / negatives
    - Ignore character/pose/style (empty space)
    """
    unique_id = f"id_setting_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__test_cache_setting__"

    # Inherit Global Project Styles
    temp_data["project"]["style_prompt"] = full_data["project"].get("style_prompt", "")
    temp_data["project"]["model"] = full_data["project"].get("model", "")

    _mirror_project_sampler_globals(temp_data, full_data)

    # Put *only* the selected setting into the temp project
    temp_data["project"]["settings"] = [selected_setting]
    temp_data["project"]["styles"] = []

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    apply_look_context_to_temp_project(temp_data, look_context)
    workflow_json = _workflow_for_asset_test(
        full_data, kind="setting", look_flat=look_context
    )

    setting_prompt = _asset_generator_prompt(selected_setting)
    merged_setting_neg = _asset_test_negative(
        _asset_generator_negative(selected_setting),
        TEST_SETTING_DEFAULT_NEGATIVE,
    )

    final_setting_prompt = "\n".join([p for p in [setting_prompt, TEST_SETTING_ANCHOR_PROMPT] if p]).strip()

    test_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "pose": "",
        "layout": TEST_SETTING_LAYOUT_PROMPT,
        "template": "",
        "workflow_json": workflow_json,
        "negatives": {"left": merged_setting_neg, "right": "", "heal": ""},
        "characters": ["", ""],
        "selected_image_path": None,
        "use_animal_pose": False,
        "controlnet_settings": copy.deepcopy(DEFAULT_KF_CN_SETTINGS),
        "image_iterations_override": 1,
        "force_generate": True
    }

    test_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "setting_id": selected_setting.get("id", "") or "",
        "setting_prompt": final_setting_prompt,
        "style_prompt": "",
        "action_prompt": "",
        "video_plan": {"open_start": False, "open_end": True},
        "keyframes": {unique_id: test_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }

    temp_data["sequences"] = {unique_id: test_seq}

    return temp_data, unique_id, unique_id

def _create_temp_json_for_style_asset_test(
    full_data: Dict,
    selected_style: Dict,
    look_context: dict | None = None,
) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a style asset test.
    - Focus on the style prompt
    - Inherit global project styles / negatives
    - Ignore character/pose/setting
    - Uses a default anchor scene for consistency
    """
    unique_id = f"id_styleasset_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__test_cache_style_asset__"

    # Inherit Global Project Styles
    temp_data["project"]["style_prompt"] = full_data["project"].get("style_prompt", "")
    temp_data["project"]["model"] = full_data["project"].get("model", "")

    _mirror_project_sampler_globals(temp_data, full_data)

    # Put *only* the selected style into the temp project
    temp_data["project"]["styles"] = [selected_style]
    temp_data["project"]["settings"] = []

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    apply_look_context_to_temp_project(temp_data, look_context)
    workflow_json = _workflow_for_asset_test(
        full_data, kind="style", look_flat=look_context
    )

    style_prompt = _asset_generator_prompt(selected_style)
    merged_style_neg = _asset_test_negative(
        _asset_generator_negative(selected_style),
        TEST_STYLE_DEFAULT_NEGATIVE,
    )

    final_style_prompt = style_prompt.strip()

    test_kf = {
        "id": unique_id,
        "type": "keyframe",
        "sequence_id": unique_id,
        "pose": "",
        "layout": TEST_STYLE_LAYOUT_PROMPT,
        "template": "",
        "workflow_json": workflow_json,
        "negatives": {"left": merged_style_neg, "right": "", "heal": ""},
        "characters": ["", ""],
        "selected_image_path": None,
        "use_animal_pose": False,
        "controlnet_settings": copy.deepcopy(DEFAULT_KF_CN_SETTINGS),
        "image_iterations_override": 1,
        "force_generate": True
    }

    test_seq = {
        "id": unique_id,
        "type": "sequence",
        "order": 0,
        "style_id": selected_style.get("id", "") or "",
        "setting_prompt": TEST_STYLE_ANCHOR_PROMPT,
        "style_prompt": final_style_prompt,
        "action_prompt": "",
        "video_plan": {"open_start": False, "open_end": True},
        "keyframes": {unique_id: test_kf},
        "keyframe_order": [unique_id],
        "videos": {},
        "video_order": []
    }

    temp_data["sequences"] = {unique_id: test_seq}

    return temp_data, unique_id, unique_id


def _create_temp_json_for_style_test(full_data: Dict, target_choice: str) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a style test.
    Handles 'PRESET:Name' or direct 'kf_id'.
    """
    unique_id = f"id_style_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    
    temp_data = copy.deepcopy(full_data)
    temp_data["project"]["name"] = "__style_cache__"

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)

    target_choice = target_choice or "PRESET:Standard Landscape"

    # --- CASE A: PRESET ---
    if target_choice.startswith("PRESET:"):
        preset_key = target_choice.split(":", 1)[1]
        preset = STYLE_PRESETS.get(preset_key)
        if not preset:
            preset = STYLE_PRESETS.get("Standard Landscape", next(iter(STYLE_PRESETS.values())))

        test_kf = {
            "id": unique_id,
            "type": "keyframe",
            "sequence_id": unique_id,
            "pose": "",
            "layout": preset["layout"],
            "template": "",
            "workflow_json": str(Path(WORKFLOWS_DIR) / "pose_OPEN.json"),
            "negatives": {"left":"", "right":"", "heal":""},
            "characters": ["", ""],
            "selected_image_path": None,
            "use_animal_pose": False,
            "image_iterations_override": 1,
            "force_generate": True
        }
        
        test_seq = {
            "id": unique_id,
            "type": "sequence",
            "order": 0,
            "setting_prompt": preset["setting"],
            "style_prompt": "",
            "action_prompt": "",
            "video_plan": {"open_start": False, "open_end": True},
            # V2 Structure
            "keyframes": {unique_id: test_kf},
            "keyframe_order": [unique_id],
            "videos": {},
            "video_order": []
        }
    
    # --- CASE B: KEYFRAME ID (V2) ---
    else:
        # Assume target_choice is a node ID
        node, kind, parent_seq, seq_id = _resolve_context_safe(full_data, target_choice)
        
        if kind != "kf" or not parent_seq:
            return None, None, None

        # Copy the parent sequence to preserve context
        test_seq = copy.deepcopy(parent_seq)
        
        # Isolate the specific keyframe
        original_kf = test_seq["keyframes"][target_choice]
        test_kf = copy.deepcopy(original_kf)
        test_kf["image_iterations_override"] = 1
        test_kf["force_generate"] = True
        
        # Override ID to avoid conflicts if needed, or re-use for pathing?
        # Re-using ID allows finding it easily, but we are in a temp project
        # Let's keep the structure clean:
        
        test_seq["id"] = unique_id
        # Update self-ref
        test_seq["sequence_id"] = unique_id
        test_kf["sequence_id"] = unique_id
        
        # Prune
        test_seq["keyframes"] = {unique_id: test_kf}
        test_seq["keyframe_order"] = [unique_id]
        test_seq["videos"] = {}
        test_seq["video_order"] = []

    temp_data["sequences"] = {unique_id: test_seq}
    
    return temp_data, unique_id, unique_id





def _resolve_context_safe(data, nid):
    """Local helper safely wrapping get_node_by_id logic for partial updates"""
    try:
        # Assuming helpers is imported or duplicated logic needed?
        # We imported get_node_by_id, but that returns (node, type).
        # We need parent.
        node, kind = get_node_by_id(data, nid)
        if not node: return None, None, None, None
        
        if kind == "seq":
            return node, "seq", node, node["id"]
            
        # If KF or Vid, we need parent. In V2, parent ID is in the object.
        seq_id = node.get("sequence_id")
        parent = data.get("sequences", {}).get(seq_id)
        
        return node, kind, parent, seq_id
    except:
        return None, None, None, None

def run_image_generation_task(temp_data: Dict, project_name: str, seq_id: str, kf_id: str):
    """
    Generic helper to run an image generation script via subprocess.
    """
    temp_data_str = json.dumps(temp_data, indent=2, ensure_ascii=False)

    temp_dir = _get_temp_dir(temp_data) or (os.path.dirname(__file__) if os.path.dirname(__file__) else ".")
    unique_suffix = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    temp_project_filename = f"__temp_img_{unique_suffix}.json"
    temp_filepath = os.path.join(temp_dir, temp_project_filename)
    
    main_image_path = None
    openpose_path = None
    shape_path = None
    outline_path = None
    output_log = ""

    try:
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            f.write(temp_data_str)
        
        script_path = os.path.join(SCRIPT_DIRECTORY, "run_images.py")
        command = [sys.executable, "-u", script_path, "--config", temp_filepath]
        
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1
        )
        
        for line in process.stdout:
            output_log += line
            yield {
                "main_image_path": None,
                "openpose_path": None,
                "shape_path": None,
                "outline_path": None,
                "log_output": output_log
            }
        process.wait()

        # Find the output images
        try:
            output_root = temp_data.get("project", {}).get("comfy", {}).get("output_root", "")
            image_dir = Path(output_root) / project_name / seq_id / kf_id

            if image_dir.exists():
                image_files = [str(p) for p in image_dir.glob('*') if p.suffix.lower() in ['.png', '.jpg', '.jpeg']]
                if image_files:
                    
                    def find_latest(suffix_key: str) -> str | None:
                        candidates = [f for f in image_files if suffix_key in Path(f).name]
                        return max(candidates, key=os.path.getmtime) if candidates else None

                    openpose_path = find_latest("openposepreview")
                    shape_path = find_latest("shapepreview")
                    outline_path = find_latest("outlinepreview")
                    
                    # Find the main image (newest, not containing any preview keywords)
                    preview_keywords = {"openposepreview", "shapepreview", "outlinepreview"}
                    main_candidates = [f for f in image_files if not any(kw in Path(f).name for kw in preview_keywords)]
                    if main_candidates:
                        main_image_path = max(main_candidates, key=os.path.getmtime)
                    
                    output_log += f"\n\nSuccess: Found main image."
                else:
                    output_log += f"\n\nError: Script finished, but no image was found in {str(image_dir)}"
            else:
                output_log += f"\n\nError: Script finished, but the output directory was not found: {str(image_dir)}"
        except Exception as e:
            output_log += f"\n\nError finding output image(s): {e}"

    finally:
        try:
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)
        except Exception as e:
            print(f"Warning: Failed to clean up temp file {temp_filepath}: {e}")

        yield {
            "main_image_path": main_image_path,
            "openpose_path": openpose_path,
            "shape_path": shape_path,
            "outline_path": outline_path,
            "log_output": output_log
        }


# def run_pose_preview_task(project_data: Dict, image_path: str):
def run_pose_preview_task(project_data: Dict, image_path: str, output_dir: str = None, use_animal_pose: bool = False):
    """
    Generates controlnet preview images (openpose, shape, outline) from an existing image.
    Calls run_images.py with --preview-only flag.
    Yields progress dicts with log_output, openpose_path, shape_path, outline_path.
    """
    temp_dir = _get_temp_dir(project_data) or (os.path.dirname(__file__) if os.path.dirname(__file__) else ".")
    unique_suffix = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    temp_project_filename = f"__temp_preview_{unique_suffix}.json"
    temp_filepath = os.path.join(temp_dir, temp_project_filename)

    # Build minimal keyframe structure with use_animal_pose
    temp_data = copy.deepcopy(project_data) if isinstance(project_data, dict) else {}
    unique_id = f"preview_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    temp_kf = {"id": unique_id, "use_animal_pose": use_animal_pose}
    temp_seq = {"id": unique_id, "keyframes": {unique_id: temp_kf}, "keyframe_order": [unique_id]}
    temp_data["sequences"] = {unique_id: temp_seq}

    openpose_path = None
    shape_path = None
    outline_path = None
    output_log = ""
    
    try:
        # Write minimal project config (just need comfy settings)
        # temp_data_str = json.dumps(project_data, indent=2, ensure_ascii=False)
        temp_data_str = json.dumps(temp_data, indent=2, ensure_ascii=False)
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            f.write(temp_data_str)
        
        script_path = os.path.join(SCRIPT_DIRECTORY, "run_images.py")
        command = [
            sys.executable, "-u", script_path,
            "--config", temp_filepath,
            "--preview-only",
            "--image", image_path
        ]
        
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1
        )
        
        for line in process.stdout:
            output_log += line
            
            # Parse result lines
            if line.startswith("PREVIEW_POSE:"):
                path = line.split(":", 1)[1].strip()
                if path != "NOT_FOUND":
                    openpose_path = path
            elif line.startswith("PREVIEW_SHAPE:"):
                path = line.split(":", 1)[1].strip()
                if path != "NOT_FOUND":
                    shape_path = path
            elif line.startswith("PREVIEW_OUTLINE:"):
                path = line.split(":", 1)[1].strip()
                if path != "NOT_FOUND":
                    outline_path = path
            
            yield {
                "openpose_path": openpose_path,
                "shape_path": shape_path,
                "outline_path": outline_path,
                "log_output": output_log
            }
        
        process.wait()
        
        if openpose_path and shape_path and outline_path:
            output_log += "\n\nSuccess: Controlnet previews extracted."
        else:
            output_log += "\n\nWarning: Some preview images were not found."
    
    finally:
        try:
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)
        except Exception as e:
            print(f"Warning: Failed to clean up temp file {temp_filepath}: {e}")
        
        yield {
            "openpose_path": openpose_path,
            "shape_path": shape_path,
            "outline_path": outline_path,
            "log_output": output_log
        }



def _create_temp_json_for_image_test(full_data: Dict, target_kf_id: str, seed_override: int = None) -> Tuple[Dict | None, str | None, str | None]:
    """
    Creates a minimal V2 project JSON for a single keyframe test.
    """
    # Resolve context using ID
    node, kind, parent_seq, seq_id = _resolve_context_safe(full_data, target_kf_id)
    if kind != "kf" or not parent_seq:
        return None, None, None
    
    temp_data = copy.deepcopy(full_data)

    if "keyframe_generation" in temp_data["project"]:
        temp_data["project"]["keyframe_generation"]["image_iterations_default"] = 1
        if seed_override is not None:
            temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = seed_override
            temp_data["project"]["keyframe_generation"]["advance_seed_by"] = 0  # No advancement for explicit seed
            print(f"[DEBUG SEED] Using override: {seed_override}")
        else:
            temp_data["project"]["keyframe_generation"]["sampler_seed_start"] = random.randint(0, 2**32 - 1)
            print(f"[DEBUG SEED] Using random: {temp_data['project']['keyframe_generation']['sampler_seed_start']}")

    # Isolate target sequence
    target_seq = temp_data["sequences"][seq_id]
    
    # Isolate target keyframe
    target_kf = target_seq["keyframes"][target_kf_id]
    
    # Set flags
    target_kf["image_iterations_override"] = 1
    target_kf["force_generate"] = True
    # target_kf.pop("sampler_seed_start", None) 
    
    # Prune
    target_seq["keyframes"] = {target_kf_id: target_kf}
    target_seq["keyframe_order"] = [target_kf_id]
    target_seq["videos"] = {}
    target_seq["video_order"] = []
    
    # Prune sequences
    temp_data["sequences"] = {seq_id: target_seq}
    
    return temp_data, seq_id, target_kf_id

def handle_style_test(project_dict: dict, path_at_start: str, target_source: str = None):
    """
    The main function for the style test generation button.
    Prepares data and calls the shared generation helper.
    """
    if not isinstance(project_dict, dict):
        yield (None, "Error: No project data found.", gr.update())
        return

    full_data = project_dict
    temp_data, seq_id, kf_id = _create_temp_json_for_style_test(full_data, target_source)

    if not temp_data:
        yield (None, "Error: Could not create test data for style test.", gr.update())
        return

    project_name = temp_data.get("project", {}).get("name", "__test_cache_style__")

    yield (None, "Starting style test generation...", gr.update())

    final_main_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_log = result.get("log_output", "")
        yield (final_main_path, final_log, gr.update())

    # Fallback
    if not final_main_path:
        try:
            output_root = full_data.get("project", {}).get("comfy", {}).get("output_root")
            if output_root:
                tmp_dir = Path(output_root) / "__style_cache__"
                if tmp_dir.exists():
                    files = sorted([p for p in tmp_dir.glob("*") if p.suffix.lower() in [".png", ".jpg", ".webp"]], key=os.path.getmtime, reverse=True)
                    if files:
                        final_main_path = str(files[0])
                        final_log += f"\n\n[System] Preview path inferred from newest temp image: {files[0].name}"
        except Exception as e:
            print(f"Fallback failed: {e}")

    # Show notification
    if final_main_path:
        context_name = target_source if target_source else "project style"
        gr.Info(f"✓ Style preview: {context_name}")
    
    yield (final_main_path, final_log, gr.update())


def sync_style_test_scene_dropdown(project_dict):
    """Refresh Pick a Scene choices (dropdown only; no State input/output loop)."""
    opts = list_style_test_options(project_dict)
    if opts and isinstance(opts[0], (list, tuple)):
        values = [v for _, v in opts]
    else:
        values = list(opts) if opts else []
    pick = values[0] if values else None
    return gr.update(choices=opts, value=pick)


def run_style_preview_click(path, pj, scene, settings_str):
    """Gradio handler for Project tab Generate Preview (not handle_style_test)."""
    from helpers import cb_save_project

    cb_save_project(path, pj, settings_str)
    paths = get_style_test_images(pj)
    gal_status = f"Found {len(paths)} images."
    for i, (img, log, buf) in enumerate(handle_style_test(pj, path, scene)):
        if i == 0:
            yield "", img, log, buf, paths, paths, gal_status
        else:
            yield gr.update(), img, log, buf, gr.update(), gr.update(), gr.update()


def save_style_to_project(temp_path: str, project_dict: dict):
    """
    Saves a temp style image to the project's _looks folder.
    Metadata is already embedded by run_images.py - just copy the file.
    """
    from helpers import save_to_project_folder
    if not (temp_path and isinstance(project_dict, dict)):
        return "Error: Missing input."
    
    data = project_dict
    proj_data = data.get("project", {})
    output_root = proj_data.get("comfy", {}).get("output_root")
    project_name = proj_data.get("name")
    
    if not (output_root and project_name):
        return "Error: Project paths invalid."
    
    name = looks_save_basename(data)

    dest_dir = Path(output_root) / project_name / "_looks"
    dest_dir.mkdir(parents=True, exist_ok=True)

    msg, new_path = save_to_project_folder(temp_path, str(dest_dir), name)

    if new_path:
        inject_look_metadata_from_project(new_path, data)
        return f"Saved Look: {Path(new_path).name}"
    else:
        return msg  # Error message from save_to_project_folder

def handle_test_generation(project_dict: dict, target_nid: str, path_at_start: str, seed_override: int = None):
    """
    The main function for the keyframe test generation button.
    Prepares data and calls the shared generation helper.
    """
    if not isinstance(project_dict, dict) or not target_nid:
        yield (None, None, "Error: No project data or target selected.", None)
        return

    full_data = project_dict
    temp_data, seq_id, kf_id = _create_temp_json_for_image_test(full_data, target_nid, seed_override=seed_override)

    if not temp_data:
        yield (None, None, f"Error: Could not create test data for target '{target_nid}'.", None)
        return

    project_name = full_data.get("project", {}).get("name", "")
    if not project_name:
        yield (None, None, "Error: Project name is missing from JSON data.", None)
        return

    yield (None, None, "Starting keyframe test generation...", None)

    final_main_path = None
    final_openpose_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_openpose_path = result.get("openpose_path") 
        final_log = result.get("log_output", "")
        
        yield (final_main_path, final_openpose_path, final_log, None)

    yield (
        final_main_path, 
        final_openpose_path, 
        final_log, 
        {"final_json": full_data, "source_path": path_at_start}
    )

def handle_character_test(
    project_dict: dict,
    selected_char_id: str,
    look_context: dict | None = None,
):
    """
    Assets tab: Character test generation.
    Returns (image_path, log_text).
    """
    if not isinstance(project_dict, dict):
        yield (None, "Error: No project data found.")
        return

    if not selected_char_id:
        yield (None, "Error: No character is selected.")
        return

    full_data = project_dict

    chars = full_data.get("project", {}).get("characters", [])
    selected_char = next((c for c in chars if c.get("id") == selected_char_id), None)

    if not selected_char:
        yield (None, f"Error: Could not find character with ID {selected_char_id}.")
        return

    temp_data, seq_id, kf_id = _create_temp_json_for_character_test(
        full_data, selected_char, pose_path="", look_context=look_context
    )

    if not temp_data:
        yield (None, "Error: Could not create test data for character test.")
        return

    project_name = temp_data.get("project", {}).get("name", "__test_cache_character__")

    yield (None, f"Starting character test for: {selected_char.get('name', 'Unknown')}")

    final_main_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_log = result.get("log_output", "")
        yield (final_main_path, final_log)

    # Show notification
    if final_main_path:
        char_name = selected_char.get('name', 'Unknown')
        gr.Info(f"✓ Character test: {char_name}")
    
    yield (final_main_path, final_log)


def handle_setting_test(
    project_dict: dict,
    selected_setting_id: str,
    look_context: dict | None = None,
):
    """
    Assets tab: Setting test generation.
    Returns (image_path, log_text) like character test.
    """
    if not isinstance(project_dict, dict):
        yield (None, "Error: No project data found.")
        return

    if not selected_setting_id:
        yield (None, "Error: No setting is selected.")
        return

    full_data = project_dict

    settings_list = full_data.get("project", {}).get("settings", [])
    selected_setting = next((s for s in settings_list if s.get("id") == selected_setting_id), None)

    if not selected_setting:
        yield (None, f"Error: Could not find setting with ID {selected_setting_id}.")
        return

    temp_data, seq_id, kf_id = _create_temp_json_for_setting_asset_test(
        full_data, selected_setting, look_context=look_context
    )

    if not temp_data:
        yield (None, "Error: Could not create test data for setting test.")
        return

    project_name = temp_data.get("project", {}).get("name", "__test_cache_setting__")

    yield (None, f"Starting setting test for: {selected_setting.get('name', 'Unknown')}")

    final_main_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_log = result.get("log_output", "")
        yield (final_main_path, final_log)

    # Show notification
    if final_main_path:
        setting_name = selected_setting.get('name', 'Unknown')
        gr.Info(f"✓ Setting test: {setting_name}")
    
    yield (final_main_path, final_log)


def handle_style_asset_test(
    project_dict: dict,
    selected_style_id: str,
    look_context: dict | None = None,
):
    """
    Assets tab: Style asset test generation.
    Returns (image_path, log_text) like character test.
    """
    if not isinstance(project_dict, dict):
        yield (None, "Error: No project data found.")
        return

    if not selected_style_id:
        yield (None, "Error: No style is selected.")
        return

    full_data = project_dict

    styles_list = full_data.get("project", {}).get("styles", [])
    selected_style = next((s for s in styles_list if s.get("id") == selected_style_id), None)

    if not selected_style:
        yield (None, f"Error: Could not find style with ID {selected_style_id}.")
        return

    temp_data, seq_id, kf_id = _create_temp_json_for_style_asset_test(
        full_data, selected_style, look_context=look_context
    )

    if not temp_data:
        yield (None, "Error: Could not create test data for style test.")
        return

    project_name = temp_data.get("project", {}).get("name", "__test_cache_style_asset__")

    yield (None, f"Starting style test for: {selected_style.get('name', 'Unknown')}")

    final_main_path = None
    final_log = ""

    for result in run_image_generation_task(temp_data, project_name, seq_id, kf_id):
        final_main_path = result.get("main_image_path")
        final_log = result.get("log_output", "")
        yield (final_main_path, final_log)

    # Show notification
    if final_main_path:
        style_name = selected_style.get('name', 'Unknown')
        gr.Info(f"✓ Style test: {style_name}")
    
    yield (final_main_path, final_log)



def get_style_test_images(project_dict: dict):
    """Recursively finds images in the [ProjectName]/_looks folder."""
    try:
        data = project_dict if isinstance(project_dict, dict) else {}
        output_root = data.get("project", {}).get("comfy", {}).get("output_root")
        project_name = data.get("project", {}).get("name")
        
        if not output_root or not project_name:
            return []
        
        # Target the new _styles subdirectory
        target_dir = Path(output_root) / project_name / "_looks"
        
        if not target_dir.exists():
            return []
            
        files = []
        for ext in ["*.png", "*.jpg", "*.webp"]:
            files.extend(target_dir.rglob(ext))
            
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [str(p) for p in files]
        
    except Exception:
        return []