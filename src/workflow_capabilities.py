"""Workflow capability scan for THM UI (Phase 3)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from helpers import DEFAULT_VIDEO_WORKFLOW_FILENAME, WORKFLOWS_DIR, effective_video_workflow_filename, is_custom_image_family

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import workflow_controls as wc  # noqa: E402

# Controls exposed as boolean flags for Phase 4 UI gating.
CAPABILITY_KEYS = (
    "prompt",
    "negative_prompt",
    "checkpoint",
    "lora",
    "pose_reference",
    "image_reference",
    "save_image",
    "seed",
    "steps",
    "cfg",
    "sampler",
    "scheduler",
    "width",
    "height",
    "image_size",
)

# Shown in capabilities markdown instead of internal keys.
CONTROL_DISPLAY_LABELS: dict[str, str] = {
    "prompt": "prompt",
    "negative_prompt": "negative prompt",
    "checkpoint": "checkpoint",
    "lora": "lora",
    "pose_reference": "pose reference",
    "image_reference": "image reference",
    "save_image": "save image",
}

# Listed in grouped discovery sections, not as flat keys or in "Not in workflow".
GROUPED_DISCOVERY_KEYS = frozenset(
    {"width", "height", "image_size", "seed", "steps", "cfg", "sampler", "scheduler"}
)

_GENERATION_FIELD_LABELS = {
    wc.SEED: "seed",
    wc.STEPS: "steps",
    wc.CFG: "cfg",
    wc.SAMPLER: "sampler",
    wc.SCHEDULER: "scheduler",
}

_KSAMPLER_BUNDLE_HINT = " (also `THM-KSampler` bundle nodes)"

_MULTI_CHAR_PROMPT_TITLES = frozenset(
    {"LeftPrompt", "RightPrompt", "HealPosPrompt", "THM-Prompt-Left", "THM-Prompt-Right"}
)

_MAIN_OR_LEFT_NEGATIVE_TITLES = frozenset(
    {"MainNegPrompt", "NegPrompt", "THM-NegativePrompt", "LeftNegPrompt"}
)


@dataclass
class KeyframeEditorVisibility:
    """Which keyframe Properties fields to show for the selected workflow."""

    show_pose_group: bool = True
    show_pose_library: bool = True
    show_pose_cn_controls: bool = True
    show_char_left: bool = True
    show_char_right: bool = False
    show_reference_slots_group: bool = False
    show_prompt: bool = True
    show_advanced: bool = True
    show_inject_lora: bool = False
    show_neg_left: bool = False
    show_neg_right: bool = False
    show_neg_heal: bool = False


@dataclass
class ProjectNegativeVisibility:
    """Project tab negative fields (Look Development) gated by workflow scan."""

    show_keyframes_all: bool = True
    show_heal_all: bool = False


@dataclass
class VideoGenerationDefaultsVisibility:
    """Generation Defaults video column fields driven by video workflow scan."""

    show_video_steps: bool = False
    show_video_fps: bool = False
    video_steps_info: str = (
        "Total denoise steps for THM-KSampler chain, THM-Steps scheduler "
        "(e.g. LTXVScheduler), or legacy SlowMoPrimer / IterKSampler / WanFixedSeed triple only."
    )
    video_fps_info: str = (
        "Injected into THM-FrameRate / THM-FPS (wired to SaveVideo frame_rate)."
    )


@dataclass
class WorkflowCapabilities:
    """Structured result of scanning a ComfyUI workflow JSON."""

    workflow_path: str | None = None
    controls: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    has: dict[str, bool] = field(default_factory=dict)
    lora_clip_support: bool = False
    has_pose_control: bool = False
    is_multi_char_prompt: bool = False
    two_char_pipeline: wc.TwoCharDiscovery | None = None
    unknown: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    image_size_control: wc.ControlDiscovery | None = None
    generation_settings: wc.GenerationSettingsDiscovery | None = None
    reference_wiring: wc.ReferenceWiringDiscovery | None = None
    image_reference_slots: list[wc.ImageReferenceSlot] = field(default_factory=list)
    has_video_seed: bool = False

    @property
    def has_driven_image_size(self) -> bool:
        return bool(self.image_size_control and self.image_size_control.status == "full")

    def has_confirmed_generation_field(self, name: str) -> bool:
        if not self.generation_settings:
            return False
        disc = self.generation_settings.fields.get(name)
        return bool(disc and disc.status == "confirmed")

    def negative_prompt_titles(self) -> set[str]:
        return {str(n.get("title") or "") for n in (self.controls.get("negative_prompt") or [])}

    def has_secondary_image_reference(self) -> bool:
        """True when multiple image-reference slots exist (future multi-ref workflows)."""
        return len(self.controls.get("image_reference") or []) >= 2

    def has_secondary_character_reference(self) -> bool:
        """True when workflow exposes multiple ``THM-ImageReference`` slots."""
        return len(self.image_reference_slots or []) >= 2

    def is_image_reference_only(self) -> bool:
        return (
            bool(self.has.get("image_reference"))
            and not bool(self.has.get("pose_reference"))
            and not self.has_pose_control
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_path": self.workflow_path,
            "controls": self.controls,
            "has": self.has,
            "lora_clip_support": self.lora_clip_support,
            "has_pose_control": self.has_pose_control,
            "is_multi_char_prompt": self.is_multi_char_prompt,
            "two_char_pipeline": (
                {
                    "active": self.two_char_pipeline.active,
                    "has_heal_pass": self.two_char_pipeline.has_heal_pass,
                    "slots": {
                        name: {
                            "present": slot.present,
                            "titles": slot.titles,
                            "source": slot.source,
                        }
                        for name, slot in self.two_char_pipeline.slots.items()
                    },
                }
                if self.two_char_pipeline
                else None
            ),
            "unknown": self.unknown,
            "error": self.error,
            "image_size_control": (
                {"status": self.image_size_control.status, "mechanisms": self.image_size_control.mechanisms}
                if self.image_size_control
                else None
            ),
            "generation_settings": (
                {
                    name: {"status": disc.status, "mechanisms": disc.mechanisms}
                    for name, disc in self.generation_settings.fields.items()
                }
                if self.generation_settings
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> WorkflowCapabilities | None:
        if not data or not isinstance(data, dict):
            return None
        return cls(
            workflow_path=data.get("workflow_path"),
            controls=dict(data.get("controls") or {}),
            has=dict(data.get("has") or {}),
            lora_clip_support=bool(data.get("lora_clip_support")),
            has_pose_control=bool(data.get("has_pose_control")),
            is_multi_char_prompt=bool(data.get("is_multi_char_prompt")),
            unknown=list(data.get("unknown") or []),
            error=data.get("error"),
        )


@dataclass
class RuntimeInjectionFlags:
    """Which run-time injections are safe for a scanned workflow."""

    use_prompt: bool = False
    use_two_char: bool = False
    use_single_negative: bool = False
    use_lora: bool = False
    use_pose_cn: bool = False


def runtime_injection_flags(caps: WorkflowCapabilities | None) -> RuntimeInjectionFlags:
    """Derive run_images injection gates from a capability scan (matches editor visibility)."""
    if not caps or caps.error:
        return RuntimeInjectionFlags()
    two_char = bool(caps.two_char_pipeline and caps.two_char_pipeline.active)
    return RuntimeInjectionFlags(
        use_prompt=bool(caps.has.get("prompt")),
        use_two_char=two_char,
        use_single_negative=bool(caps.has.get("negative_prompt")) and not two_char,
        use_lora=bool(caps.has.get("lora")),
        use_pose_cn=caps.has_pose_control,
    )


def resolve_workflow_path(workflow_name: str | None, workflows_dir: Path | None = None) -> Path | None:
    """Resolve a dropdown filename or path to an absolute workflow JSON path."""
    name = str(workflow_name or "").strip()
    if not name:
        return None
    base = workflows_dir or WORKFLOWS_DIR
    candidate = Path(name)
    if candidate.is_file():
        return candidate.resolve()
    joined = base / name
    if joined.is_file():
        return joined.resolve()
    if not name.endswith(".json"):
        with_json = base / f"{name}.json"
        if with_json.is_file():
            return with_json.resolve()
    return None


def load_workflow_dict(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Workflow root must be an object: {path}")
    wc.strip_prompt_metadata(data)
    return data


def sampler_settings_from_workflow_path(workflow_path: str | Path) -> dict[str, Any]:
    """Read steps/cfg/sampler_name/scheduler from a workflow file (Custom mode display / metadata)."""
    path = Path(workflow_path)
    if not path.is_file():
        return {}
    try:
        return wc.read_generation_settings_from_workflow(load_workflow_dict(path))
    except Exception:
        return {}


def video_workflow_name_from_project(project_dict: Any) -> str:
    """Effective in-between video workflow basename from loaded project JSON."""
    if isinstance(project_dict, str):
        try:
            project_dict = json.loads(project_dict)
        except Exception:
            return DEFAULT_VIDEO_WORKFLOW_FILENAME
    if not isinstance(project_dict, dict):
        return DEFAULT_VIDEO_WORKFLOW_FILENAME
    return effective_video_workflow_filename(project_dict)


def generations_seed_visible(
    caps: WorkflowCapabilities | None,
    features: dict[str, Any] | None = None,
    *,
    video: bool = False,
    video_scan: "VideoWorkflowScan | None" = None,
) -> bool:
    """Show Generations seed when feature-flagged and the workflow exposes a seed target."""
    if not (features or {}).get("show_generation_info", False):
        return False
    if video:
        if video_scan is not None:
            return bool(not video_scan.error and video_scan.has_video_seed)
        if not caps or caps.error:
            return False
        return bool(caps.has_video_seed)
    if not caps or caps.error:
        return False
    if caps.has_confirmed_generation_field("seed"):
        return True
    return False


def keyframe_editor_visibility(
    caps: WorkflowCapabilities | None,
    *,
    default_image_family: bool = False,
    custom_image_family: bool = False,
) -> KeyframeEditorVisibility:
    """Derive keyframe Properties visibility from a workflow capability scan."""
    if not caps or caps.error:
        if default_image_family:
            return KeyframeEditorVisibility(show_pose_group=True, show_pose_library=True)
        if custom_image_family:
            return KeyframeEditorVisibility(show_pose_group=False, show_pose_library=False)
        return KeyframeEditorVisibility()

    two_char = bool(caps.two_char_pipeline and caps.two_char_pipeline.active)
    show_pose_cn = caps.has_pose_control
    discovered_slots = list(caps.image_reference_slots or [])
    use_reference_slots_ui = custom_image_family and bool(discovered_slots)
    show_pose = default_image_family

    neg_titles = caps.negative_prompt_titles()
    show_neg_left = bool(neg_titles & _MAIN_OR_LEFT_NEGATIVE_TITLES)

    return KeyframeEditorVisibility(
        show_pose_group=show_pose,
        show_pose_library=show_pose,
        show_pose_cn_controls=show_pose_cn,
        show_char_left=not use_reference_slots_ui,
        show_char_right=(two_char or caps.has_secondary_character_reference()) and not use_reference_slots_ui,
        show_reference_slots_group=use_reference_slots_ui,
        show_prompt=bool(caps.has.get("prompt")),
        show_advanced=True,
        show_inject_lora=bool(caps.has.get("lora")),
        show_neg_left=show_neg_left,
        show_neg_right=two_char,
        show_neg_heal=two_char,
    )


def project_negative_visibility(
    caps: WorkflowCapabilities | None,
    *,
    custom_family: bool,
) -> ProjectNegativeVisibility:
    """Show project keyframe/heal negatives when the scanned workflow can accept them."""
    if custom_family:
        show_kf = bool(caps and not caps.error and caps.has.get("negative_prompt"))
        return ProjectNegativeVisibility(show_keyframes_all=show_kf, show_heal_all=False)

    if not caps or caps.error:
        return ProjectNegativeVisibility(show_keyframes_all=True, show_heal_all=False)

    show_kf = bool(caps.has.get("negative_prompt"))
    two_char = bool(caps.two_char_pipeline and caps.two_char_pipeline.active)
    show_heal = two_char and "HealNegPrompt" in caps.negative_prompt_titles()
    return ProjectNegativeVisibility(show_keyframes_all=show_kf, show_heal_all=show_heal)


def _detect_multi_char_prompt(workflow: dict[str, Any]) -> bool:
    count = 0
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        title = wc.node_title(node)
        if title in _MULTI_CHAR_PROMPT_TITLES:
            count += 1
        elif title.lower().startswith("thm-prompt-"):
            count += 1
    return count >= 2


def scan_workflow_capabilities(workflow: dict[str, Any], workflow_path: str | None = None) -> WorkflowCapabilities:
    """Scan workflow for THM controls (tag + legacy) and derived flags."""
    caps = WorkflowCapabilities(workflow_path=workflow_path)
    raw = wc.scan_workflow_controls(workflow)
    caps.controls = {k: v for k, v in raw.items() if k not in ("unknown", "lora_clip_support")}
    caps.lora_clip_support = bool(raw.get("lora_clip_support"))
    caps.unknown = list(raw.get("unknown") or [])
    caps.has_pose_control = bool(wc.find_nodes_by_title(workflow, "PoseControl"))
    caps.is_multi_char_prompt = _detect_multi_char_prompt(workflow)
    caps.two_char_pipeline = wc.discover_two_char_pipeline(workflow)
    caps.image_size_control = wc.discover_image_size_control(workflow)
    caps.generation_settings = wc.discover_generation_settings_control(workflow)
    caps.reference_wiring = wc.discover_reference_wiring_order(workflow)
    caps.image_reference_slots = wc.discover_image_reference_slots(workflow)
    caps.has_video_seed = wc.workflow_has_video_seed_target(workflow)

    for key in CAPABILITY_KEYS:
        caps.has[key] = bool(caps.controls.get(key))

    return caps


def scan_workflow_file(workflow_name: str | None, workflows_dir: Path | None = None) -> WorkflowCapabilities:
    path = resolve_workflow_path(workflow_name, workflows_dir)
    if not path:
        return WorkflowCapabilities(error="no workflow selected")
    try:
        workflow = load_workflow_dict(path)
        return scan_workflow_capabilities(workflow, workflow_path=str(path))
    except Exception as exc:
        return WorkflowCapabilities(workflow_path=str(path), error=str(exc))


def _format_mechanism_labels(mechanisms: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for mech in mechanisms:
        kind = mech.get("kind", "")
        if kind == "legacy_class" and mech.get("class_type"):
            parts.append(f"`{mech['class_type']}` (legacy class)")
        elif kind == "tag":
            for node in mech.get("nodes") or []:
                parts.append(f"`{node.get('title', '?')}` (tag)")
        elif kind == "legacy_title":
            for node in mech.get("nodes") or []:
                parts.append(f"`{node.get('title', '?')}` (legacy)")
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return ", ".join(seen) if seen else ""


def _format_image_size_block(caps: WorkflowCapabilities) -> list[str]:
    disc = caps.image_size_control
    if not disc:
        return []
    lines: list[str] = []
    detail = _format_mechanism_labels(disc.mechanisms)
    if disc.status == "full":
        lines.append(f"**Image size (THM drives):** Yes — {detail}" if detail else "**Image size (THM drives):** Yes")
    elif disc.status == "partial":
        lines.append(
            f"**Image size (THM drives):** Partial — {detail}; height and width are not both driven"
            if detail
            else "**Image size (THM drives):** Partial"
        )
    else:
        lines.append("**Image size (THM drives):** No")
    return lines


def _format_generation_settings_block(caps: WorkflowCapabilities) -> list[str]:
    gen = caps.generation_settings
    if not gen:
        return []
    lines = ["**Generation settings (project controls):**"]
    confirmed = gen.confirmed_fields()
    not_controlled = gen.not_controlled_fields()
    display_confirmed = [name for name in confirmed if name != wc.KSAMPLER]
    if display_confirmed:
        mech_parts: list[str] = []
        for field_name in display_confirmed:
            disc = gen.fields[field_name]
            label = _format_mechanism_labels(disc.mechanisms)
            if label:
                mech_parts.append(label)
        mech_text = ", ".join(dict.fromkeys(mech_parts))
        field_names = ", ".join(_GENERATION_FIELD_LABELS.get(name, name) for name in display_confirmed)
        bundle_hint = ""
        if gen.fields.get(wc.KSAMPLER) and gen.fields[wc.KSAMPLER].status == "confirmed":
            bundle_hint = _KSAMPLER_BUNDLE_HINT
        lines.append(f"- **Confirmed:** {field_names}" + (f" — {mech_text}" if mech_text else "") + bundle_hint)
    if not_controlled:
        names = ", ".join(_GENERATION_FIELD_LABELS.get(name, name) for name in not_controlled)
        lines.append(f"- **Not controlled by project:** {names} (workflow uses baked value)")
    if not confirmed and not not_controlled:
        lines.append("- None")
    return lines


_TWO_CHAR_SLOT_LABELS = {
    "lora_left": "LoRA (left)",
    "lora_right": "LoRA (right)",
    "prompt_left": "Prompt (left)",
    "prompt_right": "Prompt (right)",
    "prompt_heal": "Prompt (heal)",
    "neg_left": "Negative (left)",
    "neg_right": "Negative (right)",
    "neg_heal": "Negative (heal)",
}


def _format_two_char_slot_line(slot_key: str, slot: wc.TwoCharSlot) -> str:
    label = _TWO_CHAR_SLOT_LABELS.get(slot_key, slot_key)
    if not slot.present:
        return f"- **{label}:** —"
    titles = ", ".join(f"`{t}`" for t in slot.titles)
    return f"- **{label}:** {titles} ({slot.source})"


_REFERENCE_ROLE_LABELS = {
    "pose": "pose",
    "character_1": "character 1",
    "character_2": "character 2",
    "location": "location",
    "style": "style",
}

_REFERENCE_HINT_LABELS = {
    "pose": "Pose",
    "character_1": "character",
    "character_2": "character",
    "location": "location",
    "style": "style",
}

_FALLBACK_REFERENCE_HINT_ORDER = ("pose", "location", "style", "character_1", "character_2")


def _char_display_name(
    project: dict[str, Any],
    id_conf: dict[str, Any],
    role: str,
) -> str:
    chars = project.get("characters") or []
    char_by_id = {c.get("id"): c for c in chars if isinstance(c, dict) and c.get("id")}
    char_by_name = {
        str(c.get("name") or "").strip().lower(): c
        for c in chars
        if isinstance(c, dict) and c.get("name")
    }
    desired = [v for v in (id_conf or {}).get("characters", []) if v and isinstance(v, str)]
    if role == "character_2" and len(desired) >= 2:
        ref = desired[1]
    elif len(desired) >= 1:
        ref = desired[0]
    else:
        return "character"
    ch = char_by_id.get(ref) or char_by_name.get(str(ref).strip().lower())
    return str((ch or {}).get("name") or ref)


def _setting_display_name(project: dict[str, Any], sequence: dict[str, Any] | None) -> str:
    sid = str((sequence or {}).get("setting_id") or "").strip()
    if not sid:
        return "location"
    for item in project.get("settings") or []:
        if isinstance(item, dict) and item.get("id") == sid:
            return str(item.get("name") or sid)
    return sid


def _style_display_name(project: dict[str, Any], sequence: dict[str, Any] | None) -> str:
    sid = str((sequence or {}).get("style_id") or "").strip()
    if not sid:
        return "style"
    for item in project.get("styles") or []:
        if isinstance(item, dict) and item.get("id") == sid:
            return str(item.get("name") or sid)
    return sid


def _reference_hint_label(
    role: str,
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    id_conf: dict[str, Any],
) -> str:
    if role == "pose":
        return "Pose"
    if role == "location":
        return _setting_display_name(project, sequence)
    if role == "style":
        return _style_display_name(project, sequence)
    if role in ("character_1", "character_2"):
        return _char_display_name(project, id_conf, role)
    return _REFERENCE_HINT_LABELS.get(role, role)


def workflow_supports_image_references(caps: WorkflowCapabilities | None) -> bool:
    """True when the workflow exposes THM multi-image reference slots."""
    if not caps or caps.error:
        return False
    if caps.image_reference_slots:
        return True
    wiring = caps.reference_wiring
    if wiring and wiring.tier in ("linear_ref_stack", "tagged_loader_only"):
        return bool(wiring.entries or wiring.orphan_loaders)
    return False


@dataclass
class ReferencePresentSlot:
    """One active reference image for keyframe prompt preview."""

    role: str
    display_name: str
    caption: str
    path: str
    image_index: int


def _path_for_reference_role(
    role: str,
    *,
    pose_path: str,
    location_path: str,
    style_path: str = "",
    character_paths: dict[str | None, str],
) -> str:
    if role == "pose":
        return str(pose_path or "").strip()
    if role == "location":
        return str(location_path or "").strip()
    if role == "style":
        return str(style_path or "").strip()
    if role == "character_1":
        return str(
            character_paths.get(None)
            or character_paths.get("1")
            or character_paths.get("left")
            or ""
        ).strip()
    if role == "character_2":
        return str(
            character_paths.get("2") or character_paths.get("right") or ""
        ).strip()
    return ""


def _binding_display_name(
    slot_key: str,
    binding: dict[str, Any],
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
) -> str:
    sem = wc._binding_semantic(binding)
    if sem == "pose":
        return "Pose"
    if sem == "location":
        sid = wc.effective_setting_id_for_binding(binding, sequence)
        if sid:
            return wc._setting_display_name_for_id(project, sid)
        return _setting_display_name(project, sequence)
    if sem == "style":
        style_id = wc.effective_style_id_for_binding(binding, sequence)
        if style_id:
            return wc._style_display_name_for_id(project, style_id)
        return _style_display_name(project, sequence)
    if sem == "character":
        cid = str(binding.get("character_id") or "").strip()
        for ch in project.get("characters") or []:
            if not isinstance(ch, dict):
                continue
            if ch.get("id") == cid or str(ch.get("name", "")).strip().lower() == cid.lower():
                return str(ch.get("name") or cid)
        return "character"
    return f"image{slot_key or '1'}"


def compose_reference_present_slots(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    keyframe: dict[str, Any] | None,
    workflow_name: str | None = None,
    *,
    caps: WorkflowCapabilities | None = None,
) -> list[ReferencePresentSlot]:
    """Active reference slots in effective image order with filesystem paths for UI preview."""
    project = project or {}
    id_conf = keyframe if isinstance(keyframe, dict) else {}
    seq = sequence if isinstance(sequence, dict) else {}

    if caps is None:
        caps = scan_workflow_file(workflow_name) if workflow_name else WorkflowCapabilities()

    if not workflow_supports_image_references(caps):
        return []

    discovered_slots = list(caps.image_reference_slots or [])
    use_bindings = is_custom_image_family(project) and bool(discovered_slots)
    discovery = caps.reference_wiring

    char_paths = wc.resolve_character_reference_paths(project, id_conf)
    location_path = wc.resolve_location_reference_path(project, seq)
    style_path = wc.resolve_style_reference_path(project, seq)
    pose_path = str(id_conf.get("pose") or "").strip()
    paths_by_slot = (
        wc.resolve_reference_paths_from_bindings(id_conf, discovered_slots, project, seq)
        if use_bindings
        else None
    )
    active_by_role = wc.build_reference_active_by_role(
        pose_path=pose_path,
        character_paths=char_paths,
        location_path=location_path,
        style_path=style_path,
        slots=discovered_slots or None,
        paths_by_slot=paths_by_slot,
    )

    if use_bindings and discovered_slots and discovery and discovery.entries:
        bindings = wc.normalize_reference_bindings(id_conf, discovered_slots, project, seq)
        paths = paths_by_slot or {}
        slots: list[ReferencePresentSlot] = []
        effective_idx = 1
        for entry in discovery.entries:
            slot = next((s for s in discovered_slots if s.node_id == entry.load_image_id), None)
            if not slot:
                continue
            binding = bindings.get(wc.binding_key_for_slot(slot)) or bindings.get(slot.slot_key) or {}
            if wc._binding_semantic(binding) == "unset":
                continue
            if not active_by_role.get(entry.role):
                continue
            path = wc.path_for_image_slot(slot, paths)
            if not path or not os.path.isfile(path):
                continue
            label = f"image{effective_idx}"
            slots.append(
                ReferencePresentSlot(
                    role=entry.role,
                    display_name=label,
                    caption=label,
                    path=path,
                    image_index=effective_idx,
                )
            )
            effective_idx += 1
        return slots

    slots = []

    def _append(role: str, image_index: int) -> None:
        if not active_by_role.get(role):
            return
        path = _path_for_reference_role(
            role,
            pose_path=pose_path,
            location_path=location_path,
            style_path=style_path,
            character_paths=char_paths,
        )
        if not path or not os.path.isfile(path):
            return
        label = f"image{image_index}"
        slots.append(
            ReferencePresentSlot(
                role=role,
                display_name=label,
                caption=label,
                path=path,
                image_index=image_index,
            )
        )

    if discovery and discovery.tier == "linear_ref_stack" and discovery.entries:
        effective_idx = 1
        for entry in discovery.entries:
            if not active_by_role.get(entry.role):
                continue
            _append(entry.role, effective_idx)
            effective_idx += 1
    else:
        idx = 1
        for role in _FALLBACK_REFERENCE_HINT_ORDER:
            if not active_by_role.get(role):
                continue
            _append(role, idx)
            idx += 1

    return slots


def compose_reference_present_hint(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    keyframe: dict[str, Any] | None,
    workflow_name: str | None = None,
    *,
    caps: WorkflowCapabilities | None = None,
) -> str:
    """One-line Prompt field hint listing active image references (imageN)."""
    if caps is None:
        caps = scan_workflow_file(workflow_name) if workflow_name else WorkflowCapabilities()
    if not workflow_supports_image_references(caps):
        return ""
    slots = compose_reference_present_slots(
        project, sequence, keyframe, workflow_name, caps=caps
    )
    if not slots:
        return "No image references active for this keyframe."
    return ", ".join(slot.caption for slot in slots)


def compose_keyframe_reference_prelude_text(
    project: dict[str, Any],
    sequence: dict[str, Any] | None,
    keyframe: dict[str, Any] | None,
    workflow_name: str | None = None,
    *,
    caps: WorkflowCapabilities | None = None,
) -> str:
    """Multiline prelude for editor preview and generation (same as run_images)."""
    project = project or {}
    id_conf = keyframe if isinstance(keyframe, dict) else {}
    seq = sequence if isinstance(sequence, dict) else {}

    if caps is None:
        caps = scan_workflow_file(workflow_name) if workflow_name else WorkflowCapabilities()
    if not workflow_supports_image_references(caps):
        return ""

    discovery = caps.reference_wiring
    if not discovery or discovery.tier != "linear_ref_stack" or not discovery.entries:
        return ""

    discovered_slots = list(caps.image_reference_slots or [])
    char_paths = wc.resolve_character_reference_paths(project, id_conf)
    location_path = wc.resolve_location_reference_path(project, seq)
    style_path = wc.resolve_style_reference_path(project, seq)
    pose_path = str(id_conf.get("pose") or "").strip()
    paths_by_slot = (
        wc.resolve_reference_paths_from_bindings(id_conf, discovered_slots, project, seq)
        if is_custom_image_family(project) and discovered_slots
        else None
    )
    active_by_role = wc.build_reference_active_by_role(
        pose_path=pose_path,
        character_paths=char_paths,
        location_path=location_path,
        style_path=style_path,
        slots=discovered_slots or None,
        paths_by_slot=paths_by_slot,
    )
    wc.assign_effective_image_indices(discovery.entries, active_by_role)
    return wc.compose_reference_prelude(
        discovery,
        active_by_role,
        project=project,
        sequence=seq,
        id_conf=id_conf,
    )


def _format_reference_wiring_block(caps: WorkflowCapabilities) -> list[str]:
    wiring = caps.reference_wiring
    if not wiring:
        return []
    lines = ["**Reference wiring:**"]
    if wiring.warning:
        lines.append(f"_{wiring.warning}_")
    tier_labels = {
        "linear_ref_stack": "linear ReferenceLatent stack (THM skips empty branches at run time)",
        "tagged_loader_only": "tagged loaders only (inject/mute LoadImage; no ReferenceLatent rewire)",
        "none": "not detected",
    }
    lines.append(f"- **Topology:** {tier_labels.get(wiring.tier, wiring.tier)}")
    if wiring.entries:
        lines.append("")
        lines.append("**Reference image order (from wiring):**")
        for entry in wiring.entries:
            role_label = _REFERENCE_ROLE_LABELS.get(entry.role, entry.role)
            skip_note = "_skipped at run when no image_"
            lines.append(
                f"- image{entry.image_index} — {role_label} — `{entry.title}` — {skip_note}"
            )
    elif wiring.tier == "none":
        lines.append(
            "- _No ReferenceLatent stack detected. Tag LoadImage nodes and wire a linear chain toward `THM-Prompt`._"
        )
    if wiring.orphan_loaders:
        lines.append("")
        lines.append("**Tagged loaders outside ReferenceLatent chain:**")
        for summary in wiring.orphan_loaders[:6]:
            title = summary.get("title", "?")
            lines.append(f"- `{title}` — inject/mute loader only")
    if caps.image_reference_slots:
        lines.append("")
        lines.append("**Reference slots (Custom editor, up to 4):**")
        for slot in caps.image_reference_slots:
            sem = slot.default_semantic
            lines.append(
                f"- image{slot.image_index or '?'} — `{slot.title}` — default semantic: {sem}"
            )
    return lines


def _format_two_char_block(caps: WorkflowCapabilities) -> list[str]:
    pipe = caps.two_char_pipeline
    if not pipe or not pipe.active:
        return []
    lines = ["**2-character pass (project drives):**"]
    for slot_key in wc.TWO_CHAR_SLOT_TITLES:
        slot = pipe.slots.get(slot_key)
        if slot:
            lines.append(_format_two_char_slot_line(slot_key, slot))
    lora_count = pipe.present_slot_count("lora_")
    if lora_count >= 2:
        lines.append("- _LoRA tags from left+right prompts are injected into both markers._")
    if pipe.has_heal_pass:
        lines.append("- _Heal pass: positive `HealPosPrompt`; negative `HealNegPrompt`._")
    return lines


def _format_two_char_summary(pipe: wc.TwoCharDiscovery) -> str:
    lora_n = pipe.present_slot_count("lora_")
    prompt_n = pipe.present_slot_count("prompt_")
    neg_n = pipe.present_slot_count("neg_")
    summary = f"2char=lora×{lora_n},prompt×{prompt_n},neg×{neg_n}"
    if pipe.has_heal_pass:
        summary += ",heal=yes"
    return summary


def _keys_excluded_from_absent(caps: WorkflowCapabilities) -> set[str]:
    excluded = set(GROUPED_DISCOVERY_KEYS)
    if caps.image_size_control and caps.image_size_control.status in ("full", "partial"):
        excluded.update({"width", "height", "image_size"})
    if caps.generation_settings:
        for field_name in caps.generation_settings.confirmed_fields():
            excluded.add(field_name)
    return excluded


def capabilities_summary_line(caps: WorkflowCapabilities) -> str:
    if caps.error:
        return f"error={caps.error}"
    present = [
        k
        for k in CAPABILITY_KEYS
        if caps.has.get(k) and k not in GROUPED_DISCOVERY_KEYS
    ]
    parts = [f"present={','.join(present) or 'none'}"]
    if caps.image_size_control:
        isc = caps.image_size_control
        mech = _format_mechanism_labels(isc.mechanisms)
        if isc.status == "full" and mech:
            parts.append(f"image_size=full({mech})")
        else:
            parts.append(f"image_size={isc.status}")
    if caps.generation_settings:
        confirmed = caps.generation_settings.confirmed_fields()
        not_ctrl = caps.generation_settings.not_controlled_fields()
        if confirmed:
            parts.append(f"gen={','.join(_GENERATION_FIELD_LABELS.get(f, f) for f in confirmed)}")
        if not_ctrl:
            parts.append(f"!{','.join(_GENERATION_FIELD_LABELS.get(f, f) for f in not_ctrl)}")
    if caps.lora_clip_support:
        parts.append("lora_clip=yes")
    if caps.has_pose_control:
        parts.append("pose_control=yes")
    if caps.reference_wiring and caps.reference_wiring.entries:
        parts.append(f"ref_stack={len(caps.reference_wiring.entries)}")
    if caps.two_char_pipeline and caps.two_char_pipeline.active:
        parts.append(_format_two_char_summary(caps.two_char_pipeline))
    elif caps.is_multi_char_prompt:
        parts.append("multi_char_prompt=yes")
    if caps.unknown:
        parts.append(f"unknown_tags={len(caps.unknown)}")
    return " ".join(parts)


def log_capabilities(caps: WorkflowCapabilities, workflow_label: str = "") -> None:
    label = workflow_label or caps.workflow_path or "workflow"
    print(f"[CAPABILITIES] {label}: {capabilities_summary_line(caps)}")


def format_capabilities_markdown(caps: WorkflowCapabilities | None) -> str:
    if not caps:
        return "_No workflow selected._"
    if caps.error:
        return f"**Scan error:** {caps.error}"

    lines = [
        f"**File:** `{Path(caps.workflow_path).name if caps.workflow_path else '?'}`",
        "",
        "Workflow-level controls (project Settings may still apply steps/CFG/negatives):",
        "",
    ]

    lines.extend(_format_image_size_block(caps))
    lines.extend(_format_generation_settings_block(caps))
    ref_lines = _format_reference_wiring_block(caps)
    if ref_lines:
        if lines[-1] != "":
            lines.append("")
        lines.extend(ref_lines)
    if lines[-1] != "":
        lines.append("")

    two_char_lines = _format_two_char_block(caps)
    if two_char_lines:
        lines.extend(two_char_lines)
        lines.append("")

    for key in CAPABILITY_KEYS:
        if key in GROUPED_DISCOVERY_KEYS:
            continue
        nodes = caps.controls.get(key) or []
        if not nodes:
            continue
        label = CONTROL_DISPLAY_LABELS.get(key, key.replace("_", " "))
        titles = ", ".join(f"`{n['title']}` ({n['source']})" for n in nodes[:5])
        extra = f" +{len(nodes) - 5} more" if len(nodes) > 5 else ""
        lines.append(f"- **{label}**: {titles}{extra}")

    excluded = _keys_excluded_from_absent(caps)
    absent = [k for k in CAPABILITY_KEYS if not caps.has.get(k) and k not in excluded]
    if absent:
        absent_labels = [CONTROL_DISPLAY_LABELS.get(k, k.replace("_", " ")) for k in absent]
        lines.append("")
        lines.append("**Not in workflow:** " + ", ".join(absent_labels))

    flags = []
    if caps.lora_clip_support:
        flags.append("LoRA dual-path (model + clip)")
    if caps.has_pose_control:
        flags.append("PoseControl node (legacy SDXL)")
    if caps.two_char_pipeline and caps.two_char_pipeline.active:
        flags.append("2CHAR pipeline (left/right/heal)")
    elif caps.is_multi_char_prompt:
        flags.append("Multi-character prompts")
    if flags:
        lines.append("")
        lines.append("**Flags:** " + "; ".join(flags))

    if caps.unknown:
        lines.append("")
        lines.append("**Unknown THM tags:** " + ", ".join(f"`{u['title']}`" for u in caps.unknown))

    return "\n".join(lines)


def _control_node_titles(workflow: dict[str, Any], control: str) -> list[str]:
    return [n.title for n in wc.find_control_nodes(workflow, control)]


def _video_seed_mechanism_labels(workflow: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    if wc.find_tagged_control_nodes(workflow, wc.SEED):
        labels.append("`THM-Seed` (tag)")
    legacy = wc.find_legacy_control_nodes(workflow, wc.SEED)
    if legacy:
        titles = sorted({n.title for n in legacy})
        labels.append("legacy: " + ", ".join(f"`{t}`" for t in titles))
    for _, node in wc.find_nodes_by_class(workflow, "KSamplerAdvanced"):
        if wc._sampler_adds_noise(node):
            title = wc.node_title(node) or "KSamplerAdvanced"
            labels.append(f"`{title}` (noise entry)")
            break
    if wc.find_nodes_by_class(workflow, "RandomNoise"):
        labels.append("`RandomNoise` (legacy class)")
    return labels


@dataclass
class VideoWorkflowScan:
    """Video workflow capability scan for UI and `[VIDEO]` logging."""

    workflow_path: str | None = None
    video: wc.VideoCapabilities | None = None
    has_video_seed: bool = False
    seed_mechanisms: list[str] = field(default_factory=list)
    prompt_titles: list[str] = field(default_factory=list)
    negative_titles: list[str] = field(default_factory=list)
    lora_high_titles: list[str] = field(default_factory=list)
    lora_low_titles: list[str] = field(default_factory=list)
    lora_single_titles: list[str] = field(default_factory=list)
    thm_ksampler_count: int = 0
    project_controls_video_steps: bool = False
    workflow_baked_samplers: list[dict[str, Any]] = field(default_factory=list)
    supports_start_frame: bool = False
    supports_end_frame: bool = False
    start_frame_mechanisms: list[str] = field(default_factory=list)
    end_frame_mechanisms: list[str] = field(default_factory=list)
    workflow_baked_sigma_schedules: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        video = self.video
        return {
            "workflow_path": self.workflow_path,
            "error": self.error,
            "has_video_seed": self.has_video_seed,
            "seed_mechanisms": list(self.seed_mechanisms),
            "prompt_titles": list(self.prompt_titles),
            "negative_titles": list(self.negative_titles),
            "lora_high_titles": list(self.lora_high_titles),
            "lora_low_titles": list(self.lora_low_titles),
            "lora_single_titles": list(self.lora_single_titles),
            "thm_ksampler_count": self.thm_ksampler_count,
            "project_controls_video_steps": self.project_controls_video_steps,
            "workflow_baked_samplers": list(self.workflow_baked_samplers),
            "supports_start_frame": self.supports_start_frame,
            "supports_end_frame": self.supports_end_frame,
            "start_frame_mechanisms": list(self.start_frame_mechanisms),
            "end_frame_mechanisms": list(self.end_frame_mechanisms),
            "workflow_baked_sigma_schedules": list(self.workflow_baked_sigma_schedules),
            "video": {
                "lora_mode": video.lora_mode if video else "none",
                "has_video_generator": bool(video and video.has_video_generator),
                "has_frame_count": bool(video and video.has_frame_count),
                "has_frame_rate": bool(video and video.has_frame_rate),
                "has_save_video": bool(video and video.has_save_video),
                "has_start_frame": bool(video and video.has_start_frame),
                "has_end_frame": bool(video and video.has_end_frame),
                "has_express_samplers": bool(video and video.has_express_samplers),
                "has_legacy_wan_generator": bool(video and video.has_legacy_wan_generator),
                "has_thm_ksampler_passes": bool(video and video.has_thm_ksampler_passes),
                "has_thm_slowmo_primer": bool(video and video.has_thm_slowmo_primer),
                "has_thm_steps": bool(video and video.has_thm_steps),
                "supports_start_frame": bool(video and video.supports_start_frame),
                "supports_end_frame": bool(video and video.supports_end_frame),
                "start_frame_mechanisms": list(video.start_frame_mechanisms) if video else [],
                "end_frame_mechanisms": list(video.end_frame_mechanisms) if video else [],
                "workflow_baked_sigma_schedules": list(video.workflow_baked_sigma_schedules) if video else [],
            },
        }


def video_frame_input_support_from_scan(
    scan: VideoWorkflowScan | dict[str, Any] | None,
) -> tuple[bool, bool]:
    """Return (supports_start_frame, supports_end_frame) from scan or serialized dict."""
    if scan is None:
        return False, False
    if isinstance(scan, dict):
        if scan.get("error"):
            return False, False
        video = scan.get("video") or {}
        return bool(video.get("supports_start_frame")), bool(video.get("supports_end_frame"))
    if scan.error or not scan.video:
        return False, False
    vc = scan.video
    return bool(vc.supports_start_frame), bool(vc.supports_end_frame)


def scan_video_workflow_capabilities(
    workflow: dict[str, Any],
    workflow_path: str | None = None,
) -> VideoWorkflowScan:
    video = wc.discover_video_capabilities(workflow)
    project_steps = wc.video_project_controls_steps(workflow)
    frame_support = wc.discover_video_frame_input_support(workflow)
    return VideoWorkflowScan(
        workflow_path=workflow_path,
        video=video,
        has_video_seed=wc.workflow_has_video_seed_target(workflow),
        seed_mechanisms=_video_seed_mechanism_labels(workflow),
        prompt_titles=_control_node_titles(workflow, wc.PROMPT),
        negative_titles=_control_node_titles(workflow, wc.NEGATIVE_PROMPT),
        lora_high_titles=_control_node_titles(workflow, wc.LORA_HIGH),
        lora_low_titles=_control_node_titles(workflow, wc.LORA_LOW),
        lora_single_titles=_control_node_titles(workflow, wc.LORA),
        thm_ksampler_count=len(wc.discover_thm_ksampler_passes(workflow)),
        project_controls_video_steps=project_steps,
        workflow_baked_samplers=wc.discover_workflow_baked_samplers(workflow),
        supports_start_frame=frame_support.supports_start_frame,
        supports_end_frame=frame_support.supports_end_frame,
        start_frame_mechanisms=list(frame_support.start_mechanisms),
        end_frame_mechanisms=list(frame_support.end_mechanisms),
        workflow_baked_sigma_schedules=list(video.workflow_baked_sigma_schedules),
    )


def video_generation_defaults_visibility(
    scan: VideoWorkflowScan | None,
) -> VideoGenerationDefaultsVisibility:
    """Show project Video steps / FPS only when the scanned workflow exposes injection targets."""
    if not scan or scan.error or not scan.video:
        return VideoGenerationDefaultsVisibility(show_video_steps=False, show_video_fps=False)
    vc = scan.video
    return VideoGenerationDefaultsVisibility(
        show_video_steps=bool(scan.project_controls_video_steps),
        show_video_fps=bool(vc.has_frame_rate),
    )


def scan_video_workflow_file(
    workflow_name: str | None,
    workflows_dir: Path | None = None,
) -> VideoWorkflowScan:
    path = resolve_workflow_path(workflow_name, workflows_dir)
    if not path:
        return VideoWorkflowScan(error="no workflow selected")
    try:
        workflow = load_workflow_dict(path)
        return scan_video_workflow_capabilities(workflow, workflow_path=str(path))
    except Exception as exc:
        return VideoWorkflowScan(workflow_path=str(path), error=str(exc))


def _video_bool_line(label: str, found: bool) -> str:
    status = "yes" if found else "no"
    return f"- **{label}:** {status}"


def video_capabilities_summary_line(scan: VideoWorkflowScan | None) -> str:
    if not scan:
        return "error=no scan"
    if scan.error:
        return f"error={scan.error}"
    vc = scan.video
    if not vc:
        return "error=no video caps"
    parts = [
        f"lora_mode={vc.lora_mode}",
        f"THM-VideoGenerator={'found' if vc.has_video_generator else 'missed'}",
        f"THM-FrameCount={'found' if vc.has_frame_count else 'missed'}",
        f"THM-FrameRate={'found' if vc.has_frame_rate else 'missed'}",
        f"THM-SaveVideo={'found' if vc.has_save_video else 'missed'}",
        f"THM-StartFrame={'found' if vc.has_start_frame else 'missed'}",
        f"THM-EndFrame={'found' if vc.has_end_frame else 'missed'}",
        f"THM-KSampler={'found' if vc.has_thm_ksampler_passes else 'missed'}",
        f"THM-SlowMoPrimer={'found' if vc.has_thm_slowmo_primer else 'missed'}",
        f"THM-Steps={'found' if vc.has_thm_steps else 'missed'}",
        f"start_frame={'supported' if vc.supports_start_frame else 'unsupported'}",
        f"end_frame={'supported' if vc.supports_end_frame else 'unsupported'}",
        f"express_samplers={'found' if vc.has_express_samplers else 'missed'}",
        f"legacy_wan_generator={'found' if vc.has_legacy_wan_generator else 'missed'}",
        f"project_steps={'yes' if scan.project_controls_video_steps else 'no'}",
        f"seed={'yes' if scan.has_video_seed else 'no'}",
    ]
    return " ".join(parts)


def log_video_capabilities(scan: VideoWorkflowScan, workflow_label: str = "") -> None:
    label = workflow_label or (Path(scan.workflow_path).name if scan.workflow_path else "workflow")
    print(f"[VIDEO] {label}: {video_capabilities_summary_line(scan)}")


def format_video_capabilities_markdown(scan: VideoWorkflowScan | None) -> str:
    if not scan:
        return "_No workflow selected._"
    if scan.error:
        return f"**Scan error:** {scan.error}"

    vc = scan.video
    if not vc:
        return "_No video capability data._"

    lines = [
        f"**File:** `{Path(scan.workflow_path).name if scan.workflow_path else '?'}`",
        "",
        "Video workflow controls (project **Default video workflow** + in-between runner):",
        "",
    ]

    if scan.prompt_titles:
        lines.append(f"- **Prompt:** {', '.join(f'`{t}`' for t in scan.prompt_titles)}")
    else:
        lines.append("- **Prompt:** _not found_")
    if scan.negative_titles:
        lines.append(f"- **Negative:** {', '.join(f'`{t}`' for t in scan.negative_titles)}")
    else:
        lines.append("- **Negative:** _not found_")

    lines.append("")
    lines.append("**Seed injection:**")
    if scan.has_video_seed:
        mech = "; ".join(scan.seed_mechanisms) if scan.seed_mechanisms else "detected"
        lines.append(f"- **Writable:** yes — {mech}")
    else:
        lines.append("- **Writable:** no")

    lines.append("")
    lines.append("**Frame inputs (workflow support):**")

    def _frame_support_line(label: str, supported: bool, mechanisms: list[str]) -> str:
        if supported:
            mech = ", ".join(f"`{m}`" for m in mechanisms) if mechanisms else "detected"
            return f"- **{label}:** supported — {mech}"
        return f"- **{label}:** not supported by this workflow"

    lines.append(_frame_support_line("Start frame", vc.supports_start_frame, vc.start_frame_mechanisms))
    lines.append(_frame_support_line("End frame", vc.supports_end_frame, vc.end_frame_mechanisms))
    if vc.has_legacy_wan_generator and not (vc.has_start_frame and vc.has_end_frame):
        lines.append("- _Legacy Wan generator also present; runner may wire StartImage/EndImage loaders._")

    lines.append("")
    lines.append("**Frame tags (discovery):**")
    lines.append(_video_bool_line("THM-StartFrame tag", vc.has_start_frame))
    lines.append(_video_bool_line("THM-EndFrame tag", vc.has_end_frame))
    lines.append(_video_bool_line("Legacy WanFirstLastFrameToVideo", vc.has_legacy_wan_generator))

    lines.append("")
    lines.append("**Timing / output:**")
    lines.append(_video_bool_line("THM-FrameRate / THM-FPS", vc.has_frame_rate))
    lines.append(_video_bool_line("THM-FrameCount", vc.has_frame_count))
    lines.append(_video_bool_line("THM-SaveVideo / combine node", vc.has_save_video))
    lines.append(_video_bool_line("THM-VideoGenerator", vc.has_video_generator))

    lines.append("")
    lines.append("**LoRA:**")
    lines.append(f"- **Mode:** `{vc.lora_mode}`")
    if scan.lora_high_titles or scan.lora_low_titles:
        if scan.lora_high_titles:
            lines.append(f"- **High markers:** {', '.join(f'`{t}`' for t in scan.lora_high_titles)}")
        if scan.lora_low_titles:
            lines.append(f"- **Low markers:** {', '.join(f'`{t}`' for t in scan.lora_low_titles)}")
    if scan.lora_single_titles and vc.lora_mode == "single":
        lines.append(f"- **Single-path markers:** {', '.join(f'`{t}`' for t in scan.lora_single_titles)}")

    lines.append("")
    lines.append("**Sampler path:**")
    if vc.has_express_samplers:
        lines.append(
            "- **Legacy triple:** `SlowMoPrimer` / `IterKSampler` / `WanFixedSeed` "
            "(step ranges from `video_steps_default`)"
        )
    if vc.has_thm_slowmo_primer:
        lines.append("- **Tagged primer:** `THM-SlowMoPrimer` (2 steps fixed in `run_video.py`)")
    if vc.has_thm_ksampler_passes:
        lines.append(
            f"- **Tagged chain:** {scan.thm_ksampler_count}× `THM-KSampler` pass(es) "
            "(step **ranges** from `video_steps_default`, not project CFG)"
        )
    if vc.has_thm_steps:
        lines.append(
            "- **Tagged scheduler:** `THM-Steps` (writes `steps` on tagged node, e.g. `LTXVScheduler`)"
        )
    if vc.workflow_baked_sigma_schedules:
        for entry in vc.workflow_baked_sigma_schedules:
            if entry.get("class_type") == "ManualSigmas":
                sigmas = entry.get("sigmas")
                lines.append(
                    f"- **Workflow-baked sigmas:** `{entry.get('title', '?')}` "
                    f"(ManualSigmas — not driven by `video_steps_default`; sigmas={sigmas!r})"
                )
            else:
                steps = entry.get("steps")
                lines.append(
                    f"- **Workflow-baked scheduler:** `{entry.get('title', '?')}` "
                    f"({entry.get('class_type', '?')}) — steps={steps if steps is not None else '?'}"
                )
    if scan.workflow_baked_samplers:
        for entry in scan.workflow_baked_samplers:
            steps = entry.get("steps")
            steps_label = steps if steps is not None else "?"
            lines.append(
                f"- **Workflow-baked:** `{entry.get('title', '?')}` "
                f"({entry.get('class_type', 'sampler')}) — steps={steps_label}"
            )
    if not (
        vc.has_express_samplers
        or vc.has_thm_ksampler_passes
        or vc.has_thm_slowmo_primer
        or vc.has_thm_steps
        or scan.workflow_baked_samplers
    ):
        lines.append("- _No tagged, legacy triple, or workflow-baked sampler detected._")

    lines.append("")
    if scan.project_controls_video_steps:
        lines.append("**Project-driven (not workflow-baked CFG):**")
        lines.append("- **Video steps:** `project.inbetween_generation.video_steps_default`")
        lines.append("- **Slomo primer steps:** fixed at **2** in code (`PRIMER_STEPS` in `run_video.py`)")
        if vc.has_frame_rate:
            lines.append("- **FPS:** `project.inbetween_generation.fps` → `THM-FrameRate` / `THM-FPS`")
        else:
            lines.append("- **FPS:** not injected (no `THM-FrameRate` / `THM-FPS` tag)")
    elif scan.workflow_baked_samplers:
        lines.append("**Workflow-baked (project Video steps not applied):**")
        for entry in scan.workflow_baked_samplers:
            steps = entry.get("steps")
            steps_label = steps if steps is not None else "?"
            lines.append(f"- **`{entry.get('title', '?')}`:** steps={steps_label} (edit in ComfyUI export)")
        if vc.has_frame_rate:
            lines.append("- **FPS:** `project.inbetween_generation.fps` → `THM-FrameRate` / `THM-FPS`")
        else:
            lines.append("- **FPS:** workflow-baked (no `THM-FrameRate` / `THM-FPS` tag)")
    else:
        lines.append("**Video steps:** not controlled by project (no injectable sampler chain).")
        if vc.has_frame_rate:
            lines.append("- **FPS:** `project.inbetween_generation.fps` → `THM-FrameRate` / `THM-FPS`")
        else:
            lines.append("- **FPS:** not controlled by project (no `THM-FrameRate` / `THM-FPS` tag)")

    missing: list[str] = []
    if not scan.prompt_titles:
        missing.append("prompt (`THM-Prompt`)")
    if not vc.has_save_video:
        missing.append("save video (`THM-SaveVideo` or VHS/SaveVideo class)")
    if not vc.has_frame_rate:
        missing.append("frame rate (`THM-FrameRate` / `THM-FPS`)")
    if not vc.supports_start_frame and not vc.supports_end_frame:
        missing.append("frame input (no start/end support detected)")
    if not (
        vc.has_express_samplers
        or vc.has_thm_ksampler_passes
        or vc.has_thm_slowmo_primer
        or vc.has_thm_steps
    ):
        missing.append("sampler chain (legacy triple, `THM-KSampler`, or `THM-Steps`)")
    if missing:
        lines.append("")
        lines.append("**Missing for BYO:** " + ", ".join(missing))

    return "\n".join(lines)


def show_capabilities_panel(project_dict: dict | None, features: dict | None) -> bool:
    if (features or {}).get("show_workflow_capabilities"):
        return True
    project = (project_dict or {}).get("project") if isinstance(project_dict, dict) else {}
    if not isinstance(project, dict):
        project = {}
    return bool(project.get("debug", {}).get("show_workflow_capabilities", False))
