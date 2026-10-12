"""Content-addressed result cache keyed by a task's input fingerprint."""

from __future__ import annotations

from typing import Protocol

from famas.core.models import TaskResult


class ResultCache(Protocol):
    def get(self, fingerprint: str) -> TaskResult | None: ...

    def put(self, fingerprint: str, result: TaskResult) -> None: ...


class InMemoryResultCache:
    def __init__(self) -> None:
        self._store: dict[str, TaskResult] = {}

    def get(self, fingerprint: str) -> TaskResult | None:
        r = self._store.get(fingerprint)
        return r.model_copy(deep=True) if r else None

    def put(self, fingerprint: str, result: TaskResult) -> None:
        self._store[fingerprint] = result.model_copy(deep=True)

    def __len__(self) -> int:
        return len(self._store)


class NullCache:
    """Used by baselines that recompute everything (restart / full replanning)."""

    def get(self, fingerprint: str) -> TaskResult | None:
        return None

    def put(self, fingerprint: str, result: TaskResult) -> None:
        return None


class LayeredCache:
    """Run-local cache backed by an optional persistent cache (cross-run reuse)."""

    def __init__(self, local: ResultCache, persistent: ResultCache | None = None) -> None:
        self.local = local
        self.persistent = persistent

    def get(self, fingerprint: str) -> TaskResult | None:
        r = self.local.get(fingerprint)
        if r is None and self.persistent is not None:
            r = self.persistent.get(fingerprint)
            if r is not None:
                self.local.put(fingerprint, r)
        return r

    def put(self, fingerprint: str, result: TaskResult) -> None:
        self.local.put(fingerprint, result)
        if self.persistent is not None:
            self.persistent.put(fingerprint, result)
