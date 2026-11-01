"""In-memory project cache with per-slug locks and a version counter.

A project is loaded on first request and kept; the file's mtime is checked on
every ``get`` so a hand edit on disk is picked up. The version counter starts at
1 on first load, increments on reload and on ``touch`` (after the server's own
save), and is what the SSE stream sends as its event id. Versions are not
persisted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from thm_core import Project, ProjectNotFound, ProjectStore


@dataclass
class _Entry:
    project: Project
    mtime_ns: int


class ProjectRegistry:
    def __init__(self, store: ProjectStore):
        self.store = store
        self._cache: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._versions: dict[str, int] = {}

    def lock(self, slug: str) -> asyncio.Lock:
        lock = self._locks.get(slug)
        if lock is None:
            lock = self._locks[slug] = asyncio.Lock()
        return lock

    def _stat_ns(self, slug: str) -> int:
        path = self.store.project_path(slug)
        try:
            return path.stat().st_mtime_ns
        except FileNotFoundError:
            self._cache.pop(slug, None)
            raise ProjectNotFound(str(path)) from None

    def get(self, slug: str) -> Project:
        mtime = self._stat_ns(slug)
        entry = self._cache.get(slug)
        if entry is not None and (entry.mtime_ns == mtime or self.lock(slug).locked()):
            # While a write holds the lock, the cached object is the truth and the file may be
            # half-way through a multi-save command (an upload that makes its bin first): a
            # reader that reloaded here would put that half-written state in the cache, the
            # writer's ``touch`` would bless it, and the next write would save it over the
            # writer's own changes. Hand edits on disk are picked up once the lock is free.
            return entry.project
        project = self.store.load(slug)
        self._versions[slug] = self._versions.get(slug, 0) + 1
        self._cache[slug] = _Entry(project, mtime)
        return project

    def version(self, slug: str) -> int:
        self.get(slug)
        return self._versions[slug]

    def touch(self, slug: str) -> int:
        """Call after the server's own save: refresh the cached mtime and bump the version."""
        entry = self._cache.get(slug)
        if entry is not None:
            try:
                entry.mtime_ns = self.store.project_path(slug).stat().st_mtime_ns
            except FileNotFoundError:
                self._cache.pop(slug, None)
        self._versions[slug] = self._versions.get(slug, 0) + 1
        return self._versions[slug]

    def invalidate(self, slug: str | None = None) -> None:
        if slug is None:
            self._cache.clear()
        else:
            self._cache.pop(slug, None)
