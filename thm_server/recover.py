"""Generation records rebuilt from the media itself, on the server's terms (design: media first).

``thm_core.provenance`` reads the ComfyUI graph a file carries and turns it into the record
the log is missing. The server calls it where a file without a record shows up: an upload
landing in a bin, a recipe asked of plain media, a bin's history being read. The log is a
cache of the file, so a file is read once per process for a given content hash and the
answer, record or nothing, sticks until the server restarts. Every entry point runs under
the project lock; the caller saves and publishes when something was recovered.
"""

from __future__ import annotations

from thm_core import Bin, Media, Project
from thm_core.provenance import Recovery, recover_generation

from .deps import ServerState


def output_root_for(st: ServerState, project: Project) -> str | None:
    return (project.comfy.output_root or "").strip() or str(st.settings.comfy.output_root or "").strip() or None


def _has_record(st: ServerState, project: Project, media: Media) -> bool:
    return media.origin.type == "generation" and bool(media.origin.generation) and st.store.read_generation(project, media.origin.generation) is not None


# derived files: a graph they carry belongs to what they were cut from, not to them
DERIVED_ORIGINS = ("export", "clip", "frame", "gif")


def recover_media(st: ServerState, slug: str, project: Project, media: Media) -> Recovery | None:
    """Blocking (ffprobe, Pillow); call in a threadpool under the project lock."""
    if _has_record(st, project, media) or media.origin.type in DERIVED_ORIGINS:
        return None
    key = (slug, media.id, media.sha256 or media.path)
    if key in st.provenance_probed:
        return None
    st.provenance_probed.add(key)
    return recover_generation(st.store, project, media, workflows_dir=st.settings.workflows_dir, output_root=output_root_for(st, project))


def sweep_bin(st: ServerState, slug: str, project: Project, bin_: Bin) -> list[Recovery]:
    """Every media in the bin that has no record and whose file carries one. Blocking, under the lock."""
    out: list[Recovery] = []
    for media_id in list(bin_.media):
        media = project.media_by_id.get(media_id)
        if media is None or _has_record(st, project, media):
            continue
        rec = recover_media(st, slug, project, media)
        if rec is not None:
            out.append(rec)
    return out
