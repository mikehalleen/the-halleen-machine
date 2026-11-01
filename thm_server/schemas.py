"""Response and request shapes that are not the raw records.

Records (``Bin``, ``Media``, ``Change``, ``Message``, ``Generation``) are returned as they
are. These models cover the derived and paginated views the screens need, and the request
bodies, which forbid unknown keys.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from thm_core import AgentSession, Bin, Change, Generation, Media, Message
from thm_core.models import ComfySettings, Lora, MediaType, ProjectDefaults


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
    action: Literal["approve", "reject", "reopen", "close"]
    note: str | None = None
    items: list[ShotDecision] | None = None  # approve of a propose_shots card only


class ClipBody(BaseModel):
    """A segment cut or a still frame lifted from an existing video media (design's "clip and
    still"). Lands in ``imports/`` and joins a bin the same way an upload does."""

    model_config = ConfigDict(extra="forbid")
    source: str
    mode: Literal["segment", "still"]
    start_s: float | None = None
    end_s: float | None = None
    at_s: float | None = None
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
    blocker: Message | None = None
    pending_changes: int = 0
    # live generations in this server process, so the project list shows which are busy
    generating: int = 0
    generating_job: "JobOut | None" = None  # the first live one, for its phase and step count


class BinCard(Bin):
    """A bin in a list: what it holds, how many takes, and the media it leads with, so the
    explorer filters and draws without loading media."""

    media_types: list[MediaType] = Field(default_factory=list)
    take_count: int = 0
    selected_media: Media | None = None
    kind: Literal["image", "video"] | None = None  # the workflow's kind; None when it has none
    generating: list[str] = Field(default_factory=list)  # live generation ids, so the explorer can say so


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
    configured: bool = False
    agent_session: AgentSession | None
    bins: list[BinCard]
    tags: list[str]  # the project's vocabulary: the defaults, then every tag a bin carries
    media_count: int
    pending_changes: int
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
    kind: Literal["image", "video"] = "image"
    error: str | None = None


class LoraFile(BaseModel):
    """One file under ``[paths] loras``; ``name`` is what goes in a ``<lora:name:strength>`` tag."""

    name: str
    size: int


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


class ClosePendingOut(BaseModel):
    closed: int
    messages: list[Message]
    project_version: int


class InboxOut(BaseModel):
    messages: list[Message]
    cursor: int


class BinRef(BaseModel):
    id: str
    name: str


class ShotRefOut(BaseModel):
    media: str
    type: MediaType
    label: str  # the reference bin's name: what the handle will be


class ShotOut(BaseModel):
    """One item of a propose_shots card as the feed shows it: the proposal, and after the decision
    the outcome (dropped, edited, the bin it made, the child change that rendered)."""

    index: int
    name: str
    description: str
    has_prompt: bool  # the agent wrote a prompt apart from the one-liner: an edit holds the render
    workflow: str | None
    kind: str | None
    length_s: float | None
    quarter: bool
    references: list[ShotRefOut]
    dropped: bool
    edited: bool
    bin: BinRef | None
    change: str | None
    error: str | None


class FeedEntry(BaseModel):
    """One line of the session feed: a message plus, for change and decision lines, the change it is about,
    the media that change produced or bound (for the card preview), and the bin it targets. A
    propose_shots change carries its items as ``shots``."""

    message: Message
    change: Change | None = None
    media: Media | None = None
    bin: BinRef | None = None
    shots: list[ShotOut] | None = None


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


class AgentActivityOut(BaseModel):
    """The agent's last sign of life on this project: when, and what it said it was doing.
    ``idle_s`` is the server's clock, so the browser need not trust its own."""

    seen: str | None = None
    doing: str | None = None
    idle_s: float | None = None


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
