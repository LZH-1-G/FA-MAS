"""Background run management for the API: start runs, track live state, persist results."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from famas.api.live import LiveRunView
from famas.config import Settings
from famas.core.events import EventBus
from famas.core.models import RunConfig, new_id
from famas.orchestration.engine import Engine
from famas.persistence.store import Store
from famas.planning.dag import TaskGraph


@dataclass
class ActiveRun:
    run_id: str
    bus: EventBus
    view: LiveRunView
    task: asyncio.Task | None = None


def _task_detail_from_graph(graph: dict, task_id: str) -> dict[str, Any] | None:
    for t in graph.get("tasks", []):
        if t["id"] != task_id:
            continue
        failures = []
        for f in t.get("failures", []):
            f = dict(f)
            f.pop("partial_result", None)
            failures.append(f)
        result = t.get("result") or {}
        return {
            "id": t["id"],
            "name": t["name"],
            "description": t["description"],
            "type": t["task_type"],
            "status": t["status"],
            "agent": t.get("assigned_agent"),
            "dependencies": t.get("dependencies", []),
            "inputs": t.get("inputs", {}),
            "covers": t.get("covers", []),
            "attempts": t.get("attempts", 0),
            "feedback": t.get("feedback", []),
            "excluded_agents": t.get("excluded_agents", []),
            "tag": t.get("recovery_tag"),
            "selection": t.get("selection"),
            "evaluation": t.get("evaluation"),
            "failures": failures,
            "result": {
                "content": result.get("content"),
                "agent_id": result.get("agent_id"),
                "tokens_in": result.get("tokens_in"),
                "tokens_out": result.get("tokens_out"),
                "cost": result.get("cost"),
                "duration_ms": result.get("duration_ms"),
                "sources": result.get("sources", []),
                "input_fingerprint": result.get("input_fingerprint"),
                "output_fingerprint": result.get("output_fingerprint"),
            }
            if result
            else None,
        }
    return None


class RunManager:
    def __init__(self, engine: Engine, store: Store, settings: Settings) -> None:
        self.engine = engine
        self.store = store
        self.settings = settings
        self.active: dict[str, ActiveRun] = {}

    async def start(self, request: str, config: RunConfig) -> str:
        run_id = new_id("run_")
        cfg = config.model_dump(mode="json")
        bus = EventBus(run_id)
        view = LiveRunView(run_id, request, cfg)
        bus.add_listener(view.apply)
        bus.add_listener(self.store.add_event)
        self.store.create_run(run_id, request, cfg)
        active = ActiveRun(run_id, bus, view)
        self.active[run_id] = active
        active.task = asyncio.create_task(self._execute(active, request, config))
        return run_id

    async def _execute(self, active: ActiveRun, request: str, config: RunConfig) -> None:
        try:
            outcome = await self.engine.run(request, config, run_id=active.run_id, events=active.bus)
            self.store.update_run(
                active.run_id,
                status=outcome.status,
                error=outcome.error,
                analysis=outcome.analysis.model_dump(mode="json") if outcome.analysis else None,
                graph=outcome.graph,
                metrics=outcome.metrics.model_dump(mode="json"),
                replans=[r.model_dump(mode="json") for r in outcome.replans],
                final_output=outcome.final_output,
                finished_at=time.time(),
            )
            if self.engine.stats is not None:
                self.store.save_agent_stats(self.engine.stats.snapshot())
        except Exception as e:  # pragma: no cover - defensive
            self.store.update_run(
                active.run_id, status="failed", error=f"{type(e).__name__}: {e}", finished_at=time.time()
            )
        finally:
            await asyncio.sleep(2.0)  # let SSE clients drain the final events
            self.active.pop(active.run_id, None)

    # ----------------------------------------------------------- queries
    def snapshot(self, run_id: str) -> dict[str, Any] | None:
        active = self.active.get(run_id)
        row = self.store.get_run(run_id)
        if active is not None and (row is None or row["status"] == "running"):
            return active.view.snapshot()
        if row is None:
            return None
        graph = row.get("graph") or {}
        view = TaskGraph.from_dict(graph).view() if graph else {"nodes": [], "edges": []}
        return {
            "id": row["id"],
            "request": row["request"],
            "status": row["status"],
            "error": row.get("error"),
            "config": row.get("config"),
            "analysis": row.get("analysis"),
            "view": view,
            "replans": row.get("replans") or [],
            "metrics": row.get("metrics"),
            "running": {},
            "final_output": row.get("final_output"),
            "created_at": row.get("created_at"),
            "finished_at": row.get("finished_at"),
            "live": False,
        }

    def task_detail(self, run_id: str, task_id: str) -> dict[str, Any] | None:
        row = self.store.get_run(run_id)
        if row and row.get("graph"):
            return _task_detail_from_graph(row["graph"], task_id)
        active = self.active.get(run_id)
        if active is None:
            return None
        node = next((n for n in active.view.view.get("nodes", []) if n["id"] == task_id), None)
        if node is None:
            return None
        return {**node, **active.view.task_info.get(task_id, {}), "live": True}

    async def stream(self, run_id: str, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        active = self.active.get(run_id)
        queue = active.bus.subscribe() if active else None
        last = after
        try:
            for row in self.store.get_events(run_id, after):
                last = row["seq"]
                yield row
            if queue is None:
                return
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield {"type": "ping", "seq": last}
                    continue
                if ev is None:
                    return
                if ev.seq <= last:
                    continue
                last = ev.seq
                yield ev.model_dump(mode="json")
        finally:
            if active and queue is not None:
                active.bus.unsubscribe(queue)

    async def shutdown(self) -> None:
        for a in list(self.active.values()):
            if a.task:
                a.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await a.task
