"""Prompt enhancement: rewrite a prompt through an LLM, with per-workflow instructions.

A workflow may carry a node titled ``THM-EnhancerSystemPrompt`` whose text tells the LLM
how prompts for that model must read (MiniMax H3 wants six labelled sections; an SDXL
workflow wants a comma list). The node is an orphan -- nothing reads it in ComfyUI -- so
the runners strip it before posting (``workflow_controls.strip_prompt_metadata``). A workflow
without one gets a default keyed by its kind, from ``DEFAULT_SYSTEM_PROMPTS`` below.

The local engine runs ``workflows/prompt_enhancer.json``: a ComfyUI graph with two text
inputs (``THM-EnhancerSystemPrompt``, ``THM-EnhancerUserPrompt``) and a ``PreviewAny`` node
that surfaces the generated text in ``/history``. Nothing here talks HTTP; the server does.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Literal

from .workflow_names import WORKFLOWS_DIR

ENHANCER_WORKFLOW_FILENAME = "prompt_enhancer.json"
SYSTEM_PROMPT_TITLE = "THM-EnhancerSystemPrompt"
USER_PROMPT_TITLE = "THM-EnhancerUserPrompt"
ENHANCER_TITLE_PREFIX = "THM-Enhancer"
TEXT_INPUT_KEYS = ("text", "value", "string")

Kind = Literal["image", "video"]
SystemSource = Literal["workflow", "default"]

# Built on the phrasing of the tested MiniMax enhancer prompt: the local model follows this
# shape and derails on others. The one rule is the user's -- nothing invented.
_HANDOFF = """\
if the prompt submitted contains notes that appear to be from a previous run by a similar agent, remove them first then reassess this turn

Here is the message from the user which may include notes for you to consider and/or a prompt for you to rewrite, return only the new prompt."""

DEFAULT_SYSTEM_PROMPTS: dict[str, str] = {
    "image": """\
You are a format compiler for image generation prompts (Flux, Klein, Z-Image, Krea,
SDXL). You receive a prompt and, optionally, a note saying what to change. You
restructure the prompt into clear descriptive prose. You do not add information.

THE ONE RULE
You are a formatter, not a writer. Every subject, action, setting, wardrobe, lighting,
camera, style and mood fact in your output must be traceable to the user's prompt or
note. If the user did not state something, leave it out. Do not add a lens, an f-stop,
a time of day, a film stock, a framing or an emotion because a good prompt usually has
one. A prompt with gaps is correct. A prompt with invented detail is a failure, even if
the invention is plausible.

You may rephrase, reorder, split and combine the user's statements, and turn shorthand
into clear visual language. You may not introduce a fact they did not state. If the
note contradicts the prompt, the note wins.

TAGS
The user names reference media with dot-tags (.ed, .band-og, .jack.2). Keep every tag
exactly as written, in place. Never rename a tag, never convert it to "image 1" or
"<Subject 1>", never invent one. Do not write a sentence about the tags themselves.

OUTPUT
One paragraph of plain prose: subject and what it is doing, then setting, then framing,
then lighting, then style and mood -- each only if the user gave it. No preamble, no
commentary, no markdown, no quotation marks, no negatives ("no blur"), no quality
boilerplate ("masterpiece, 8k"). English only.

""" + _HANDOFF,
    "video": """\
You are a format compiler for video generation prompts (LTX-2, Wan 2.2, Kling,
Seedance). You receive a prompt and, optionally, a note saying what to change. You
restructure the prompt into one clearly described continuous shot. You do not add information.

THE ONE RULE
You are a formatter, not a writer. Every subject, action, setting, wardrobe, lighting,
camera, style and mood fact in your output must be traceable to the user's prompt or
note. If the user did not state something, leave it out. Do not add a camera move, a lens,
a time of day, a sound or an emotion because a good prompt usually has
one. A prompt with gaps is correct. A prompt with invented detail is a failure, even if
the invention is plausible.

You may rephrase, reorder, split and combine the user's statements, and turn shorthand
into clear visual language. You may not introduce a fact they did not state. If the
note contradicts the prompt, the note wins.

TAGS
The user names reference media with dot-tags (.ed, .band-og, .jack.2). Keep every tag
exactly as written, in place. Never rename a tag, never convert it to "image 1" or
"<Subject 1>", never invent one. Do not write a sentence about the tags themselves.

OUTPUT
One paragraph of plain prose describing one continuous shot in the order it plays:
subject and setting as it begins, then the motion in sequence, then the camera if the
user named one, then lighting and mood if the user gave them. No cuts unless asked.
No sound unless the user mentioned it. No preamble, no
commentary, no markdown, no quotation marks, no negatives ("no blur"), no quality
boilerplate ("masterpiece, 8k"). English only.

""" + _HANDOFF,
}


# -- workflow scanning ----------------------------------------------------------


def _node_title(node: Any) -> str:
    return str(((node or {}).get("_meta") or {}).get("title") or "") if isinstance(node, dict) else ""


def _node_text(node: dict[str, Any]) -> str | None:
    inputs = node.get("inputs") or {}
    for key in TEXT_INPUT_KEYS:
        v = inputs.get(key)
        if isinstance(v, str):
            return v
    return None


def is_enhancer_node(node: Any) -> bool:
    """A node the app reads and ComfyUI never should: title starts with ``THM-Enhancer``."""
    return _node_title(node).startswith(ENHANCER_TITLE_PREFIX)


def find_titled(workflow: dict[str, Any], title: str) -> tuple[str, dict[str, Any]] | None:
    for node_id, node in workflow.items():
        if isinstance(node, dict) and _node_title(node) == title:
            return str(node_id), node
    return None


def workflow_system_prompt(workflow: dict[str, Any]) -> str | None:
    """The ``THM-EnhancerSystemPrompt`` text baked into a generation workflow, if any."""
    found = find_titled(workflow, SYSTEM_PROMPT_TITLE)
    if found is None:
        return None
    text = _node_text(found[1])
    return text.strip() if text and text.strip() else None


def load_workflow(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def system_prompt_for(workflow_path: Path | None, kind: Kind) -> tuple[str, SystemSource]:
    """The workflow's own enhancer instructions when it has them, else the kind's default."""
    if workflow_path is not None:
        wf = load_workflow(workflow_path)
        text = workflow_system_prompt(wf) if wf else None
        if text:
            return text, "workflow"
    return DEFAULT_SYSTEM_PROMPTS[kind], "default"


# -- the enhancer graph ----------------------------------------------------------


def enhancer_path(workflows_dir: Path | None = None) -> Path:
    return Path(workflows_dir or WORKFLOWS_DIR) / ENHANCER_WORKFLOW_FILENAME


def enhancer_available(workflows_dir: Path | None = None) -> bool:
    """The local engine exists when the enhancer workflow is on disk and carries both inputs."""
    wf = load_workflow(enhancer_path(workflows_dir))
    return bool(wf) and find_titled(wf, SYSTEM_PROMPT_TITLE) is not None and find_titled(wf, USER_PROMPT_TITLE) is not None


def build_user_message(prompt: str, note: str, references: list[tuple[str, str]] | None = None) -> str:
    """What follows the system text: the note, the bound tags, the prompt to rewrite.

    The enhancer graph concatenates the system text and this into one string, so the break
    between them is supplied here. The NOTE / PROMPT layout is the one the workflow was tested
    with; the small local model derails more on other framings.
    """
    # `references` is accepted for the API shape but not written into the message: the tags
    # already sit in the prompt, the system text says to keep them, and a reminder here came
    # back parroted as "all reference tags preserved verbatim: ..." inside the rewrite
    del references
    note = (note or "").strip() or "improve the prompt as it stands"
    return f"\n\nNOTE\n\n{note}\n\nPROMPT\n\n{(prompt or '').strip()}"


def _set_text(node: dict[str, Any], text: str) -> None:
    inputs = node.setdefault("inputs", {})
    key = next((k for k in TEXT_INPUT_KEYS if k in inputs), "value")
    for k in TEXT_INPUT_KEYS:
        if k != key and k in inputs:
            del inputs[k]
    inputs[key] = text


def build_enhancer_graph(enhancer: dict[str, Any], system_prompt: str, user_message: str, seed: int | None = None) -> dict[str, Any]:
    """The enhancer workflow with both texts set and every switch forced to its LLM branch.

    The saved workflow carries a "Refine Prompt?" boolean so it can be run pass-through in
    ComfyUI; here the answer is always yes, so the switch input is pinned rather than the
    boolean node hunted down by title. The sampler seed stays as saved unless ``seed`` is
    given: the saved one is the tested one, and the small model derails far more on others.
    """
    graph = copy.deepcopy(enhancer)
    sys_node = find_titled(graph, SYSTEM_PROMPT_TITLE)
    usr_node = find_titled(graph, USER_PROMPT_TITLE)
    if sys_node is None or usr_node is None:
        raise ValueError(f"enhancer workflow lacks {SYSTEM_PROMPT_TITLE} or {USER_PROMPT_TITLE}")
    _set_text(sys_node[1], system_prompt)
    _set_text(usr_node[1], user_message)
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == "ComfySwitchNode":
            node.setdefault("inputs", {})["switch"] = True
        if seed is not None:
            inputs = node.get("inputs") or {}
            for key in list(inputs.keys()):
                if key == "seed" or key.endswith(".seed"):
                    inputs[key] = seed
    return graph


def preview_node_ids(graph: dict[str, Any]) -> list[str]:
    return [str(nid) for nid, node in graph.items() if isinstance(node, dict) and node.get("class_type") == "PreviewAny"]


def extract_text(history_entry: dict[str, Any], graph: dict[str, Any]) -> str | None:
    """The generated text from a ``/history/<id>`` entry: the PreviewAny node's ``text`` output."""
    outputs = history_entry.get("outputs") or {}
    ordered = preview_node_ids(graph) + [k for k in outputs.keys() if k not in preview_node_ids(graph)]
    for nid in ordered:
        out = outputs.get(nid) or {}
        texts = out.get("text")
        if isinstance(texts, list) and texts:
            joined = "\n".join(str(t) for t in texts if t is not None).strip()
            if joined:
                return joined
        if isinstance(texts, str) and texts.strip():
            return texts.strip()
    return None


def history_error(history_entry: dict[str, Any]) -> str | None:
    status = history_entry.get("status") or {}
    if status.get("status_str") != "error":
        return None
    for msg in status.get("messages") or []:
        if isinstance(msg, (list, tuple)) and len(msg) > 1 and msg[0] == "execution_error":
            data = msg[1] if isinstance(msg[1], dict) else {}
            return str(data.get("exception_message") or data.get("exception_type") or "execution error")
    return "execution error"


# -- output hygiene --------------------------------------------------------------
#
# The local model is small and derails often: it wraps the answer in a label, narrates its
# input first, echoes the instructions back, or answers empty. Cleaning handles the wrappers;
# ``output_problem`` names what cleaning cannot fix so the caller can try another seed.

_ROLE_LINES = ("assistant", "user", "system")
_LABEL_WORDS = ("prompt", "answer", "output", "rewrite", "result")
# a label is the phrase followed by a colon or markdown emphasis, so "prompt rewriter" in prose is not one
_REWRITE_LABEL_RE = re.compile(r"(?i)(?:rewritten prompt|enhanced prompt|new prompt|prompt rewrite)\s*[:*_]+")
_TRAILING_CUTS = ("\n---", "\n\nNote:", "\n\nNotes:", "\n\n*Note", "\n\n**Note")
_TAG_RE = re.compile(r"(?<![\w.])\.[a-z][\w-]*(?:\.\d+)?")
ECHO_LINES = 3  # verbatim instruction lines in an output before it counts as an echo


def _drop_label_line(t: str) -> str:
    """A first line that only announces the answer goes -- ``**Rewritten prompt:**``,
    ``Final Answer``, ``Here is the rewritten prompt based on your instructions:`` -- and the
    text after it stays."""
    first, _, rest = t.partition("\n")
    bare = first.strip().strip("*_# ").rstrip(":").strip().lower()
    labelled = first.strip().endswith((":", "*")) or bare in ("final answer", "rewritten prompt", "prompt")
    announces = any(w in bare for w in _LABEL_WORDS) or bare.startswith("here is")
    if bare and len(bare) <= 90 and announces and labelled and rest.strip():
        return rest.lstrip()
    return t


def clean_output(text: str) -> str:
    """Strip the wrappers a small model adds despite instructions: fences, a stray role token,
    a label line, an echoed input block before a "Rewritten prompt:" label, trailing commentary."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
        t = t.strip()
    # when the model narrates its input first, the rewrite follows the last such label
    hits = list(_REWRITE_LABEL_RE.finditer(t))
    if hits:
        after = t[hits[-1].end():].lstrip(" :*_\n")
        if after.strip():
            t = after
    for _ in range(3):
        first, _, rest = t.partition("\n")
        if first.strip().lower() in _ROLE_LINES:
            t = rest.lstrip()
            continue
        stripped = _drop_label_line(t)
        if stripped != t:
            t = stripped
            continue
        break
    for cut in _TRAILING_CUTS:
        i = t.find(cut)
        if i > 0:
            t = t[:i]
    t = t.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" and "\n" not in t:
        t = t[1:-1].strip()
    return t.strip()


def output_problem(text: str, system_prompt: str, original: str) -> str | None:
    """Why a cleaned output is not a usable prompt, or None: empty, an echo of the instructions
    or the input, a refusal or an aside, or a rewrite that dropped a tag the original used."""
    t = (text or "").strip()
    if len(t) < 12:
        return "empty"
    head = t[:60].lower()
    if head.startswith(("you are ", "you're ", "you rewrite ", "wait ", "i cannot", "i can't", "sorry", "as an ai")):
        return "echoed the instructions or refused"
    # several full lines of the instructions reproduced verbatim is an echo, however it
    # starts. One or two is not: a format-compiler prompt dictates boilerplate the output
    # must carry word for word (a mandated negatives line, a fixed placeholder).
    echoed = sum(1 for line in system_prompt.splitlines() if len(line.strip()) >= 30 and line.strip() in t)
    if echoed >= ECHO_LINES:
        return "echoed the instructions or refused"
    if t.lower().startswith(("note\n", "note:", "user note")):
        return "echoed the input"
    missing = sorted(tag for tag in set(_TAG_RE.findall(original or "")) if tag not in t)
    if missing:
        return f"dropped reference tag(s) {', '.join(missing)}"
    return None

