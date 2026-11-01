"""The Halleen Machine 2.0 core: records, store, commands, resolver, assembler.

No UI, no server, no gradio. The 1.x app in ``src/`` imports the capability
scanner from here through a shim; nothing here imports ``src/``.
"""

from .assembler import (
    API_TOKEN_NOTATION,
    AssembledPrompt,
    AssemblyWarning,
    Capacity,
    TokenProfile,
    assemble,
    merge_negatives,
    derive_token_profile,
    load_token_profile,
    merge_token_profiles,
    workflow_capacity,
)
from .clipper import extract_gif, extract_segment, extract_still
from .commands import (
    COMMANDS,
    Command,
    CommandContext,
    CommandRefused,
    CommandResult,
    Env,
    check_inbox_gate,
    check_session_gate,
    close_pending,
    decide,
    execute,
    parse_command,
)
from .config import BackupSettings, CoreSettings, load_core_settings
from .media import (
    adopt_media,
    ensure_thumbnails,
    media_type_for,
    probe_audio,
    probe_image,
    probe_video,
    sha256_file,
    thumb_cache_dir,
    thumb_paths,
)
from .models import (
    SCHEMA_VERSION,
    AgentSession,
    Change,
    Decision,
    Bin,
    Generation,
    Kind,
    Media,
    MediaOrigin,
    Message,
    MessageContext,
    MessageFile,
    Post,
    Project,
    QcMark,
    Reference,
    Sequence,
    SequenceItem,
)
from .resolver import ResolvedReference, resolve_references
from .runner import (
    ComfyTarget,
    JobPaths,
    LiveProgress,
    RunnerStatus,
    build_generation_spec,
    filter_outputs,
    job_paths,
    land_outputs,
    log_tail,
    map_status,
    read_status,
    resolve_comfy,
    runner_script,
)
from .slug import assign_handle, find_tokens, media_ext, rename_bin_handles, renumber_handles, rewrite_tokens, slugify
from .store import ProjectNotFound, ProjectStore, SchemaVersionError, upgrade_v3, upgrade_v4, upgrade_v5

__all__ = [
    "API_TOKEN_NOTATION", "AssembledPrompt", "AssemblyWarning", "Capacity", "TokenProfile", "assemble", "merge_negatives",
    "derive_token_profile", "load_token_profile", "merge_token_profiles", "workflow_capacity",
    "extract_gif", "extract_segment", "extract_still",
    "COMMANDS", "Command", "CommandContext", "CommandRefused", "CommandResult", "Env",
    "check_inbox_gate", "check_session_gate", "close_pending", "decide", "execute", "parse_command",
    "BackupSettings", "CoreSettings", "load_core_settings",
    "adopt_media", "ensure_thumbnails", "media_type_for", "probe_audio", "probe_image", "probe_video", "sha256_file",
    "thumb_cache_dir", "thumb_paths",
    "SCHEMA_VERSION", "AgentSession", "Bin", "Change", "Decision", "Generation", "Kind", "Media", "MediaOrigin",
    "Message", "MessageContext", "MessageFile", "Post", "Project", "QcMark", "Reference",
    "ResolvedReference", "resolve_references",
    "ComfyTarget", "JobPaths", "LiveProgress", "RunnerStatus", "build_generation_spec", "filter_outputs", "job_paths",
    "land_outputs", "log_tail", "map_status", "read_status", "resolve_comfy", "runner_script",
    "assign_handle", "find_tokens", "media_ext", "rename_bin_handles", "renumber_handles", "rewrite_tokens", "slugify",
    "ProjectNotFound", "ProjectStore", "SchemaVersionError", "upgrade_v3", "upgrade_v4", "upgrade_v5",
]
