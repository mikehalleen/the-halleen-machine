"""The command layer (design section 6).

Every write to a project, from the UI or the agent, is a typed command. The
handler validates it, applies it, and appends a change record carrying enough
before and after state to undo it. Agent changes carry a decision; the user
approves, rejects, or reopens them through ``decide``.

Two structural gates live here so the server can enforce them without
duplicating logic: an agent needs a live session, and an agent ``generate``
must have read its inbox.
"""

from __future__ import annotations

import copy
import os
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from . import ids
from .assembler import AssembledPrompt, AssemblyWarning, Capacity, assemble, sidecar_path, workflow_capacity
from .ids import utc_now
from .loras import lift_lora_tags, list_lora_names, near_lora_names, resolve_lora_name
from .media import media_type_for
from .models import (
    Actor,
    AgentSession,
    Bin,
    Change,
    ChangeTarget,
    Decision,
    Generation,
    ItemMix,
    Kind,
    Lora,
    Media,
    Message,
    MessageContext,
    MessageFile,
    Post,
    PostPlatform,
    Project,
    QcMark,
    QcStatus,
    Reference,
    Sequence,
    SequenceItem,
    SessionMode,
    SoundLayer,
    STILL_DEFAULT_S,
    TAKE_ORIGINS,
    YT_DESCRIPTION_MAX_BYTES,
    YT_TAGS_MAX_CHARS,
    YT_TITLE_MAX,
    stamp_take,
)
from .resolver import resolve_references
from .slug import assign_handle, handle_base, handle_ext, handles_in, media_ext, renumber_handles, reslug_bin_handles, rewrite_tokens, slugify
from .store import ProjectStore
from .workflow_names import WORKFLOWS_DIR, resolve_curated_workflow


class CommandRefused(Exception):
    """A command that does not fit the model. ``code`` is machine-readable."""

    def __init__(self, code: str, detail: str = "", extra: dict | None = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.extra = extra or {}


@dataclass
class CommandContext:
    """Who is asking. Supplied by the caller, never part of the payload."""

    actor: Actor
    session: str | None = None
    inbox_cursor: int | None = None
    propose: bool = False
    now: str | None = None
    bypass_gates: bool = False  # internal: set only by decide() when it executes an approved proposal
    # the session mode the agent last saw (``mode_token``): a change since refuses its next
    # command with ``mode_changed``, so a plan made in one mode never runs on in another
    seen_mode: str | None = None

    def ts(self) -> str:
        return self.now or utc_now()


@dataclass
class Env:
    """Where to find workflows and how to check files. Injectable for tests."""

    workflows_dir: Path = WORKFLOWS_DIR
    isfile: Callable[[str], bool] = os.path.isfile
    # [paths] loras: unset (tests, a server with no folder configured) checks no lora names
    loras_dir: Path | None = None
    # what the server knows about a YouTube channel id: ``{id, title, handle}`` for a connected
    # one, None for an unknown one. Unset (core tests): channels are not checked and lines carry
    # the id.
    youtube_channel: Callable[[str], dict | None] | None = None
    _capacity: dict[str, tuple[tuple, Capacity]] = field(default_factory=dict)

    def channel_label(self, channel_id: str) -> str:
        """``@halleen`` for a connected channel, else the id as given."""
        if not channel_id:
            return ""
        info = self.youtube_channel(channel_id) if self.youtube_channel is not None else None
        if info:
            return str(info.get("handle") or info.get("title") or channel_id)
        return channel_id

    def channel_known(self, channel_id: str) -> bool:
        """False only when the server can say the channel has no token here."""
        if not channel_id or self.youtube_channel is None:
            return bool(channel_id)
        return self.youtube_channel(channel_id) is not None

    def lora_names(self) -> list[str] | None:
        """The LoRA files ComfyUI can load, or None when there is no folder to check against."""
        if self.loras_dir is None or not Path(self.loras_dir).is_dir():
            return None
        return list_lora_names(self.loras_dir)

    def resolve_loras(self, loras: list, *, strict: bool = True) -> list[Lora]:
        """``loras`` with every name spelled the way the file is (extension, folder, case). An
        entry no file answers to is refused (``unknown_lora``), or with ``strict=False`` kept as
        typed for the render check to refuse later (a recalled recipe still lands)."""
        out = [Lora.model_validate(l if isinstance(l, dict) else _dump(l)) for l in loras]
        names = self.lora_names()
        if names is None:
            return out
        for lora in out:
            hit = resolve_lora_name(lora.name, names)
            if hit is not None:
                lora.name = hit
            elif strict:
                near = near_lora_names(lora.name, names)
                raise CommandRefused(
                    "unknown_lora",
                    f"no lora file matches {lora.name!r}"
                    + (f"; closest: {', '.join(near)}" if near else "")
                    + " -- `loras` lists the files; use the name exactly as listed",
                    {"lora": lora.name, "closest": near},
                )
        return out

    def _stamp(self, workflow: str) -> tuple:
        """What the scan read: the workflow file and its sidecar, by size and mtime. A workflow
        edited in ComfyUI and saved over (a new THM-IsDraft switch, another reference slot) is
        scanned again on the next look, with no server restart."""

        def stat(path) -> tuple | None:
            try:
                st = os.stat(path)
                return (str(path), st.st_size, st.st_mtime_ns)
            except (OSError, TypeError):
                return None

        return (stat(resolve_curated_workflow(workflow, self.workflows_dir)), stat(sidecar_path(workflow, self.workflows_dir)))

    def capacity(self, workflow: str) -> Capacity:
        stamp = self._stamp(workflow)
        hit = self._capacity.get(workflow)
        if hit is None or hit[0] != stamp:
            hit = (stamp, workflow_capacity(workflow, self.workflows_dir))
            self._capacity[workflow] = hit
        return hit[1]

    def workflow_exists(self, workflow: str) -> bool:
        return bool(workflow) and self.capacity(workflow).found

    def kind(self, workflow: str) -> Kind:
        """What the workflow makes. A bin with no workflow yet reads as image."""
        return self.capacity(workflow).kind if workflow else "image"


@dataclass
class Applied:
    target: ChangeTarget
    generation: Generation | None = None
    assembled: AssembledPrompt | None = None
    warnings: list[AssemblyWarning] = field(default_factory=list)  # for commands with no assembly of their own


def token_warnings(project: Project, bin_: Bin, env: Env) -> list[AssemblyWarning]:
    """The handle scan ``generate`` runs, at prompt-write time: an ``@token`` in the prompt or
    negative that no reference answers to, and (on workflows that address by token) a bound
    handle the prompt never mentions. The same words the render would warn with, earlier."""
    used = handles_in(bin_.prompt) | handles_in(bin_.negative)
    known = {r.handle for r in bin_.references}
    out = [AssemblyWarning("unknown_token", h, f"@{h} matches no reference in this bin") for h in sorted(used - known)]
    if bin_.workflow and env.capacity(bin_.workflow).addresses_by_token:
        out += [AssemblyWarning("unused_handle", h, f"@{h} appears nowhere in the prompt") for h in sorted(known - used)]
    return out


@dataclass
class CommandResult:
    project: Project
    change: Change | None = None
    generation: Generation | None = None
    warnings: list[dict] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    # a propose_shots batch: one child generate change per rendered item, and its generation
    changes: list[Change] = field(default_factory=list)
    generations: list[Generation] = field(default_factory=list)
    # the bin the command made or bound into (create_bin, duplicate_bin, start_from_media), so
    # the caller can go there without finding it by name
    bin: str | None = None
    # the sequence the command made (create_sequence, clone_sequence)
    sequence: str | None = None
    # the post the command made (create_post)
    post: str | None = None

    def all_generations(self) -> list[Generation]:
        return [g for g in (self.generation, *self.generations) if g is not None]


# -- record access -----------------------------------------------------------


def _bin(project: Project, bin_id: str) -> Bin:
    bin_ = project.bins_by_id.get(bin_id)
    if bin_ is None:
        raise CommandRefused("unknown_id", f"no bin {bin_id}", {"type": "bin", "id": bin_id})
    return bin_


def _media_label(bin_: Bin, media: Media) -> str:
    """`take 3` for a take generated in this bin; `file 2` (its place in the gallery) otherwise --
    the same words the bin screen shows under the tile."""
    if media.take and media.origin is not None and media.origin.type in TAKE_ORIGINS and media.origin.bin == bin_.id:
        return f"take {media.take}"
    ids = list(bin_.media or [])
    return f"file {ids.index(media.id) + 1}" if media.id in ids else media.id




def _media(project: Project, media_id: str) -> Media:
    media = project.media_by_id.get(media_id)
    if media is None:
        raise CommandRefused("unknown_id", f"no media {media_id}", {"type": "media", "id": media_id})
    return media


def _bin_for(project: Project, media: Media) -> Bin | None:
    """The bin a media belongs to: where it landed, else the first gallery holding it. None for
    a media no bin holds any more (its origin bin was deleted); such a media cannot be
    referenced, since the handle is minted from the bin's name."""
    if media.origin and media.origin.bin and media.origin.bin in project.bins_by_id:
        return project.bins_by_id[media.origin.bin]
    for bin_ in project.bins_by_id.values():
        if media.id in bin_.media:
            return bin_
    return None


def _ref_bin(project: Project, media: Media) -> Bin:
    bin_ = _bin_for(project, media)
    if bin_ is None:
        raise CommandRefused("no_bin", f"{media.id} is in no bin; add it to one first", {"type": "media", "id": media.id})
    return bin_


def _not_audio_reference(media: Media) -> None:
    """No workflow takes an audio reference yet. One place to lift when audio-conditioned
    workflows arrive: every bind path checks here before capacity."""
    if media.type == "audio":
        raise CommandRefused("audio_reference", f"{media.id} is audio; no workflow takes an audio reference", {"type": "media", "id": media.id})


def _not_private(project: Project) -> None:
    """A private project has no agent session: the agent may never see it."""
    if project.private:
        raise CommandRefused("private_project", "a private project has no agent session; turn private off first")


def _record(project: Project, target: ChangeTarget):
    if target.type == "project":
        return project
    if target.type == "bin":
        return _bin(project, target.id)
    if target.type == "media":
        return _media(project, target.id)
    if target.type == "session":
        if project.agent_session is None:
            raise CommandRefused("unknown_id", "no session", {"type": "session", "id": target.id})
        return project.agent_session
    raise CommandRefused("bad_target", target.type)


def _dump(record) -> dict:
    return record.model_dump(mode="json")


def _assign(record, data: dict) -> None:
    """Replace a record's fields with validated values, keeping the object identity."""
    updated = type(record).model_validate(data)
    for name in type(record).model_fields:
        setattr(record, name, getattr(updated, name))


def apply_patch(record, patch: dict) -> None:
    _assign(record, {**_dump(record), **copy.deepcopy(patch)})


def _diff(before: dict, after: dict) -> tuple[dict, dict]:
    """What a command changed, one level into dict-valued fields.

    A project-level change (delete a media, add a bin) used to carry the whole ``media_by_id``
    and ``bins_by_id`` on both sides, so the log grew with the square of the project. For a key
    whose two sides are both dicts only the entries that differ are kept: an entry removed sits
    in ``before`` alone, one added sits in ``after`` alone, one edited sits in both. Scalars and
    lists are recorded whole, as before."""
    b, a = {}, {}
    for key in set(before) | set(after):
        bv, av = before.get(key), after.get(key)
        if bv == av:
            continue
        if isinstance(bv, dict) and isinstance(av, dict):
            changed = [k for k in set(bv) | set(av) if bv.get(k) != av.get(k)]
            b[key] = {k: copy.deepcopy(bv[k]) for k in changed if k in bv}
            a[key] = {k: copy.deepcopy(av[k]) for k in changed if k in av}
        else:
            b[key], a[key] = copy.deepcopy(bv), copy.deepcopy(av)
    return b, a


def _merge_entries(current: dict, side: dict, other_side: dict | None) -> dict:
    """``side``'s entries laid over ``current``; entries only ``other_side`` names are removed."""
    merged = dict(current)
    merged.update(copy.deepcopy(side))
    for k in other_side or {}:
        if k not in side:
            merged.pop(k, None)
    return merged


def apply_delta(record, other_side: dict, side: dict) -> None:
    """Move a record to one side of a change: dict-valued keys merge entry by entry (a
    ``_diff`` lists only the entries that moved), everything else replaces."""
    current = _dump(record)
    patch: dict = {}
    for key, value in side.items():
        other = other_side.get(key)
        if isinstance(value, dict) and isinstance(other, dict) and isinstance(current.get(key), dict):
            patch[key] = _merge_entries(current[key], value, other)
        else:
            patch[key] = value
    apply_patch(record, patch)


def _session_id(project: Project) -> str | None:
    sess = project.agent_session
    if sess and sess.status in ("waiting", "live"):
        return sess.id
    return None


def _fmt_len(v) -> str:
    if v is None:
        return "none"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{int(f)}s" if f.is_integer() else f"{f:g}s"


def _take_number(project: Project, media_id: str) -> tuple[Bin | None, int | None]:
    """The bin a media was generated into and its take number there; ``(bin, None)`` for media
    that is in a bin but was not generated there."""
    media = project.media_by_id.get(media_id)
    if media is None:
        return None, None
    bin_ = _bin_for(project, media)
    if bin_ and media.origin.type in TAKE_ORIGINS and media.origin.bin == bin_.id and media_id in bin_.media:
        return bin_, media.take or bin_.media.index(media_id) + 1
    return bin_, None


RAW_ID_RE = re.compile(r"\b(?:p|b|s|it|m|r|g|c|msg|as|f|sq|rule|pub)_[0-9a-f]{8}\b")


def _words_for(project: Project, raw: str) -> str:
    """What the user sees for an id: `"the invite", take 4` for a take, the bin's name, else a hint."""
    if raw in project.media_by_id:
        media = project.media_by_id[raw]
        bin_ = _bin_for(project, media)
        if bin_ is not None:
            return f'"{bin_.name}", {_media_label(bin_, media)}'
        return "the file by what it shows or its name"
    if raw in project.bins_by_id:
        return f'"{project.bins_by_id[raw].name}"'
    seq = next((q for q in project.sequences if q.id == raw), None)
    if seq is not None:
        return f'sequence "{seq.name}"'
    post = next((q for q in project.posts if q.id == raw), None)
    if post is not None:
        return f"the {post.platform} post {post_label(project, post)}"
    return "what it is, in words (the user never sees ids)"


def check_no_raw_ids(project: Project, text: str) -> None:
    """The user never sees ids: an agent line naming ``m_8c72e7d7`` is refused, with the words to
    use instead (the bin's name and the take or file label the bin screen shows). Attach media
    with ``--attach`` to show it; name it in words."""
    tokens = re.findall(r"\b[a-z]{1,4}_[0-9a-z]+\b", text or "")
    found = list(dict.fromkeys(t for t in tokens if RAW_ID_RE.fullmatch(t) or t in project.media_by_id or t in project.bins_by_id))
    if found:
        swaps = {raw: _words_for(project, raw) for raw in found}
        raise CommandRefused(
            "raw_id",
            "the user never sees ids; write " + "; ".join(f"{words} instead of {raw}" for raw, words in swaps.items()),
            {"ids": swaps},
        )


def bin_label(bin_: Bin) -> str:
    """How a bin is named in prose: ``shot 18 "he agrees"`` for a shot, the title for an asset."""
    return f'shot {bin_.shot} "{bin_.name}"' if bin_.shot else bin_.name


def _take_label(project: Project, media_id: str) -> str:
    bin_, n = _take_number(project, media_id)
    if bin_ and n:
        return f"{bin_label(bin_)} take {n}"
    return f"{bin_label(bin_)} media" if bin_ else media_id


def _mint_shot(project: Project) -> int:
    """The next permanent shot number: the project's counter, never reused."""
    n = int(project.counters.get("shot", 0)) + 1
    project.counters["shot"] = n
    return n


def _new_slug(project: Project, name: str, shot: int | None) -> str:
    """The frozen handle base for a new bin: ``shot-18`` for a shot; for an asset the title's
    slug, made unique among the bins' slugs (``mara``, ``mara-2``) so two bins called the same
    thing do not share a base."""
    if shot:
        return f"shot-{shot}"
    taken = {b.slug for b in project.bins_by_id.values() if b.slug}
    base = slugify(name)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _unique_name(project: Project, wanted: str) -> str:
    """``wanted`` if no bin has it (case-insensitive), else ``wanted-2``, ``wanted-3``, ..."""
    taken = {b.name.strip().lower() for b in project.bins_by_id.values()}
    if wanted.lower() not in taken:
        return wanted
    n = 2
    while f"{wanted}-{n}".lower() in taken:
        n += 1
    return f"{wanted}-{n}"


def _clean_tags(value: Any) -> list[str]:
    """Tags as stored: lowercased, trimmed, empties dropped, duplicates dropped, order kept."""
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise CommandRefused("bad_value", "tags must be a list of strings")
    out: list[str] = []
    for t in value:
        tag = t.strip().lower()
        if tag and tag not in out:
            out.append(tag)
    return out



# a bin's name is its title, a few words: a note pasted as a name is refused, not shortened
MAX_BIN_NAME = 60


def _check_bin_name(name: str | None) -> None:
    if name is None:
        return
    if not str(name).strip():
        raise CommandRefused("bad_value", "bin name is empty")
    if len(str(name).strip()) > MAX_BIN_NAME:
        raise CommandRefused("bad_value", f"a bin name is a title, keep it under {MAX_BIN_NAME} characters")

def next_bin_name(project: Project) -> str:
    """``bin-1``, ``bin-2``, ... the lowest number nothing is using, so a deleted bin's name
    comes back around instead of the counter drifting upward."""
    taken = {b.name.strip().lower() for b in project.bins_by_id.values()}
    n = 1
    while f"bin-{n}" in taken:
        n += 1
    return f"bin-{n}"


# -- commands ----------------------------------------------------------------


class Command(BaseModel):
    """Base for every write. Unknown fields are refused, so an invented key is an error."""

    model_config = ConfigDict(extra="forbid")
    name: ClassVar[str] = ""
    reason: str | None = None
    requested: bool = False
    """The user asked for this in the feed, so it lands done rather than owing a verdict.

    The agent sets it; only the agent can know, since the request arrived as prose in a
    message. Never set it on something the agent decided by itself -- that is the case the
    approval card exists for."""

    quiet: bool = False
    """Work in progress: recorded and undoable, but no card in the feed.

    The agent's own iteration -- rendering a candidate, looking at it, discarding it, rendering
    again -- is not something the user should have to read as a stack of cards, and a card that
    appears empty and fills in later reads as a request rather than a status. Narrate that in
    an agent message instead, and let the card be reserved for the candidate worth a verdict.
    Implies nothing about approval: a quiet change still lands, it simply is not announced."""

    card: bool = False
    """Ask anyway: this one change owes a verdict and shows as a card.

    The manual modes land everything but the renders they ask about; this makes one other
    change a card too. Auto mode ignores it: an auto session owes the user no click at all,
    and its finish is a summary post naming the picks, answered in chat if the user disagrees.
    Redundant on a change that is a card already."""

    def target(self, project: Project) -> ChangeTarget:  # pragma: no cover - overridden
        raise NotImplementedError

    def renders(self, project: Project, env: Env) -> Kind | None:
        """What this command spends GPU time on, ``image`` or ``video``; None for everything else.

        A review session makes the renders it asks about into cards (``video_review`` the
        videos, ``image_review`` every render). Bins, bindings, prompt edits, tags and QC marks
        are the agent's own work and land as a feed line with the reason, in every mode."""
        return None

    def check(self, project: Project, ctx: CommandContext, env: Env) -> None:
        """Raise CommandRefused when the command does not fit the model."""

    def apply(self, project: Project, ctx: CommandContext, env: Env) -> Applied:
        raise NotImplementedError

    def describe(self, project: Project, before: dict, after: dict) -> str:
        return self.name.replace("_", " ")


class CreateBin(Command):
    """A new bin. Without a name it is ``bin-n``; without a workflow it takes the project's
    image default, or none at all, since a bin that only ever holds uploads needs no workflow
    and an unconfigured project must still be able to mint one."""

    name: ClassVar[str] = "create_bin"
    bin_name: str | None = None
    workflow: str | None = None
    length_s: float | None = None
    tags: list[str] = []
    # a shot of the narrative: it gets a permanent number and a @shot-N handle. Off for an
    # asset (a character, a plate, a reference clip), which is named for the thing
    shot: bool = False

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def _workflow(self, project: Project) -> str:
        return str(self.workflow if self.workflow is not None else project.defaults.image_workflow or "").strip()

    def check(self, project, ctx, env) -> None:
        wf = self._workflow(project)
        if wf and not env.workflow_exists(wf):
            raise CommandRefused("unknown_workflow", f"no workflow {wf}")
        _check_bin_name(self.bin_name)
        if self.length_s is not None and self.length_s <= 0:
            raise CommandRefused("bad_value", "length_s must be positive")
        _clean_tags(self.tags)

    def apply(self, project, ctx, env) -> Applied:
        wf = self._workflow(project)
        length = None
        if wf and env.kind(wf) == "video":
            length = self.length_s if self.length_s is not None else project.defaults.video_length_s
        name = str(self.bin_name).strip() if self.bin_name is not None else next_bin_name(project)
        number = _mint_shot(project) if self.shot else None
        bin_ = Bin(
            id=ids.new_id(ids.BIN), name=name, shot=number, slug=_new_slug(project, name, number), workflow=wf, length_s=length,
            tags=_clean_tags(self.tags), created=ctx.ts(),
        )
        project.bins_by_id[bin_.id] = bin_
        self._created = bin_.id
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(getattr(self, "_created", ""), None)
        return f"added {bin_label(bin_)}" if bin_ else "added a bin"


class DuplicateBin(Command):
    """A copy of a bin's recipe, and of its gallery when asked. References are cloned with fresh
    ids and the same handles, still pointing at the source's media, so the copy renders the same
    thing until you change it."""

    name: ClassVar[str] = "duplicate_bin"
    bin: str
    with_media: bool = False
    shot: bool = False  # the copy is a shot of its own: a new permanent number and handle base

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        _bin(project, self.bin)

    def apply(self, project, ctx, env) -> Applied:
        src = _bin(project, self.bin)
        name = _unique_name(project, f"{src.name}-copy")
        number = _mint_shot(project) if self.shot else None
        copy_ = Bin(
            id=ids.new_id(ids.BIN),
            name=name,
            shot=number,
            slug=_new_slug(project, name, number),
            workflow=src.workflow,
            length_s=src.length_s,
            loras=[Lora.model_validate(_dump(l)) for l in src.loras],
            prompt=src.prompt,
            negative=src.negative,
            references=[Reference(id=ids.new_id(ids.REFERENCE), media=r.media, bin=r.bin, handle=r.handle) for r in src.references],
            media=list(src.media) if self.with_media else [],
            selected=src.selected if self.with_media else None,
            counters=dict(src.counters) if self.with_media else {},
            description=src.description,
            tags=list(src.tags),
            created=ctx.ts(),
        )
        project.bins_by_id[copy_.id] = copy_
        self._created = copy_.id
        self._src = src.name
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        made = project.bins_by_id.get(getattr(self, "_created", ""), None)
        return f"duplicated {getattr(self, '_src', self.bin)} as {made.name if made else 'a bin'}"


SettableField = Literal["name", "slug", "workflow", "length_s", "prompt", "negative", "loras", "selected", "description", "tags"]


def _validate_loras(value: Any) -> list[dict[str, Any]]:
    """A list of ``Lora`` payloads, or ``bad_value``. Empty names go; the rest keep their order."""
    try:
        loras = [Lora.model_validate(x) for x in (value or [])]
    except (ValidationError, TypeError) as exc:
        raise CommandRefused("bad_value", f"loras: {exc}") from None
    return [lora.model_dump() for lora in loras if lora.name.strip()]


class SetBinField(Command):
    name: ClassVar[str] = "set_bin_field"
    bin: str
    field: SettableField
    value: Any = None

    def target(self, project: Project) -> ChangeTarget:
        # a new slug rewrites handles and prompts in every bin referencing this one, so the
        # change is the project's: the whole rewrite sits in one diff and a reject undoes it all.
        # A new name is a title and touches this bin alone
        if self.field == "slug":
            return ChangeTarget(type="project", id=project.id)
        return ChangeTarget(type="bin", id=self.bin)

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        f, v = self.field, self.value
        if f == "workflow":
            if not isinstance(v, str) or not env.workflow_exists(v):
                raise CommandRefused("unknown_workflow", f"no workflow {v}")
        elif f == "length_s":
            # stored on any bin, used only while its workflow makes video: so a length and a
            # switch to a video workflow land in either order, and a switch away keeps the length
            try:
                if v is None or float(v) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise CommandRefused("bad_value", "length_s must be a positive number") from None
        elif f in ("prompt", "negative", "description"):
            if not isinstance(v, str):
                raise CommandRefused("bad_value", f"{f} must be a string")
            if f == "prompt":
                env.resolve_loras(lift_lora_tags(v)[1])
        elif f == "name":
            if not isinstance(v, str) or not v.strip():
                raise CommandRefused("bad_value", "bin name is empty")
        elif f == "slug":
            # the one rename that rewrites prompts: the user's call, never the agent's
            if ctx.actor != "user":
                raise CommandRefused("wrong_actor", "only the user changes a bin's handle; retitle it with name instead")
            if not isinstance(v, str) or not v.strip() or slugify(v) != v.strip():
                raise CommandRefused("bad_value", "a slug is lowercase words joined by hyphens")
            if any(b.slug == v and b.id != self.bin for b in project.bins_by_id.values()):
                raise CommandRefused("bad_value", f"another bin already uses @{v}")
        elif f == "loras":
            env.resolve_loras(_validate_loras(v))
        elif f == "tags":
            _clean_tags(v)
        elif f == "selected":
            if v is not None and v not in bin_.media:
                raise CommandRefused("not_in_bin", f"{v} is not in {bin_.name}")
        else:  # pragma: no cover - Literal already refuses
            raise CommandRefused("field_not_settable", f)

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        value = self.value
        if self.field == "slug":
            self._old = bin_.slug
            self._touched = reslug_bin_handles(project, self.bin, str(value).strip())
            return Applied(target=self.target(project))
        if self.field == "name":
            # a title: this bin's own, no handle or prompt anywhere moves
            self._old = bin_.name
            value = str(value).strip()
        if self.field == "length_s":
            value = float(value)
        if self.field == "loras":
            value = [lora.model_dump() for lora in env.resolve_loras(_validate_loras(value))]
        if self.field == "tags":
            value = _clean_tags(value)
        if self.field == "prompt":
            # a tag typed into the prompt is a lora entry, not prompt text
            value, typed = lift_lora_tags(value)
            _, loras = lift_lora_tags("".join(l.tag() for l in env.resolve_loras(typed)), bin_.loras)
            apply_patch(bin_, {"loras": [lora.model_dump() for lora in loras]})
        apply_patch(bin_, {self.field: value})
        # a prompt naming a handle nobody bound is accepted (the words are the user's) and warned
        # about now, not only when the render assembles
        warnings = token_warnings(project, bin_, env) if self.field in ("prompt", "negative") else []
        return Applied(target=self.target(project), warnings=warnings)

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        name = bin_.name if bin_ else self.bin
        f = self.field
        if f == "length_s":
            return f"{name} - length {_fmt_len(before.get(f))} to {_fmt_len(after.get(f))}"
        if f == "workflow":
            return f"{name} - workflow {before.get(f) or 'none'} to {after.get(f)}"
        if f == "selected":
            _, n = _take_number(project, after.get(f) or "")
            return f"{name} - current take is now {f'take {n}' if n else (after.get(f) or 'none')}"
        if f in ("prompt", "negative", "description"):
            return f"{name} - {f} edited"
        if f == "name":
            return f"{getattr(self, '_old', '?')} renamed to {name}"
        if f == "slug":
            n = getattr(self, "_touched", 0)
            tail = f" ({n} bin{'s reference' if n != 1 else ' references'} it)" if n else ""
            return f"{name} - handle @{getattr(self, '_old', '?')} is now @{after.get(f) or self.value}{tail}"
        if f == "loras":
            names = [str(x.get("name", "")) for x in (after.get(f) or [])]
            return f"{name} - loras {', '.join(names) if names else 'none'}"
        if f == "tags":
            return f"{name} - tags {', '.join(after.get(f) or []) or 'none'}"
        return f"{name} - {f} changed"


class BindReference(Command):
    name: ClassVar[str] = "bind_reference"
    bin: str
    media: str
    position: int | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="bin", id=self.bin)

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        media = _media(project, self.media)
        _ref_bin(project, media)
        _not_audio_reference(media)
        cap = env.capacity(bin_.workflow)
        if not cap.found:
            raise CommandRefused("unknown_workflow", f"{bin_.name} has no usable workflow ({bin_.workflow or 'none'})")
        limit = cap.slots_for(media.type)
        if limit <= 0:
            raise CommandRefused("media_type_unsupported", f"{bin_.workflow} takes no {media.type} references")
        same = sum(1 for r in bin_.references if project.media_by_id.get(r.media, None) and project.media_by_id[r.media].type == media.type)
        if same + 1 > limit:
            raise CommandRefused("too_many_references", f"{bin_.workflow} takes at most {limit} {media.type} reference(s)")
        if self.position is not None and self.position < 0:
            raise CommandRefused("bad_value", "position is a 0-based index into the references (0 = first); negative is out of range")

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        media = _media(project, self.media)
        ref_bin = _ref_bin(project, media)
        handle = assign_handle(bin_, ref_bin, media_ext(media.path))
        ref = Reference(id=ids.new_id(ids.REFERENCE), media=media.id, bin=ref_bin.id, handle=handle)
        if self.position is None:
            bin_.references.append(ref)
        else:
            # past the end is the end: "put it last" on an empty bin is an append, not a refusal
            bin_.references.insert(min(self.position, len(bin_.references)), ref)
        self._handle = handle
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        return f"{bin_.name if bin_ else self.bin} - bound @{getattr(self, '_handle', '?')}"


class UnbindReference(Command):
    name: ClassVar[str] = "unbind_reference"
    bin: str
    reference: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="bin", id=self.bin)

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        if not any(r.id == self.reference for r in bin_.references):
            raise CommandRefused("unknown_id", f"no reference {self.reference} in {bin_.name}", {"type": "reference", "id": self.reference})

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        gone = next(r for r in bin_.references if r.id == self.reference)
        self._handle = gone.handle
        bin_.references = [r for r in bin_.references if r.id != self.reference]
        # an @mention follows its media: the unbound one's mentions can no longer point at
        # anything, and must not inherit its name once the survivors renumber (@mara.png.2
        # becoming @mara.png would otherwise put the wrong picture in the prompt). They become a
        # visible dangling token, which generate refuses until the prompt is fixed.
        dangling = {gone.handle: _dangling_handle(gone.handle)}
        bin_.prompt = rewrite_tokens(bin_.prompt, dangling)
        bin_.negative = rewrite_tokens(bin_.negative, dangling)
        # close the gap, so the last reference to a bin never sits there wearing a .2
        renumber_handles(bin_, gone.bin)
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        return f"{bin_.name if bin_ else self.bin} - unbound @{getattr(self, '_handle', '?')}"


def _dangling_handle(handle: str) -> str:
    """What a mention of an unbound reference becomes: ``mara.png.2`` to ``mara-unbound-2.png``.
    Still a token (so the scan sees it), never a name a live reference can be minted with."""
    base = handle_base(handle)
    slug, _, ext = base.rpartition(".")
    n = handle[len(base) + 1 :] if handle != base else ""
    return f"{slug}-unbound{'-' + n if n else ''}.{ext}"


class ReorderReferences(Command):
    name: ClassVar[str] = "reorder_references"
    bin: str
    order: list[str]

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="bin", id=self.bin)

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        if sorted(self.order) != sorted(r.id for r in bin_.references):
            raise CommandRefused("bad_order", "order must list every reference id exactly once")

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        by_id = {r.id: r for r in bin_.references}
        bin_.references = [by_id[i] for i in self.order]
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        return f"{bin_.name if bin_ else self.bin} - references reordered"


class RebindReference(Command):
    """Swap a reference's media in place, keeping its handle: same prompt token, a different
    picture or clip behind it. A media of another file type cannot keep it (the handle spells
    the extension): the reference is re-minted for the new type and the prompt follows."""

    name: ClassVar[str] = "rebind_reference"
    bin: str
    reference: str
    media: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="bin", id=self.bin)

    def _ref(self, bin_: Bin) -> Reference:
        for r in bin_.references:
            if r.id == self.reference:
                return r
        raise CommandRefused("unknown_id", f"no reference {self.reference} in {bin_.name}", {"type": "reference", "id": self.reference})

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        self._ref(bin_)
        media = _media(project, self.media)
        _ref_bin(project, media)
        _not_audio_reference(media)

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        ref = self._ref(bin_)
        media = _media(project, self.media)
        ref_bin = _ref_bin(project, media)
        ext = media_ext(media.path)
        old_handle, old_bin = ref.handle, ref.bin
        if ext != handle_ext(old_handle):
            # mint against the others, then point the prompt at the new name before the old
            # series closes its gap (else a renumber could fold a sibling onto the old token)
            at = bin_.references.index(ref)
            bin_.references.remove(ref)
            ref.handle = assign_handle(bin_, ref_bin, ext)
            bin_.references.insert(at, ref)
            bin_.prompt = rewrite_tokens(bin_.prompt, {old_handle: ref.handle})
            bin_.negative = rewrite_tokens(bin_.negative, {old_handle: ref.handle})
        ref.media = media.id
        ref.bin = ref_bin.id
        if ref.handle != old_handle:
            renumber_handles(bin_, old_bin)
        self._handle = ref.handle
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        ref = self._ref(bin_) if bin_ else None
        return f"{bin_.name if bin_ else self.bin} - @{ref.handle if ref else '?'} now uses different media"


class StartFromMedia(Command):
    """Start a generation from a media: bind it as a reference into a bin -- this one, another,
    or a freshly minted one -- in one write, so Generate is the next tap. The reference-first
    path, where ``bind_reference`` is the recipe-first one.

    Never refuses on capacity or media type: a reference the workflow has no slot for lands in
    the unused state the bin screen already draws, and comes alive when the workflow changes
    to one that fits. A bin with no workflow takes the project's image default, as
    ``create_bin`` does; none set, it stays without one and the recipe view asks for it."""

    name: ClassVar[str] = "start_from_media"
    media: str
    bin: str | None = None
    """An existing bin to bind into; absent, a new bin is minted."""
    bin_name: str | None = None
    """The new bin's name; absent, the next ``bin-n``. Ignored when ``bin`` is given."""
    workflow: str | None = None
    """For the new bin, or an existing bin that has none; absent, the project's image default."""
    shot: bool = False
    """The new bin is a shot of the narrative: a permanent number and a @shot-N handle."""

    def target(self, project: Project) -> ChangeTarget:
        if self.bin is not None:
            return ChangeTarget(type="bin", id=self.bin)
        return ChangeTarget(type="project", id=project.id)

    def _workflow(self, project: Project) -> str:
        return str(self.workflow if self.workflow is not None else project.defaults.image_workflow or "").strip()

    def check(self, project, ctx, env) -> None:
        media = _media(project, self.media)
        _ref_bin(project, media)
        _not_audio_reference(media)
        if self.bin is not None:
            _bin(project, self.bin)
        else:
            _check_bin_name(self.bin_name)
        if self.workflow is not None and self.workflow.strip() and not env.workflow_exists(self.workflow.strip()):
            raise CommandRefused("unknown_workflow", f"no workflow {self.workflow.strip()}")

    def apply(self, project, ctx, env) -> Applied:
        media = _media(project, self.media)
        ref_bin = _ref_bin(project, media)
        self._made = self.bin is None
        if self.bin is not None:
            bin_ = _bin(project, self.bin)
            if not bin_.workflow:
                wf = self._workflow(project)
                if wf and env.workflow_exists(wf):
                    apply_patch(bin_, {"workflow": wf})
        else:
            wf = self._workflow(project)
            if wf and not env.workflow_exists(wf):
                wf = ""
            length = None
            if wf and env.kind(wf) == "video":
                length = project.defaults.video_length_s
            name = str(self.bin_name).strip() if self.bin_name is not None else next_bin_name(project)
            number = _mint_shot(project) if self.shot else None
            bin_ = Bin(id=ids.new_id(ids.BIN), name=name, shot=number, slug=_new_slug(project, name, number), workflow=wf, length_s=length, created=ctx.ts())
            project.bins_by_id[bin_.id] = bin_
        handle = assign_handle(bin_, ref_bin, media_ext(media.path))
        bin_.references.append(Reference(id=ids.new_id(ids.REFERENCE), media=media.id, bin=ref_bin.id, handle=handle))
        self._bin = bin_.id
        self._handle = handle
        self._from = f"{ref_bin.name} {_media_label(ref_bin, media)}"
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(getattr(self, "_bin", ""), None)
        name = bin_.name if bin_ else (self.bin or "a bin")
        handle = getattr(self, "_handle", "?")
        source = getattr(self, "_from", self.media)
        if getattr(self, "_made", False):
            return f"added {name} from {source} as @{handle}"
        return f"{name} - bound @{handle} from {source}"


class RecallRef(BaseModel):
    """One restored reference: the media, and the handle it answered to when the take was made."""

    model_config = ConfigDict(extra="forbid")
    media: str
    handle: str


class RecallGeneration(Command):
    """Put a bin back the way it was when it produced one of its takes, ready to render again.

    Restores the bin's workflow, prompt, negative and reference set. The references carry the
    handles the generation recorded, so the recalled prompt's @tokens point at the same media
    they did then.

    The values arrive as payload rather than being read out of the generation log, because the
    command layer only ever sees the loaded project; the caller reads the log (the media recipe
    endpoint) and sends what it found. One command, so it is one line in the change log and one
    thing to undo.
    """

    name: ClassVar[str] = "recall_generation"
    bin: str
    from_media: str = ""  # what was recalled, for the change description
    workflow: str = ""
    prompt: str = ""
    negative: str = ""
    references: list[RecallRef] = []
    loras: list[Lora] = []

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="bin", id=self.bin)

    def check(self, project, ctx, env) -> None:
        _bin(project, self.bin)
        if self.workflow and not env.workflow_exists(self.workflow):
            raise CommandRefused("unknown_workflow", f"no workflow {self.workflow}")
        for r in self.references:
            _ref_bin(project, _media(project, r.media))

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        if self.workflow:
            bin_.workflow = self.workflow
        bin_.negative = self.negative
        bin_.prompt, loras = lift_lora_tags(self.prompt, [lora for lora in self.loras if lora.name.strip()])
        bin_.loras = env.resolve_loras(loras, strict=False)
        # a reference the bin already holds (same media, same handle) keeps its id: a rebind or
        # unbind the agent planned by id still lands after the recall
        kept = {(r.media, r.handle): r.id for r in bin_.references}
        used: set[str] = set()
        refs: list[Reference] = []
        for r in self.references:
            old = kept.get((r.media, r.handle))
            ref_id = old if old and old not in used else ids.new_id(ids.REFERENCE)
            used.add(ref_id)
            refs.append(Reference(id=ref_id, media=r.media, bin=_ref_bin(project, _media(project, r.media)).id, handle=r.handle))
        bin_.references = refs
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        return f"{bin_.name if bin_ else self.bin} - recalled the settings that made {self.from_media or 'a take'}"


def full_quality(project: Project, workflow: str, params: dict[str, Any], env: Env) -> bool:
    """Whether a render is the expensive tier: the step a drafts-first session asks about first.

    A draft is everything cheap the render can be: a video at quarter size, and the workflow's
    own draft setting turned on (``draft``) when it has one (a THM-IsDraft switch). Anything
    short of that is full quality. A still on a workflow with no draft setting has one tier and
    runs. A **video** with no cheap tier at all (a partner API node: no canvas, no draft switch)
    is full quality every time: it is a spend, and a drafts-first session asks before each one."""
    if not workflow:
        return False
    cap = env.capacity(workflow)
    if env.kind(workflow) == "video":
        # quarter only counts where the workflow has a canvas to halve (a partner API node has none)
        if cap.supports_size and not params.get("quarter"):
            return True
        if not cap.supports_size and not cap.supports_draft:
            return True
    return cap.supports_draft and not params.get("draft")


# the cheap settings a workflow may lack, and the refusal code each used to carry
_CHEAP_SETTINGS = (("draft", "supports_draft", "draft_unsupported", "has no draft setting (no THM-IsDraft switch)"), ("quarter", "supports_size", "quarter_unsupported", "sets no canvas size, so it has no quarter size"))


def _check_render_settings(env: Env, workflow: str, params: dict[str, Any], where: str) -> list[dict]:
    """A draft or quarter-size setting on a workflow that cannot do it is dropped from the
    params, with a warning, so the record never says draft over a full-quality render and the
    render still runs at the one tier the workflow has (a drafts-first agent turns on every
    cheap setting it can name; the workflow decides which exist)."""
    cap = env.capacity(workflow)
    dropped: list[dict] = []
    for key, attr, code, why in _CHEAP_SETTINGS:
        if params.get(key) and not getattr(cap, attr):
            params.pop(key, None)
            dropped.append({"kind": code, "handle": "", "detail": f"{where}: {_wf_stem(workflow)} {why}; {key} dropped, rendering at its one tier"})
    return dropped


class Generate(Command):
    """Render the bin's recipe once; the output lands in its gallery as the next take."""

    name: ClassVar[str] = "generate"
    bin: str
    params: dict[str, Any] = {}
    # save for later: the recipe is resolved and logged now, exactly as it stands, and rendered
    # only when the project's held group is released. Edits to the bin after this never reach it.
    hold: bool = False

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="bin", id=self.bin)

    def renders(self, project, env) -> Kind | None:
        bin_ = project.bins_by_id.get(self.bin)
        return env.kind(bin_.workflow) if bin_ is not None else None

    def full_quality(self, project, env) -> bool:
        bin_ = project.bins_by_id.get(self.bin)
        return bin_ is not None and full_quality(project, bin_.workflow, self.params, env)

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        if not env.workflow_exists(bin_.workflow):
            raise CommandRefused("unknown_workflow", f"{bin_.name} has no usable workflow ({bin_.workflow or 'none'}); set one first")
        self._dropped = _check_render_settings(env, bin_.workflow, self.params, bin_.name)
        env.resolve_loras(bin_.loras)
        unknown = sorted((handles_in(bin_.prompt) | handles_in(bin_.negative)) - {r.handle for r in bin_.references})
        if unknown:
            raise CommandRefused(
                "unknown_handle",
                f"{bin_.name}: the prompt mentions {', '.join('@' + h for h in unknown)}, which no reference in this bin answers to; "
                "bind it or take it out of the prompt",
                {"handles": unknown},
            )

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        kind = env.kind(bin_.workflow)
        spelled = env.resolve_loras(bin_.loras)
        if [l.name for l in spelled] != [l.name for l in bin_.loras]:
            # "style" is style.safetensors: the bin keeps the name the loader will find
            bin_.loras = spelled
        resolved = resolve_references(project, bin_, isfile=env.isfile)
        assembled = assemble(project, bin_, resolved, workflows_dir=env.workflows_dir, capacity=env.capacity(bin_.workflow))
        gen = Generation(
            id=ids.new_id(ids.GENERATION),
            bin=bin_.id,
            kind=kind,
            ts=ctx.ts(),
            workflow=bin_.workflow,
            prompt_typed=assembled.prompt_typed,
            prompt_submitted=assembled.prompt_submitted,
            negative_submitted=assembled.negative_submitted,
            references=assembled.references,
            loras=list(bin_.loras),
            # canvas from the project sits under everything; the caller's params win
            params={
                **({"width": project.defaults.width, "height": project.defaults.height} if project.defaults.configured else {}),
                **({"length_s": bin_.length_s} if kind == "video" and bin_.length_s else {}),
                **self.params,
            },
            status="held" if self.hold else "queued",
            prelude=assembled.prelude,
            paths_by_slot=assembled.paths_by_slot,
            warnings=[w.to_dict() for w in assembled.warnings] + list(getattr(self, "_dropped", []) or []),
        )
        return Applied(target=self.target(project), generation=gen, assembled=assembled)

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        # a seed the caller chose is worth saying: a held seed is why two takes look alike
        notes = [n for n in (("saved for later" if self.hold else ""), ("quarter size" if self.params.get("quarter") else ""), (f"seed {self.params['seed']}" if self.params.get("seed") is not None else "")) if n]
        return f"{bin_.name if bin_ else self.bin} - generate" + (f" ({', '.join(notes)})" if notes else "")


class ShotNeed(BaseModel):
    """A reference this shot is meant to render with that does not exist yet: the bin it will
    come from and what it is ("French over onto Sally"). The card says the shot is waiting on
    it, and reads as ready once that bin holds a take; the agent revises the card with the
    real media id bound when it lands. A dependency named, not a binding."""

    model_config = ConfigDict(extra="forbid")
    bin: str | None = None  # the bin the reference will come from, when it exists already
    bin_name: str = ""  # or its name, when the bin is still to be made
    what: str  # the picture needed, in a few words


class ShotItem(BaseModel):
    """One intended generation on a propose_shots card. The user reads ``description``, the
    one-liner, and may rewrite it or drop the item before approving; ``prompt`` is what the
    render says when the agent wrote one apart from the one-liner. The trailing fields are the
    server's: written on decision and replayed with the payload, so they are declared here."""

    model_config = ConfigDict(extra="forbid")
    name: str = ""
    bin: str | None = None
    """An existing bin to render in; the fields below overwrite its own when given, and ``name``
    is filled from it. Without it ``name`` is a new bin's title."""
    shot: bool = True
    """A new bin made here is a shot of the narrative (numbered, @shot-N) unless this says it
    is an asset: a character, a plate, a reference clip, named for the thing."""
    description: str
    prompt: str | None = None
    negative: str | None = None
    workflow: str | None = None
    references: list[str] = []
    length_s: float | None = None
    quarter: bool = False
    # the workflow's own draft setting (a THM-IsDraft switch), when it has one
    draft: bool = False
    params: dict[str, Any] = {}
    tags: list[str] = []
    # references this shot is waiting on that are not generated yet (see ShotNeed)
    needs: list[ShotNeed] = []
    # decision and outcome
    dropped: bool = False
    edited: bool = False
    bin_id: str | None = None
    change: str | None = None
    error: str | None = None

    def renders(self) -> bool:
        """An edited one-liner over a separate prompt is the agent's to reprompt: the bin is
        made, the render waits. Without a separate prompt the edit is the prompt."""
        return not self.dropped and not (self.edited and self.prompt is not None)


class ProposeShots(Command):
    """A batch of intended generations, asked about before any of them spends. Proposed (never
    applied directly, unless the user asked for the batch out loud): the card lists the items,
    the user keeps, edits or drops each, and the approval mints the bins, writes the recipes,
    binds the references and queues the renders in one step. Each queued render is its own
    child change tagged with this change's id, so the session's mode still decides whether the
    landed take is a card."""

    name: ClassVar[str] = "propose_shots"
    title: str | None = None
    items: list[ShotItem]
    # the user answered "later": the bins and recipes are made now, the renders are saved held
    # and run when the user releases them. Written by the decision, never by the agent.
    hold: bool = False

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def _workflow(self, project: Project, item: ShotItem) -> str:
        if item.bin is not None and item.workflow is None:
            return _bin(project, item.bin).workflow
        return str(item.workflow if item.workflow is not None else project.defaults.image_workflow or "").strip()

    def check(self, project, ctx, env) -> None:
        if not self.items:
            raise CommandRefused("bad_value", "no shots")
        if self.hold and ctx.actor == "agent" and not ctx.bypass_gates:
            raise CommandRefused("wrong_actor", "hold is the user's answer on the card, not the agent's to set")
        for i, item in enumerate(self.items):
            if item.dropped:
                continue
            where = f"shot {i + 1}"
            if item.bin is not None:
                item.name = _bin(project, item.bin).name
            if not str(item.name).strip():
                raise CommandRefused("bad_value", f"{where}: name is empty")
            if not str(item.description).strip():
                raise CommandRefused("bad_value", f"{where}: description is empty")
            wf = self._workflow(project, item)
            if not wf or not env.workflow_exists(wf):
                raise CommandRefused("unknown_workflow", f"{where}: no workflow {wf or '(none)'}")
            _check_render_settings(
                env, wf, {"quarter": item.quarter and env.kind(wf) == "video", "draft": item.draft, **item.params}, where
            )
            if item.length_s is not None:
                if item.length_s <= 0:
                    raise CommandRefused("bad_value", f"{where}: length_s must be positive")
            cap = env.capacity(wf)
            counts: dict[str, int] = {}
            for media_id in item.references:
                media = _media(project, media_id)
                _ref_bin(project, media)
                limit = cap.slots_for(media.type)
                if limit <= 0:
                    raise CommandRefused("media_type_unsupported", f"{where}: {wf} takes no {media.type} references")
                counts[media.type] = counts.get(media.type, 0) + 1
                if counts[media.type] > limit:
                    raise CommandRefused("too_many_references", f"{where}: {wf} takes at most {limit} {media.type} reference(s)")
            _clean_tags(item.tags)

    def apply(self, project, ctx, env) -> Applied:
        self._renders: list[tuple[ShotItem, Generate]] = []
        warnings: list[AssemblyWarning] = []
        for item in self.items:
            if item.dropped:
                continue
            wf = self._workflow(project, item)
            kind = env.kind(wf)
            if item.bin is not None:
                bin_ = _bin(project, item.bin)
                if item.workflow is not None:
                    apply_patch(bin_, {"workflow": wf})
                if item.length_s is not None:
                    apply_patch(bin_, {"length_s": float(item.length_s)})
                if item.tags:
                    apply_patch(bin_, {"tags": _clean_tags(item.tags)})
            else:
                length = None
                if kind == "video":
                    length = item.length_s if item.length_s is not None else project.defaults.video_length_s
                title = str(item.name).strip()
                number = _mint_shot(project) if item.shot else None
                bin_ = Bin(
                    id=ids.new_id(ids.BIN), name=title, shot=number, slug=_new_slug(project, title, number), workflow=wf, length_s=length,
                    tags=_clean_tags(item.tags), created=ctx.ts(),
                )
                project.bins_by_id[bin_.id] = bin_
            item.bin_id = bin_.id
            if item.prompt is None and item.bin is not None:
                # a retake with no prompt of its own renders the bin's recipe as it stands: the
                # card's one-liner is display text, never the prompt (a model speaks it on camera)
                apply_patch(bin_, {"description": item.description.strip()})
            else:
                prompt = item.prompt if item.prompt is not None else item.description
                prompt, loras = lift_lora_tags(prompt, bin_.loras)
                loras = env.resolve_loras(loras, strict=False)
                apply_patch(bin_, {"prompt": prompt, "loras": [lora.model_dump() for lora in loras], "description": item.description.strip()})
            if item.negative is not None:
                apply_patch(bin_, {"negative": item.negative})
            if item.bin is not None and item.references:
                # a retake names the references it renders with: they are the bin's references
                # now, not more on top of the ones its last take left bound (three on the card
                # plus three already there is over a four-slot workflow, and refused the approval)
                bin_.references = []
            for media_id in item.references:
                bind = BindReference(bin=bin_.id, media=media_id)
                bind.check(project, ctx, env)
                bind.apply(project, ctx, env)
            warnings.extend(token_warnings(project, bin_, env))
            if item.renders():
                params = {
                    **({"quarter": True} if item.quarter and kind == "video" else {}),
                    **({"draft": True} if item.draft and env.capacity(bin_.workflow).supports_draft else {}),
                    **item.params,
                }
                self._renders.append((item, Generate(bin=bin_.id, params=params, reason=self.reason, hold=self.hold)))
        return Applied(target=self.target(project), warnings=warnings)

    def describe(self, project, before, after) -> str:
        kept = [i for i in self.items if not i.dropped]
        n = len(self.items)
        held = sum(1 for i in kept if not i.renders())
        # after the apply, an edited one-liner over the agent's prompt holds its render for a reprompt
        tail = " (held for a reprompt)" if held and (before or after) else ""
        if n == 1:
            # a batch goes out as one card per shot: the card is the shot
            item = self.items[0]
            return f"shot {str(item.name or item.bin or '').strip() or '1'}{tail}"
        shots = f"{n} shot{'s' if n != 1 else ''}"
        if len(kept) != n:
            shots = f"{len(kept)} of {shots}"
        title = f": {self.title.strip()}" if self.title and self.title.strip() else ""
        return f"{shots}{title}{tail}"

    def intro(self) -> str:
        """The batch's own line, ahead of its cards: the count, the title and the reason."""
        n = len(self.items)
        title = f": {self.title.strip()}" if self.title and self.title.strip() else ""
        why = (self.reason or "").strip()
        return f"{n} shots{title}" + (f" -- {why}" if why else "")

    def merge_decision(self, items: list[dict]) -> None:
        """The user's per-item verdicts from the card: kept, dropped, or a rewritten one-liner."""
        for entry in items:
            try:
                index = int(entry.get("index"))
            except (TypeError, ValueError):
                raise CommandRefused("bad_value", "item index missing") from None
            if not 0 <= index < len(self.items):
                raise CommandRefused("bad_value", f"no shot at index {index}")
            item = self.items[index]
            if entry.get("enabled") is False:
                item.dropped = True
                continue
            item.dropped = False
            text = entry.get("description")
            if isinstance(text, str) and text.strip() and text.strip() != item.description.strip():
                item.description = text.strip()
                item.edited = True


class MarkTake(Command):
    name: ClassVar[str] = "mark_take"
    media: str
    status: QcStatus
    note: str | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="media", id=self.media)

    def check(self, project, ctx, env) -> None:
        media = _media(project, self.media)
        if media.origin.type != "generation" or not media.origin.bin:
            raise CommandRefused("not_a_take", f"{self.media} was not generated into a bin")
        # the agent's fail closes the user's card on the take: the card says why, so it needs a why
        if ctx.actor == "agent" and self.status == "fail" and not (self.note or "").strip():
            raise CommandRefused("bad_value", "a fail from the agent needs a note: what is wrong with the take (it shows on the closed card)")

    def apply(self, project, ctx, env) -> Applied:
        media = _media(project, self.media)
        media.qc = QcMark(status=self.status, note=self.note, by=ctx.actor, ts=ctx.ts())
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        return f"{_take_label(project, self.media)} - marked {self.status.replace('_', ' ')}"


class AddMediaToBin(Command):
    """Put an existing media into a bin's gallery. Like a landing take: the bin's first media
    leads (nothing was chosen yet); after that an arrival is new and the winner stays put until
    someone chooses."""

    name: ClassVar[str] = "add_media_to_bin"
    bin: str
    media: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="bin", id=self.bin)

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        _media(project, self.media)
        if self.media in bin_.media:
            raise CommandRefused("already_in_bin", f"{self.media} is already in {bin_.name}")

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        had_winner = bool(bin_.selected and bin_.selected in bin_.media)
        bin_.media.append(self.media)
        if not had_winner:
            bin_.selected = self.media
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        return f"added media to {bin_.name if bin_ else self.bin}"


class RemoveMediaFromBin(Command):
    """Take a media out of a bin's gallery. The media record and its file stay; nothing here deletes."""

    name: ClassVar[str] = "remove_media_from_bin"
    bin: str
    media: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="bin", id=self.bin)

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        if self.media not in bin_.media:
            raise CommandRefused("not_in_bin", f"{self.media} is not in {bin_.name}")

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        bin_.media = [m for m in bin_.media if m != self.media]
        bin_.unseen = [m for m in bin_.unseen if m != self.media]
        if bin_.selected == self.media:
            bin_.selected = bin_.media[-1] if bin_.media else None
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        return f"removed media from {bin_.name if bin_ else self.bin}"


def _cascade_delete_media(project: Project, media_id: str) -> None:
    """Remove every trace of a media record: every bin's gallery and references, clamping the
    bin's selected, then the record itself. No file I/O; deleting an orphaned upload's file is
    the route's job."""
    for bin_ in project.bins_by_id.values():
        if any(r.media == media_id for r in bin_.references):
            bin_.references = [r for r in bin_.references if r.media != media_id]
        if media_id in bin_.media:
            bin_.media = [m for m in bin_.media if m != media_id]
        if media_id in bin_.unseen:
            bin_.unseen = [m for m in bin_.unseen if m != media_id]
        if bin_.selected == media_id:
            bin_.selected = bin_.media[-1] if bin_.media else None
    project.media_by_id.pop(media_id, None)
    # a draft post is its video plus words: the video gone, the draft goes. An uploaded post is
    # the record of what went to the platform and stays
    if any(p.media == media_id and p.status != "uploaded" for p in project.posts):
        project.posts = [p for p in project.posts if not (p.media == media_id and p.status != "uploaded")]
    for p in project.posts:
        if p.thumbnail == media_id:
            p.thumbnail = None


class DeleteMedia(Command):
    """Remove a media record everywhere it appears: every gallery, every reference. Cascading,
    not just an unlink; ``remove_media_from_bin`` is the one that keeps the record."""

    name: ClassVar[str] = "delete_media"
    media: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        _media(project, self.media)

    def apply(self, project, ctx, env) -> Applied:
        self._label = _take_label(project, self.media)
        _cascade_delete_media(project, self.media)
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        return f"deleted {getattr(self, '_label', self.media)} from the library"


def _sweep_orphans(project: Project) -> int:
    """Drop every media record no bin holds any more, in a gallery or as a reference, and say
    how many went. A media lives while something reaches it; nothing else keeps one."""
    kept: set[str] = set()
    for bin_ in project.bins_by_id.values():
        kept.update(bin_.media)
        kept.update(r.media for r in bin_.references)
    orphans = [m for m in project.media_by_id if m not in kept]
    for media_id in orphans:
        project.media_by_id.pop(media_id, None)
    return len(orphans)


class DeleteBin(Command):
    """Remove a bin, then every media nothing reaches any more. A media in a surviving bin's
    gallery, or bound as one of its references, has been promoted past this bin and stays;
    the rest go like ``delete_media``. The bin's references to its own media do not keep them,
    and a media that only this bin's references kept alive goes with it. Files on disk are
    never touched."""

    name: ClassVar[str] = "delete_bin"
    bin: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        _bin(project, self.bin)

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        self._name = bin_.name
        project.bins_by_id.pop(self.bin, None)
        self._gone = _sweep_orphans(project)
        # a sequence item is a bin: the bin gone, the item goes with it. A sequence whose exports
        # landed here has no target any more: the next export asks again rather than minting quietly
        self._from = [s.name for s in project.sequences if any(i.bin == self.bin for i in s.items)]
        self._untargeted = [s.name for s in project.sequences if s.output_bin == self.bin]
        for seq in project.sequences:
            if any(i.bin == self.bin for i in seq.items):
                seq.items = [i for i in seq.items if i.bin != self.bin]
                seq.updated = ctx.ts()
            if seq.output_bin == self.bin:
                seq.output_bin = None
                seq.updated = ctx.ts()
        # a post drafted from this bin goes with it; one already uploaded is a record and stays
        self._posts = sum(1 for p in project.posts if p.bin == self.bin and p.status != "uploaded")
        if self._posts:
            project.posts = [p for p in project.posts if not (p.bin == self.bin and p.status != "uploaded")]
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        n = getattr(self, "_gone", 0)
        seqs = getattr(self, "_from", [])
        tail = f"; removed from {', '.join(seqs)}" if seqs else ""
        lost = getattr(self, "_untargeted", [])
        tail += f"; {', '.join(lost)} {'has' if len(lost) == 1 else 'have'} no exports bin now" if lost else ""
        posts = getattr(self, "_posts", 0)
        tail += f"; {posts} draft post{'s' if posts != 1 else ''} dropped" if posts else ""
        return f"deleted {getattr(self, '_name', self.bin)} ({n} media){tail}"


class PostMessage(Command):
    name: ClassVar[str] = "post_message"
    text: str
    attachments: list[str] = []
    # ids of files uploaded for this note alone (the chat-files route); never project media
    files: list[str] = []
    kind: Literal["user", "agent", "blocker"] = "user"
    # what the user was looking at (bin + lead media); the names are filled here from the project
    context: MessageContext | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        # a user's note may be a picture alone: what it carries is the message
        if not str(self.text).strip() and not (ctx.actor == "user" and (self.files or self.attachments)):
            raise CommandRefused("bad_value", "message text is empty")
        if ctx.actor == "agent":
            check_no_raw_ids(project, self.text)
        for m in self.attachments:
            _media(project, m)
        if self.context is not None:
            if not self.context.bin and not self.context.sequence:
                raise CommandRefused("bad_value", "a note's context names a bin or a sequence")
            if self.context.bin:
                _bin(project, self.context.bin)
            if self.context.sequence:
                _sequence(project, self.context.sequence)
            if self.context.media:
                _media(project, self.context.media)
        if ctx.actor == "user" and self.kind != "user":
            raise CommandRefused("wrong_actor", "a user posts user messages")
        if ctx.actor == "agent" and self.kind == "user":
            raise CommandRefused("wrong_actor", "an agent posts agent or blocker messages")


class AnswerNotes(Command):
    """The agent accounts for the user's notes: each one named here is closed with what was done
    about it ("quarter re-render queued", "already fixed in take 11", "waiting on your yes on the
    P05 card"). A note is open until this names it, however long ago it was read: the inbox
    cursor says the agent saw it, this says the agent dealt with it. Nothing lands in the feed
    but the line on the note itself."""

    name: ClassVar[str] = "answer_notes"
    notes: list[str]
    did: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if ctx.actor != "agent":
            raise CommandRefused("wrong_actor", "the agent answers the user's notes")
        if not self.notes:
            raise CommandRefused("bad_value", "no notes named")
        if not str(self.did).strip():
            raise CommandRefused("bad_value", "say what you did about it")
        check_no_raw_ids(project, self.did)


class WithdrawChange(Command):
    """The agent takes back a card of its own that still waits for the user (a propose_shots
    card posted in a mode that needed none, a render card for a take it has since failed): the
    card closes with the agent's reason on it, nothing runs, nothing is undone. Only the live
    session's own cards; a card the user already answered is theirs and stays."""

    name: ClassVar[str] = "withdraw_change"
    change: str
    why: str = ""

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if ctx.actor != "agent":
            raise CommandRefused("wrong_actor", "the agent withdraws its own cards; the user closes them")
        if self.why:
            check_no_raw_ids(project, self.why)


# the mode in which the agent may cancel a render or restart ComfyUI on its own judgement; in
# every other mode it does so only when the user asked for it (``requested``)
COMFY_OPS_SELF_MODES = ("auto",)


def check_comfy_ops_gate(project: Project, ctx: CommandContext, *, requested: bool, what: str) -> None:
    """Cancelling a render and restarting ComfyUI are the user's levers. The agent gets them
    too, narrowly: on its own only in an auto session (the user handed it the loop), and in any
    other mode only on the user's word (``requested``). Never without a live session."""
    if ctx.actor != "agent":
        return
    sess = project.agent_session
    if sess is None or sess.status != "live":
        raise CommandRefused("no_session", f"an agent {what} needs a live session here; connect first")
    if ctx.session and ctx.session != sess.id:
        raise CommandRefused("wrong_session", f"session {ctx.session} is not this project's live session ({sess.id})")
    if requested or sess.mode in COMFY_OPS_SELF_MODES:
        return
    raise CommandRefused(
        "requested_required",
        f"in {mode_token(sess)} the agent may {what} only when the user asked for it: pass requested (the user said so in the feed), "
        "or post what you would do and wait. On its own only in auto",
        {"mode": mode_token(sess)},
    )


class AddProjectRule(Command):
    """Write down a standing rule for this project: the user's answer to a kind of QC finding
    (fail and re-roll, fix in post, or accept), or a continuity fact. Every later take is checked
    against it, by any session, without asking again."""

    name: ClassVar[str] = "add_project_rule"
    kind: Literal["qc", "continuity", "other"] = "other"
    text: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if not str(self.text).strip():
            raise CommandRefused("bad_value", "a rule needs its text")

    def apply(self, project, ctx, env) -> Applied:
        from .models import ProjectRule

        rule = ProjectRule(id=ids.new_id(ids.RULE), kind=self.kind, text=self.text.strip(), created=ctx.ts(), by=ctx.actor)
        project.rules = [*project.rules, rule]
        self._rule = rule
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        rule = getattr(self, "_rule", None)
        return f"project rule ({self.kind}) - {rule.text if rule else self.text.strip()}"


class RemoveProjectRule(Command):
    """Retire a project rule: it no longer applies. The change log keeps what it said."""

    name: ClassVar[str] = "remove_project_rule"
    rule: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if not any(r.id == self.rule for r in project.rules):
            raise CommandRefused("unknown_id", f"no rule {self.rule}", {"type": "rule", "id": self.rule})

    def apply(self, project, ctx, env) -> Applied:
        gone = next(r for r in project.rules if r.id == self.rule)
        self._text = gone.text
        project.rules = [r for r in project.rules if r.id != self.rule]
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        return f"project rule retired - {getattr(self, '_text', self.rule)}"


# what each mode means, in a line, for the feed's mode-change note (read from the gates below:
# check_session_gate, MODE_ASKS, _check_verdict_gate, the plan rule)
MODE_MEANINGS: dict[str, str] = {
    "plan": "the agent only talks and proposes a revised brief; nothing is generated or changed until you pick another mode",
    "manual": "every generation is proposed as a card first and starts only when you approve it",
    "image_review": "generations start on their own; every take, still or video, comes back as a card for your verdict",
    "video_review": "generations start on their own; the agent checks stills itself and every video take comes back as a card",
    "auto": "the agent works toward the goal on its own and brings the finished candidate as one card",
}
DRAFT_MEANING = "cheap generations (quarter size, the workflow's draft setting) run freely; a full-quality generation asks you first"

# the modes as the panel names them: the feed line reads the way the menu does
MODE_LABELS: dict[str, str] = {
    "plan": "plan", "manual": "manual", "image_review": "media cards",
    "video_review": "video cards", "auto": "full auto",
}


class StartSession(Command):
    """Open a waiting session. Kickoff notes, when given, post as the user's message with
    the refs bin's media attached; the brief itself is set when the project is created."""

    name: ClassVar[str] = "start_session"
    notes: str | None = None
    refs_bin: str | None = None
    mode: SessionMode = "auto"
    drafts: bool = False

    @model_validator(mode="before")
    @classmethod
    def _draft_mode(cls, data):
        if isinstance(data, dict) and data.get("mode") == "draft":
            data = {**data, "mode": "auto", "drafts": True}
        return data

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if ctx.actor != "user":
            raise CommandRefused("wrong_actor", "only the user starts a session")
        _not_private(project)
        if self.refs_bin:
            _bin(project, self.refs_bin)
        if _session_id(project):
            raise CommandRefused("session_active", "a session is already waiting or live")

    def apply(self, project, ctx, env) -> Applied:
        project.agent_session = AgentSession(id=ids.new_id(ids.SESSION), status="waiting", mode=self.mode, drafts=self.drafts, started=ctx.ts(), notes_tracked=True)
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        extra = [MODE_LABELS[self.mode]] if self.mode != "auto" else []
        if self.drafts:
            extra.append("draft mode")
        return f"session started ({', '.join(extra)})" if extra else "session started"


class SetSessionMode(Command):
    """Change how much the agent asks, mid-session. The user does it from the panel; the agent
    does it ``requested`` when the user said so in chat ("switch to manual")."""

    name: ClassVar[str] = "set_session_mode"
    mode: SessionMode | None = None
    drafts: bool | None = None  # the drafts-first switch, independent of the mode

    @model_validator(mode="before")
    @classmethod
    def _draft_mode(cls, data):
        if isinstance(data, dict) and data.get("mode") == "draft":
            data = {**data, "mode": None, "drafts": True}
        return data

    def target(self, project: Project) -> ChangeTarget:
        sess = project.agent_session
        return ChangeTarget(type="session", id=sess.id if sess else "")

    def check(self, project, ctx, env) -> None:
        if not _session_id(project):
            raise CommandRefused("no_session", "no session to set a mode on")
        if self.mode is None and self.drafts is None:
            raise CommandRefused("bad_value", "name a mode, the drafts switch, or both")

    def apply(self, project, ctx, env) -> Applied:
        if self.mode is not None:
            project.agent_session.mode = self.mode
        if self.drafts is not None:
            project.agent_session.drafts = self.drafts
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        parts = []
        if self.mode is not None:
            parts.append(f"session mode {MODE_LABELS[self.mode]}")
        if self.drafts is not None:
            parts.append("draft mode on" if self.drafts else "draft mode off")
        return ", ".join(parts)


SETTINGS_FIELDS = (
    "width", "height", "image_workflow", "video_workflow", "video_length_s",
    "image_prompt", "video_prompt", "image_negative", "video_negative",
)
GLOBAL_PROMPT_FIELDS = ("image_prompt", "video_prompt", "image_negative", "video_negative")


def _wf_stem(name: str) -> str:
    return name[:-5] if name.lower().endswith(".json") else name


class SetProjectSettings(Command):
    """Rename the project or change what new bins and generations inherit. Only given fields change.

    The user's settings form and the agent's configure-from-brief both land here; an agent's
    call is an approvable card whose reject restores the name and the whole defaults block.
    """

    name: ClassVar[str] = "set_project_settings"
    project_name: str | None = None
    brief: str | None = None  # the revised brief a plan-mode session proposes; approval makes it the goal
    private: bool | None = None  # no cover on the project list, and the agent cannot see the project
    youtube_channel: str | None = None  # the channel this project's posts go to; "" clears it. The user's pick, never the agent's
    width: int | None = None
    height: int | None = None
    image_workflow: str | None = None
    video_workflow: str | None = None
    video_length_s: float | None = None
    image_prompt: str | None = None
    video_prompt: str | None = None
    image_negative: str | None = None
    video_negative: str | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def changes(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in SETTINGS_FIELDS:
            value = getattr(self, key)
            if value is not None:
                out[key] = value.strip() if isinstance(value, str) else value
        return out

    def check(self, project, ctx, env) -> None:
        if self.project_name is not None and not self.project_name.strip():
            raise CommandRefused("bad_value", "project name is empty")
        if (self.width is None) != (self.height is None):
            raise CommandRefused("bad_value", "width and height go together")
        if self.width is not None and (self.width <= 0 or self.height <= 0):
            raise CommandRefused("bad_value", "canvas size must be positive")
        if self.video_length_s is not None and self.video_length_s <= 0:
            raise CommandRefused("bad_value", "video_length_s must be positive")
        for wf in (self.image_workflow, self.video_workflow):
            if wf and not env.workflow_exists(wf.strip()):
                raise CommandRefused("unknown_workflow", f"no workflow {wf.strip()}")
        if self.brief is not None and not self.brief.strip():
            raise CommandRefused("bad_value", "brief is empty")
        if self.youtube_channel is not None:
            if ctx.actor == "agent" and not ctx.bypass_gates:
                raise CommandRefused("wrong_actor", "the YouTube channel is the user's pick, in the project settings; ask them to connect or choose one there")
            cid = self.youtube_channel.strip()
            if cid and not env.channel_known(cid):
                raise CommandRefused("unknown_channel", f"channel {cid} is not connected on this server; connect it from the project settings first", {"channel": cid})
        if self.project_name is None and self.brief is None and self.private is None and self.youtube_channel is None and not self.changes():
            raise CommandRefused("bad_value", "nothing to change")

    def apply(self, project, ctx, env) -> Applied:
        self._changed = self.changes()
        self._ended_session = False
        self._resumed_session = False
        patch: dict[str, Any] = {}
        if self.project_name is not None:
            patch["name"] = self.project_name.strip()
        if self.brief is not None:
            patch["brief"] = self.brief.strip()
        if self.private is not None:
            patch["private"] = self.private
        if self.youtube_channel is not None:
            patch["youtube_channel"] = self.youtube_channel.strip()
            self._channel_label = env.channel_label(self.youtube_channel.strip())
        if self._changed:
            patch["defaults"] = {**_dump(project.defaults), **self._changed}
        apply_patch(project, patch)
        if self.private and _session_id(project):
            # going private shuts the agent out; a session it could not continue ends here
            project.agent_session.status = "ended"
            project.agent_session.ended = ctx.ts()
            project.agent_session.ended_by_private = True
            self._ended_session = True
        sess = project.agent_session
        if self.private is False and sess is not None and sess.status == "ended" and sess.ended_by_private:
            # back to public: the session going private ended reopens, waiting, in the same mode,
            # so the agent finds the project again without being told
            project.agent_session = AgentSession(id=ids.new_id(ids.SESSION), status="waiting", mode=sess.mode, drafts=sess.drafts, started=ctx.ts(), notes_tracked=True)
            self._resumed_session = True
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        ch = getattr(self, "_changed", {})
        parts: list[str] = []
        if self.project_name is not None:
            parts.append(f"renamed to {self.project_name.strip()}")
        if self.brief is not None:
            parts.append("brief revised")
        if self.private is not None:
            private = "private (session ended)" if getattr(self, "_ended_session", False) else "private"
            public = "not private (session reopened)" if getattr(self, "_resumed_session", False) else "not private"
            parts.append(private if self.private else public)
        if self.youtube_channel is not None:
            label = getattr(self, "_channel_label", self.youtube_channel.strip())
            parts.append(f"youtube channel {label}" if self.youtube_channel.strip() else "youtube channel cleared")
        if "width" in ch:
            parts.append(f"{ch['width']}x{ch['height']}")
        if "image_workflow" in ch:
            parts.append(f"image {_wf_stem(ch['image_workflow']) or 'none'}")
        if "video_workflow" in ch:
            parts.append(f"video {_wf_stem(ch['video_workflow']) or 'none'}")
        if "video_length_s" in ch:
            parts.append(f"video length {_fmt_len(ch['video_length_s'])}")
        prompts = [k for k in GLOBAL_PROMPT_FIELDS if k in ch]
        if prompts:
            parts.append(f"global prompts {len(prompts)}")
        return "project settings - " + " - ".join(parts) if parts else "project settings"


class ConnectSession(Command):
    """``takeover`` connects to a session that is already live: the server sets it when the
    agent that had the session has not been heard for a while (``agent_activity.gone``), so a
    harness that closed without ``end`` does not strand the project."""

    name: ClassVar[str] = "connect_session"
    harness: str
    takeover: bool = False
    # the agent that owns the session coming back to it (a restart, a quiet spell): logged, but
    # no feed line -- it is the same agent, nothing changed hands
    rejoin: bool = False

    def target(self, project: Project) -> ChangeTarget:
        sess = project.agent_session
        return ChangeTarget(type="session", id=sess.id if sess else "")

    def check(self, project, ctx, env) -> None:
        if not str(self.harness).strip():
            raise CommandRefused("bad_value", "harness name is empty")
        _not_private(project)

    def apply(self, project, ctx, env) -> Applied:
        sess = project.agent_session
        sess.status = "live"
        sess.harness = self.harness.strip()
        sess.connected = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        if self.rejoin:
            return f"agent rejoined via {self.harness.strip()}"
        if self.takeover:
            return f"agent reconnected via {self.harness.strip()} (the previous one was gone)"
        return f"agent connected via {self.harness.strip()}"


class EndSession(Command):
    name: ClassVar[str] = "end_session"

    def target(self, project: Project) -> ChangeTarget:
        sess = project.agent_session
        return ChangeTarget(type="session", id=sess.id if sess else "")

    def check(self, project, ctx, env) -> None:
        if not _session_id(project):
            raise CommandRefused("no_session", "no session to end")

    def apply(self, project, ctx, env) -> Applied:
        sess = project.agent_session
        sess.status = "ended"
        sess.ended = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        return "session ended"


# -- sequences ---------------------------------------------------------------
#
# A sequence is an ordered list of bins; each item plays its bin's selected take. Every edit
# targets the project (the list is the project's), so one change holds the whole move and a
# revert puts it all back. The user and the agent use the same commands; the agent's extra
# settings on an item (``agent``) are the one agent-only field.

EXPORTS_TAG = "exports"
SEQ_TRIM_TOLERANCE_S = 0.05  # a take this close in length to the trimmed one still fits the trim
# the one key in the agent's settings the app reads: what the user sees under the player
AGENT_SUMMARY_KEY = "summary"
MAX_AGENT_SUMMARY = 120
# an item's summary describes that item alone; these words say it is really a sequence-wide layer
_WHOLE_SEQUENCE_WORDS = re.compile(r"\b(whole|entire|throughout|under the (?:whole |entire )?(?:cut|sequence)|across the)\b", re.IGNORECASE)


def _merge_agent(current: dict[str, Any], props: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for k, v in props.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = copy.deepcopy(v)
    return merged


def _check_agent_summary(merged: dict[str, Any], *, where: str, example: str) -> None:
    """Settings that change the export need the agent's own words for the user: a short
    ``summary``, required whenever any other key is in place, and never longer than a line."""
    others = [k for k in merged if k != AGENT_SUMMARY_KEY]
    summary = merged.get(AGENT_SUMMARY_KEY)
    if others and (not isinstance(summary, str) or not summary.strip()):
        raise CommandRefused(
            "summary_required",
            f'include "{AGENT_SUMMARY_KEY}": a few words the user reads under the player saying what the raw preview will not have on {where} '
            f'(e.g. "{example}"); keep it current when the settings change',
            {"keys": others},
        )
    if isinstance(summary, str) and len(summary.strip()) > MAX_AGENT_SUMMARY:
        raise CommandRefused("bad_value", f"{AGENT_SUMMARY_KEY} is a line, keep it under {MAX_AGENT_SUMMARY} characters")
    if isinstance(summary, str) and re.search(r"\bcut\b", summary, re.IGNORECASE):
        raise CommandRefused("bad_value", f'{AGENT_SUMMARY_KEY}: the user\'s word is "sequence", never "cut"')


def _sequence(project: Project, seq_id: str) -> Sequence:
    for seq in project.sequences:
        if seq.id == seq_id:
            return seq
    raise CommandRefused("unknown_id", f"no sequence {seq_id}", {"type": "sequence", "id": seq_id})


def _seq_item(seq: Sequence, bin_id: str) -> SequenceItem:
    for item in seq.items:
        if item.bin == bin_id:
            return item
    raise CommandRefused("not_in_sequence", f"{bin_id} is not in {seq.name}", {"type": "bin", "id": bin_id})


def _seq_label(project: Project, bin_id: str) -> str:
    bin_ = project.bins_by_id.get(bin_id)
    return bin_label(bin_) if bin_ else bin_id


def _unique_sequence_name(project: Project, wanted: str) -> str:
    taken = {s.name.strip().lower() for s in project.sequences}
    if wanted.lower() not in taken:
        return wanted
    n = 2
    while f"{wanted} {n}".lower() in taken:
        n += 1
    return f"{wanted} {n}"


def next_sequence_name(project: Project) -> str:
    """``Sequence 1``, ``Sequence 2``, ... the lowest number nothing is using."""
    taken = {s.name.strip().lower() for s in project.sequences}
    n = 1
    while f"sequence {n}" in taken:
        n += 1
    return f"Sequence {n}"


def ensure_output_bin(project: Project, ctx: CommandContext, seq: Sequence) -> Bin:
    """The bin this sequence's exports land in: the one it points at when that still exists,
    else a fresh ``<name> Exports`` made now and remembered. Called by the export landing."""
    out = project.bins_by_id.get(seq.output_bin) if seq.output_bin else None
    if out is None:
        out = _mint_output_bin(project, ctx, seq.name)
        seq.output_bin = out.id
        seq.updated = ctx.ts()
    return out


def _mint_output_bin(project: Project, ctx: CommandContext, seq_name: str) -> Bin:
    """The bin a sequence's exports land in: ``<name> Exports``, tagged, no workflow. Named
    once; it does not follow a rename of the sequence."""
    name = _unique_name(project, f"{seq_name} Exports")
    bin_ = Bin(id=ids.new_id(ids.BIN), name=name, slug=_new_slug(project, name, None), workflow="", tags=[EXPORTS_TAG], created=ctx.ts())
    project.bins_by_id[bin_.id] = bin_
    return bin_


def _selected_media(project: Project, bin_: Bin) -> Media | None:
    """The media the bin leads with: its selected when that still exists, else its newest."""
    if bin_.selected and bin_.selected in project.media_by_id and bin_.selected in bin_.media:
        return project.media_by_id[bin_.selected]
    held = [project.media_by_id[m] for m in bin_.media if m in project.media_by_id]
    if not held:
        return None
    return sorted(reversed(held), key=lambda m: m.created, reverse=True)[0]


def item_media(project: Project, item: SequenceItem) -> Media | None:
    """The media an item plays: the bin's current take, or None for a bin with no take. Audio
    counts as no take: a song is a layer on the sequence, never a picture in it, so a bin
    whose current media turned to audio after it was added plays as a title card."""
    bin_ = project.bins_by_id.get(item.bin)
    media = _selected_media(project, bin_) if bin_ is not None else None
    return None if media is None or media.type == "audio" else media


def item_kind(project: Project, item: SequenceItem) -> str:
    """``video``, ``still`` or ``title`` (no take yet: the bin's name on a card)."""
    media = item_media(project, item)
    if media is None:
        return "title"
    return "video" if media.type == "video" else "still"


def trim_dropped(project: Project, item: SequenceItem) -> bool:
    """The trim was applied against a take of another length: it no longer fits and is ignored."""
    if not (item.head_s or item.tail_s) or item.trim_len_s is None:
        return False
    media = item_media(project, item)
    if media is None or media.type != "video" or media.duration_s is None:
        return False
    return abs(float(media.duration_s) - float(item.trim_len_s)) > SEQ_TRIM_TOLERANCE_S


def item_duration(project: Project, item: SequenceItem) -> float:
    """What the item contributes to the running time, in seconds."""
    bin_ = project.bins_by_id.get(item.bin)
    media = item_media(project, item)
    if media is None:
        length = (bin_.length_s if bin_ is not None else None) or project.defaults.video_length_s
        return float(length or STILL_DEFAULT_S)
    if media.type != "video":
        return float(item.still_s if item.still_s is not None else STILL_DEFAULT_S)
    total = float(media.duration_s or 0.0)
    if trim_dropped(project, item):
        return total
    return max(0.0, total - float(item.head_s or 0.0) - float(item.tail_s or 0.0))


def _fmt_clock(seconds: float) -> str:
    s = max(0.0, float(seconds))
    m = int(s // 60)
    return f"{m}:{s - m * 60:04.1f}"


class CreateSequence(Command):
    """An empty sequence. Its output bin is made at the first export, unless one is chosen first."""

    name: ClassVar[str] = "create_sequence"
    sequence_name: str | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if self.sequence_name is not None and not str(self.sequence_name).strip():
            raise CommandRefused("bad_value", "sequence name is empty")

    def apply(self, project, ctx, env) -> Applied:
        name = _unique_sequence_name(project, str(self.sequence_name).strip()) if self.sequence_name else next_sequence_name(project)
        seq = Sequence(id=ids.new_id(ids.SEQUENCE), name=name, created=ctx.ts(), updated=ctx.ts())
        project.sequences = [*project.sequences, seq]
        self._created = seq.id
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        seq = next((s for s in project.sequences if s.id == getattr(self, "_created", "")), None)
        return f"created sequence {seq.name if seq else '?'}"


class SetSequenceField(Command):
    name: ClassVar[str] = "set_sequence_field"
    sequence: str
    field: Literal["name", "output_bin"]
    value: Any = None
    """``name``: the title. ``output_bin``: the bin exports land in -- any bin not in the sequence,
    or null for a fresh ``<name> Exports`` made at the next export."""

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        seq = _sequence(project, self.sequence)
        if self.field == "output_bin":
            if self.value is None:
                return
            bin_ = _bin(project, str(self.value))
            if any(i.bin == bin_.id for i in seq.items):
                raise CommandRefused("bad_value", f"{bin_label(bin_)} is in the sequence; exports cannot land in a bin that plays in the cut")
            return
        if not isinstance(self.value, str) or not self.value.strip():
            raise CommandRefused("bad_value", "sequence name is empty")
        if len(self.value.strip()) > MAX_BIN_NAME:
            raise CommandRefused("bad_value", f"a sequence name is a title, keep it under {MAX_BIN_NAME} characters")

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        if self.field == "output_bin":
            seq.output_bin = str(self.value) if self.value else None
        else:
            self._old = seq.name
            seq.name = str(self.value).strip()
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        seq = next((s for s in project.sequences if s.id == self.sequence), None)
        name = seq.name if seq else self.sequence
        if self.field == "output_bin":
            if not self.value:
                return f"{name} - exports land in a new bin at the next export"
            return f"{name} - exports land in {_seq_label(project, str(self.value))}"
        return f"renamed sequence {getattr(self, '_old', '?')} to {name}"


class CloneSequence(Command):
    """``<name> copy`` with the same items, order, trims, still lengths and agent settings; its
    exports get their own bin at its first export."""

    name: ClassVar[str] = "clone_sequence"
    sequence: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        _sequence(project, self.sequence)

    def apply(self, project, ctx, env) -> Applied:
        src = _sequence(project, self.sequence)
        name = _unique_sequence_name(project, f"{src.name} copy")
        copy_ = Sequence(
            id=ids.new_id(ids.SEQUENCE), name=name,
            items=[SequenceItem.model_validate(_dump(i)) for i in src.items], agent=copy.deepcopy(src.agent), created=ctx.ts(), updated=ctx.ts(),
        )
        project.sequences = [*project.sequences, copy_]
        self._created = copy_.id
        self._src = src.name
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        seq = next((s for s in project.sequences if s.id == getattr(self, "_created", "")), None)
        return f"cloned {getattr(self, '_src', '?')} as {seq.name if seq else 'a sequence'}"


class DeleteSequence(Command):
    """Remove a sequence. The bins in it are untouched. Its output bin stays as an ordinary bin
    unless ``delete_exports`` says to take it, and its media, with it (the ``delete_bin`` cascade)."""

    name: ClassVar[str] = "delete_sequence"
    sequence: str
    delete_exports: bool = False

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        _sequence(project, self.sequence)

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        self._name = seq.name
        out = project.bins_by_id.get(seq.output_bin) if seq.output_bin else None
        self._out = out.name if out else None
        self._exports = len(out.media) if out else 0
        project.sequences = [s for s in project.sequences if s.id != seq.id]
        self._deleted_bin = False
        if self.delete_exports and out is not None:
            delete = DeleteBin(bin=out.id)
            delete.apply(project, ctx, env)
            self._deleted_bin = True
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        name = getattr(self, "_name", self.sequence)
        out = getattr(self, "_out", None)
        if getattr(self, "_deleted_bin", False):
            n = getattr(self, "_exports", 0)
            return f"deleted sequence {name} and bin {out} ({n} export{'s' if n != 1 else ''})"
        return f"deleted sequence {name}, kept bin {out}" if out else f"deleted sequence {name}"


class AddSequenceItems(Command):
    """Add bins to a sequence, each once: at the end, or before position ``at`` (0-based)."""

    name: ClassVar[str] = "add_sequence_items"
    sequence: str
    bins: list[str]
    at: int | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        seq = _sequence(project, self.sequence)
        if not self.bins:
            raise CommandRefused("bad_value", "no bins to add")
        if len(set(self.bins)) != len(self.bins):
            raise CommandRefused("bad_value", "a bin is listed twice")
        held = {i.bin for i in seq.items}
        for bin_id in self.bins:
            bin_ = _bin(project, bin_id)
            if bin_id in held:
                raise CommandRefused("already_in_sequence", f"{bin_label(bin_)} is already in {seq.name}", {"type": "bin", "id": bin_id})
            if seq.output_bin and bin_id == seq.output_bin:
                raise CommandRefused("bad_value", f"{bin_.name} holds this sequence's exports; it cannot be in the sequence")
            current = _selected_media(project, bin_)
            if current is not None and current.type == "audio":
                raise CommandRefused(
                    "audio_item",
                    f"{bin_label(bin_)} leads with audio; a song or a bed goes on the sequence as a layer (set_sequence_layer), not as an item",
                    {"type": "bin", "id": bin_id, "media": current.id},
                )

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        new = [SequenceItem(bin=b) for b in self.bins]
        at = len(seq.items) if self.at is None else max(0, min(len(seq.items), int(self.at)))
        seq.items = [*seq.items[:at], *new, *seq.items[at:]]
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        seq = next((s for s in project.sequences if s.id == self.sequence), None)
        names = ", ".join(_seq_label(project, b) for b in self.bins)
        where = f" at #{int(self.at) + 1}" if self.at is not None and seq is not None and int(self.at) < len(seq.items) - len(self.bins) + 1 else ""
        return f"added {names} to {seq.name if seq else self.sequence}{where}"


class RemoveSequenceItem(Command):
    """Take a bin out of a sequence. The bin and its takes stay."""

    name: ClassVar[str] = "remove_sequence_item"
    sequence: str
    bin: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        _seq_item(_sequence(project, self.sequence), self.bin)

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        seq.items = [i for i in seq.items if i.bin != self.bin]
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        seq = next((s for s in project.sequences if s.id == self.sequence), None)
        return f"removed {_seq_label(project, self.bin)} from {seq.name if seq else self.sequence}"


class ReorderSequence(Command):
    """The whole order, as bin ids, every item exactly once."""

    name: ClassVar[str] = "reorder_sequence"
    sequence: str
    order: list[str]

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        seq = _sequence(project, self.sequence)
        if sorted(self.order) != sorted(i.bin for i in seq.items):
            raise CommandRefused("bad_order", "order must list every bin in the sequence exactly once")

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        before = [i.bin for i in seq.items]
        by_bin = {i.bin: i for i in seq.items}
        seq.items = [by_bin[b] for b in self.order]
        seq.updated = ctx.ts()
        # the one item that moved, when one did: "moved shot 8 to #3" reads better than "reordered"
        self._moved = None
        for b in self.order:
            if before.index(b) != self.order.index(b) and [x for x in before if x != b] == [x for x in self.order if x != b]:
                self._moved = b
                break
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        seq = next((s for s in project.sequences if s.id == self.sequence), None)
        name = seq.name if seq else self.sequence
        moved = getattr(self, "_moved", None)
        if moved:
            return f"{name} - moved {_seq_label(project, moved)} to #{self.order.index(moved) + 1}"
        return f"{name} - reordered"


class SetItemTrim(Command):
    """Head and tail, in seconds, dropped from a video item's selected take. The take itself is
    never changed. Remembers the take's length so a take of another length drops the trim."""

    name: ClassVar[str] = "set_item_trim"
    sequence: str
    bin: str
    head_s: float = 0.0
    tail_s: float = 0.0

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        seq = _sequence(project, self.sequence)
        _seq_item(seq, self.bin)
        bin_ = _bin(project, self.bin)
        media = _selected_media(project, bin_)
        if media is None or media.type != "video":
            raise CommandRefused("wrong_kind", f"{bin_label(bin_)} has no video take to trim")
        if self.head_s < 0 or self.tail_s < 0:
            raise CommandRefused("bad_value", "head_s and tail_s must be 0 or more")
        if media.duration_s is not None and self.head_s + self.tail_s >= float(media.duration_s) - SEQ_TRIM_TOLERANCE_S:
            raise CommandRefused("bad_value", f"head + tail leave nothing of the {float(media.duration_s):.1f}s take")

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        item = _seq_item(seq, self.bin)
        media = _selected_media(project, _bin(project, self.bin))
        item.head_s = float(self.head_s)
        item.tail_s = float(self.tail_s)
        item.trim_len_s = float(media.duration_s) if media is not None and media.duration_s is not None and (self.head_s or self.tail_s) else None
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        if not self.head_s and not self.tail_s:
            return f"{_seq_label(project, self.bin)} - full take"
        return f"trimmed {_seq_label(project, self.bin)} head {self.head_s:g}s tail {self.tail_s:g}s"


class SetItemStill(Command):
    """How long a still item shows."""

    name: ClassVar[str] = "set_item_still"
    sequence: str
    bin: str
    still_s: float

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        seq = _sequence(project, self.sequence)
        _seq_item(seq, self.bin)
        bin_ = _bin(project, self.bin)
        media = _selected_media(project, bin_)
        if media is not None and media.type == "video":
            raise CommandRefused("wrong_kind", f"{bin_label(bin_)} plays a video; its length is the take's")
        if self.still_s < 0.5:
            raise CommandRefused("bad_value", "a still shows for at least 0.5s")

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        item = _seq_item(seq, self.bin)
        self._old = item.still_s if item.still_s is not None else STILL_DEFAULT_S
        item.still_s = float(self.still_s)
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        return f"{_seq_label(project, self.bin)} shown {getattr(self, '_old', STILL_DEFAULT_S):g}s to {self.still_s:g}s"


class SetItemAgent(Command):
    """The agent's own settings on an item (sound, gain, a dissolve): merged key by key, a null
    value removes a key. The app shows them read-only and never changes them; the agent honours
    them when it builds the export itself. ``summary`` is the one key the app reads: a few words
    the user sees under the player saying what the raw preview will not have ("walla, 0.3s fade
    in/out, pop removal"); it is required whenever any other setting is in place."""

    name: ClassVar[str] = "set_item_agent"
    sequence: str
    bin: str
    props: dict[str, Any]

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def _merged(self, item: SequenceItem) -> dict[str, Any]:
        return _merge_agent(item.agent, self.props)

    def check(self, project, ctx, env) -> None:
        if ctx.actor != "agent" and not ctx.bypass_gates:
            raise CommandRefused("wrong_actor", "agent settings on a sequence item are the agent's; the app has no control for them")
        item = _seq_item(_sequence(project, self.sequence), self.bin)
        if not self.props:
            raise CommandRefused("bad_value", "no settings given")
        merged = self._merged(item)
        _check_agent_summary(merged, where="this item", example="pop removal, 0.3s fade in")
        summary = merged.get(AGENT_SUMMARY_KEY)
        if isinstance(summary, str) and _WHOLE_SEQUENCE_WORDS.search(summary):
            raise CommandRefused(
                "scope_item",
                "an item's summary describes that item alone, times from its start; a layer that spans the sequence "
                "(a walla bed, room tone, music) goes on the sequence with set_sequence_agent, and each item says only "
                'what happens in it ("walla fades out over 1.5s from 4.8s")',
            )

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        item = _seq_item(seq, self.bin)
        item.agent = self._merged(item)
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        parts = [f"{k} {v}" if not isinstance(v, bool) else k for k, v in self.props.items() if v is not None]
        gone = [k for k, v in self.props.items() if v is None]
        bits = ([", ".join(parts)] if parts else []) + ([f"cleared {', '.join(gone)}"] if gone else [])
        return f"{_seq_label(project, self.bin)} - agent settings: {'; '.join(bits)}"


class SetSequenceAgent(Command):
    """The agent's settings that span the whole sequence (a walla bed, room tone, music): merged
    key by key like an item's, with the same ``summary`` rule. Shown once, at the sequence
    level; an item's own settings stay item-local."""

    name: ClassVar[str] = "set_sequence_agent"
    sequence: str
    props: dict[str, Any]

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if ctx.actor != "agent" and not ctx.bypass_gates:
            raise CommandRefused("wrong_actor", "agent settings on a sequence are the agent's; the app has no control for them")
        seq = _sequence(project, self.sequence)
        if not self.props:
            raise CommandRefused("bad_value", "no settings given")
        _check_agent_summary(_merge_agent(seq.agent, self.props), where="the whole sequence", example="walla bed, 0.3s fade in at the start, 0.8s fade out at the end")

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        seq.agent = _merge_agent(seq.agent, self.props)
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        seq = next((s for s in project.sequences if s.id == self.sequence), None)
        parts = [f"{k} {v}" if not isinstance(v, bool) else k for k, v in self.props.items() if v is not None]
        gone = [k for k, v in self.props.items() if v is None]
        bits = ([", ".join(parts)] if parts else []) + ([f"cleared {', '.join(gone)}"] if gone else [])
        return f"{seq.name if seq else self.sequence} - sequence settings: {'; '.join(bits)}"


# -- sound layers and the mix -----------------------------------------------------------

SEQUENCE_DEFAULT_FPS = 24.0
LAYER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
GAIN_MIN_DB, GAIN_MAX_DB = -60.0, 12.0
MAX_EDGE_FADE_MS = 500
MAX_RAMP_MS = 2000
MAX_FADE_S = 30.0


def sequence_fps(project: Project, seq: Sequence) -> float:
    """The rate every frame count in the sequence is measured at: the first video item's take,
    else 24. The stitcher conforms every item to it."""
    for item in seq.items:
        media = item_media(project, item)
        if media is not None and media.type == "video" and media.fps:
            return float(media.fps)
    return SEQUENCE_DEFAULT_FPS


def item_frames(project: Project, item: SequenceItem, fps: float) -> int:
    """An item's exact length in whole frames: its duration at the sequence rate, rounded once.
    Start frames are cumulative sums of these, so the picture never drifts against a layer."""
    return max(1, int(round(item_duration(project, item) * fps)))


def item_frame_spans(project: Project, seq: Sequence, fps: float | None = None) -> list[tuple[int, int]]:
    """``(start_frame, frames)`` per item, in order."""
    rate = fps or sequence_fps(project, seq)
    out: list[tuple[int, int]] = []
    start = 0
    for item in seq.items:
        n = item_frames(project, item, rate)
        out.append((start, n))
        start += n
    return out


def layer_span_under(layer: SoundLayer, media: Media | None, start_frame: int, frames: int, fps: float) -> tuple[float, float] | None:
    """The source span (seconds in the layer's media) heard under an item, or None when the layer
    is silent there: not started yet, or run out (``end: source``)."""
    first = max(start_frame, layer.start_frame)
    last = start_frame + frames
    if first >= last:
        return None
    src_in = float(layer.source_in_s) + (first - layer.start_frame) / fps
    src_out = float(layer.source_in_s) + (last - layer.start_frame) / fps
    if layer.end == "source" and media is not None and media.duration_s:
        if src_in >= float(media.duration_s):
            return None
        src_out = min(src_out, float(media.duration_s))
    return (round(src_in, 4), round(src_out, 4))


def _layer(seq: Sequence, layer_id: str) -> SoundLayer:
    for layer in seq.layers:
        if layer.id == layer_id:
            return layer
    raise CommandRefused("unknown_layer", f"{seq.name} has no layer {layer_id}", {"type": "layer", "id": layer_id, "layers": [x.id for x in seq.layers]})


def _carries_audio(media: Media) -> bool:
    if media.type == "audio":
        return True
    if media.type != "video":
        return False
    from .stitch import has_audio_stream  # local: stitch imports this module lazily

    return has_audio_stream(media.path)


def _check_db(value: Any, what: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise CommandRefused("bad_value", f"{what} must be a number of dB") from None
    if not (GAIN_MIN_DB <= v <= GAIN_MAX_DB):
        raise CommandRefused("bad_value", f"{what} must be between {GAIN_MIN_DB:g} and {GAIN_MAX_DB:g} dB")
    return v


def _set_summary(settings: dict[str, Any], summary: str | None, *, where: str, example: str, item_scope: bool = False, require: bool = True) -> dict[str, Any]:
    """The agent's words for the user ride with every sound edit: written into the agent
    settings' ``summary`` and checked like ``set_item_agent`` checks its own. ``require`` means
    this edit changes what is heard, so the line must come with it (a stale one does not do)."""
    merged = dict(settings)
    if require and (summary is None or not str(summary).strip()):
        raise CommandRefused(
            "summary_required",
            f'include "summary": a few words the user reads under the player saying what they hear on {where} (e.g. "{example}"); every edit that changes the sound brings its line',
        )
    if summary is not None:
        text = str(summary).strip()
        if text:
            merged[AGENT_SUMMARY_KEY] = text
        else:
            merged.pop(AGENT_SUMMARY_KEY, None)
    _check_agent_summary(merged, where=where, example=example)
    text = merged.get(AGENT_SUMMARY_KEY)
    if item_scope and isinstance(text, str) and _WHOLE_SEQUENCE_WORDS.search(text):
        raise CommandRefused("scope_item", "an item's summary describes that item alone; what spans the sequence is said on the layer (set_sequence_layer)")
    return merged


class SetSequenceLayer(Command):
    """Add a sound layer to a sequence, or replace the one with this id: a song or a bed that
    runs continuously under the picture from ``source_in_s``, starting at ``start_frame``. The
    export lays it down in one piece; per-item level changes are ``set_item_mix``. ``summary``
    is the user's line under the player and is required (it becomes the sequence's agent
    ``summary``). Agent only."""

    name: ClassVar[str] = "set_sequence_layer"
    sequence: str
    layer: SoundLayer
    summary: str | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if ctx.actor != "agent" and not ctx.bypass_gates:
            raise CommandRefused("wrong_actor", "sound layers are the agent's; the app has no control for them")
        seq = _sequence(project, self.sequence)
        layer = self.layer
        if not LAYER_ID_RE.match(layer.id or ""):
            raise CommandRefused("bad_value", "a layer id is a short lowercase name: music, walla, room-tone")
        media = _media(project, layer.media)
        if not _carries_audio(media):
            raise CommandRefused("no_audio", f"{layer.media} carries no audio track", {"type": "media", "id": layer.media})
        if layer.source_in_s < 0:
            raise CommandRefused("bad_value", "source_in_s must be 0 or more")
        if media.duration_s and layer.source_in_s >= float(media.duration_s):
            raise CommandRefused("bad_value", f"source_in_s is past the end of the {float(media.duration_s):.1f}s source")
        if layer.start_frame < 0:
            raise CommandRefused("bad_value", "start_frame must be 0 or more")
        _check_db(layer.gain_db, "gain_db")
        for key in ("fade_in_s", "fade_out_s"):
            v = getattr(layer, key)
            if v < 0 or v > MAX_FADE_S:
                raise CommandRefused("bad_value", f"{key} must be between 0 and {MAX_FADE_S:g} s")
        _set_summary(seq.agent, self.summary, where="the whole sequence", example="song under the whole sequence from its first chorus, -6 dB, 1 s fade out")

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        self._replaced = any(x.id == self.layer.id for x in seq.layers)
        seq.layers = [x for x in seq.layers if x.id != self.layer.id] + [self.layer.model_copy(deep=True)]
        seq.agent = _set_summary(seq.agent, self.summary, where="the whole sequence", example="", require=False)
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        seq = next((s for s in project.sequences if s.id == self.sequence), None)
        name = seq.name if seq else self.sequence
        verb = "replaced layer" if getattr(self, "_replaced", False) else "added layer"
        lay = self.layer
        media = project.media_by_id.get(lay.media)
        home = _bin_for(project, media) if media is not None else None
        source = f"{bin_label(home)} {_media_label(home, media)}" if home is not None and media is not None else lay.media
        bits = [f"{source} from {lay.source_in_s:g}s at frame {lay.start_frame}"]
        if lay.gain_db:
            bits.append(f"{lay.gain_db:+g} dB")
        if lay.fade_in_s or lay.fade_out_s:
            bits.append(f"fade {lay.fade_in_s:g}s/{lay.fade_out_s:g}s")
        if lay.end == "source":
            bits.append("to the end of the source")
        return f"{name} - {verb} {lay.id}: {', '.join(bits)}"


class RemoveSequenceLayer(Command):
    """Take a layer off a sequence; every item's offset for it goes too. Agent only."""

    name: ClassVar[str] = "remove_sequence_layer"
    sequence: str
    layer: str
    summary: str | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if ctx.actor != "agent" and not ctx.bypass_gates:
            raise CommandRefused("wrong_actor", "sound layers are the agent's; the app has no control for them")
        seq = _sequence(project, self.sequence)
        _layer(seq, self.layer)
        remaining = [x for x in seq.layers if x.id != self.layer]
        if remaining or [k for k in seq.agent if k != AGENT_SUMMARY_KEY]:
            _set_summary(seq.agent, self.summary, where="the whole sequence", example="walla bed only now, the song is gone", require=bool(remaining))
        elif self.summary is None:
            raise CommandRefused("summary_required", 'say what the user hears now, in a few words ("no music under the sequence"), or "" to clear the line')

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        seq.layers = [x for x in seq.layers if x.id != self.layer]
        for item in seq.items:
            if item.mix is not None and self.layer in item.mix.layers:
                item.mix.layers = {k: v for k, v in item.mix.layers.items() if k != self.layer}
        remaining_keys = [k for k in seq.agent if k != AGENT_SUMMARY_KEY]
        if seq.layers or remaining_keys:
            seq.agent = _set_summary(seq.agent, self.summary, where="the whole sequence", example="", require=False)
        else:
            merged = dict(seq.agent)
            text = str(self.summary or "").strip()
            if text:
                merged[AGENT_SUMMARY_KEY] = text
            else:
                merged.pop(AGENT_SUMMARY_KEY, None)
            seq.agent = merged
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        seq = next((s for s in project.sequences if s.id == self.sequence), None)
        return f"{seq.name if seq else self.sequence} - removed layer {self.layer}"


class SetItemMix(Command):
    """How one item's sound sits against the layers: its own clip level, a dB offset per layer
    (null mutes the layer under this item), the edge fade on its own audio, the ramp speed at
    its edges, and ``reviewed_start_frame`` once the agent has listened to it in place. Merged
    key by key; ``layers`` merges per layer id (a missing id is unchanged). ``summary`` is the
    item's line for the user and is required whenever the mix departs from the defaults. Agent
    only."""

    name: ClassVar[str] = "set_item_mix"
    sequence: str
    bin: str
    clip_db: float | None = None
    layers: dict[str, float | None] | None = None
    edge_fade_ms: int | None = None
    ramp_ms: int | None = None
    reviewed_start_frame: int | None = None
    summary: str | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def _merged(self, item: SequenceItem) -> ItemMix:
        mix = item.mix.model_copy(deep=True) if item.mix is not None else ItemMix()
        if self.clip_db is not None:
            mix.clip_db = float(self.clip_db)
        if self.layers:
            layers = dict(mix.layers)
            layers.update(self.layers)
            mix.layers = layers
        if self.edge_fade_ms is not None:
            mix.edge_fade_ms = int(self.edge_fade_ms)
        if self.ramp_ms is not None:
            mix.ramp_ms = int(self.ramp_ms)
        if self.reviewed_start_frame is not None:
            mix.reviewed_start_frame = int(self.reviewed_start_frame)
        return mix

    def check(self, project, ctx, env) -> None:
        if ctx.actor != "agent" and not ctx.bypass_gates:
            raise CommandRefused("wrong_actor", "the mix is the agent's; the app has no control for it")
        seq = _sequence(project, self.sequence)
        item = _seq_item(seq, self.bin)
        if all(v is None for v in (self.clip_db, self.layers, self.edge_fade_ms, self.ramp_ms, self.reviewed_start_frame, self.summary)):
            raise CommandRefused("bad_value", "nothing to set")
        if self.clip_db is not None:
            _check_db(self.clip_db, "clip_db")
        known = {x.id for x in seq.layers}
        for key, value in (self.layers or {}).items():
            if key not in known:
                raise CommandRefused("unknown_layer", f"{seq.name} has no layer {key}", {"type": "layer", "id": key, "layers": sorted(known)})
            if value is not None:
                _check_db(value, f"layers.{key}")
        if self.edge_fade_ms is not None and not (0 <= int(self.edge_fade_ms) <= MAX_EDGE_FADE_MS):
            raise CommandRefused("bad_value", f"edge_fade_ms must be between 0 and {MAX_EDGE_FADE_MS}")
        if self.ramp_ms is not None and not (0 <= int(self.ramp_ms) <= MAX_RAMP_MS):
            raise CommandRefused("bad_value", f"ramp_ms must be between 0 and {MAX_RAMP_MS}")
        if self.reviewed_start_frame is not None and int(self.reviewed_start_frame) < 0:
            raise CommandRefused("bad_value", "reviewed_start_frame must be 0 or more")
        mix = self._merged(item)
        departs = mix.clip_db != 0.0 or any(v is None or v != 0.0 for v in mix.layers.values()) or mix.edge_fade_ms != 10 or mix.ramp_ms != 30
        changes_sound = any(v is not None for v in (self.clip_db, self.layers, self.edge_fade_ms, self.ramp_ms))
        if departs or self.summary is not None:
            # the mix is a setting the export reads: it needs the user's line like any other
            merged = dict(item.agent)
            if departs:
                merged.setdefault("mix", True)
            _set_summary(merged, self.summary, where="this item", example="music -8 dB under the line, back up over 30 ms at the end", item_scope=True, require=departs and changes_sound)

    def apply(self, project, ctx, env) -> Applied:
        seq = _sequence(project, self.sequence)
        item = _seq_item(seq, self.bin)
        item.mix = self._merged(item)
        if self.summary is not None:
            merged = dict(item.agent)
            text = str(self.summary).strip()
            if text:
                merged[AGENT_SUMMARY_KEY] = text
            else:
                merged.pop(AGENT_SUMMARY_KEY, None)
            item.agent = merged
        seq.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bits = []
        if self.clip_db is not None:
            bits.append(f"clip {self.clip_db:+g} dB")
        for key, value in (self.layers or {}).items():
            bits.append(f"{key} muted" if value is None else f"{key} {float(value):+g} dB")
        if self.edge_fade_ms is not None:
            bits.append(f"edge fade {self.edge_fade_ms} ms")
        if self.ramp_ms is not None:
            bits.append(f"ramp {self.ramp_ms} ms")
        if self.reviewed_start_frame is not None:
            bits.append(f"reviewed at frame {self.reviewed_start_frame}")
        return f"{_seq_label(project, self.bin)} - mix: {', '.join(bits) if bits else 'summary'}"


# -- posts -------------------------------------------------------------------
#
# A post is a video with the words that go with it, drafted for a platform (YouTube first) and
# held unpublished. Phase 1 is the record and these three commands; the upload (``upload_post``,
# a card the user approves, run by the server off the lock like an export) is phase 2.

POST_FIELDS = ("title", "description", "tags", "privacy", "synthetic", "made_for_kids", "media", "thumbnail", "publish_at", "extra")
_YT_FORBIDDEN = ("<", ">")


def _post_record(project: Project, post_id: str) -> Post:
    for p in project.posts:
        if p.id == post_id:
            return p
    raise CommandRefused("unknown_id", f"no post {post_id}", {"type": "post", "id": post_id})


def _post_media_label(project: Project, media_id: str | None) -> str:
    """``Rough cut Exports take 4`` for a take, ``Video 1 file 2`` for an upload: the bin and the
    words under the tile, as the bin screen shows them."""
    media = project.media_by_id.get(media_id or "")
    bin_ = _bin_for(project, media) if media is not None else None
    if media is None or bin_ is None:
        return _take_label(project, media_id or "")
    return f"{bin_label(bin_)} {_media_label(bin_, media)}"


def post_label(project: Project, post: Post) -> str:
    """How the feed names a post: its title in quotes, else the video it is about."""
    if post.title.strip():
        return f'"{post.title.strip()}"'
    return f"the {post.platform} post for {_post_media_label(project, post.media)}"


def _check_post_title(value: Any) -> str:
    if not isinstance(value, str):
        raise CommandRefused("bad_value", "title must be a string")
    text = " ".join(value.split())
    if any(ch in text for ch in _YT_FORBIDDEN):
        raise CommandRefused("bad_value", "YouTube refuses < and > in a title")
    if len(text) > YT_TITLE_MAX:
        raise CommandRefused("bad_value", f"a YouTube title is at most {YT_TITLE_MAX} characters; this one is {len(text)}", {"max": YT_TITLE_MAX, "length": len(text)})
    return text


def _check_post_description(value: Any) -> str:
    if not isinstance(value, str):
        raise CommandRefused("bad_value", "description must be a string")
    text = value.replace("\r\n", "\n").strip()
    if any(ch in text for ch in _YT_FORBIDDEN):
        raise CommandRefused("bad_value", "YouTube refuses < and > in a description")
    size = len(text.encode("utf-8"))
    if size > YT_DESCRIPTION_MAX_BYTES:
        raise CommandRefused("bad_value", f"a YouTube description is at most {YT_DESCRIPTION_MAX_BYTES} bytes; this one is {size}", {"max": YT_DESCRIPTION_MAX_BYTES, "length": size})
    return text


def _check_post_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [t for t in value.split(",")]
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise CommandRefused("bad_value", "tags must be a list of strings")
    tags = list(dict.fromkeys(" ".join(t.split()) for t in value if t.strip()))
    if any(ch in t for t in tags for ch in _YT_FORBIDDEN):
        raise CommandRefused("bad_value", "YouTube refuses < and > in a tag")
    # YouTube counts every tag's characters plus a separator, and a tag with a space as quoted
    total = sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)
    if total > YT_TAGS_MAX_CHARS:
        raise CommandRefused("bad_value", f"YouTube tags are at most {YT_TAGS_MAX_CHARS} characters together; these are {total}", {"max": YT_TAGS_MAX_CHARS, "length": total})
    return tags


def _check_publish_at(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise CommandRefused("bad_value", "publish_at must be an ISO 8601 time, like 2026-11-02T17:00:00Z")
    text = value.strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise CommandRefused("bad_value", f"publish_at {text!r} is not an ISO 8601 time, like 2026-11-02T17:00:00Z") from None
    return text


NO_CHANNEL_HINT = "connect this project to a YouTube channel in its settings (Project settings > YouTube)"


def _project_channel(project: Project, env: Env) -> str:
    """The channel a new post goes to: the project's. Refused ``no_channel`` when the project
    has none (the user picks one in its settings) and ``unknown_channel`` when the server no
    longer holds that channel's token."""
    cid = (project.youtube_channel or "").strip()
    if not cid:
        raise CommandRefused("no_channel", f"this project has no YouTube channel yet: {NO_CHANNEL_HINT}")
    if not env.channel_known(cid):
        raise CommandRefused("unknown_channel", f"the project's channel {cid} is not connected on this server any more: {NO_CHANNEL_HINT}", {"channel": cid})
    return cid


def _post_destination(project: Project, post: Post, env: Env) -> str:
    """``youtube @halleen`` for the feed, or just ``youtube`` when the post has no channel."""
    label = env.channel_label(post.channel)
    return f"{post.platform} {label}" if label else post.platform


def _post_video(project: Project, bin_: Bin, media_id: str | None) -> Media:
    """The video a post is about: the one named, else the bin's current. Refused when the bin
    has nothing (``no_take``), the media is elsewhere (``not_in_bin``) or is not a video (``not_video``)."""
    if media_id:
        media = _media(project, media_id)
        if media.id not in bin_.media:
            raise CommandRefused("not_in_bin", f"{_post_media_label(project, media.id)} is not in {bin_label(bin_)}", {"media": media.id, "bin": bin_.id})
    else:
        media = _selected_media(project, bin_)
        if media is None:
            raise CommandRefused("no_take", f"{bin_label(bin_)} has no media yet; a post needs a video", {"bin": bin_.id})
    if media.type != "video":
        raise CommandRefused("not_video", f"{_post_media_label(project, media.id)} is {media.type}; a post is a video", {"media": media.id, "type": media.type})
    return media


class CreatePost(Command):
    """Draft a post for a bin's video: the bin's current take unless ``media`` names another take
    in that bin. The words can come now or through ``set_post_field``; nothing is sent."""

    name: ClassVar[str] = "create_post"
    bin: str
    platform: PostPlatform = "youtube"
    media: str | None = None
    title: str = ""
    description: str = ""
    tags: list[str] | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        _post_video(project, bin_, self.media)
        _check_post_title(self.title)
        _check_post_description(self.description)
        _check_post_tags(self.tags)
        _project_channel(project, env)

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        media = _post_video(project, bin_, self.media)
        now = ctx.ts()
        post = Post(
            id=ids.new_id(ids.POST), platform=self.platform, bin=bin_.id, media=media.id, channel=_project_channel(project, env),
            title=_check_post_title(self.title), description=_check_post_description(self.description), tags=_check_post_tags(self.tags),
            created=now, updated=now, by=ctx.actor,
        )
        project.posts = [*project.posts, post]
        self._created = post.id
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        post = next((p for p in project.posts if p.id == getattr(self, "_created", "")), None)
        if post is None:
            return "drafted a post"
        what = f"drafted a {post.platform} post for {_post_media_label(project, post.media)}"
        return f'{what}: "{post.title}"' if post.title else f"{what} (no title yet)"


class SetPostField(Command):
    """One field of a draft. The YouTube limits are checked here, so a draft is always sendable.
    Refused once the post is uploading or uploaded: from then on the video is finished in the
    platform's own app (phase 3 sends edits with ``videos.update``)."""

    name: ClassVar[str] = "set_post_field"
    post: str
    field: Literal["title", "description", "tags", "privacy", "synthetic", "made_for_kids", "media", "thumbnail", "publish_at", "extra"]
    value: Any = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def _value(self, project: Project, post: Post, ctx: CommandContext) -> Any:
        f, v = self.field, self.value
        if f == "title":
            return _check_post_title(v)
        if f == "description":
            return _check_post_description(v)
        if f == "tags":
            return _check_post_tags(v)
        if f == "privacy":
            if v not in ("private", "unlisted", "public"):
                raise CommandRefused("bad_value", "privacy is private, unlisted or public")
            if v == "public" and ctx.actor == "agent":
                raise CommandRefused("bad_value", "the agent never makes a post public; the user does that in the platform's app, or sets it here")
            if v != "private" and post.publish_at:
                raise CommandRefused("bad_value", "a scheduled post (publish_at) stays private until it goes out; clear publish_at first")
            return v
        if f in ("synthetic", "made_for_kids"):
            if not isinstance(v, bool):
                raise CommandRefused("bad_value", f"{f} is true or false")
            return v
        if f == "media":
            bin_ = _bin(project, post.bin)
            return _post_video(project, bin_, str(v) if v else None).id
        if f == "thumbnail":
            if v is None or v == "":
                return None
            media = _media(project, str(v))
            if media.type != "image":
                raise CommandRefused("not_image", f"{_post_media_label(project, media.id)} is {media.type}; a thumbnail is a picture", {"media": media.id})
            return media.id
        if f == "publish_at":
            when = _check_publish_at(v)
            if when and post.privacy != "private":
                raise CommandRefused("bad_value", "a scheduled post must be private until it goes out; set privacy private first")
            return when
        if f == "extra":
            if v is None:
                return {}
            if not isinstance(v, dict):
                raise CommandRefused("bad_value", "extra is a JSON object")
            return _merge_agent(post.extra, v)
        raise CommandRefused("bad_value", f"unknown post field {f}")  # pragma: no cover - the Literal refuses first

    def check(self, project, ctx, env) -> None:
        post = _post_record(project, self.post)
        if post.status == "uploading":
            raise CommandRefused("post_busy", f"{post_label(project, post)} is uploading; wait for it to land")
        if post.status == "uploaded":
            raise CommandRefused("post_uploaded", f"{post_label(project, post)} is on {post.platform} already; finish it there", {"studio_url": post.studio_url})
        self._value(project, post, ctx)

    def apply(self, project, ctx, env) -> Applied:
        post = _post_record(project, self.post)
        self._before = post_label(project, post)
        setattr(post, self.field, self._value(project, post, ctx))
        post.updated = ctx.ts()
        if post.status == "failed":
            post.status, post.error = "draft", None  # edited since the failure: a draft again
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        post = next((p for p in project.posts if p.id == self.post), None)
        label = post_label(project, post) if post is not None else self.post
        f = self.field
        if post is None:
            return f"post {self.post} - {f}"
        if f == "title":
            was = getattr(self, "_before", label)
            return f'{was} - titled "{post.title}"' if post.title else f"{was} - title cleared"
        if f == "description":
            n = len(post.description)
            return f"{label} - description written ({n} character{'s' if n != 1 else ''})" if n else f"{label} - description cleared"
        if f == "tags":
            return f"{label} - tags: {', '.join(post.tags)}" if post.tags else f"{label} - tags cleared"
        if f == "media":
            return f"{label} - now {_post_media_label(project, post.media)}"
        if f == "thumbnail":
            return f"{label} - thumbnail {_post_media_label(project, post.thumbnail)}" if post.thumbnail else f"{label} - thumbnail cleared"
        if f == "publish_at":
            return f"{label} - scheduled for {post.publish_at}" if post.publish_at else f"{label} - schedule cleared"
        if f == "privacy":
            return f"{label} - {post.privacy}"
        if f in ("synthetic", "made_for_kids"):
            word = "altered or synthetic content" if f == "synthetic" else "made for kids"
            return f"{label} - {word}: {'yes' if getattr(post, f) else 'no'}"
        return f"{label} - {f} set"


class DeletePost(Command):
    """Drop a post. The video stays in its bin; a video already on the platform stays there too."""

    name: ClassVar[str] = "delete_post"
    post: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        post = _post_record(project, self.post)
        if post.status == "uploading":
            raise CommandRefused("post_busy", f"{post_label(project, post)} is uploading; wait for it to land")

    def apply(self, project, ctx, env) -> Applied:
        post = _post_record(project, self.post)
        self._label = post_label(project, post)
        self._was = post.status
        project.posts = [p for p in project.posts if p.id != post.id]
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        label = getattr(self, "_label", self.post)
        if getattr(self, "_was", "draft") == "uploaded":
            return f"forgot {label}; the video stays on the platform"
        return f"dropped the draft post {label}"


class UploadPost(Command):
    """Send the post's video to its platform, private, with the words on the record. A spend
    (YouTube allows about six a day) and an outward step, so from the agent it is a card
    (``--propose``) unless the user asked for it in the feed (``requested``); the user's own
    click lands it. The command marks the post ``uploading``; the server does the transfer off
    the lock and lands the outcome with ``record_post_upload``."""

    name: ClassVar[str] = "upload_post"
    post: str

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        post = _post_record(project, self.post)
        if post.status == "uploading":
            raise CommandRefused("post_busy", f"{post_label(project, post)} is uploading already")
        if post.status == "uploaded":
            raise CommandRefused("post_uploaded", f"{post_label(project, post)} is on {post.platform} already; finish it there", {"studio_url": post.studio_url})
        if not post.title.strip():
            raise CommandRefused("title_required", f"{post_label(project, post)} has no title; YouTube needs one")
        if not post.channel:
            raise CommandRefused("no_channel", f"{post_label(project, post)} has no channel to go to: {NO_CHANNEL_HINT}, then draft the post again")
        if not env.channel_known(post.channel):
            raise CommandRefused("unknown_channel", f"the channel of {post_label(project, post)} ({post.channel}) is not connected on this server any more: {NO_CHANNEL_HINT}", {"channel": post.channel})
        media = project.media_by_id.get(post.media)
        if media is None:
            raise CommandRefused("unknown_id", f"the video of {post_label(project, post)} is gone; point the post at another with set_post_field media", {"type": "media", "id": post.media})
        if media.type != "video":
            raise CommandRefused("not_video", f"{_post_media_label(project, media.id)} is {media.type}; a post is a video", {"media": media.id})
        if not env.isfile(media.path):
            raise CommandRefused("missing_file", f"the file of {_post_media_label(project, media.id)} is not on disk: {media.path}", {"media": media.id, "path": media.path})
        # a proposal is described without being applied: the card's line names the channel too
        self._platform = _post_destination(project, post, env)

    def apply(self, project, ctx, env) -> Applied:
        post = _post_record(project, self.post)
        self._label = post_label(project, post)
        self._platform, self._privacy = _post_destination(project, post, env), post.privacy
        post.status, post.error = "uploading", None
        post.updated = ctx.ts()
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        post = next((p for p in project.posts if p.id == self.post), None)
        label = getattr(self, "_label", post_label(project, post) if post else self.post)
        platform = getattr(self, "_platform", post.platform if post else "youtube")
        privacy = getattr(self, "_privacy", post.privacy if post else "private")
        return f"upload {label} to {platform} ({privacy})"


class RecordPostUpload(Command):
    """How the upload ended, written by the server when the transfer finishes. Not in the
    command table: nothing outside the server sends it."""

    name: ClassVar[str] = "record_post_upload"
    post: str
    status: Literal["uploaded", "failed"]
    remote_id: str | None = None
    remote_url: str | None = None
    studio_url: str | None = None
    error: str | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        _post_record(project, self.post)
        if self.status == "uploaded" and not self.remote_id:
            raise CommandRefused("bad_value", "an uploaded post needs its remote id")

    def apply(self, project, ctx, env) -> Applied:
        post = _post_record(project, self.post)
        self._label = post_label(project, post)
        self._platform, self._privacy = _post_destination(project, post, env), post.privacy
        self._where = "YouTube Studio" if post.platform == "youtube" else post.platform
        post.status = self.status
        post.updated = ctx.ts()
        if self.status == "uploaded":
            post.remote_id, post.remote_url, post.studio_url = self.remote_id, self.remote_url, self.studio_url
            post.uploaded, post.error = ctx.ts(), None
        else:
            post.error = (self.error or "upload failed")[:2000]
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        post = next((p for p in project.posts if p.id == self.post), None)
        label = getattr(self, "_label", post_label(project, post) if post else self.post)
        platform = getattr(self, "_platform", "youtube")
        if self.status == "uploaded":
            where = getattr(self, "_where", "YouTube Studio")
            return f"uploaded {label} to {platform} ({getattr(self, '_privacy', 'private')}); finish it in {where}: {self.studio_url or self.remote_url or ''}".rstrip(": ")
        return f"upload of {label} to {platform} failed: {(self.error or 'unknown error')[:300]}"


COMMANDS: dict[str, type[Command]] = {
    cls.name: cls
    for cls in (
        CreatePost,
        SetPostField,
        DeletePost,
        UploadPost,
        CreateSequence,
        SetSequenceField,
        CloneSequence,
        DeleteSequence,
        AddSequenceItems,
        RemoveSequenceItem,
        ReorderSequence,
        SetItemTrim,
        SetItemStill,
        SetItemAgent,
        SetSequenceAgent,
        SetSequenceLayer,
        RemoveSequenceLayer,
        SetItemMix,
        CreateBin,
        DuplicateBin,
        SetBinField,
        BindReference,
        UnbindReference,
        ReorderReferences,
        RebindReference,
        StartFromMedia,
        RecallGeneration,
        Generate,
        ProposeShots,
        MarkTake,
        AddMediaToBin,
        RemoveMediaFromBin,
        DeleteMedia,
        DeleteBin,
        SetProjectSettings,
        PostMessage,
        AnswerNotes,
        WithdrawChange,
        AddProjectRule,
        RemoveProjectRule,
        StartSession,
        SetSessionMode,
        ConnectSession,
        EndSession,
    )
}


def parse_command(name: str, payload: dict) -> Command:
    cls = COMMANDS.get(name)
    if cls is None:
        raise CommandRefused("unknown_command", name)
    try:
        return cls.model_validate(payload)
    except ValidationError as exc:
        raise CommandRefused("invalid_payload", str(exc)) from None


# -- gates -------------------------------------------------------------------


def mode_token(sess: AgentSession | None) -> str | None:
    """The session's mode as one comparable word: ``auto``, ``manual+drafts``..."""
    if sess is None or sess.status not in ("waiting", "live"):
        return None
    return sess.mode + ("+drafts" if sess.drafts else "")


def check_mode_gate(project: Project, command: Command, ctx: CommandContext) -> None:
    """The user changes the mode whenever they like, often right after a note. Every agent
    command that changes something carries the mode the agent last saw; if it is not the mode
    now, the command is refused before it runs, naming the current mode, so the agent re-plans
    at this step rather than finishing a plan made under the old one. A post is held too: words
    written under the old mode ("say go and I'll render it full size" after drafts went off) are
    that old plan talking. Answering a note, connecting, ending and asked-for mode switches are
    never held."""
    if ctx.actor != "agent" or ctx.bypass_gates or not ctx.seen_mode:
        return
    if isinstance(command, (AnswerNotes, ConnectSession, EndSession, SetSessionMode)):
        return
    now = mode_token(project.agent_session)
    if now is not None and now != ctx.seen_mode:
        raise CommandRefused(
            "mode_changed",
            f"the session mode is {now} now (you last saw {ctx.seen_mode}): nothing was done; re-plan this step for {now}",
            {"mode": now, "seen": ctx.seen_mode},
        )


def check_session_gate(project: Project, command: Command, ctx: CommandContext, env: Env | None = None) -> None:
    """An agent needs a live session; connect needs a waiting one; the user never connects."""
    sess = project.agent_session
    if ctx.actor != "agent":
        if isinstance(command, ConnectSession):
            raise CommandRefused("wrong_actor", "only the agent connects a session")
        return
    if isinstance(command, (PostMessage, AnswerNotes)) and not ctx.bypass_gates:
        # talking still needs to be in the right room: one agent drives many projects, and a
        # post carrying another project's session id is the leak, refused rather than logged
        if not sess or sess.status != "live":
            raise CommandRefused("no_session", "an agent post needs a live session here; connect first")
        if ctx.session and ctx.session != sess.id:
            raise CommandRefused("wrong_session", f"session {ctx.session} is not this project's live session ({sess.id}); you are in the wrong project")
        check_mode_gate(project, command, ctx)
        return
    if isinstance(command, (PostMessage, AnswerNotes)):
        return
    if isinstance(command, StartSession):
        raise CommandRefused("wrong_actor", "only the user starts a session")
    if isinstance(command, ConnectSession):
        if sess is not None and sess.status == "live" and command.takeover:
            return
        if not sess or sess.status != "waiting":
            raise CommandRefused("no_session", "no session is waiting for an agent")
        return
    if sess is None and ctx.bypass_gates:
        # harness-first: the song a project was made from (``create --audio``) lands as the
        # agent's before the user opens a session; the upload route sets the bypass, once
        return
    if not sess or sess.status != "live":
        raise CommandRefused("no_session", "agent commands need a live session; connect first")
    check_mode_gate(project, command, ctx)
    # one agent may drive many projects, each under its own session: a session id from another
    # project (a stale state file, a wrong --project) is refused rather than recorded here
    if ctx.session and ctx.session != sess.id:
        raise CommandRefused("wrong_session", f"session {ctx.session} is not this project's live session ({sess.id}); connect here first")
    # a plan session reads and talks; the one thing it writes is the revised brief, and only as
    # a proposal the user approves. The only way out of plan is that approval (or the user's
    # own hand on the panel): a reply in chat is not a verdict, so the agent cannot switch the
    # mode itself, however the reply read.
    if sess.mode == "plan" and not ctx.bypass_gates and not isinstance(command, EndSession):
        if isinstance(command, SetSessionMode):
            raise CommandRefused("plan_mode", "a plan session leaves plan only when the user approves the revised brief or changes the mode in the panel; answer with a revised proposal")
        if not (isinstance(command, SetProjectSettings) and ctx.propose) and not isinstance(command, (AddProjectRule, RemoveProjectRule, WithdrawChange)):
            raise CommandRefused("plan_mode", "a plan session only posts messages and proposes a revised brief (set_project_settings with --propose)")
    if ctx.bypass_gates:
        return
    # a single render is never asked about before it runs: the review modes make the take
    # itself the card, judged after it lands, and auto owes no click at all. Asking first is
    # what propose_shots is for, and in the fully manual mode it is the only way to a render.
    if isinstance(command, Generate) and ctx.propose:
        raise CommandRefused("propose_render", "a render is approved after it lands, not before; propose it with propose_shots, or run it and the take is the card in a review mode")
    if isinstance(command, Generate) and sess.mode == "manual" and not command.requested:
        raise CommandRefused("propose_first", "a manual session renders only what the user approved on a propose_shots card; propose it first")
    # drafts run; the step up to full quality is the user's call, asked on a card first
    if isinstance(command, Generate) and sess.drafts and not command.requested and command.full_quality(project, env or Env()):
        raise CommandRefused(
            "full_quality_ask",
            "a drafts-first session runs drafts on its own (video at quarter size, and draft on wherever the workflow has a THM-IsDraft setting) "
            "and asks before a full-quality render: "
            "propose it with propose_shots (one item, bin = this bin), and the user answers yes, later or no",
            {"bin": command.bin},
        )
    # the batch card is the ask; without the ask it is only what the user already said out loud
    # an upload is a spend and an outward step: the agent asks on a card, or the user asked out loud
    if isinstance(command, UploadPost) and not ctx.propose and not command.requested:
        raise CommandRefused("propose_required", "an upload is the user's call: send upload_post with --propose (a card), or --requested when the user asked for it in the feed")
    if isinstance(command, ProposeShots) and not ctx.propose and not command.requested:
        raise CommandRefused("propose_required", "propose_shots is a card: send it with --propose, or --requested when the user asked for the batch in the feed")


INBOX_KINDS = ("user", "decision", "system", "blocker")


def check_inbox_gate(store: ProjectStore, project: Project, command: Command, ctx: CommandContext, env: Env | None = None) -> None:
    """An agent render must carry a current inbox cursor; unread items refuse it. A review
    session also waits: the next render of a kind the mode asks about is refused while a take of
    that kind still owes the user a verdict. An agent post is gated too: a note that landed
    while the agent wrote its reply is answered, not talked over -- the refusal hands it over."""
    if ctx.actor != "agent" or not isinstance(command, (Generate, ProposeShots, PostMessage, AnswerNotes)) or ctx.bypass_gates:
        return
    if isinstance(command, ProposeShots) and ctx.propose:
        return  # the proposal spends nothing; the approval replays it with the gates bypassed
    if isinstance(command, AnswerNotes) and ctx.inbox_cursor is None:
        return  # a caller with no cursor to offer (a harness script) is not held; the CLI always sends one
    if ctx.inbox_cursor is None:
        raise CommandRefused("inbox_cursor_required", f"{command.name} needs the agent's inbox cursor")
    if isinstance(command, (PostMessage, AnswerNotes)):
        # a note that landed since the cursor is answered, not talked over -- and never closed
        # unread: an answer sent after a refused post would otherwise close the note with no reply
        msgs, cursor = store.read_inbox(project, ctx.inbox_cursor)
        unread = [m for m in msgs if m.kind in ("user", "decision", "blocker")]
        if unread:
            verb = "wrote" if isinstance(command, PostMessage) else "worked"
            raise CommandRefused(
                "inbox_behind",
                f"{len(unread)} item(s) arrived while you {verb}; read them, then post and answer with all of them in view",
                {"messages": [m.model_dump(mode="json") for m in unread], "cursor": cursor},
            )
        return
    # the gate keeps the agent from pre-empting the user's answer; a render the user asked for
    # out loud (``requested``) is that answer
    if not getattr(command, "requested", False):
        _check_verdict_gate(store, project, command, env or Env())
    msgs, cursor = store.read_inbox(project, ctx.inbox_cursor)
    unread = [m for m in msgs if m.kind in INBOX_KINDS]
    if any(m.kind == "system" and m.change for m in unread):
        # the agent's own auto-mode lines ("agent: Video 1 - prompt edited") are its record, not
        # news it has to read; a system line about a user's change still is
        changes = store.read_changes(project)
        unread = [m for m in unread if not (m.kind == "system" and m.change and (c := changes.get(m.change)) is not None and c.actor == "agent")]
    # likewise the batch's own line ahead of its shots cards, which names no change
    unread = [m for m in unread if not (m.kind == "system" and not m.change and m.text.startswith("agent: "))]
    if unread:
        raise CommandRefused(
            "inbox_behind",
            f"{len(unread)} unread item(s) since cursor {ctx.inbox_cursor}",
            {"messages": [m.model_dump(mode="json") for m in unread], "cursor": cursor},
        )


# -- execute -----------------------------------------------------------------

# the renders each mode turns into a card; a mode not listed asks about none
MODE_ASKS: dict[str, tuple[Kind, ...]] = {"video_review": ("video",), "image_review": ("image", "video")}


def _check_verdict_gate(store: ProjectStore, project: Project, command: Command, env: Env) -> None:
    """A review session holds only what *depends* on a take still waiting for a verdict: a
    render whose bin binds that take as a reference (the clip made from a still the user
    has not approved). Everything else runs -- twelve stills can land as twelve cards at
    once, and a reroll in the same bin is the agent's call. Blocking every render of the
    reviewed kind behind the last card was the anti-pattern this replaces."""
    sess = project.agent_session
    asks = MODE_ASKS.get(sess.mode, ()) if sess is not None else ()
    if not asks:
        return
    gens = {g.id: g for g in store.read_generations(project)}
    # only a take that is in owes a verdict: a render waiting, running, cancelled or failed has
    # nothing to judge, the app shows no card for it, and so it never blocks anything (a card
    # nobody can see must not hold the agent)
    owed = {
        c.generation: c
        for c in store.read_changes(project).values()
        if c.session == sess.id and c.command == "generate" and c.generation and c.decision is not None and c.decision.status == "pending"
        and (g := gens.get(c.generation)) is not None and g.outputs
    }
    if not owed:
        return
    if isinstance(command, Generate):
        bin_ = project.bins_by_id.get(command.bin)
        wanted = [(bin_.name if bin_ else command.bin, command.bin, [r.media for r in bin_.references])] if bin_ is not None else []
    else:
        wanted = []
        for item in command.items:
            if item.dropped:
                continue
            refs = list(item.references)
            if item.bin is not None and not refs:
                src = project.bins_by_id.get(item.bin)
                refs = [r.media for r in src.references] if src is not None else []
            wanted.append((item.name or item.bin or "shot", item.bin, refs))
    for where, bin_id, refs in wanted:
        # the bin's own take on a card: another take now would answer the question for the user.
        # The agent's own fail closes the card first; otherwise the verdict comes first
        for gen_id, card in owed.items():
            gen = gens.get(gen_id)
            if gen is not None and gen.bin == bin_id:
                raise CommandRefused(
                    "verdict_owed",
                    f"{sess.mode}: {where} already has a take on a card waiting for the user's verdict (change {card.id}); "
                    "a new take would pre-empt their answer. If your own QC fails it, mark it fail and the card closes; otherwise watch for the decision",
                    {"change": card.id, "bin": bin_id},
                )
        for media_id in refs:
            media = project.media_by_id.get(media_id)
            gen = media.origin.generation if media is not None and media.origin is not None else None
            if gen and gen in owed:
                card = owed[gen]
                raise CommandRefused(
                    "verdict_owed",
                    f"{sess.mode}: {where} renders from {_take_label(project, media_id)}, which still waits for the user's verdict (change {card.id}); "
                    "render what does not depend on it, and watch for the decision before this one",
                    {"change": card.id, "media": media_id},
                )


# a session from before notes were tracked owes the last few hours of them, not its whole past
LEGACY_NOTES_WINDOW_S = 3 * 3600


def is_note(message: Message) -> bool:
    """Something the user said that is work for the agent: a message, or a reply typed on a card."""
    return message.kind == "user" or (message.kind == "decision" and message.status == "replied" and bool(message.note or message.files))


def _ts(value: str) -> float:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def open_notes(store: ProjectStore, project: Project) -> list[Message]:
    """The live session's notes the agent has not accounted for, oldest first. Reading a note
    moves the inbox cursor and nothing else: a round of ten notes read in one go and answered
    for two leaves eight here, and they stay here until ``answer_notes`` names them."""
    sess = project.agent_session
    if sess is None or sess.status != "live":
        return []
    messages = [m for m in store.read_messages(project) if m.session == sess.id]
    notes = [m for m in messages if is_note(m) and m.id not in sess.answered]
    if notes and not sess.notes_tracked:
        cutoff = _ts(messages[-1].ts) - LEGACY_NOTES_WINDOW_S
        notes = [m for m in notes if _ts(m.ts) >= cutoff]
    return notes


def message_file(store: ProjectStore, project: Project, file_id: str) -> MessageFile:
    """The record for a file uploaded to the chat, read from the file itself (``<id>_<name>``)."""
    path = store.chat_file(project, file_id)
    if path is None:
        raise CommandRefused("unknown_id", f"no message file {file_id}", {"type": "file", "id": file_id})
    return MessageFile(id=file_id, name=path.name[len(file_id) + 1 :], type=media_type_for(path) or "image")


def _post(store: ProjectStore, project: Project, kind: str, text: str = "", *, change: str | None = None, ts: str, **extra) -> Message:
    msg = Message(id=ids.new_id(ids.MESSAGE), ts=ts, session=_session_id(project), kind=kind, text=text, change=change, **extra)
    store.append_message(project, msg)
    return msg


def execute(
    store: ProjectStore,
    project: Project,
    command: Command,
    ctx: CommandContext,
    *,
    env: Env | None = None,
) -> CommandResult:
    env = env or Env()
    check_session_gate(project, command, ctx, env)
    check_inbox_gate(store, project, command, ctx, env)
    command.check(project, ctx, env)
    now = ctx.ts()

    if isinstance(command, PostMessage):
        context = None
        if command.context is not None:
            bin_ = _bin(project, command.context.bin) if command.context.bin else None
            seq = _sequence(project, command.context.sequence) if command.context.sequence else None
            media = _media(project, command.context.media) if command.context.media else None
            position = None
            if seq is not None and bin_ is not None:
                position = next((i + 1 for i, item in enumerate(seq.items) if item.bin == bin_.id), None)
            context = MessageContext(
                bin=bin_.id if bin_ is not None else None,
                bin_name=bin_.name if bin_ is not None else "",
                media=media.id if media is not None else None,
                media_label=command.context.media_label or (_media_label(bin_, media) if media is not None and bin_ is not None else ""),
                sequence=seq.id if seq is not None else None,
                sequence_name=seq.name if seq is not None else "",
                position=position,
            )
        if ctx.actor == "agent" and not command.files:
            # the same words again: a retry of a post that went through (a chained call whose
            # other half was refused reads as "nothing ran"). Said once; the answer says so
            said = next((m for m in reversed(store.read_messages(project)) if m.kind in ("agent", "blocker")), None)
            if said is not None and said.kind == command.kind and said.text == command.text.strip() and list(said.attachments) == list(command.attachments):
                return CommandResult(
                    project=project,
                    warnings=[{"kind": "already_posted", "detail": f"{said.id} at {said.ts} says exactly this; not posted again"}],
                )
        files = [message_file(store, project, f) for f in dict.fromkeys(command.files)]
        msg = _post(store, project, command.kind, command.text.strip(), ts=now, attachments=list(command.attachments), files=files, context=context)
        return CommandResult(project=project, messages=[msg])

    if isinstance(command, AnswerNotes):
        sess = project.agent_session
        known = {m.id for m in store.read_messages(project) if is_note(m) and m.session == sess.id}
        for note in command.notes:
            if note not in known:
                raise CommandRefused("unknown_id", f"no user note {note} in this session", {"type": "message", "id": note})
        for note in dict.fromkeys(command.notes):
            sess.answered[note] = command.did.strip()
        store.save(project)
        return CommandResult(project=project)

    if isinstance(command, WithdrawChange):
        return _withdraw(store, project, command, ctx, now=now)

    if ctx.propose and ctx.actor != "agent":
        raise CommandRefused("wrong_actor", "only the agent proposes")
    if isinstance(command, ProposeShots) and len(command.items) > 1:
        return _run_shots(store, project, command, ctx, env, now=now)
    if ctx.propose:
        return _propose(store, project, command, ctx, now=now)
    if isinstance(command, ProposeShots):
        return _apply_batch(store, project, command, ctx, env, now=now)
    res = _apply_and_log(store, project, command, ctx, env, now=now)
    if isinstance(command, MarkTake) and ctx.actor == "agent" and command.status == "fail":
        res.changes.extend(_close_failed_take_cards(store, project, command.media, now))
    return res


def _withdraw(store: ProjectStore, project: Project, command: WithdrawChange, ctx: CommandContext, *, now: str) -> CommandResult:
    """Close one of the live session's own waiting cards, with the agent's reason on it. The
    feed gets one system line so the user sees the card go; a shots batch's other cards stay."""
    sess = project.agent_session
    change = store.read_changes(project).get(command.change)
    if change is None:
        raise CommandRefused("unknown_id", f"no change {command.change}", {"type": "change", "id": command.change})
    if change.decision is None or change.actor != "agent":
        raise CommandRefused("not_agent_change", "only the agent's own cards can be withdrawn")
    if sess is None or change.session != sess.id:
        raise CommandRefused("wrong_session", "that card belongs to another session; it is not yours to withdraw")
    if change.decision.status not in ("proposed", "pending"):
        raise CommandRefused("bad_transition", f"cannot withdraw a {change.decision.status} card: the user has answered it")
    why = (command.why or "").strip()
    closed = change.model_copy()
    closed.decision = Decision(status="closed", note=f"withdrawn by the agent{': ' + why if why else ''}", ts=now)
    store.append_change(project, closed)
    what = change.description.removeprefix("proposed: ")
    msg = _post(store, project, "system", f"agent: withdrew the card \"{what}\"{' - ' + why if why else ''}", change=closed.id, ts=now)
    return CommandResult(project=project, change=closed, messages=[msg])


def _open_take_card(store: ProjectStore, project: Project, media_id: str) -> Change | None:
    """The card still waiting on a verdict for this take: the render that made it, or an
    earlier mark on it."""
    media = project.media_by_id.get(media_id)
    gen = media.origin.generation if media is not None and media.origin is not None else None
    for change in store.read_changes(project).values():
        if change.decision is None or change.decision.status not in ("pending", "proposed"):
            continue
        if gen and change.command == "generate" and change.generation == gen:
            return change
        if change.command == "mark_take" and change.target.id == media_id:
            return change
    return None


def _close_failed_take_cards(store: ProjectStore, project: Project, media_id: str, now: str) -> list[Change]:
    """A take the agent itself marked fail owes the user no verdict: the card that was waiting
    for one ("is this image done?") would be asking them to approve a failure. Its pending
    render card closes, quietly, and carries the agent's reason; the mark and note stay on the take."""
    media = project.media_by_id.get(media_id)
    why = (media.qc.note or "").strip() if media is not None and media.qc is not None else ""
    gen = media.origin.generation if media is not None and media.origin is not None else None
    if not gen:
        return []
    closed: list[Change] = []
    for change in store.read_changes(project).values():
        if change.command == "generate" and change.generation == gen and change.decision is not None and change.decision.status == "pending":
            change = change.model_copy()
            change.decision = Decision(status="closed", note=f"the agent failed this take: {why}" if why else "the agent marked this take fail", ts=now)
            store.append_change(project, change)
            closed.append(change)
    return closed


def _run_shots(store: ProjectStore, project: Project, command: ProposeShots, ctx: CommandContext, env: Env, *, now: str) -> CommandResult:
    """A batch of shots is one card per shot, each approved, edited or closed on its own; the
    batch's title and reason go out once, as the agent's line ahead of the cards. The result's
    ``change`` is the first card and ``changes`` every card (and, for a requested batch, every
    render each card queued)."""
    intro = _post(store, project, "system", f"agent: {command.intro()}", ts=now)
    res = CommandResult(project=project, messages=[intro])
    for item in command.items:
        single = command.model_copy(update={"items": [item], "reason": None})
        if ctx.propose:
            one = _propose(store, project, single, ctx, now=now)
        else:
            one = _apply_batch(store, project, single, ctx, env, now=now)
        if one.change is not None:
            if res.change is None:
                res.change = one.change
            res.changes.append(one.change)
        res.changes.extend(one.changes)
        res.generations.extend(one.all_generations())
        res.messages.extend(one.messages)
        res.warnings.extend(one.warnings)
    return res


def _propose(store: ProjectStore, project: Project, command: Command, ctx: CommandContext, *, now: str) -> CommandResult:
    """The agent's card: the command as a payload, decided later."""
    target = command.target(project)
    messages: list[Message] = []
    if isinstance(command, UploadPost):
        # one upload card per post: a second one while the first waits is where two uploads of
        # the same video come from. Edits to the post flow into the waiting card (it uploads the
        # post as it stands when approved); a replied or closed card is gone and a new one may go
        waiting = next((c for c in store.read_changes(project).values() if c.command == UploadPost.name and c.decision is not None and c.decision.status in ("proposed", "pending") and (c.payload or {}).get("post") == command.post), None)
        if waiting is not None:
            raise CommandRefused(
                "card_exists",
                f"an upload card for {post_label(project, _post_record(project, command.post))} is already waiting ({waiting.id}); the user answers that one. Edits to the post go into it: it uploads the post as it stands when approved",
                {"change": waiting.id},
            )
    # every proposal is its own card, a revised brief included: a plan session may put several
    # on the table and the user approves or closes each
    change = Change(
        id=ids.new_id(ids.CHANGE),
        ts=now,
        actor="agent",
        session=_session_id(project),
        command=command.name,
        target=target,
        description=f"proposed: {command.describe(project, {}, {})}",
        reason=command.reason,
        decision=Decision(status="proposed", ts=now),
        payload=command.model_dump(mode="json"),
    )
    store.append_change(project, change)
    messages.append(_post(store, project, "change", change.description, change=change.id, ts=now))
    return CommandResult(project=project, change=change, messages=messages)


def _apply_batch(
    store: ProjectStore,
    project: Project,
    command: ProposeShots,
    ctx: CommandContext,
    env: Env,
    *,
    now: str,
    change_id: str | None = None,
    decision: Decision | None = None,
    announce: bool = True,
) -> CommandResult:
    """The batch lands as one change (the bins, recipes and bindings, with the items and their
    outcome in the payload), then one generate change per rendered item, tagged with the
    batch's id so the feed folds them under its card and the mode still decides whether each
    landed take is a card of its own."""
    parent_id = change_id or ids.new_id(ids.CHANGE)
    for item in command.items:
        item.change = ids.new_id(ids.CHANGE) if item.renders() else None
        item.error = None
    res = _apply_and_log(store, project, command, ctx, env, now=now, change_id=parent_id, decision=decision, announce=announce, payload=True, keep_empty=True)
    failed = False
    for item, gen_cmd in getattr(command, "_renders", []):
        try:
            gen_cmd.check(project, ctx, env)
            child = _apply_and_log(store, project, gen_cmd, ctx, env, now=now, change_id=item.change, batch=parent_id)
        except CommandRefused as exc:
            item.error = f"{exc.code}: {exc.detail}" if exc.detail else exc.code
            item.change = None
            failed = True
            continue
        if child.change is not None:
            res.changes.append(child.change)
        if child.generation is not None:
            res.generations.append(child.generation)
        res.messages.extend(child.messages)
        res.warnings.extend(child.warnings)
    if failed and res.change is not None:
        # the outcome changed after the parent line was written: rewrite it, once
        res.change.payload = command.model_dump(mode="json")
        store.append_change(project, res.change)
    return res


def _touch_bins(project: Project, target: ChangeTarget, before: dict, after: dict, now: str) -> None:
    """Stamp ``updated`` on every bin a command changed, after the diff so the stamp never shows
    up as a change of its own: the bin itself, the bins a project-level change rewrote, or the
    bins holding a media record that was marked or edited."""
    if target.type == "bin":
        touched = {target.id}
    elif target.type == "project":
        touched = set(before.get("bins_by_id") or {}) | set(after.get("bins_by_id") or {})
    elif target.type == "media":
        touched = {b.id for b in project.bins_by_id.values() if target.id in b.media}
    else:
        touched = set()
    for bin_id in touched:
        bin_ = project.bins_by_id.get(bin_id)
        if bin_ is not None:
            bin_.updated = now


def _apply_and_log(
    store: ProjectStore,
    project: Project,
    command: Command,
    ctx: CommandContext,
    env: Env,
    *,
    now: str,
    change_id: str | None = None,
    decision: Decision | None = None,
    announce: bool = True,
    payload: bool = False,
    keep_empty: bool = False,
    batch: str | None = None,
) -> CommandResult:
    target = command.target(project)
    record = _record(project, target)
    before_dump = _dump(record)
    applied = command.apply(project, ctx, env)
    target = applied.target
    record = _record(project, target)
    before, after = _diff(before_dump, _dump(record))

    # agent changes await the user's verdict, except the session lifecycle itself: connecting
    # or ending is not something to approve, and it must not count as an owed decision.
    # A command the user asked for in the feed is not owed one either -- asking the user to
    # approve what they just requested is noise, and they said so.
    # A command that changed nothing is not an event. Renaming "alex" to "alex" produced a real
    # card and a verdict to give, which is noise in a feed the user has to read. An empty diff
    # with no generation means the apply was a no-op: commands with effects outside the record
    # (post_message) never reach this function. A batch is the exception (``keep_empty``): its
    # record is the items, and rendering into bins that already exist changes nothing else.
    if not before and not after and applied.generation is None and not keep_empty:
        return CommandResult(project=project, change=None, warnings=[
            {"kind": "no_change", "detail": f"{command.name} changed nothing"}
        ])
    _touch_bins(project, target, before, after, now)

    # one card per take: a mark asked to be a card on a take whose render card is still waiting
    # lands quietly -- that card is where the verdict is given (a second one asks it twice)
    card_warnings: list[dict] = []
    if isinstance(command, MarkTake) and command.card and ctx.actor == "agent":
        waiting = _open_take_card(store, project, command.media)
        if waiting is not None:
            command = command.model_copy(update={"card": False, "quiet": True})
            card_warnings.append({"kind": "card_exists", "detail": f"this take's card is already waiting for the user ({waiting.description}); no second card"})

    # The session's mode decides the rest: an auto session lands the agent's changes done with
    # a feed line and never a card (the gallery is the monitor, the summary post is the finish)
    # -- except the one the agent asks for with ``card``, the finished candidate; a review
    # session does the same for everything but the renders it asks about, which owe a verdict
    # -- the user wanted bins, bindings, prompts and marks just made and only the spend to
    # approve. A batch is always a card: it exists to be read. A plan session never reaches
    # here except through an approval.
    sess = project.agent_session
    mode = sess.mode if sess is not None else "image_review"
    silent = (
        ctx.actor == "agent"
        and decision is None
        and not command.card
        and not isinstance(command, ProposeShots)
        and command.renders(project, env) not in MODE_ASKS.get(mode, ())
    )
    # a reviewed render is the one thing ``requested`` does not wave through: the card is the
    # review of the take after it lands, not an approval of the spend the user already gave
    reviewed = ctx.actor == "agent" and command.renders(project, env) in MODE_ASKS.get(mode, ())
    # ``quiet`` wins over ``card``: a quiet change lands with no line and owes no verdict. The
    # other reading (a pending decision with no card to answer it from) left the bar counting
    # cards the feed could not show.
    if (
        decision is None
        and ctx.actor == "agent"
        and (not command.requested or reviewed)
        and not silent
        and not command.quiet
        and not isinstance(command, (ConnectSession, EndSession, SetSessionMode))
    ):
        decision = Decision(status="pending")
    change = Change(
        id=change_id or ids.new_id(ids.CHANGE),
        ts=now,
        actor=ctx.actor,
        session=ctx.session or _session_id(project),
        command=command.name,
        target=target,
        before=before,
        after=after,
        description=command.describe(project, before, after),
        reason=command.reason,
        decision=decision,
        generation=applied.generation.id if applied.generation else None,
        payload=command.model_dump(mode="json") if payload else None,
        batch=batch,
    )
    if applied.generation is not None:
        store.append_generation(project, applied.generation)
    store.append_change(project, change)
    store.save(project)

    messages: list[Message] = []
    live = project.agent_session is not None and project.agent_session.status == "live"
    if isinstance(command, StartSession):
        notes = str(command.notes or "").strip()
        if notes:
            # the kickoff notes are the user's message; the refs bin's media ride along as
            # attachments so the agent's inbox carries them
            refs = project.bins_by_id.get(command.refs_bin) if command.refs_bin else None
            attachments = list(refs.media) if refs is not None else []
            messages.append(_post(store, project, "user", notes, ts=now, attachments=attachments))
            n = len(attachments)
            sent = f"notes sent with {n} reference file{'s' if n != 1 else ''}, waiting for an agent to connect" if n else "notes sent, waiting for an agent to connect"
        else:
            sent = "session started, waiting for an agent to connect"
        messages.append(_post(store, project, "system", sent, change=change.id, ts=now))
    elif isinstance(command, SetSessionMode):
        # the mode-change note says what the mode now means, not just its name
        meaning = []
        if command.mode is not None:
            meaning.append(MODE_MEANINGS.get(command.mode, ""))
        if command.drafts is not None:
            meaning.append(DRAFT_MEANING if command.drafts else "generations run at the quality each step needs, with no draft step first")
        text = change.description + "".join(f" -- {m}" for m in meaning if m)
        messages.append(_post(store, project, "system", text, change=change.id, ts=now))
    elif isinstance(command, (ConnectSession, EndSession)):
        if not (isinstance(command, ConnectSession) and command.rejoin):
            messages.append(_post(store, project, "system", change.description, change=change.id, ts=now))
    elif announce and ctx.actor == "agent" and not command.quiet and (decision is not None or isinstance(command, ProposeShots)):
        # a card: something that owes a verdict, or a shot of a batch (a requested batch's shots
        # land done but are still cards, they exist to be read)
        messages.append(_post(store, project, "change", change.description, change=change.id, ts=now))
    elif announce and ctx.actor == "agent" and not command.quiet:
        # no verdict owed (auto mode, or a render the user asked for out loud in a review mode),
        # but visibility is: the experiment shows in the feed as a line with the agent's reason,
        # the way the user's own edits show as "you: ...", never as a card
        why = (command.reason or "").strip()
        messages.append(_post(store, project, "system", f"agent: {change.description}" + (f" -- {why}" if why else ""), change=change.id, ts=now))
    elif announce and ctx.actor == "user" and live:
        messages.append(_post(store, project, "system", f"you: {change.description}", change=change.id, ts=now))

    warnings = [w.to_dict() for w in (applied.assembled.warnings if applied.assembled else applied.warnings)] + list(getattr(command, "_dropped", []) or []) + card_warnings
    made = getattr(command, "_bin", None) or getattr(command, "_created", None)
    if isinstance(command, (CreateSequence, CloneSequence)):
        # the id made is a sequence's, and the caller goes there, not to a bin
        return CommandResult(project=project, change=change, generation=applied.generation, warnings=warnings, messages=messages, sequence=made)
    if isinstance(command, (CreatePost, UploadPost)):
        return CommandResult(project=project, change=change, generation=applied.generation, warnings=warnings, messages=messages, post=made or getattr(command, "post", None))
    return CommandResult(project=project, change=change, generation=applied.generation, warnings=warnings, messages=messages, bin=made)


# -- decisions ---------------------------------------------------------------

DecisionAction = Literal["approve", "reject", "reopen", "close", "reply"]


def _is_take_change(change: Change) -> str | None:
    """The media id this change is about when it is a take decision, else None."""
    if change.target.type == "media":
        return change.target.id
    if change.command == "set_bin_field" and "selected" in change.after:
        return change.after.get("selected") or None
    # a schema-3 card still owed a verdict
    if change.command == "set_shot_field" and "selected_take" in change.after:
        return change.after.get("selected_take") or None
    return None


def _lineage(store: ProjectStore, project: Project, ids) -> set[str]:
    """Every media these were made from: the references their generations used, and what an
    upload says it came from, followed back as far as the records go."""
    gens = {g.id: g for g in store.read_generations(project)}
    seen: set[str] = set()
    todo = list(ids)
    while todo:
        media = project.media_by_id.get(todo.pop())
        if media is None or media.origin is None:
            continue
        parents = [media.origin.source] if media.origin.source else []
        gen = gens.get(media.origin.generation) if media.origin.generation else None
        parents += [r.media for r in gen.references] if gen else []
        for parent in parents:
            if parent and parent not in seen:
                seen.add(parent)
                todo.append(parent)
    return seen


def _drop_unseen(store: ProjectStore, project: Project, gone: set[str]) -> list[str]:
    cleared: list[str] = []
    for bin_ in project.bins_by_id.values():
        if any(m in gone for m in bin_.unseen):
            cleared += [m for m in bin_.unseen if m in gone]
            bin_.unseen = [m for m in bin_.unseen if m not in gone]
    if cleared:
        store.save(project)
    return cleared


def clear_unseen_like(store: ProjectStore, project: Project, media_id: str) -> list[str]:
    """The user gave a verdict on this media in chat: its bin stops marking takes of the same type as new,
    and so does everything it was made from (the character frame behind the keyframe behind
    the video). Attention was given in the conversation; the takes stay, only the flags go."""
    media = project.media_by_id.get(media_id)
    bin_ = _bin_for(project, media) if media else None
    if bin_ is None:
        return []
    gone = {m for m in bin_.unseen if (o := project.media_by_id.get(m)) is not None and o.type == media.type}
    gone.add(media_id)
    return _drop_unseen(store, project, gone | _lineage(store, project, gone))


def clear_unseen_for(store: ProjectStore, project: Project, media_ids: list[str]) -> list[str]:
    """The agent says these media were seen (shown in an assembly the user was given): their
    flags go, and so do those of everything they were made from."""
    for m in media_ids:
        if m not in project.media_by_id:
            raise CommandRefused("unknown_id", f"no media {m}", {"type": "media", "id": m})
    gone = set(media_ids)
    return _drop_unseen(store, project, gone | _lineage(store, project, gone))


def clear_unseen_all(store: ProjectStore, project: Project) -> list[str]:
    """Every new marker in the project goes."""
    return _drop_unseen(store, project, {m for b in project.bins_by_id.values() for m in b.unseen})


def close_render_card(store: ProjectStore, project: Project, gen_id: str, why: str, now: str | None = None) -> Change | None:
    """A render that will never have a take (cancelled, or discarded before it ran) settles its
    card: nothing is left to judge. Returns the closed change, or None when no card was owed."""
    for change in store.read_changes(project).values():
        if change.generation == gen_id and change.command == "generate" and change.decision is not None and change.decision.status in ("pending", "proposed"):
            closed = change.model_copy()
            closed.decision = Decision(status="closed", note=why, ts=now or utc_now())
            store.append_change(project, closed)
            return closed
    return None


def _approved_media(store: ProjectStore, project: Project, change: Change) -> str | None:
    """The media a decided card showed: the take it selects, else what its render produced."""
    take = _is_take_change(change)
    if take:
        return take
    if change.generation:
        for g in store.read_generations(project):
            if g.id == change.generation and g.outputs:
                return g.outputs[-1]
    return None


def _select_approved(store: ProjectStore, project: Project, change: Change, ctx: CommandContext, env: Env, now: str) -> CommandResult | None:
    """Make the take an approved card showed its bin's current one, as the user's own pick:
    the bin it was made in, else the one holding it. None when it already is, or there is none."""
    take = _approved_media(store, project, change)
    media = project.media_by_id.get(take) if take else None
    if media is None:
        return None
    bin_ = _bin_for(project, media)
    if bin_ is None or media.id not in bin_.media or bin_.selected == media.id:
        return None
    command = SetBinField(bin=bin_.id, field="selected", value=media.id)
    command.check(project, ctx, env)
    return _apply_and_log(store, project, command, ctx, env, now=now)


def _unstamped(entry):
    """A bin entry without its ``updated`` stamp: stamped after the diff, so it is never part of
    what a change moved and never a sign that a later one did."""
    return {k: v for k, v in entry.items() if k != "updated"} if isinstance(entry, dict) else entry


def _revert(project: Project, change: Change) -> bool:
    """Assign ``before`` for every field whose current value still equals ``after``.

    Returns True when every field reverted, False when a newer change moved one.
    """
    record = _record(project, change.target)
    current = _dump(record)
    patch: dict = {}
    skipped = False
    for key, value in change.after.items():
        before_v = change.before.get(key)
        cur = current.get(key)
        if isinstance(value, dict) and isinstance(before_v, dict) and isinstance(cur, dict):
            # entry by entry: one a later change moved stays as it is, the rest go back
            merged = dict(cur)
            for k in set(value) | set(before_v):
                if _unstamped(cur.get(k)) != _unstamped(value.get(k)):
                    skipped = True
                    continue
                if k in before_v:
                    merged[k] = copy.deepcopy(before_v[k])
                else:
                    merged.pop(k, None)
            patch[key] = merged
        elif cur == value:
            patch[key] = before_v
        else:
            skipped = True
    if patch:
        apply_patch(record, patch)
    return not skipped


def decide(
    store: ProjectStore,
    project: Project,
    change_id: str,
    action: DecisionAction,
    note: str | None,
    ctx: CommandContext,
    *,
    env: Env | None = None,
    items: list[dict] | None = None,
    hold: bool = False,
    files: list[str] | None = None,
) -> CommandResult:
    """The user's verdict on an agent change. ``items`` is the per-item verdict on a proposed
    propose_shots card (index, enabled, description); an item left alone is kept as it was.
    ``hold`` approves a shots card for later: everything is set up, the renders are saved held.
    ``files`` are chat files put on the reply; like a note they make it a reply, and a reply of
    files alone is a reply."""
    env = env or Env()
    if ctx.actor != "user":
        raise CommandRefused("wrong_actor", "only the user decides")
    change = store.read_changes(project).get(change_id)
    if change is None:
        raise CommandRefused("unknown_id", f"no change {change_id}", {"type": "change", "id": change_id})
    change = change.model_copy()  # the store's record is shared; the verdict lands on our own copy
    if change.decision is None:
        raise CommandRefused("not_agent_change", "only agent changes carry a decision")
    status = change.decision.status
    now = ctx.ts()
    note = (note or "").strip() or None
    messages: list[Message] = []
    result_change = change
    generation: Generation | None = None  # an approved generate's render; the caller dispatches it
    children: list[Change] = []
    generations: list[Generation] = []  # an approved batch's renders, likewise

    def _bad():
        raise CommandRefused("bad_transition", f"cannot {action} a {status} change")

    # a note is a reply, never an approval: the words are the instruction, and the agent acts
    # on them; the card ran or was undone only when the user says so in a bare click
    sent = [message_file(store, project, f) for f in dict.fromkeys(files or [])] if action not in ("close", "reopen") else []
    if action == "approve" and (note or sent):
        action = "reply"
    if action == "reply" and not (note or sent):
        raise CommandRefused("bad_value", "a reply needs a note or a file")

    command: Command | None = None
    if action == "approve" and status in ("proposed", "replied") and change.payload is not None and not change.after and not _batch_applied(change):
        status = "proposed"  # a replied proposal never ran; the bare approve runs it now
        command = parse_command(change.command, change.payload or {})
        if isinstance(command, ProposeShots):
            command.hold = bool(hold)
        if isinstance(command, ProposeShots) and items is not None:
            command.merge_decision(items)
            if not any(not i.dropped for i in command.items):
                action = "close"  # nothing kept is not an approval of an empty batch

    if action == "approve":
        if status == "proposed":
            assert command is not None
            agent_ctx = CommandContext(actor="agent", session=change.session, now=now, bypass_gates=True)
            command.check(project, agent_ctx, env)
            run = _apply_batch if isinstance(command, ProposeShots) else _apply_and_log
            res = run(
                store, project, command, agent_ctx, env, now=now,
                change_id=change.id, decision=Decision(status="approved", note=note, ts=now), announce=False,
            )
            result_change = res.change
            generation = res.generation
            children = res.changes
            generations = res.generations
            messages.extend(res.messages)
            # a plan session stays a plan session: the brief is written, the user picks the
            # mode in the panel when the planning is done
        elif status in ("pending", "approved", "replied"):
            change.decision = Decision(status="approved", note=note, ts=now)
            store.append_change(project, change)
            # the approved take is the bin's current one from this moment, not when the agent
            # gets round to selecting it
            picked = _select_approved(store, project, change, ctx, env, now)
            if picked is not None:
                children.append(picked.change)
                messages.extend(picked.messages)
        else:
            _bad()

    elif action == "reply":
        # nothing applied, nothing reverted, nothing owed: the agent reads the note and acts
        if status in ("proposed", "pending", "approved", "replied"):
            change.decision = Decision(status="replied", note=note, files=sent, ts=now)
            store.append_change(project, change)
        else:
            _bad()

    elif action == "reject":
        if status == "proposed":
            change.decision = Decision(status="rejected", note=note, ts=now)
            store.append_change(project, change)
        elif change.command == ProposeShots.name and status in ("approved", "replied") and _batch_applied(change):
            # the bins stay: renders may be in flight in them, and their takes are the record
            change.decision = Decision(status="rejected", note=note, ts=now)
            store.append_change(project, change)
        elif status in ("pending", "approved", "replied"):
            take = _is_take_change(change)
            if take:
                mark = MarkTake(media=take, status="fail", note=note)
                mark.check(project, ctx, env)
                res = _apply_and_log(store, project, mark, ctx, env, now=now, announce=False)
                messages.extend(res.messages)
                change.decision = Decision(status="rejected", note=note, ts=now)
            else:
                fully = _revert(project, change)
                change.decision = Decision(status="rejected" if fully else "superseded", note=note, ts=now)
                _touch_bins(project, change.target, change.before, change.after, now)
                store.save(project)
            store.append_change(project, change)
        else:
            _bad()

    elif action == "close":
        # seen, nothing owed, nothing said: what was applied stays, what was proposed never runs
        if status in ("proposed", "pending", "replied"):
            change.decision = Decision(status="closed", ts=now)
            store.append_change(project, change)
        else:
            _bad()

    elif action == "reopen":
        if status in ("rejected", "superseded", "closed"):
            batch = change.command == ProposeShots.name
            never_applied = not _batch_applied(change) if batch else (change.payload is not None and not change.after)
            if never_applied:
                change.decision = Decision(status="proposed", note=note, ts=now)
            else:
                # a batch's bins carry takes by now; its approval-time snapshot would erase them
                if not batch and not _is_take_change(change) and change.after:
                    apply_delta(_record(project, change.target), change.before, change.after)
                    _touch_bins(project, change.target, change.before, change.after, now)
                    store.save(project)
                change.decision = Decision(status="pending", note=note, ts=now)
            store.append_change(project, change)
        else:
            _bad()
    else:  # pragma: no cover - Literal
        raise CommandRefused("bad_value", f"unknown action {action}")

    # any verdict on the card means the user looked at what it showed: approve, reject or a reply.
    # Close is not a verdict (nothing was judged), and reopen takes one back.
    if action in ("approve", "reject", "reply"):
        seen_media = _approved_media(store, project, change)
        if seen_media:
            clear_unseen_like(store, project, seen_media)

    final = result_change.decision.status if result_change.decision else None
    messages.append(_post(store, project, "decision", ts=now, change=result_change.id, status=final, note=note if action != "close" else None, files=sent))
    return CommandResult(project=project, change=result_change, generation=generation, messages=messages, changes=children, generations=generations)


def _batch_applied(change: Change) -> bool:
    """A propose_shots change that ran: its items carry the bins they made."""
    return any(i.get("bin_id") for i in ((change.payload or {}).get("items") or []) if isinstance(i, dict))


def close_pending(store: ProjectStore, project: Project, ctx: CommandContext) -> CommandResult:
    """Every card the waiting or live session still owes, closed at once: seen, nothing said.
    One feed line for the lot, so the agent's inbox reads one line too."""
    if ctx.actor != "user":
        raise CommandRefused("wrong_actor", "only the user decides")
    sess = project.agent_session
    now = ctx.ts()
    closed: list[Change] = []
    if sess is not None and sess.status in ("waiting", "live"):
        for change in store.read_changes(project).values():
            if change.session == sess.id and change.decision is not None and change.decision.status in ("pending", "proposed"):
                change = change.model_copy()
                change.decision = Decision(status="closed", ts=now)
                store.append_change(project, change)
                closed.append(change)
    messages: list[Message] = []
    if closed:
        n = len(closed)
        messages.append(_post(store, project, "system", f"you: closed {n} card{'s' if n != 1 else ''}", ts=now))
    return CommandResult(project=project, messages=messages, changes=closed)
