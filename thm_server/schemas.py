"""Response and request shapes that are not the raw records.

Records (``Bin``, ``Media``, ``Change``, ``Message``, ``Generation``) are returned as they
are. These models cover the derived and paginated views the screens need, and the request
bodies, which forbid unknown keys.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from thm_core import AgentSession, Bin, Change, Generation, Media, Message
from thm_core.models import ComfySettings, GenerationStatus, ItemMix, Lora, MediaType, Post, ProjectDefaults, ProjectRule, SoundLayer


class ProjectSettingsBody(BaseModel):
    """What new bins and generations inherit; every field optional, only given ones change."""

    model_config = ConfigDict(extra="forbid")
    width: int | None = None
    height: int | None = None
    image_workflow: str | None = None
    video_workflow: str | None = None
    video_length_s: float | None = None
    image_prompt: str | None = None
    video_prompt: str | None = None
    image_negative: str | None = None
    video_negative: str | None = None
    private: bool | None = None  # no cover on the project list, and the agent cannot see the project
    youtube_channel: str | None = None  # a connected channel's id, or "" for none; the project's posts go there


class CreateProjectBody(BaseModel):
    """Manual setup sends the settings; the agent path sends the brief instead and the agent configures."""

    model_config = ConfigDict(extra="forbid")
    name: str
    brief: str | None = None
    settings: ProjectSettingsBody | None = None


class ShotDecision(BaseModel):
    """The user's verdict on one item of a propose_shots card: kept, dropped, or a rewritten one-liner."""

    model_config = ConfigDict(extra="forbid")
    index: int
    enabled: bool = True
    description: str | None = None


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject", "reopen", "close", "reply"]
    note: str | None = None
    items: list[ShotDecision] | None = None  # approve of a propose_shots card only
    hold: bool = False  # approve of a propose_shots card, for later: set it up, save the renders held
    files: list[str] = []  # chat files on the reply (uploaded through the chat-files route)


class FlipBody(BaseModel):
    """Where a flipped copy lands: the named bin, else the bin the source landed in."""

    model_config = ConfigDict(extra="forbid")
    bin: str | None = None


class ClipBody(BaseModel):
    """A segment cut, a still frame, or an animated GIF made from an existing video media
    (design's "clip and still"). Lands in ``imports/`` and joins a bin the same way an upload
    does. A GIF takes the whole video unless ``start_s``/``end_s`` trim it; ``fps`` and
    ``width`` scale it down (default: the source's own)."""

    model_config = ConfigDict(extra="forbid")
    source: str
    mode: Literal["segment", "still", "gif"]
    start_s: float | None = None
    end_s: float | None = None
    at_s: float | None = None
    fps: float | None = None
    width: int | None = None
    bin: str | None = None
    bin_name: str | None = None


class ThumbRef(BaseModel):
    id: str
    type: MediaType


class ProjectCard(BaseModel):
    slug: str
    id: str
    name: str
    bin_count: int
    video_count: int  # media, by type
    image_count: int
    hero: ThumbRef | None = None  # the newest bin's selected picture
    # private: no hero, no blocker text, and its live job carries no bin name; the agent never gets the card at all
    private: bool = False
    modified: str
    version: int
    canvas: str | None = None
    image_workflow: str = ""
    video_workflow: str = ""
    # the session at a glance, so an agent watching every project reads one list, not one detail each
    agent_session: AgentSession | None = None
    # a live session the server has not heard an agent on for a while (``agent_activity.GONE_S``):
    # the harness closed without ``end``. The bar says so, and the next agent connects to it
    agent_gone: bool = False
    blocker: Message | None = None
    pending_changes: int = 0
    # the user's notes the agent has not accounted for yet: work owed, so its watch does not block
    open_notes: int = 0
    # live generations in this server process, so the project list shows which are busy
    generating: int = 0
    generating_job: "JobOut | None" = None  # the running one, else the first queued: for its phase and step count
    held: int = 0  # generations saved for later: the bar sums these into the machine's one queue


class BinCard(Bin):
    """A bin in a list: what it holds, how many takes, and the media it leads with, so the
    explorer filters and draws without loading media."""

    media_types: list[MediaType] = Field(default_factory=list)
    take_count: int = 0
    selected_media: Media | None = None
    kind: Literal["image", "video"] | None = None  # the workflow's kind; None when it has none
    generating: list[str] = Field(default_factory=list)  # live generation ids, so the explorer can say so


class SequenceCard(BaseModel):
    """A sequence on the project screen: name, how many items, how long."""

    id: str
    name: str
    output_bin: str | None = None  # where exports land; None = a new bin at the first export
    items: int
    total_s: float
    thumb: Media | None = None  # the first item's picture or clip, standing for the sequence
    bins: list[str] = Field(default_factory=list)  # the items' bins, in order: what a render in one of them touches
    fresh: int = 0  # items whose current take nobody has looked at yet: what the export now carries that is new


class LayerUnderItem(BaseModel):
    """What one layer does under one item: the span of its source heard there (None = silent
    here: not started yet, or run out), and the item's offset on it (None = muted here)."""

    source_in_s: float | None = None
    source_out_s: float | None = None
    db: float | None = 0.0
    muted: bool = False


class SequenceItemOut(BaseModel):
    """One row of the sequence screen, joined to its bin and the take that plays."""

    bin: str
    position: int  # 1-based place in the sequence
    shot: int | None = None
    name: str
    tags: list[str] = Field(default_factory=list)
    media: Media | None = None  # the bin's selected take; None = no take yet, plays as a title card
    kind: Literal["video", "still", "title"]
    duration_s: float
    start_s: float
    head_s: float = 0.0
    tail_s: float = 0.0
    still_s: float | None = None
    # the trim was applied against a take of another length: it is ignored and the row says so
    trim_dropped: bool = False
    agent: dict[str, Any] = Field(default_factory=dict)
    # exact frames at the sequence rate: the item's length rounded once, starts cumulative
    start_frame: int = 0
    frames: int = 0
    mix: ItemMix | None = None
    layers: dict[str, LayerUnderItem] = Field(default_factory=dict)  # per layer id
    # the agent QC'd this item against the layers at another start frame: it needs another listen
    layer_moved: bool = False


class SequenceOut(BaseModel):
    id: str
    name: str
    output_bin: str | None = None
    output_bin_name: str = ""  # empty until a bin exists
    items: list[SequenceItemOut]
    agent: dict[str, Any] = Field(default_factory=dict)  # sequence-wide agent settings; ``summary`` is the user's line
    layers: list[SoundLayer] = Field(default_factory=list)  # the sound under the picture, agent-only
    fps: float = 24.0  # the rate every frame count is measured at
    total_frames: int = 0
    total_s: float
    without_take: int
    last_export: Media | None = None  # the newest media in the output bin
    created: str = ""
    updated: str = ""


class PostOut(Post):
    """A post as the screens and the agent read it: the record plus the bin's name and the video
    it names, so a list needs no second fetch. Ids only otherwise: nothing here that a private
    project's routes would not already serve."""

    bin_name: str = ""
    media_record: Media | None = None  # the video; None when it has been deleted since
    thumbnail_record: Media | None = None
    channel_handle: str = ""  # "@halleen": the connected channel the post goes to; empty when its token is gone


class ChannelOut(BaseModel):
    """A YouTube channel connected on this server (its token file is here)."""

    id: str
    title: str = ""
    handle: str = ""  # "@halleen" as YouTube shows it
    connected: str = ""


class ConnectStatusOut(BaseModel):
    """Where a project's channel consent stands. ``waiting`` carries Google's URL for the browser
    on the server's machine; ``done`` the channel that is now the project's."""

    status: Literal["idle", "waiting", "done", "failed"]
    auth_url: str | None = None
    channel: ChannelOut | None = None
    error: str | None = None
    started: str | None = None
    finished: str | None = None


class UploadStatusOut(BaseModel):
    """Where a post's upload stands. ``idle`` when none is running and none has run this server
    process (the post's own ``status`` says what happened before); ``done`` carries the ids."""

    post: str
    status: Literal["idle", "running", "done", "failed"]
    progress: float | None = None  # 0..1 while running
    remote_id: str | None = None
    remote_url: str | None = None
    studio_url: str | None = None
    error: str | None = None
    started: str | None = None
    finished: str | None = None


class PublishStatusOut(BaseModel):
    """Whether the server can upload to a platform, the one-line fix when it cannot, and the
    channels connected so far. ``can_connect``: this request came from the server's own machine,
    where a new channel's consent can finish (the redirect is loopback)."""

    platform: Literal["youtube"]
    configured: bool
    detail: str = ""  # why not, with the fix; empty when configured
    channels: list[ChannelOut] = Field(default_factory=list)
    can_connect: bool = False


class PreviewBody(BaseModel):
    """One item rendered with its layer slices and mix, through the export's own plan and graph,
    plus up to ``handles_s`` of the take before and after it. A scratch file for the agent's QC
    listen: not media, in no bin."""

    model_config = ConfigDict(extra="forbid")
    bin: str
    handles_s: float = 0.0


class ExportBody(BaseModel):
    """Where this export lands: a bin id to point the sequence at from now on; omitted, the
    sequence's own output bin (made now if it has none yet)."""

    model_config = ConfigDict(extra="forbid")
    bin: str | None = None


class ExportStatusOut(BaseModel):
    """Where a sequence's export stands. ``idle`` when none is running and none has run this
    server process; ``done`` carries the media it made."""

    sequence: str
    status: Literal["idle", "running", "done", "failed"]
    progress: float | None = None  # 0..1 while running
    media: str | None = None
    take: int | None = None
    error: str | None = None
    started: str | None = None
    finished: str | None = None


class ProjectDetail(BaseModel):
    schema_version: int
    id: str
    name: str
    slug: str
    comfy: ComfySettings
    defaults: ProjectDefaults
    counters: dict[str, int]
    brief: str = ""
    private: bool = False
    youtube_channel: str = ""  # the channel id this project's posts go to; "" = none picked yet
    youtube_channel_record: "ChannelOut | None" = None  # that channel as connected here; None when none, or its token is gone
    configured: bool = False
    agent_session: AgentSession | None
    agent_gone: bool = False  # live, but no agent heard on it lately (see ProjectCard)
    bins: list[BinCard]
    sequences: list[SequenceCard] = Field(default_factory=list)
    posts: list["PostOut"] = Field(default_factory=list)  # every post, newest first
    tags: list[str]  # the project's vocabulary: the defaults, then every tag a bin carries
    media_count: int
    pending_changes: int
    open_notes: int = 0  # the user's notes the live session has not accounted for yet
    version: int
    blocker: Message | None = None
    session_changes: int = 0  # every change the current session made, for the bar's "31 changes" after it ends


class CapacityOut(BaseModel):
    found: bool
    image_slots: int
    video_slots: int
    supports_start_frame: bool
    supports_end_frame: bool
    addresses_by_token: bool
    slot_node_ids: list[str]
    video_slot_node_ids: list[str]
    # a THM-IsDraft switch: the workflow has a draft setting, and the Generate bar offers Draft
    supports_draft: bool = False
    # a canvas THM sets: the Generate bar offers quarter size (video)
    supports_size: bool = False
    kind: Literal["image", "video"] = "image"
    error: str | None = None


class LoraFile(BaseModel):
    """One file under ``[paths] loras``; ``name`` is what goes in a ``<lora:name:strength>`` tag."""

    name: str
    size: int


class ComfyRun(BaseModel):
    """One run in ComfyUI's history that the user started from the ComfyUI frontend (THM's own
    renders are left out), to pick a workflow to import from."""

    id: str
    created: int | None = None  # ms since the epoch, ComfyUI's clock
    status: str = ""
    has_ui: bool = False  # the run carries the UI graph too, so a Comfy-openable copy can be written
    nodes: int = 0
    kind: Literal["image", "video"] = "image"
    models: list[str] = []
    outputs: list[str] = []
    classes: list[str] = []


class WorkflowImportIn(BaseModel):
    """Import a workflow the user has working in ComfyUI. The source is a run in ComfyUI's
    history (``run``: its id or a prefix; the newest when neither source is given) or an output
    PNG (``png_b64``) carrying the graphs. ``tags`` / ``untag`` correct the proposal by node id.
    ``system_prompt`` is the enhancer instructions for this model; required when the graph has
    no built-in enhancer to take them from."""

    model_config = ConfigDict(extra="forbid")

    name: str
    run: str | None = None
    png_b64: str | None = None
    kind: Literal["image", "video"] | None = None
    tags: dict[str, str] = {}
    untag: list[str] = []
    system_prompt: str | None = None
    keep_builtin_enhancer: bool = False
    lora_marker: bool = True
    dry_run: bool = False
    replace: bool = False


class WorkflowImportOut(BaseModel):
    name: str
    kind: Literal["image", "video"]
    source: str
    dry_run: bool
    files: dict[str, str] = {}  # "api" / "ui" -> the path written, or that would be on a dry run
    tags: list[dict] = []  # {node, class, from, to, why}
    kept: list[dict] = []  # THM titles the user already gave: {node, class, title}
    baked: list[str] = []
    enhancer: dict = {}
    lora: dict = {}
    notes: list[str] = []
    capabilities: dict = {}
    has_ui: bool = False


class WorkflowInfo(BaseModel):
    name: str
    kind: Literal["image", "video"]
    image_slots: int
    video_slots: int
    supports_start_frame: bool
    supports_end_frame: bool
    addresses_by_token: bool
    has_lora: bool
    has_negative: bool
    supports_draft: bool = False  # a THM-IsDraft switch: Draft can be turned on per render
    supports_size: bool = False  # a canvas THM sets: quarter size is possible
    is_default: bool = False  # the configured default for its kind, which new projects start on
    error: str | None = None
    # the prompt rules for this workflow: the enhancer system prompt baked into the graph, or
    # the app's default for its kind. Only the single-workflow route fills it; the list stays light.
    guide: str | None = None
    guide_source: Literal["workflow", "default"] | None = None


class RecipeOut(BaseModel):
    """How a media was made, in the shape a bin's recall needs to be seeded from it.

    Only generated media has one: an upload, a clip or a lifted still carries no generation, and
    comes back ``found=False`` so the UI can leave the recall controls out rather than offer a
    button that does nothing. References that have since been deleted are dropped from
    ``references`` and named in ``missing``; a workflow that no longer resolves is reported in
    ``workflow_missing`` rather than silently swapped for a different one.

    This is the whole recipe, including ``seed``. Recall applies only the part the bin can
    actually show and edit, so the seed does not reach the form today; it is here for provenance,
    and for the seed control when there is one. ``loras`` is applied: the chip row edits it.
    """

    found: bool = False
    generation: str | None = None
    bin: str | None = None
    kind: Literal["image", "video"] | None = None
    workflow: str = ""
    workflow_missing: bool = False
    prompt: str = ""
    negative: str = ""
    references: list[str] = []
    handles: list[str] = []  # parallel to `references`: the token each answered to
    missing: list[str] = []
    loras: list[Lora] = []
    seed: int | None = None
    length_s: float | None = None
    # the canvas the render was asked at (before quarter size halves it)
    width: int | None = None
    height: int | None = None
    # the render's cheap settings: the workflow's draft branch, and quarter size
    draft: bool = False
    quarter: bool = False
    # how long ComfyUI ran the prompt, in seconds, queue wait excluded; None for a record from
    # before this was kept, or one rebuilt from a file
    exec_seconds: float | None = None
    # the record was rebuilt from the graph the file carries (an upload of a render THM never
    # landed), not written by the render itself
    recovered: bool = False


class ReferenceOut(BaseModel):
    id: str
    handle: str
    media: Media | None
    bin_id: str | None  # the bin the handle is named from, when it still exists
    bin_name: str
    ordinal: int | None
    sub_label: Literal["start", "end", "unused"] | None
    active: bool
    missing: bool


JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]


class JobOut(BaseModel):
    """A render job: one per generation, keyed by the generation id. Also the ``job`` SSE payload."""

    job: str
    slug: str
    bin: str
    status: JobStatus
    progress: float | None = None
    task: str | None = None
    error: str | None = None
    takes: list[str] = []
    started: str
    finished: str | None = None
    # how long ComfyUI ran the prompt, by its own clock: the render alone, not the wait in its
    # queue nor THM's landing. Known once the job is done; None while live and on a failure.
    exec_seconds: float | None = None
    # live ComfyUI progress while running (all None before the runner reports any)
    phase: str | None = None
    step: int | None = None
    steps: int | None = None
    pass_no: int | None = None
    passes: int | None = None
    queue_ahead: int | None = None
    queue_ahead_ours: int | None = None
    # for the system bar: the bin's name (the bar names it without a project load) and who
    # queued the render. None when the server re-attached a job it did not start.
    bin_name: str = ""
    queued_by: Literal["user", "agent"] | None = None
    # running, but its progress has not moved for ``jobs.STALL_AFTER_S``: ComfyUI may be hung.
    # The feed got an alert line when it tripped; the bar says STALLED while it lasts
    stalled: bool = False
    # queued again after ComfyUI (or the machine, or this server) lost it; once only
    retried: bool = False


class CancelBody(BaseModel):
    """``POST .../jobs/{id}/cancel``: the agent says whether the user asked (``requested``), and
    why; the user's own click needs neither."""

    requested: bool = False
    reason: str = ""


class ComfyRestartBody(BaseModel):
    """``POST /api/comfy/restart``: the agent names the project it is acting for (its session and
    mode decide whether it may), whether the user asked, and why."""

    slug: str | None = None
    requested: bool = False
    reason: str = ""


class ComfyRestartOut(BaseModel):
    script: str
    requeued: list[str]  # the live renders stopped and queued again, to start when ComfyUI answers
    started: str  # utc, when the script was started
    log: str


ProjectCard.model_rebuild()  # resolves the forward reference to JobOut above


class BinDetail(BaseModel):
    bin: Bin
    media: list[Media]  # the gallery, newest first
    selected: str | None
    references: list[ReferenceOut]
    capacity: CapacityOut
    workflow: WorkflowInfo | None
    kind: Literal["image", "video"] | None  # the workflow's kind; None when the bin has none
    project_slug: str
    project_name: str
    active_jobs: list[JobOut] = []  # every live render on this bin, oldest first


class SeenOut(BaseModel):
    """The seen route's answer: which bins stopped marking the media as new."""

    media: str
    cleared: list[str]
    project_version: int


class SeenManyIn(BaseModel):
    media: list[str] = []
    all: bool = False  # every new marker in the project, whatever the bin or type


class SeenManyOut(BaseModel):
    cleared: list[str]
    project_version: int


class BinSeenOut(BaseModel):
    """The bin clear route's answer: the media that stopped being marked new."""

    bin: str
    cleared: list[str]
    project_version: int


class MediaPage(BaseModel):
    items: list[Media]
    next_cursor: str | None
    total: int


class CommandResultOut(BaseModel):
    change: Change | None
    generation: Generation | None
    warnings: list[dict[str, Any]]
    messages: list[Message]
    project_version: int
    # a propose_shots batch: the child generate changes and their renders, in item order
    changes: list[Change] = Field(default_factory=list)
    generations: list[Generation] = Field(default_factory=list)
    # the bin the command made or bound into (create_bin, duplicate_bin, start_from_media)
    bin: str | None = None
    sequence: str | None = None  # the sequence the command made (create_sequence, clone_sequence)
    post: str | None = None  # the post the command made (create_post)
    mode: str | None = None  # the session mode after the command (``mode_token``)


class ClosePendingOut(BaseModel):
    closed: int
    messages: list[Message]
    project_version: int


class InboxOut(BaseModel):
    messages: list[Message]
    cursor: int
    mode: str | None = None  # the session mode now (``mode_token``): the agent re-plans when it moved


class InterruptedOut(BaseModel):
    """A render that stopped because ComfyUI or the machine went away, with no take: re-run it
    (``recall --generate`` on its bin's last take, or ``generate``) or let it go."""

    generation: str
    bin: str
    bin_name: str = ""
    ts: str
    why: str = ""


class ResumeOut(BaseModel):
    """Everything a reconnecting agent needs in one read, so a resume after a crash or a reboot
    starts from the project's state, not from memory."""

    mode: str | None = None
    open_notes: list[Message] = Field(default_factory=list)
    jobs: list["JobOut"] = Field(default_factory=list)  # queued and running, on this server
    interrupted: list[InterruptedOut] = Field(default_factory=list)  # the last day's renders cut off with no take
    last_agent_post: Message | None = None
    rules: list[ProjectRule] = Field(default_factory=list)
    # sequence items the agent QC'd against the layers at another start frame: re-listen to them
    moved: list["MovedItemOut"] = Field(default_factory=list)


class MovedItemOut(BaseModel):
    sequence: str
    sequence_name: str
    bin: str
    name: str
    position: int
    reviewed_start_frame: int
    start_frame: int


class NotesOut(BaseModel):
    """The user's notes the live session still owes an answer to, oldest first."""

    notes: list[Message]


class BinRef(BaseModel):
    id: str
    name: str


class ShotNeedOut(BaseModel):
    """A reference the shot waits on: where it comes from, what it is, and whether that bin
    holds a take yet (``ready``, with the take it would be)."""

    bin: str | None
    bin_name: str
    what: str
    ready: bool
    media: str | None = None


class ShotRefOut(BaseModel):
    media: str
    type: MediaType
    label: str  # the reference bin's name: what the handle will be


class ShotOut(BaseModel):
    """One item of a propose_shots card as the feed shows it: the proposal, and after the decision
    the outcome (dropped, edited, the bin it made, the child change that rendered)."""

    index: int
    name: str
    shot: int | None = None  # the bin's permanent number, once the approval made (or named) it
    description: str
    has_prompt: bool  # the agent wrote a prompt apart from the one-liner: an edit holds the render
    workflow: str | None
    kind: str | None
    length_s: float | None
    quarter: bool
    draft: bool = False  # the workflow's own draft setting is on for this shot's render
    full_quality: bool  # the expensive tier (full-size video off the draft workflows): the card asks plainly
    takes: int  # takes the target bin already holds: with any, the shot is a regenerate
    held: bool  # approved for later: the render is saved, not running
    references: list[ShotRefOut]
    references_from_bin: bool = False  # the card named none: these are the bin's own, as bound now
    needs: list[ShotNeedOut] = []  # references the shot waits on, not yet generated when proposed
    dropped: bool
    edited: bool
    bin: BinRef | None
    change: str | None
    error: str | None


class RenderRef(BaseModel):
    """The render a generate change started, by id and last known status: a card shown before
    its take lands reads as rendering (the live job carries the progress), failed or cancelled."""

    generation: str
    status: GenerationStatus


class FeedEntry(BaseModel):
    """One line of the session feed: a message plus, for change and decision lines, the change it is about,
    the media that change produced or bound (for the card preview), and the bin it targets. A
    propose_shots change carries its items as ``shots``; a generate change its render as ``render``."""

    message: Message
    # a user note in the live session: what the agent did about it, or still open (work owed)
    answer: str | None = None
    open: bool = False
    change: Change | None = None
    media: Media | None = None
    bin: BinRef | None = None
    shots: list[ShotOut] | None = None
    render: RenderRef | None = None


class FeedOut(BaseModel):
    entries: list[FeedEntry]
    cursor: int


class UploadOut(BaseModel):
    media: Media
    bin: Bin
    change: Change | None
    project_version: int


class RemoveUploadOut(BaseModel):
    """The media left the bin; `deleted` says whether the upload itself was dropped as unused."""

    media: str
    bin: Bin
    deleted: bool
    change: Change | None
    project_version: int


class KeepFileBody(BaseModel):
    """Where a note's file goes when it becomes project media: a bin id, a bin name (found or
    minted), or neither for the next ``bin-n``."""

    model_config = ConfigDict(extra="forbid")
    bin: str | None = None
    bin_name: str | None = None


class RemoveFileOut(BaseModel):
    file: str
    deleted: bool


class AgentActivityOut(BaseModel):
    """The agent's last sign of life on this project: when, and what it said it was doing.
    ``idle_s`` is the server's clock, so the browser need not trust its own."""

    seen: str | None = None
    doing: str | None = None
    idle_s: float | None = None
    # the live answer to "is anyone on the live session": None when there is no live session.
    # The project detail's ``agent_gone`` is the same test frozen at the time of that fetch.
    gone: bool | None = None


class AgentActivityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doing: str


class AgentStatusOut(BaseModel):
    """Is an agent CLI talking to this server at all, on any project. ``available`` is the
    server's verdict (heard from within ``agent_activity.AVAILABLE_S``); the bar shows
    ``no agent`` when it is false and offers ``start`` when it is true."""

    available: bool
    seen: str | None = None
    idle_s: float | None = None


class HealthOut(BaseModel):
    ok: bool = True
    version: str
    projects_dir: str
    workflows_dir: str
    comfy_url: str = ""  # the ComfyUI renders go to ([comfyui] api_base): the bar's dot links to it


# -- prompt enhancement ----------------------------------------------------------


class EnhanceStatusOut(BaseModel):
    """Which enhancer engines can serve this project right now. Neither = no Enhance pill."""

    local: bool
    agent: bool
    detail: str | None = None  # why local is off, when it is (no workflow file, ComfyUI unreachable)


class EnhanceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str
    type: str = "image"


class EnhanceBody(BaseModel):
    """The prompt to rewrite, the workflow whose instructions apply, and the user's note."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["image", "video"]
    workflow: str = ""
    prompt: str
    note: str = ""
    engine: Literal["local", "agent"] = "local"
    references: list[EnhanceReference] = Field(default_factory=list)
    bin: str | None = None  # names the bin in the feed when the agent engine is asked
    # the app's own id for this request, so it can cancel the run while it waits; minted here when empty
    request: str = ""


class EnhanceCancelOut(BaseModel):
    """The cancel route's answer: the run named, and what ComfyUI was told."""

    request: str
    detail: str


class EnhanceRequestOut(BaseModel):
    """A rewrite the app is waiting on the agent for: the same two texts the local engine gets."""

    id: str
    kind: Literal["image", "video"]
    workflow: str
    system_prompt: str
    message: str
    created: str


class EnhanceReplyBody(BaseModel):
    """The agent's answer: the new prompt, or why it could not produce one."""

    model_config = ConfigDict(extra="forbid")
    prompt: str = ""
    error: str = ""


class EnhanceOut(BaseModel):
    prompt: str
    engine: Literal["local", "agent"]
    system_source: Literal["workflow", "default"]
    elapsed_s: float
    # reference tags the original prompt used that the rewrite left out: it lands anyway, warned
    dropped_tags: list[str] = []
