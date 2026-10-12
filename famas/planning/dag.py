"""Task dependency graph (DAG) built on NetworkX.

The task DAG is deliberately separate from the LangGraph workflow: LangGraph
orchestrates *phases* of the system (analyse, plan, execute, recover), while
this graph describes the *work* - which subtasks exist, what they depend on,
and the state of each one. Recovery edits this graph in place.

Invariant: ``Task.dependencies`` is the single source of truth; the NetworkX
graph is rebuilt from it on every structural mutation, so the two can never
drift apart.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import networkx as nx

from famas.core.fingerprint import spec_fingerprint, stable_hash
from famas.core.models import TERMINAL_OK, Task, TaskStatus


class TaskGraph:
    def __init__(self, tasks: Iterable[Task] | None = None) -> None:
        self._tasks: dict[str, Task] = {}
        self._g = nx.DiGraph()
        self.version = 0
        for t in tasks or []:
            self._tasks[t.id] = t
        self._rebuild()

    # ------------------------------------------------------------------ basics
    def _rebuild(self) -> None:
        g = nx.DiGraph()
        for tid in self._tasks:
            g.add_node(tid)
        for tid, t in self._tasks.items():
            for dep in t.dependencies:
                if dep in self._tasks:
                    g.add_edge(dep, tid)
        self._g = g
        self.version += 1

    @property
    def nx(self) -> nx.DiGraph:
        return self._g

    @property
    def tasks(self) -> dict[str, Task]:
        return self._tasks

    def __contains__(self, task_id: str) -> bool:
        return task_id in self._tasks

    def __len__(self) -> int:
        return len(self._tasks)

    def __iter__(self):
        return iter(self._tasks.values())

    def get(self, task_id: str) -> Task:
        return self._tasks[task_id]

    def ids(self) -> list[str]:
        return list(self._tasks)

    # --------------------------------------------------------------- mutation
    def add_task(self, task: Task) -> None:
        if task.id in self._tasks:
            raise ValueError(f"duplicate task id {task.id!r}")
        self._tasks[task.id] = task
        self._rebuild()

    def remove_task(self, task_id: str) -> Task:
        task = self._tasks.pop(task_id)
        for t in self._tasks.values():
            if task_id in t.dependencies:
                t.dependencies = [d for d in t.dependencies if d != task_id]
        self._rebuild()
        return task

    def add_dependency(self, task_id: str, dep_id: str) -> None:
        if dep_id not in self._tasks or task_id not in self._tasks:
            raise KeyError(f"unknown task in edge {dep_id}->{task_id}")
        if dep_id == task_id or task_id in self.ancestors(dep_id) or dep_id in self.descendants(task_id):
            raise ValueError(f"edge {dep_id}->{task_id} would create a cycle")
        t = self._tasks[task_id]
        if dep_id not in t.dependencies:
            t.dependencies.append(dep_id)
            self._rebuild()

    def remove_dependency(self, task_id: str, dep_id: str) -> None:
        t = self._tasks[task_id]
        if dep_id in t.dependencies:
            t.dependencies = [d for d in t.dependencies if d != dep_id]
            self._rebuild()

    def set_dependencies(self, task_id: str, deps: list[str]) -> None:
        self._tasks[task_id].dependencies = list(dict.fromkeys(deps))
        self._rebuild()
        if not nx.is_directed_acyclic_graph(self._g):
            raise ValueError(f"dependencies for {task_id} create a cycle")

    # -------------------------------------------------------------- structure
    def predecessors(self, task_id: str) -> list[str]:
        return list(self._g.predecessors(task_id))

    def successors(self, task_id: str) -> list[str]:
        return list(self._g.successors(task_id))

    def ancestors(self, task_id: str) -> set[str]:
        return set(nx.ancestors(self._g, task_id)) if task_id in self._g else set()

    def descendants(self, task_id: str) -> set[str]:
        return set(nx.descendants(self._g, task_id)) if task_id in self._g else set()

    def descendants_of(self, task_ids: Iterable[str]) -> set[str]:
        out: set[str] = set()
        for tid in task_ids:
            out |= self.descendants(tid)
        return out

    def is_acyclic(self) -> bool:
        return nx.is_directed_acyclic_graph(self._g)

    def find_cycle(self) -> list[tuple[str, str]]:
        try:
            return list(nx.find_cycle(self._g))
        except nx.NetworkXNoCycle:
            return []

    def topological_order(self) -> list[str]:
        return list(nx.lexicographical_topological_sort(self._g))

    def layers(self) -> list[list[str]]:
        return [sorted(layer) for layer in nx.topological_generations(self._g)]

    def roots(self) -> list[str]:
        return sorted(n for n in self._g if self._g.in_degree(n) == 0)

    def sinks(self) -> list[str]:
        return sorted(n for n in self._g if self._g.out_degree(n) == 0)

    def critical_path_length(self, weight: dict[str, float] | None = None) -> float:
        weight = weight or {}
        best: dict[str, float] = {}
        for n in self.topological_order():
            preds = [best[p] for p in self._g.predecessors(n)]
            best[n] = (max(preds) if preds else 0.0) + weight.get(n, 1.0)
        return max(best.values(), default=0.0)

    # ------------------------------------------------------------------ state
    def by_status(self, *statuses: TaskStatus) -> list[Task]:
        return [t for t in self._tasks.values() if t.status in statuses]

    def ready_tasks(self) -> list[Task]:
        """Pending tasks whose dependencies are all satisfied (completed or skipped)."""
        ready = []
        for t in self._tasks.values():
            if t.status != TaskStatus.PENDING:
                continue
            if all(self._tasks[d].status in TERMINAL_OK for d in t.dependencies if d in self._tasks):
                ready.append(t)
        ready.sort(key=lambda t: (-t.priority, t.id))
        return ready

    def is_complete(self) -> bool:
        return all(t.is_done() for t in self._tasks.values())

    def has_failures(self) -> bool:
        return any(t.status == TaskStatus.FAILED for t in self._tasks.values())

    def is_stuck(self) -> bool:
        """Work remains but nothing can make progress."""
        if self.is_complete():
            return False
        return not (self.ready_tasks() or self.by_status(TaskStatus.RUNNING, TaskStatus.FAILED))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self._tasks.values():
            out[t.status.value] = out.get(t.status.value, 0) + 1
        return out

    # ----------------------------------------------------------- fingerprints
    def spec_fingerprint(self, task_id: str) -> str:
        return spec_fingerprint(self._tasks[task_id])

    def input_fingerprint(self, task_id: str) -> str:
        task = self._tasks[task_id]
        deps = {}
        for d in sorted(task.dependencies):
            dep = self._tasks.get(d)
            if dep is None:
                deps[d] = "missing"
            elif dep.status == TaskStatus.SKIPPED:
                deps[d] = "skipped"
            elif dep.result is not None:
                deps[d] = dep.result.output_fingerprint
            else:
                deps[d] = "unresolved"
        return stable_hash({"spec": spec_fingerprint(task), "deps": deps})

    # ---------------------------------------------------------- serialisation
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tasks": [t.model_dump(mode="json") for t in self._tasks.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskGraph:
        g = cls(Task.model_validate(t) for t in data.get("tasks", []))
        g.version = data.get("version", g.version)
        return g

    def copy(self) -> TaskGraph:
        return TaskGraph.from_dict(self.to_dict())

    def view(self) -> dict[str, Any]:
        """Compact representation for the UI (no full outputs)."""
        nodes = []
        layer_of = {}
        if self.is_acyclic():
            for i, layer in enumerate(self.layers()):
                for n in layer:
                    layer_of[n] = i
        for t in self._tasks.values():
            nodes.append(
                {
                    "id": t.id,
                    "name": t.name,
                    "type": t.task_type.value,
                    "status": t.status.value,
                    "agent": t.assigned_agent,
                    "attempts": t.attempts,
                    "tag": t.recovery_tag.value if t.recovery_tag else None,
                    "round": t.recovery_round,
                    "origin": t.origin,
                    "parent": t.parent_id,
                    "layer": layer_of.get(t.id, 0),
                    "optional": t.optional,
                    "started_ms": t.started_ms,
                    "finished_ms": t.finished_ms,
                }
            )
        edges = [{"source": u, "target": v} for u, v in self._g.edges]
        return {"nodes": nodes, "edges": edges, "version": self.version}
