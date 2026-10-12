"""Persistent execution state: runs, events, result cache, agent statistics, benchmarks.

A deliberately small DB-API layer that speaks both SQLite (stdlib, default)
and PostgreSQL (via ``psycopg``) with the same portable SQL. JSON documents are
stored as TEXT so the schema works unchanged on both engines.

URLs: ``sqlite:///./data/famas.db``, ``sqlite:///:memory:``,
``postgresql://user:pass@host:5432/famas``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from famas.core.models import TaskResult

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        request TEXT NOT NULL,
        system TEXT NOT NULL,
        mode TEXT NOT NULL,
        scenario TEXT,
        status TEXT NOT NULL,
        error TEXT,
        config TEXT,
        analysis TEXT,
        graph TEXT,
        metrics TEXT,
        replans TEXT,
        final_output TEXT,
        created_at DOUBLE PRECISION,
        finished_at DOUBLE PRECISION
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        run_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        type TEXT NOT NULL,
        t_ms DOUBLE PRECISION,
        wall DOUBLE PRECISION,
        task_id TEXT,
        data TEXT,
        PRIMARY KEY (run_id, seq)
    )""",
    """CREATE TABLE IF NOT EXISTS result_cache (
        fingerprint TEXT PRIMARY KEY,
        task_id TEXT,
        result TEXT NOT NULL,
        created_at DOUBLE PRECISION
    )""",
    """CREATE TABLE IF NOT EXISTS agent_stats (
        agent_id TEXT NOT NULL,
        task_type TEXT NOT NULL,
        successes INTEGER NOT NULL DEFAULT 0,
        failures INTEGER NOT NULL DEFAULT 0,
        avg_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
        tokens INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (agent_id, task_type)
    )""",
    """CREATE TABLE IF NOT EXISTS benchmarks (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        config TEXT,
        results TEXT,
        summary TEXT,
        created_at DOUBLE PRECISION,
        finished_at DOUBLE PRECISION
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_run ON events (run_id, seq)",
]

RUN_JSON_FIELDS = {"config", "analysis", "graph", "metrics", "replans"}


def _dumps(v: Any) -> str | None:
    if v is None:
        return None
    return json.dumps(v, ensure_ascii=False, default=str)


def _loads(v: Any) -> Any:
    if v is None or v == "":
        return None
    return json.loads(v)


class Store:
    def __init__(self, url: str = "sqlite:///:memory:") -> None:
        self.url = url
        self._lock = threading.RLock()
        if url.startswith("sqlite"):
            path = url.split("sqlite:///", 1)[1] if "sqlite:///" in url else ":memory:"
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self.conn.row_factory = sqlite3.Row
            if path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.dialect = "sqlite"
        elif url.startswith(("postgresql", "postgres")):
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as e:  # pragma: no cover - optional dependency
                raise RuntimeError("install fa-mas[postgres] (psycopg) to use PostgreSQL") from e
            dsn = url.replace("postgresql+psycopg://", "postgresql://")
            self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
            self.dialect = "postgres"
        else:
            raise ValueError(f"unsupported database url {url!r}")
        self.init_schema()

    # ------------------------------------------------------------ plumbing
    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def execute(self, sql: str, params: tuple | list = ()) -> None:
        with self._lock:
            self.conn.execute(self._sql(sql), params)

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.conn.execute(self._sql(sql), params)
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def init_schema(self) -> None:
        for stmt in SCHEMA:
            self.execute(stmt)

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---------------------------------------------------------------- runs
    def create_run(self, run_id: str, request: str, config: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO runs (id, request, system, mode, scenario, status, config, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                run_id,
                request,
                config.get("system", "fa_mas"),
                config.get("mode", "mock"),
                config.get("scenario"),
                "running",
                _dumps(config),
                time.time(),
            ),
        )

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k} = ?")
            vals.append(_dumps(v) if k in RUN_JSON_FIELDS else v)
        vals.append(run_id)
        self.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id = ?", vals)

    def _run_row(self, row: dict[str, Any]) -> dict[str, Any]:
        for k in RUN_JSON_FIELDS:
            if k in row:
                row[k] = _loads(row[k])
        return row

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM runs WHERE id = ?", (run_id,))
        return self._run_row(rows[0]) if rows else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT id, request, system, mode, scenario, status, error, metrics, created_at, finished_at "
            "FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._run_row(r) for r in rows]

    def delete_run(self, run_id: str) -> None:
        self.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
        self.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    # -------------------------------------------------------------- events
    def add_event(self, ev) -> None:
        self.execute(
            "INSERT INTO events (run_id, seq, type, t_ms, wall, task_id, data) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (run_id, seq) DO NOTHING",
            (ev.run_id, ev.seq, ev.type, ev.t_ms, ev.wall, ev.task_id, _dumps(ev.data)),
        )

    def get_events(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after_seq)
        )
        for r in rows:
            r["data"] = _loads(r["data"]) or {}
        return rows

    # --------------------------------------------------------------- cache
    def cache_get(self, fingerprint: str) -> TaskResult | None:
        rows = self.query("SELECT result FROM result_cache WHERE fingerprint = ?", (fingerprint,))
        return TaskResult.model_validate(_loads(rows[0]["result"])) if rows else None

    def cache_put(self, fingerprint: str, result: TaskResult) -> None:
        self.execute(
            "INSERT INTO result_cache (fingerprint, task_id, result, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT (fingerprint) DO UPDATE SET result = excluded.result, created_at = excluded.created_at",
            (fingerprint, result.task_id, result.model_dump_json(), time.time()),
        )

    # --------------------------------------------------------- agent stats
    def load_agent_stats(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM agent_stats ORDER BY agent_id, task_type")

    def save_agent_stats(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            self.execute(
                "INSERT INTO agent_stats (agent_id, task_type, successes, failures, avg_ms, tokens) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (agent_id, task_type) DO UPDATE SET successes = excluded.successes, "
                "failures = excluded.failures, avg_ms = excluded.avg_ms, tokens = excluded.tokens",
                (r["agent_id"], r["task_type"], r["successes"], r["failures"], r["avg_ms"], r["tokens"]),
            )

    # ---------------------------------------------------------- benchmarks
    def create_benchmark(self, bench_id: str, config: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO benchmarks (id, status, config, created_at) VALUES (?,?,?,?)",
            (bench_id, "running", _dumps(config), time.time()),
        )

    def finish_benchmark(self, bench_id: str, status: str, results: list[dict], summary: dict) -> None:
        self.execute(
            "UPDATE benchmarks SET status = ?, results = ?, summary = ?, finished_at = ? WHERE id = ?",
            (status, _dumps(results), _dumps(summary), time.time(), bench_id),
        )

    def get_benchmark(self, bench_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM benchmarks WHERE id = ?", (bench_id,))
        if not rows:
            return None
        r = rows[0]
        for k in ("config", "results", "summary"):
            r[k] = _loads(r[k])
        return r

    def list_benchmarks(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT id, status, config, summary, created_at, finished_at FROM benchmarks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        for r in rows:
            r["config"] = _loads(r["config"])
            r["summary"] = _loads(r["summary"])
        return rows


class StoreResultCache:
    """Persistent ``ResultCache`` backed by the store (enables cross-run reuse)."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def get(self, fingerprint: str) -> TaskResult | None:
        return self.store.cache_get(fingerprint)

    def put(self, fingerprint: str, result: TaskResult) -> None:
        self.store.cache_put(fingerprint, result)
