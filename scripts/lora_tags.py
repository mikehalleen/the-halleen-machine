"""Parse and format ``__lora:...__`` tags embedded in THM prompt text."""

from __future__ import annotations

import re
from dataclasses import dataclass


LORA_TAG_RE = re.compile(r"__lora:([^:]+):([\d.]+)(?::([\d.]+))?__")


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


def find_lora_tags(text: str) -> list[LoraSpec]:
    if not text:
        return []
    specs: list[LoraSpec] = []
    for match in LORA_TAG_RE.finditer(text):
        spec = _parse_match(match.group(1), match.group(2), match.group(3))
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


def strip_lora_tags(text: str) -> str:
    if not text:
        return ""
    return LORA_TAG_RE.sub("", text).strip()


def format_lora_tag(spec: LoraSpec) -> str:
    if spec.strength_b is None:
        return f"__lora:{spec.name}:{spec.strength:g}__"
    return f"__lora:{spec.name}:{spec.strength:g}:{spec.strength_b:g}__"


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
