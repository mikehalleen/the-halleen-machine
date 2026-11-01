"""Parse and format LoRA tags embedded in THM prompt text.

Two notations are read: the current ``<lora:name:strength[:strength_b]>`` and the 1.x
``__lora:name:strength[:strength_b]__`` (the Gradio app could not keep angle brackets intact,
so 1.x projects carry the underscore form). Only the angle form is ever written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_LEGACY = r"__lora:(?P<lname>[^:]+):(?P<ls>[\d.]+)(?::(?P<lsb>[\d.]+))?__"
_ANGLE = r"<lora:(?P<aname>[^:>]+):(?P<as>[\d.]+)(?::(?P<asb>[\d.]+))?>"
LORA_TAG_RE = re.compile(f"(?:{_LEGACY})|(?:{_ANGLE})")


@dataclass(frozen=True)
class LoraSpec:
    name: str
    strength: float
    strength_b: float | None = None

    def secondary_strength(self) -> float:
        return self.strength if self.strength_b is None else self.strength_b


def _parse_match(name: str, strength_s: str, strength_b_s: str | None) -> LoraSpec | None:
    name_clean = str(name).strip()
    if not name_clean:
        return None
    try:
        strength = float(strength_s)
    except (TypeError, ValueError):
        return None
    strength_b: float | None = None
    if strength_b_s is not None and str(strength_b_s).strip() != "":
        try:
            strength_b = float(strength_b_s)
        except (TypeError, ValueError):
            return None
    return LoraSpec(name=name_clean, strength=strength, strength_b=strength_b)


def _spec_from_match(match: re.Match) -> LoraSpec | None:
    g = match.groupdict()
    if g.get("aname") is not None:
        return _parse_match(g["aname"], g["as"], g.get("asb"))
    return _parse_match(g["lname"], g["ls"], g.get("lsb"))


def find_lora_tags(text: str) -> list[LoraSpec]:
    if not text:
        return []
    specs: list[LoraSpec] = []
    for match in LORA_TAG_RE.finditer(text):
        spec = _spec_from_match(match)
        if spec:
            specs.append(spec)
    return specs


def find_lora_tags_unique(text: str) -> list[LoraSpec]:
    by_name: dict[str, LoraSpec] = {}
    for spec in find_lora_tags(text):
        by_name[spec.name] = spec
    return list(by_name.values())


def merge_lora_specs(*texts: str) -> list[LoraSpec]:
    by_name: dict[str, LoraSpec] = {}
    for text in texts:
        for spec in find_lora_tags(text):
            by_name[spec.name] = spec
    return list(by_name.values())


def has_lora_named(text: str, name: str) -> bool:
    """Whether ``text`` carries a tag for ``name`` in either notation."""
    return any(spec.name == name for spec in find_lora_tags(text))


_SPACES = re.compile(r"[ \t]{2,}")


def strip_lora_tags(text: str) -> str:
    """The text without its tags; the hole each tag leaves is closed on its own line."""
    if not text:
        return ""
    out = LORA_TAG_RE.sub("", text)
    return "\n".join(_SPACES.sub(" ", line).strip() for line in out.split("\n")).strip()


def format_lora_tag(spec: LoraSpec) -> str:
    if spec.strength_b is None:
        return f"<lora:{spec.name}:{spec.strength:g}>"
    return f"<lora:{spec.name}:{spec.strength:g}:{spec.strength_b:g}>"


def lora_strength_for_normalization(text: str) -> float:
    """Sum first-segment strengths for lane saturation (ignores optional second value)."""
    return sum(spec.strength for spec in find_lora_tags(text))


def scale_lora_spec(spec: LoraSpec, mult: float) -> LoraSpec:
    if mult == 1.0:
        return spec
    return LoraSpec(
        name=spec.name,
        strength=spec.strength * mult,
        strength_b=spec.strength_b * mult if spec.strength_b is not None else None,
    )
