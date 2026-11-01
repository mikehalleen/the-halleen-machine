"""Bring a workflow the user has working in ComfyUI into THM: tagged, exported, both formats.

Nothing is converted. A run started from the ComfyUI frontend leaves both graphs in
``/history``: ``prompt[2]`` is the API graph that just ran, ``prompt[3].extra_pnginfo.workflow``
the UI graph it came from, with the same node ids. An output PNG carries the same pair in its
``prompt`` / ``workflow`` text chunks. So "working in Comfy" means "ran once", and both files
THM keeps -- the API graph it renders with and the UI graph the user opens to check or change
it -- are already there. This module tags both by node id; the server fetches and writes.

Tags are proposed from what a node is and what it feeds (``propose_tags``). A ``THM-*`` title
the user already gave a node always wins and is never changed, so a re-import of a run made
from the tagged copy only fills gaps. Checkpoint, steps, cfg and sampler are left baked.

Every import leaves a ``THM-EnhancerSystemPrompt`` node (see ``thm_core.enhancer``). A built-in
enhancer flow -- an LLM node rewriting the prompt inside the graph -- gives up its system prompt
to that node and is taken out: removed from the API graph, muted in the UI graph, the prompt fed
the raw text again. Without one the caller supplies the text (the agent researches the model);
the import refuses without it.

Only the two graphs are read from a run. ``extra_data`` also carries the Comfy API key for
partner nodes, and nothing else in it is kept.
"""

from __future__ import annotations

import copy
import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

from . import workflow_capabilities as caps
from .enhancer import ENHANCER_TITLE_PREFIX, SYSTEM_PROMPT_TITLE, TEXT_INPUT_KEYS

wc = caps.wc

SAMPLER_CLASSES = frozenset({"KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced", "CFGGuider", "DualCFGGuider", "BasicGuider"})
IMAGE_SAVE_CLASSES = ("SaveImage",)
VIDEO_SAVE_CLASSES = tuple(wc.VIDEO_SAVE_CLASSES)
IMAGE_LOADER_CLASSES = ("LoadImage",)
VIDEO_LOADER_CLASSES = ("LoadVideo", "VHS_LoadVideo", "VHS_LoadVideoPath")
PRIMITIVE_STRING_CLASSES = ("PrimitiveStringMultiline", "PrimitiveString", "String Literal", "StringConstant", "StringConstantMultiline")
PRIMITIVE_NUMBER_CLASSES = ("PrimitiveInt", "PrimitiveFloat", "INTConstant", "FloatConstant", "Int Literal", "Float Literal")
MODEL_LOADER_CLASSES = ("UNETLoader", "CheckpointLoaderSimple", "UnetLoaderGGUF", "ImageOnlyCheckpointLoader", "CheckpointLoader")
LORA_MARKER_CLASS = "ModelPassThrough"  # KJNodes; a pure model passthrough THM bypasses and injects after
SEED_KEYS = ("seed", "noise_seed")
# an LLM node: one of these input keys carries its instructions
SYSTEM_PROMPT_KEYS = ("system_prompt", "system", "instructions", "system_message", "system_instruction")
LLM_USER_KEYS = ("prompt", "user_prompt", "text", "input", "string", "message")
_LLM_CLASS_RE = re.compile(r"(?i)(TextGenerate|LLM|Ollama|Gemma|Qwen.*(Text|Chat|Prompt|VL)|PromptEnhanc|EnhancePrompt|PromptGen|Chat|Gemini|OpenAI|Claude|Groq|Mistral|Florence)")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]*$")


class ImportRefused(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# -- sources ------------------------------------------------------------------------


@dataclass
class Harvest:
    api: dict[str, Any]
    ui: dict[str, Any] | None
    source: str  # "run:<prompt id>" or "png"


def _is_thm_run(extra: dict[str, Any]) -> bool:
    """A prompt THM itself posted: never offered for import (and a private project's lives here)."""
    return bool(extra.get("machine_ui_project") or extra.get("machine_ui_label"))


def harvest_history_entry(prompt_id: str, entry: dict[str, Any]) -> Harvest:
    prompt = entry.get("prompt") if isinstance(entry, dict) else None
    if not isinstance(prompt, (list, tuple)) or len(prompt) < 3 or not isinstance(prompt[2], dict):
        raise ImportRefused("bad_run", f"ComfyUI run {prompt_id} has no graph")
    extra = prompt[3] if len(prompt) > 3 and isinstance(prompt[3], dict) else {}
    if _is_thm_run(extra):
        raise ImportRefused("thm_run", f"run {prompt_id} was posted by THM, not made in ComfyUI")
    ui = (extra.get("extra_pnginfo") or {}).get("workflow")
    return Harvest(api=copy.deepcopy(prompt[2]), ui=copy.deepcopy(ui) if isinstance(ui, dict) else None, source=f"run:{prompt_id}")


def png_text_chunks(data: bytes) -> dict[str, str]:
    """The tEXt / zTXt / iTXt chunks of a PNG, keyword to text."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ImportRefused("not_png", "the file is not a PNG")
    out: dict[str, str] = {}
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        try:
            if ctype == b"tEXt":
                key, _, text = body.partition(b"\x00")
                out[key.decode("latin-1")] = text.decode("latin-1")
            elif ctype == b"zTXt":
                key, _, rest = body.partition(b"\x00")
                out[key.decode("latin-1")] = zlib.decompress(rest[1:]).decode("latin-1")
            elif ctype == b"iTXt":
                key, _, rest = body.partition(b"\x00")
                compressed, rest = rest[0], rest[2:]
                _lang, _, rest = rest.partition(b"\x00")
                _tkey, _, text = rest.partition(b"\x00")
                out[key.decode("latin-1")] = (zlib.decompress(text) if compressed else text).decode("utf-8")
        except Exception:
            continue
        if ctype == b"IEND":
            break
    return out


def harvest_png(data: bytes) -> Harvest:
    chunks = png_text_chunks(data)
    try:
        api = json.loads(chunks["prompt"])
    except (KeyError, ValueError):
        raise ImportRefused("no_graph", "the PNG carries no ComfyUI prompt; save it from a ComfyUI SaveImage node") from None
    try:
        ui = json.loads(chunks["workflow"]) if "workflow" in chunks else None
    except ValueError:
        ui = None
    if not isinstance(api, dict):
        raise ImportRefused("no_graph", "the PNG's prompt chunk is not a graph")
    return Harvest(api=api, ui=ui if isinstance(ui, dict) else None, source="png")


def run_summary(prompt_id: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    """One line's worth of a history entry for picking a run, or None for one THM posted."""
    prompt = entry.get("prompt") if isinstance(entry, dict) else None
    if not isinstance(prompt, (list, tuple)) or len(prompt) < 3 or not isinstance(prompt[2], dict):
        return None
    extra = prompt[3] if len(prompt) > 3 and isinstance(prompt[3], dict) else {}
    if _is_thm_run(extra):
        return None
    api = prompt[2]
    classes = sorted({wc.node_class(n) for n in api.values() if isinstance(n, dict)})
    models = [str(v) for n in api.values() if isinstance(n, dict) for k, v in (n.get("inputs") or {}).items()
              if k in ("unet_name", "ckpt_name") and isinstance(v, str)]
    files: list[str] = []
    for out in (entry.get("outputs") or {}).values():
        for key in ("images", "gifs", "videos", "video"):
            for f in (out or {}).get(key) or []:
                if isinstance(f, dict) and f.get("filename") and f.get("type") == "output":
                    files.append(str(f["filename"]))
    status = (entry.get("status") or {}).get("status_str") or ""
    return {
        "id": str(prompt_id),
        "created": extra.get("create_time"),
        "status": status,
        "has_ui": isinstance((extra.get("extra_pnginfo") or {}).get("workflow"), dict),
        "nodes": len(api),
        "kind": guess_kind(api),
        "models": models,
        "outputs": files[:4],
        "classes": classes,
    }


# -- graph helpers (API format) --------------------------------------------------------


def _is_ref(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], (str, int)) and isinstance(v[1], int)


def _nodes(api: dict[str, Any]):
    return ((str(k), n) for k, n in api.items() if isinstance(n, dict) and "class_type" in n)


def _refs(node: dict[str, Any]):
    for key, v in (node.get("inputs") or {}).items():
        if _is_ref(v):
            yield key, str(v[0]), int(v[1])


def _consumers(api: dict[str, Any]) -> dict[str, list[tuple[str, str, int]]]:
    """source id -> [(consumer id, input key, source slot)]."""
    out: dict[str, list[tuple[str, str, int]]] = {}
    for nid, node in _nodes(api):
        for key, src, slot in _refs(node):
            out.setdefault(src, []).append((nid, key, slot))
    return out


def _upstream(api: dict[str, Any], start: str, *, stop=None) -> list[str]:
    """Every node reachable backward from ``start`` (excluded), breadth first; ``stop(id)`` ends a branch there (still listed)."""
    seen: list[str] = []
    queue = [src for _, src, _ in _refs(api.get(start) or {})]
    while queue:
        nid = queue.pop(0)
        if nid in seen or nid not in api:
            continue
        seen.append(nid)
        if stop and stop(nid):
            continue
        queue.extend(src for _, src, _ in _refs(api[nid]))
    return seen


def _literal_text(node: dict[str, Any]) -> str | None:
    inputs = node.get("inputs") or {}
    for key in TEXT_INPUT_KEYS:
        if isinstance(inputs.get(key), str):
            return inputs[key]
    return None


def _is_text_source(node: dict[str, Any]) -> bool:
    """A node that only holds a string the user typed: a string primitive, nothing wired in."""
    return wc.node_class(node) in PRIMITIVE_STRING_CLASSES and not any(True for _ in _refs(node)) and _literal_text(node) is not None


def _is_number_source(node: dict[str, Any]) -> bool:
    return wc.node_class(node) in PRIMITIVE_NUMBER_CLASSES and not any(True for _ in _refs(node))


def _is_encoder(node: dict[str, Any]) -> bool:
    cls = wc.node_class(node)
    return "TextEncode" in cls or (cls.startswith("CLIP") and "Encode" in cls and "Vision" not in cls)


def _is_llm(node: dict[str, Any]) -> bool:
    inputs = node.get("inputs") or {}
    return bool(_LLM_CLASS_RE.search(wc.node_class(node))) or any(k in inputs for k in SYSTEM_PROMPT_KEYS)


def _id_key(nid: str) -> tuple:
    parts = []
    for p in nid.split(":"):
        parts.append((0, int(p), "") if p.isdigit() else (1, 0, p))
    return tuple(parts)


def _next_id(api: dict[str, Any], ui: dict[str, Any] | None) -> str:
    top = [int(k) for k in api if str(k).isdigit()]
    if ui:
        top += [int(n["id"]) for n in ui.get("nodes") or [] if isinstance(n.get("id"), int)]
        top.append(int(ui.get("last_node_id") or 0))
    return str(max(top or [0]) + 1)


def guess_kind(api: dict[str, Any]) -> str:
    for _, node in _nodes(api):
        cls = wc.node_class(node)
        if cls in VIDEO_SAVE_CLASSES or cls in ("SaveWEBM", "CreateVideo") or cls.endswith("ToVideo") or "VideoNode" in cls or cls.startswith("EmptyLTXVLatentVideo"):
            return "video"
    return "image"


# -- UI graph edits --------------------------------------------------------------------
#
# A UI node is ``{"id", "type", "title"?, "mode", "inputs": [{name, link, widget?}],
# "outputs": [{name, links}], "widgets_values"}``; links are ``[id, from, from_slot, to,
# to_slot, type]``. Nodes inside a subgraph have API ids ``<instance>:<inner>``; titles are set
# there too, rewiring is only done on the top level (and the report says when it was skipped).


class _Ui:
    def __init__(self, ui: dict[str, Any] | None):
        self.ui = ui

    def node(self, api_id: str) -> dict[str, Any] | None:
        if not self.ui:
            return None
        parts = api_id.split(":")
        nodes = self.ui.get("nodes") or []
        subgraphs = {sg.get("id"): sg for sg in ((self.ui.get("definitions") or {}).get("subgraphs") or [])}
        node = None
        for i, part in enumerate(parts):
            node = next((n for n in nodes if str(n.get("id")) == part), None)
            if node is None:
                return None
            if i < len(parts) - 1:
                sg = subgraphs.get(node.get("type"))
                if sg is None:
                    return None
                nodes = sg.get("nodes") or []
        return node

    def top(self, api_id: str) -> dict[str, Any] | None:
        return self.node(api_id) if self.ui and ":" not in api_id else None

    def set_title(self, api_id: str, title: str) -> bool:
        node = self.node(api_id)
        if node is None:
            return False
        node["title"] = title
        return True

    def mute(self, api_id: str) -> bool:
        node = self.top(api_id)
        if node is None:
            return False
        node["mode"] = wc.COMFY_NODE_MODE_NEVER
        return True

    def _links(self) -> list:
        return self.ui.setdefault("links", [])

    def _new_link_id(self) -> int:
        ids = [int(link[0]) for link in self._links() if isinstance(link, list) and link]
        nid = max(ids + [int(self.ui.get("last_link_id") or 0)]) + 1
        self.ui["last_link_id"] = nid
        return nid

    def _input(self, node: dict[str, Any], name: str) -> tuple[int, dict[str, Any]] | None:
        for i, inp in enumerate(node.get("inputs") or []):
            if inp.get("name") == name:
                return i, inp
        return None

    def drop_input_link(self, api_id: str, name: str) -> bool:
        node = self.top(api_id)
        found = node and self._input(node, name)
        if not found or found[1].get("link") is None:
            return False
        link_id = found[1]["link"]
        found[1]["link"] = None
        for link in list(self._links()):
            if isinstance(link, list) and link and link[0] == link_id:
                self._links().remove(link)
                src = self.top(str(link[1]))
                for out in (src or {}).get("outputs") or []:
                    if link_id in (out.get("links") or []):
                        out["links"].remove(link_id)
        return True

    def drop_output_links(self, api_id: str) -> None:
        """Unwire everything a node feeds: it stands alone (a carrier node nothing reads)."""
        node = self.top(api_id)
        if node is None:
            return
        for link in list(self._links()):
            if isinstance(link, list) and len(link) > 3 and str(link[1]) == api_id:
                target = self.top(str(link[3]))
                for inp in (target or {}).get("inputs") or []:
                    if inp.get("link") == link[0]:
                        inp["link"] = None
                self._links().remove(link)
        for out in node.get("outputs") or []:
            out["links"] = []

    def connect(self, src_id: str, src_slot: int, dst_id: str, name: str, ltype: str) -> bool:
        src, dst = self.top(src_id), self.top(dst_id)
        found = dst and self._input(dst, name)
        if src is None or not found:
            return False
        if found[1].get("link") is not None:
            self.drop_input_link(dst_id, name)
        link_id = self._new_link_id()
        self._links().append([link_id, int(src["id"]), src_slot, int(dst["id"]), found[0], ltype])
        found[1]["link"] = link_id
        outs = src.setdefault("outputs", [])
        while len(outs) <= src_slot:
            outs.append({"name": ltype, "type": ltype, "links": []})
        outs[src_slot].setdefault("links", []).append(link_id)
        return True

    def set_widget(self, api_id: str, name: str, value: Any) -> bool:
        """Set a widget by the position its input holds among the node's widget inputs."""
        node = self.top(api_id)
        values = (node or {}).get("widgets_values")
        if not isinstance(values, list):
            return False
        widgets = [inp.get("name") for inp in node.get("inputs") or [] if inp.get("widget")]
        idx = widgets.index(name) if name in widgets else (0 if len(values) == 1 else -1)
        if idx < 0 or idx >= len(values):
            return False
        values[idx] = value
        return True

    def add_node(self, api_id: str, class_type: str, title: str, *, widgets: list, outputs: list[tuple[str, str]], inputs: list[tuple[str, str]] = (), near: str | None = None) -> dict[str, Any] | None:
        if not self.ui:
            return None
        anchor = self.top(near) if near else None
        pos = list((anchor or {}).get("pos") or [0, 0])
        node = {
            "id": int(api_id), "type": class_type, "title": title,
            "pos": [pos[0] + 40, pos[1] + 140] if anchor else [0, -400], "size": [400, 200],
            "flags": {}, "order": 0, "mode": wc.COMFY_NODE_MODE_ALWAYS,
            "inputs": [{"name": n, "type": t, "link": None} for n, t in inputs],
            "outputs": [{"name": n, "type": t, "links": []} for n, t in outputs],
            "properties": {"Node name for S&R": class_type},
            "widgets_values": list(widgets),
        }
        self.ui.setdefault("nodes", []).append(node)
        self.ui["last_node_id"] = max(int(self.ui.get("last_node_id") or 0), int(api_id))
        return node

    def move_output_links(self, src_id: str, slot: int, new_src: str, *, only_to: set[str]) -> None:
        src, new = self.top(src_id), self.top(new_src)
        if src is None or new is None:
            return
        outs = src.get("outputs") or []
        moving = []
        for link in self._links():
            if isinstance(link, list) and len(link) > 3 and str(link[1]) == src_id and link[2] == slot and str(link[3]) in only_to:
                link[1], link[2] = int(new["id"]), 0
                moving.append(link[0])
        if slot < len(outs):
            outs[slot]["links"] = [lid for lid in outs[slot].get("links") or [] if lid not in moving]
        new["outputs"][0]["links"].extend(moving)


# -- the import -------------------------------------------------------------------------


@dataclass
class TagPick:
    node: str
    class_type: str
    old_title: str
    tag: str
    why: str

    def to_dict(self) -> dict:
        return {"node": self.node, "class": self.class_type, "from": self.old_title, "to": self.tag, "why": self.why}


@dataclass
class ImportResult:
    name: str
    kind: str
    api: dict[str, Any]
    ui: dict[str, Any] | None
    tags: list[TagPick] = field(default_factory=list)
    kept: list[dict] = field(default_factory=list)  # THM titles the user already gave
    baked: list[str] = field(default_factory=list)
    enhancer: dict[str, Any] = field(default_factory=dict)
    lora: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)

    def report(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "tags": [t.to_dict() for t in self.tags], "kept": self.kept,
            "baked": self.baked, "enhancer": self.enhancer, "lora": self.lora, "notes": self.notes,
            "capabilities": self.capabilities, "has_ui": self.ui is not None,
        }


def check_name(name: str) -> str:
    """The file stem for ``name``: ``krea-portrait`` or ``krea-portrait.json`` both give ``krea-portrait``."""
    stem = str(name or "").strip()
    if stem.lower().endswith(".json"):
        stem = stem[:-5]
    if not stem or not _NAME_RE.match(stem) or stem.startswith(("_", "THM-")):
        raise ImportRefused("bad_name", f"{name!r} is not a usable workflow name (letters, digits, - _ . only; not starting with _ or THM-)")
    return stem


class _Importer:
    def __init__(self, h: Harvest, kind: str | None):
        self.api = h.api
        self.uix = _Ui(h.ui)
        self.kind = kind or guess_kind(self.api)
        self.picks: dict[str, TagPick] = {}
        self.res = ImportResult(name="", kind=self.kind, api=self.api, ui=h.ui)
        self.original_titles = {nid: wc.node_title(n) for nid, n in _nodes(self.api)}

    # tags --------------------------------------------------------------------------

    def user_tagged(self, nid: str) -> bool:
        return wc._is_thm_title(self.original_titles.get(nid, ""))

    def covered(self, control: str) -> bool:
        return any(self.user_tagged(c.node_id) for c in wc.find_tagged_control_nodes(self.api, control))

    def pick(self, nid: str, tag: str, why: str) -> None:
        if nid not in self.api or self.user_tagged(nid) or nid in self.picks:
            return
        node = self.api[nid]
        self.picks[nid] = TagPick(nid, wc.node_class(node), wc.node_title(node), tag, why)

    def samplers(self) -> list[str]:
        return [nid for nid, n in _nodes(self.api) if wc.node_class(n) in SAMPLER_CLASSES]

    def partner(self) -> str | None:
        if self.samplers():
            return None
        for nid, n in sorted(_nodes(self.api), key=lambda x: _id_key(x[0])):
            inputs = n.get("inputs") or {}
            keys = [k for k in inputs if k in wc.PARTNER_PROMPT_KEYS or k.split(".")[-1] in wc.PARTNER_PROMPT_KEYS and k.count(".") == 1]
            if keys and not _is_llm(n) and not _is_encoder(n) and wc.node_class(n) not in PRIMITIVE_STRING_CLASSES:
                return nid
        return None

    def encoders(self, key: str) -> list[str]:
        found: list[str] = []
        for sid in self.samplers():
            ref = (self.api[sid].get("inputs") or {}).get(key)
            if not _is_ref(ref):
                continue
            # conditioning nodes that pass both sides through (a video generator, ControlNet apply)
            # take a positive and a negative input: follow the side we are tracing
            start = str(ref[0])
            while start in self.api and not _is_encoder(self.api[start]) and _is_ref((self.api[start].get("inputs") or {}).get(key)):
                start = str(self.api[start]["inputs"][key][0])
            chain = [start] + _upstream(self.api, start, stop=lambda i: _is_encoder(self.api[i]))
            for nid in chain:
                if nid in self.api and _is_encoder(self.api[nid]) and nid not in found:
                    found.append(nid)
                    break
        return found

    def prompt_targets(self) -> list[tuple[str, str]]:
        """Where the positive prompt text goes in: (node, input key)."""
        p = self.partner()
        if p:
            inputs = self.api[p].get("inputs") or {}
            return [(p, k) for k in inputs if k.split(".")[-1] in wc.PARTNER_PROMPT_KEYS and k.count(".") <= 1][:1]
        out = []
        for e in self.encoders("positive") or self.encoders("conditioning"):
            key = wc.text_input_key_for_node(self.api[e])
            out.append((e, key))
        return out

    def tag_prompts(self) -> None:
        p = self.partner()
        if p:
            if not self.covered(wc.PARTNER_NODE):
                self.pick(p, "THM-PartnerNode", "partner API node: THM writes prompt, duration, seed it already has")
            return
        positives = self.encoders("positive") or self.encoders("conditioning")
        negatives = [n for n in self.encoders("negative") if n not in positives]
        for control, tag, nodes in ((wc.PROMPT, "THM-Prompt", positives), (wc.NEGATIVE_PROMPT, "THM-NegativePrompt", negatives)):
            if self.covered(control):
                continue
            for enc in nodes:
                key = wc.text_input_key_for_node(self.api[enc])
                ref = (self.api[enc].get("inputs") or {}).get(key)
                if _is_ref(ref) and str(ref[0]) in self.api and _is_text_source(self.api[str(ref[0])]):
                    self.pick(str(ref[0]), tag, f"the text feeding {wc.node_class(self.api[enc])} {enc}")
                elif isinstance(ref, str) or ref is None:
                    self.pick(enc, tag, "text encoder on the sampler's " + ("positive" if tag == "THM-Prompt" else "negative"))
                else:
                    self.res.notes.append(f"{tag}: encoder {enc} takes its text from node {ref[0]} ({wc.node_class(self.api.get(str(ref[0]), {}))}); tagged the encoder, THM's text replaces that link")
                    self.pick(enc, tag, "text encoder; its wired text is replaced at run time")

    def tag_save(self) -> None:
        classes = VIDEO_SAVE_CLASSES if self.kind == "video" else IMAGE_SAVE_CLASSES
        control, tag = (wc.SAVE_VIDEO, "THM-SaveVideo") if self.kind == "video" else (wc.SAVE_IMAGE, "THM-SaveImage")
        if self.covered(control):
            return
        saves = [nid for nid, n in _nodes(self.api) if wc.node_class(n) in classes]
        if not saves and self.kind == "image":
            previews = [nid for nid, n in _nodes(self.api) if wc.node_class(n) == "PreviewImage"]
            if previews:
                nid = max(previews, key=lambda i: len(_upstream(self.api, i)))
                self.api[nid]["class_type"] = "SaveImage"
                self.api[nid].setdefault("inputs", {})["filename_prefix"] = "ComfyUI"
                ui = self.uix.top(nid)
                if ui is not None:
                    ui["type"] = "SaveImage"
                    ui["widgets_values"] = ["ComfyUI"]
                self.res.notes.append(f"node {nid} was a Preview Image; it is a Save Image now so THM can find the output")
                saves = [nid]
        if not saves:
            want = " or ".join(classes)
            raise ImportRefused("no_save_node", f"no {want} node: THM finds its output through one. Add one in ComfyUI, run it, and import again")
        best = max(saves, key=lambda i: len(_upstream(self.api, i)))
        self.pick(best, tag, "the output THM collects" + (f" (the deepest of {len(saves)})" if len(saves) > 1 else ""))

    def tag_media_inputs(self) -> None:
        cons = _consumers(self.api)
        partner = self.partner()
        for nid, n in sorted(_nodes(self.api), key=lambda x: _id_key(x[0])):
            cls = wc.node_class(n)
            if cls in VIDEO_LOADER_CLASSES:
                if not self.covered(wc.VIDEO_REFERENCE):
                    self.pick(nid, "THM-VideoReference", "video loader: a reference clip slot")
                continue
            if cls not in IMAGE_LOADER_CLASSES:
                continue
            keys = self._downstream_keys(nid, cons)
            low = [k.lower() for k in keys]
            if self.kind == "video" and any(k in wc.VIDEO_GENERATOR_END_KEYS or "end" in k or "last" in k for k in low):
                if not self.covered(wc.VIDEO_END_FRAME):
                    self.pick(nid, "THM-EndFrame", "feeds the video's end frame")
            elif self.kind == "video" and any("reference" in k for k in low):
                if not self.covered(wc.IMAGE_REFERENCE):
                    self.pick(nid, "THM-ImageReference", "feeds a reference image input")
            elif self.kind == "video" and (any(k in wc.VIDEO_GENERATOR_START_KEYS or "start" in k or "first" in k for k in low) or (partner and "image" in low) or "image" in low):
                if not self.covered(wc.VIDEO_START_FRAME):
                    self.pick(nid, "THM-StartFrame", "feeds the video's start frame")
            elif not self.covered(wc.IMAGE_REFERENCE):
                self.pick(nid, "THM-ImageReference", "image input: a reference slot THM fills per render")

    def _downstream_keys(self, nid: str, cons, depth: int = 4) -> list[str]:
        """Input keys the node's output reaches within a few hops (through resizes and encodes)."""
        keys, frontier = [], [nid]
        for _ in range(depth):
            nxt = []
            for f in frontier:
                for c, key, _ in cons.get(f, []):
                    keys.append(key)
                    nxt.append(c)
            frontier = nxt
        return keys

    def tag_size(self) -> None:
        if self.partner() or any(self.covered(c) for c in (wc.IMAGE_SIZE, wc.WIDTH, wc.HEIGHT, wc.VIDEO_GENERATOR)):
            return
        generators = [nid for nid, n in _nodes(self.api) if wc.node_class(n).endswith("ToVideo") and {"width", "height"} <= set(n.get("inputs") or {})]
        latents = [nid for nid, n in _nodes(self.api) if wc.node_class(n).startswith("Empty") and "Latent" in wc.node_class(n) and {"width", "height"} <= set(n.get("inputs") or {})]
        target, tag = (generators[0], "THM-VideoGenerator") if generators else (latents[0], "THM-ImageSize") if latents else (None, None)
        if target is None:
            self.res.baked.append("size: no latent or video generator node with width/height; THM cannot set the canvas")
            return
        inputs = self.api[target]["inputs"]
        w, h = inputs.get("width"), inputs.get("height")
        if _is_ref(w) and _is_ref(h) and _is_number_source(self.api.get(str(w[0]), {})) and _is_number_source(self.api.get(str(h[0]), {})):
            self.pick(str(w[0]), "THM-Width", f"the width feeding {wc.node_class(self.api[target])} {target}")
            self.pick(str(h[0]), "THM-Height", f"the height feeding {wc.node_class(self.api[target])} {target}")
            if tag == "THM-VideoGenerator":
                self.pick(target, tag, "video generator: THM sets its length")
            return
        if _is_ref(w) or _is_ref(h):
            src = str((w if _is_ref(w) else h)[0])
            self.res.notes.append(f"size on {target} came from {wc.node_class(self.api.get(src, {}))} {src}; THM writes the size directly, so that node no longer decides it")
        self.pick(target, tag, "the canvas THM sets" + (" (and length)" if tag == "THM-VideoGenerator" else ""))
        if tag == "THM-VideoGenerator":
            length = inputs.get("length")
            if _is_ref(length) and _is_number_source(self.api.get(str(length[0]), {})) and not self.covered(wc.FRAME_COUNT):
                self.pick(str(length[0]), "THM-FrameCount", "the frame count feeding the generator")

    def tag_timing(self) -> None:
        if self.kind != "video" or self.partner() or self.covered(wc.FRAME_RATE):
            return
        for nid, n in _nodes(self.api):
            inputs = n.get("inputs") or {}
            for key in ("frame_rate", "fps"):
                if key not in inputs:
                    continue
                v = inputs[key]
                if _is_ref(v) and _is_number_source(self.api.get(str(v[0]), {})):
                    self.pick(str(v[0]), "THM-FrameRate", f"the frame rate feeding {wc.node_class(n)} {nid}")
                    return
        self.res.baked.append("frame rate: baked in the graph (wire a number node into the save node's frame_rate to let THM set it)")

    def tag_seed(self) -> None:
        if self.partner() or self.covered(wc.SEED):
            return
        for nid, n in sorted(_nodes(self.api), key=lambda x: _id_key(x[0])):
            inputs = n.get("inputs") or {}
            if inputs.get("add_noise") == "disable":
                continue
            for key in SEED_KEYS:
                v = inputs.get(key)
                if isinstance(v, int) and not isinstance(v, bool) and (wc.node_class(n) in SAMPLER_CLASSES or wc.node_class(n) == "RandomNoise"):
                    self.pick(nid, "THM-Seed", "the sampler's seed")
                elif _is_ref(v) and _is_number_source(self.api.get(str(v[0]), {})):
                    self.pick(str(v[0]), "THM-Seed", f"the seed feeding {wc.node_class(n)} {nid}")

    def tag_lora(self, allowed: bool) -> None:
        if self.partner() or any(self.covered(c) for c in (wc.LORA, wc.LORA_HIGH, wc.LORA_LOW)):
            self.res.lora = {"marker": None, "why": "partner node" if self.partner() else "already tagged"}
            return
        cons = _consumers(self.api)
        loaders = [nid for nid, n in _nodes(self.api) if wc.node_class(n) in MODEL_LOADER_CLASSES and any(k == "model" for _, k, s in cons.get(nid, []) if s == 0)]
        if not allowed:
            self.res.lora = {"marker": None, "why": "not asked for"}
            return
        if len(loaders) != 1:
            why = "no model loader" if not loaders else f"{len(loaders)} model chains ({', '.join(loaders)}); tag THM-Lora-High / THM-Lora-Low by hand or say which is which"
            self.res.lora = {"marker": None, "why": why}
            return
        loader = loaders[0]
        tag = "THM-LoraAfterThisNode" if self.kind == "video" else "THM-Lora"
        new = _next_id(self.api, self.uix.ui)
        targets = {c for c, k, s in cons.get(loader, []) if k == "model" and s == 0}
        self.api[new] = {"inputs": {"model": [loader, 0]}, "class_type": LORA_MARKER_CLASS, "_meta": {"title": tag}}
        for c in targets:
            self.api[c]["inputs"]["model"] = [new, 0]
        ui_done = self.uix.add_node(new, LORA_MARKER_CLASS, tag, widgets=[], inputs=[("model", "MODEL")], outputs=[("MODEL", "MODEL")], near=loader) is not None
        if ui_done:
            self.uix.move_output_links(loader, 0, new, only_to=targets)
            self.uix.connect(loader, 0, new, "model", "MODEL")
        self.picks[new] = TagPick(new, LORA_MARKER_CLASS, "", tag, f"inserted after {wc.node_class(self.api[loader])} {loader}: prompt LoRAs inject here")
        self.res.lora = {"marker": new, "after": loader, "why": "one model chain", "ui": ui_done}

    # enhancer ------------------------------------------------------------------------

    def find_builtin_enhancer(self) -> dict[str, Any] | None:
        for target, key in self.prompt_targets():
            ref = (self.api[target].get("inputs") or {}).get(key)
            if not _is_ref(ref):
                continue
            chain = [str(ref[0])] + _upstream(self.api, str(ref[0]), stop=lambda i: _is_llm(self.api[i]))
            llm = next((i for i in chain if i in self.api and _is_llm(self.api[i])), None)
            if llm is None:
                continue
            inputs = self.api[llm].get("inputs") or {}
            system, system_node = None, None
            for k in SYSTEM_PROMPT_KEYS:
                v = inputs.get(k)
                if isinstance(v, str) and v.strip():
                    system = v
                elif _is_ref(v) and str(v[0]) in self.api:
                    system_node = str(v[0])
                    system = _literal_text(self.api[system_node])
                if system:
                    break
            user_ref, user_text = None, None
            for k in LLM_USER_KEYS:
                v = inputs.get(k)
                if isinstance(v, str):
                    user_text = v
                    break
                if _is_ref(v) and str(v[0]) in self.api and _is_text_source(self.api[str(v[0])]):
                    user_ref = str(v[0])
                    user_text = _literal_text(self.api[user_ref])
                    break
            return {"llm": llm, "class": wc.node_class(self.api[llm]), "target": target, "key": key, "system": system,
                    "system_node": system_node, "user_node": user_ref, "user_text": user_text}
        return None

    def detach_enhancer(self, found: dict[str, Any]) -> list[str]:
        """Feed the prompt input the raw text again and take the LLM branch out. Returns the removed ids."""
        target, key, llm = found["target"], found["key"], found["llm"]
        keep = {target, found.get("system_node"), found.get("user_node")} - {None}
        before = _consumers(self.api)
        removed: set[str] = set()
        queue = [llm]
        while queue:
            nid = queue.pop()
            if nid in removed or nid in keep:
                continue
            removed.add(nid)
            queue.extend(c for c, _, _ in before.get(nid, []) if c != target)
        protected = [n for n in removed if wc.node_class(self.api[n]) in SAMPLER_CLASSES.union(IMAGE_SAVE_CLASSES, VIDEO_SAVE_CLASSES)]
        if protected:
            raise ImportRefused("enhancer_woven_in", f"the built-in enhancer {llm} feeds {', '.join(protected)} beyond the prompt; import with keep_builtin_enhancer")
        if found.get("user_node"):
            self.api[target]["inputs"][key] = [found["user_node"], 0]
        else:
            self.api[target]["inputs"][key] = found.get("user_text") or ""
        # what fed only the removed branch goes too (the switch's other inputs, the system text)
        changed = True
        while changed:
            changed = False
            live = _consumers({k: v for k, v in self.api.items() if k not in removed})
            for nid, _ in list(_nodes(self.api)):
                if nid in removed or nid in keep:
                    continue
                if before.get(nid) and not live.get(nid):
                    removed.add(nid)
                    changed = True
        for nid in removed:
            del self.api[nid]
            self.uix.mute(nid)
        ui_rewired = self.uix.drop_input_link(target, key) if self.uix.top(target) else False
        if found.get("user_node"):
            self.uix.connect(found["user_node"], 0, target, key, "STRING")
        elif self.uix.top(target):
            self.uix.set_widget(target, key, found.get("user_text") or "")
        if found.get("system_node"):
            self.uix.drop_output_links(found["system_node"])
        if self.uix.ui and not ui_rewired:
            self.res.notes.append("the UI copy's prompt wiring sits inside a subgraph and was left as is; the API file is rewired")
        return sorted(removed, key=_id_key)

    def place_system_prompt(self, found: dict[str, Any] | None, supplied: str | None, keep_builtin: bool) -> None:
        existing = wc.find_nodes_by_title(self.api, SYSTEM_PROMPT_TITLE)
        if existing and not supplied:
            self.res.enhancer = {"mode": "existing", "node": existing[0][0], "system_prompt": _literal_text(existing[0][1]) or ""}
            return
        text = supplied or (found or {}).get("system")
        if not text or not text.strip():
            raise ImportRefused(
                "system_prompt_needed",
                "no built-in enhancer to take a system prompt from: research how this model wants prompts and pass one (system_prompt)",
            )
        removed: list[str] = []
        if found and not keep_builtin:
            removed = self.detach_enhancer(found)
        if existing:
            nid = existing[0][0]
            self.api[nid]["inputs"][wc.text_input_key_for_node(self.api[nid])] = text
            self.uix.set_widget(nid, "value", text) or self.uix.set_widget(nid, "text", text)
        elif found and found.get("system_node") and not keep_builtin and not supplied:
            nid = found["system_node"]
            self.api[nid]["_meta"] = {**(self.api[nid].get("_meta") or {}), "title": SYSTEM_PROMPT_TITLE}
            self.uix.set_title(nid, SYSTEM_PROMPT_TITLE)
        else:
            nid = _next_id(self.api, self.uix.ui)
            self.api[nid] = {"inputs": {"value": text}, "class_type": "PrimitiveStringMultiline", "_meta": {"title": SYSTEM_PROMPT_TITLE}}
            self.uix.add_node(nid, "PrimitiveStringMultiline", SYSTEM_PROMPT_TITLE, widgets=[text], outputs=[("STRING", "STRING")])
        if found:
            mode = "builtin_kept" if keep_builtin else "builtin_detached"
        else:
            mode = "supplied"
        self.res.enhancer = {"mode": mode, "node": nid, "system_prompt": text, "from": found and {"llm": found["llm"], "class": found["class"]},
                             "removed": removed, "replaced_with_supplied": bool(found and supplied)}

    # report --------------------------------------------------------------------------

    def baked_report(self) -> None:
        for nid, n in sorted(_nodes(self.api), key=lambda x: _id_key(x[0])):
            inputs = n.get("inputs") or {}
            for k in ("unet_name", "ckpt_name"):
                if isinstance(inputs.get(k), str):
                    self.res.baked.append(f"model: {inputs[k]} ({wc.node_class(n)} {nid})")
            if wc.node_class(n) in ("KSampler", "KSamplerAdvanced"):
                self.res.baked.append(f"sampler {nid}: steps {inputs.get('steps')}, cfg {inputs.get('cfg')}, {inputs.get('sampler_name')}/{inputs.get('scheduler')}")
        if not wc.find_tagged_control_nodes(self.api, wc.IS_DRAFT):
            self.res.baked.append("no draft setting (a THM-IsDraft switch adds one)")

    def capabilities(self) -> dict[str, Any]:
        frames = wc.discover_video_frame_input_support(self.api)
        summary = wc.scan_workflow_controls(self.api)
        return {
            "image_slots": len(wc.discover_image_reference_slots(self.api)),
            "video_slots": len(wc.discover_video_reference_slots(self.api)),
            "supports_start_frame": bool(frames.supports_start_frame),
            "supports_end_frame": bool(frames.supports_end_frame),
            "has_negative": bool(wc.find_control_nodes(self.api, wc.NEGATIVE_PROMPT)) or self._partner_negative(),
            "has_lora": bool(wc.find_control_nodes(self.api, wc.LORA) or wc.find_control_nodes(self.api, wc.LORA_HIGH)),
            "supports_draft": wc.supports_draft(self.api),
            "supports_size": wc.supports_size(self.api),
            "unknown_tags": [c.title for c in wc.unknown_thm_tags(self.api) if not c.title.startswith(ENHANCER_TITLE_PREFIX)],
            "controls": sorted(k for k in summary if k in wc.CONTROL_TAGS),
        }

    def _partner_negative(self) -> bool:
        p = self.partner()
        return bool(p and any(k.split(".")[-1] in wc.PARTNER_NEGATIVE_PROMPT_KEYS for k in (self.api[p].get("inputs") or {})))


def build_import(
    h: Harvest,
    *,
    name: str,
    kind: str | None = None,
    tags: dict[str, str] | None = None,
    untag: list[str] | None = None,
    system_prompt: str | None = None,
    keep_builtin_enhancer: bool = False,
    lora_marker: bool = True,
) -> ImportResult:
    """Tag a harvested pair for THM. Pure: works on copies, writes nothing."""
    h = Harvest(api=copy.deepcopy(h.api), ui=copy.deepcopy(h.ui), source=h.source)
    if kind not in (None, "image", "video"):
        raise ImportRefused("bad_kind", f"kind must be image or video, got {kind!r}")
    imp = _Importer(h, kind)
    imp.res.name = check_name(name)
    imp.res.kept = [{"node": nid, "class": wc.node_class(n), "title": imp.original_titles[nid]}
                    for nid, n in sorted(_nodes(imp.api), key=lambda x: _id_key(x[0])) if imp.user_tagged(nid)]
    # the enhancer first: taking it out changes what feeds the prompt
    imp.place_system_prompt(imp.find_builtin_enhancer(), (system_prompt or "").strip() or None, keep_builtin_enhancer)
    imp.tag_save()
    imp.tag_prompts()
    imp.tag_media_inputs()
    imp.tag_size()
    imp.tag_timing()
    imp.tag_seed()
    imp.tag_lora(lora_marker)
    for nid in untag or []:
        nid = str(nid)
        if nid in imp.picks:
            del imp.picks[nid]
        elif nid in imp.api and imp.user_tagged(nid):
            imp.api[nid]["_meta"] = {**(imp.api[nid].get("_meta") or {}), "title": wc.node_class(imp.api[nid])}
            imp.uix.set_title(nid, wc.node_class(imp.api[nid]))
            imp.res.kept = [k for k in imp.res.kept if k["node"] != nid]
        else:
            raise ImportRefused("unknown_node", f"untag: no tagged node {nid}")
    for nid, tag in (tags or {}).items():
        nid = str(nid)
        if nid not in imp.api:
            raise ImportRefused("unknown_node", f"tag: no node {nid} in the graph")
        if not wc._is_thm_title(str(tag)):
            raise ImportRefused("bad_tag", f"tag for {nid} must start with THM-, got {tag!r}")
        node = imp.api[nid]
        imp.picks[nid] = TagPick(nid, wc.node_class(node), imp.original_titles.get(nid, ""), str(tag), "asked for")
    for nid, p in imp.picks.items():
        imp.api[nid]["_meta"] = {**(imp.api[nid].get("_meta") or {}), "title": p.tag}
        imp.uix.set_title(nid, p.tag)
    if not wc.find_control_nodes(imp.api, wc.SAVE_VIDEO if imp.kind == "video" else wc.SAVE_IMAGE):
        raise ImportRefused("no_save_node", "nothing THM can collect the output from after tagging")
    if not imp.prompt_targets() and not wc.find_control_nodes(imp.api, wc.PROMPT):
        imp.res.notes.append("no prompt input found: THM's prompt will not reach this graph; tag the text node THM-Prompt")
    imp.res.tags = sorted(imp.picks.values(), key=lambda p: _id_key(p.node))
    imp.baked_report()
    imp.res.capabilities = imp.capabilities()
    if imp.res.ui is None:
        imp.res.notes.append("the run carried no UI graph (posted through the API?); only the API file is written")
    return imp.res
