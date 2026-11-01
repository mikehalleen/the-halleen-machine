"""Submit-time prompt assembly: handles to workflow tokens, slot paths, prelude, warnings.

Token notation is derived from the workflow graph (``derive_token_profile``):

- a known partner/API node class (``API_TOKEN_NOTATION``) gives that API's tokens,
  for example ``<Picture {n}>`` and ``<Video {n}>``;
- otherwise a linear ``ReferenceLatent`` chain gives ``image{n}``;
- otherwise the workflow is positional: handles in the prompt become the referenced
  bin's plain name and no token warnings are raised.

Slot counts and node ids come from scanning the graph for ``THM-ImageReference``
and ``THM-VideoReference`` tags. A sidecar ``workflows/<name>.thm.toml`` beside
the workflow JSON is an override only: fields it sets win, and its ``[limits]``
can clip slot counts but never grant slots the graph does not have.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .models import Bin, GenerationReference, Kind, Project
from .resolver import ResolvedReference
from .slug import all_handles, handles_in, rewrite_tokens
from .workflow_names import WORKFLOWS_DIR, resolve_curated_workflow, workflow_kind_for_path

# Partner/API node classes whose prompts address references by token: (image, video).
# Checked against every node's class_type; the node need not carry THM-PartnerNode.
API_TOKEN_NOTATION: dict[str, tuple[str | None, str | None]] = {
    "MiniMaxH3ReferenceToVideo": ("<Picture {n}>", "<Video {n}>"),
}

# Notation for a linear ReferenceLatent stack (Klein-style image edit workflows).
REF_STACK_IMAGE_NOTATION = "image{n}"


@dataclass
class TokenProfile:
    image: str | None = None
    video: str | None = None
    image_slots: int | None = None
    video_slots: int | None = None
    prelude: str | None = None
    found: bool = False  # carries token notation from any source (graph or sidecar)
    sidecar: bool = False  # a sidecar file contributed to this profile

    @property
    def addresses_by_token(self) -> bool:
        return bool(self.image or self.video)


def sidecar_path(workflow_name: str, workflows_dir: Path | None = None) -> Path:
    """A sidecar sits beside its workflow — which since the split lives in workflows_image/ or
    workflows_video/, not in the flat folder. Resolve the workflow first and use its own parent;
    fall back to the anchor dir only when the workflow itself cannot be found."""
    from . import workflow_capabilities as caps

    base = Path(workflows_dir or WORKFLOWS_DIR)
    name = Path(str(workflow_name)).name
    stem = name[:-5] if name.lower().endswith(".json") else name
    resolved = resolve_curated_workflow(workflow_name, base)
    return (resolved.parent if resolved else base) / f"{stem}.thm.toml"


def derive_token_profile(workflow: dict) -> TokenProfile:
    """Token notation from the loaded graph. Pure; prints nothing."""
    from . import workflow_capabilities as caps

    classes = {caps.wc.node_class(n) for n in workflow.values() if isinstance(n, dict)}
    image = video = None
    for cls, (img, vid) in API_TOKEN_NOTATION.items():
        if cls in classes:
            image, video = img, vid
            break
    if image is None and caps.wc.discover_reference_wiring_order(workflow).tier == "linear_ref_stack":
        image = REF_STACK_IMAGE_NOTATION
    return TokenProfile(image=image, video=video, found=bool(image or video))


def read_sidecar(path: Path) -> TokenProfile:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    tokens = data.get("tokens") or {}
    limits = data.get("limits") or {}
    prelude = data.get("prelude") or {}

    def _int(v):
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    return TokenProfile(
        image=str(tokens["image"]) if tokens.get("image") else None,
        video=str(tokens["video"]) if tokens.get("video") else None,
        image_slots=_int(limits.get("image_slots")),
        video_slots=_int(limits.get("video_slots")),
        prelude=str(prelude["line"]) if prelude.get("line") else None,
        found=True,
        sidecar=True,
    )


def merge_token_profiles(base: TokenProfile, override: TokenProfile) -> TokenProfile:
    """Field by field: an override value that is set wins; unset fields keep the base."""
    merged = replace(base)
    for name in ("image", "video", "image_slots", "video_slots", "prelude"):
        value = getattr(override, name)
        if value is not None:
            setattr(merged, name, value)
    merged.sidecar = base.sidecar or override.sidecar
    merged.found = bool(merged.image or merged.video) or merged.sidecar
    return merged


def load_token_profile(workflow_name: str, workflows_dir: Path | None = None) -> TokenProfile:
    """Derive from the graph, then merge the sidecar on top when one exists."""
    from . import workflow_capabilities as caps

    base = Path(workflows_dir or WORKFLOWS_DIR)
    profile = TokenProfile()
    path = resolve_curated_workflow(workflow_name, base)
    if path:
        try:
            profile = derive_token_profile(caps.load_workflow_dict(path))
        except Exception:  # a broken JSON is reported by workflow_capacity, not here
            profile = TokenProfile()
    sc = sidecar_path(workflow_name, base)
    if sc.is_file():
        profile = merge_token_profiles(profile, read_sidecar(sc))
    return profile


@dataclass
class Capacity:
    found: bool = False
    image_slots: int = 0
    video_slots: int = 0
    supports_start_frame: bool = False
    supports_end_frame: bool = False
    slot_node_ids: list[str] = field(default_factory=list)  # image slots, wiring order
    video_slot_node_ids: list[str] = field(default_factory=list)  # THM-VideoReference, node-id order
    addresses_by_token: bool = False
    kind: Kind = "image"  # what the workflow makes: the folder it sits in, else guessed from the graph
    error: str | None = None

    def slots_for(self, media_type: str) -> int:
        return self.image_slots if media_type == "image" else self.video_slots


def workflow_capacity(workflow_name: str, workflows_dir: Path | None = None) -> Capacity:
    """Reference capacity from the graph scan, clipped by the sidecar limits."""
    from . import workflow_capabilities as caps

    base = Path(workflows_dir or WORKFLOWS_DIR)
    path = resolve_curated_workflow(workflow_name, base)
    if not path:
        return Capacity(found=False, error="workflow not found")
    try:
        workflow = caps.load_workflow_dict(path)
        slots = caps.wc.discover_image_reference_slots(workflow)
        video_slots_found = caps.wc.discover_video_reference_slots(workflow)
        frames = caps.wc.discover_video_frame_input_support(workflow)
    except Exception as exc:  # a broken JSON must refuse, not crash the command layer
        return Capacity(found=False, error=str(exc))
    profile = load_token_profile(workflow_name, base)
    kind = workflow_kind_for_path(path, base) or _guess_kind(str(path), base)
    node_ids = [s.node_id for s in slots]
    image_slots = len(node_ids)
    if image_slots == 0:
        image_slots = int(frames.supports_start_frame) + int(frames.supports_end_frame)
    if profile.image_slots is not None:
        image_slots = min(image_slots, profile.image_slots)
    video_ids = [s.node_id for s in video_slots_found]
    video_slots = len(video_ids)
    if profile.video_slots is not None:
        video_slots = min(video_slots, profile.video_slots)
    return Capacity(
        found=True,
        image_slots=image_slots,
        video_slots=video_slots,
        supports_start_frame=bool(frames.supports_start_frame),
        supports_end_frame=bool(frames.supports_end_frame),
        slot_node_ids=node_ids,
        video_slot_node_ids=video_ids,
        addresses_by_token=profile.addresses_by_token,
        kind=kind,
    )


def _guess_kind(path: str, workflows_dir: Path) -> Kind:
    """For a file outside the kind folders: a video generator or a video save node makes it
    video, and image is what is left over."""
    from . import workflow_capabilities as caps

    try:
        vscan = caps.scan_video_workflow_file(path, workflows_dir)
    except Exception:
        return "image"
    video = vscan.video if not vscan.error else None
    return "video" if video is not None and (video.has_video_generator or video.has_save_video) else "image"


@dataclass
class AssemblyWarning:
    kind: str  # "unknown_token" | "unused_handle" | "missing_file"
    handle: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "handle": self.handle, "detail": self.detail}


def merge_negatives(*texts: str) -> str:
    """Comma-separated negatives merged in order, duplicates dropped case-insensitively."""
    seen: set[str] = set()
    parts: list[str] = []
    for text in texts:
        for raw in str(text or "").split(","):
            part = raw.strip()
            if part and part.lower() not in seen:
                seen.add(part.lower())
                parts.append(part)
    return ", ".join(parts)


@dataclass
class AssembledPrompt:
    prompt_typed: str
    prompt_submitted: str
    negative_typed: str
    negative_submitted: str
    references: list[GenerationReference]
    paths_by_slot: dict[str, str]
    prelude: list[str]
    warnings: list[AssemblyWarning]


def _slot_for(kind: Kind, resolved: ResolvedReference, capacity: Capacity | None) -> str | None:
    if resolved.ordinal is None or resolved.media is None:
        return None
    n = resolved.ordinal
    if resolved.media.type != "image":
        ids = capacity.video_slot_node_ids if capacity else []
        return ids[n - 1] if n <= len(ids) else None
    if capacity and capacity.slot_node_ids:
        return capacity.slot_node_ids[n - 1] if n <= len(capacity.slot_node_ids) else None
    if kind == "video":
        return "start" if n == 1 else "end" if n == 2 else None
    return None


def assemble(
    project: Project,
    bin_: Bin,
    resolved: list[ResolvedReference],
    *,
    workflows_dir: Path | None = None,
    profile: TokenProfile | None = None,
    capacity: Capacity | None = None,
) -> AssembledPrompt:
    profile = profile or load_token_profile(bin_.workflow, workflows_dir)
    capacity = capacity or workflow_capacity(bin_.workflow, workflows_dir)
    kind = capacity.kind

    mapping: dict[str, str] = {}
    references: list[GenerationReference] = []
    paths_by_slot: dict[str, str] = {}
    prelude: list[str] = []
    warnings: list[AssemblyWarning] = []

    for r in resolved:
        bin_name = r.bin.name if r.bin else r.handle
        fmt = None
        if r.media is not None and r.ordinal is not None:
            fmt = profile.image if r.media.type == "image" else profile.video
        mapping[r.handle] = fmt.format(n=r.ordinal) if fmt else bin_name
        slot = _slot_for(kind, r, capacity)
        references.append(
            GenerationReference(ref=r.reference.id, media=r.reference.media, handle=r.handle, slot=slot, ordinal=r.ordinal)
        )
        if slot and r.path:
            paths_by_slot[slot] = r.path
        if r.ordinal is not None and profile.prelude:
            prelude.append(
                profile.prelude.format(
                    n=r.ordinal,
                    item=bin_name,  # the sidecar placeholder keeps its schema-3 name
                    handle=r.handle,
                    kind="",
                    type=(r.media.type if r.media else ""),
                )
            )
        if not r.exists:
            warnings.append(AssemblyWarning("missing_file", r.handle, r.note or "missing file"))

    prompt_submitted = rewrite_tokens(bin_.prompt, mapping, keep_dot=False)
    negative_submitted = rewrite_tokens(bin_.negative, mapping, keep_dot=False)

    # the project's global prompts, applied at generation time: positive after the bin's
    # prompt, negative merged with it; both literal, never token-rewritten
    glob_prompt = project.defaults.prompt_for(kind)
    glob_negative = project.defaults.negative_for(kind)
    if glob_prompt:
        prompt_submitted = f"{prompt_submitted}\n{glob_prompt}" if prompt_submitted.strip() else glob_prompt
    if glob_negative:
        negative_submitted = merge_negatives(negative_submitted, glob_negative)

    # the bin's LoRAs go in front as <lora:...> tags: the runner lifts them out of the text
    # again and builds loader nodes, so the typed prompt never has to carry them
    tags = " ".join(lora.tag() for lora in bin_.loras if lora.name.strip())
    if tags:
        prompt_submitted = f"{tags} {prompt_submitted}" if prompt_submitted.strip() else tags

    known = all_handles(bin_.references)
    used = handles_in(bin_.prompt) | handles_in(bin_.negative)
    for h in sorted(used - known):
        warnings.append(AssemblyWarning("unknown_token", h, f".{h} matches no reference in this bin"))
    if profile.addresses_by_token:
        for ref in bin_.references:
            if ref.handle not in used:
                warnings.append(AssemblyWarning("unused_handle", ref.handle, f".{ref.handle} appears nowhere in the prompt"))

    return AssembledPrompt(
        prompt_typed=bin_.prompt,
        prompt_submitted=prompt_submitted,
        negative_typed=bin_.negative,
        negative_submitted=negative_submitted,
        references=references,
        paths_by_slot=paths_by_slot,
        prelude=prelude,
        warnings=warnings,
    )
