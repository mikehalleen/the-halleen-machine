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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from . import ids
from .assembler import AssembledPrompt, AssemblyWarning, Capacity, assemble, workflow_capacity
from .ids import utc_now
from .loras import lift_lora_tags
from .models import (
    Actor,
    AgentSession,
    Bin,
    Change,
    ChangeTarget,
    Decision,
    Generation,
    Kind,
    Lora,
    Media,
    Message,
    MessageContext,
    Project,
    QcMark,
    QcStatus,
    Reference,
    SessionMode,
)
from .resolver import resolve_references
from .slug import assign_handle, handles_in, rename_bin_handles, renumber_handles
from .store import ProjectStore
from .workflow_names import WORKFLOWS_DIR


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

    def ts(self) -> str:
        return self.now or utc_now()


@dataclass
class Env:
    """Where to find workflows and how to check files. Injectable for tests."""

    workflows_dir: Path = WORKFLOWS_DIR
    isfile: Callable[[str], bool] = os.path.isfile
    _capacity: dict[str, Capacity] = field(default_factory=dict)

    def capacity(self, workflow: str) -> Capacity:
        if workflow not in self._capacity:
            self._capacity[workflow] = workflow_capacity(workflow, self.workflows_dir)
        return self._capacity[workflow]

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
    """The handle scan ``generate`` runs, at prompt-write time: a ``.token`` in the prompt or
    negative that no reference answers to, and (on workflows that address by token) a bound
    handle the prompt never mentions. The same words the render would warn with, earlier."""
    used = handles_in(bin_.prompt) | handles_in(bin_.negative)
    known = {r.handle for r in bin_.references}
    out = [AssemblyWarning("unknown_token", h, f".{h} matches no reference in this bin") for h in sorted(used - known)]
    if bin_.workflow and env.capacity(bin_.workflow).addresses_by_token:
        out += [AssemblyWarning("unused_handle", h, f".{h} appears nowhere in the prompt") for h in sorted(known - used)]
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

    def all_generations(self) -> list[Generation]:
        return [g for g in (self.generation, *self.generations) if g is not None]


# -- record access -----------------------------------------------------------


def _bin(project: Project, bin_id: str) -> Bin:
    bin_ = project.bins_by_id.get(bin_id)
    if bin_ is None:
        raise CommandRefused("unknown_id", f"no bin {bin_id}", {"type": "bin", "id": bin_id})
    return bin_


def _media_label(bin_: Bin, media: Media) -> str:
    """`take 3` for a take generated in this bin; `#2` (its place in the gallery) otherwise --
    the same words the bin screen shows under the tile."""
    if media.take and media.origin is not None and media.origin.type == "generation" and media.origin.bin == bin_.id:
        return f"take {media.take}"
    ids = list(bin_.media or [])
    return f"#{ids.index(media.id) + 1}" if media.id in ids else media.id


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
    if bin_ and media.origin.type == "generation" and media.origin.bin == bin_.id and media_id in bin_.media:
        return bin_, media.take or bin_.media.index(media_id) + 1
    return bin_, None


def _take_label(project: Project, media_id: str) -> str:
    bin_, n = _take_number(project, media_id)
    if bin_ and n:
        return f"{bin_.name} take {n}"
    return f"{bin_.name} media" if bin_ else media_id


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

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def _workflow(self, project: Project) -> str:
        return str(self.workflow if self.workflow is not None else project.defaults.image_workflow or "").strip()

    def check(self, project, ctx, env) -> None:
        wf = self._workflow(project)
        if wf and not env.workflow_exists(wf):
            raise CommandRefused("unknown_workflow", f"no workflow {wf}")
        if self.bin_name is not None and not str(self.bin_name).strip():
            raise CommandRefused("bad_value", "bin name is empty")
        if self.length_s is not None and self.length_s <= 0:
            raise CommandRefused("bad_value", "length_s must be positive")
        _clean_tags(self.tags)

    def apply(self, project, ctx, env) -> Applied:
        wf = self._workflow(project)
        length = None
        if wf and env.kind(wf) == "video":
            length = self.length_s if self.length_s is not None else project.defaults.video_length_s
        name = str(self.bin_name).strip() if self.bin_name is not None else next_bin_name(project)
        bin_ = Bin(id=ids.new_id(ids.BIN), name=name, workflow=wf, length_s=length, tags=_clean_tags(self.tags), created=ctx.ts())
        project.bins_by_id[bin_.id] = bin_
        self._created = bin_.id
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(getattr(self, "_created", ""), None)
        return f"added {bin_.name}" if bin_ else "added a bin"


class DuplicateBin(Command):
    """A copy of a bin's recipe, and of its gallery when asked. References are cloned with fresh
    ids and the same handles, still pointing at the source's media, so the copy renders the same
    thing until you change it."""

    name: ClassVar[str] = "duplicate_bin"
    bin: str
    with_media: bool = False

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        _bin(project, self.bin)

    def apply(self, project, ctx, env) -> Applied:
        src = _bin(project, self.bin)
        copy_ = Bin(
            id=ids.new_id(ids.BIN),
            name=_unique_name(project, f"{src.name}-copy"),
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


SettableField = Literal["name", "workflow", "length_s", "prompt", "negative", "loras", "selected", "description", "tags"]


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
        # a rename rewrites handles and prompts in every bin referencing this one, so the
        # change is the project's: the whole rewrite sits in one diff and a reject undoes it all
        if self.field == "name":
            return ChangeTarget(type="project", id=project.id)
        return ChangeTarget(type="bin", id=self.bin)

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        f, v = self.field, self.value
        if f == "workflow":
            if not isinstance(v, str) or not env.workflow_exists(v):
                raise CommandRefused("unknown_workflow", f"no workflow {v}")
        elif f == "length_s":
            if env.kind(bin_.workflow) != "video":
                raise CommandRefused("wrong_kind", "length_s applies to a video workflow only")
            try:
                if v is None or float(v) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise CommandRefused("bad_value", "length_s must be a positive number") from None
        elif f in ("prompt", "negative", "description"):
            if not isinstance(v, str):
                raise CommandRefused("bad_value", f"{f} must be a string")
        elif f == "name":
            if not isinstance(v, str) or not v.strip():
                raise CommandRefused("bad_value", "bin name is empty")
        elif f == "loras":
            _validate_loras(v)
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
        if self.field == "name":
            self._old = bin_.name
            self._touched = rename_bin_handles(project, self.bin, str(value).strip())
            return Applied(target=self.target(project))
        if self.field == "length_s":
            value = float(value)
        if self.field == "loras":
            value = _validate_loras(value)
        if self.field == "tags":
            value = _clean_tags(value)
        if self.field == "prompt":
            # a tag typed into the prompt is a lora entry, not prompt text
            value, loras = lift_lora_tags(value, bin_.loras)
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
            return f"{name} - selected {f'take {n}' if n else (after.get(f) or 'none')}"
        if f in ("prompt", "negative", "description"):
            return f"{name} - {f} edited"
        if f == "name":
            n = getattr(self, "_touched", 0)
            tail = f" ({n} bin{'s reference' if n != 1 else ' references'} it)" if n else ""
            return f"{getattr(self, '_old', '?')} renamed to {name}{tail}"
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
        cap = env.capacity(bin_.workflow)
        if not cap.found:
            raise CommandRefused("unknown_workflow", f"{bin_.name} has no usable workflow ({bin_.workflow or 'none'})")
        limit = cap.slots_for(media.type)
        if limit <= 0:
            raise CommandRefused("media_type_unsupported", f"{bin_.workflow} takes no {media.type} references")
        same = sum(1 for r in bin_.references if project.media_by_id.get(r.media, None) and project.media_by_id[r.media].type == media.type)
        if same + 1 > limit:
            raise CommandRefused("too_many_references", f"{bin_.workflow} takes at most {limit} {media.type} reference(s)")
        if self.position is not None and not (0 <= self.position <= len(bin_.references)):
            raise CommandRefused("bad_value", "position out of range")

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        media = _media(project, self.media)
        ref_bin = _ref_bin(project, media)
        handle = assign_handle(bin_, ref_bin)
        ref = Reference(id=ids.new_id(ids.REFERENCE), media=media.id, bin=ref_bin.id, handle=handle)
        if self.position is None:
            bin_.references.append(ref)
        else:
            bin_.references.insert(self.position, ref)
        self._handle = handle
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        return f"{bin_.name if bin_ else self.bin} - bound .{getattr(self, '_handle', '?')}"


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
        # close the gap, so the last reference to a bin never sits there wearing a .2
        renumber_handles(bin_, gone.bin)
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        return f"{bin_.name if bin_ else self.bin} - unbound .{getattr(self, '_handle', '?')}"


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
    picture or clip behind it."""

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
        _ref_bin(project, _media(project, self.media))

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        ref = self._ref(bin_)
        media = _media(project, self.media)
        ref.media = media.id
        ref.bin = _ref_bin(project, media).id
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        ref = self._ref(bin_) if bin_ else None
        return f"{bin_.name if bin_ else self.bin} - .{ref.handle if ref else '?'} now uses different media"


class RecallRef(BaseModel):
    """One restored reference: the media, and the handle it answered to when the take was made."""

    model_config = ConfigDict(extra="forbid")
    media: str
    handle: str


class RecallGeneration(Command):
    """Put a bin back the way it was when it produced one of its takes, ready to render again.

    Restores the bin's workflow, prompt, negative and reference set. The references carry the
    handles the generation recorded, so the recalled prompt's .tokens point at the same media
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
        bin_.prompt, bin_.loras = lift_lora_tags(self.prompt, [lora for lora in self.loras if lora.name.strip()])
        bin_.references = [
            Reference(
                id=ids.new_id(ids.REFERENCE),
                media=r.media,
                bin=_ref_bin(project, _media(project, r.media)).id,
                handle=r.handle,
            )
            for r in self.references
        ]
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        return f"{bin_.name if bin_ else self.bin} - recalled the settings that made {self.from_media or 'a take'}"


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

    def check(self, project, ctx, env) -> None:
        bin_ = _bin(project, self.bin)
        if not env.workflow_exists(bin_.workflow):
            raise CommandRefused("unknown_workflow", f"{bin_.name} has no usable workflow ({bin_.workflow or 'none'}); set one first")

    def apply(self, project, ctx, env) -> Applied:
        bin_ = _bin(project, self.bin)
        kind = env.kind(bin_.workflow)
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
            warnings=[w.to_dict() for w in assembled.warnings],
        )
        return Applied(target=self.target(project), generation=gen, assembled=assembled)

    def describe(self, project, before, after) -> str:
        bin_ = project.bins_by_id.get(self.bin)
        # a seed the caller chose is worth saying: a held seed is why two takes look alike
        notes = [n for n in (("saved for later" if self.hold else ""), ("quarter size" if self.params.get("quarter") else ""), (f"seed {self.params['seed']}" if self.params.get("seed") is not None else "")) if n]
        return f"{bin_.name if bin_ else self.bin} - generate" + (f" ({', '.join(notes)})" if notes else "")


class ShotItem(BaseModel):
    """One intended generation on a propose_shots card. The user reads ``description``, the
    one-liner, and may rewrite it or drop the item before approving; ``prompt`` is what the
    render says when the agent wrote one apart from the one-liner. The trailing fields are the
    server's: written on decision and replayed with the payload, so they are declared here."""

    model_config = ConfigDict(extra="forbid")
    name: str = ""
    bin: str | None = None
    """An existing bin to render in; the fields below overwrite its own when given, and ``name``
    is filled from it. Without it ``name`` is a new bin's name."""
    description: str
    prompt: str | None = None
    negative: str | None = None
    workflow: str | None = None
    references: list[str] = []
    length_s: float | None = None
    quarter: bool = False
    params: dict[str, Any] = {}
    tags: list[str] = []
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

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def _workflow(self, project: Project, item: ShotItem) -> str:
        if item.bin is not None and item.workflow is None:
            return _bin(project, item.bin).workflow
        return str(item.workflow if item.workflow is not None else project.defaults.image_workflow or "").strip()

    def check(self, project, ctx, env) -> None:
        if not self.items:
            raise CommandRefused("bad_value", "no shots")
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
            if item.length_s is not None:
                if env.kind(wf) != "video":
                    raise CommandRefused("wrong_kind", f"{where}: length_s applies to a video workflow only")
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
                bin_ = Bin(id=ids.new_id(ids.BIN), name=str(item.name).strip(), workflow=wf, length_s=length, tags=_clean_tags(item.tags), created=ctx.ts())
                project.bins_by_id[bin_.id] = bin_
            item.bin_id = bin_.id
            prompt = item.prompt if item.prompt is not None else item.description
            prompt, loras = lift_lora_tags(prompt, bin_.loras)
            apply_patch(bin_, {"prompt": prompt, "loras": [lora.model_dump() for lora in loras], "description": item.description.strip()})
            if item.negative is not None:
                apply_patch(bin_, {"negative": item.negative})
            for media_id in item.references:
                bind = BindReference(bin=bin_.id, media=media_id)
                bind.check(project, ctx, env)
                bind.apply(project, ctx, env)
            warnings.extend(token_warnings(project, bin_, env))
            if item.renders():
                params = {**({"quarter": True} if item.quarter and kind == "video" else {}), **item.params}
                self._renders.append((item, Generate(bin=bin_.id, params=params, reason=self.reason)))
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

    def apply(self, project, ctx, env) -> Applied:
        media = _media(project, self.media)
        media.qc = QcMark(status=self.status, note=self.note, by=ctx.actor, ts=ctx.ts())
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        return f"{_take_label(project, self.media)} - marked {self.status.replace('_', ' ')}"


class AddMediaToBin(Command):
    """Put an existing media into a bin's gallery. The newest arrival leads until something
    else is selected, the same as a landing take does."""

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
        bin_.media.append(self.media)
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
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        n = getattr(self, "_gone", 0)
        return f"deleted {getattr(self, '_name', self.bin)} ({n} media)"


class PostMessage(Command):
    name: ClassVar[str] = "post_message"
    text: str
    attachments: list[str] = []
    kind: Literal["user", "agent", "blocker"] = "user"
    # what the user was looking at (bin + lead media); the names are filled here from the project
    context: MessageContext | None = None

    def target(self, project: Project) -> ChangeTarget:
        return ChangeTarget(type="project", id=project.id)

    def check(self, project, ctx, env) -> None:
        if not str(self.text).strip():
            raise CommandRefused("bad_value", "message text is empty")
        for m in self.attachments:
            _media(project, m)
        if self.context is not None:
            _bin(project, self.context.bin)
            if self.context.media:
                _media(project, self.context.media)
        if ctx.actor == "user" and self.kind != "user":
            raise CommandRefused("wrong_actor", "a user posts user messages")
        if ctx.actor == "agent" and self.kind == "user":
            raise CommandRefused("wrong_actor", "an agent posts agent or blocker messages")


class StartSession(Command):
    """Open a waiting session. Kickoff notes, when given, post as the user's message with
    the refs bin's media attached; the brief itself is set when the project is created."""

    name: ClassVar[str] = "start_session"
    notes: str | None = None
    refs_bin: str | None = None
    mode: SessionMode = "auto"

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
        project.agent_session = AgentSession(id=ids.new_id(ids.SESSION), status="waiting", mode=self.mode, started=ctx.ts())
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        return "session started" if self.mode == "auto" else f"session started ({self.mode})"


class SetSessionMode(Command):
    """Change how much the agent asks, mid-session. The user does it from the panel; the agent
    does it ``requested`` when the user said so in chat ("switch to manual")."""

    name: ClassVar[str] = "set_session_mode"
    mode: SessionMode

    def target(self, project: Project) -> ChangeTarget:
        sess = project.agent_session
        return ChangeTarget(type="session", id=sess.id if sess else "")

    def check(self, project, ctx, env) -> None:
        if not _session_id(project):
            raise CommandRefused("no_session", "no session to set a mode on")

    def apply(self, project, ctx, env) -> Applied:
        project.agent_session.mode = self.mode
        return Applied(target=self.target(project))

    def describe(self, project, before, after) -> str:
        return f"session mode {self.mode}"


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
        if self.project_name is None and self.brief is None and self.private is None and not self.changes():
            raise CommandRefused("bad_value", "nothing to change")

    def apply(self, project, ctx, env) -> Applied:
        self._changed = self.changes()
        self._ended_session = False
        patch: dict[str, Any] = {}
        if self.project_name is not None:
            patch["name"] = self.project_name.strip()
        if self.brief is not None:
            patch["brief"] = self.brief.strip()
        if self.private is not None:
            patch["private"] = self.private
        if self._changed:
            patch["defaults"] = {**_dump(project.defaults), **self._changed}
        apply_patch(project, patch)
        if self.private and _session_id(project):
            # going private shuts the agent out; a session it could not continue ends here
            project.agent_session.status = "ended"
            project.agent_session.ended = ctx.ts()
            self._ended_session = True
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
            parts.append(private if self.private else "not private")
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
    name: ClassVar[str] = "connect_session"
    harness: str

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


COMMANDS: dict[str, type[Command]] = {
    cls.name: cls
    for cls in (
        CreateBin,
        DuplicateBin,
        SetBinField,
        BindReference,
        UnbindReference,
        ReorderReferences,
        RebindReference,
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


def check_session_gate(project: Project, command: Command, ctx: CommandContext) -> None:
    """An agent needs a live session; connect needs a waiting one; the user never connects."""
    sess = project.agent_session
    if ctx.actor != "agent":
        if isinstance(command, ConnectSession):
            raise CommandRefused("wrong_actor", "only the agent connects a session")
        return
    if isinstance(command, PostMessage):
        return
    if isinstance(command, StartSession):
        raise CommandRefused("wrong_actor", "only the user starts a session")
    if isinstance(command, ConnectSession):
        if not sess or sess.status != "waiting":
            raise CommandRefused("no_session", "no session is waiting for an agent")
        return
    if not sess or sess.status != "live":
        raise CommandRefused("no_session", "agent commands need a live session; connect first")
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
        if not (isinstance(command, SetProjectSettings) and ctx.propose):
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
    # the batch card is the ask; without the ask it is only what the user already said out loud
    if isinstance(command, ProposeShots) and not ctx.propose and not command.requested:
        raise CommandRefused("propose_required", "propose_shots is a card: send it with --propose, or --requested when the user asked for the batch in the feed")


INBOX_KINDS = ("user", "decision", "system", "blocker")


def check_inbox_gate(store: ProjectStore, project: Project, command: Command, ctx: CommandContext, env: Env | None = None) -> None:
    """An agent render must carry a current inbox cursor; unread items refuse it. A review
    session also waits: the next render of a kind the mode asks about is refused while a take of
    that kind still owes the user a verdict."""
    if ctx.actor != "agent" or not isinstance(command, (Generate, ProposeShots)) or ctx.bypass_gates:
        return
    if isinstance(command, ProposeShots) and ctx.propose:
        return  # the proposal spends nothing; the approval replays it with the gates bypassed
    if ctx.inbox_cursor is None:
        raise CommandRefused("inbox_cursor_required", "generate needs the agent's inbox cursor")
    if not command.requested:  # the user asked for this one out loud, verdict or no verdict
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
    """A review session stops for each take it asks about: while one of that kind is still
    pending, the next render of that kind waits for the verdict."""
    sess = project.agent_session
    asks = MODE_ASKS.get(sess.mode, ()) if sess is not None else ()
    if not asks:
        return
    kinds = {command.renders(project, env)} if isinstance(command, Generate) else set(asks)
    owed = [
        c for c in store.read_changes(project).values()
        if c.session == sess.id and c.command == "generate" and c.decision is not None and c.decision.status == "pending"
    ]
    if not owed:
        return
    gens = {g.id: g for g in store.read_generations(project)}
    for c in owed:
        gen = gens.get(c.generation or "")
        kind = gen.kind if gen is not None else None
        if kind in asks and kind in kinds:
            raise CommandRefused(
                "verdict_owed",
                f"{sess.mode}: a {kind} take is still waiting for the user's verdict (change {c.id}); watch for the decision before the next {kind} render",
                {"change": c.id, "kind": kind},
            )


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
    check_session_gate(project, command, ctx)
    check_inbox_gate(store, project, command, ctx, env)
    command.check(project, ctx, env)
    now = ctx.ts()

    if isinstance(command, PostMessage):
        context = None
        if command.context is not None:
            bin_ = _bin(project, command.context.bin)
            media = _media(project, command.context.media) if command.context.media else None
            context = MessageContext(
                bin=bin_.id,
                bin_name=bin_.name,
                media=media.id if media is not None else None,
                media_label=command.context.media_label or (_media_label(bin_, media) if media is not None else ""),
            )
        msg = _post(store, project, command.kind, command.text.strip(), ts=now, attachments=list(command.attachments), context=context)
        return CommandResult(project=project, messages=[msg])

    if ctx.propose and ctx.actor != "agent":
        raise CommandRefused("wrong_actor", "only the agent proposes")
    if isinstance(command, ProposeShots) and len(command.items) > 1:
        return _run_shots(store, project, command, ctx, env, now=now)
    if ctx.propose:
        return _propose(store, project, command, ctx, now=now)
    if isinstance(command, ProposeShots):
        return _apply_batch(store, project, command, ctx, env, now=now)
    return _apply_and_log(store, project, command, ctx, env, now=now)


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
    # a revised brief replaces the one still on the table: the plan is a conversation of
    # proposals, and only the latest is open for a verdict
    if isinstance(command, SetProjectSettings) and command.brief is not None:
        for prior in store.read_changes(project).values():
            if (
                prior.actor == "agent"
                and prior.command == command.name
                and prior.decision is not None
                and prior.decision.status == "proposed"
                and (prior.payload or {}).get("brief") is not None
            ):
                prior = prior.model_copy()
                prior.decision = Decision(status="superseded", note="revised", ts=now)
                store.append_change(project, prior)
                messages.append(_post(store, project, "decision", ts=now, change=prior.id, status="superseded", note="revised"))
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
    if (
        decision is None
        and ctx.actor == "agent"
        and not command.requested
        and not silent
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
    elif isinstance(command, (ConnectSession, EndSession, SetSessionMode)):
        messages.append(_post(store, project, "system", change.description, change=change.id, ts=now))
    elif announce and ctx.actor == "agent" and not command.quiet and not silent:
        messages.append(_post(store, project, "change", change.description, change=change.id, ts=now))
    elif announce and ctx.actor == "agent" and not command.quiet and silent:
        # auto mode owes no verdict, but it owes visibility: the experiment shows in the feed as
        # a line with the agent's reason, the way the user's own edits show as "you: ..."
        why = (command.reason or "").strip()
        messages.append(_post(store, project, "system", f"agent: {change.description}" + (f" -- {why}" if why else ""), change=change.id, ts=now))
    elif announce and ctx.actor == "user" and live:
        messages.append(_post(store, project, "system", f"you: {change.description}", change=change.id, ts=now))

    warnings = [w.to_dict() for w in (applied.assembled.warnings if applied.assembled else applied.warnings)]
    return CommandResult(project=project, change=change, generation=applied.generation, warnings=warnings, messages=messages)


# -- decisions ---------------------------------------------------------------

DecisionAction = Literal["approve", "reject", "reopen"]


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
                if cur.get(k) != value.get(k):
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
) -> CommandResult:
    """The user's verdict on an agent change. ``items`` is the per-item verdict on a proposed
    propose_shots card (index, enabled, description); an item left alone is kept as it was."""
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

    command: Command | None = None
    if action == "approve" and status == "proposed":
        command = parse_command(change.command, change.payload or {})
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
            # approving the plan session's revised brief is the go: the loop runs on it from here
            sess = project.agent_session
            if sess is not None and sess.mode == "plan" and isinstance(command, SetProjectSettings) and command.brief is not None:
                sess.mode = "auto"
                store.save(project)
                messages.append(_post(store, project, "system", "brief approved, session mode auto", ts=now))
        elif status in ("pending", "approved"):
            change.decision = Decision(status="approved", note=note, ts=now)
            store.append_change(project, change)
        else:
            _bad()

    elif action == "reject":
        if status == "proposed":
            change.decision = Decision(status="rejected", note=note, ts=now)
            store.append_change(project, change)
        elif change.command == ProposeShots.name and status == "approved":
            # the bins stay: renders may be in flight in them, and their takes are the record
            change.decision = Decision(status="rejected", note=note, ts=now)
            store.append_change(project, change)
        elif status in ("pending", "approved"):
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
                store.save(project)
            store.append_change(project, change)
        else:
            _bad()

    elif action == "close":
        # seen, nothing owed, nothing said: what was applied stays, what was proposed never runs
        if status in ("proposed", "pending"):
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
                    store.save(project)
                change.decision = Decision(status="pending", note=note, ts=now)
            store.append_change(project, change)
        else:
            _bad()
    else:  # pragma: no cover - Literal
        raise CommandRefused("bad_value", f"unknown action {action}")

    final = result_change.decision.status if result_change.decision else None
    messages.append(_post(store, project, "decision", ts=now, change=result_change.id, status=final, note=note if action != "close" else None))
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
