"""
THM project JSON builder — fork for thm-agent client.

Forked from project-builder/builder.py — evolve independently; do not import
project-builder at runtime. Imports core schema helpers from ../src/ at runtime.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = REPO_ROOT / "src"


def _bootstrap_src_imports() -> None:
    """Allow importing src/helpers when optional app deps (gradio, PIL) are absent."""
    import sys
    from unittest.mock import MagicMock

    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

    for mod in ("gradio", "gradio.components", "PIL", "PIL.Image", "PIL.PngImagePlugin"):
        if mod in sys.modules:
            continue
        root = mod.split(".")[0]
        try:
            __import__(root)
        except ImportError:
            sys.modules[mod] = MagicMock()


_bootstrap_src_imports()

from helpers import (  # noqa: E402
    DEFAULT_PROJECT,
    DEFAULT_PROJECT_WORKFLOW_FILENAME,
    DUR_CHOICES,
    IMAGE_MODEL_FAMILY_CUSTOM,
    IMAGE_MODEL_FAMILY_DEFAULT,
    VIDEO_MODEL_FAMILY_CUSTOM,
    VIDEO_MODEL_FAMILY_DEFAULT,
    _deep_copy,
    _ensure_nonempty_api_base,
    _ensure_project,
    atomic_write,
    is_custom_image_family,
    load_config,
    validate_before_save,
)
from editor_helpers import (  # noqa: E402
    _add_keyframe,
    _add_sequence,
    _delete_sequence,
    _refresh_video_chain,
)

PathLike = Union[str, Path]

# Fields preserved on round-trip merge unless explicitly overwritten in new tree.
_GENERATION_PRESERVE_KEYFRAME = (
    "selected_image_path",
    "pose",
    "reference_bindings",
    "sampler_seed_start",
    "controlnet_settings",
)
_GENERATION_PRESERVE_VIDEO = ("selected_video_path",)
_GENERATION_PRESERVE_ASSET = ("reference_image",)

_CLIP_DURATION_MIN = int(DUR_CHOICES[0])
_CLIP_DURATION_MAX = int(DUR_CHOICES[-1])


def normalize_clip_duration_sec(value: Optional[float]) -> Optional[int]:
    """
    Clip lengths are whole seconds only, 1–10 (matches app ``DUR_CHOICES`` / UI radio).

    Rounds fractional values to the nearest integer and clamps to the allowed range.
    Returns None when value is None or cannot be parsed.
    """
    if value is None:
        return None
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(_CLIP_DURATION_MIN, min(_CLIP_DURATION_MAX, n))


def clip_duration_choices() -> List[int]:
    """Allowed per-clip durations in seconds (same as Gradio UI)."""
    return [int(x) for x in DUR_CHOICES]


def sequence_video_count(seq: dict) -> int:
    return len(seq.get("video_order") or [])


def effective_sequence_action_prompt(seq: dict, action_prompt: str) -> str:
    """
    Sequence action_prompt applies to every in-between in a sequence.

    Only use when the sequence has **2+ videos** (shared consistency, not narrative).
    Single-video sequences must leave action_prompt empty; put motion in
    video.inbetween_prompt instead.
    """
    if sequence_video_count(seq) < 2:
        return ""
    return (action_prompt or "").strip()


@dataclass
class BeatSpec:
    """One beat inside a continuous sequence (oner): still + motion to the next anchor."""

    layout: str
    inbetween_prompt: str = ""
    duration_sec: Optional[float] = None
    character_ids: Tuple[str, str] = ("", "")


@dataclass
class ShotSpec:
    """One discrete cut → becomes its own sequence."""

    layout: str
    inbetween_prompt: str = ""
    duration_sec: Optional[float] = None
    character_ids: Tuple[str, str] = ("", "")
    open_start: Optional[bool] = None
    open_end: Optional[bool] = None
    # When open_start and open_end are both true (1 keyframe → 2 videos):
    inbetween_prompt_out: str = ""
    duration_sec_out: Optional[float] = None
    # When open_start and open_end are both false (true KF→KF clip):
    layout_end: str = ""
    setting_prompt: str = ""
    style_prompt: str = ""
    action_prompt: str = ""


# In-between text suggesting motion *into* the keyframe (keyframe = last frame of clip).
_INTO_KEYFRAME_HINTS = (
    "until", "before", "approaches", "arrives", "enters", "walks to", "walks down",
    "finds and", "pick up", "picks up", "lands on", "ends on", "ends with", "into frame",
    "leading to", "prior to", "preceding", "reaches", "comes to", "settles on",
)

# In-between text suggesting motion *out of* the keyframe (keyframe = first frame of clip).
_FROM_KEYFRAME_HINTS = (
    "continues", "walks away", "leaves", "departs", "from here", "after", "heads toward",
    "exits", "turns and", "starts to", "begins to", "pushes off", "steps forward",
)


def recommend_video_plan(layout: str = "", inbetween_prompt: str = "") -> Tuple[bool, bool]:
    """
    Recommend ``video_plan`` for a single-keyframe, single-video clip.

    Only distinguishes motion-into vs motion-out of one still. For both-open
    (2 videos, keyframe in the middle) or both-closed (KF→KF), set flags explicitly.
    """
    text = (inbetween_prompt or "").lower()
    into_score = sum(1 for h in _INTO_KEYFRAME_HINTS if h in text)
    from_score = sum(1 for h in _FROM_KEYFRAME_HINTS if h in text)
    if into_score > from_score:
        return True, False
    if from_score > into_score:
        return False, True
    return False, True


def describe_video_plan(open_start: bool, open_end: bool, keyframe_count: int = 1) -> str:
    """Human-readable summary of video gaps for a sequence."""
    if keyframe_count == 1:
        if open_start and open_end:
            return "2 videos: open → keyframe → open (keyframe is the middle frame)"
        if open_start and not open_end:
            return "1 video: open → keyframe (keyframe is last frame)"
        if not open_start and open_end:
            return "1 video: keyframe → open (keyframe is first frame)"
        return "0 videos with 1 keyframe — need layout_end (2 keyframes) for KF→KF"
    if not open_start and not open_end and keyframe_count >= 2:
        return f"{keyframe_count - 1} video(s): keyframe → keyframe (true first/last frame clip)"
    parts = []
    if open_start:
        parts.append("open → first keyframe")
    for i in range(keyframe_count - 1):
        parts.append(f"keyframe{i + 1} → keyframe{i + 2}")
    if open_end:
        parts.append("last keyframe → open")
    return f"{len(parts)} video gap(s): " + ", ".join(parts)


def resolve_video_plan(
    layout: str = "",
    inbetween_prompt: str = "",
    open_start: Optional[bool] = None,
    open_end: Optional[bool] = None,
) -> Tuple[bool, bool]:
    """Use explicit open_start/open_end when set; otherwise ``recommend_video_plan``."""
    rec_start, rec_end = recommend_video_plan(layout, inbetween_prompt)
    return (
        rec_start if open_start is None else open_start,
        rec_end if open_end is None else open_end,
    )
_CUTS_KEYWORDS = (
    "shot", "shots", "cut", "cuts", "scene", "scenes", "clip", "clips",
    "angle", "angles", "insert", "inserts", "close-up", "close up", "wide shot",
    "medium shot", "establishing", "cut to", "smash cut", "jump cut",
)

# User language implying one continuous take (one sequence, chained keyframes).
_ONER_KEYWORDS = (
    "oner", "one take", "one-shot", "one shot", "continuous", "long take",
    "without cutting", "no cuts", "single take", "follow through", "unbroken",
    "tracking shot through", "walk through without cutting",
)


def _workflow_for_characters(data: dict, character_ids: Sequence[str]) -> str:
    """Default-family workflow filename from character slot count."""
    filled = sum(1 for c in character_ids if str(c or "").strip())
    if filled >= 2:
        for seq in (data.get("sequences") or {}).values():
            for kf in (seq.get("keyframes") or {}).values():
                wf = str(kf.get("workflow_json") or "")
                if "Klein" in wf or "klein" in wf.lower():
                    return "pose_2CHAR-Klein.json"
        return "pose_2CHAR.json"
    if filled == 1:
        return "pose_1CHAR.json"
    return DEFAULT_PROJECT_WORKFLOW_FILENAME


def load_project(path: PathLike) -> dict:
    """Load project JSON from disk and normalize to V2."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return _ensure_project(data)


def load_project_with_fingerprint(path: PathLike) -> Tuple[dict, str]:
    """Like load_project, but also returns a fingerprint of the raw bytes read.

    Pass the fingerprint to save_project(expected_fingerprint=...) to detect
    whether someone else wrote to the file between this load and that save."""
    p = Path(path)
    raw = p.read_bytes()
    data = json.loads(raw)
    return _ensure_project(data), hashlib.sha256(raw).hexdigest()


class StaleProjectError(RuntimeError):
    """Raised by save_project when the file changed on disk since it was loaded."""


def save_project(path: PathLike, data: dict, expected_fingerprint: Optional[str] = None) -> None:
    """Validate, normalize, and atomically write project JSON.

    expected_fingerprint, if given, must match the file's current on-disk
    fingerprint or the save is refused — someone else (typically a manual
    save from the UI) wrote to it since this caller's load."""
    data = _ensure_project(copy.deepcopy(data))
    ok, msg = validate_before_save(data, str(path))
    if not ok:
        raise ValueError(msg)
    p = Path(path)
    if expected_fingerprint is not None and p.exists():
        current_fingerprint = hashlib.sha256(p.read_bytes()).hexdigest()
        if current_fingerprint != expected_fingerprint:
            raise StaleProjectError(
                f"Project file changed on disk since it was loaded: {p}. "
                "Someone else (likely a manual save from the UI) wrote to it in "
                "the meantime. Reload the project and reapply this change."
            )
    data["project"]["active_writer"] = "agent"
    atomic_write(p, data)


def create_blank(name: str, *, family: str = "default") -> dict:
    """Create a new project dict with app defaults (one empty sequence + keyframe)."""
    data = _deep_copy(DEFAULT_PROJECT)
    data["project"]["name"] = name.strip() or "untitled"
    data["project"]["is_protected_from_empty_save"] = True

    fam = (family or IMAGE_MODEL_FAMILY_DEFAULT).strip().lower()
    if fam == IMAGE_MODEL_FAMILY_CUSTOM:
        data["project"]["image_model_family"] = IMAGE_MODEL_FAMILY_CUSTOM
    else:
        data["project"]["image_model_family"] = IMAGE_MODEL_FAMILY_DEFAULT
        data["project"]["default_workflow_json"] = DEFAULT_PROJECT_WORKFLOW_FILENAME

    try:
        cfg = load_config().get("models", {})
        proj = data["project"]
        proj["model"] = cfg.get("default_project_model", "sdXL_v10VAEFix.safetensors")
        proj["pose_model_fast"] = cfg.get("pose_model_fast", "sdXL_v10VAEFix.safetensors")
        proj["pose_model_enhanced"] = cfg.get("pose_model_enhanced", "obsessionIllustrious_v21.safetensors")
        proj["inpainting_model"] = cfg.get("inpainting_model", "juggernautxl-inpainting.safetensors")
        proj["controlnet_model"] = cfg.get("controlnet_model", "diffusion_pytorch_model_promax.safetensors")
        proj["upscale_model"] = cfg.get("upscale_model", "4x_NMKD-Siax_200k.pth")
        proj["interpolation_model"] = cfg.get("interpolation_model", "rife47.pth")
    except Exception:
        pass

    data = _ensure_nonempty_api_base(data, "")
    data, _seq_id = _add_sequence(data)
    data, _kf_id = _add_keyframe(data, _seq_id)
    return _ensure_project(data)


def _new_asset_id() -> str:
    return str(uuid.uuid4())


def _append_asset(
    data: dict,
    list_key: str,
    name: str,
    prompt: str,
    *,
    negative: str = "",
    lora_keyword: str = "",
    generator_prompt: str = "",
    generator_negative: str = "",
) -> Tuple[dict, str]:
    data = _ensure_project(data)
    asset_id = _new_asset_id()
    item = {
        "id": asset_id,
        "name": name,
        "lora_keyword": lora_keyword,
        "prompt": prompt,
        "negative_prompt": negative,
    }
    if generator_prompt:
        item["generator_prompt"] = generator_prompt
    if generator_negative:
        item["generator_negative_prompt"] = generator_negative
    data["project"].setdefault(list_key, []).append(item)
    data["project"]["is_protected_from_empty_save"] = True
    return data, asset_id


def add_character(
    data: dict,
    name: str,
    prompt: str,
    *,
    negative: str = "",
    lora_keyword: str = "",
    generator_prompt: str = "",
    generator_negative: str = "",
) -> Tuple[dict, str]:
    return _append_asset(
        data,
        "characters",
        name,
        prompt,
        negative=negative,
        lora_keyword=lora_keyword,
        generator_prompt=generator_prompt,
        generator_negative=generator_negative,
    )


def add_setting(
    data: dict,
    name: str,
    prompt: str,
    *,
    negative: str = "",
    lora_keyword: str = "",
    generator_prompt: str = "",
    generator_negative: str = "",
) -> Tuple[dict, str]:
    return _append_asset(
        data,
        "settings",
        name,
        prompt,
        negative=negative,
        lora_keyword=lora_keyword,
        generator_prompt=generator_prompt,
        generator_negative=generator_negative,
    )


def add_style(
    data: dict,
    name: str,
    prompt: str,
    *,
    negative: str = "",
    lora_keyword: str = "",
    generator_prompt: str = "",
    generator_negative: str = "",
) -> Tuple[dict, str]:
    return _append_asset(
        data,
        "styles",
        name,
        prompt,
        negative=negative,
        lora_keyword=lora_keyword,
        generator_prompt=generator_prompt,
        generator_negative=generator_negative,
    )


def add_sequence(
    data: dict,
    *,
    seq_id: Optional[str] = None,
    setting_id: str = "",
    style_id: str = "",
    setting_prompt: str = "",
    style_prompt: str = "",
    action_prompt: str = "",
    open_start: bool = False,
    open_end: bool = True,
) -> Tuple[dict, str]:
    data = _ensure_project(data)
    if seq_id:
        if seq_id in data.get("sequences", {}):
            raise ValueError(f"Sequence already exists: {seq_id}")
        new_id = seq_id
        new_seq = {
            "id": new_id,
            "type": "sequence",
            "keyframes": {},
            "keyframe_order": [],
            "videos": {},
            "video_order": [],
            "video_plan": {"open_start": open_start, "open_end": open_end},
        }
        d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
        new_seq = _refresh_video_chain(new_seq, d, data)
        data["sequences"][new_id] = new_seq
        order = data.setdefault("sequence_order", [])
        if new_id not in order:
            order.append(new_id)
            order.sort(
                key=lambda s: int(s[3:]) if s.startswith("seq") and s[3:].isdigit() else 9999
            )
        data["project"]["is_protected_from_empty_save"] = True
        sid = new_id
    else:
        data, sid = _add_sequence(data)
    seq = data["sequences"][sid]
    seq["setting_id"] = setting_id or ""
    seq["style_id"] = style_id or ""
    seq["setting_prompt"] = setting_prompt
    seq["style_prompt"] = style_prompt
    seq["action_prompt"] = action_prompt
    seq["video_plan"] = {"open_start": open_start, "open_end": open_end}
    d = float(data["project"]["inbetween_generation"].get("duration_default_sec", 3.0))
    _refresh_video_chain(seq, d, data)
    return data, sid


def add_keyframe(
    data: dict,
    seq_id: str,
    layout: str,
    *,
    character_ids: Tuple[str, str] = ("", ""),
    workflow: Optional[str] = None,
) -> Tuple[dict, str]:
    data, kf_id = _add_keyframe(data, seq_id)
    seq = data["sequences"][seq_id]
    kf = seq["keyframes"][kf_id]
    kf["layout"] = layout
    c1, c2 = (character_ids[0] if len(character_ids) > 0 else "", character_ids[1] if len(character_ids) > 1 else "")
    kf["characters"] = [c1 or "", c2 or ""]
    if workflow is not None:
        kf["workflow_json"] = workflow
    elif not is_custom_image_family(data):
        kf["workflow_json"] = _workflow_for_characters(data, kf["characters"])
    return data, kf_id


def set_video_prompt(
    data: dict,
    seq_id: str,
    vid_id: str,
    inbetween_prompt: str,
    *,
    duration_sec: Optional[float] = None,
    negative_prompt: str = "",
) -> dict:
    data = _ensure_project(data)
    seq = data["sequences"].get(seq_id)
    if not seq:
        raise KeyError(f"Sequence not found: {seq_id}")
    vid = seq.get("videos", {}).get(vid_id)
    if not vid:
        raise KeyError(f"Video not found: {vid_id} in {seq_id}")
    vid["inbetween_prompt"] = inbetween_prompt
    if negative_prompt:
        vid["negative_prompt"] = negative_prompt
    if duration_sec is not None:
        normalized = normalize_clip_duration_sec(duration_sec)
        if normalized is not None:
            vid["duration_override_sec"] = normalized
    return data


def recommend_edit_structure(brief: str = "", *, explicit_oner: bool = False, explicit_cuts: bool = False) -> str:
    """
    Return ``"cuts"`` (one sequence per shot) or ``"oner"`` (chained in-betweens in one sequence).

    Prefer explicit user flags over keyword heuristics. When ambiguous, default to ``"cuts"``
    because multiple shots usually means discrete edits.
    """
    if explicit_oner and not explicit_cuts:
        return "oner"
    if explicit_cuts and not explicit_oner:
        return "cuts"
    text = (brief or "").lower()
    if any(k in text for k in _ONER_KEYWORDS):
        return "oner"
    if any(k in text for k in _CUTS_KEYWORDS):
        return "cuts"
    return "cuts"


def remove_placeholder_sequences(data: dict) -> dict:
    """Remove empty sequences left by create_blank (single keyframe, blank layout)."""
    data = _ensure_project(copy.deepcopy(data))
    to_remove: List[str] = []
    for sid in list(data.get("sequence_order") or []):
        seq = data.get("sequences", {}).get(sid)
        if not isinstance(seq, dict):
            to_remove.append(sid)
            continue
        kf_order = seq.get("keyframe_order") or []
        keyframes = seq.get("keyframes") or {}
        if not kf_order and not keyframes:
            to_remove.append(sid)
            continue
        if len(kf_order) == 1:
            kf = keyframes.get(kf_order[0], {})
            if isinstance(kf, dict) and not str(kf.get("layout") or "").strip():
                to_remove.append(sid)
    for sid in to_remove:
        data, _ = _delete_sequence(data, sid)
    return data


def build_shot(
    data: dict,
    shot: ShotSpec,
    *,
    setting_id: str = "",
    style_id: str = "",
    setting_prompt: str = "",
    style_prompt: str = "",
    action_prompt: str = "",
) -> Tuple[dict, str]:
    """Add one sequence for a discrete cut (1–2 keyframes depending on video_plan)."""
    layout_end = str(shot.layout_end or "").strip()

    if layout_end:
        open_start, open_end = False, False
        beats = [
            BeatSpec(shot.layout, shot.inbetween_prompt, shot.duration_sec, shot.character_ids),
            BeatSpec(layout_end, "", None, shot.character_ids),
        ]
    else:
        open_start, open_end = resolve_video_plan(
            shot.layout, shot.inbetween_prompt, shot.open_start, shot.open_end
        )
        beats = [
            BeatSpec(
                shot.layout,
                shot.inbetween_prompt,
                shot.duration_sec,
                shot.character_ids,
            ),
        ]

    data, sid = build_narrative_sequence(
        data,
        beats=beats,
        setting_id=setting_id,
        style_id=style_id,
        setting_prompt=shot.setting_prompt or setting_prompt,
        style_prompt=shot.style_prompt or style_prompt,
        action_prompt=shot.action_prompt or action_prompt,
        open_start=open_start,
        open_end=open_end,
    )

    seq = data["sequences"][sid]
    vid_order = seq.get("video_order") or []
    if (
        not layout_end
        and open_start
        and open_end
        and len(vid_order) >= 2
        and shot.inbetween_prompt_out.strip()
    ):
        set_video_prompt(
            data,
            sid,
            vid_order[1],
            shot.inbetween_prompt_out,
            duration_sec=shot.duration_sec_out,
        )

    return _ensure_project(data), sid


def build_shots(
    data: dict,
    shots: List[ShotSpec],
    *,
    setting_id: str = "",
    style_id: str = "",
    setting_prompt: str = "",
    style_prompt: str = "",
    action_prompt: str = "",
    clear_placeholders: bool = True,
) -> Tuple[dict, List[str]]:
    """
    Build a project as multiple discrete cuts — **one sequence per shot**.

    Use when the user asks for shots, cuts, scenes, or angles. Do not use
    build_narrative_sequence for that case (it chains keyframes into one oner).
    """
    if not shots:
        raise ValueError("shots must not be empty")
    if clear_placeholders:
        data = remove_placeholder_sequences(data)
    seq_ids: List[str] = []
    for shot in shots:
        data, sid = build_shot(
            data,
            shot,
            setting_id=setting_id,
            style_id=style_id,
            setting_prompt=setting_prompt,
            style_prompt=style_prompt,
            action_prompt=action_prompt,
        )
        seq_ids.append(sid)
    return _ensure_project(data), seq_ids


def build_narrative_sequence(
    data: dict,
    *,
    beats: List[BeatSpec],
    setting_id: str = "",
    style_id: str = "",
    setting_prompt: str = "",
    style_prompt: str = "",
    action_prompt: str = "",
    open_start: bool = False,
    open_end: bool = True,
    seq_id: Optional[str] = None,
) -> Tuple[dict, str]:
    """
    Build ONE continuous sequence (oner): multiple keyframes linked by in-between videos.

    Chaining KF1→KF2→KF3 in a single sequence produces one unbroken video chain at
    export/stitch time. For discrete **shots/cuts**, use build_shots() instead.

    Video[i] receives beats[i].inbetween_prompt (motion leaving beat i's keyframe).
    """
    if not beats:
        raise ValueError("beats must not be empty")

    if seq_id and seq_id in data.get("sequences", {}):
        sid = seq_id
        seq = data["sequences"][sid]
        seq["keyframes"] = {}
        seq["keyframe_order"] = []
        seq["videos"] = {}
        seq["video_order"] = []
    else:
        data, sid = add_sequence(
            data,
            seq_id=seq_id,
            setting_id=setting_id,
            style_id=style_id,
            setting_prompt=setting_prompt,
            style_prompt=style_prompt,
            action_prompt=action_prompt,
            open_start=open_start,
            open_end=open_end,
        )
        seq = data["sequences"][sid]

    seq["setting_id"] = setting_id or ""
    seq["style_id"] = style_id or ""
    seq["setting_prompt"] = setting_prompt
    seq["style_prompt"] = style_prompt
    seq["video_plan"] = {"open_start": open_start, "open_end": open_end}

    for beat in beats:
        data, _ = add_keyframe(
            data,
            sid,
            beat.layout,
            character_ids=beat.character_ids,
        )

    vid_order = seq.get("video_order", [])
    for i, beat in enumerate(beats):
        if i < len(vid_order) and beat.inbetween_prompt:
            set_video_prompt(
                data,
                sid,
                vid_order[i],
                beat.inbetween_prompt,
                duration_sec=beat.duration_sec,
            )

    seq["action_prompt"] = effective_sequence_action_prompt(seq, action_prompt)

    return _ensure_project(data), sid


def _asset_ids(data: dict, list_key: str) -> set:
    return {a.get("id") for a in data.get("project", {}).get(list_key, []) if isinstance(a, dict) and a.get("id")}


def validate_project(data: dict) -> List[str]:
    """Return human-readable validation issues (empty list = OK)."""
    data = _ensure_project(copy.deepcopy(data))
    issues: List[str] = []

    proj = data.get("project", {})
    if not str(proj.get("name") or "").strip():
        issues.append("project.name is empty")

    char_ids = _asset_ids(data, "characters")
    setting_ids = _asset_ids(data, "settings")
    style_ids = _asset_ids(data, "styles")

    seqs = data.get("sequences") or {}
    order = data.get("sequence_order") or []
    if not seqs and order:
        issues.append("sequence_order references sequences but sequences dict is empty")

    for sid in order:
        if sid not in seqs:
            issues.append(f"sequence_order references missing sequence: {sid}")

    for sid, seq in seqs.items():
        if not isinstance(seq, dict):
            issues.append(f"sequence {sid} is not an object")
            continue

        sid_setting = seq.get("setting_id") or ""
        if sid_setting and sid_setting not in setting_ids:
            issues.append(f"sequence {sid}: setting_id {sid_setting!r} not found in project.settings")

        sid_style = seq.get("style_id") or ""
        if sid_style and sid_style not in style_ids:
            issues.append(f"sequence {sid}: style_id {sid_style!r} not found in project.styles")

        kf_order = seq.get("keyframe_order") or []
        keyframes = seq.get("keyframes") or {}
        for kf_id in kf_order:
            if kf_id not in keyframes:
                issues.append(f"sequence {sid}: keyframe_order references missing keyframe {kf_id}")
            else:
                kf = keyframes[kf_id]
                if not str(kf.get("layout") or "").strip():
                    issues.append(f"sequence {sid} keyframe {kf_id}: layout is empty")
                for ci, cid in enumerate(kf.get("characters") or []):
                    if cid and cid not in char_ids:
                        issues.append(f"sequence {sid} keyframe {kf_id}: character slot {ci} id {cid!r} not in project.characters")

        vid_order = seq.get("video_order") or []
        videos = seq.get("videos") or {}
        for vid_id in vid_order:
            if vid_id not in videos:
                issues.append(f"sequence {sid}: video_order references missing video {vid_id}")
            else:
                dur = videos[vid_id].get("duration_override_sec")
                if dur is not None:
                    try:
                        f = float(dur)
                        if not f.is_integer():
                            issues.append(
                                f"sequence {sid} video {vid_id}: duration_override_sec must be a whole "
                                f"number of seconds (got {dur!r})"
                            )
                        else:
                            d = int(f)
                            if d < _CLIP_DURATION_MIN or d > _CLIP_DURATION_MAX:
                                issues.append(
                                    f"sequence {sid} video {vid_id}: duration_override_sec {d} "
                                    f"outside allowed range {_CLIP_DURATION_MIN}-{_CLIP_DURATION_MAX}"
                                )
                    except (TypeError, ValueError):
                        issues.append(
                            f"sequence {sid} video {vid_id}: invalid duration_override_sec {dur!r}"
                        )

        from editor_helpers import _compute_required_gaps

        expected_gaps = len(_compute_required_gaps(seq))
        if len(vid_order) != expected_gaps:
            issues.append(
                f"sequence {sid}: video count {len(vid_order)} != required gaps {expected_gaps} "
                f"(keyframe_order={kf_order}, video_plan={seq.get('video_plan')})"
            )

        if sequence_video_count(seq) < 2 and str(seq.get("action_prompt") or "").strip():
            issues.append(
                f"sequence {sid}: action_prompt is set but sequence has only one video — "
                "move narrative motion to video.inbetween_prompt; action_prompt is for "
                "multi-clip consistency only (e.g. '2 beats per second dance')"
            )

        plan = seq.get("video_plan") or {}
        os_plan = bool(plan.get("open_start", False))
        oe_plan = bool(plan.get("open_end", True))
        n_kf = len(kf_order)
        n_vid = len(vid_order)
        if n_kf == 1 and not os_plan and not oe_plan:
            issues.append(
                f"sequence {sid}: open_start and open_end are both false with one keyframe — "
                "no video gap; use layout_end on ShotSpec for a KF→KF clip, or enable open_start/open_end"
            )
        if n_kf == 1 and os_plan and oe_plan and n_vid >= 2:
            if not str((videos.get(vid_order[1]) or {}).get("inbetween_prompt") or "").strip():
                issues.append(
                    f"sequence {sid}: both open_start and open_end with one keyframe needs "
                    "inbetween prompts on both videos (inbetween_prompt + inbetween_prompt_out)"
                )

    return issues


def _get_nested(obj: dict, parts: List[str]) -> Any:
    cur: Any = obj
    for p in parts:
        if not isinstance(cur, dict):
            raise KeyError(".".join(parts))
        cur = cur[p]
    return cur


def _set_nested(obj: dict, parts: List[str], value: Any) -> None:
    cur = obj
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def patch_field(data: dict, dot_path: str, value: Any) -> dict:
    """Set a single field by dot path, e.g. sequences.seq1.keyframes.id2.layout."""
    data = copy.deepcopy(data)
    parts = [p for p in dot_path.split(".") if p]
    if not parts:
        raise ValueError("dot_path must not be empty")
    _set_nested(data, parts, value)
    return _ensure_project(data)


def clone_project_from_host(
    host_path: PathLike,
    new_name: str,
    *,
    dest_path: PathLike | None = None,
) -> tuple[dict, Path]:
    """
    Create a new project JSON with blank sequences but full host project globals.

    Copies characters, settings, styles (including reference_image and pinned asset
    data), models, generation settings, and other project-level fields.
    Does not copy sequences, sequence_order, or per-beat generation state.
    Host file is read-only — never modified.
    """
    host = load_project(host_path)
    slug = (new_name or "untitled").strip().replace(" ", "-").lower() or "untitled"
    if dest_path is None:
        try:
            cfg = load_config()
            workspace = Path(cfg.get("paths", {}).get("workspace", "./projects"))
            if not workspace.is_absolute():
                workspace = REPO_ROOT / workspace
        except Exception:
            workspace = REPO_ROOT / "projects"
        dest = workspace / f"{slug}.json"
    else:
        dest = Path(dest_path)

    new = create_blank(new_name)
    host_proj = copy.deepcopy(host.get("project") or {})
    host_proj["name"] = new_name.strip() or "untitled"
    host_proj["is_protected_from_empty_save"] = True
    new["project"] = host_proj
    save_project(dest, new)

    try:
        from client.workspace import scaffold_project_files_dir  # noqa: WPS433

        scaffold_project_files_dir(dest.parent, dest.stem)
    except Exception:
        pass  # sibling -files/ folder is a convenience, not load-bearing

    return new, dest


def preserve_generation_fields(old: dict, new: dict) -> dict:
    """
    Merge generation/runtime fields from old into new.

    Preserves selected paths, poses, reference bindings, and controlnet settings
    when the same entity id exists in both projects.
    """
    old = _ensure_project(copy.deepcopy(old))
    new = _ensure_project(copy.deepcopy(new))

    def _merge_assets(list_key: str) -> None:
        old_by_id = {a["id"]: a for a in old.get("project", {}).get(list_key, []) if isinstance(a, dict) and a.get("id")}
        for asset in new.get("project", {}).get(list_key, []):
            if not isinstance(asset, dict):
                continue
            oid = asset.get("id")
            if oid in old_by_id:
                for key in _GENERATION_PRESERVE_ASSET:
                    if key in old_by_id[oid] and old_by_id[oid][key]:
                        asset[key] = old_by_id[oid][key]

    _merge_assets("characters")
    _merge_assets("settings")
    _merge_assets("styles")

    old_seqs = old.get("sequences") or {}
    new_seqs = new.get("sequences") or {}
    for sid, nseq in new_seqs.items():
        oseq = old_seqs.get(sid)
        if not isinstance(oseq, dict):
            continue
        for kf_id, nkf in (nseq.get("keyframes") or {}).items():
            okf = (oseq.get("keyframes") or {}).get(kf_id)
            if not isinstance(okf, dict):
                continue
            for key in _GENERATION_PRESERVE_KEYFRAME:
                if key in okf and okf[key] not in (None, "", {}):
                    nkf[key] = copy.deepcopy(okf[key])
        for vid_id, nvid in (nseq.get("videos") or {}).items():
            ovid = (oseq.get("videos") or {}).get(vid_id)
            if not isinstance(ovid, dict):
                continue
            for key in _GENERATION_PRESERVE_VIDEO:
                if key in ovid and ovid[key]:
                    nvid[key] = ovid[key]

    return _ensure_project(new)


def recommend_model_family(
    brief: str = "",
    *,
    num_characters: int = 1,
    needs_reference_images: bool = False,
) -> str:
    """
    Recommend 'default' or 'custom' image model family for a project brief.

    Custom when explicit reference-image workflow is needed; otherwise Default for
    standard pose-driven 0–2 character narratives.
    """
    _ = brief  # reserved for future keyword heuristics
    if needs_reference_images:
        return IMAGE_MODEL_FAMILY_CUSTOM
    if num_characters > 2:
        return IMAGE_MODEL_FAMILY_CUSTOM
    return IMAGE_MODEL_FAMILY_DEFAULT


def recommend_video_model_family(
    brief: str = "",
    *,
    needs_custom_video_workflow: bool = False,
) -> str:
    """
    Recommend 'default' or 'custom' video model family for a project brief.

    Custom when a non-Wan workflow (LTX, fun_inpaint, BYO export) is required.
    """
    if needs_custom_video_workflow:
        return VIDEO_MODEL_FAMILY_CUSTOM
    brief_lower = (brief or "").lower()
    custom_keywords = ("ltx", "fun_inpaint", "byo", "custom video", "custom workflow", "ltx2")
    if any(k in brief_lower for k in custom_keywords):
        return VIDEO_MODEL_FAMILY_CUSTOM
    return VIDEO_MODEL_FAMILY_DEFAULT


def summarize_project(data: dict) -> str:
    """Human-readable storyboard summary for CLI / agent review."""
    data = _ensure_project(copy.deepcopy(data))
    lines: List[str] = []
    pname = data.get("project", {}).get("name", "")
    lines.append(f"Project: {pname}")
    lines.append(f"Model family: {data.get('project', {}).get('image_model_family', IMAGE_MODEL_FAMILY_DEFAULT)}")
    lines.append(
        f"Video family: {data.get('project', {}).get('video_model_family', VIDEO_MODEL_FAMILY_DEFAULT)}"
    )
    lines.append("")

    proj = data.get("project", {})
    for label, key in (("Characters", "characters"), ("Settings", "settings"), ("Styles", "styles")):
        items = proj.get(key) or []
        if items:
            lines.append(f"{label}:")
            for a in items:
                if isinstance(a, dict):
                    lines.append(f"  - {a.get('name', '?')} ({str(a.get('id', ''))[:8]}...)")
            lines.append("")

    for sid in data.get("sequence_order") or []:
        seq = data.get("sequences", {}).get(sid)
        if not isinstance(seq, dict):
            continue
        lines.append(f"Sequence {sid}:")
        if seq.get("setting_id"):
            lines.append(f"  setting_id: {seq['setting_id']}")
        if seq.get("style_id"):
            lines.append(f"  style_id: {seq['style_id']}")
        if seq.get("setting_prompt"):
            sp = str(seq["setting_prompt"])
            lines.append(f"  setting_prompt: {sp[:80]}..." if len(sp) > 80 else f"  setting_prompt: {sp}")
        if seq.get("style_prompt"):
            sp = str(seq["style_prompt"])
            lines.append(f"  style_prompt: {sp[:80]}..." if len(sp) > 80 else f"  style_prompt: {sp}")
        if seq.get("action_prompt"):
            ap = str(seq["action_prompt"])
            lines.append(f"  action_prompt: {ap[:80]}..." if len(ap) > 80 else f"  action_prompt: {ap}")
        lines.append(f"  video_plan: {seq.get('video_plan')}")

        kf_order = seq.get("keyframe_order") or []
        vid_order = seq.get("video_order") or []
        for i, kf_id in enumerate(kf_order):
            kf = (seq.get("keyframes") or {}).get(kf_id, {})
            layout = str(kf.get("layout") or "")[:100]
            chars = kf.get("characters") or []
            lines.append(f"  KF {kf_id}: {layout!r} chars={chars}")
            if i < len(vid_order):
                vid = (seq.get("videos") or {}).get(vid_order[i], {})
                motion = str(vid.get("inbetween_prompt") or "")[:100]
                dur = vid.get("duration_override_sec")
                dur_s = f" ({dur}s)" if dur is not None else ""
                lines.append(f"    -> video {vid_order[i]}{dur_s}: {motion!r}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
