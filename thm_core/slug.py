"""Slugs, per-bin handles, and prompt tokens (design section 5).

A handle is the token a user types in a prompt to mean one reference in one
bin: ``@mara.png``, ``@mara.png.2``. It is generated once when the reference is
bound, stored on the reference, and rewritten into the workflow's notation at
submit. The handle base is the slug of the referenced bin's name plus the media
file's extension, so a still and a clip from the same bin read ``@mara.png`` and
``@mara.mp4``; a bin referencing its own media gets its own name. Duplicates
within one (bin, extension) count up: ``@mara.png.2``.

The prefix is ``@`` rather than ``.``: a leading dot reads as sentence punctuation
to a phone keyboard, which then moves or spaces it.
"""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Iterable

from .ids import short_id

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# An at-sign, a slug, a dot and an extension, and an optional dot plus digits. The
# extension starts with a letter, so ``.2`` can never be read as one. The lookbehind
# stops ``a@b``, ``@@mara.png`` and email-shaped ``me@mara.png`` from matching. The
# lookahead means the counter is consumed only as a complete digit run, so
# ``@mara.png.`` at sentence end reads as ``@mara.png`` plus a period and
# ``@mara.png.2b`` reads as ``@mara.png`` followed by text.
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9\-@])@([a-z0-9][a-z0-9-]*)\.([a-z][a-z0-9]{0,4})(?:\.(\d+))?(?![A-Za-z0-9\-])")

PREFIX = "@"

# what a media file with no usable extension is called in a handle
FALLBACK_EXT = "file"

_EXT_RE = re.compile(r"^[a-z][a-z0-9]{0,4}$")


def slugify(name: str) -> str:
    """Lowercase; every run of non-alphanumerics becomes one hyphen; trimmed."""
    s = _NON_ALNUM.sub("-", str(name or "").lower()).strip("-")
    return s or "bin"


def media_ext(path: str | None) -> str:
    """The extension a handle carries for a media file: lowercased, no dot (``png``, ``mp4``).
    One that the token grammar cannot spell (empty, starts with a digit, longer than five)
    becomes ``file``."""
    ext = PurePath(str(path or "")).suffix.lstrip(".").lower()
    return ext if _EXT_RE.match(ext) else FALLBACK_EXT


def handle_base(handle: str) -> str:
    """Strip a trailing ``.<digits>`` counter: ``mara.png.2`` to ``mara.png``."""
    head, dot, tail = str(handle).rpartition(".")
    if dot and tail.isdigit():
        return head
    return handle


def handle_ext(handle: str) -> str:
    """The extension part of a handle: ``mara.png.2`` to ``png``."""
    _, _, ext = handle_base(handle).rpartition(".")
    return ext


def _handle_slug(handle: str) -> str:
    """The slug part of a handle, collision suffix included: ``mara-ab12.png.2`` to ``mara-ab12``."""
    slug, _, _ = handle_base(handle).rpartition(".")
    return slug


def _bases_of_other_bins(bin_, ref_bin_id: str) -> set[str]:
    return {handle_base(r.handle) for r in bin_.references if r.bin != ref_bin_id}


def _choose_base(slug: str, ext: str, ref_bin_id: str, taken: set[str]) -> str:
    base = f"{slug}.{ext}"
    if base in taken:
        base = f"{slug}-{short_id(ref_bin_id)}.{ext}"
    return base


def _numbered(base: str, n: int) -> str:
    return base if n == 1 else f"{base}.{n}"


def assign_handle(bin_, ref_bin, ext: str) -> str:
    """Mint the handle for a new reference to ``ref_bin`` in ``bin_`` for a media with extension
    ``ext`` (see ``media_ext``).

    Numbered against the references that are actually bound right now, not against a counter of
    everything the bin has ever had: two stills from one bin read ``mara.png`` and ``mara.png.2``,
    and a lone one always reads ``mara.png``. A clip from the same bin is its own series:
    ``mara.mp4``. Referencing the same bin twice is the rare case, so a number showing up should
    itself be rare and mean something. The slug is stable for a referenced bin within a bin: a
    second reference reuses the slug the first one got, collision suffix included.
    """
    mine = [r for r in bin_.references if r.bin == ref_bin.id]
    same = [r for r in mine if handle_ext(r.handle) == ext]
    if same:
        base = handle_base(same[0].handle)
    else:
        slug = _handle_slug(mine[0].handle) if mine else bin_slug(ref_bin)
        base = _choose_base(slug, ext, ref_bin.id, _bases_of_other_bins(bin_, ref_bin.id))
    taken = {r.handle for r in same}
    n = 1
    while _numbered(base, n) in taken:
        n += 1
    return _numbered(base, n)


def renumber_handles(bin_, ref_bin_id: str) -> dict[str, str]:
    """Close the gap after a reference is unbound: the referenced bin's remaining references
    become 1..n per extension, in the order they sit, so removing the first of two stills
    leaves ``mara.png``, not a lone ``mara.png.2``.

    Returns the old->new handle mapping and rewrites the bin's own prompt and negative with it,
    so what you typed keeps pointing at the same picture. Only the unbound bin's handles move;
    every other token in the prompt is left alone.
    """
    mine = [r for r in bin_.references if r.bin == ref_bin_id]
    if not mine:
        return {}
    mapping: dict[str, str] = {}
    for ext in dict.fromkeys(handle_ext(r.handle) for r in mine):
        group = [r for r in mine if handle_ext(r.handle) == ext]
        base = handle_base(group[0].handle)
        for n, r in enumerate(group, start=1):
            new = _numbered(base, n)
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


def bin_slug(bin_) -> str:
    """The handle base a bin's references are minted from: its frozen ``slug``, or (a record
    from before slugs were stored) its name slugified, which is what the slug would have been."""
    return getattr(bin_, "slug", "") or slugify(bin_.name)


def rename_bin_handles(project, bin_id: str, new_name: str) -> int:
    """Retitle a bin *and* move its handle base to the new title's slug, rewriting prompt
    tokens in every bin that references it. The old whole-rename; ``set_bin_field name`` no
    longer does this (a title is not a handle), ``set_bin_field slug`` does the second half."""
    project.bins_by_id[bin_id].name = new_name
    return reslug_bin_handles(project, bin_id, slugify(new_name))


def reslug_bin_handles(project, bin_id: str, new_slug: str) -> int:
    """Change the handle base a bin is addressed by and rewrite its handles and prompt tokens
    in every bin that references it, itself included. The title is untouched.

    Returns the number of referencing bins touched. Nothing on disk changes.
    """
    ref_bin = project.bins_by_id[bin_id]
    ref_bin.slug = new_slug
    touched = 0
    for bin_ in project.bins_by_id.values():
        mine = [r for r in bin_.references if r.bin == bin_id]
        if not mine:
            continue
        taken = _bases_of_other_bins(bin_, bin_id)
        mapping: dict[str, str] = {}
        for r in mine:
            base = _choose_base(new_slug, handle_ext(r.handle), bin_id, taken)
            new = base + _suffix_of(r.handle)
            if new != r.handle:
                mapping[r.handle] = new
                r.handle = new
        if mapping:
            bin_.prompt = rewrite_tokens(bin_.prompt, mapping)
            bin_.negative = rewrite_tokens(bin_.negative, mapping)
        touched += 1
    return touched


def _handle_of(m: re.Match) -> str:
    return m.group(1) + "." + m.group(2) + (f".{m.group(3)}" if m.group(3) else "")


def find_tokens(text: str) -> list[tuple[int, int, str]]:
    """Every ``@handle`` token in ``text`` as (start, end, handle)."""
    out: list[tuple[int, int, str]] = []
    for m in TOKEN_RE.finditer(text or ""):
        out.append((m.start(), m.end(), _handle_of(m)))
    return out


def rewrite_tokens(text: str, mapping: dict[str, str], *, keep_prefix: bool = True) -> str:
    """Replace each known token in one pass, so chained renames cannot double-replace.

    ``mapping`` maps a handle to its replacement. With ``keep_prefix`` the replacement
    is written as ``@<value>`` (handle to handle); without it the value is written
    verbatim (handle to model-facing token).
    """
    if not text:
        return text or ""

    def _sub(m: re.Match) -> str:
        handle = _handle_of(m)
        if handle not in mapping:
            return m.group(0)
        value = mapping[handle]
        return f"{PREFIX}{value}" if keep_prefix else value

    return TOKEN_RE.sub(_sub, text)


def handles_in(text: str) -> set[str]:
    return {h for _, _, h in find_tokens(text)}


def all_handles(references: Iterable) -> set[str]:
    return {r.handle for r in references}
