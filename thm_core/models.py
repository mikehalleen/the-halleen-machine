"""THM records (design sections 3 and 6). Ids are opaque strings; nothing references anything by name.

Schema 4: one record, the bin, holds what schema 3 split between a shot (a recipe with takes)
and an item (a gallery). A bin is a recipe plus a gallery. Any bin's media can be referenced
from any bin, its own included, by a handle minted from the bin's name. Nothing is a
deliverable until a sequence (later) says so.

Schema-3 files upgrade in ``store.upgrade_v3``; the append-only logs are not rewritten, so the
records that appear in them (``Reference``, ``MediaOrigin``, ``ChangeTarget``, ``Generation``)
read the old ``shot``/``item`` keys tolerantly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 4

Actor = Literal["user", "agent"]
MediaType = Literal["image", "video"]
# what a workflow makes, and so what a generation is; there is no container kind any more
Kind = MediaType

# Tags say what a bin is for; the explorer filters on them. These four are always offered
# (1.x's characters, poses, settings and styles); a project's vocabulary is these plus
# whatever else its bins carry.
DEFAULT_TAGS: tuple[str, ...] = ("character", "pose", "location", "clip", "keyframe")


class Record(BaseModel):
    """Stored records tolerate unknown keys so an older core can load a newer file."""

    model_config = ConfigDict(extra="allow")


def _fold_bin(data: Any, *keys: str) -> Any:
    """Schema-3 lines named the target ``shot`` or ``item``; both mean ``bin`` now."""
    if not isinstance(data, dict) or not any(k in data for k in keys):
        return data
    data = dict(data)
    if not data.get("bin"):
        for key in keys:
            if data.get(key):
                data["bin"] = data[key]
                break
    for key in keys:
        data.pop(key, None)
    return data


class Reference(Record):
    id: str
    media: str
    bin: str  # the bin the handle is minted from; may be the referencing bin itself
    handle: str

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        data = _fold_bin(data, "item")
        # schema 3 stood a take with no item in for a synthetic item named after its shot
        if isinstance(data, dict) and isinstance(data.get("bin"), str) and data["bin"].startswith("shot:"):
            data = {**data, "bin": data["bin"][5:]}
        return data


class Lora(Record):
    """One LoRA on a bin. ``strength_b`` is the second lane where a graph has one (CLIP
    strength, or the low-noise lane of a dual video graph); None means the same as ``strength``.
    Delivered to the runner as a ``<lora:name:strength[:strength_b]>`` tag in front of the
    submitted prompt, never stored in the typed prompt."""

    name: str
    strength: float = 1.0
    strength_b: float | None = None

    def tag(self) -> str:
        s = f"{self.strength:g}"
        return f"<lora:{self.name}:{s}>" if self.strength_b is None else f"<lora:{self.name}:{s}:{self.strength_b:g}>"


class Bin(Record):
    """A recipe plus a gallery.

    The recipe (workflow, prompt, negative, references, loras, length) is what Generate renders;
    the gallery (``media``) is everything that landed here: takes, uploads, clips. ``selected``
    is the one the bin leads with, in the hero and on a library card, and the one another bin
    picks up by default when it references this bin. A bin's kind is its workflow's kind, read
    at command time; ``length_s`` only means something when that is video.
    """

    id: str
    name: str
    workflow: str = ""
    length_s: float | None = None
    loras: list[Lora] = Field(default_factory=list)
    prompt: str = ""
    negative: str = ""
    references: list[Reference] = Field(default_factory=list)
    media: list[str] = Field(default_factory=list)
    selected: str | None = None
    counters: dict[str, int] = Field(default_factory=dict)  # "take": the running take number
    description: str = ""  # downstream description; never a generator prompt
    tags: list[str] = Field(default_factory=list)
    created: str = ""  # so an empty bin still has a place in a newest-first library
    # takes that landed and nobody has looked at yet: the gallery and the explorer mark them
    # until the user shows one (the hero, the viewer), which clears it through the seen route
    unseen: list[str] = Field(default_factory=list)


OriginType = Literal["generation", "upload", "clip", "frame"]


class MediaOrigin(Record):
    type: OriginType
    generation: str | None = None
    bin: str | None = None  # the bin the media landed in
    source: str | None = None  # clip and frame: the video it came from; upload: the media it was made from, when known
    start_s: float | None = None
    end_s: float | None = None
    at_s: float | None = None
    # an upload's provenance in words, when the uploader has it ("audio removed, ffmpeg -an").
    # Not a recipe and never required: an agent that edited a take locally says what it did here.
    note: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        return _fold_bin(data, "shot", "item")

    @model_validator(mode="after")
    def _check_variant(self) -> "MediaOrigin":
        if self.type == "generation":
            if not self.generation:
                raise ValueError("generation origin needs 'generation'")
            if not self.bin:
                raise ValueError("generation origin needs 'bin'")
        elif self.type == "upload":
            if not self.bin:
                raise ValueError("upload origin needs 'bin'")
        elif self.type == "clip":
            if not self.source or self.start_s is None or self.end_s is None:
                raise ValueError("clip origin needs 'source', 'start_s' and 'end_s'")
        elif self.type == "frame":
            if not self.source or self.at_s is None:
                raise ValueError("frame origin needs 'source' and 'at_s'")
        return self


QcStatus = Literal["pass", "fail", "in_progress"]


class QcMark(Record):
    status: QcStatus
    note: str | None = None
    by: Actor = "user"
    ts: str = ""


class Media(Record):
    id: str
    type: MediaType
    path: str
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    fps: float | None = None
    frames: int | None = None
    created: str = ""
    origin: MediaOrigin
    qc: QcMark | None = None
    # the bin's running take number, stamped as the take lands and never reused: take 9 stays
    # take 9 after the others are deleted. None for media that was not generated into a bin.
    take: int | None = None


SessionStatus = Literal["none", "waiting", "live", "ended"]

# How much the agent asks. ``auto``: the user states the goal and the agent loops; its changes
# land done with no card, and the finished candidate is presented as one card. ``manual``: every
# render is proposed first (propose_shots); the approval starts it and the takes land as lines.
# ``image_review``: renders start on their own and every take, still or video, is a card after it
# lands; the next render of a kind waits for the verdict on the last. ``video_review``: the same
# for video takes only; the agent QCs the stills itself. ``plan``: read-only except messages and
# one proposed revised brief, whose approval flips the session to auto.
SessionMode = Literal["auto", "manual", "image_review", "video_review", "plan"]

# the two manual modes of schema 4's first week, as stored in live projects
_RENAMED_MODES = {"manual_video": "video_review", "manual_all": "image_review"}


class AgentSession(Record):
    """One span of agent activity on a project. The brief is the project's, not the session's."""

    id: str
    status: SessionStatus = "waiting"
    mode: SessionMode = "auto"
    harness: str | None = None
    started: str | None = None
    connected: str | None = None
    ended: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _renamed_modes(cls, data):
        if isinstance(data, dict) and data.get("mode") in _RENAMED_MODES:
            data = {**data, "mode": _RENAMED_MODES[data["mode"]]}
        return data


class ComfySettings(Record):
    api_base: str = ""
    output_root: str = ""


class ProjectDefaults(Record):
    """What new bins and generations inherit. An unset canvas means the project is not configured yet.

    Global prompts apply at generation time only: the positive is appended after the bin's
    prompt, the negative merges with the bin's negative. Existing takes keep their records.
    """

    width: int | None = None
    height: int | None = None
    image_workflow: str = ""
    video_workflow: str = ""
    video_length_s: float = 5.0
    image_prompt: str = ""
    video_prompt: str = ""
    image_negative: str = ""
    video_negative: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.width and self.height)

    def workflow_for(self, kind: str) -> str:
        return self.video_workflow if kind == "video" else self.image_workflow

    def prompt_for(self, kind: str) -> str:
        return (self.video_prompt if kind == "video" else self.image_prompt).strip()

    def negative_for(self, kind: str) -> str:
        return (self.video_negative if kind == "video" else self.image_negative).strip()


class Project(Record):
    schema_version: Literal[4] = SCHEMA_VERSION
    id: str
    name: str
    slug: str = ""
    comfy: ComfySettings = Field(default_factory=ComfySettings)
    defaults: ProjectDefaults = Field(default_factory=ProjectDefaults)
    bins_by_id: dict[str, Bin] = Field(default_factory=dict)
    media_by_id: dict[str, Media] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)
    # The brief is written once, when the project is created; sessions continue from it and the feed.
    brief: str = ""
    # private: the project list shows no cover picture for it (the home screen can be glanced at),
    # and the agent cannot see it: the server hides it from the agent's list, refuses every agent
    # call addressed to it, and no session starts on it. Turning it on ends a running session.
    private: bool = False
    agent_session: AgentSession | None = None


TargetType = Literal["project", "bin", "media", "session"]


class ChangeTarget(Record):
    type: TargetType
    id: str

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("type") in ("shot", "item"):
            data = {**data, "type": "bin"}
        return data


# ``closed``: seen, nothing owed and nothing said -- the user cleared the card without a reply
DecisionStatus = Literal["proposed", "pending", "approved", "rejected", "superseded", "closed"]


class Decision(Record):
    status: DecisionStatus
    note: str | None = None
    ts: str | None = None


class Change(Record):
    id: str
    ts: str
    actor: Actor
    session: str | None = None
    command: str
    target: ChangeTarget
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    reason: str | None = None
    decision: Decision | None = None
    payload: dict[str, Any] | None = None
    generation: str | None = None
    # the propose_shots change this render was approved under; the feed folds it under that card
    batch: str | None = None


MessageKind = Literal["user", "agent", "system", "change", "decision", "blocker"]


class MessageContext(Record):
    """What the user was looking at when they wrote the note: the bin on screen and its lead
    media. "This take" / "this video" in the text means these ids. Names ride along so the feed
    and the agent's inbox line read without a lookup."""

    bin: str
    bin_name: str = ""
    media: str | None = None
    media_label: str = ""


class Message(Record):
    id: str
    ts: str
    session: str | None = None
    kind: MessageKind
    text: str = ""
    attachments: list[str] = Field(default_factory=list)
    context: MessageContext | None = None
    change: str | None = None
    status: str | None = None
    note: str | None = None


class GenerationReference(Record):
    ref: str
    media: str
    handle: str
    slot: str | None = None
    ordinal: int | None = None


# held: saved for later -- a resolved snapshot the job manager never saw; released by hand
GenerationStatus = Literal["held", "queued", "running", "done", "failed", "cancelled"]


class Generation(Record):
    id: str
    bin: str  # where the outputs land
    kind: Kind = "image"  # what the workflow makes, stamped when the generation is built
    ts: str
    workflow: str
    prompt_typed: str = ""
    prompt_submitted: str = ""
    negative_submitted: str = ""
    references: list[GenerationReference] = Field(default_factory=list)
    loras: list[Lora] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    status: GenerationStatus = "queued"
    outputs: list[str] = Field(default_factory=list)
    prelude: list[str] = Field(default_factory=list)
    paths_by_slot: dict[str, str] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    # how long ComfyUI ran the prompt, in seconds, by its own clock (``/history`` execution
    # marks): the render alone, not the wait in its queue. None until it lands, and for a
    # record from before this was kept.
    exec_seconds: float | None = None
    # the file this record was rebuilt from (``provenance``): a render THM never landed whose
    # output carries the ComfyUI graph. None for a record the render itself wrote.
    recovered_from: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        data = _fold_bin(data, "shot", "item")
        if isinstance(data, dict) and data.get("kind") is None:
            data = {**data, "kind": "image"}
        return data
