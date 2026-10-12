"""Execution events and an in-process publish/subscribe bus.

Every observable decision (planning, agent selection, task start/finish,
evaluation, failure diagnosis, impact analysis, replanning) is emitted as an
``Event``. The API persists events and streams them to the UI over SSE; tests
and benchmarks inspect them directly.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from famas.core.models import wall_time

EVENT_TYPES = {
    "run_started",
    "analysis_completed",
    "plan_created",
    "plan_validated",
    "agents_assigned",
    "task_started",
    "task_completed",
    "task_failed",
    "task_reused",
    "task_skipped",
    "wave_finished",
    "failure_diagnosed",
    "impact_analyzed",
    "replan_applied",
    "run_completed",
    "run_failed",
    "log",
}


class Event(BaseModel):
    seq: int
    run_id: str
    type: str
    t_ms: float  # run clock (virtual or real)
    wall: float = Field(default_factory=wall_time)
    task_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


Listener = Callable[[Event], Any]


class EventBus:
    """Per-run event log with fan-out to async subscribers and sync listeners."""

    def __init__(self, run_id: str, clock=None, keep_history: bool = True) -> None:
        self.run_id = run_id
        self.clock = clock
        self.keep_history = keep_history
        self.history: list[Event] = []
        self._seq = 0
        self._queues: list[asyncio.Queue] = []
        self._listeners: list[Listener] = []
        self.closed = False

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with contextlib.suppress(ValueError):
            self._queues.remove(q)

    def emit(self, type: str, task_id: str | None = None, **data: Any) -> Event:
        self._seq += 1
        ev = Event(
            seq=self._seq,
            run_id=self.run_id,
            type=type,
            t_ms=round(self.clock.now(), 3) if self.clock else 0.0,
            task_id=task_id,
            data=data,
        )
        if self.keep_history:
            self.history.append(ev)
        for fn in list(self._listeners):
            try:
                fn(ev)
            except Exception:  # listeners must never break execution
                pass
        for q in list(self._queues):
            q.put_nowait(ev)
        return ev

    def close(self) -> None:
        self.closed = True
        for q in list(self._queues):
            q.put_nowait(None)

    def of_type(self, *types: str) -> list[Event]:
        return [e for e in self.history if e.type in types]
