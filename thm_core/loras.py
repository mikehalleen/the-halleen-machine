"""LoRA tags typed into a prompt, lifted into the shot's ``loras`` field.

The prompt never stores a LoRA: the field does, and the assembler writes the tags back in
front of the submitted prompt at generate time. A tag someone types anyway -- either the
current ``<lora:name:s[:s2]>`` form or the 1.x ``__lora:name:s[:s2]__`` form -- is not an
error, it is intent: it comes out of the text and goes into the field, where the chip row
shows it. ``scripts/lora_tags.py`` reads the same two notations on the runner side.
"""

from __future__ import annotations

import re

from .models import Lora

_LEGACY = r"__lora:(?P<lname>[^:]+):(?P<ls>[\d.]+)(?::(?P<lsb>[\d.]+))?__"
_ANGLE = r"<lora:(?P<aname>[^:>]+):(?P<as>[\d.]+)(?::(?P<asb>[\d.]+))?>"
LORA_TAG_RE = re.compile(f"(?:{_LEGACY})|(?:{_ANGLE})")
_SPACES = re.compile(r"[ \t]{2,}")


def _lora_from(match: re.Match) -> Lora | None:
    g = match.groupdict()
    name, s, sb = (g["aname"], g["as"], g["asb"]) if g.get("aname") is not None else (g["lname"], g["ls"], g["lsb"])
    name = str(name).strip()
    if not name:
        return None
    try:
        return Lora(name=name, strength=float(s), strength_b=float(sb) if sb not in (None, "") else None)
    except ValueError:
        return None


def lift_lora_tags(text: str, loras: list[Lora] | None = None) -> tuple[str, list[Lora]]:
    """``(text without tags, loras)``: the given list with every tag in ``text`` merged in.

    A typed tag for a name already on the list replaces that entry in place (the typed value
    is the newer intent); new names append in the order typed. The hole each tag leaves is
    closed on its own line.
    """
    merged: list[Lora] = [lora.model_copy() for lora in (loras or [])]
    if not text:
        return "", merged
    for match in LORA_TAG_RE.finditer(text):
        lora = _lora_from(match)
        if lora is None:
            continue
        for i, have in enumerate(merged):
            if have.name == lora.name:
                merged[i] = lora
                break
        else:
            merged.append(lora)
    clean = LORA_TAG_RE.sub("", text)
    clean = "\n".join(_SPACES.sub(" ", line).strip() for line in clean.split("\n")).strip()
    return clean, merged
