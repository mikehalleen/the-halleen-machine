"""THM records (design sections 3 and 6). Ids are opaque strings; nothing references anything by name.

Schema 4: one record, the bin, holds what schema 3 split between a shot (a recipe with takes)
and an item (a gallery). A bin is a recipe plus a gallery. Any bin's media can be referenced
from any bin, its own included, by a handle minted from the bin's name. Nothing is a
deliverable until a sequence (later) says so.

Schema 5: a reference's handle carries the media file's extension and prompts address it with
an ``@`` prefix (``@mara.png``, ``@mara.mp4``, ``@mara.png.2``); see ``slug``.

Schema 6: a bin's name is its title and its handle base is a separate frozen ``slug``; a bin
made as a shot carries a permanent ``shot`` number (``store.upgrade_v5``).

Schema-3 files upgrade in ``store.upgrade_v3``, schema-4 in ``store.upgrade_v4``, schema-5 in
``store.upgrade_v5``; the
append-only logs are not rewritten, so the records that appear in them (``Reference``,
``MediaOrigin``, ``ChangeTarget``, ``Generation``) read the old ``shot``/``item`` keys
tolerantly, and a generation recorded before schema 5 carries dot-era handles.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 6

Actor = Literal["user", "agent"]
# audio is ordinary media in a bin (a song, a voice line, a bed) with a player and no picture;
# it is never a sequence item or a generation reference, and no workflow makes it
MediaType = Literal["image", "video", "audio"]
# what a workflow makes, and so what a generation is; there is no container kind any more
Kind = Literal["image", "video"]

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
    # the title: what the bin is about ("he agrees", "Jim", "Desert flat"), editable any time
    # and never a handle, so retitling rewrites no prompt
    name: str
    # a shot's permanent number, minted from the project's "shot" counter when a bin is created
    # as a shot and never reused or renumbered; None on an asset bin (a character, a plate, a
    # reference clip). Where it sits in the cut is a third thing, kept by the sequence
    shot: int | None = None
    # the handle base prompts address it by (@shot-18.png, @mara.png): frozen at creation,
    # changed only by an explicit set_bin_field slug, which is the one rename that rewrites prompts
    slug: str = ""
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
    # the last time anything touched the bin: an edit by either actor, a take landing. Empty on a
    # bin no command has touched since this was added; the explorer falls back to its content
    updated: str = ""
    # takes that landed and nobody has looked at yet: the gallery and the explorer mark them
    # until the user shows one (the hero, the viewer), which clears it through the seen route
    unseen: list[str] = Field(default_factory=list)


OriginType = Literal["generation", "upload", "clip", "frame", "gif", "export"]


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
    # generation: how long ComfyUI ran the prompt, in seconds, by its own clock (the render
    # alone, not its queue wait). Copied from the generation record at landing so the gallery
    # can say it without a join; None for a take from before the timing was kept.
    exec_seconds: float | None = None
    # export: the sound layers mixed in, each ``{id, media, source_in_s, start_frame}``; None
    # for an export with none, and for every other origin
    layers: list[dict[str, Any]] | None = None

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
        elif self.type == "gif":
            if not self.source:
                raise ValueError("gif origin needs 'source'")
        elif self.type == "export":
            # a sequence rendered to one file: ``source`` is the sequence id, ``bin`` its output bin
            if not self.source or not self.bin:
                raise ValueError("export origin needs 'source' (the sequence) and 'bin'")
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
    # audio: the probed stream; None on a picture or a clip (a clip's sound is read by the tools)
    sample_rate: int | None = None
    channels: int | None = None
    created: str = ""
    origin: MediaOrigin
    qc: QcMark | None = None
    # the bin's running take number, stamped as the take lands and never reused: take 9 stays
    # take 9 after the others are deleted. None for media that was not generated into a bin.
    take: int | None = None


# origins that make a media a numbered take of its bin: a render, or a sequence's export
TAKE_ORIGINS = ("generation", "export")


def stamp_take(bin_: Bin, media: Media) -> int:
    """The bin's next take number, stamped on the media and never reused."""
    bin_.counters["take"] = int(bin_.counters.get("take", 0)) + 1
    media.take = bin_.counters["take"]
    return media.take


SessionStatus = Literal["none", "waiting", "live", "ended"]

# How much the agent asks. ``auto``: the user states the goal and the agent loops; its changes
# land done with no card, and the finished candidate is presented as one card. ``manual``: every
# render is proposed first (propose_shots); the approval starts it and the takes land as lines.
# ``image_review``: renders start on their own and every take, still or video, is a card after it
# lands; the next render of a kind waits for the verdict on the last. ``video_review``: the same
# for video takes only; the agent QCs the stills itself. ``plan``: read-only except messages and
# one proposed revised brief, whose approval flips the session to auto. Drafts-first is not a
# mode but a switch on the session (``AgentSession.drafts``) that rides with any mode: everything
# cheap -- stills, quarter-size video, anything on a workflow the project lists as a draft
# workflow -- runs as the mode says, while a full-quality render is asked about first (a
# propose_shots card: yes, later, or no). The step up in cost is the one thing that waits.
SessionMode = Literal["auto", "manual", "image_review", "video_review", "plan"]

# the two manual modes of schema 4's first week, as stored in live projects
_RENAMED_MODES = {"manual_video": "video_review", "manual_all": "image_review"}


class AgentSession(Record):
    """One span of agent activity on a project. The brief is the project's, not the session's."""

    id: str
    status: SessionStatus = "waiting"
    mode: SessionMode = "auto"
    drafts: bool = False
    harness: str | None = None
    started: str | None = None
    connected: str | None = None
    ended: str | None = None
    # ended because the project went private: turning private off opens the next one by itself
    ended_by_private: bool = False
    # The user's notes the agent has accounted for: message id -> what it did about it, in a
    # few words. A note not in here is open, and an open note is work owed: the agent's watch
    # does not block while one exists. Reading a note (the inbox cursor) is not answering it.
    answered: dict[str, str] = Field(default_factory=dict)
    # set when the session starts; a session from before notes were tracked owes only its
    # recent ones (``commands.open_notes``), not every note it ever got
    notes_tracked: bool = False

    @model_validator(mode="before")
    @classmethod
    def _renamed_modes(cls, data):
        if isinstance(data, dict) and data.get("mode") in _RENAMED_MODES:
            data = {**data, "mode": _RENAMED_MODES[data["mode"]]}
        # drafts-first was a mode before it was a switch
        if isinstance(data, dict) and data.get("mode") == "draft":
            data = {**data, "mode": "auto", "drafts": True}
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


RuleKind = Literal["qc", "continuity", "other"]


class ProjectRule(Record):
    """A standing answer the user gave once, kept on the project so it outlives any one agent
    session: what to do with a kind of QC finding ("colour drift over 10: re-roll"), or a
    continuity fact ("Tommy's empty plate is in every shot where the bar is visible")."""

    id: str
    kind: RuleKind = "other"
    text: str
    created: str = ""
    by: str = "user"  # who wrote it down: the user, or the agent recording the user's answer


# how long a still shows in a sequence when its item sets no length (a title card, a plate)
STILL_DEFAULT_S = 3.0


LayerEnd = Literal["picture", "source"]


class SoundLayer(Record):
    """An audio source that runs continuously under the sequence: a song, a walla bed, room
    tone. Agent-only, no UI control; the export lays it down in one piece (never cut and joined)
    from ``source_in_s`` starting at sequence frame ``start_frame``, until the picture ends or
    the source runs out. Per-item level changes live on the item (``ItemMix.layers``)."""

    id: str  # the agent's name for it: "music", "walla"
    media: str  # an audio media, or a video media with an audio track
    source_in_s: float = 0.0  # where in the source the layer starts playing
    start_frame: int = 0  # the sequence frame the layer starts on
    end: LayerEnd = "picture"
    gain_db: float = 0.0
    fade_in_s: float = 0.0
    fade_out_s: float = 0.0


class ItemMix(Record):
    """How one item's sound sits against the layers: its own clip level, a dB offset (or a mute,
    None) per layer id, the fade on its own audio at both edges, and how fast a layer's gain
    ramps at the item's edges. ``reviewed_start_frame`` is where the item started when the agent
    last QC'd it against the layers; a move since then means it needs another listen."""

    clip_db: float = 0.0
    layers: dict[str, float | None] = Field(default_factory=dict)
    edge_fade_ms: int = 10
    ramp_ms: int = 30
    reviewed_start_frame: int | None = None


class SequenceItem(Record):
    """One place in a sequence: a bin, which plays its selected take. The bin is the item's
    identity (a bin sits in a sequence at most once). Trim and still length are the item's, never
    the take's; ``agent`` is whatever the agent set that the UI has no control for (sound, gain,
    a dissolve): opaque here, shown read-only, round-tripped untouched by every UI edit.
    ``mix`` is the one agent setting the export reads (``ItemMix``)."""

    bin: str
    head_s: float = 0.0  # video: seconds dropped from the head
    tail_s: float = 0.0  # video: seconds dropped from the tail
    # the selected take's length when the trim was applied: a take of another length means the
    # trim no longer fits and is dropped (``derive.sequence_out`` says so on the row)
    trim_len_s: float | None = None
    still_s: float | None = None  # image bin: how long the picture shows; None = the default
    agent: dict[str, Any] = Field(default_factory=dict)
    mix: ItemMix | None = None


class Sequence(Record):
    """An ordered list of bins that plays each bin's selected take and exports to one file. A
    sibling of bins, never inside one. Its exports land as takes in ``output_bin``: any bin the
    user points it at, else one made at the first export (``<name> Exports``)."""

    id: str
    name: str
    output_bin: str | None = None
    items: list[SequenceItem] = Field(default_factory=list)
    # the agent's settings that span the whole sequence (a walla bed, room tone, music): opaque
    # like an item's, with its own ``summary`` the user sees once. Item settings are item-local.
    agent: dict[str, Any] = Field(default_factory=dict)
    # the sound under the picture: songs and beds the export mixes in, agent-only
    layers: list[SoundLayer] = Field(default_factory=list)
    created: str = ""
    updated: str = ""


PostPlatform = Literal["youtube"]
PostPrivacy = Literal["private", "unlisted", "public"]
# ``draft``: written, nothing sent. ``uploading``: the server is sending the file. ``uploaded``: on
# the platform, as ``privacy`` says (private unless someone chose otherwise); the user finishes it in
# the platform's own app (thumbnail, end screens, publish). ``failed``: the upload did not land;
# ``error`` says why, and the upload can be run again.
PostStatus = Literal["draft", "uploading", "uploaded", "failed"]

# YouTube's limits on a video's metadata; refused at write time, so a draft is always sendable
YT_TITLE_MAX = 100
YT_DESCRIPTION_MAX_BYTES = 5000
YT_TAGS_MAX_CHARS = 500  # every tag joined, as YouTube counts it


class Post(Record):
    """A video with the words that go with it, drafted for a platform and held unpublished until
    the upload is asked for. Made from a bin: the bin's current take when the post is created (a
    sequence's output bin most of the time, but any bin whose current media is a video). The media
    is frozen on the post: a newer take does not swap in by itself (``set_post_field media``)."""

    id: str
    platform: PostPlatform = "youtube"
    bin: str
    media: str  # the video, in that bin
    # the channel the video goes to: the project's ``youtube_channel`` when the post was drafted
    # (a YouTube channel id; its token lives on the server). Not an agent-settable field.
    channel: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    privacy: PostPrivacy = "private"
    synthetic: bool = True  # YouTube's altered-or-synthetic disclosure; THM output is made, so on by default
    made_for_kids: bool = False
    thumbnail: str | None = None  # an image media, sent after the upload (phase 3)
    publish_at: str | None = None  # ISO 8601: a scheduled publish; needs ``privacy`` private (phase 3)
    extra: dict[str, Any] = Field(default_factory=dict)  # platform-specific knobs (category, playlist, ...)
    status: PostStatus = "draft"
    remote_id: str | None = None  # the platform's id for the video, once uploaded
    remote_url: str | None = None  # where it plays
    studio_url: str | None = None  # where the user finishes it (YouTube Studio)
    error: str | None = None
    uploaded: str | None = None
    created: str = ""
    updated: str = ""
    by: Actor = "user"


class Project(Record):
    schema_version: Literal[6] = SCHEMA_VERSION
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
    # the YouTube channel this project's posts go to (a channel id whose token the server holds;
    # see thm_core/youtube.py). Picked by the user in project settings from the channels connected
    # on the server; many projects share one. Empty: no destination yet, so no post can be drafted.
    youtube_channel: str = ""
    agent_session: AgentSession | None = None
    # standing rules for this project: QC actions and continuity facts the user settled once
    rules: list[ProjectRule] = Field(default_factory=list)
    sequences: list[Sequence] = Field(default_factory=list)
    posts: list[Post] = Field(default_factory=list)


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
# ``replied``: the user answered in words; nothing applied, nothing reverted, nothing owed --
# the note is the instruction, and the agent acts on it
DecisionStatus = Literal["proposed", "pending", "approved", "rejected", "superseded", "closed", "replied"]


class MessageFile(Record):
    """A file the note itself carries: a marked-up screenshot, a picture from somewhere else.
    It lives beside the message log (``<slug>-files/chat/``), in no bin, and is not project
    media: no library, no picker, no reference can reach it until someone keeps it into a bin."""

    id: str
    name: str = ""
    type: MediaType = "image"


class Decision(Record):
    status: DecisionStatus
    note: str | None = None
    # files the user put on the reply (pasted, dropped, picked on the card): the note's own
    files: list[MessageFile] = Field(default_factory=list)
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
    media, or the sequence on screen and, when a row is open, that item's bin and take. "This
    take" / "this shot" / "this cut" in the text means these ids. Names ride along so the feed
    and the agent's inbox line read without a lookup."""

    bin: str | None = None
    bin_name: str = ""
    media: str | None = None
    media_label: str = ""
    sequence: str | None = None
    sequence_name: str = ""
    position: int | None = None  # the open item's 1-based place in the sequence


class Message(Record):
    id: str
    ts: str
    session: str | None = None
    kind: MessageKind
    text: str = ""
    # project media the line points at (ids); ``files`` are the note's own, outside the project
    attachments: list[str] = Field(default_factory=list)
    files: list[MessageFile] = Field(default_factory=list)
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
