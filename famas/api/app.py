"""FastAPI application exposing FA-MAS: runs, live event streams, agents, benchmarks."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from famas import __version__
from famas.api.runs import RunManager
from famas.benchmark.dataset import DEFAULT_TASKS, EXAMPLE_REQUESTS
from famas.benchmark.runner import BenchmarkConfig, run_benchmark, summarize
from famas.config import Settings, get_settings
from famas.core.models import InjectionRule, RunConfig, SystemName, new_id
from famas.orchestration.engine import Engine
from famas.orchestration.graph_runtime import LANGGRAPH_AVAILABLE
from famas.orchestration.stats import AgentStatsStore
from famas.orchestration.systems import SYSTEMS
from famas.persistence.store import Store, StoreResultCache
from famas.simulation.scenarios import SCENARIOS


class RunRequest(BaseModel):
    request: str = Field(min_length=3, max_length=8000)
    system: SystemName = "fa_mas"
    mode: Literal["mock", "live"] = "mock"
    scenario: str = "none"
    seed: int = 0
    max_concurrency: int = Field(default=4, ge=1, le=32)
    simulated: bool = False  # true: instant virtual-clock run; false: paced in real time
    time_scale: float | None = Field(default=None, ge=0, le=5)
    detection_probability: float = Field(default=1.0, ge=0, le=1)
    natural_failure_rate: float = Field(default=0.0, ge=0, le=1)
    use_llm_judge: bool = False
    persist_cache: bool = False
    injection: list[InjectionRule] = Field(default_factory=list)


class BenchmarkRequest(BaseModel):
    tasks: list[str] | None = None
    scenarios: list[str] | None = None
    systems: list[str] | None = None
    seeds: list[int] | None = None
    detection_probability: float = 1.0


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = store or Store(settings.database_url)
    stats = AgentStatsStore()
    stats.load(store.load_agent_stats())
    engine = Engine(settings, stats=stats, persistent_cache=StoreResultCache(store))
    manager = RunManager(engine, store, settings)
    benchmarks: dict[str, asyncio.Task] = {}
    progress: dict[str, dict[str, Any]] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await manager.shutdown()
        for t in benchmarks.values():
            t.cancel()

    app = FastAPI(title="FA-MAS API", version=__version__, lifespan=lifespan)
    app.state.manager = manager
    app.state.store = store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------- meta
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "langgraph": LANGGRAPH_AVAILABLE,
            "llm_provider": settings.llm_provider,
            "live_available": settings.live_available,
            "database": store.dialect,
        }

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "systems": [s.model_dump(mode="json") for s in SYSTEMS.values()],
            "scenarios": [s.model_dump(mode="json") for s in SCENARIOS.values()],
            "agents": [a.model_dump(mode="json") for a in engine.registry.specs()],
            "examples": EXAMPLE_REQUESTS,
            "benchmark_tasks": [t.model_dump() for t in DEFAULT_TASKS],
            "live_available": settings.live_available,
            "default_time_scale": settings.ui_time_scale,
        }

    @app.get("/api/agents")
    def agents() -> dict[str, Any]:
        return {
            "agents": [a.model_dump(mode="json") for a in engine.registry.specs()],
            "stats": stats.snapshot(),
        }

    # ------------------------------------------------------------- runs
    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunRequest) -> dict[str, str]:
        if body.mode == "live" and not settings.live_available:
            raise HTTPException(
                400, "live mode requires FAMAS_LLM_PROVIDER and an API key (see .env.example)"
            )
        if body.scenario not in SCENARIOS:
            raise HTTPException(400, f"unknown scenario {body.scenario!r}")
        cfg = RunConfig(
            system=body.system,
            mode=body.mode,
            scenario=body.scenario,
            seed=body.seed,
            max_concurrency=body.max_concurrency,
            simulated_clock=body.simulated and body.mode == "mock",
            time_scale=settings.ui_time_scale if body.time_scale is None else body.time_scale,
            detection_probability=body.detection_probability,
            natural_failure_rate=body.natural_failure_rate,
            use_llm_judge=body.use_llm_judge,
            persist_cache=body.persist_cache,
            injection=body.injection,
        )
        run_id = await manager.start(body.request, cfg)
        return {"run_id": run_id}

    @app.get("/api/runs")
    def list_runs(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        out = []
        for r in store.list_runs(limit):
            m = r.get("metrics") or {}
            out.append(
                {
                    "id": r["id"],
                    "request": r["request"],
                    "system": r["system"],
                    "mode": r["mode"],
                    "scenario": r["scenario"],
                    "status": r["status"],
                    "error": r.get("error"),
                    "created_at": r["created_at"],
                    "finished_at": r.get("finished_at"),
                    "latency_ms": m.get("latency_ms"),
                    "tokens": m.get("total_tokens"),
                    "cost": m.get("cost"),
                    "recovery_rounds": m.get("recovery_rounds"),
                }
            )
        return out

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        snap = manager.snapshot(run_id)
        if snap is None:
            raise HTTPException(404, "run not found")
        return snap

    @app.get("/api/runs/{run_id}/tasks/{task_id}")
    def get_task(run_id: str, task_id: str) -> dict[str, Any]:
        detail = manager.task_detail(run_id, task_id)
        if detail is None:
            raise HTTPException(404, "task not found")
        return detail

    @app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
    def get_report(run_id: str) -> str:
        row = store.get_run(run_id)
        if row is None:
            raise HTTPException(404, "run not found")
        if not row.get("final_output"):
            raise HTTPException(409, f"run is {row['status']}; no report available")
        return row["final_output"]

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(run_id: str, after: int = 0) -> StreamingResponse:
        if store.get_run(run_id) is None:
            raise HTTPException(404, "run not found")

        async def gen():
            async for ev in manager.stream(run_id, after):
                yield f"id: {ev.get('seq', 0)}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
            yield "event: end\ndata: {}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.delete("/api/runs/{run_id}", status_code=204)
    def delete_run(run_id: str) -> None:
        if run_id in manager.active:
            raise HTTPException(409, "run is still executing")
        store.delete_run(run_id)

    # ------------------------------------------------------- benchmarks
    @app.post("/api/benchmarks", status_code=201)
    async def start_benchmark(body: BenchmarkRequest) -> dict[str, Any]:
        cfg = BenchmarkConfig(detection_probability=body.detection_probability)
        if body.tasks:
            cfg.tasks = body.tasks
        if body.scenarios:
            unknown = [s for s in body.scenarios if s not in SCENARIOS]
            if unknown:
                raise HTTPException(400, f"unknown scenarios {unknown}")
            cfg.scenarios = body.scenarios
        if body.systems:
            unknown = [s for s in body.systems if s not in SYSTEMS]
            if unknown:
                raise HTTPException(400, f"unknown systems {unknown}")
            cfg.systems = body.systems
        if body.seeds:
            cfg.seeds = body.seeds
        bench_id = new_id("bench_")
        store.create_benchmark(bench_id, cfg.model_dump())
        progress[bench_id] = {"done": 0, "total": None}

        def on_progress(i: int, n: int, row: dict) -> None:
            progress[bench_id] = {"done": i, "total": n}

        async def job() -> None:
            try:
                rows = await run_benchmark(cfg, progress=on_progress)
                store.finish_benchmark(bench_id, "completed", rows, summarize(rows))
            except Exception as e:  # pragma: no cover - defensive
                store.finish_benchmark(bench_id, "failed", [], {"error": str(e)})

        benchmarks[bench_id] = asyncio.create_task(job())
        return {"benchmark_id": bench_id}

    @app.get("/api/benchmarks")
    def list_benchmarks() -> list[dict[str, Any]]:
        rows = store.list_benchmarks()
        for r in rows:
            r["progress"] = progress.get(r["id"])
        return rows

    @app.get("/api/benchmarks/{bench_id}")
    def get_benchmark(bench_id: str, include_rows: bool = False) -> dict[str, Any]:
        b = store.get_benchmark(bench_id)
        if b is None:
            raise HTTPException(404, "benchmark not found")
        b["progress"] = progress.get(bench_id)
        if not include_rows:
            b.pop("results", None)
        return b

    # -------------------------------------------------- static frontend
    dist = Path(settings.frontend_dist)
    if (dist / "index.html").exists():

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404)
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


def app_factory() -> FastAPI:  # for `uvicorn famas.api.app:app_factory --factory`
    return create_app()
