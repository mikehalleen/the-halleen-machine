"""Recipes read back out of the media itself (design: media first).

ComfyUI writes the API graph it executed into every output it saves: a PNG carries it in a
``prompt`` text chunk, a video in a ``prompt`` format tag. THM's runners add their own
snapshot beside it when they land a take (``the_machine_snapshot`` / ``comment``), which
names the generation. The generation log is a cache of that. When a file turns up without a
record -- the output of a render THM gave up on, uploaded by hand; a file found in the output
tree -- the record is rebuilt from what the file carries: the workflow (the graph's node set
against the curated workflow files), the params (seed, canvas, frames and rate, steps), the
references (loader paths mapped back to project media), the prompt (tokens turned back into
the bin's handles), and, when a failed or cancelled generation in the log made it, the file
is attached to that record instead of a fresh one being minted.

Nothing here talks to ComfyUI or sleeps; ffprobe reads video tags, Pillow reads PNG chunks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from . import ids
from .assembler import Capacity, TokenProfile, load_token_profile, workflow_capacity
from .ids import utc_now
from .media import media_type_for
from .models import Bin, Generation, GenerationReference, Lora, Media, MediaOrigin, Project, Reference
from .slug import assign_handle
from .store import ProjectStore
from .workflow_names import resolve_curated_workflow, workflow_kind_dirs

# the runner side of these lives in scripts/workflow_controls.py (CONTROL_TAGS)
IMAGE_REF_TITLES = ("THM-ImageReference", "THM-ReferenceImage")
VIDEO_REF_TITLES = ("THM-VideoReference", "THM-ReferenceVideo")
FRAME_TITLES = {"THM-StartFrame": "start", "THM-StartImage": "start", "THM-EndFrame": "end", "THM-EndImage": "end"}
LORA_MARKER_TITLES = ("THM-Lora", "THM-LoraAfterThisNode", "THM-Lora-High", "THM-Lora-Low")
LOADER_KEYS = {"LoadImage": "image", "LoadImageMask": "image", "VHS_LoadVideo": "video", "VHS_LoadVideoPath": "video", "LoadVideo": "file"}
LORA_CLASSES = ("LoraLoader", "LoraLoaderModelOnly")
TEXT_KEYS = ("text", "value", "string")
COMFY_UPLOAD_SUBFOLDER = "thm-refs"  # workflow_controls._COMFY_UPLOAD_SUBFOLDER
MIN_WORKFLOW_COVERAGE = 0.6  # a candidate needs this share of its own nodes present in the graph

_ANNOTATED = re.compile(r"^(.*?)\s*\[(output|input|temp)\]$")
_UPLOAD_NAME = re.compile(r"^(?P<stem>.+)_(?P<digest>[0-9a-f]{10})$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


# -- reading the file -------------------------------------------------------------


@dataclass
class Embedded:
    """What one file carries: ComfyUI's graph and, for a take THM landed, THM's snapshot."""

    graph: dict[str, dict] | None = None
    snapshot: dict[str, Any] | None = None

    @property
    def empty(self) -> bool:
        return self.graph is None and self.snapshot is None


def _graph_from(text: Any) -> dict[str, dict] | None:
    if not isinstance(text, (str, bytes)) or not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    nodes = {str(k): v for k, v in data.items() if isinstance(v, dict) and v.get("class_type")}
    return nodes or None


def _snapshot_from(text: Any) -> dict[str, Any] | None:
    """THM's own snapshot, the ``thm`` object the 2.0 runners write. A 1.x snapshot (no
    ``thm`` key) says nothing a 2.0 record can use and reads as none."""
    if not isinstance(text, (str, bytes)) or not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    inner = data.get("thm") if isinstance(data, dict) else None
    return inner if isinstance(inner, dict) else None


def _read_png(path: Path) -> Embedded:
    try:
        with Image.open(path) as im:
            info = dict(im.info)
    except Exception:
        return Embedded()
    return Embedded(graph=_graph_from(info.get("prompt")), snapshot=_snapshot_from(info.get("the_machine_snapshot")))


def _read_video(path: Path) -> Embedded:
    exe = shutil.which("ffprobe")
    if not exe:
        return Embedded()
    cmd = [exe, "-v", "error", "-show_entries", "format_tags=prompt,comment", "-of", "json", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return Embedded()
    fmt = data.get("format") if isinstance(data, dict) else None
    tags = fmt.get("tags") if isinstance(fmt, dict) else None
    if not isinstance(tags, dict):
        return Embedded()
    return Embedded(graph=_graph_from(tags.get("prompt")), snapshot=_snapshot_from(tags.get("comment")))


def read_embedded(path) -> Embedded:
    """The graph and snapshot a file carries; empty for a file with neither, or one that is gone."""
    p = Path(str(path))
    if not p.is_file():
        return Embedded()
    kind = media_type_for(p)
    if kind == "image":
        return _read_png(p)
    if kind == "video":
        return _read_video(p)
    return Embedded()


# -- reading the graph ------------------------------------------------------------


def _title(node: dict) -> str:
    meta = node.get("_meta")
    return str(meta.get("title") or "") if isinstance(meta, dict) else ""


def _cls(node: dict) -> str:
    return str(node.get("class_type") or "")


def _inputs(node: dict) -> dict:
    ins = node.get("inputs")
    return ins if isinstance(ins, dict) else {}


def _value(graph: dict, node: dict, key: str) -> Any:
    """A literal input, or the ``value`` of the primitive node a link points at."""
    v = _inputs(node).get(key)
    if isinstance(v, list):
        if len(v) >= 1:
            producer = graph.get(str(v[0]))
            if isinstance(producer, dict) and "value" in _inputs(producer):
                return _inputs(producer)["value"]
        return None
    return v


def _tagged(graph: dict, titles) -> list[tuple[str, dict]]:
    wanted = {t.lower() for t in titles}
    return [(nid, n) for nid, n in graph.items() if _title(n).strip().lower() in wanted]


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        return float(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _first(graph: dict, titles, keys, convert):
    """The tagged node's value first; else the first node in the graph whose inputs carry one of ``keys``."""
    for _, node in _tagged(graph, titles):
        for key in ("value",) + tuple(keys):
            got = convert(_value(graph, node, key))
            if got is not None:
                return got
    for nid in sorted(graph, key=lambda k: (len(k), k)):
        node = graph[nid]
        for key in keys:
            if key in _inputs(node):
                got = convert(_value(graph, node, key))
                if got is not None:
                    return got
    return None


def _text(graph: dict, titles) -> str:
    for _, node in _tagged(graph, titles):
        for key in TEXT_KEYS:
            v = _value(graph, node, key)
            if isinstance(v, str):
                return v
    return ""


@dataclass
class GraphRecipe:
    """The parts of a ComfyUI API graph a THM record is made of."""

    seed: int | None = None
    width: int | None = None
    height: int | None = None
    frames: int | None = None
    fps: float | None = None
    steps: int | None = None
    prompt: str = ""
    negative: str = ""
    image_loaders: dict[str, str] = field(default_factory=dict)  # slot node id -> loader value
    video_loaders: dict[str, str] = field(default_factory=dict)
    frame_loaders: dict[str, str] = field(default_factory=dict)  # "start"/"end" -> loader value
    loras: list[Lora] = field(default_factory=list)  # the loaders THM injected for the recipe's LoRA tags
    marker_loras: dict[str, Lora] = field(default_factory=dict)  # THM-Lora markers, by node id: the workflow's own unless changed
    nodes: set[tuple[str, str]] = field(default_factory=set)  # (node id, class type)


def _loader_value(node: dict) -> str | None:
    key = LOADER_KEYS.get(_cls(node))
    if key is None:
        return None
    v = _inputs(node).get(key)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _lora_of(node: dict) -> Lora | None:
    ins = _inputs(node)
    name = str(ins.get("lora_name") or "").strip()
    if not name:
        return None
    strength = _float(ins.get("strength_model"))
    clip = _float(ins.get("strength_clip"))
    return Lora(name=name, strength=strength if strength is not None else 1.0, strength_b=clip)


def parse_graph(graph: dict[str, dict]) -> GraphRecipe:
    """Pure: the THM-tagged controls first, plain node inputs when a graph is untagged."""
    rec = GraphRecipe()
    rec.nodes = {(nid, _cls(n)) for nid, n in graph.items()}
    rec.seed = _first(graph, ("THM-Seed",), ("seed", "noise_seed"), _int)
    rec.steps = _first(graph, ("THM-Steps",), ("steps",), _int)
    rec.width = _first(graph, ("THM-Width", "THM-ImageSize"), ("width",), _int)
    rec.height = _first(graph, ("THM-Height", "THM-ImageSize"), ("height",), _int)
    rec.frames = _first(graph, ("THM-FrameCount",), ("length", "num_frames", "frame_count"), _int)
    rec.fps = _first(graph, ("THM-FrameRate", "THM-FPS"), ("fps", "frame_rate"), _float)
    rec.prompt = _text(graph, ("THM-Prompt",))
    rec.negative = _text(graph, ("THM-NegativePrompt",))
    for nid, node in _tagged(graph, IMAGE_REF_TITLES):
        v = _loader_value(node)
        if v:
            rec.image_loaders[nid] = v
    for nid, node in _tagged(graph, VIDEO_REF_TITLES):
        v = _loader_value(node)
        if v:
            rec.video_loaders[nid] = v
    for nid, node in graph.items():
        side = FRAME_TITLES.get(_title(node).strip())
        v = _loader_value(node) if side else None
        if side and v and side not in rec.frame_loaders:
            rec.frame_loaders[side] = v
    highs: list[Lora] = []
    lows: list[float] = []
    for nid in sorted(graph, key=lambda k: (len(k), k)):
        node = graph[nid]
        if _cls(node) not in LORA_CLASSES:
            continue
        title = _title(node)
        lora = _lora_of(node)
        if lora is None:
            continue
        if title in LORA_MARKER_TITLES:
            rec.marker_loras[nid] = lora
        elif title.startswith("Injected_Low_"):
            lows.append(lora.strength)
        elif title.startswith("Injected_High_"):
            highs.append(lora)
        elif title.startswith("Injected_"):
            rec.loras.append(lora)
    for i, lora in enumerate(highs):  # a dual-lane pair reads as one LoRA: high strength, low strength_b
        rec.loras.append(Lora(name=lora.name, strength=lora.strength, strength_b=lows[i] if i < len(lows) else None))
    return rec


# -- which workflow --------------------------------------------------------------


@dataclass
class WorkflowMatch:
    name: str
    path: Path
    kind: str
    score: float
    graph: dict[str, dict]


def _workflow_nodes(graph: dict) -> set[tuple[str, str]]:
    return {(str(nid), _cls(n)) for nid, n in graph.items() if isinstance(n, dict) and n.get("class_type")}


def match_workflow(graph: dict[str, dict], workflows_dir, *, prefer: list[str] = ()) -> WorkflowMatch | None:
    """The curated workflow whose node set the graph came from.

    The runner adds nodes (injected LoRAs, an upscaler) and drops some (loaders nobody bound),
    so the match is the best overlap of ``(node id, class type)`` pairs, not equality: a
    candidate has to show most of its own nodes in the graph, and the one sharing the largest
    part of the union wins. Two files that differ only in orphan nodes score the same;
    ``prefer`` (the bin's workflow, the project's default) breaks the tie, then the name.
    """
    from . import workflow_capabilities as caps

    have = _workflow_nodes(graph)
    if not have:
        return None
    ranked: list[tuple[float, int, str, WorkflowMatch]] = []
    prefer_ix: dict[str, int] = {}
    for i, p in enumerate(prefer):
        if p:
            prefer_ix.setdefault(Path(p).name, i)
    for kind, folder in workflow_kind_dirs(workflows_dir).items():
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                wf = caps.load_workflow_dict(path)
            except Exception:
                continue
            theirs = _workflow_nodes(wf)
            if not theirs:
                continue
            shared = len(have & theirs)
            if shared / len(theirs) < MIN_WORKFLOW_COVERAGE:
                continue
            score = shared / len(have | theirs)
            ranked.append((score, prefer_ix.get(path.name, len(prefer) + 1), path.name, WorkflowMatch(path.name, path, kind, score, wf)))
    if not ranked:
        return None
    ranked.sort(key=lambda t: (-t[0], t[1], t[2]))
    return ranked[0][3]


# -- which media -------------------------------------------------------------------


def _norm(path: str) -> str:
    return Path(str(path)).as_posix().lower()


def _upload_digest(path: str) -> str:
    """The fingerprint the runner stamps on a file it uploads into ComfyUI's input dir
    (``workflow_controls._upload_image_to_comfy_input``)."""
    try:
        info = os.stat(path)
    except OSError:
        return ""
    return hashlib.sha1(f"{os.path.abspath(path)}|{info.st_mtime_ns}|{info.st_size}".encode("utf-8")).hexdigest()[:10]


def _upload_stem(path: str) -> str:
    return _UNSAFE.sub("_", Path(path).stem)[:48] or "ref"


def resolve_loader(value: str, project: Project, output_root: str | None = None) -> Media | None:
    """The project media a loader's path names, or None.

    ``rel [output]`` is a file under ComfyUI's output tree: matched by path suffix, so a
    project whose output root moved (or is not configured) still resolves, and a bin that was
    deleted since still resolves through the media table. ``thm-refs/<stem>_<digest>.ext [input]``
    is a file the runner uploaded: matched by the sanitised stem, the digest deciding between
    look-alikes. A bare absolute path is compared as is.
    """
    v = str(value or "").strip()
    if not v:
        return None
    m = _ANNOTATED.match(v)
    rel, where = (m.group(1).strip(), m.group(2)) if m else (v, None)
    media = list(project.media_by_id.values())
    if where == "output" or (where is None and not Path(rel).is_absolute()):
        target = _norm(rel)
        if output_root:
            joined = _norm(Path(output_root) / rel)
            for md in media:
                if _norm(md.path) == joined:
                    return md
        for md in media:
            if _norm(md.path).endswith("/" + target):
                return md
        return None
    if where == "input":
        name = Path(rel)
        head = name.parent.as_posix()
        if head != COMFY_UPLOAD_SUBFOLDER:
            return None
        parsed = _UPLOAD_NAME.match(name.stem)
        if not parsed:
            return None
        stem, digest, suffix = parsed.group("stem"), parsed.group("digest"), name.suffix.lower()
        candidates = [md for md in media if Path(md.path).suffix.lower() == suffix and _upload_stem(md.path) == stem]
        if len(candidates) == 1:
            return candidates[0]
        for md in candidates:
            if _upload_digest(md.path) == digest:
                return md
        return None
    if where is None:
        target = _norm(rel)
        for md in media:
            if _norm(md.path) == target:
                return md
    return None


def _media_by_path(project: Project, path: str) -> Media | None:
    target = _norm(path)
    for md in project.media_by_id.values():
        if _norm(md.path) == target:
            return md
    return None


# -- the record --------------------------------------------------------------------


@dataclass
class Recovery:
    generation: Generation
    attached: bool  # a failed or cancelled record in the log was completed, rather than a new one minted
    workflow: WorkflowMatch | None
    warnings: list[str] = field(default_factory=list)


def _bin_holding(project: Project, media: Media) -> Bin | None:
    for b in project.bins_by_id.values():
        if media.id in b.media:
            return b
    return project.bins_by_id.get(media.origin.bin) if media.origin.bin else None


def _untokenize(text: str, fmt: str | None, handles: list[str | None]) -> str:
    """``<Picture 2>`` back to ``.mara.2``: the ordinal is the position in the slot order, the
    same order the assembler numbered them in. Tokens with no handle stay as written."""
    if not text or not fmt or "{n}" not in fmt:
        return text
    pattern = "(?<![A-Za-z0-9])" + re.escape(fmt).replace(r"\{n\}", r"(\d+)") + "(?![A-Za-z0-9])"

    def swap(m: re.Match) -> str:
        n = int(m.group(1))
        handle = handles[n - 1] if 0 < n <= len(handles) else None
        return f".{handle}" if handle else m.group(0)

    return re.sub(pattern, swap, text)


def _same_prompt(a: str, b: str) -> bool:
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


def _attachable(gens: list[Generation], bin_id: str, seed: int | None, prompt: str, workflow: str | None) -> Generation | None:
    """The failed or cancelled generation in this bin this file is the output of: the seed
    decides, the prompt (then the workflow) settles a held seed, newest wins."""
    if seed is None:
        return None
    pool = [g for g in gens if g.bin == bin_id and g.status in ("failed", "cancelled") and _int(g.params.get("seed")) == seed]
    if not pool:
        return None
    exact = [g for g in pool if prompt and _same_prompt(g.prompt_submitted, prompt)]
    if not exact and workflow:
        exact = [g for g in pool if Path(g.workflow).name == Path(workflow).name]
    chosen = exact or pool
    return sorted(chosen, key=lambda g: g.ts)[-1]


def reconstruct(
    project: Project,
    media: Media,
    embedded: Embedded,
    *,
    generations: list[Generation],
    workflows_dir,
    output_root: str | None = None,
    now: str | None = None,
) -> Recovery | None:
    """A generation record for ``media`` from what its file carries. Pure: nothing is written.

    Returns None when the file carries nothing usable. The record is stamped ``done`` with the
    media as its output; ``recovered_from`` names the file.
    """
    if embedded.empty:
        return None
    snapshot = embedded.snapshot or {}
    parsed = parse_graph(embedded.graph) if embedded.graph else GraphRecipe()
    bin_id = media.origin.bin or ""
    bin_ = project.bins_by_id.get(bin_id)
    kind = media.type
    warnings: list[str] = []
    by_id = {g.id: g for g in generations}

    # the runner's own snapshot names the generation outright: a deleted take re-uploaded, or
    # a landing that never reached the project
    named = str(snapshot.get("generation") or "")
    existing = by_id.get(named) if named else None
    if existing is not None and media.id in existing.outputs:
        return None

    # the workflow: the snapshot says, else the graph's node set does
    match: WorkflowMatch | None = None
    workflow = str(snapshot.get("workflow") or "").strip()
    if workflow and resolve_curated_workflow(workflow, workflows_dir) is None:
        warnings.append(f"the file names workflow {workflow}, which is not in the curated folders")
        workflow = ""
    if not workflow and embedded.graph:
        prefer = [bin_.workflow if bin_ else "", project.defaults.workflow_for(kind)]
        match = match_workflow(embedded.graph, workflows_dir, prefer=prefer)
        if match is not None:
            workflow = match.name
        else:
            warnings.append("no curated workflow matches the graph in the file")

    prompt = str(snapshot.get("prompt") or "") or parsed.prompt
    negative = str(snapshot.get("negative") or "") or parsed.negative
    seed = _int(snapshot.get("seed")) if snapshot.get("seed") is not None else parsed.seed

    if existing is None:
        existing = _attachable(generations, bin_id, seed, prompt, workflow)
    if existing is not None:
        gen = existing.model_copy(deep=True)
        gen.status = "done"
        gen.outputs = [*gen.outputs, media.id]
        for key, value in (("steps", parsed.steps), ("fps", parsed.fps)):
            if value is not None and gen.params.get(key) is None:
                gen.params[key] = value
        gen.warnings = [*gen.warnings, {"kind": "recovered", "handle": "", "detail": f"output recovered from {Path(media.path).name}, which carries the render's graph"}]
        gen.recovered_from = media.path
        return Recovery(generation=gen, attached=True, workflow=match, warnings=warnings)

    capacity: Capacity | None = None
    profile: TokenProfile | None = None
    if workflow:
        capacity = workflow_capacity(workflow, workflows_dir)
        profile = load_token_profile(workflow, workflows_dir)

    # references: the loaders the runner filled, mapped back to media, in the slot order the
    # assembler numbers them in so the ordinals (and the tokens) line up
    snap_paths = snapshot.get("paths_by_slot") if isinstance(snapshot.get("paths_by_slot"), dict) else {}
    image_slots = list(capacity.slot_node_ids) if capacity and capacity.slot_node_ids else []
    image_slots += [s for s in parsed.image_loaders if s not in image_slots]
    if not image_slots and kind == "video":
        image_slots = [s for s in ("start", "end") if s in parsed.frame_loaders or s in snap_paths]
    video_slots = list(capacity.video_slot_node_ids) if capacity and capacity.video_slot_node_ids else []
    video_slots += [s for s in parsed.video_loaders if s not in video_slots]

    def loader_media(slot: str) -> Media | None:
        if snap_paths.get(slot):
            found = _media_by_path(project, str(snap_paths[slot]))
            if found is not None:
                return found
        value = parsed.image_loaders.get(slot) or parsed.video_loaders.get(slot) or parsed.frame_loaders.get(slot)
        if not value:
            return None
        found = resolve_loader(value, project, output_root)
        if found is None:
            warnings.append(f"slot {slot} loads {value}, which is not a media this project knows")
        return found

    scratch = Bin(id=bin_id or "scratch", name=bin_.name if bin_ else "")  # handles minted against what is already bound
    scratch.references = [r.model_copy() for r in bin_.references] if bin_ else []
    references: list[GenerationReference] = []
    paths_by_slot: dict[str, str] = {}
    image_handles: list[str | None] = []
    video_handles: list[str | None] = []
    for slots, handles in ((image_slots, image_handles), (video_slots, video_handles)):
        for n, slot in enumerate(slots, start=1):
            found = loader_media(slot)
            if found is None:
                handles.append(None)
                continue
            bound = next((r for r in scratch.references if r.media == found.id), None)
            if bound is not None:
                ref_id, handle = bound.id, bound.handle
            else:
                ref_bin = _bin_holding(project, found) or Bin(id=found.origin.bin or found.id, name="ref")
                handle = assign_handle(scratch, ref_bin)
                ref_id = ""
                scratch.references.append(Reference(id="", media=found.id, bin=ref_bin.id, handle=handle))
            references.append(GenerationReference(ref=ref_id, media=found.id, handle=handle, slot=slot, ordinal=n))
            paths_by_slot[slot] = found.path
            handles.append(handle)

    # the typed prompt: tokens back to handles, and the project's global prompt (which the
    # assembler appends at submit time) taken off the end
    typed = prompt
    if profile is not None:
        typed = _untokenize(_untokenize(prompt, profile.image, image_handles), profile.video, video_handles)
    glob = project.defaults.prompt_for(kind)
    if glob and typed.rstrip().endswith(glob):
        typed = typed.rstrip()[: -len(glob)].rstrip()

    # LoRAs: what the runner injected for the recipe, plus a marker whose value is not the
    # workflow file's own
    loras = list(parsed.loras)
    base_graph = match.graph if match else None
    if base_graph is None and workflow:
        path = resolve_curated_workflow(workflow, workflows_dir)
        if path is not None:
            try:
                from . import workflow_capabilities as caps

                base_graph = caps.load_workflow_dict(path)
            except Exception:
                base_graph = None
    for nid, lora in parsed.marker_loras.items():
        stock = _lora_of(base_graph[nid]) if base_graph and isinstance(base_graph.get(nid), dict) else None
        if stock is None or stock.name != lora.name or stock.strength != lora.strength:
            if all(l.name != lora.name for l in loras):
                loras.append(lora)

    params: dict[str, Any] = {}
    snap_params = snapshot.get("params") if isinstance(snapshot.get("params"), dict) else {}
    width = _int(snap_params.get("width")) or parsed.width
    height = _int(snap_params.get("height")) or parsed.height
    if seed is not None:
        params["seed"] = seed
    canvas = (project.defaults.width, project.defaults.height) if project.defaults.configured else None
    if width and height:
        if canvas and (width, height) == (canvas[0] // 2, canvas[1] // 2):
            params.update({"width": canvas[0], "height": canvas[1], "quarter": True})
        else:
            params.update({"width": width, "height": height})
            if snap_params.get("quarter"):
                params["quarter"] = True
    if kind == "video":
        length = _float(snap_params.get("length_s"))
        if length is None and parsed.frames and parsed.fps:
            length = round((parsed.frames - 1) / parsed.fps, 3)  # the runner renders length_s * fps + 1 frames
        if length is not None:
            params["length_s"] = length
        if parsed.fps is not None:
            params["fps"] = parsed.fps
    if parsed.steps is not None:
        params["steps"] = parsed.steps

    gen = Generation(
        id=ids.new_id(ids.GENERATION),
        bin=bin_id,
        kind=kind,
        ts=media.created or now or utc_now(),
        workflow=workflow,
        prompt_typed=typed,
        prompt_submitted=prompt,
        negative_submitted=negative,
        references=references,
        loras=loras,
        params=params,
        status="done",
        outputs=[media.id],
        paths_by_slot=paths_by_slot,
        warnings=[{"kind": "recovered", "handle": "", "detail": f"record rebuilt from the graph in {Path(media.path).name}"}]
        + [{"kind": "recovered", "handle": "", "detail": w} for w in warnings],
        recovered_from=media.path,
    )
    return Recovery(generation=gen, attached=False, workflow=match, warnings=warnings)


def recover_generation(
    store: ProjectStore,
    project: Project,
    media: Media,
    *,
    workflows_dir,
    output_root: str | None = None,
    now: str | None = None,
) -> Recovery | None:
    """Give ``media`` the generation record its file describes, when it has none.

    Reads the file, rebuilds or completes the record, appends it to the log, and turns the
    media into that generation's take (origin, take number) in the bin it sits in. Does not
    save the project; the caller does, under its lock. None when the media already has a
    record, or its file carries nothing.
    """
    if media.origin.type == "generation" and media.origin.generation and store.read_generation(project, media.origin.generation) is not None:
        return None
    embedded = read_embedded(media.path)
    if embedded.empty:
        return None
    rec = reconstruct(
        project, media, embedded, generations=store.read_generations(project), workflows_dir=workflows_dir, output_root=output_root, now=now
    )
    if rec is None:
        return None
    gen = rec.generation
    home = gen.bin or media.origin.bin
    if home:
        media.origin = MediaOrigin(type="generation", generation=gen.id, bin=home, source=media.origin.source, note=media.origin.note)
    bin_ = project.bins_by_id.get(gen.bin)
    if bin_ is not None and media.id in bin_.media and media.take is None:
        bin_.counters["take"] = int(bin_.counters.get("take", 0)) + 1
        media.take = bin_.counters["take"]
    store.append_generation(project, gen)
    return rec
