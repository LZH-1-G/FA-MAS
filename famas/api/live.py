"""Live view of an executing run, folded from its event stream.

The dispatcher mutates the task DAG inside a LangGraph node, so the API keeps
its own projection: plan/replan events carry the full DAG view, and task events
update node status, agent, attempts and recovery tags in between.
"""

from __future__ import annotations

from typing import Any

from famas.core.events import Event


class LiveRunView:
    def __init__(self, run_id: str, request: str, config: dict[str, Any]) -> None:
        self.run_id = run_id
        self.request = request
        self.config = config
        self.status = "running"
        self.error: str | None = None
        self.analysis: dict | None = None
        self.view: dict[str, Any] = {"nodes": [], "edges": []}
        self.task_info: dict[str, dict[str, Any]] = {}
        self.replans: list[dict] = []
        self.metrics: dict | None = None
        self.running: dict[str, str] = {}
        self.t_ms = 0.0
        self.last_seq = 0

    def _node(self, task_id: str) -> dict | None:
        return next((n for n in self.view.get("nodes", []) if n["id"] == task_id), None)

    def apply(self, ev: Event) -> None:
        self.last_seq = ev.seq
        self.t_ms = ev.t_ms
        d = ev.data
        info = self.task_info.setdefault(ev.task_id, {}) if ev.task_id else None
        if ev.type == "analysis_completed":
            self.analysis = d.get("analysis")
        elif ev.type in ("plan_created", "replan_applied") and d.get("view"):
            self.view = d["view"]
            if ev.type == "replan_applied":
                self.replans.append(d["record"])
        elif ev.type == "agents_assigned":
            for tid, agent in d.get("assignments", {}).items():
                n = self._node(tid)
                if n:
                    n["agent"] = agent
                ti = self.task_info.setdefault(tid, {})
                if d.get("rationale", {}).get(tid):
                    ti["selection"] = d["rationale"][tid]
        elif ev.type == "task_started":
            n = self._node(ev.task_id)
            if n:
                n.update(
                    status="running", agent=d.get("agent"), attempts=d.get("attempt"), started_ms=ev.t_ms
                )
            self.running[ev.task_id] = d.get("agent")
        elif ev.type == "task_completed":
            n = self._node(ev.task_id)
            if n:
                n.update(status="completed", finished_ms=ev.t_ms)
            self.running.pop(ev.task_id, None)
            info.update(
                preview=d.get("preview"),
                checks=d.get("checks"),
                sources=d.get("sources"),
                tokens=d.get("tokens"),
                duration_ms=d.get("duration_ms"),
                score=d.get("score"),
                agent=d.get("agent"),
            )
        elif ev.type == "task_failed":
            n = self._node(ev.task_id)
            if n:
                n.update(status="failed", finished_ms=ev.t_ms)
            self.running.pop(ev.task_id, None)
            info.setdefault("failures", []).append(
                {k: d.get(k) for k in ("agent", "attempt", "error_type", "message", "checks", "issues")}
            )
        elif ev.type == "task_reused":
            n = self._node(ev.task_id)
            if n:
                n.update(status="completed", tag="cache_hit")
            info["reused_via"] = d.get("how")
        elif ev.type == "failure_diagnosed":
            info.setdefault("diagnoses", []).append(d.get("diagnosis"))
        elif ev.type in ("run_completed", "run_failed"):
            self.status = "completed" if d.get("success") else "failed"
            self.error = d.get("error")
            self.metrics = d.get("metrics")
            if d.get("view"):
                self.view = d["view"]
            self.running = {}

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.run_id,
            "request": self.request,
            "status": self.status,
            "error": self.error,
            "config": self.config,
            "analysis": self.analysis,
            "view": self.view,
            "replans": self.replans,
            "metrics": self.metrics,
            "running": self.running,
            "t_ms": self.t_ms,
            "last_seq": self.last_seq,
            "live": True,
        }
