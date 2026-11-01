"""Project persistence (design section 8).

One project file at ``projects/<slug>.json`` written atomically with throttled
backups in ``projects/_backups/``, plus three append-only logs under
``projects/<slug>-files/``: generations, changes, and messages. This store is the
only writer; the agent has no file access.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import BackupSettings
from .ids import PROJECT, new_id
from .models import SCHEMA_VERSION, Change, Generation, Message, Project
from .slug import handle_base, media_ext, slugify

GENERATIONS_LOG = "generations.jsonl"
CHANGES_LOG = "changes.jsonl"
MESSAGES_LOG = "messages.jsonl"
CHAT_DIR = "chat"
_CHAT_ID_RE = re.compile(r"^f_[0-9a-f]{8}$")


class SchemaVersionError(ValueError):
    """The file is not a THM (schema_version 3 to 6) project."""


# schema-3 files (shots and items) and schema-4 files (dot handles) are read and upgraded in
# place; anything older is 1.x
UPGRADABLE_VERSIONS = (3, 4, 5)

# the schema-4 prompt token: a dot, a slug, an optional dot plus digits (thm_core.slug.TOKEN_RE
# before the handle grew an extension and the prefix became @)
_LEGACY_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9\-.])\.([a-z0-9][a-z0-9-]*)(?:\.(\d+))?(?![A-Za-z0-9\-])")


class ProjectNotFound(FileNotFoundError):
    pass


@dataclass
class _LogCache:
    """One parsed log, valid while the file's (size, mtime_ns) still matches.

    ``records`` is the file in order; ``by_id`` is the later-line-wins view for the logs
    whose lines carry an id (changes, generations) and None for messages. Records handed
    out from here are shared: a caller that mutates one copies it first (``model_copy``)."""

    stat: tuple[int, int] | None
    records: list
    by_id: dict[str, object] | None


def _replace_with_retry(tmp: Path, path: Path, *, tries: int = 8, pause_s: float = 0.05) -> None:
    """``os.replace`` with a few retries: on Windows a reader that has the file open for an
    instant (another THM server on the same folder, an indexer) makes the rename fail with
    ``PermissionError``; it clears within milliseconds, and giving up would lose the save."""
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(pause_s * (attempt + 1))


def _peek_version(raw: dict) -> int | None:
    v = raw.get("schema_version")
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _is_1x(raw: dict) -> bool:
    """A 1.x project file: it keeps its cut under ``sequences`` and has no record dicts. A 2.0
    file with sequences (schema 6 and on) always carries ``bins_by_id``; a schema-3 file carries
    ``shots_by_id``."""
    return "sequences" in raw and not any(k in raw for k in ("bins_by_id", "shots_by_id", "items_by_id"))


def _readable(raw: dict) -> bool:
    version = _peek_version(raw)
    return (version == SCHEMA_VERSION or version in UPGRADABLE_VERSIONS) and not _is_1x(raw)


def check_schema_version(raw: dict, path: Path) -> None:
    version = _peek_version(raw)
    if _readable(raw):
        return
    if version is None or version < SCHEMA_VERSION or _is_1x(raw):
        shown = version if version is not None else "missing"
        raise SchemaVersionError(
            f"{path.name} is a 1.x project (schema_version {shown}); THM reads "
            f"schema_version {SCHEMA_VERSION} only. Open it in the Gradio app."
        )
    raise SchemaVersionError(
        f"{path.name} has schema_version {version}, newer than this THM core "
        f"(schema_version {SCHEMA_VERSION}). Update the app."
    )


def upgrade_v3(raw: dict) -> dict:
    """Schema 3 to 4, in place: every shot and every item becomes a bin.

    Ids are kept, so the append-only logs, media origins, references, and the output
    directories named after them keep resolving. A shot's takes become the bin's gallery and
    its selected take the bin's selected; an item's cover becomes its selected and it inherits
    the project's default workflow for the kind of media it holds. Reference rows lose their
    ``item`` key (``shot:<id>`` synthetic ids, which schema 3 persisted for a take with no item,
    become the bare id) and media origins fold ``shot``/``item`` into ``bin``. The record
    validators tolerate the old keys too; this pass is what makes the saved file clean.
    """
    defaults = raw.get("defaults") or {}
    media = raw.get("media_by_id") or {}
    bins: dict[str, dict] = {}
    for sid, s in (raw.pop("shots_by_id", None) or {}).items():
        bins[sid] = {
            "id": sid, "name": s.get("name", ""), "workflow": s.get("workflow", ""), "length_s": s.get("length_s"),
            "loras": s.get("loras") or [], "prompt": s.get("prompt", ""), "negative": s.get("negative", ""),
            "references": s.get("references") or [], "media": list(s.get("takes") or []),
            "selected": s.get("selected_take"), "counters": s.get("counters") or {},
            "description": "", "tags": [], "created": "",
        }
    for iid, it in (raw.pop("items_by_id", None) or {}).items():
        held = list(it.get("media") or [])
        types = {media[m].get("type") for m in held if m in media}
        kind = "video" if types == {"video"} else "image"
        cover = it.get("cover") or None
        bins[iid] = {
            "id": iid, "name": it.get("name", ""),
            "workflow": defaults.get("video_workflow" if kind == "video" else "image_workflow") or "",
            "length_s": defaults.get("video_length_s") if kind == "video" else None,
            "loras": [], "prompt": "", "negative": it.get("negative", ""), "references": [],
            "media": held, "selected": cover if cover in held else None, "counters": {},
            "description": it.get("description", ""), "tags": it.get("tags") or [], "created": it.get("created", ""),
        }
    raw.pop("shots", None)
    for b in bins.values():
        for r in b["references"]:
            old = str(r.pop("item", None) or r.get("bin") or "")
            if old.startswith("shot:"):
                old = old[5:]
            if old.startswith("media:"):
                mid = old[6:]
                old = next((bid for bid, bb in bins.items() if mid in bb["media"]), old)
            r["bin"] = old
    for m in media.values():
        o = m.get("origin") or {}
        if not o.get("bin"):
            o["bin"] = o.get("shot") or o.get("item")
        o.pop("shot", None)
        o.pop("item", None)
    counters = dict(raw.get("counters") or {})
    counters.pop("shot_video", None)
    counters.pop("shot_image", None)
    raw["counters"] = counters
    raw["bins_by_id"] = bins
    raw["schema_version"] = 4
    return raw


def upgrade_v4(raw: dict) -> dict:
    """Schema 4 to 5, in place: handles grow the media file's extension and prompt tokens
    change prefix, ``.mara`` to ``@mara.png``.

    Each bin's references are renumbered per (referenced bin, extension) in the order they
    sit, so a still and a clip that read ``mara`` and ``mara.2`` become ``mara.png`` and
    ``mara.mp4``. The bin's prompt and negative are rewritten for its own handles only; a dot
    word nobody was bound to is left as typed.
    """
    media = raw.get("media_by_id") or {}
    for b in (raw.get("bins_by_id") or {}).values():
        refs = b.get("references") or []
        counts: dict[tuple[str, str], int] = {}
        mapping: dict[str, str] = {}
        for r in refs:
            old = str(r.get("handle") or "")
            m = media.get(r.get("media")) or {}
            ext = media_ext(m.get("path"))
            key = (str(r.get("bin") or ""), ext)
            counts[key] = counts.get(key, 0) + 1
            n = counts[key]
            base = f"{handle_base(old)}.{ext}"
            new = base if n == 1 else f"{base}.{n}"
            r["handle"] = new
            mapping.setdefault(old, new)

        def _sub(m: re.Match) -> str:
            handle = m.group(1) + (f".{m.group(2)}" if m.group(2) else "")
            return f"@{mapping[handle]}" if handle in mapping else m.group(0)

        for field_ in ("prompt", "negative"):
            text = b.get(field_) or ""
            if text and mapping:
                b[field_] = _LEGACY_TOKEN_RE.sub(_sub, text)
    raw["schema_version"] = 5
    return raw


# the accident this upgrade undoes: the agent's "P18 please stop" / "S03 hood urban bus" names,
# a series letter and number glued to the title
_PREFIXED_NAME_RE = re.compile(r"^[PS](\d{1,3})([a-z])?\s+(.+)$")


def upgrade_v5(raw: dict) -> dict:
    """Schema 5 to 6, in place: a bin's name becomes its title alone and its handle base is a
    frozen ``slug``; a name that carried the agent's series prefix ("P18 please stop") becomes
    shot 18 titled "please stop".

    The slug is set to exactly what every handle already derived from (``slugify(name)``), so
    no prompt token changes: old shots keep ``@p18-please-stop.png``, and only bins made from
    here on read ``@shot-19.png``. A lettered variant (``P21b``) or a second bin with the same
    number takes the next number above the highest; the project's ``shot`` counter lands there.
    """
    bins = raw.get("bins_by_id") or {}
    for b in bins.values():
        if not b.get("slug"):
            b["slug"] = slugify(str(b.get("name") or ""))
    parsed: list[tuple[dict, str, int, bool]] = []
    for b in bins.values():
        m = _PREFIXED_NAME_RE.match(str(b.get("name") or "").strip())
        if m and b.get("shot") is None:
            parsed.append((b, m.group(0)[0], int(m.group(1)), bool(m.group(2))))
            b["name"] = m.group(3).strip()
    taken = {int(b["shot"]) for b in bins.values() if isinstance(b.get("shot"), int)}
    high = max(taken, default=0)
    # two series side by side (P for the narrative, S for an earlier list of test clips): the
    # P numbers are the ones the user knows, so they are kept and every S bin is numbered above
    # them; a lone series keeps its numbers. Lettered variants and duplicates go above the max too
    series = {letter for _, letter, _, _ in parsed}
    keep = "P" if "P" in series else "S"
    order = sorted(parsed, key=lambda t: (t[1] != keep or t[3], t[2]))
    for b, letter, n, lettered in order:
        if letter != keep or lettered or n in taken:
            high = max(high, max(taken, default=0)) + 1
            n = high
        b["shot"] = n
        taken.add(n)
        high = max(high, n)
    counters = raw.get("counters") or {}
    if high:
        counters["shot"] = max(int(counters.get("shot") or 0), high)
        raw["counters"] = counters
    raw["schema_version"] = SCHEMA_VERSION
    return raw


def number_takes(project: Project, generations: list[Generation] = ()) -> None:
    """Takes landed before numbers were stamped get theirs once, from the generation log: the
    order every output ever landed in the bin, deleted ones included, so a take that was the
    ninth is take 9 even with earlier ones gone. A take the log does not know (imported, or a
    log that was lost) takes the next number. From then on the bin's counter hands out numbers
    and a delete never shifts the rest. Saved with the project the next time anything writes it."""
    history: dict[str, list[str]] = {}
    for gen in generations:
        if gen.bin and gen.status == "done":
            seen = history.setdefault(gen.bin, [])
            seen.extend(o for o in gen.outputs if o not in seen)
    for bin_ in project.bins_by_id.values():
        takes = [
            project.media_by_id[t] for t in bin_.media
            if t in project.media_by_id
            and project.media_by_id[t].origin.type == "generation"
            and project.media_by_id[t].origin.bin == bin_.id
        ]
        landed = history.get(bin_.id, [])
        highest = max([m.take or 0 for m in takes] + [len(landed), int(bin_.counters.get("take", 0))], default=0)
        for m in takes:
            if m.take is not None:
                continue
            if m.id in landed:
                m.take = landed.index(m.id) + 1
            else:
                highest += 1
                m.take = highest
            highest = max(highest, m.take)
        if takes or landed:
            bin_.counters["take"] = highest


class ProjectStore:
    def __init__(
        self,
        projects_dir: str | Path,
        *,
        backups: BackupSettings | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.projects_dir = Path(projects_dir)
        self.backups = backups or BackupSettings()
        self._clock = clock
        self._last_backup: dict[str, float] = {}
        self._logs: dict[Path, _LogCache] = {}

    # -- paths ---------------------------------------------------------------

    @staticmethod
    def slug_of(project: Project | str) -> str:
        if isinstance(project, str):
            return project
        if not project.slug:
            raise ValueError("project has no slug; use ProjectStore.new_project or set project.slug")
        return project.slug

    def project_path(self, project: Project | str) -> Path:
        return self.projects_dir / f"{self.slug_of(project)}.json"

    def files_dir(self, project: Project | str) -> Path:
        return self.projects_dir / f"{self.slug_of(project)}-files"

    def backups_dir(self) -> Path:
        return self.projects_dir / "_backups"

    def chat_dir(self, project: Project | str) -> Path:
        """Where the files a message carries live: beside the logs, in no bin."""
        return self.files_dir(project) / CHAT_DIR

    def chat_file(self, project: Project | str, file_id: str) -> Path | None:
        """The file behind a message-file id (``<id>_<name>`` on disk), or None."""
        if not _CHAT_ID_RE.match(str(file_id)):
            return None
        return next((p for p in self.chat_dir(project).glob(f"{file_id}_*") if p.is_file()), None)

    # -- project file --------------------------------------------------------

    def new_project(self, name: str, *, slug: str | None = None, **fields) -> Project:
        """Build an unsaved project with a fresh id and a slug derived from the name."""
        chosen = slugify(slug or name)
        base, n = chosen, 1
        while self.project_path(chosen).exists():
            n += 1
            chosen = f"{base}-{n}"
        return Project(id=new_id(PROJECT), name=name, slug=chosen, **fields)

    def list_projects(self) -> list[str]:
        """Slugs of THM project files in the projects dir; 1.x files are skipped."""
        out: list[str] = []
        for path in sorted(self.projects_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict) and _readable(raw):
                out.append(path.stem)
        return out

    def load(self, slug: str) -> Project:
        path = self.project_path(slug)
        if not path.is_file():
            raise ProjectNotFound(str(path))
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SchemaVersionError(f"{path.name} is not a project object")
        check_schema_version(raw, path)
        version = _peek_version(raw)
        if version == 3:
            upgrade_v3(raw)
        if version in (3, 4):
            upgrade_v4(raw)
        if version in (3, 4, 5):
            upgrade_v5(raw)
        project = Project.model_validate(raw)
        if not project.slug:
            project.slug = slug
        # early phase 5 files kept the brief on the session; it belongs to the project
        legacy = raw.get("agent_session") or {}
        if not project.brief and isinstance(legacy, dict) and str(legacy.get("brief") or "").strip():
            project.brief = str(legacy["brief"]).strip()
        number_takes(project, self.read_generations(project))
        return project

    def delete_project(self, slug: str) -> None:
        """Remove the project file and its ``<slug>-files/`` tree. Backups under ``_backups/``
        stay as the recovery net. Missing pieces are fine; an unknown slug raises."""
        path = self.project_path(slug)
        if not path.is_file():
            raise ProjectNotFound(str(path))
        path.unlink()
        shutil.rmtree(self.files_dir(slug), ignore_errors=True)
        self._last_backup.pop(slug, None)
        for name in (GENERATIONS_LOG, CHANGES_LOG, MESSAGES_LOG):
            self._logs.pop(self._log_path(slug, name), None)

    def save(self, project: Project) -> Path:
        path = self.project_path(project)
        data = project.model_dump(mode="json")
        self._atomic_write(path, data)
        self.files_dir(project).mkdir(parents=True, exist_ok=True)
        return path

    def _atomic_write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, ensure_ascii=False)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            self._maybe_backup(path)
            _replace_with_retry(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def _maybe_backup(self, path: Path) -> None:
        if not path.exists():
            return
        now = self._clock()
        last = self._last_backup.get(str(path), float("-inf"))
        if now - last <= self.backups.throttle_seconds:
            return
        backup_dir = self.backups_dir()
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = backup_dir / f"{path.stem}.json.backup.{stamp}"
        n = 1
        while candidate.exists():
            n += 1
            candidate = backup_dir / f"{path.stem}.json.backup.{stamp}-{n}"
        shutil.copy2(path, candidate)
        self._last_backup[str(path)] = now
        existing = sorted(backup_dir.glob(f"{path.stem}.json.backup.*"))
        keep = max(int(self.backups.retention_count), 0)
        for old in existing[: max(len(existing) - keep, 0)]:
            try:
                old.unlink()
            except OSError:
                pass

    # -- append-only logs ----------------------------------------------------

    def _log_path(self, project: Project | str, name: str) -> Path:
        return self.files_dir(project) / name

    @staticmethod
    def _log_stat(path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_size, st.st_mtime_ns

    def _append(self, project: Project | str, name: str, record) -> None:
        path = self._log_path(project, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        cache = self._logs.get(path)
        if cache is not None and cache.stat != self._log_stat(path):
            # someone else wrote the file since we parsed it: the next read starts over
            self._logs.pop(path, None)
            cache = None
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if cache is not None:
            kept = record.model_copy()
            cache.records.append(kept)
            if cache.by_id is not None:
                cache.by_id[kept.id] = kept
            cache.stat = self._log_stat(path)

    def _log(self, project: Project | str, name: str, model, keyed: bool) -> _LogCache:
        """The parsed log, re-read only when the file on disk no longer matches the cache.

        The logs are append-only and this store is their only writer while the server runs,
        so a parse happens once per server lifetime per log; every read after that is a copy of
        the cached containers. A file rewritten underneath (a seed script, a hand edit) changes
        its size or mtime and is parsed again."""
        path = self._log_path(project, name)
        stat = self._log_stat(path)
        cache = self._logs.get(path)
        if cache is not None and cache.stat == stat:
            return cache
        records = [model.model_validate(d) for d in self._read_lines(project, name)]
        by_id: dict[str, object] | None = None
        if keyed:
            by_id = {}
            for rec in records:
                by_id[rec.id] = rec
        cache = self._logs[path] = _LogCache(stat=stat, records=records, by_id=by_id)
        return cache

    def _read_lines(self, project: Project | str, name: str) -> list[dict]:
        path = self._log_path(project, name)
        if not path.is_file():
            return []
        out: list[dict] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def append_generation(self, project: Project | str, generation: Generation) -> None:
        self._append(project, GENERATIONS_LOG, generation)

    def read_generations(self, project: Project | str) -> list[Generation]:
        """Generations in first-seen order; a later line with the same id wins (status transitions)."""
        return list(self._log(project, GENERATIONS_LOG, Generation, keyed=True).by_id.values())

    def read_generation(self, project: Project | str, gen_id: str) -> Generation | None:
        return self._log(project, GENERATIONS_LOG, Generation, keyed=True).by_id.get(gen_id)

    def append_change(self, project: Project | str, change: Change) -> None:
        self._append(project, CHANGES_LOG, change)

    def read_changes(self, project: Project | str) -> dict[str, Change]:
        """Changes by id in first-seen order; a later line with the same id wins."""
        return dict(self._log(project, CHANGES_LOG, Change, keyed=True).by_id)

    def append_message(self, project: Project | str, message: Message) -> None:
        self._append(project, MESSAGES_LOG, message)

    def read_messages(self, project: Project | str) -> list[Message]:
        return list(self._log(project, MESSAGES_LOG, Message, keyed=False).records)

    def message_cursor(self, project: Project | str) -> int:
        """Current end of the message log, as a line count."""
        return len(self._log(project, MESSAGES_LOG, Message, keyed=False).records)

    def read_inbox(self, project: Project | str, cursor: int | None) -> tuple[list[Message], int]:
        """Messages past ``cursor`` (a line offset) and the new cursor. Nothing is cleared."""
        records = self._log(project, MESSAGES_LOG, Message, keyed=False).records
        start = max(int(cursor or 0), 0)
        return list(records[start:]), len(records)
