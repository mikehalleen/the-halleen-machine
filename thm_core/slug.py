"""Slugs, per-bin handles, and prompt tokens (design section 5).

A handle is the token a user types in a prompt to mean one reference in one
bin: ``.mara``, ``.mara.2``. It is generated once when the reference is bound,
stored on the reference, and rewritten into the workflow's notation at submit.
The handle base is the slug of the referenced bin's name; a bin referencing its
own media gets its own name.
"""

from __future__ import annotations

import re
from typing import Iterable

from .ids import short_id

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# A dot, a slug, and an optional dot plus digits. The lookbehind stops ``end.next``
# and ``..mara`` from matching. The lookahead means the counter is consumed only as
# a complete digit run, so ``.mara.`` at sentence end reads as ``.mara`` plus a
# period and ``.mara.2b`` reads as ``.mara`` followed by text.
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9\-.])\.([a-z0-9][a-z0-9-]*)(?:\.(\d+))?(?![A-Za-z0-9\-])")


def slugify(name: str) -> str:
    """Lowercase; every run of non-alphanumerics becomes one hyphen; trimmed."""
    s = _NON_ALNUM.sub("-", str(name or "").lower()).strip("-")
    return s or "bin"


def handle_base(handle: str) -> str:
    """Strip a trailing ``.<digits>`` counter."""
    head, dot, tail = str(handle).rpartition(".")
    if dot and tail.isdigit():
        return head
    return handle


def _bases_of_other_bins(bin_, ref_bin_id: str) -> set[str]:
    return {handle_base(r.handle) for r in bin_.references if r.bin != ref_bin_id}


def _choose_base(ref_bin, taken: set[str]) -> str:
    base = slugify(ref_bin.name)
    if base in taken:
        base = f"{base}-{short_id(ref_bin.id)}"
    return base


def assign_handle(bin_, ref_bin) -> str:
    """Mint the handle for a new reference to ``ref_bin`` in ``bin_``.

    Numbered against the references that are actually bound right now, not against a counter of
    everything the bin has ever had: two references to one bin read ``.mara`` and ``.mara.2``,
    and a lone one always reads ``.mara``. Referencing the same bin twice is the rare case, so a
    number showing up should itself be rare and mean something. The base is stable for a
    referenced bin within a bin: a second reference reuses the base the first one got, collision
    suffix included.
    """
    existing = [r for r in bin_.references if r.bin == ref_bin.id]
    if existing:
        base = handle_base(existing[0].handle)
    else:
        base = _choose_base(ref_bin, _bases_of_other_bins(bin_, ref_bin.id))
    taken = {r.handle for r in existing}
    n = 1
    while (base if n == 1 else f"{base}.{n}") in taken:
        n += 1
    return base if n == 1 else f"{base}.{n}"


def renumber_handles(bin_, ref_bin_id: str) -> dict[str, str]:
    """Close the gap after a reference is unbound: the referenced bin's remaining references
    become 1..n in the order they sit, so removing the first of two leaves ``.mara``, not a lone
    ``.mara.2``.

    Returns the old->new handle mapping and rewrites the bin's own prompt and negative with it,
    so what you typed keeps pointing at the same picture. Only the unbound bin's handles move;
    every other token in the prompt is left alone.
    """
    mine = [r for r in bin_.references if r.bin == ref_bin_id]
    if not mine:
        return {}
    base = handle_base(mine[0].handle)
    mapping: dict[str, str] = {}
    for n, r in enumerate(mine, start=1):
        new = base if n == 1 else f"{base}.{n}"
        if new != r.handle:
            mapping[r.handle] = new
    if mapping:
        for r in mine:
            r.handle = mapping.get(r.handle, r.handle)
        bin_.prompt = rewrite_tokens(bin_.prompt, mapping)
        bin_.negative = rewrite_tokens(bin_.negative, mapping)
    return mapping


def _suffix_of(handle: str) -> str:
    _, dot, tail = str(handle).rpartition(".")
    return f".{tail}" if dot and tail.isdigit() else ""


def rename_bin_handles(project, bin_id: str, new_name: str) -> int:
    """Rename a bin and rewrite its handles and prompt tokens in every bin that references it,
    itself included.

    Returns the number of referencing bins touched. Nothing on disk changes.
    """
    ref_bin = project.bins_by_id[bin_id]
    ref_bin.name = new_name
    touched = 0
    for bin_ in project.bins_by_id.values():
        mine = [r for r in bin_.references if r.bin == bin_id]
        if not mine:
            continue
        base = _choose_base(ref_bin, _bases_of_other_bins(bin_, bin_id))
        mapping: dict[str, str] = {}
        for r in mine:
            new = base + _suffix_of(r.handle)
            if new != r.handle:
                mapping[r.handle] = new
                r.handle = new
        if mapping:
            bin_.prompt = rewrite_tokens(bin_.prompt, mapping)
            bin_.negative = rewrite_tokens(bin_.negative, mapping)
        touched += 1
    return touched


def find_tokens(text: str) -> list[tuple[int, int, str]]:
    """Every ``.handle`` token in ``text`` as (start, end, handle)."""
    out: list[tuple[int, int, str]] = []
    for m in TOKEN_RE.finditer(text or ""):
        handle = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
        out.append((m.start(), m.end(), handle))
    return out


def rewrite_tokens(text: str, mapping: dict[str, str], *, keep_dot: bool = True) -> str:
    """Replace each known token in one pass, so chained renames cannot double-replace.

    ``mapping`` maps a handle to its replacement. With ``keep_dot`` the replacement
    is written as ``.<value>`` (handle to handle); without it the value is written
    verbatim (handle to model-facing token).
    """
    if not text:
        return text or ""

    def _sub(m: re.Match) -> str:
        handle = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
        if handle not in mapping:
            return m.group(0)
        value = mapping[handle]
        return f".{value}" if keep_dot else value

    return TOKEN_RE.sub(_sub, text)


def handles_in(text: str) -> set[str]:
    return {h for _, _, h in find_tokens(text)}


def all_handles(references: Iterable) -> set[str]:
    return {r.handle for r in references}
