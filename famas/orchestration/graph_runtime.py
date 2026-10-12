"""LangGraph import shim.

FA-MAS orchestrates its phases with LangGraph's ``StateGraph``. If LangGraph is
not installed (e.g. in a minimal sandbox), a tiny compatible runtime with the
same API subset is used instead so the engine and tests still run. Install the
real dependency (``pip install langgraph``) for checkpointing, streaming and
LangSmith tracing.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

try:  # pragma: no cover - exercised when langgraph is installed
    from langgraph.graph import END, START, StateGraph

    try:
        from langgraph.checkpoint.memory import InMemorySaver as MemorySaver
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver

    LANGGRAPH_AVAILABLE = True
except ImportError:  # fallback runtime
    LANGGRAPH_AVAILABLE = False
    START = "__start__"
    END = "__end__"
    MemorySaver = None  # type: ignore[assignment]

    class _Compiled:
        def __init__(self, g: StateGraph) -> None:  # type: ignore[name-defined]
            self.g = g

        async def ainvoke(
            self, state: dict[str, Any], config: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            limit = (config or {}).get("recursion_limit", 25)
            current = self.g.edges[START]
            values = dict(state or {})
            steps = 0
            while current != END:
                steps += 1
                if steps > limit:
                    raise RecursionError(f"recursion limit {limit} reached")
                fn = self.g.nodes[current]
                update = fn(values)
                if inspect.isawaitable(update):
                    update = await update
                if update:
                    values.update(update)
                if current in self.g.conditional:
                    router, path_map = self.g.conditional[current]
                    key = router(values)
                    current = path_map[key] if path_map else key
                else:
                    current = self.g.edges[current]
            return values

    class StateGraph:  # type: ignore[no-redef]
        def __init__(self, state_schema: Any = None) -> None:
            self.nodes: dict[str, Callable] = {}
            self.edges: dict[str, str] = {}
            self.conditional: dict[str, tuple[Callable, dict | None]] = {}

        def add_node(self, name: str, fn: Callable) -> None:
            self.nodes[name] = fn

        def add_edge(self, src: str, dst: str) -> None:
            self.edges[src] = dst

        def add_conditional_edges(self, src: str, router: Callable, path_map: dict | None = None) -> None:
            self.conditional[src] = (router, path_map)

        def compile(self, checkpointer: Any = None) -> _Compiled:
            return _Compiled(self)


__all__ = ["END", "START", "StateGraph", "MemorySaver", "LANGGRAPH_AVAILABLE"]
