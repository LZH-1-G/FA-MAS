"""Call accounting and cost-arithmetic regression tests. All offline."""

from __future__ import annotations

import json
import random
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

from famas.config import Settings
from famas.core.models import RunConfig
from famas.llm.base import LLMUsage
from famas.llm.mock import MockLLM
from famas.llm.pricing import MODEL_PRICES, cost_of
from famas.monitoring.metrics import MetricsCollector
from famas.orchestration.engine import Engine
from famas.orchestration.stats import AgentStatsStore
from famas.planning.dag import TaskGraph

ROOT = Path(__file__).resolve().parents[1]
REQUEST = "Compare the architectures of LangGraph and CrewAI."
MICRO = Decimal("0.000001")


class CountingLLM(MockLLM):
    """The deterministic mock, counting every request that would reach a model."""

    def __init__(self, live: bool) -> None:
        super().__init__()
        self.is_mock = not live
        self.requests = 0

    async def complete(self, **kw):
        self.requests += 1
        return await super().complete(**kw)


class StubGitHub:
    async def repo_overview(self, repo: str):
        from famas.tools.github import RepoInfo

        return RepoInfo(repo=repo, description="stub", readme=f"# {repo}")


async def _run(monkeypatch, scenario: str, mode: str):
    llm = CountingLLM(live=mode == "live")
    monkeypatch.setattr("famas.orchestration.engine.create_llm", lambda *a, **k: llm)
    engine = Engine(Settings(), stats=AgentStatsStore())
    engine.github = StubGitHub()  # live tools never touch the network
    out = await engine.run(REQUEST, RunConfig(mode=mode, scenario=scenario))
    return out, llm


@pytest.mark.parametrize("mode", ["mock", "live"])
async def test_injected_timeout_is_a_simulated_call_without_a_request(monkeypatch, mode):
    """Test C's accounting: the injected timeout is charged as one call but sends nothing."""
    out, llm = await _run(monkeypatch, "transient", mode)
    m = out.metrics
    assert out.status == "completed", out.error
    assert m.recovery_rounds == 1 and m.failed_executions == 1
    assert m.simulated_llm_calls == 1
    assert m.llm_calls - m.simulated_llm_calls == llm.requests  # real requests only
    assert m.agent_invocations == m.task_executions  # the failed attempt is an invocation


async def test_no_simulated_calls_without_injected_timeouts(monkeypatch):
    out, llm = await _run(monkeypatch, "none", "mock")
    assert out.metrics.simulated_llm_calls == 0
    assert out.metrics.llm_calls == llm.requests


# ----------------------------------------------------------------------------- cost arithmetic
def _exact(model: str, tin: int, tout: int) -> Decimal:
    pin, pout = MODEL_PRICES[model]
    return (tin * Decimal(str(pin)) + tout * Decimal(str(pout))) / Decimal(10**6)


def test_cost_totals_match_exact_decimal_sum_except_ties():
    """Totals equal the exact decimal cost rounded to 6 places; exact half-micro-dollar ties may
    round either way (float representation decides), but always to one of the two neighbours."""
    rng = random.Random(20261013)
    ties = 0
    for _ in range(400):
        m, exact = MetricsCollector(), Decimal(0)
        for _ in range(rng.randint(1, 40)):
            model = rng.choice(["mock-small", "mock-large"])
            tin, tout = rng.randint(0, 6000), rng.randint(0, 1500)
            m.record_llm(
                rng.choice(["agent:a", "planner", "replanner"]), LLMUsage(cost=cost_of(model, tin, tout))
            )
            exact += _exact(model, tin, tout)
        total = round(m.summarize(TaskGraph(), [], 0.0, True, True).cost, 6)
        scaled = exact * 10**7
        if scaled == scaled.to_integral_value() and scaled % 10 == 5:
            ties += 1
            assert total in (
                float(exact.quantize(MICRO, ROUND_FLOOR)),
                float(exact.quantize(MICRO, ROUND_CEILING)),
            )
        else:
            assert total == float(exact.quantize(MICRO, ROUND_HALF_EVEN))
    assert ties > 0  # the generator does produce ties, so both branches are exercised


# Committed benchmark rows whose exact cost is a half-micro-dollar tie and whose stored value
# changed when totals moved from sequential addition to math.fsum (see docs/evaluation.md).
TIE_ROWS = [
    ("results", 1.0, "T4", "corruption", "naive_incremental", 0, "cost_usd", "0.0920085"),
    ("results", 1.0, "T4", "cascade", "naive_incremental", 0, "cost_usd", "0.0921885"),
    ("results", 1.0, "T6", "stochastic", "fa_mas_verify", 3, "overhead_cost_usd", "0.0006585"),
    ("results/detect_0.5", 0.5, "T5", "cascade", "fa_mas_verify", 2, "overhead_cost_usd", "0.0010785"),
    ("results/detect_0.8", 0.8, "T4", "corruption", "naive_incremental", 8, "cost_usd", "0.0920085"),
    ("results/detect_0.8", 0.8, "T4", "cascade", "naive_incremental", 8, "cost_usd", "0.0921885"),
]


@pytest.mark.parametrize("subdir, p, task, scenario, system, seed, field, exact", TIE_ROWS)
async def test_committed_tie_rows_reproduce_exactly(subdir, p, task, scenario, system, seed, field, exact):
    from famas.benchmark.runner import BenchmarkConfig, run_benchmark

    rows = json.loads((ROOT / "benchmarks" / subdir / "results.json").read_text())
    committed = next(
        r
        for r in rows
        if (r["task"], r["scenario"], r["system"], r["seed"]) == (task, scenario, system, seed)
    )
    cfg = BenchmarkConfig(
        tasks=[task],
        scenarios=[scenario],
        systems=[system],
        seeds=[seed],
        deterministic_seeds=[seed],
        detection_probability=p,
    )
    (row,) = await run_benchmark(cfg)
    assert row == committed  # every field, bit for bit
    lo, hi = Decimal(exact).quantize(MICRO, ROUND_FLOOR), Decimal(exact).quantize(MICRO, ROUND_CEILING)
    assert row[field] in (float(lo), float(hi))
