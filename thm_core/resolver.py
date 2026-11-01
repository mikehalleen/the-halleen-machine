"""Reference resolution: a bin's references to files and ordinals (design section 5)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from .models import Bin, Media, Project, Reference


@dataclass
class ResolvedReference:
    reference: Reference
    media: Media | None
    bin: Bin | None  # the bin the handle is named from
    path: str
    exists: bool
    ordinal: int | None
    note: str | None = None

    @property
    def handle(self) -> str:
        return self.reference.handle

    @property
    def media_type(self) -> str | None:
        return self.media.type if self.media else None


def resolve_references(
    project: Project,
    bin_: Bin,
    *,
    isfile: Callable[[str], bool] = os.path.isfile,
) -> list[ResolvedReference]:
    """Walk the bin's references in order and rank the ones that resolve to a file.

    The ordinal counts references of the same media type, in bin order, skipping
    any whose file is missing. Nothing is dropped; a missing file simply has no
    ordinal and carries a note.
    """
    counts: dict[str, int] = {}
    out: list[ResolvedReference] = []
    for ref in bin_.references:
        media = project.media_by_id.get(ref.media)
        ref_bin = project.bins_by_id.get(ref.bin)
        if media is None:
            out.append(ResolvedReference(ref, None, ref_bin, "", False, None, "unknown media"))
            continue
        exists = bool(media.path) and isfile(media.path)
        if not exists:
            out.append(ResolvedReference(ref, media, ref_bin, media.path, False, None, "missing file"))
            continue
        counts[media.type] = counts.get(media.type, 0) + 1
        out.append(ResolvedReference(ref, media, ref_bin, media.path, True, counts[media.type]))
    return out
