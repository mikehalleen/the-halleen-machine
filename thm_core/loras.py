"""LoRA tags typed into a prompt, lifted into the shot's ``loras`` field.

The prompt never stores a LoRA: the field does, and the assembler writes the tags back in
front of the submitted prompt at generate time. A tag someone types anyway -- either the
current ``<lora:name:s[:s2]>`` form or the 1.x ``__lora:name:s[:s2]__`` form -- is not an
error, it is intent: it comes out of the text and goes into the field, where the chip row
shows it. ``scripts/lora_tags.py`` reads the same two notations on the runner side.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from .models import Lora

LORA_SUFFIXES = (".safetensors", ".pt", ".ckpt")

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


def list_lora_names(loras_dir: Path | str | None) -> list[str]:
    """Every LoRA under ``[paths] loras``, named the way ComfyUI's loader wants it: the path
    relative to that folder with forward slashes, extension included. Empty when the folder is
    unset or missing."""
    if loras_dir is None or not Path(loras_dir).is_dir():
        return []
    root = Path(loras_dir)
    out = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in LORA_SUFFIXES and not path.name.startswith(".")
    ]
    out.sort(key=str.lower)
    return out


def resolve_lora_name(name: str, available: list[str]) -> str | None:
    """The one file ``name`` means, spelled exactly as ComfyUI lists it, or None.

    Forgiving about what a typist drops, never about which file: exact, then ignoring case, then
    with the extension left off, then without the folder. A step that matches two files is no
    match at all (``style`` in two folders is a question, not a guess).
    """
    want = str(name).strip().replace("\\", "/")
    if not want:
        return None
    if want in available:
        return want
    low = want.lower()

    def stem(n: str) -> str:
        for suffix in LORA_SUFFIXES:
            if n.lower().endswith(suffix):
                return n[: -len(suffix)]
        return n

    def base(n: str) -> str:
        return n.rsplit("/", 1)[-1]

    for key in (
        lambda n: n.lower() == low,
        lambda n: stem(n).lower() == stem(want).lower(),
        lambda n: base(n).lower() == base(want).lower(),
        lambda n: stem(base(n)).lower() == stem(base(want)).lower(),
    ):
        hits = [n for n in available if key(n)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


def near_lora_names(name: str, available: list[str], n: int = 3) -> list[str]:
    """The closest few files to an unknown ``name``, for the refusal to suggest: compared by
    the bare file name, so an extension or folder on either side does not hide a near miss."""

    def bare(x: str) -> str:
        x = x.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
        for suffix in LORA_SUFFIXES:
            if x.endswith(suffix):
                return x[: -len(suffix)]
        return x

    keyed: dict[str, list[str]] = {}
    for a in available:
        keyed.setdefault(bare(a), []).append(a)
    hits = difflib.get_close_matches(bare(name), list(keyed), n=n, cutoff=0.6)
    return [f for h in hits for f in keyed[h]][:n]
