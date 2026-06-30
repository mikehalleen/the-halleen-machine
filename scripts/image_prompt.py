"""Image prompt template composition (no Comfy/PIL deps — testable in isolation)."""

from __future__ import annotations

import random
import re

_TEMPLATE_RE = re.compile(r"\[([a-zA-Z0-9_.]+)\]")
_WC_RE = re.compile(r"\{([^{}]+)\}")

DEFAULT_IMAGE_TEMPLATE = """
[keyframe.layout]
[sequence.setting_asset]
[sequence.setting_prompt]
[project.style_prompt]
[char.prompt]
[sequence.style_asset]
[sequence.style_prompt]
"""

DEFAULT_IMAGE_TEMPLATE_CUSTOM = """[thm.reference_prelude]
[keyframe.layout]
[sequence.setting_asset]
[sequence.setting_prompt]
[project.style_prompt]
[char.prompt]
[sequence.style_asset]
[sequence.style_prompt]
"""

DEFAULT_IMAGE_TEMPLATE_2CHAR = """
LEFT: [char1.prompt]
RIGHT: [char2.prompt]
[keyframe.layout]
[sequence.setting_asset]
[sequence.setting_prompt]
[project.style_prompt]
[sequence.style_asset]
[sequence.style_prompt]
"""


def expand_inline_wildcards(text, iter_index=0):
    if not text:
        return ""

    def repl(m):
        opts = [p.strip() for p in m.group(1).split("|")]
        if not opts:
            return ""
        return random.choice(opts)

    return _WC_RE.sub(repl, text)


def resolve_wildcards_in_dict(data):
    """Pre-resolve all wildcards in a dict's string values."""
    if not data:
        return {}
    resolved = {}
    for k, v in data.items():
        if isinstance(v, str):
            resolved[k] = expand_inline_wildcards(v)
        else:
            resolved[k] = v
    return resolved


def compose_image_prompt(
    template_str,
    project_data,
    sequence_data,
    keyframe_data,
    char_data=None,
    iter_index=0,
    reference_prelude: str = "",
):
    if char_data is None:
        char_data = {}

    def resolve_placeholder(match):
        key_path = match.group(1)
        parts = key_path.split(".")
        if len(parts) != 2:
            return f"[INVALID_KEY: {key_path}]"
        source_name, key = parts[0], parts[1]
        source_data = None
        if source_name == "project":
            source_data = project_data
        elif source_name == "sequence":
            source_data = sequence_data
        elif source_name == "keyframe":
            source_data = keyframe_data
        elif source_name == "char":
            source_data = char_data
        elif source_name == "thm" and key == "reference_prelude":
            return reference_prelude
        value = (source_data or {}).get(key, "")
        return expand_inline_wildcards(str(value), iter_index)

    prompt = _TEMPLATE_RE.sub(resolve_placeholder, template_str)
    return "\n".join(line for line in prompt.splitlines() if line.strip()).strip()


def resolve_single_pass_prompt(
    *,
    iteration: int,
    image_index: int,
    simple_p_raw: str,
    prompt_template: str,
    project_data,
    sequence_data,
    keyframe_data,
    char_data,
    reference_prelude: str,
) -> str:
    """First loop pass uses cached prompt (includes prelude); later passes recompose without prelude."""
    if iteration == 0:
        return simple_p_raw
    return compose_image_prompt(
        prompt_template,
        project_data,
        sequence_data,
        keyframe_data,
        char_data,
        image_index,
        reference_prelude="",
    )


def compose_image_prompt_2char(
    template_str, project_data, sequence_data, keyframe_data, char1_data=None, char2_data=None, iter_index=0
):
    if char1_data is None:
        char1_data = {}
    if char2_data is None:
        char2_data = {}

    def resolve_placeholder(match):
        key_path = match.group(1)
        parts = key_path.split(".")
        if len(parts) != 2:
            return f"[INVALID_KEY: {key_path}]"
        source_name, key = parts[0], parts[1]
        source_data = None
        if source_name == "project":
            source_data = project_data
        elif source_name == "sequence":
            source_data = sequence_data
        elif source_name == "keyframe":
            source_data = keyframe_data
        elif source_name == "char1":
            source_data = char1_data
        elif source_name == "char2":
            source_data = char2_data
        value = (source_data or {}).get(key, "")
        return expand_inline_wildcards(str(value), iter_index)

    prompt = _TEMPLATE_RE.sub(resolve_placeholder, template_str)
    return "\n".join(line for line in prompt.splitlines() if line.strip()).strip()


def compose_image_prompt_2char_noresolve(
    template_str, project_data, sequence_data, keyframe_data, char1_data=None, char2_data=None
):
    """Compose prompt WITHOUT expanding wildcards - expects pre-resolved data."""
    if char1_data is None:
        char1_data = {}
    if char2_data is None:
        char2_data = {}

    def resolve_placeholder(match):
        key_path = match.group(1)
        parts = key_path.split(".")
        if len(parts) != 2:
            return f"[INVALID_KEY: {key_path}]"
        source_name, key = parts[0], parts[1]
        source_data = None
        if source_name == "project":
            source_data = project_data
        elif source_name == "sequence":
            source_data = sequence_data
        elif source_name == "keyframe":
            source_data = keyframe_data
        elif source_name == "char1":
            source_data = char1_data
        elif source_name == "char2":
            source_data = char2_data
        return str((source_data or {}).get(key, ""))

    prompt = _TEMPLATE_RE.sub(resolve_placeholder, template_str)
    return "\n".join(line for line in prompt.splitlines() if line.strip()).strip()
