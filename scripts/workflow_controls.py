"""Shared ComfyUI workflow control helpers for THM execution scripts.

This module is intentionally UI-free. It only knows how to find and mutate
controllable nodes in an already-loaded ComfyUI workflow dictionary.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal


def _console_print(message: str) -> None:
    """Avoid UnicodeEncodeError on Windows charmap consoles when logging."""
    try:
        print(message)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(message.encode(enc, errors="replace").decode(enc))

try:
    from lora_tags import LoraSpec
except ImportError:
    from scripts.lora_tags import LoraSpec  # type: ignore[no-redef]


def _coerce_lora_spec(entry: Any) -> LoraSpec | None:
    if hasattr(entry, "name") and hasattr(entry, "strength"):
        try:
            strength_b = getattr(entry, "strength_b", None)
            return LoraSpec(
                name=str(entry.name).strip(),
                strength=float(entry.strength),
                strength_b=float(strength_b) if strength_b is not None else None,
            )
        except (TypeError, ValueError):
            return None
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        try:
            strength_b = float(entry[2]) if len(entry) > 2 and entry[2] is not None else None
            return LoraSpec(name=str(entry[0]).strip(), strength=float(entry[1]), strength_b=strength_b)
        except (TypeError, ValueError):
            return None
    return None


class WorkflowControlError(ValueError):
    """Raised when a tagged workflow control cannot be satisfied."""


# ComfyUI execution modes (see ComfyUI NODE_CLASS_MAPPINGS / workflow JSON).
COMFY_NODE_MODE_ALWAYS = 0
COMFY_NODE_MODE_NEVER = 2  # muted / inactive
COMFY_NODE_MODE_BYPASS = 4  # passthrough — LoRA markers use this to stay inline


PROMPT = "prompt"
NEGATIVE_PROMPT = "negative_prompt"
CHECKPOINT = "checkpoint"
LORA = "lora"
POSE_REFERENCE = "pose_reference"
IMAGE_REFERENCE = "image_reference"
CHARACTER_REFERENCE = "character_reference"
LOCATION_REFERENCE = "location_reference"
STYLE_REFERENCE = "style_reference"
SAVE_IMAGE = "save_image"
SEED = "seed"
STEPS = "steps"
CFG = "cfg"
SAMPLER = "sampler"
SCHEDULER = "scheduler"
KSAMPLER = "ksampler"
WIDTH = "width"
HEIGHT = "height"
IMAGE_SIZE = "image_size"
VIDEO_GENERATOR = "video_generator"
FRAME_COUNT = "frame_count"
FRAME_RATE = "frame_rate"
SAVE_VIDEO = "save_video"
VIDEO_START_FRAME = "video_start_frame"
VIDEO_END_FRAME = "video_end_frame"
LORA_HIGH = "lora_high"
LORA_LOW = "lora_low"
SLOWMO_PRIMER = "slowmo_primer"

SAMPLER_CHAIN_CLASSES = frozenset({"KSampler", "KSamplerAdvanced"})
LATENT_UPSTREAM_KEYS = ("latent_image", "latent", "samples")


TAG_PREFIX = "THM-"


CONTROL_TAGS: dict[str, list[str]] = {
    PROMPT: ["THM-Prompt"],
    NEGATIVE_PROMPT: ["THM-NegativePrompt"],
    CHECKPOINT: ["THM-Checkpoint"],
    LORA: ["THM-LoraAfterThisNode", "THM-Lora"],
    POSE_REFERENCE: [],  # legacy MainImageAndMask only — no THM tag
    IMAGE_REFERENCE: ["THM-ImageReference"],
    SAVE_IMAGE: ["THM-SaveImage"],
    SEED: ["THM-Seed"],
    STEPS: ["THM-Steps"],
    CFG: ["THM-CFG"],
    SAMPLER: ["THM-Sampler"],
    SCHEDULER: ["THM-Scheduler"],
    KSAMPLER: ["THM-KSampler"],
    WIDTH: ["THM-Width"],
    HEIGHT: ["THM-Height"],
    IMAGE_SIZE: ["THM-ImageSize"],
    VIDEO_GENERATOR: ["THM-VideoGenerator"],
    FRAME_COUNT: ["THM-FrameCount"],
    FRAME_RATE: ["THM-FrameRate", "THM-FPS"],
    SAVE_VIDEO: ["THM-SaveVideo"],
    VIDEO_START_FRAME: ["THM-StartFrame"],
    VIDEO_END_FRAME: ["THM-EndFrame"],
    LORA_HIGH: ["THM-Lora-High"],
    LORA_LOW: ["THM-Lora-Low"],
    SLOWMO_PRIMER: ["THM-SlowMoPrimer"],
}

_DEPRECATED_REFERENCE_TAG_PREFIXES = (
    "THM-PoseReference",
    "THM-CharacterReference",
    "THM-LocationReference",
    "THM-SettingReference",
    "THM-StyleReference",
)


LEGACY_TITLE_ALIASES: dict[str, list[str]] = {
    PROMPT: ["MainPrompt", "LeftPrompt", "RightPrompt", "HealPosPrompt", "PosPrompt"],
    NEGATIVE_PROMPT: ["MainNegPrompt", "NegPrompt", "LeftNegPrompt", "RightNegPrompt", "HealNegPrompt"],
    CHECKPOINT: ["MainCheckpoint", "LeftCheckpoint", "RightCheckpoint", "Load Checkpoint"],
    LORA: ["MainLora", "LeftLora", "RightLora"],
    POSE_REFERENCE: ["MainImageAndMask"],
    SAVE_IMAGE: ["Save Image"],
    WIDTH: ["Width"],
    HEIGHT: ["Height"],
    SEED: ["IterKSampler", "WanFixedSeed", "SlowMoPrimer"],
    VIDEO_GENERATOR: ["WanFirstLastFrameToVideo"],
    LORA_HIGH: ["HighNoiseUnet"],
    LORA_LOW: ["LowNoiseUnet"],
    SLOWMO_PRIMER: ["SlowMoPrimer"],
}

VIDEO_SAVE_CLASSES = ("SaveVideo", "VHS_VideoCombine")
VIDEO_GENERATOR_LENGTH_KEYS = ("length", "frames", "num_frames", "frame_count")
VIDEO_GENERATOR_WIDTH_KEYS = ("width", "W", "out_width", "video_width")
VIDEO_GENERATOR_HEIGHT_KEYS = ("height", "H", "out_height", "video_height")
VIDEO_GENERATOR_START_KEYS = ("start_image", "first_frame", "clip_vision_start_image")
VIDEO_GENERATOR_END_KEYS = ("end_image", "last_frame", "clip_vision_end_image")
VideoFrameClipType = Literal["SE", "SO", "OE"]


DIMENSION_CLASSES = [
    "EmptyLatentImage",
    "ImageScale",
    "Image Overlay",
    "ImageCrop",
    "Image Blank",
    "EmptyFlux2LatentImage",
]

# ComfyUI nodes store user-facing strings on different input keys.
CLASS_TEXT_INPUT_KEY: dict[str, str] = {
    "CLIPTextEncode": "text",
    "PrimitiveStringMultiline": "value",
}
TEXT_INPUT_KEYS = ("text", "value", "string")

# Node classes whose ``clip`` input may be rewired during LoRA injection.
CLIP_ENCODE_CLASSES = frozenset({"CLIPTextEncode"})


@dataclass(frozen=True)
class ControlNode:
    node_id: str
    node: dict[str, Any]
    control: str
    title: str
    class_type: str
    slot: str | None = None
    source: str = "tag"


def node_title(node: dict[str, Any]) -> str:
    return (node.get("_meta", {}) or {}).get("title", "")


def node_class(node: dict[str, Any]) -> str:
    return str(node.get("class_type") or "")


def find_nodes_by_title(workflow: dict[str, Any], title: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and node_title(node) == title
    ]


def find_nodes_by_class(workflow: dict[str, Any], class_type: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


def set_input(node: dict[str, Any], input_key: str, value: Any) -> bool:
    if not isinstance(node, dict):
        return False
    node.setdefault("inputs", {})[input_key] = value
    return True


def text_input_key_for_node(node: dict[str, Any]) -> str:
    class_type = node_class(node)
    if class_type in CLASS_TEXT_INPUT_KEY:
        return CLASS_TEXT_INPUT_KEY[class_type]
    inputs = node.get("inputs") or {}
    for key in TEXT_INPUT_KEYS:
        if key in inputs:
            return key
    return "text"


def set_node_text(node: dict[str, Any], text: str) -> bool:
    """Write prompt/negative text on the node's canonical string input.

    Clears other common string inputs so baked workflow defaults cannot leak
    through (e.g. ``value`` on PrimitiveStringMultiline when only ``text`` was set).
    """
    if not isinstance(node, dict):
        return False
    inputs = node.setdefault("inputs", {})
    target_key = text_input_key_for_node(node)
    for key in TEXT_INPUT_KEYS:
        if key != target_key and key in inputs:
            del inputs[key]
    inputs[target_key] = text if text is not None else ""
    return True


def _is_thm_title(title: str) -> bool:
    return bool(title) and title.lower().startswith(TAG_PREFIX.lower())


def _parse_tag(title: str) -> tuple[str | None, str | None]:
    if not _is_thm_title(title):
        return None, None

    title_lower = title.lower()

    for control, tags in CONTROL_TAGS.items():
        for tag in tags:
            if title_lower == tag.lower():
                return control, None

    best_control: str | None = None
    best_slot: str | None = None
    best_len = -1
    for control, tags in CONTROL_TAGS.items():
        for tag in tags:
            tag_lower = tag.lower()
            prefix = f"{tag_lower}-"
            if title_lower.startswith(prefix) and len(tag_lower) > best_len:
                rest = title[len(tag) :]
                slot = rest[1:] if rest.startswith("-") else (rest or None)
                best_control = control
                best_slot = slot or None
                best_len = len(tag_lower)
    return best_control, best_slot


def find_tagged_control_nodes(workflow: dict[str, Any], control: str) -> list[ControlNode]:
    matches: list[ControlNode] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        title = node_title(node)
        parsed_control, slot = _parse_tag(title)
        if parsed_control == control:
            matches.append(
                ControlNode(
                    node_id=str(node_id),
                    node=node,
                    control=control,
                    title=title,
                    class_type=node_class(node),
                    slot=slot,
                    source="tag",
                )
            )
    return matches


def find_legacy_control_nodes(workflow: dict[str, Any], control: str) -> list[ControlNode]:
    matches: list[ControlNode] = []
    for title in LEGACY_TITLE_ALIASES.get(control, []):
        for node_id, node in find_nodes_by_title(workflow, title):
            matches.append(
                ControlNode(
                    node_id=str(node_id),
                    node=node,
                    control=control,
                    title=title,
                    class_type=node_class(node),
                    source="legacy",
                )
            )
    return matches


def find_control_nodes(workflow: dict[str, Any], control: str) -> list[ControlNode]:
    tagged = find_tagged_control_nodes(workflow, control)
    if tagged:
        return tagged
    return find_legacy_control_nodes(workflow, control)


def unknown_thm_tags(workflow: dict[str, Any]) -> list[ControlNode]:
    unknown: list[ControlNode] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        title = node_title(node)
        if _is_thm_title(title):
            control, slot = _parse_tag(title)
            if control is None:
                unknown.append(
                    ControlNode(
                        node_id=str(node_id),
                        node=node,
                        control="unknown",
                        title=title,
                        class_type=node_class(node),
                        slot=slot,
                    )
                )
    return unknown


def scan_workflow_controls(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return tagged/legacy control nodes present in a workflow (matches runtime injection)."""
    scanned: dict[str, Any] = {}
    for control in CONTROL_TAGS:
        nodes = find_control_nodes(workflow, control)
        if nodes:
            scanned[control] = [_control_node_summary(node) for node in nodes]
    lora_markers = find_control_nodes(workflow, LORA)
    if lora_markers:
        summaries = []
        any_clip_support = False
        for marker in lora_markers:
            clip_support = detect_lora_clip_support(workflow, marker)
            any_clip_support = any_clip_support or clip_support
            entry = _control_node_summary(marker)
            entry["clip_support"] = clip_support
            summaries.append(entry)
        scanned["lora"] = summaries
        scanned["lora_clip_support"] = any_clip_support
    unknown = unknown_thm_tags(workflow)
    if unknown:
        scanned["unknown"] = [_control_node_summary(node) for node in unknown]
    return scanned


def _control_node_summary(control_node: ControlNode) -> dict[str, Any]:
    return {
        "node_id": control_node.node_id,
        "title": control_node.title,
        "class_type": control_node.class_type,
        "slot": control_node.slot,
        "source": control_node.source,
    }


def set_text_control(workflow: dict[str, Any], control: str, text: str) -> int:
    count = 0
    for control_node in find_control_nodes(workflow, control):
        if set_node_text(control_node.node, text):
            count += 1
    return count


def set_text_on_title(workflow: dict[str, Any], title: str, text: str) -> int:
    count = 0
    for _, node in find_nodes_by_title(workflow, title):
        if set_node_text(node, text):
            count += 1
    return count


def set_prompt(workflow: dict[str, Any], text: str) -> int:
    return set_text_control(workflow, PROMPT, text)


def set_negative_prompt(workflow: dict[str, Any], text: str) -> int:
    return set_text_control(workflow, NEGATIVE_PROMPT, text)


def set_checkpoint(workflow: dict[str, Any], model_name: str | None) -> int:
    control_nodes = find_control_nodes(workflow, CHECKPOINT)
    if not control_nodes:
        return 0
    name = str(model_name or "").strip()
    if not name:
        raise WorkflowControlError(
            "Workflow defines THM-Checkpoint (or a legacy checkpoint control) "
            "but no model is specified for this project/look."
        )
    count = 0
    for control_node in control_nodes:
        inputs = control_node.node.setdefault("inputs", {})
        if "unet_name" in inputs or control_node.class_type == "UNETLoader":
            set_input(control_node.node, "unet_name", name)
        elif "ckpt_name" in inputs or control_node.class_type == "CheckpointLoaderSimple":
            set_input(control_node.node, "ckpt_name", name)
        else:
            set_input(control_node.node, "ckpt_name", name)
        count += 1
    return count


def set_save_image_prefix(workflow: dict[str, Any], filename_prefix: str) -> int:
    count = 0
    for control_node in find_control_nodes(workflow, SAVE_IMAGE):
        inputs = control_node.node.setdefault("inputs", {})
        inputs.pop("output_dir", None)
        inputs["filename_prefix"] = filename_prefix
        count += 1
    return count


def _write_image_input_on_control_node(control_node: ControlNode, image_path: str) -> None:
    """Set the canonical image path input on a reference LoadImage node (may be empty)."""
    inputs = control_node.node.setdefault("inputs", {})
    target_key = next(
        (key for key in ("image", "image_path", "file", "filename", "filepath") if key in inputs),
        "image",
    )
    inputs[target_key] = image_path if image_path is not None else ""


def set_image_reference(workflow: dict[str, Any], control: str, image_path: str) -> int:
    if not image_path:
        return 0
    count = 0
    for control_node in find_control_nodes(workflow, control):
        _write_image_input_on_control_node(control_node, image_path)
        count += 1
    return count


TAGGED_REFERENCE_CONTROLS = (IMAGE_REFERENCE,)


def _warn_deprecated_reference_tags(workflow: dict[str, Any]) -> None:
    warned: set[str] = set()
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        title = node_title(node)
        if not title or title in warned:
            continue
        title_lower = title.lower()
        for prefix in _DEPRECATED_REFERENCE_TAG_PREFIXES:
            if title_lower == prefix.lower() or title_lower.startswith(f"{prefix.lower()}-"):
                _console_print(
                    f"[REF] Deprecated reference tag {title!r}; use duplicate bare THM-ImageReference only."
                )
                warned.add(title)
                break

MAX_IMAGE_REFERENCE_SLOTS = 4

ReferenceSemantic = Literal["pose", "character", "location", "style", "unset"]


@dataclass(frozen=True)
class ImageReferenceSlot:
    """One tagged LoadImage reference slot (up to four per workflow)."""

    slot_key: str  # suffix for THM-ImageReference ("" or "2"); semantic key for legacy tags
    node_id: str
    title: str
    tag_control: str
    default_semantic: ReferenceSemantic
    image_index: int  # wiring order for prelude imageN
    binding_key: str = ""  # unique keyframe reference_bindings key (assigned at discovery)


def _legacy_binding_key_from_slot(slot: ImageReferenceSlot) -> str:
    if slot.tag_control == POSE_REFERENCE:
        return "pose"
    if slot.tag_control == LOCATION_REFERENCE:
        return "location"
    if slot.tag_control == STYLE_REFERENCE:
        return "style"
    if slot.tag_control == CHARACTER_REFERENCE:
        if slot.slot_key in ("2", "right"):
            return "character_2"
        return "character_1"
    return slot.node_id


def _assign_binding_keys(slots: list[ImageReferenceSlot]) -> list[ImageReferenceSlot]:
    """Assign binding_key = Comfy node_id for each generic image reference slot."""
    result: list[ImageReferenceSlot] = []
    for s in slots:
        if s.tag_control == IMAGE_REFERENCE:
            bk = s.node_id
        else:
            bk = _legacy_binding_key_from_slot(s)
        result.append(replace(s, binding_key=bk))
    return result


def _legacy_binding_alias_keys_for_slot(slot: ImageReferenceSlot, ordinal: int) -> list[str]:
    """One-time migration keys from saved projects (ordinal, semantic, empty)."""
    keys: list[str] = []

    def add(key: str) -> None:
        k = str(key)
        if k not in keys:
            keys.append(k)

    add(str(ordinal))
    if slot.image_index and slot.image_index >= 1:
        add(str(slot.image_index))
    if slot.slot_key:
        add(slot.slot_key)
    if ordinal == 1 or slot.image_index == 1:
        add("")
    add("pose")
    add("location")
    add("style")
    add("character_1")
    add("character_2")
    if ordinal == 1:
        add("character_1")
    if ordinal == 2:
        add("character_2")
    return keys


def remap_reference_bindings_for_slots(
    bindings: dict[str, dict[str, Any]],
    slots: list[ImageReferenceSlot],
) -> dict[str, dict[str, Any]]:
    """Map saved bindings onto canonical slot keys (preserves semantics and gallery pins)."""
    src: dict[str, dict[str, Any]] = {
        str(k): dict(v) for k, v in (bindings or {}).items() if isinstance(v, dict)
    }
    if not slots or not src:
        return src

    remapped: dict[str, dict[str, Any]] = {}
    for ordinal, slot in enumerate(slots, start=1):
        sk = binding_key_for_slot(slot)
        if sk in src:
            remapped[sk] = dict(src[sk])
            continue
        picked: dict[str, Any] | None = None
        for alias in _legacy_binding_alias_keys_for_slot(slot, ordinal):
            if alias == sk:
                continue
            candidate = src.get(alias)
            if not isinstance(candidate, dict):
                continue
            if picked is None or (
                _binding_semantic(candidate) != "unset"
                and _binding_semantic(picked) == "unset"
            ):
                picked = dict(candidate)
            if _binding_semantic(candidate) != "unset":
                break
        if picked is not None:
            remapped[sk] = picked
    return remapped


def remap_reference_slot_last_choice(
    last_choices: dict[str, str],
    slots: list[ImageReferenceSlot],
) -> dict[str, str]:
    src = {str(k): str(v) for k, v in (last_choices or {}).items()}
    if not slots or not src:
        return src
    remapped: dict[str, str] = {}
    for ordinal, slot in enumerate(slots, start=1):
        sk = binding_key_for_slot(slot)
        for alias in _legacy_binding_alias_keys_for_slot(slot, ordinal):
            if alias in src:
                remapped[sk] = src[alias]
                break
    return remapped


def binding_key_for_slot(slot: ImageReferenceSlot) -> str:
    """Stable keyframe ``reference_bindings`` / path map key for this slot (node_id for generic refs)."""
    if slot.binding_key:
        return slot.binding_key
    if slot.tag_control == IMAGE_REFERENCE:
        return slot.node_id
    return _legacy_binding_key_from_slot(slot)


def role_for_image_slot(slot: ImageReferenceSlot) -> str:
    """ReferenceLatent / mute role for this slot (unique per generic image loader)."""
    if slot.tag_control == IMAGE_REFERENCE:
        return f"ref_{slot.node_id}"
    return _role_from_reference_control(slot.tag_control, slot.slot_key or None)


def _slot_key_from_tag_slot(slot: str | None) -> str:
    return slot or ""


def _default_semantic_for_tag(control: str, slot: str | None) -> ReferenceSemantic:
    if control == POSE_REFERENCE:
        return "pose"
    if control == LOCATION_REFERENCE:
        return "location"
    if control == STYLE_REFERENCE:
        return "style"
    if control == CHARACTER_REFERENCE:
        return "character"
    if control == IMAGE_REFERENCE:
        return "unset"
    return "unset"


def _sort_image_reference_slots(slots: list[ImageReferenceSlot]) -> list[ImageReferenceSlot]:
    def _key(s: ImageReferenceSlot) -> tuple[int, int, str]:
        slot_ord = int(s.slot_key) if str(s.slot_key).isdigit() else (0 if not s.slot_key else 999)
        node_ord = int(s.node_id) if str(s.node_id).isdigit() else 0
        return (s.image_index if s.image_index else 9999, slot_ord, f"{node_ord:020d}")

    return sorted(slots, key=_key)


def discover_image_reference_slots(workflow: dict[str, Any]) -> list[ImageReferenceSlot]:
    """Discover up to four bare ``THM-ImageReference`` LoadImage slots in wiring order."""
    _warn_deprecated_reference_tags(workflow)
    seen: set[str] = set()
    raw: list[ImageReferenceSlot] = []
    wiring = discover_reference_wiring_order(workflow)
    load_to_index: dict[str, int] = {}
    if wiring.entries:
        for entry in wiring.entries:
            load_to_index[entry.load_image_id] = entry.image_index

    for control_node in find_tagged_control_nodes(workflow, IMAGE_REFERENCE):
        if control_node.node_id in seen:
            continue
        if control_node.slot:
            _console_print(
                f"[REF] Custom workflow uses suffixed THM-ImageReference tag "
                f"({control_node.title}); use duplicate bare titles only."
            )
        seen.add(control_node.node_id)
        slot_key = _slot_key_from_tag_slot(control_node.slot)
        raw.append(
            ImageReferenceSlot(
                slot_key=slot_key,
                node_id=control_node.node_id,
                title=control_node.title,
                tag_control=IMAGE_REFERENCE,
                default_semantic="unset",
                image_index=load_to_index.get(control_node.node_id, 0),
            )
        )

    ordered = _sort_image_reference_slots(raw)
    capped = ordered[:MAX_IMAGE_REFERENCE_SLOTS]
    return _assign_binding_keys(capped)


def clear_tagged_reference_nodes(workflow: dict[str, Any]) -> int:
    """Clear image inputs on all ``THM-ImageReference`` LoadImage nodes."""
    count = 0
    for control_node in find_tagged_control_nodes(workflow, IMAGE_REFERENCE):
        _write_image_input_on_control_node(control_node, "")
        count += 1
    return count


def _resolve_character_path_by_id(project: dict[str, Any], character_id: str) -> str:
    if not character_id:
        return ""
    chars = project.get("characters") or []
    char_by_id = {c.get("id"): c for c in chars if isinstance(c, dict) and c.get("id")}
    char_by_name = {
        str(c.get("name", "")).strip().lower(): c
        for c in chars
        if isinstance(c, dict) and c.get("name")
    }
    ref = str(character_id).strip()
    char = char_by_id.get(ref) or char_by_name.get(ref.lower())
    if not char:
        return ""
    return str(char.get("reference_image") or "").strip()


def _binding_semantic(binding: dict[str, Any] | None) -> ReferenceSemantic:
    sem = str((binding or {}).get("semantic") or "unset").strip().lower()
    if sem in ("pose", "character", "location", "style", "unset"):
        return sem  # type: ignore[return-value]
    return "unset"


def _pinned_reference_path(binding: dict[str, Any] | None) -> str:
    pin = str((binding or {}).get("reference_image") or "").strip()
    if pin and os.path.isfile(pin):
        return pin
    return ""


def merge_binding_on_semantic_choice(
    old_binding: dict[str, Any],
    new_binding: dict[str, Any],
    prev_choice: str | None,
    choice: str,
) -> dict[str, Any]:
    """Apply semantic dropdown change; preserve keyframe gallery pin when choice is unchanged."""
    merged = dict(new_binding)
    if prev_choice is not None and choice != prev_choice:
        return merged
    old_pin = str(old_binding.get("reference_image") or "").strip()
    if old_pin:
        merged["reference_image"] = old_pin
    return merged


_IMAGE_GALLERY_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_ASSET_GALLERY_SUBDIRS = {
    "settings": "_locations",
    "styles": "_styles",
    "characters": "_characters",
}


def _asset_id_for_path(asset_id: str) -> str:
    aid = str(asset_id or "").strip()
    if not aid:
        return "asset"
    aid = re.sub(r'[<>:"/\\|?*]', "", aid)
    return aid or "asset"


def _project_output_base(project: dict[str, Any]) -> Path | None:
    output_root = str((project.get("comfy") or {}).get("output_root") or "").strip()
    name = str(project.get("name") or "").strip()
    if not output_root or not name:
        return None
    return Path(output_root) / name


def _asset_gallery_dir(project: dict[str, Any], collection_key: str, asset_id: str) -> Path | None:
    subfolder = _ASSET_GALLERY_SUBDIRS.get(collection_key)
    if not subfolder or not asset_id:
        return None
    base = _project_output_base(project)
    if not base:
        return None
    return base / subfolder / _asset_id_for_path(asset_id)


def _first_image_in_dir(directory: str | Path | None) -> str:
    if not directory:
        return ""
    p = Path(directory)
    if not p.is_dir():
        return ""
    try:
        files = sorted(
            [fp.resolve() for fp in p.iterdir() if fp.is_file() and fp.suffix.lower() in _IMAGE_GALLERY_EXTS],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        return str(files[0]) if files else ""
    except OSError:
        return ""


def _default_asset_reference_path(
    project: dict[str, Any],
    collection_key: str,
    asset_id: str,
) -> str:
    """Asset canonical reference_image, else first image in asset gallery folder."""
    if collection_key == "settings":
        asset_path = _resolve_setting_path_by_id(project, asset_id)
    elif collection_key == "styles":
        asset_path = _resolve_style_path_by_id(project, asset_id)
    elif collection_key == "characters":
        asset_path = _resolve_character_path_by_id(project, asset_id)
    else:
        asset_path = ""
    if asset_path and os.path.isfile(asset_path):
        return asset_path
    gallery_dir = _asset_gallery_dir(project, collection_key, asset_id)
    gallery_path = _first_image_in_dir(gallery_dir)
    if gallery_path:
        return gallery_path
    return asset_path


def _default_pose_reference_path(project: dict[str, Any], pose_path: str | None) -> str:
    p = str(pose_path or "").strip()
    if p and p != "(No pose)" and os.path.isfile(p):
        return p
    base = _project_output_base(project)
    gallery_path = _first_image_in_dir(base / "_poses" if base else None)
    if gallery_path:
        return gallery_path
    return p if p and p != "(No pose)" else ""


def resolve_binding_default_reference_path(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    binding: dict[str, Any],
    *,
    pose_path: str | None = None,
) -> str:
    """Default filesystem image for a binding (ignores any existing reference_image pin)."""
    bare = {k: v for k, v in (binding or {}).items() if k != "reference_image"}
    sem = _binding_semantic(bare)
    if sem == "unset":
        return ""
    if sem == "pose":
        return _default_pose_reference_path(project, pose_path)
    if sem == "character":
        cid = str(bare.get("character_id") or "").strip()
        return _default_asset_reference_path(project, "characters", cid) if cid else ""
    if sem == "location":
        return resolve_location_reference_path_from_binding(project, sequence, bare)
    if sem == "style":
        return resolve_style_reference_path(project, sequence, bare)
    return ""


def ensure_binding_default_reference_image(
    binding: dict[str, Any],
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    *,
    pose_path: str | None = None,
    choice_changed: bool = True,
) -> dict[str, Any]:
    """Pin the default gallery/asset image when semantic is newly selected."""
    result = dict(binding or {})
    sem = _binding_semantic(result)
    if sem == "unset":
        result.pop("reference_image", None)
        return result
    if not choice_changed and _pinned_reference_path(result):
        return result
    default_path = resolve_binding_default_reference_path(
        project, sequence, result, pose_path=pose_path
    )
    if default_path and os.path.isfile(default_path):
        result["reference_image"] = default_path
    else:
        result.pop("reference_image", None)
    return result


def _sequence_pinned_path(sequence: dict[str, Any] | None, field_name: str) -> str:
    pin = str((sequence or {}).get(field_name) or "").strip()
    if pin and os.path.isfile(pin):
        return pin
    return ""


def resolve_sequence_setting_reference_path(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
) -> str:
    """Sequence location image: sequence pin, else asset default, else first gallery file."""
    seq_pin = _sequence_pinned_path(sequence, "setting_reference_image")
    if seq_pin:
        return seq_pin
    seq = sequence or {}
    setting_id = str(seq.get("setting_id") or seq.get("setting_asset") or "").strip()
    if not setting_id:
        return ""
    return _default_asset_reference_path(project, "settings", setting_id)


def resolve_sequence_style_reference_path(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
) -> str:
    """Sequence style image: sequence pin, else asset default, else first gallery file."""
    seq_pin = _sequence_pinned_path(sequence, "style_reference_image")
    if seq_pin:
        return seq_pin
    seq = sequence or {}
    style_id = str(seq.get("style_id") or "").strip()
    if not style_id:
        return ""
    return _default_asset_reference_path(project, "styles", style_id)


def _resolve_setting_path_by_id(project: dict[str, Any], setting_id: str) -> str:
    if not setting_id:
        return ""
    settings = project.get("settings") or []
    by_id = {s.get("id"): s for s in settings if isinstance(s, dict) and s.get("id")}
    by_name = {
        str(s.get("name", "")).strip().lower(): s
        for s in settings
        if isinstance(s, dict) and s.get("name")
    }
    ref = str(setting_id).strip()
    setting = by_id.get(ref) or by_name.get(ref.lower())
    if not setting:
        return ""
    return str(setting.get("reference_image") or "").strip()


def _resolve_style_path_by_id(project: dict[str, Any], style_id: str) -> str:
    if not style_id:
        return ""
    styles = project.get("styles") or []
    by_id = {s.get("id"): s for s in styles if isinstance(s, dict) and s.get("id")}
    by_name = {
        str(s.get("name", "")).strip().lower(): s
        for s in styles
        if isinstance(s, dict) and s.get("name")
    }
    ref = str(style_id).strip()
    style = by_id.get(ref) or by_name.get(ref.lower())
    if not style:
        return ""
    return str(style.get("reference_image") or "").strip()


def effective_setting_id_for_binding(
    binding: dict[str, Any] | None,
    sequence: dict[str, Any] | None,
) -> str:
    explicit = str((binding or {}).get("setting_id") or "").strip()
    if explicit:
        return explicit
    if str((binding or {}).get("source") or "").strip().lower() == "sequence":
        return str((sequence or {}).get("setting_id") or (sequence or {}).get("setting_asset") or "").strip()
    return ""


def effective_style_id_for_binding(
    binding: dict[str, Any] | None,
    sequence: dict[str, Any] | None,
) -> str:
    explicit = str((binding or {}).get("style_id") or "").strip()
    if explicit:
        return explicit
    if str((binding or {}).get("source") or "").strip().lower() == "sequence":
        return str((sequence or {}).get("style_id") or "").strip()
    return ""


def _setting_display_name_for_id(project: dict[str, Any], setting_id: str) -> str:
    if not setting_id:
        return "location"
    settings = project.get("settings") or []
    for item in settings:
        if isinstance(item, dict) and item.get("id") == setting_id:
            return str(item.get("name") or setting_id)
    return setting_id


def _style_display_name_for_id(project: dict[str, Any], style_id: str) -> str:
    if not style_id:
        return "style"
    styles = project.get("styles") or []
    for item in styles:
        if isinstance(item, dict) and item.get("id") == style_id:
            return str(item.get("name") or style_id)
    return style_id


def resolve_style_reference_path(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    binding: dict[str, Any] | None = None,
) -> str:
    """Resolve style reference image from binding pin, explicit style_id, or sequence."""
    pin = _pinned_reference_path(binding)
    if pin:
        return pin
    if binding and str(binding.get("source") or "").strip().lower() == "sequence":
        return resolve_sequence_style_reference_path(project, sequence)
    style_id = effective_style_id_for_binding(binding, sequence)
    if style_id:
        return _default_asset_reference_path(project, "styles", style_id)
    return resolve_sequence_style_reference_path(project, sequence)


def resolve_location_reference_path_from_binding(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    binding: dict[str, Any] | None = None,
) -> str:
    """Resolve location reference from binding pin, explicit setting_id, or sequence."""
    pin = _pinned_reference_path(binding)
    if pin:
        return pin
    if binding and str(binding.get("source") or "").strip().lower() == "sequence":
        return resolve_sequence_setting_reference_path(project, sequence)
    setting_id = effective_setting_id_for_binding(binding, sequence)
    if setting_id:
        return _default_asset_reference_path(project, "settings", setting_id)
    return resolve_sequence_setting_reference_path(project, sequence)


def enforce_one_pose_binding(bindings: dict[str, dict[str, Any]]) -> None:
    """Keep at most one slot bound to pose semantics (last wins)."""
    pose_keys = [sk for sk, b in bindings.items() if _binding_semantic(b) == "pose"]
    if len(pose_keys) <= 1:
        return
    for sk in pose_keys[:-1]:
        bindings[sk] = {"semantic": "unset"}


def _legacy_character_id_for_slot(slot_key: str, desired: list[str]) -> str:
    if slot_key in ("2", "right"):
        return desired[1] if len(desired) >= 2 else ""
    return desired[0] if desired else ""


def _has_semantic_assigned(bindings: dict[str, dict[str, Any]], semantic: str) -> bool:
    return any(_binding_semantic(b) == semantic for b in bindings.values())


def _apply_sequence_defaults_to_generic_slots(
    bindings: dict[str, dict[str, Any]],
    slots: list[ImageReferenceSlot],
    sequence: dict[str, Any] | None,
) -> None:
    """Auto-assign sequence location then style to first free generic slots (wiring order)."""
    seq = sequence or {}
    seq_setting = str(seq.get("setting_id") or seq.get("setting_asset") or "").strip()
    seq_style = str(seq.get("style_id") or "").strip()
    location_assigned = _has_semantic_assigned(bindings, "location")
    style_assigned = _has_semantic_assigned(bindings, "style")

    for slot in slots:
        if slot.tag_control != IMAGE_REFERENCE:
            continue
        sk = binding_key_for_slot(slot)
        if sk in bindings:
            continue
        if not location_assigned and seq_setting:
            bindings[sk] = {"semantic": "location", "source": "sequence"}
            location_assigned = True
        elif not style_assigned and seq_style:
            bindings[sk] = {"semantic": "style", "source": "sequence"}
            style_assigned = True


def _first_free_generic_slot(
    slots: list[ImageReferenceSlot],
    bindings: dict[str, dict[str, Any]],
) -> ImageReferenceSlot | None:
    for slot in slots:
        if slot.tag_control != IMAGE_REFERENCE:
            continue
        if binding_key_for_slot(slot) not in bindings:
            return slot
    return None


def _assigned_character_ids(bindings: dict[str, dict[str, Any]]) -> set[str]:
    assigned: set[str] = set()
    for binding in bindings.values():
        if _binding_semantic(binding) == "character":
            cid = str(binding.get("character_id") or "").strip()
            if cid:
                assigned.add(cid)
    return assigned


def _legacy_characters_need_backfill(kf: dict[str, Any], bindings: dict[str, dict[str, Any]]) -> bool:
    assigned = _assigned_character_ids(bindings)
    for cid in kf.get("characters") or []:
        if cid and isinstance(cid, str) and str(cid).strip() and str(cid).strip() not in assigned:
            return True
    return False


def _legacy_pose_needs_backfill(kf: dict[str, Any], bindings: dict[str, dict[str, Any]]) -> bool:
    pose_path = str(kf.get("pose") or "").strip()
    if not pose_path or pose_path == "(No pose)":
        return False
    return not _has_semantic_assigned(bindings, "pose")


def _should_seed_legacy_bindings(kf: dict[str, Any], bindings: dict[str, dict[str, Any]]) -> bool:
    if not bindings:
        return True
    return _legacy_characters_need_backfill(kf, bindings) or _legacy_pose_needs_backfill(kf, bindings)


def _seed_legacy_pose_characters_and_sequence(
    bindings: dict[str, dict[str, Any]],
    kf: dict[str, Any],
    slots: list[ImageReferenceSlot],
    sequence: dict[str, Any] | None,
) -> None:
    """Fill free generic slots: legacy pose, then legacy characters, then sequence location/style."""
    pose_path = str(kf.get("pose") or "").strip()
    if pose_path and pose_path != "(No pose)" and not _has_semantic_assigned(bindings, "pose"):
        slot = _first_free_generic_slot(slots, bindings)
        if slot:
            bindings[binding_key_for_slot(slot)] = {"semantic": "pose"}

    assigned = _assigned_character_ids(bindings)
    for cid in kf.get("characters") or []:
        if not cid or not isinstance(cid, str):
            continue
        cid = str(cid).strip()
        if not cid or cid in assigned:
            continue
        slot = _first_free_generic_slot(slots, bindings)
        if not slot:
            break
        bindings[binding_key_for_slot(slot)] = {"semantic": "character", "character_id": cid}
        assigned.add(cid)

    _apply_sequence_defaults_to_generic_slots(bindings, slots, sequence)


def normalize_reference_bindings(
    kf: dict[str, Any],
    slots: list[ImageReferenceSlot],
    project: dict[str, Any],
    sequence: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Ensure reference_bindings exist; migrate from legacy pose/characters when missing."""
    bindings: dict[str, dict[str, Any]] = {
        str(k): dict(v) for k, v in (kf.get("reference_bindings") or {}).items() if isinstance(v, dict)
    }
    if not slots:
        enforce_one_pose_binding(bindings)
        return bindings

    canonical = {binding_key_for_slot(s) for s in slots}
    if bindings and any(k not in canonical for k in bindings):
        bindings = remap_reference_bindings_for_slots(bindings, slots)

    if _should_seed_legacy_bindings(kf, bindings):
        _seed_legacy_pose_characters_and_sequence(bindings, kf, slots, sequence)
    else:
        _apply_sequence_defaults_to_generic_slots(bindings, slots, sequence)

    enforce_one_pose_binding(bindings)
    return bindings


def sync_reference_bindings_to_legacy(
    kf: dict[str, Any],
    bindings: dict[str, dict[str, Any]],
    slots: list[ImageReferenceSlot],
) -> None:
    """Write reference_bindings and mirror pose/characters for legacy consumers."""
    if slots:
        canonical = {binding_key_for_slot(s) for s in slots}
        bindings = {k: v for k, v in bindings.items() if k in canonical}
    kf["reference_bindings"] = bindings
    char_ids: list[str] = []
    for slot in slots:
        sk = binding_key_for_slot(slot)
        binding = bindings.get(sk) or {}
        if _binding_semantic(binding) == "character":
            cid = str(binding.get("character_id") or "").strip()
            if cid and cid not in char_ids:
                char_ids.append(cid)
    while len(char_ids) < 2:
        char_ids.append("")
    kf["characters"] = char_ids[:2]


def resolve_reference_paths_from_bindings(
    kf: dict[str, Any],
    slots: list[ImageReferenceSlot],
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
) -> dict[str, str]:
    """Map slot_key -> filesystem path from normalized bindings."""
    bindings = normalize_reference_bindings(kf, slots, project, sequence)
    paths: dict[str, str] = {}
    pose_path = str(kf.get("pose") or "").strip()
    for slot in slots:
        sk = binding_key_for_slot(slot)
        binding = bindings.get(sk) or bindings.get(slot.slot_key) or {}
        sem = _binding_semantic(binding)
        if sem == "unset":
            paths[sk] = ""
        elif sem == "pose":
            pin = _pinned_reference_path(binding)
            if pin:
                paths[sk] = pin
            else:
                paths[sk] = _default_pose_reference_path(project, pose_path)
        elif sem == "character":
            pin = _pinned_reference_path(binding)
            cid = str(binding.get("character_id") or "").strip()
            asset = _default_asset_reference_path(project, "characters", cid) if cid else ""
            paths[sk] = pin or asset
        elif sem == "location":
            paths[sk] = resolve_location_reference_path_from_binding(project, sequence, binding)
        elif sem == "style":
            paths[sk] = resolve_style_reference_path(project, sequence, binding)
        else:
            paths[sk] = ""
    return paths


def path_for_image_slot(slot: ImageReferenceSlot, paths_by_slot: dict[str, str]) -> str:
    return paths_by_slot.get(binding_key_for_slot(slot), paths_by_slot.get(slot.slot_key, ""))


def inject_reference_slot_paths(
    workflow: dict[str, Any],
    slots: list[ImageReferenceSlot],
    paths_by_slot: dict[str, str],
) -> int:
    count = 0
    for slot in slots:
        path = path_for_image_slot(slot, paths_by_slot)
        for control_node in find_tagged_control_nodes(workflow, slot.tag_control):
            if control_node.node_id != slot.node_id:
                continue
            _write_image_input_on_control_node(control_node, path)
            count += 1
            break
    return count


def resolve_character_reference_paths(
    project: dict[str, Any],
    id_conf: dict[str, Any],
) -> dict[str | None, str]:
    """Map character reference slots to filesystem paths from keyframe character picks.

    Single-primary phase: ``characters[0]`` feeds unsuffixed ``THM-CharacterReference`` and
    ``THM-CharacterReference-1``. ``characters[1]`` feeds ``THM-CharacterReference-2`` only
    when a second character is selected (multi-slot workflows).
    """
    chars = project.get("characters") or []
    char_by_id = {c.get("id"): c for c in chars if isinstance(c, dict) and c.get("id")}
    char_by_name = {
        str(c.get("name", "")).strip().lower(): c
        for c in chars
        if isinstance(c, dict) and c.get("name")
    }

    def _resolve_char(ref: str) -> dict[str, Any] | None:
        if not ref:
            return None
        if ref in char_by_id:
            return char_by_id[ref]
        return char_by_name.get(str(ref).strip().lower())

    def _path_for(ref: str) -> str:
        char = _resolve_char(ref)
        if not char:
            return ""
        return str(char.get("reference_image") or "").strip()

    desired = [v for v in (id_conf or {}).get("characters", []) if v and isinstance(v, str)]
    paths: dict[str | None, str] = {}
    if len(desired) >= 1:
        p = _path_for(desired[0])
        paths[None] = p
        paths["1"] = p
        paths["left"] = p
    if len(desired) >= 2:
        p = _path_for(desired[1])
        paths["2"] = p
        paths["right"] = p
    return paths


def resolve_location_reference_path(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
) -> str:
    """Resolve location/setting reference image from sequence (pin, asset default, first gallery)."""
    return resolve_sequence_setting_reference_path(project, sequence)


def inject_location_references(workflow: dict[str, Any], image_path: str) -> int:
    if not image_path:
        return 0
    return set_image_reference(workflow, LOCATION_REFERENCE, image_path)


def _role_from_reference_control(control: str, slot: str | None) -> str:
    if control == POSE_REFERENCE:
        return "pose"
    if control == LOCATION_REFERENCE:
        return "location"
    if control == STYLE_REFERENCE:
        return "style"
    if control == CHARACTER_REFERENCE:
        if slot in ("2", "right"):
            return "character_2"
        return "character_1"
    if control == IMAGE_REFERENCE:
        return f"ref_{slot or '1'}"
    return control


def _input_node_ref(node: dict[str, Any], key: str) -> list[Any] | None:
    val = (node.get("inputs") or {}).get(key)
    if isinstance(val, list) and len(val) >= 2:
        return [str(val[0]), val[1]]
    return None


def _trace_tagged_load_image_upstream(
    workflow: dict[str, Any],
    start_id: str,
) -> tuple[str | None, dict[str, Any] | None, str | None, str | None]:
    """Walk upstream from a node to the tagged LoadImage feeding a ReferenceLatent."""
    visited: set[str] = set()
    stack = [start_id]
    while stack:
        nid = stack.pop()
        if nid in visited or nid not in workflow:
            continue
        visited.add(nid)
        node = workflow[nid]
        if not isinstance(node, dict):
            continue
        title = node_title(node)
        parsed, slot = _parse_tag(title)
        if parsed in TAGGED_REFERENCE_CONTROLS:
            return nid, node, parsed, slot
        for val in (node.get("inputs") or {}).values():
            if isinstance(val, list) and len(val) >= 2 and isinstance(val[0], str):
                stack.append(str(val[0]))
    return None, None, None, None


def _collect_nodes_between(
    workflow: dict[str, Any],
    start_id: str,
    end_id: str,
) -> list[str]:
    """Collect intermediate node ids on paths from start toward end (exclude endpoints)."""
    found: list[str] = []

    def walk(nid: str, target: str, path: list[str]) -> bool:
        if nid == target:
            found.extend(path)
            return True
        if nid in path:
            return False
        node = workflow.get(nid)
        if not isinstance(node, dict):
            return False
        next_path = path + [nid]
        for val in (node.get("inputs") or {}).values():
            if isinstance(val, list) and len(val) >= 2 and isinstance(val[0], str):
                if walk(str(val[0]), target, next_path):
                    return True
        return False

    walk(start_id, end_id, [])
    return list(dict.fromkeys(found))


@dataclass
class ReferenceWiringEntry:
    role: str
    image_index: int
    reference_latent_id: str
    load_image_id: str
    title: str
    control: str
    slot: str | None = None
    upstream_conditioning: list[Any] | None = None
    branch_node_ids: list[str] = field(default_factory=list)
    effective_image_index: int | None = None


@dataclass
class ReferenceWiringDiscovery:
    tier: str  # linear_ref_stack | tagged_loader_only | none
    entries: list[ReferenceWiringEntry] = field(default_factory=list)
    orphan_loaders: list[dict[str, Any]] = field(default_factory=list)
    warning: str | None = None


def _reference_chain_length(workflow: dict[str, Any], ref_ids: set[str], children: dict[str, list[str]], root: str) -> int:
    best = 0

    def dfs(nid: str, depth: int) -> None:
        nonlocal best
        best = max(best, depth)
        for child in children.get(nid, []):
            dfs(child, depth + 1)

    dfs(root, 1)
    return best


def _reference_latent_graph(
    workflow: dict[str, Any],
) -> tuple[set[str], dict[str, list[str]], list[str]] | tuple[None, None, None]:
    ref_ids = {
        str(nid)
        for nid, node in workflow.items()
        if isinstance(node, dict) and node_class(node) == "ReferenceLatent"
    }
    if not ref_ids:
        return None, None, None

    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for nid in ref_ids:
        node = workflow[nid]
        if not isinstance(node, dict):
            continue
        cref = _input_node_ref(node, "conditioning")
        if cref and cref[0] in ref_ids:
            children.setdefault(cref[0], []).append(nid)
        else:
            roots.append(nid)
    return ref_ids, children, roots


def _sort_ref_node_ids(node_ids: list[str]) -> list[str]:
    def _key(nid: str) -> tuple[int, str]:
        return (0, f"{int(nid):020d}") if str(nid).isdigit() else (1, str(nid))

    return sorted(node_ids, key=_key)


def _walk_reference_chain_ids(
    root: str,
    workflow: dict[str, Any],
    ref_ids: set[str],
    children: dict[str, list[str]],
) -> list[str]:
    chain_ids: list[str] = []

    def walk_chain(nid: str) -> None:
        chain_ids.append(nid)
        kids = children.get(nid, [])
        if not kids:
            return
        if len(kids) == 1:
            walk_chain(kids[0])
            return
        walk_chain(max(kids, key=lambda k: _reference_chain_length(workflow, ref_ids, children, k)))

    walk_chain(root)
    return chain_ids


def _entries_for_reference_chain(
    workflow: dict[str, Any],
    chain_ids: list[str],
    chain_load_ids: set[str] | None = None,
) -> list[ReferenceWiringEntry]:
    entries: list[ReferenceWiringEntry] = []
    loads = chain_load_ids if chain_load_ids is not None else set()
    for image_index, ref_id in enumerate(chain_ids, start=1):
        ref_node = workflow.get(ref_id)
        if not isinstance(ref_node, dict):
            continue
        upstream = _input_node_ref(ref_node, "conditioning")
        latent_ref = _input_node_ref(ref_node, "latent")
        load_id, load_node, control, slot = (None, None, None, None)
        if latent_ref:
            load_id, load_node, control, slot = _trace_tagged_load_image_upstream(workflow, latent_ref[0])
        if not load_id or not control:
            continue
        branch_nodes: list[str] = []
        if latent_ref:
            branch_nodes = _collect_nodes_between(workflow, load_id, ref_id)
        if control == IMAGE_REFERENCE:
            role = f"ref_{load_id}"
        else:
            role = _role_from_reference_control(control, slot)
        entries.append(
            ReferenceWiringEntry(
                role=role,
                image_index=image_index,
                reference_latent_id=ref_id,
                load_image_id=load_id,
                title=node_title(load_node) if load_node else "",
                control=control,
                slot=slot,
                upstream_conditioning=list(upstream) if upstream else None,
                branch_node_ids=branch_nodes,
            )
        )
        loads.add(load_id)
    return entries


def discover_all_reference_stack_entries(
    workflow: dict[str, Any],
) -> list[list[ReferenceWiringEntry]]:
    """Every parallel ReferenceLatent chain (e.g. positive + negative KSampler stacks)."""
    ref_ids, children, roots = _reference_latent_graph(workflow)
    if not ref_ids or not roots:
        return []

    stacks: list[list[ReferenceWiringEntry]] = []
    for root in _sort_ref_node_ids(roots):
        chain_ids = _walk_reference_chain_ids(root, workflow, ref_ids, children)
        entries = _entries_for_reference_chain(workflow, chain_ids)
        if entries:
            stacks.append(entries)
    return stacks


def discover_reference_wiring_order(workflow: dict[str, Any]) -> ReferenceWiringDiscovery:
    """Discover ReferenceLatent stack order and map each hop to a tagged LoadImage role."""
    ref_ids, children, roots = _reference_latent_graph(workflow)
    if not ref_ids:
        return ReferenceWiringDiscovery(tier="none", entries=[])

    if not roots:
        return ReferenceWiringDiscovery(
            tier="none",
            entries=[],
            warning="ReferenceLatent nodes found but no stack root.",
        )

    sorted_roots = _sort_ref_node_ids(roots)
    root = min(
        sorted_roots,
        key=lambda r: (
            -_reference_chain_length(workflow, ref_ids, children, r),
            int(r) if str(r).isdigit() else 0,
        ),
    )

    chain_ids = _walk_reference_chain_ids(root, workflow, ref_ids, children)
    chain_load_ids: set[str] = set()
    entries = _entries_for_reference_chain(workflow, chain_ids, chain_load_ids)

    orphan_loaders: list[dict[str, Any]] = []
    for control_node in find_tagged_control_nodes(workflow, IMAGE_REFERENCE):
        if control_node.node_id in chain_load_ids:
            continue
        orphan_loaders.append(_control_node_summary(control_node))

    if entries and not orphan_loaders:
        tier = "linear_ref_stack"
    elif entries and orphan_loaders:
        tier = "linear_ref_stack"
    elif orphan_loaders:
        tier = "tagged_loader_only"
    else:
        tier = "none"

    warning = None
    if len(roots) > 1:
        warning = "Multiple ReferenceLatent roots; canonical chain uses lowest root id."
    if orphan_loaders and entries:
        warning = (warning + " " if warning else "") + "Some tagged loaders are outside the ReferenceLatent chain."

    return ReferenceWiringDiscovery(
        tier=tier,
        entries=entries,
        orphan_loaders=orphan_loaders,
        warning=warning,
    )


def patch_discovery_roles_from_slots(
    discovery: ReferenceWiringDiscovery,
    slots: list[ImageReferenceSlot],
) -> None:
    """Align wiring entry roles with discovered slot binding keys."""
    load_to_role = _load_id_to_role_from_slots(slots)
    for entry in discovery.entries:
        if entry.control == IMAGE_REFERENCE and entry.load_image_id in load_to_role:
            entry.role = load_to_role[entry.load_image_id]


def _load_id_to_role_from_slots(slots: list[ImageReferenceSlot]) -> dict[str, str]:
    return {
        s.node_id: role_for_image_slot(s)
        for s in slots
        if s.tag_control == IMAGE_REFERENCE
    }


def _patch_stack_entry_roles(
    stacks: list[list[ReferenceWiringEntry]],
    slots: list[ImageReferenceSlot],
) -> None:
    load_to_role = _load_id_to_role_from_slots(slots)
    for entries in stacks:
        for entry in entries:
            if entry.control == IMAGE_REFERENCE and entry.load_image_id in load_to_role:
                entry.role = load_to_role[entry.load_image_id]


def build_reference_active_by_role(
    *,
    pose_path: str | None,
    character_paths: dict[str | None, str],
    location_path: str,
    style_path: str = "",
    slots: list[ImageReferenceSlot] | None = None,
    paths_by_slot: dict[str, str] | None = None,
) -> dict[str, bool]:

    def _active_path(path: str) -> bool:
        p = str(path or "").strip()
        return bool(p) and p != "(No pose)" and os.path.isfile(p)

    active = {
        "pose": _active_path(pose_path or "") and str(pose_path).strip() != "(No pose)",
        "character_1": _active_path(
            character_paths.get(None)
            or character_paths.get("1")
            or character_paths.get("left")
            or ""
        ),
        "character_2": _active_path(
            character_paths.get("2") or character_paths.get("right") or ""
        ),
        "location": _active_path(location_path),
        "style": _active_path(style_path),
    }

    if slots and paths_by_slot is not None:
        for slot in slots:
            path = path_for_image_slot(slot, paths_by_slot)
            is_active = _active_path(path)
            role = role_for_image_slot(slot)
            active[role] = is_active

    return active


def assign_effective_image_indices(
    entries: list[ReferenceWiringEntry],
    active_by_role: dict[str, bool],
) -> None:
    idx = 1
    for entry in entries:
        if active_by_role.get(entry.role):
            entry.effective_image_index = idx
            idx += 1
        else:
            entry.effective_image_index = None


_assign_effective_image_indices = assign_effective_image_indices


def _reference_latent_ids_for_load(workflow: dict[str, Any], load_image_id: str) -> list[str]:
    """All ReferenceLatent nodes whose latent image traces back to this LoadImage."""
    ids: list[str] = []
    for nid, node in find_nodes_by_class(workflow, "ReferenceLatent"):
        latent = _input_node_ref(node, "latent")
        if not latent:
            continue
        traced_load, _, _, _ = _trace_tagged_load_image_upstream(workflow, latent[0])
        if traced_load == load_image_id:
            ids.append(str(nid))
    return ids


def _nodes_in_reference_subgraph(workflow: dict[str, Any], load_image_id: str) -> set[str]:
    """LoadImage, encoders/scalers, and every ReferenceLatent fed by this loader."""
    nodes: set[str] = {load_image_id}
    for ref_id in _reference_latent_ids_for_load(workflow, load_image_id):
        nodes.add(ref_id)
        nodes.update(_collect_nodes_between(workflow, load_image_id, ref_id))
    return nodes


def _mute_role_reference_subgraph(workflow: dict[str, Any], load_image_id: str) -> int:
    """Mute loader + full encode/latent path so Comfy never opens empty image (input/)."""
    count = 0
    for nid in _nodes_in_reference_subgraph(workflow, load_image_id):
        node = workflow.get(nid)
        if isinstance(node, dict):
            mute_workflow_node(node)
            count += 1
    return count


def mute_inactive_reference_slots(
    workflow: dict[str, Any],
    discovery: ReferenceWiringDiscovery,
    active_by_role: dict[str, bool],
    slots: list[ImageReferenceSlot] | None = None,
) -> int:
    """Mute every inactive tagged reference (including parallel ReferenceLatent branches)."""
    load_to_role = _load_id_to_role_from_slots(slots or [])
    muted_loads: set[str] = set()
    count = 0
    for control in TAGGED_REFERENCE_CONTROLS:
        for control_node in find_tagged_control_nodes(workflow, control):
            if control_node.control == IMAGE_REFERENCE:
                tag_role = f"ref_{control_node.node_id}"
            else:
                tag_role = _role_from_reference_control(control_node.control, control_node.slot)
            role = load_to_role.get(control_node.node_id, tag_role)
            if active_by_role.get(role):
                continue
            load_id = control_node.node_id
            if load_id in muted_loads:
                continue
            muted_loads.add(load_id)
            n = _mute_role_reference_subgraph(workflow, load_id)
            if n:
                _console_print(f"[REF] muted inactive {role} subgraph ({load_id}, {n} nodes)")
            count += n
    return count


def _is_muted_workflow_node(node: dict[str, Any] | None) -> bool:
    return isinstance(node, dict) and node.get("mode") == COMFY_NODE_MODE_NEVER


def _apply_branch_skip_rewire(
    workflow: dict[str, Any],
    entries: list[ReferenceWiringEntry],
    active_by_role: dict[str, bool],
) -> int:
    """Rewire conditioning in one ReferenceLatent stack to skip inactive roles."""
    changes = 0
    for i, entry in enumerate(entries):
        if active_by_role.get(entry.role):
            continue
        changes += _mute_role_reference_subgraph(workflow, entry.load_image_id)

        upstream = entry.upstream_conditioning
        if not upstream:
            continue
        for j in range(i + 1, len(entries)):
            next_entry = entries[j]
            if not active_by_role.get(next_entry.role):
                continue
            next_ref = workflow.get(next_entry.reference_latent_id)
            if isinstance(next_ref, dict):
                next_ref.setdefault("inputs", {})["conditioning"] = list(upstream)
                changes += 1
                _console_print(
                    f"[REF] branch inactive: {entry.role} "
                    f"(rewired {next_entry.reference_latent_id} conditioning -> {upstream[0]})"
                )
            break
    return changes


def _stack_entries_for_ref(
    ref_id: str,
    stacks: list[list[ReferenceWiringEntry]],
) -> list[ReferenceWiringEntry] | None:
    for entries in stacks:
        if any(entry.reference_latent_id == ref_id for entry in entries):
            return entries
    return None


def _last_active_reference_latent_id(
    entries: list[ReferenceWiringEntry],
    active_by_role: dict[str, bool],
) -> str | None:
    last: str | None = None
    for entry in entries:
        if active_by_role.get(entry.role):
            last = entry.reference_latent_id
    return last


def _stack_root_conditioning(entries: list[ReferenceWiringEntry]) -> list[Any] | None:
    if entries and entries[0].upstream_conditioning:
        return list(entries[0].upstream_conditioning)
    return None


def _rewire_sampler_reference_terminals(
    workflow: dict[str, Any],
    stacks: list[list[ReferenceWiringEntry]],
    active_by_role: dict[str, bool],
) -> int:
    """Point KSampler/CFGGuider positive/negative away from muted terminal ReferenceLatents."""
    if not stacks:
        return 0

    changes = 0
    for class_type in ("KSampler", "CFGGuider"):
        for _, node in find_nodes_by_class(workflow, class_type):
            inputs = node.setdefault("inputs", {})
            for key in ("positive", "negative"):
                ref_id = None
                val = inputs.get(key)
                if isinstance(val, list) and len(val) >= 2 and isinstance(val[0], str):
                    ref_id = str(val[0])
                if not ref_id:
                    continue
                ref_node = workflow.get(ref_id)
                if not isinstance(ref_node, dict) or node_class(ref_node) != "ReferenceLatent":
                    continue
                if not _is_muted_workflow_node(ref_node):
                    continue
                stack = _stack_entries_for_ref(ref_id, stacks)
                if not stack:
                    continue
                replacement = _last_active_reference_latent_id(stack, active_by_role)
                if replacement:
                    if replacement != ref_id:
                        inputs[key] = [replacement, 0]
                        changes += 1
                        _console_print(
                            f"[REF] sampler {key} rewired {ref_id} -> {replacement}"
                        )
                else:
                    fallback = _stack_root_conditioning(stack)
                    if fallback and list(inputs.get(key) or []) != fallback:
                        inputs[key] = list(fallback)
                        changes += 1
                        _console_print(
                            f"[REF] sampler {key} rewired {ref_id} -> {fallback[0]}"
                        )
    return changes


def apply_reference_branch_activation(
    workflow: dict[str, Any],
    discovery: ReferenceWiringDiscovery,
    active_by_role: dict[str, bool],
    slots: list[ImageReferenceSlot] | None = None,
) -> int:
    """Mute inactive reference branches and rewire conditioning to skip empty slots."""
    if discovery.tier != "linear_ref_stack" or not discovery.entries:
        return 0

    stacks = discover_all_reference_stack_entries(workflow)
    if not stacks:
        stacks = [discovery.entries]

    if slots:
        _patch_stack_entry_roles(stacks, slots)

    changes = 0
    for entries in stacks:
        changes += _apply_branch_skip_rewire(workflow, entries, active_by_role)

    assign_effective_image_indices(discovery.entries, active_by_role)
    changes += _rewire_sampler_reference_terminals(workflow, stacks, active_by_role)
    return changes


def _mute_inactive_orphan_loaders(
    workflow: dict[str, Any],
    discovery: ReferenceWiringDiscovery,
    active_by_role: dict[str, bool],
    slots: list[ImageReferenceSlot] | None = None,
) -> int:
    count = 0
    load_to_role = {
        s.node_id: role_for_image_slot(s)
        for s in (slots or [])
        if s.tag_control == IMAGE_REFERENCE
    }
    for summary in discovery.orphan_loaders:
        title = str(summary.get("title") or "")
        control, slot = _parse_tag(title)
        if control != IMAGE_REFERENCE:
            continue
        node_id = str(summary.get("node_id") or "")
        role = load_to_role.get(node_id) or f"ref_{node_id or slot or '1'}"
        if active_by_role.get(role):
            continue
        node = workflow.get(str(summary.get("node_id")))
        if isinstance(node, dict):
            mute_workflow_node(node)
            count += 1
    return count


def _setting_display_name(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
) -> str:
    sid = str((sequence or {}).get("setting_id") or (sequence or {}).get("setting_asset") or "").strip()
    if not sid:
        return "location"
    settings = project.get("settings") or []
    by_id = {s.get("id"): s for s in settings if isinstance(s, dict) and s.get("id")}
    by_name = {
        str(s.get("name", "")).strip().lower(): s
        for s in settings
        if isinstance(s, dict) and s.get("name")
    }
    setting = by_id.get(sid) or by_name.get(sid.lower())
    if not setting:
        return sid
    return str(setting.get("name") or sid)


def _prelude_line_for_role(role: str, image_index: int, binding_semantic: str | None = None) -> str:
    """Generic prelude line for an active slot (imageN only; no asset names)."""
    sem = (binding_semantic or "").strip().lower()
    if role == "pose" or sem == "pose":
        return f"image{image_index} defines the pose to follow."
    if role in ("character_1", "character_2") or sem == "character":
        return f"image{image_index} is a character reference."
    if role == "location" or sem == "location":
        return f"image{image_index} is the setting and location reference."
    if role == "style" or sem == "style":
        return f"image{image_index} is the style reference."
    return ""


def compose_reference_prelude(
    discovery: ReferenceWiringDiscovery,
    active_by_role: dict[str, bool],
    *,
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    id_conf: dict[str, Any],
    get_character: Any = None,
) -> str:
    """Build THM reference prelude lines for active image slots (effective image indices)."""
    del get_character  # name lookup removed; kept for call-site compatibility
    lines: list[str] = []
    if discovery.tier != "linear_ref_stack":
        return ""

    bindings = (id_conf or {}).get("reference_bindings") or {}

    for entry in discovery.entries:
        if not active_by_role.get(entry.role):
            continue
        n = entry.effective_image_index
        if not n:
            continue
        sem = None
        if entry.role.startswith("ref_"):
            node_id = entry.role.removeprefix("ref_")
            binding = bindings.get(node_id) or {}
            sem = _binding_semantic(binding) if isinstance(binding, dict) else None
        line = _prelude_line_for_role(entry.role, n, sem)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _style_display_name_for_binding(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    id_conf: dict[str, Any],
) -> str:
    bindings = id_conf.get("reference_bindings") or {}
    for binding in bindings.values():
        if isinstance(binding, dict) and _binding_semantic(binding) == "style":
            style_id = effective_style_id_for_binding(binding, sequence)
            if style_id:
                return _style_display_name_for_id(project, style_id)
    seq_style = str((sequence or {}).get("style_id") or "").strip()
    if seq_style:
        return _style_display_name_for_id(project, seq_style)
    return "style"


def _prelude_label_for_ref_role(
    role: str,
    *,
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    id_conf: dict[str, Any],
) -> str:
    node_id = role.removeprefix("ref_")
    bindings = id_conf.get("reference_bindings") or {}
    binding = bindings.get(node_id) or {}
    sem = _binding_semantic(binding)
    if sem == "pose":
        return "pose"
    if sem == "location":
        sid = effective_setting_id_for_binding(binding, sequence)
        if sid:
            return f"setting:{_setting_display_name_for_id(project, sid)}"
        return f"setting:{_setting_display_name(project, sequence)}"
    if sem == "style":
        style_id = effective_style_id_for_binding(binding, sequence)
        if style_id:
            return f"style:{_style_display_name_for_id(project, style_id)}"
        return "style:style"
    if sem == "character":
        cid = str(binding.get("character_id") or "").strip()
        chars = project.get("characters") or []
        for ch in chars:
            if not isinstance(ch, dict):
                continue
            if ch.get("id") == cid or str(ch.get("name", "")).strip().lower() == cid.lower():
                return f"character:{ch.get('name') or cid}"
        return "character:character"
    return ""


def inject_character_references(
    workflow: dict[str, Any],
    character_by_slot: dict[str | None, str],
) -> int:
    """Write character reference paths onto tagged THM-CharacterReference nodes."""
    count = 0
    for control_node in find_tagged_control_nodes(workflow, CHARACTER_REFERENCE):
        slot = control_node.slot
        path = ""
        if slot is None:
            path = (
                character_by_slot.get(None)
                or character_by_slot.get("1")
                or character_by_slot.get("left")
                or ""
            )
        else:
            path = character_by_slot.get(slot) or character_by_slot.get(str(slot)) or ""
        _write_image_input_on_control_node(control_node, path)
        count += 1
    return count


def apply_reference_injection(
    workflow: dict[str, Any],
    *,
    project: dict[str, Any],
    id_conf: dict[str, Any],
    pose_path: str | None = None,
    sequence: dict[str, Any] | None = None,
    custom_mode: bool = False,
) -> dict[str, Any]:
    """Clear tagged reference nodes, inject paths, optionally skip inactive branches."""
    cleared = clear_tagged_reference_nodes(workflow)
    id_conf = dict(id_conf or {})
    slots = discover_image_reference_slots(workflow)

    if slots:
        bindings = normalize_reference_bindings(id_conf, slots, project, sequence)
        sync_reference_bindings_to_legacy(id_conf, bindings, slots)

    effective_pose = pose_path if pose_path is not None else id_conf.get("pose")
    paths_by_slot = resolve_reference_paths_from_bindings(id_conf, slots, project, sequence) if slots else {}

    slot_count = 0
    if slots:
        slot_count = inject_reference_slot_paths(workflow, slots, paths_by_slot)
    elif effective_pose and str(effective_pose).strip() and str(effective_pose).strip() != "(No pose)":
        set_pose_reference(workflow, str(effective_pose).strip())

    bindings_for_counts = (
        normalize_reference_bindings(id_conf, slots, project, sequence) if slots else {}
    )
    pose_count = char_count = location_count = style_count = 0
    if slots:
        for slot in slots:
            sk = binding_key_for_slot(slot)
            sem = _binding_semantic(bindings_for_counts.get(sk))
            path = paths_by_slot.get(sk, "")
            active = bool(str(path).strip()) and os.path.isfile(str(path))
            if not active:
                continue
            if sem == "pose":
                pose_count += 1
            elif sem == "character":
                char_count += 1
            elif sem == "location":
                location_count += 1
            elif sem == "style":
                style_count += 1
    elif effective_pose and str(effective_pose).strip() and str(effective_pose).strip() != "(No pose)":
        pose_count = 1

    char_paths = resolve_character_reference_paths(project, id_conf)
    location_path = resolve_location_reference_path(project, sequence)
    style_path = resolve_style_reference_path(project, sequence)

    discovery = discover_reference_wiring_order(workflow)
    if slots:
        patch_discovery_roles_from_slots(discovery, slots)
    active_by_role = build_reference_active_by_role(
        pose_path=effective_pose,
        character_paths=char_paths,
        location_path=location_path,
        style_path=style_path,
        slots=slots or None,
        paths_by_slot=paths_by_slot or None,
    )
    branch_changes = mute_inactive_reference_slots(workflow, discovery, active_by_role, slots)
    if custom_mode:
        if discovery.tier == "linear_ref_stack":
            branch_changes += apply_reference_branch_activation(
                workflow, discovery, active_by_role, slots
            )
        else:
            _assign_effective_image_indices(discovery.entries, active_by_role)
        branch_changes += _mute_inactive_orphan_loaders(workflow, discovery, active_by_role, slots)

    return {
        "cleared": cleared,
        "pose": pose_count,
        "character": char_count,
        "location": location_count,
        "style": style_count,
        "slot": slot_count,
        "location_path": location_path,
        "branch_changes": branch_changes,
        "discovery": discovery,
        "active_by_role": active_by_role,
        "slots": slots,
        "paths_by_slot": paths_by_slot,
    }


def set_pose_reference(workflow: dict[str, Any], image_path: str) -> int:
    """Write pose path to legacy ``MainImageAndMask`` loader (Default family)."""
    if not image_path:
        return 0
    return set_image_reference(workflow, POSE_REFERENCE, image_path)


def _resolve_pose_reference_node_id(workflow: dict[str, Any]) -> str | None:
    """Legacy ``MainImageAndMask`` pose loader for flip injection."""
    for nid, _node in find_nodes_by_title(workflow, "MainImageAndMask"):
        return str(nid)
    return None


def inject_pose_reference_flips(workflow: dict[str, Any], id_conf: dict[str, Any]) -> int:
    """Insert ImageFlip+ nodes after pose reference output (THM or legacy pose path)."""
    flip_h = bool(id_conf.get("pose_flip_horizontal"))
    flip_v = bool(id_conf.get("pose_flip_vertical"))
    if not flip_h and not flip_v:
        return 0

    pose_node_id = _resolve_pose_reference_node_id(workflow)
    if not pose_node_id:
        _console_print("[FLIP] no pose reference node found")
        return 0

    consumers: list[tuple[str, str]] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict) or "inputs" not in node:
            continue
        for input_name, source in node["inputs"].items():
            if (
                isinstance(source, list)
                and len(source) == 2
                and str(source[0]) == str(pose_node_id)
                and source[1] == 0
            ):
                consumers.append((str(nid), input_name))

    if not consumers:
        _console_print("[FLIP] no consumers wired to pose reference node")
        return 0

    current_source_id = pose_node_id
    output_index = 0
    inserted = 0

    if flip_h:
        new_id = new_node_id(workflow)
        workflow[new_id] = {
            "inputs": {"axis": "x", "image": [str(current_source_id), output_index]},
            "class_type": "ImageFlip+",
            "_meta": {"title": "Injected_Flip_Horizontal"},
        }
        current_source_id = new_id
        inserted += 1

    if flip_v:
        new_id = new_node_id(workflow)
        workflow[new_id] = {
            "inputs": {"axis": "y", "image": [str(current_source_id), output_index]},
            "class_type": "ImageFlip+",
            "_meta": {"title": "Injected_Flip_Vertical"},
        }
        current_source_id = new_id
        inserted += 1

    for target_node_id, target_input_name in consumers:
        workflow[target_node_id]["inputs"][target_input_name] = [
            str(current_source_id),
            output_index,
        ]

    axes = []
    if flip_h:
        axes.append("horizontal")
    if flip_v:
        axes.append("vertical")
    _console_print(f"[FLIP] applied {','.join(axes)} on pose node {pose_node_id}")
    return inserted


def set_dimensions(workflow: dict[str, Any], width: int, height: int) -> int:
    tagged_count = 0
    for control_node in find_tagged_control_nodes(workflow, IMAGE_SIZE):
        inputs = control_node.node.setdefault("inputs", {})
        if "width" in inputs:
            inputs["width"] = int(width)
            tagged_count += 1
        if "height" in inputs:
            inputs["height"] = int(height)
            tagged_count += 1

    for control_node in find_tagged_control_nodes(workflow, WIDTH):
        inputs = control_node.node.setdefault("inputs", {})
        target_key = "value" if "value" in inputs else "width"
        inputs[target_key] = int(width)
        tagged_count += 1

    for control_node in find_tagged_control_nodes(workflow, HEIGHT):
        inputs = control_node.node.setdefault("inputs", {})
        target_key = "value" if "value" in inputs else "height"
        inputs[target_key] = int(height)
        tagged_count += 1

    if tagged_count:
        return tagged_count

    count = 0
    for control_node in find_legacy_control_nodes(workflow, WIDTH):
        inputs = control_node.node.setdefault("inputs", {})
        target_key = "value" if "value" in inputs else "width"
        inputs[target_key] = int(width)
        count += 1

    for control_node in find_legacy_control_nodes(workflow, HEIGHT):
        inputs = control_node.node.setdefault("inputs", {})
        target_key = "value" if "value" in inputs else "height"
        inputs[target_key] = int(height)
        count += 1

    for class_type in DIMENSION_CLASSES:
        for _, node in find_nodes_by_class(workflow, class_type):
            set_input(node, "width", int(width))
            set_input(node, "height", int(height))
            count += 2

    for _, node in find_nodes_by_class(workflow, "InpaintCropImproved"):
        set_input(node, "output_target_width", int(width))
        set_input(node, "output_target_height", int(height))
        count += 2

    return count


_GENERATION_BUNDLE_FIELDS = frozenset({STEPS, CFG, SAMPLER, SCHEDULER})
_SELECTIVE_SAMPLER_CONTROLS = (STEPS, CFG, SAMPLER, SCHEDULER, KSAMPLER)


def workflow_uses_selective_generation_tags(workflow: dict[str, Any]) -> bool:
    """True when any THM sampler-parameter tag exists (per-node steps/cfg/sampler/scheduler)."""
    for control in _SELECTIVE_SAMPLER_CONTROLS:
        if find_tagged_control_nodes(workflow, control):
            return True
    return False


def _generation_write_targets(workflow: dict[str, Any], control: str) -> list[ControlNode]:
    """Tagged nodes for one generation field, including THM-KSampler bundle nodes."""
    targets = list(find_tagged_control_nodes(workflow, control))
    if control not in _GENERATION_BUNDLE_FIELDS:
        return targets
    seen = {node.node_id for node in targets}
    for control_node in find_tagged_control_nodes(workflow, KSAMPLER):
        if control_node.node_id not in seen:
            targets.append(control_node)
            seen.add(control_node.node_id)
    return targets


def set_seed(workflow: dict[str, Any], seed: int) -> int:
    tagged = find_tagged_control_nodes(workflow, SEED)
    if tagged:
        return _set_numeric_on_nodes(tagged, int(seed), ("seed", "noise_seed", "value"))

    count = 0
    for _, node in find_nodes_by_class(workflow, "KSampler"):
        set_input(node, "seed", int(seed))
        count += 1
    for _, node in find_nodes_by_class(workflow, "RandomNoise"):
        set_input(node, "noise_seed", int(seed))
        count += 1
    return count


def set_steps(workflow: dict[str, Any], steps: int | None) -> int:
    if steps is None:
        return 0
    tagged = _generation_write_targets(workflow, STEPS)
    if tagged:
        count = _set_numeric_on_nodes(tagged, int(steps), ("steps", "value"))
        for control_node in tagged:
            node_id = control_node.node_id
            title = control_node.title or node_class(control_node.node)
            actual = (control_node.node.get("inputs") or {}).get("steps")
            if actual is None:
                actual = (control_node.node.get("inputs") or {}).get("value")
            print(f"[VIDEO] THM-Steps node {node_id} ({title}): steps={actual}")
        return count
    if workflow_uses_selective_generation_tags(workflow):
        return 0

    count = 0
    for _, node in find_nodes_by_class(workflow, "KSampler"):
        set_input(node, "steps", int(steps))
        count += 1
    for _, node in find_nodes_by_class(workflow, "Flux2Scheduler"):
        set_input(node, "steps", int(steps))
        count += 1
    return count


def workflow_uses_ltx_text_encoder(workflow: dict[str, Any]) -> bool:
    return bool(find_nodes_by_class(workflow, "LTXAVTextEncoderLoader"))


def sanitize_prompt_for_ltx(text: str) -> str:
    """Strip SD-style emphasis/weight parens that break Gemma/LTX encode_token_weights."""
    if not text:
        return ""
    out = str(text)
    for _ in range(12):
        prev = out
        out = re.sub(r"\(([^():]+):[\d.]+\)", r"\1", out)
        out = re.sub(r"\(([^()]+)\)", r"\1", out)
        if out == prev:
            break
    return out.strip()


def set_cfg(workflow: dict[str, Any], cfg: float | None) -> int:
    if cfg is None:
        return 0
    tagged = _generation_write_targets(workflow, CFG)
    if tagged:
        return _set_numeric_on_nodes(tagged, float(cfg), ("cfg", "value"))
    if workflow_uses_selective_generation_tags(workflow):
        return 0

    count = 0
    for class_type in ("KSampler", "CFGGuider"):
        for _, node in find_nodes_by_class(workflow, class_type):
            set_input(node, "cfg", float(cfg))
            count += 1
    return count


def set_sampler(workflow: dict[str, Any], sampler_name: str | None) -> int:
    if not sampler_name:
        return 0
    tagged = _generation_write_targets(workflow, SAMPLER)
    if tagged:
        return _set_value_on_nodes(tagged, sampler_name, ("sampler_name", "value"))
    if workflow_uses_selective_generation_tags(workflow):
        return 0

    count = 0
    for class_type in ("KSampler", "KSamplerSelect"):
        for _, node in find_nodes_by_class(workflow, class_type):
            set_input(node, "sampler_name", sampler_name)
            count += 1
    return count


def set_scheduler(workflow: dict[str, Any], scheduler: str | None) -> int:
    if not scheduler:
        return 0
    tagged = _generation_write_targets(workflow, SCHEDULER)
    if tagged:
        return _set_value_on_nodes(tagged, scheduler, ("scheduler", "value"))
    if workflow_uses_selective_generation_tags(workflow):
        return 0

    count = 0
    for _, node in find_nodes_by_class(workflow, "KSampler"):
        set_input(node, "scheduler", scheduler)
        count += 1
    return count


def set_generation_settings(
    workflow: dict[str, Any],
    *,
    seed: int,
    cfg: float | None = None,
    sampler_name: str | None = None,
    scheduler: str | None = None,
    steps: int | None = None,
) -> dict[str, int]:
    return {
        "seed": set_seed(workflow, seed),
        "steps": set_steps(workflow, steps),
        "cfg": set_cfg(workflow, cfg),
        "sampler": set_sampler(workflow, sampler_name),
        "scheduler": set_scheduler(workflow, scheduler),
    }


def _read_value_from_control_nodes(
    control_nodes: list[ControlNode],
    keys: tuple[str, ...],
) -> Any | None:
    for control_node in control_nodes:
        inputs = control_node.node.get("inputs") or {}
        for key in keys:
            if key in inputs:
                return inputs[key]
    return None


def read_generation_settings_from_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    """Best-effort read of steps/cfg/sampler_name/scheduler baked into a workflow JSON."""
    out: dict[str, Any] = {}
    selective = workflow_uses_selective_generation_tags(workflow)

    ksampler_nodes = find_tagged_control_nodes(workflow, KSAMPLER)
    if ksampler_nodes:
        for key, read_keys in (
            ("steps", ("steps", "value")),
            ("cfg", ("cfg", "value")),
            ("sampler_name", ("sampler_name", "value")),
            ("scheduler", ("scheduler", "value")),
        ):
            value = _read_value_from_control_nodes(ksampler_nodes, read_keys)
            if value is not None:
                out[key] = value

    for control, key, read_keys in (
        (STEPS, "steps", ("steps", "value")),
        (CFG, "cfg", ("cfg", "value")),
        (SAMPLER, "sampler_name", ("sampler_name", "value")),
        (SCHEDULER, "scheduler", ("scheduler", "value")),
    ):
        if key in out:
            continue
        tagged = _generation_write_targets(workflow, control)
        if tagged:
            value = _read_value_from_control_nodes(tagged, read_keys)
            if value is not None:
                out[key] = value

    if selective:
        return out

    for _, node in find_nodes_by_class(workflow, "KSampler"):
        inputs = node.get("inputs") or {}
        for key in ("steps", "cfg", "sampler_name", "scheduler"):
            if key not in out and key in inputs:
                out[key] = inputs[key]
        break

    for _, node in find_nodes_by_class(workflow, "Flux2Scheduler"):
        inputs = node.get("inputs") or {}
        if "steps" not in out:
            if "steps" in inputs:
                out["steps"] = inputs["steps"]
            elif "value" in inputs:
                out["steps"] = inputs["value"]
        break

    return out


def _set_numeric_on_nodes(control_nodes: list[ControlNode], value: int | float, keys: tuple[str, ...]) -> int:
    return _set_value_on_nodes(control_nodes, value, keys)


def _set_value_on_nodes(control_nodes: list[ControlNode], value: Any, keys: tuple[str, ...]) -> int:
    count = 0
    for control_node in control_nodes:
        inputs = control_node.node.setdefault("inputs", {})
        target_key = next((key for key in keys if key in inputs), keys[0])
        inputs[target_key] = value
        count += 1
    return count


def model_input_ref(node: dict[str, Any]) -> list[Any] | None:
    """Return a node's ``model`` input as ``[source_node_id, output_index]``."""
    model_input = (node.get("inputs") or {}).get("model")
    if isinstance(model_input, list) and len(model_input) == 2:
        return [str(model_input[0]), model_input[1]]
    return None


def clip_input_ref(node: dict[str, Any]) -> list[Any] | None:
    """Return a node's ``clip`` input as ``[source_node_id, output_index]``."""
    clip_input = (node.get("inputs") or {}).get("clip")
    if isinstance(clip_input, list) and len(clip_input) >= 2:
        return [str(clip_input[0]), clip_input[1]]
    return None


def workflow_has_clip_consumer_of(workflow: dict[str, Any], node_id: str) -> bool:
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node_class(node) not in CLIP_ENCODE_CLASSES:
            continue
        ref = clip_input_ref(node)
        if ref and str(ref[0]) == str(node_id):
            return True
    return False


def trace_clip_source(marker_node: dict[str, Any]) -> list[Any] | None:
    """CLIP upstream of a LoRA marker (typically checkpoint slot 1)."""
    return clip_input_ref(marker_node)


def infer_clip_source_from_model_base(marker_node: dict[str, Any]) -> list[Any] | None:
    """When encoders use the marker for CLIP but it has no ``clip`` input, assume checkpoint CLIP."""
    model_ref = model_input_ref(marker_node)
    if model_ref:
        return [model_ref[0], 1]
    return None


def detect_lora_clip_support(workflow: dict[str, Any], marker: ControlNode) -> bool:
    """True when this marker participates in a CLIP path (dual LoRA injection)."""
    if trace_clip_source(marker.node) is not None:
        return True
    return workflow_has_clip_consumer_of(workflow, marker.node_id)


def rewire_clip_consumers(
    workflow: dict[str, Any],
    source_nid: str,
    new_clip_ref: list[Any],
) -> int:
    """Point ``clip`` inputs on encode nodes that referenced ``source_nid`` at ``new_clip_ref``."""
    count = 0
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node_title(node).startswith("Injected_"):
            continue
        if node_class(node) not in CLIP_ENCODE_CLASSES:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        clip_input = inputs.get("clip")
        if (
            isinstance(clip_input, list)
            and len(clip_input) >= 2
            and str(clip_input[0]) == str(source_nid)
        ):
            inputs["clip"] = list(new_clip_ref)
            count += 1
    return count


def bypass_marker_clip_to_source(workflow: dict[str, Any], marker: ControlNode) -> None:
    """Rewire CLIP encoders off a bypassed marker onto passthrough or upstream CLIP."""
    if workflow_has_clip_consumer_of(workflow, marker.node_id):
        rewire_clip_consumers(workflow, marker.node_id, [marker.node_id, 1])
        return
    clip_src = trace_clip_source(marker.node)
    if not clip_src:
        clip_src = infer_clip_source_from_model_base(marker.node)
    if clip_src:
        rewire_clip_consumers(workflow, marker.node_id, clip_src)


def new_node_id(workflow: dict[str, Any]) -> str:
    numeric = [int(k) for k in workflow.keys() if isinstance(k, str) and k.isdigit()]
    return str(max(numeric) + 1) if numeric else "1000000"


NATIVE_LORA_MARKER_CLASSES = frozenset({"LoraLoader", "LoraLoaderModelOnly"})


def is_native_lora_marker(node: dict[str, Any]) -> bool:
    return node_class(node) in NATIVE_LORA_MARKER_CLASSES


def bypass_workflow_node(node: dict[str, Any]) -> None:
    """Pass inputs through without running node logic (inline LoRA marker)."""
    node["mode"] = COMFY_NODE_MODE_BYPASS


def mute_workflow_node(node: dict[str, Any]) -> None:
    """Exclude node from execution (native LoRA markers — avoids empty ``lora_name`` validation)."""
    node["mode"] = COMFY_NODE_MODE_NEVER


def clear_lora_marker_contents(node: dict[str, Any]) -> None:
    """Strip baked LoRA settings from rgthree markers so bypass is a clean passthrough.

    ComfyUI bypass on ``Power Lora Loader (rgthree)`` still leaves widget slot values in
    the graph; those must be cleared or baked loras leak into the injected chain.

    Native ``LoraLoader`` markers are muted instead; do not clear ``lora_name`` here because
    an empty name fails ComfyUI ``/prompt`` validation even when bypassed.
    """
    if node_class(node) != "Power Lora Loader (rgthree)":
        return
    inputs = node.setdefault("inputs", {})
    for value in inputs.values():
        if isinstance(value, dict) and {"on", "lora", "strength"} <= set(value.keys()):
            value["on"] = False
            value["lora"] = ""
            value["strength"] = 0.0
    if "➕ Add Lora" in inputs:
        inputs["➕ Add Lora"] = ""


def _is_comfy_prompt_node(value: Any) -> bool:
    """True for a ComfyUI API prompt node entry (incl. subgraph ids like ``110:78``)."""
    return isinstance(value, dict) and bool(value.get("class_type"))


def strip_prompt_metadata(workflow: dict[str, Any]) -> None:
    """Remove non-node keys (e.g. ``_is_flux2``) before posting to ComfyUI ``/prompt``."""
    for key in list(workflow.keys()):
        if not _is_comfy_prompt_node(workflow.get(key)):
            del workflow[key]


def rewire_model_consumers(
    workflow: dict[str, Any],
    source_nid: str,
    new_model_ref: list[Any],
) -> int:
    """Point every ``model`` input that referenced ``source_nid`` at ``new_model_ref``."""
    count = 0
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node_title(node).startswith("Injected_"):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        model_input = inputs.get("model")
        if (
            isinstance(model_input, list)
            and len(model_input) >= 2
            and str(model_input[0]) == str(source_nid)
        ):
            inputs["model"] = list(new_model_ref)
            count += 1
    return count


def inject_loras(workflow: dict[str, Any], lora_list: list["LoraSpec"] | list | None) -> int:
    """Apply project LoRAs for tagged/legacy LoRA marker nodes.

    rgthree markers: **Bypass** (inline); inject chain attaches to ``[marker, 0]`` / ``[marker, 1]``.
    Native ``LoraLoader`` / ``LoraLoaderModelOnly``: **Never** (muted); inject chain attaches to
    the marker's upstream model/CLIP so ``lora_name`` is never cleared to ``""``.
    """
    markers = find_control_nodes(workflow, LORA)
    if not markers:
        return 0

    specs: list[LoraSpec] = []
    for entry in lora_list or []:
        spec = _coerce_lora_spec(entry)
        if spec:
            specs.append(spec)

    injected = 0
    loras_reversed = list(reversed(specs))

    for marker in markers:
        base_ref = model_input_ref(marker.node)
        if not base_ref:
            print(f"[LORA] WARNING: marker {marker.node_id} ({marker.title}) has no model input; skipped")
            continue

        clip_support = detect_lora_clip_support(workflow, marker)
        base_nid = str(base_ref[0])
        print(
            f"[LORA] clip_support={clip_support} base={base_nid} "
            f"marker={marker.node_id} ({marker.title})"
        )

        if is_native_lora_marker(marker.node):
            mute_workflow_node(marker.node)
            current_model_source = list(base_ref)
            current_clip_source: list[Any] | None = None
            if clip_support:
                current_clip_source = (
                    trace_clip_source(marker.node)
                    or infer_clip_source_from_model_base(marker.node)
                )
        else:
            clear_lora_marker_contents(marker.node)
            bypass_workflow_node(marker.node)
            current_model_source = [marker.node_id, 0]
            current_clip_source = [marker.node_id, 1] if clip_support else None

        for spec in loras_reversed:
            lora_name_clean = spec.name
            if not lora_name_clean:
                continue
            new_lora_nid = new_node_id(workflow)
            if clip_support and current_clip_source:
                workflow[new_lora_nid] = {
                    "inputs": {
                        "lora_name": lora_name_clean,
                        "strength_model": spec.strength,
                        "strength_clip": spec.secondary_strength(),
                        "model": list(current_model_source),
                        "clip": list(current_clip_source),
                    },
                    "class_type": "LoraLoader",
                    "_meta": {"title": f"Injected_{lora_name_clean}"},
                }
                current_model_source = [new_lora_nid, 0]
                current_clip_source = [new_lora_nid, 1]
            else:
                workflow[new_lora_nid] = {
                    "inputs": {
                        "lora_name": lora_name_clean,
                        "strength_model": spec.strength,
                        "model": list(current_model_source),
                    },
                    "class_type": "LoraLoaderModelOnly",
                    "_meta": {"title": f"Injected_{lora_name_clean}"},
                }
                current_model_source = [new_lora_nid, 0]
            injected += 1

        rewire_model_consumers(workflow, marker.node_id, current_model_source)
        if clip_support and current_clip_source:
            rewire_clip_consumers(workflow, marker.node_id, current_clip_source)
        else:
            bypass_marker_clip_to_source(workflow, marker)

    return injected


ImageSizeStatus = Literal["full", "partial", "none"]
GenerationFieldStatus = Literal["confirmed", "not_controlled"]


@dataclass
class ControlDiscovery:
    """Runtime-equivalent control discovery (image size or single generation field)."""

    status: str
    mechanisms: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GenerationSettingsDiscovery:
    fields: dict[str, ControlDiscovery] = field(default_factory=dict)

    def confirmed_fields(self) -> list[str]:
        return [name for name, disc in self.fields.items() if disc.status == "confirmed"]

    def not_controlled_fields(self) -> list[str]:
        return [name for name, disc in self.fields.items() if disc.status == "not_controlled"]


def _discovery_nodes_from_control_nodes(control_nodes: list[ControlNode]) -> list[dict[str, Any]]:
    return [_control_node_summary(node) for node in control_nodes]


def _append_mechanism(
    mechanisms: list[dict[str, Any]],
    kind: str,
    *,
    nodes: list[dict[str, Any]] | None = None,
    class_type: str | None = None,
) -> None:
    entry: dict[str, Any] = {"kind": kind}
    if class_type:
        entry["class_type"] = class_type
    if nodes:
        entry["nodes"] = nodes
    mechanisms.append(entry)


def discover_image_size_control(workflow: dict[str, Any]) -> ControlDiscovery:
    """Mirror set_dimensions targets: full = both width and height driven."""
    mechanisms: list[dict[str, Any]] = []
    has_width = False
    has_height = False

    if find_tagged_control_nodes(workflow, IMAGE_SIZE):
        _append_mechanism(
            mechanisms,
            "tag",
            nodes=_discovery_nodes_from_control_nodes(find_tagged_control_nodes(workflow, IMAGE_SIZE)),
        )
        has_width = has_height = True

    if find_tagged_control_nodes(workflow, WIDTH) or find_legacy_control_nodes(workflow, WIDTH):
        _append_mechanism(
            mechanisms,
            "tag" if find_tagged_control_nodes(workflow, WIDTH) else "legacy_title",
            nodes=_discovery_nodes_from_control_nodes(
                find_tagged_control_nodes(workflow, WIDTH) or find_legacy_control_nodes(workflow, WIDTH)
            ),
        )
        has_width = True

    if find_tagged_control_nodes(workflow, HEIGHT) or find_legacy_control_nodes(workflow, HEIGHT):
        _append_mechanism(
            mechanisms,
            "tag" if find_tagged_control_nodes(workflow, HEIGHT) else "legacy_title",
            nodes=_discovery_nodes_from_control_nodes(
                find_tagged_control_nodes(workflow, HEIGHT) or find_legacy_control_nodes(workflow, HEIGHT)
            ),
        )
        has_height = True

    for class_type in DIMENSION_CLASSES:
        nodes = find_nodes_by_class(workflow, class_type)
        if not nodes:
            continue
        _append_mechanism(
            mechanisms,
            "legacy_class",
            class_type=class_type,
            nodes=[
                {
                    "node_id": str(node_id),
                    "title": node_title(node),
                    "class_type": class_type,
                    "source": "legacy_class",
                }
                for node_id, node in nodes
            ],
        )
        has_width = has_height = True

    for node_id, node in find_nodes_by_class(workflow, "InpaintCropImproved"):
        inputs = node.get("inputs") or {}
        if "output_target_width" in inputs and "output_target_height" in inputs:
            _append_mechanism(
                mechanisms,
                "legacy_class",
                class_type="InpaintCropImproved",
                nodes=[
                    {
                        "node_id": str(node_id),
                        "title": node_title(node),
                        "class_type": "InpaintCropImproved",
                        "source": "legacy_class",
                    }
                ],
            )
            has_width = has_height = True

    if has_width and has_height:
        status: ImageSizeStatus = "full"
    elif has_width or has_height:
        status = "partial"
    else:
        status = "none"
    return ControlDiscovery(status=status, mechanisms=mechanisms)


def _discover_generation_field(
    workflow: dict[str, Any],
    control: str,
    class_rules: list[tuple[str, str | None]],
) -> ControlDiscovery:
    """Discover one generation field using the same targets as set_seed/set_steps/etc."""
    mechanisms: list[dict[str, Any]] = []
    tagged = _generation_write_targets(workflow, control)
    if tagged:
        _append_mechanism(mechanisms, "tag", nodes=_discovery_nodes_from_control_nodes(tagged))

    if workflow_uses_selective_generation_tags(workflow):
        status: GenerationFieldStatus = "confirmed" if mechanisms else "not_controlled"
        return ControlDiscovery(status=status, mechanisms=mechanisms)

    for class_type, input_key in class_rules:
        for node_id, node in find_nodes_by_class(workflow, class_type):
            inputs = node.get("inputs") or {}
            if input_key is not None and input_key not in inputs:
                continue
            _append_mechanism(
                mechanisms,
                "legacy_class",
                class_type=class_type,
                nodes=[
                    {
                        "node_id": str(node_id),
                        "title": node_title(node),
                        "class_type": class_type,
                        "source": "legacy_class",
                    }
                ],
            )

    status: GenerationFieldStatus = "confirmed" if mechanisms else "not_controlled"
    return ControlDiscovery(status=status, mechanisms=mechanisms)


def discover_generation_settings_control(workflow: dict[str, Any]) -> GenerationSettingsDiscovery:
    fields = {
        SEED: _discover_generation_field(
            workflow,
            SEED,
            [("KSampler", "seed"), ("RandomNoise", "noise_seed")],
        ),
        STEPS: _discover_generation_field(
            workflow,
            STEPS,
            [("KSampler", "steps"), ("Flux2Scheduler", None)],
        ),
        CFG: _discover_generation_field(
            workflow,
            CFG,
            [("KSampler", "cfg"), ("CFGGuider", "cfg")],
        ),
        SAMPLER: _discover_generation_field(
            workflow,
            SAMPLER,
            [("KSampler", "sampler_name"), ("KSamplerSelect", "sampler_name")],
        ),
        SCHEDULER: _discover_generation_field(
            workflow,
            SCHEDULER,
            [("KSampler", "scheduler")],
        ),
    }
    if find_tagged_control_nodes(workflow, KSAMPLER):
        fields[KSAMPLER] = _discover_generation_field(workflow, KSAMPLER, [])
    return GenerationSettingsDiscovery(fields=fields)


# 2-character (pose_2CHAR) node titles — must match run_images.py set_text_on_titles / inject_loras.
TWO_CHAR_SLOT_TITLES: dict[str, list[str]] = {
    "lora_left": ["LeftLora"],
    "lora_right": ["RightLora"],
    "prompt_left": ["LeftPrompt"],
    "prompt_right": ["RightPrompt"],
    "prompt_heal": ["HealPosPrompt"],
    "neg_left": ["LeftNegPrompt"],
    "neg_right": ["RightNegPrompt"],
    "neg_heal": ["HealNegPrompt"],
}

_TWO_CHAR_TAG_SLOT_HINTS: dict[str, tuple[str, str | None]] = {
    "lora_left": (LORA, "left"),
    "lora_right": (LORA, "right"),
    "prompt_left": (PROMPT, "left"),
    "prompt_right": (PROMPT, "right"),
    "prompt_heal": (PROMPT, "heal"),
    "neg_left": (NEGATIVE_PROMPT, "left"),
    "neg_right": (NEGATIVE_PROMPT, "right"),
    "neg_heal": (NEGATIVE_PROMPT, "heal"),
}


@dataclass
class TwoCharSlot:
    present: bool = False
    titles: list[str] = field(default_factory=list)
    source: str = "legacy"


@dataclass
class TwoCharDiscovery:
    """Role-specific controls for pose_2CHAR-style workflows."""

    active: bool = False
    slots: dict[str, TwoCharSlot] = field(default_factory=dict)
    has_heal_pass: bool = False

    def present_slot_count(self, prefix: str) -> int:
        return sum(
            1
            for key, slot in self.slots.items()
            if key.startswith(prefix) and slot.present
        )


def _two_char_slot_from_tagged(workflow: dict[str, Any], control: str, slot_hint: str | None) -> TwoCharSlot | None:
    for control_node in find_tagged_control_nodes(workflow, control):
        node_slot = (control_node.slot or "").lower()
        if slot_hint is None:
            if not node_slot:
                return TwoCharSlot(
                    present=True,
                    titles=[control_node.title],
                    source="tag",
                )
        elif node_slot == slot_hint.lower():
            return TwoCharSlot(
                present=True,
                titles=[control_node.title],
                source="tag",
            )
        else:
            for tag in CONTROL_TAGS.get(control, []):
                if slot_hint and control_node.title.lower() == f"{tag.lower()}-{slot_hint.lower()}":
                    return TwoCharSlot(
                        present=True,
                        titles=[control_node.title],
                        source="tag",
                    )
    return None


def _two_char_slot_from_legacy(workflow: dict[str, Any], titles: list[str]) -> TwoCharSlot:
    found: list[str] = []
    for title in titles:
        if find_nodes_by_title(workflow, title):
            found.append(title)
    if found:
        return TwoCharSlot(present=True, titles=found, source="legacy")
    return TwoCharSlot(present=False, titles=[], source="legacy")


def two_char_pipeline_is_active(slots: dict[str, TwoCharSlot]) -> bool:
    """True only for pose_2CHAR-style graphs, not legacy single-char ``Left*`` naming."""

    def has(key: str) -> bool:
        slot = slots.get(key)
        return bool(slot and slot.present)

    if has("lora_left") and has("lora_right"):
        return True
    if has("prompt_right") or has("prompt_heal"):
        return True
    if has("neg_right") or has("neg_heal"):
        return True
    return False


def discover_two_char_pipeline(workflow: dict[str, Any]) -> TwoCharDiscovery:
    """Detect left/right/heal role nodes used by the 2CHAR image runner."""
    slots: dict[str, TwoCharSlot] = {}
    for slot_key, legacy_titles in TWO_CHAR_SLOT_TITLES.items():
        tagged_hint = _TWO_CHAR_TAG_SLOT_HINTS.get(slot_key)
        slot: TwoCharSlot | None = None
        if tagged_hint:
            slot = _two_char_slot_from_tagged(workflow, tagged_hint[0], tagged_hint[1])
        if not slot or not slot.present:
            slot = _two_char_slot_from_legacy(workflow, legacy_titles)
        slots[slot_key] = slot

    active = two_char_pipeline_is_active(slots)
    heal_pos = slots.get("prompt_heal")
    heal_neg = slots.get("neg_heal")
    has_heal_pass = bool(
        (heal_pos and heal_pos.present) or (heal_neg and heal_neg.present)
    )
    return TwoCharDiscovery(active=active, slots=slots, has_heal_pass=has_heal_pass)


# --- Video workflow controls ---


def _write_first_matching_input(node: dict[str, Any], keys: tuple[str, ...], value: Any) -> bool:
    inputs = node.setdefault("inputs", {})
    for key in keys:
        if key in inputs:
            inputs[key] = value
            return True
    if keys:
        inputs[keys[0]] = value
        return True
    return False


def set_video_generator(
    workflow: dict[str, Any],
    width: int | None = None,
    height: int | None = None,
    length: int | None = None,
) -> int:
    """Write width/height/length on ``THM-VideoGenerator`` (or legacy generator title)."""
    nodes = find_control_nodes(workflow, VIDEO_GENERATOR)
    if not nodes:
        return 0
    count = 0
    for control_node in nodes:
        node = control_node.node
        if width is not None:
            if _write_first_matching_input(node, VIDEO_GENERATOR_WIDTH_KEYS, int(width)):
                count += 1
        if height is not None:
            if _write_first_matching_input(node, VIDEO_GENERATOR_HEIGHT_KEYS, int(height)):
                count += 1
        if length is not None:
            if _write_first_matching_input(node, VIDEO_GENERATOR_LENGTH_KEYS, int(length)):
                count += 1
    return count


def set_frame_count(workflow: dict[str, Any], frames: int) -> int:
    tagged = find_control_nodes(workflow, FRAME_COUNT)
    if tagged:
        return _set_numeric_on_nodes(tagged, int(frames), ("value", "int", "number"))
    if find_control_nodes(workflow, VIDEO_GENERATOR):
        return set_video_generator(workflow, length=int(frames))
    return 0


def set_frame_rate(workflow: dict[str, Any], fps: float) -> int:
    tagged = find_control_nodes(workflow, FRAME_RATE)
    if tagged:
        return _set_numeric_on_nodes(tagged, float(fps), ("value", "float", "fps"))
    return 0


def set_save_video_prefix(workflow: dict[str, Any], filename_prefix: str) -> int:
    count = 0
    for control_node in find_control_nodes(workflow, SAVE_VIDEO):
        set_input(control_node.node, "filename_prefix", filename_prefix)
        count += 1
    if count:
        return count
    for class_type in VIDEO_SAVE_CLASSES:
        for _, node in find_nodes_by_class(workflow, class_type):
            if set_input(node, "filename_prefix", filename_prefix):
                count += 1
    return count


def _write_load_image_path(node: dict[str, Any], path_str: str) -> None:
    import time

    for key in ("image", "image_path", "file", "filename"):
        if key in node.get("inputs", {}) or key == "image":
            set_input(node, key, path_str)
    set_input(node, "_cache_buster", time.time())


def _clear_load_image(node: dict[str, Any]) -> None:
    set_input(node, "image", "")
    for key in ("image_path", "file", "filename"):
        if key in node.get("inputs", {}):
            set_input(node, key, "")


def _wire_generator_frame_inputs(
    gen_node: dict[str, Any],
    keys: tuple[str, ...],
    loader_nid: str,
    *,
    out_index: int = 0,
) -> int:
    inputs = gen_node.setdefault("inputs", {})
    ref = [str(loader_nid), out_index]
    wired_keys = [key for key in keys if key in inputs] or [keys[0]]
    for key in wired_keys:
        inputs[key] = ref
    return len(wired_keys)


def _disconnect_generator_frame_inputs(gen_node: dict[str, Any], keys: tuple[str, ...]) -> int:
    inputs = gen_node.get("inputs", {})
    count = 0
    for key in keys:
        if key in inputs:
            del inputs[key]
            count += 1
    return count


def configure_video_frames(
    workflow: dict[str, Any],
    clip_type: VideoFrameClipType,
    start_path: str | None = None,
    end_path: str | None = None,
) -> int:
    """Wire/clear tagged frame loaders for closed (SE) or open-ended (SO/OE) clips."""
    use_start = clip_type in ("SE", "SO")
    use_end = clip_type in ("SE", "OE")
    start_loaders = find_control_nodes(workflow, VIDEO_START_FRAME)
    end_loaders = find_control_nodes(workflow, VIDEO_END_FRAME)
    gen_nodes = find_control_nodes(workflow, VIDEO_GENERATOR)
    if not gen_nodes:
        return set_video_frame_loaders(
            workflow,
            start_path if use_start else None,
            end_path if use_end else None,
        )

    count = 0
    start_nid = start_loaders[0].node_id if start_loaders else None
    end_nid = end_loaders[0].node_id if end_loaders else None

    for gen in gen_nodes:
        gen_node = gen.node
        if not use_start:
            count += _disconnect_generator_frame_inputs(gen_node, VIDEO_GENERATOR_START_KEYS)
            for loader in start_loaders:
                _clear_load_image(loader.node)
                count += 1
        elif start_nid:
            count += _wire_generator_frame_inputs(gen_node, VIDEO_GENERATOR_START_KEYS, start_nid)
            if start_path:
                for loader in start_loaders:
                    _write_load_image_path(loader.node, start_path)
                    count += 1

        if not use_end:
            count += _disconnect_generator_frame_inputs(gen_node, VIDEO_GENERATOR_END_KEYS)
            for loader in end_loaders:
                _clear_load_image(loader.node)
                count += 1
        elif end_nid:
            count += _wire_generator_frame_inputs(gen_node, VIDEO_GENERATOR_END_KEYS, end_nid)
            if end_path:
                for loader in end_loaders:
                    _write_load_image_path(loader.node, end_path)
                    count += 1

    return count


def set_video_frame_loaders(
    workflow: dict[str, Any],
    start_path: str | None = None,
    end_path: str | None = None,
) -> int:
    count = 0
    if start_path:
        for control_node in find_control_nodes(workflow, VIDEO_START_FRAME):
            _write_load_image_path(control_node.node, start_path)
            count += 1
    if end_path:
        for control_node in find_control_nodes(workflow, VIDEO_END_FRAME):
            _write_load_image_path(control_node.node, end_path)
            count += 1
    return count


def _sampler_adds_noise(node: dict[str, Any]) -> bool:
    """True when a sampler pass actually injects noise (seed affects output)."""
    cls = node_class(node)
    if cls == "KSampler":
        return True
    if cls == "KSamplerAdvanced":
        add_noise = (node.get("inputs") or {}).get("add_noise")
        if add_noise is None:
            return True
        return str(add_noise).lower() == "enable"
    return False


def workflow_has_video_seed_target(workflow: dict[str, Any]) -> bool:
    """True when run_video set_video_seeds would have a writable noise entry point."""
    if find_tagged_control_nodes(workflow, SEED):
        return True
    if find_legacy_control_nodes(workflow, SEED):
        return True
    for _, node in find_nodes_by_class(workflow, "KSamplerAdvanced"):
        if _sampler_adds_noise(node):
            return True
    if find_nodes_by_class(workflow, "KSampler"):
        return True
    if find_nodes_by_class(workflow, "RandomNoise"):
        return True
    return False


def set_video_seeds(
    workflow: dict[str, Any],
    seed: int,
    *,
    seed_target_title: str | None = None,
    seed_exclude_title: str | None = None,
) -> int:
    tagged = find_tagged_control_nodes(workflow, SEED)
    if tagged:
        count = 0
        for control_node in tagged:
            if seed_exclude_title and control_node.title == seed_exclude_title:
                continue
            _set_numeric_on_nodes([control_node], int(seed), ("seed", "noise_seed", "value"))
            count += 1
        if count:
            return count

    legacy_nodes = find_legacy_control_nodes(workflow, SEED)
    if legacy_nodes:
        count = 0
        for control_node in legacy_nodes:
            if seed_exclude_title and control_node.title == seed_exclude_title:
                continue
            _set_numeric_on_nodes([control_node], int(seed), ("seed", "noise_seed", "value"))
            count += 1
        if count:
            return count

    count = 0
    for _, node in find_nodes_by_class(workflow, "KSamplerAdvanced"):
        if not _sampler_adds_noise(node):
            continue
        if set_input(node, "noise_seed", int(seed)):
            count += 1
    for _, node in find_nodes_by_class(workflow, "KSampler"):
        if not _sampler_adds_noise(node):
            continue
        if set_input(node, "seed", int(seed)):
            count += 1
    for _, node in find_nodes_by_class(workflow, "RandomNoise"):
        if set_input(node, "noise_seed", int(seed)):
            count += 1
    return count


def _load_lora_pairs_registry() -> list[dict[str, str | None]]:
    csv_path = Path(__file__).parent / "lora_pairs.csv"
    if not csv_path.exists():
        return []
    import csv

    registry: list[dict[str, str | None]] = []
    with csv_path.open("r", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            high = (row.get("high") or "").strip()
            low = (row.get("low") or "").strip()
            if high:
                registry.append({"high": high, "low": low or None})
    return registry


def resolve_video_lora_pair(name: str, registry: list[dict[str, str | None]] | None = None) -> tuple[str | None, str | None]:
    name = name.strip()
    if not name:
        return None, None
    pairs = registry if registry is not None else _load_lora_pairs_registry()
    for entry in pairs:
        if name == entry["high"] or name == entry["low"]:
            return entry["high"], entry["low"]
    stem = name.replace(".safetensors", "")
    if "_high_noise" in stem or "_low_noise" in stem:
        base = stem.replace("_high_noise", "").replace("_low_noise", "")
        return f"{base}_high_noise.safetensors", f"{base}_low_noise.safetensors"
    return name, None


def _find_model_downstream(workflow: dict[str, Any], source_nid: str) -> list[tuple[str, dict[str, Any]]]:
    results: list[tuple[str, dict[str, Any]]] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        model_input = (node.get("inputs") or {}).get("model")
        if isinstance(model_input, list) and str(model_input[0]) == str(source_nid):
            results.append((str(nid), node))
    return results


def inject_video_dual_loras(
    workflow: dict[str, Any],
    lora_list: list[LoraSpec] | list | None,
    *,
    resolve_pair=None,
) -> int:
    """Legacy WAN 14B dual-pass LoRA injection (high/low UNet chains)."""
    high_markers = find_control_nodes(workflow, LORA_HIGH)
    if not high_markers:
        return 0
    specs: list[LoraSpec] = []
    for entry in lora_list or []:
        spec = _coerce_lora_spec(entry)
        if spec:
            specs.append(spec)
    if not specs:
        return 0

    resolver = resolve_pair or resolve_video_lora_pair
    low_markers = find_control_nodes(workflow, LORA_LOW)
    low_nid = low_markers[0].node_id if low_markers else None
    high_nid = high_markers[0].node_id
    injected = 0

    def build_chain(source_ref: list[Any], noise_type: str) -> list[Any]:
        nonlocal injected
        curr = source_ref
        for spec in specs:
            strength = spec.strength if noise_type == "high" else spec.secondary_strength()
            high_file, low_file = resolver(spec.name)
            lora_file = high_file if noise_type == "high" else low_file
            if not lora_file:
                continue
            nid = new_node_id(workflow)
            workflow[nid] = {
                "inputs": {"lora_name": lora_file, "strength_model": strength, "model": curr},
                "class_type": "LoraLoaderModelOnly",
                "_meta": {"title": f"Injected_{noise_type.title()}_{lora_file}"},
            }
            curr = [nid, 0]
            injected += 1
        return curr

    for nid, node in _find_model_downstream(workflow, high_nid):
        node.setdefault("inputs", {})["model"] = build_chain([high_nid, 0], "high")
    if low_nid:
        for nid, node in _find_model_downstream(workflow, low_nid):
            node.setdefault("inputs", {})["model"] = build_chain([low_nid, 0], "low")
    return injected


def video_lora_mode(workflow: dict[str, Any]) -> Literal["none", "single", "dual"]:
    if find_control_nodes(workflow, LORA_HIGH):
        return "dual"
    if find_control_nodes(workflow, LORA):
        return "single"
    return "none"


def inject_video_loras(workflow: dict[str, Any], lora_list: list[LoraSpec] | list | None) -> int:
    mode = video_lora_mode(workflow)
    if mode == "dual":
        return inject_video_dual_loras(workflow, lora_list)
    if mode == "single":
        return inject_loras(workflow, lora_list)
    return 0


def _equal_step_shares(count: int, budget: int) -> list[int]:
    if count <= 0 or budget <= 0:
        return [0] * max(count, 0)
    base = budget // count
    remainder = budget % count
    shares = [base] * count
    shares[-1] += remainder
    return shares


def _express_chain_pass_lengths(count: int, chain_budget: int, full_shares: list[int]) -> list[int]:
    if count <= 0:
        return []
    if count == 1:
        return [chain_budget]
    if count == 2:
        return [chain_budget, 0]
    if count == 3:
        half_second = full_shares[1] // 2 if len(full_shares) > 1 else 0
        return [1, half_second, 0]
    lengths = [0] * count
    lengths[0] = 1
    for index in range(1, count - 1):
        lengths[index] = full_shares[index] // 2 if index < len(full_shares) else 0
    return lengths


def _upstream_latent_source_nid(workflow: dict[str, Any], node_id: str) -> str | None:
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs") or {}
    for key in LATENT_UPSTREAM_KEYS:
        value = inputs.get(key)
        if isinstance(value, list) and value:
            return str(value[0])
    return None


def _decode_feeder_sampler_nid(workflow: dict[str, Any]) -> str | None:
    for _, decode in find_nodes_by_class(workflow, "VAEDecode"):
        samples = (decode.get("inputs") or {}).get("samples")
        if isinstance(samples, list) and samples:
            source_id = str(samples[0])
            source = workflow.get(source_id)
            if isinstance(source, dict) and node_class(source) in SAMPLER_CHAIN_CLASSES:
                return source_id
    return None


def discover_thm_slowmo_primer(workflow: dict[str, Any]) -> ControlNode | None:
    tagged = find_tagged_control_nodes(workflow, SLOWMO_PRIMER)
    if tagged:
        return tagged[0]
    legacy = find_legacy_control_nodes(workflow, SLOWMO_PRIMER)
    return legacy[0] if legacy else None


def discover_thm_ksampler_passes(workflow: dict[str, Any]) -> list[ControlNode]:
    """Ordered THM-KSampler chain passes (execution order), via latent wiring."""
    tagged = find_tagged_control_nodes(workflow, KSAMPLER)
    for control_node in tagged:
        if control_node.slot:
            _console_print(
                f"[VIDEO] Suffixed THM-KSampler tag ({control_node.title}); use duplicate bare titles only."
            )
    tagged_ids = {node.node_id for node in tagged}
    if not tagged_ids:
        return []

    chain_ids: list[str] = []
    cursor = _decode_feeder_sampler_nid(workflow)
    visited: set[str] = set()
    while cursor and cursor not in visited:
        visited.add(cursor)
        if cursor in tagged_ids:
            chain_ids.append(cursor)
        node = workflow.get(cursor)
        if not isinstance(node, dict):
            break
        if node_class(node) not in SAMPLER_CHAIN_CLASSES:
            break
        cursor = _upstream_latent_source_nid(workflow, cursor)

    if chain_ids:
        order = list(reversed(chain_ids))
        by_id = {item.node_id: item for item in tagged}
        return [by_id[item_id] for item_id in order if item_id in by_id]

    slowmo = discover_thm_slowmo_primer(workflow)
    slowmo_id = slowmo.node_id if slowmo else None
    remaining = [node for node in tagged if node.node_id != slowmo_id]
    remaining.sort(key=lambda item: int(item.node_id) if str(item.node_id).isdigit() else 0)
    return remaining


def _write_sampler_pass_range(node: dict[str, Any], total_steps: int, start: int, end: int) -> bool:
    if end <= start:
        set_input(node, "steps", int(total_steps))
        set_input(node, "start_at_step", int(start))
        set_input(node, "end_at_step", int(start))
        return True
    wrote = False
    for key, value in (
        ("steps", int(total_steps)),
        ("start_at_step", int(start)),
        ("end_at_step", int(end)),
    ):
        if set_input(node, key, value):
            wrote = True
    return wrote


def apply_video_sampler_passes(
    workflow: dict[str, Any],
    *,
    total_steps: int,
    express: bool,
    primer_steps: int,
) -> int:
    """Split denoise steps across THM-SlowMoPrimer + ordered THM-KSampler passes."""
    total_budget = max(1, int(total_steps // 2 if express else total_steps))
    primer_steps = max(0, int(primer_steps))
    chain_nodes = discover_thm_ksampler_passes(workflow)
    primer = discover_thm_slowmo_primer(workflow)
    if not chain_nodes and not primer:
        return 0

    cursor = 0
    mutations = 0
    if primer:
        primer_end = min(primer_steps, total_budget)
        if _write_sampler_pass_range(primer.node, total_budget, 0, primer_end):
            mutations += 1
        cursor = primer_end

    chain_budget = max(0, total_budget - cursor)
    count = len(chain_nodes)
    if count and chain_budget > 0:
        full_shares = _equal_step_shares(count, chain_budget)
        lengths = (
            _express_chain_pass_lengths(count, chain_budget, full_shares)
            if express
            else full_shares
        )
        for control_node, length in zip(chain_nodes, lengths):
            start = cursor
            end = cursor + max(0, int(length))
            if _write_sampler_pass_range(control_node.node, total_budget, start, end):
                mutations += 1
            cursor = end
    elif count:
        for control_node in chain_nodes:
            if _write_sampler_pass_range(control_node.node, total_budget, cursor, cursor):
                mutations += 1

    return total_budget if mutations else 0


def discover_video_sampler_capabilities(workflow: dict[str, Any]) -> dict[str, bool]:
    return {
        "has_thm_ksampler_passes": bool(discover_thm_ksampler_passes(workflow)),
        "has_thm_slowmo_primer": bool(find_tagged_control_nodes(workflow, SLOWMO_PRIMER)),
        "has_thm_steps": bool(find_tagged_control_nodes(workflow, STEPS)),
        "has_express_samplers": bool(find_nodes_by_title(workflow, "SlowMoPrimer"))
        and bool(find_nodes_by_title(workflow, "IterKSampler"))
        and bool(find_nodes_by_title(workflow, "WanFixedSeed")),
    }


LEGACY_EXPRESS_SAMPLER_TITLES = frozenset({"SlowMoPrimer", "IterKSampler", "WanFixedSeed"})


def video_project_controls_steps(workflow: dict[str, Any]) -> bool:
    """True when run_video injects steps from project video_steps_default."""
    caps = discover_video_sampler_capabilities(workflow)
    return bool(
        caps["has_express_samplers"]
        or caps["has_thm_ksampler_passes"]
        or caps["has_thm_slowmo_primer"]
        or caps["has_thm_steps"]
    )


def discover_workflow_baked_samplers(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """Untagged generic samplers whose steps stay in the workflow JSON (tier 3)."""
    if video_project_controls_steps(workflow):
        return []

    thm_ksampler_ids = {node.node_id for node in discover_thm_ksampler_passes(workflow)}
    slowmo = discover_thm_slowmo_primer(workflow)
    injected_ids = set(thm_ksampler_ids)
    if slowmo:
        injected_ids.add(slowmo.node_id)

    baked: list[dict[str, Any]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        cls = node_class(node)
        if cls not in SAMPLER_CHAIN_CLASSES:
            continue
        title = node_title(node) or cls
        if title in LEGACY_EXPRESS_SAMPLER_TITLES:
            continue
        if node_id in injected_ids:
            continue
        if find_tagged_control_nodes(workflow, KSAMPLER) and any(
            item.node_id == node_id for item in find_tagged_control_nodes(workflow, KSAMPLER)
        ):
            continue
        if find_tagged_control_nodes(workflow, SLOWMO_PRIMER) and any(
            item.node_id == node_id for item in find_tagged_control_nodes(workflow, SLOWMO_PRIMER)
        ):
            continue
        steps = node.get("inputs", {}).get("steps")
        baked.append(
            {
                "node_id": str(node_id),
                "title": title,
                "class_type": cls,
                "steps": steps,
            }
        )
    return baked


@dataclass
class VideoFrameInputSupport:
    supports_start_frame: bool = False
    supports_end_frame: bool = False
    start_mechanisms: list[str] = field(default_factory=list)
    end_mechanisms: list[str] = field(default_factory=list)


def _generator_has_frame_input(gen_node: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    inputs = gen_node.get("inputs") or {}
    return [key for key in keys if key in inputs]


def discover_video_frame_input_support(workflow: dict[str, Any]) -> VideoFrameInputSupport:
    """Whether the workflow graph can consume start/end keyframe images at run time."""
    start_mech: list[str] = []
    end_mech: list[str] = []

    if find_control_nodes(workflow, VIDEO_START_FRAME):
        start_mech.append("THM-StartFrame")
    if find_control_nodes(workflow, VIDEO_END_FRAME):
        end_mech.append("THM-EndFrame")

    for control_node in find_control_nodes(workflow, VIDEO_GENERATOR):
        for key in _generator_has_frame_input(control_node.node, VIDEO_GENERATOR_START_KEYS):
            label = f"THM-VideoGenerator.{key}"
            if label not in start_mech:
                start_mech.append(label)
        for key in _generator_has_frame_input(control_node.node, VIDEO_GENERATOR_END_KEYS):
            label = f"THM-VideoGenerator.{key}"
            if label not in end_mech:
                end_mech.append(label)

    has_legacy_wan = bool(find_nodes_by_title(workflow, "WanFirstLastFrameToVideo"))
    if has_legacy_wan:
        if find_nodes_by_title(workflow, "StartImage"):
            if "legacy StartImage" not in start_mech:
                start_mech.append("legacy StartImage")
        if find_nodes_by_title(workflow, "EndImage"):
            if "legacy EndImage" not in end_mech:
                end_mech.append("legacy EndImage")

    return VideoFrameInputSupport(
        supports_start_frame=bool(start_mech),
        supports_end_frame=bool(end_mech),
        start_mechanisms=start_mech,
        end_mechanisms=end_mech,
    )


@dataclass
class VideoCapabilities:
    lora_mode: Literal["none", "single", "dual"] = "none"
    has_video_generator: bool = False
    has_frame_count: bool = False
    has_frame_rate: bool = False
    has_save_video: bool = False
    has_start_frame: bool = False
    has_end_frame: bool = False
    has_express_samplers: bool = False
    has_legacy_wan_generator: bool = False
    has_thm_ksampler_passes: bool = False
    has_thm_slowmo_primer: bool = False
    has_thm_steps: bool = False
    supports_start_frame: bool = False
    supports_end_frame: bool = False
    start_frame_mechanisms: list[str] = field(default_factory=list)
    end_frame_mechanisms: list[str] = field(default_factory=list)
    workflow_baked_sigma_schedules: list[dict[str, Any]] = field(default_factory=list)


def discover_workflow_baked_sigma_schedules(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """ManualSigmas / untagged schedulers not driven by THM-Steps (LTX dual-pass, etc.)."""
    thm_steps_ids = {node.node_id for node in find_tagged_control_nodes(workflow, STEPS)}
    baked: list[dict[str, Any]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        cls = node_class(node)
        title = node_title(node) or cls
        if cls == "ManualSigmas":
            sigmas = (node.get("inputs") or {}).get("sigmas")
            baked.append(
                {
                    "node_id": str(node_id),
                    "title": title,
                    "class_type": cls,
                    "sigmas": sigmas,
                }
            )
        elif cls == "LTXVScheduler" and node_id not in thm_steps_ids:
            steps = (node.get("inputs") or {}).get("steps")
            baked.append(
                {
                    "node_id": str(node_id),
                    "title": title,
                    "class_type": cls,
                    "steps": steps,
                }
            )
    return baked


def discover_video_capabilities(workflow: dict[str, Any]) -> VideoCapabilities:
    sampler_caps = discover_video_sampler_capabilities(workflow)
    frame_support = discover_video_frame_input_support(workflow)
    caps = VideoCapabilities(
        lora_mode=video_lora_mode(workflow),
        has_video_generator=bool(find_control_nodes(workflow, VIDEO_GENERATOR)),
        has_frame_count=bool(find_control_nodes(workflow, FRAME_COUNT)),
        has_frame_rate=bool(find_control_nodes(workflow, FRAME_RATE)),
        has_save_video=bool(find_control_nodes(workflow, SAVE_VIDEO))
        or bool(find_nodes_by_class(workflow, "SaveVideo"))
        or bool(find_nodes_by_class(workflow, "VHS_VideoCombine")),
        has_start_frame=bool(find_control_nodes(workflow, VIDEO_START_FRAME)),
        has_end_frame=bool(find_control_nodes(workflow, VIDEO_END_FRAME)),
        has_express_samplers=sampler_caps["has_express_samplers"],
        has_legacy_wan_generator=bool(find_nodes_by_title(workflow, "WanFirstLastFrameToVideo")),
        has_thm_ksampler_passes=sampler_caps["has_thm_ksampler_passes"],
        has_thm_slowmo_primer=sampler_caps["has_thm_slowmo_primer"],
        has_thm_steps=sampler_caps["has_thm_steps"],
        supports_start_frame=frame_support.supports_start_frame,
        supports_end_frame=frame_support.supports_end_frame,
        start_frame_mechanisms=list(frame_support.start_mechanisms),
        end_frame_mechanisms=list(frame_support.end_mechanisms),
        workflow_baked_sigma_schedules=discover_workflow_baked_sigma_schedules(workflow),
    )
    return caps


@dataclass
class VideoInjectionContext:
    positive_prompt: str = ""
    negative_prompt: str = ""
    seed: int = 0
    fps: float = 16.0
    frame_count: int = 0
    width: int | None = None
    height: int | None = None
    save_video_prefix: str = ""
    start_frame_path: str | None = None
    end_frame_path: str | None = None
    frame_clip_type: VideoFrameClipType | None = None
    lora_specs: list[LoraSpec] | None = None
    seed_target_title: str | None = None
    seed_exclude_title: str | None = None


def apply_video_injection(workflow: dict[str, Any], ctx: VideoInjectionContext) -> VideoCapabilities:
    """Apply tagged/legacy video controls; return capability scan for logging."""
    caps = discover_video_capabilities(workflow)
    if ctx.positive_prompt:
        set_prompt(workflow, ctx.positive_prompt)
    if ctx.negative_prompt is not None:
        set_negative_prompt(workflow, ctx.negative_prompt)
    if ctx.fps:
        set_frame_rate(workflow, float(ctx.fps))
    if ctx.frame_count:
        set_frame_count(workflow, int(ctx.frame_count))
    if ctx.width is not None and ctx.height is not None:
        set_video_generator(workflow, width=int(ctx.width), height=int(ctx.height))
        set_dimensions(workflow, int(ctx.width), int(ctx.height))
    if ctx.save_video_prefix:
        set_save_video_prefix(workflow, ctx.save_video_prefix)
    has_tagged_frames = bool(find_control_nodes(workflow, VIDEO_START_FRAME)) or bool(
        find_control_nodes(workflow, VIDEO_END_FRAME)
    )
    if ctx.frame_clip_type and has_tagged_frames:
        configure_video_frames(
            workflow,
            ctx.frame_clip_type,
            ctx.start_frame_path,
            ctx.end_frame_path,
        )
    elif ctx.start_frame_path or ctx.end_frame_path:
        set_video_frame_loaders(workflow, ctx.start_frame_path, ctx.end_frame_path)
    if ctx.lora_specs:
        inject_video_loras(workflow, ctx.lora_specs)
    set_video_seeds(
        workflow,
        int(ctx.seed),
        seed_target_title=ctx.seed_target_title,
        seed_exclude_title=ctx.seed_exclude_title,
    )
    return caps
