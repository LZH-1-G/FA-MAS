"""Persistence, sandbox, dispatcher reuse, LLM layer and benchmark plumbing."""

import pytest

from famas.core.events import EventBus
from famas.core.models import RecoveryTag, RunConfig, TaskResult, TaskStatus
from famas.llm.base import MockReply, estimate_tokens, extract_json
from famas.llm.mock import MockLLM
from famas.orchestration.dispatcher import Dispatcher
from famas.orchestration.selector import AgentSelector
from famas.persistence.store import Store, StoreResultCache
from famas.planning.dag import TaskGraph
from famas.tools.sandbox import CodeSandbox
from tests.conftest import MEDIUM, make_task


# ----------------------------------------------------------------- store
def test_store_runs_events_cache_stats(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/t.db")
    store.create_run("r1", "req", {"system": "fa_mas", "mode": "mock", "scenario": "none"})
    store.update_run(
        "r1", status="completed", metrics={"latency_ms": 1.0}, graph={"tasks": []}, final_output="# hi"
    )
    row = store.get_run("r1")
    assert (
        row["status"] == "completed"
        and row["metrics"]["latency_ms"] == 1.0
        and row["config"]["system"] == "fa_mas"
    )
    assert store.list_runs()[0]["id"] == "r1"

    bus = EventBus("r1")
    bus.add_listener(store.add_event)
    bus.emit("run_started", request="req")
    bus.emit("task_started", task_id="a", agent="x")
    assert [e["type"] for e in store.get_events("r1")] == ["run_started", "task_started"]
    assert [e["seq"] for e in store.get_events("r1", after_seq=1)] == [2]

    cache = StoreResultCache(store)
    res = TaskResult(task_id="a", agent_id="x", content="c", data={"units": {"u": "v"}})
    cache.put("fp1", res)
    assert cache.get("fp1").content == "c" and cache.get("nope") is None

    store.save_agent_stats(
        [
            {
                "agent_id": "x",
                "task_type": "research",
                "successes": 3,
                "failures": 1,
                "avg_ms": 10.0,
                "tokens": 5,
            }
        ]
    )
    assert store.load_agent_stats()[0]["successes"] == 3

    store.create_benchmark("b1", {"a": 1})
    store.finish_benchmark("b1", "completed", [{"x": 1}], {"n_runs": 1})
    assert store.get_benchmark("b1")["summary"]["n_runs"] == 1
    store.delete_run("r1")
    assert store.get_run("r1") is None


def test_store_rejects_unknown_urls():
    with pytest.raises(ValueError):
        Store("mysql://x")


# --------------------------------------------------------------- sandbox
async def test_sandbox_runs_code_and_reports_errors():
    sb = CodeSandbox(timeout_s=5)
    ok = await sb.run(
        "def main(data):\n    print('hi')\n    return {'n': len(data['xs'])}", {"xs": [1, 2, 3]}
    )
    assert ok.ok and ok.result == {"n": 3} and ok.stdout == "hi"
    bad = await sb.run("def main(data):\n    raise ValueError('boom')")
    assert not bad.ok and "boom" in bad.error
    missing = await sb.run("x = 1")
    assert not missing.ok
    slow = await CodeSandbox(timeout_s=0.5).run("import time\ndef main(d):\n    time.sleep(5)")
    assert not slow.ok and slow.error == "timeout"


# ------------------------------------------------------------ LLM layer
async def test_mock_llm_models_tokens_cost_and_latency():
    llm = MockLLM()
    small = await llm.complete(
        component="c", system="s", prompt="p" * 400, tier="small", mock=lambda: MockReply(text="x" * 800)
    )
    large = await llm.complete(
        component="c", system="s", prompt="p" * 400, tier="large", mock=lambda: MockReply(text="x" * 800)
    )
    assert small.usage.tokens_out == large.usage.tokens_out == 200
    assert large.usage.cost > small.usage.cost and large.usage.duration_ms > small.usage.duration_ms
    again = await llm.complete(
        component="c", system="s", prompt="p" * 400, tier="large", mock=lambda: MockReply(text="x" * 800)
    )
    assert again.usage.duration_ms == large.usage.duration_ms  # deterministic


def test_token_estimate_and_json_extraction():
    assert estimate_tokens("abcd" * 10) == 10
    assert estimate_tokens("你好世界") == 4
    assert extract_json('Sure!\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert extract_json('prefix {"b": 2} suffix') == {"b": 2}


def test_live_client_constructs_without_network():
    pytest.importorskip("langchain_core")
    from famas.config import Settings
    from famas.llm.langchain_client import LangChainLLM

    client = LangChainLLM(Settings(llm_provider="openai", llm_model="gpt-4o-mini", llm_api_key="sk-test"))
    assert client.model_for("large") == "gpt-4o-mini"


# ---------------------------------------------------------- dispatcher
async def test_dispatcher_reuses_results_by_fingerprint(engine):
    ctx = engine.build_context(MEDIUM, RunConfig())
    g = TaskGraph([make_task("a"), make_task("b", ["a"])])
    a = g.get("a")
    a.status = TaskStatus.COMPLETED
    a.result = TaskResult(task_id="a", agent_id="x", content="A", output_fingerprint="oa")
    b = g.get("b")
    b.previous_result = TaskResult(
        task_id="b",
        agent_id="x",
        content="B",
        input_fingerprint=g.input_fingerprint("b"),
        output_fingerprint="ob",
        data={"units": {"task:b": "B" * 30}},
    )
    b.recovery_tag = RecoveryTag.RECOMPUTE
    d = Dispatcher(ctx, AgentSelector(ctx.registry, ctx.stats))
    rep = await d.run_wave(g)
    assert b.status == TaskStatus.COMPLETED and b.recovery_tag == RecoveryTag.CACHE_HIT
    assert rep.completed == [] and ctx.metrics.executions == 0  # early cut-off: nothing re-executed


async def test_dispatcher_executes_ready_tasks_in_dependency_order(engine):
    ctx = engine.build_context("Write a short note.", RunConfig())
    g = TaskGraph([make_task("a"), make_task("b"), make_task("c", ["a", "b"])])
    d = Dispatcher(ctx, AgentSelector(ctx.registry, ctx.stats))
    order = []
    ctx.events.add_listener(lambda e: order.append(e.task_id) if e.type == "task_started" else None)
    rep = await d.run_wave(g)
    assert sorted(rep.completed) == ["a", "b", "c"] and order[-1] == "c"
    assert g.get("c").started_ms >= max(g.get("a").finished_ms, g.get("b").finished_ms)


# ------------------------------------------------------------ benchmark
async def test_benchmark_runner_and_reports(tmp_path):
    from famas.benchmark.report import summary_markdown, write_plots, write_rows, write_summary
    from famas.benchmark.runner import BenchmarkConfig, run_benchmark, summarize

    cfg = BenchmarkConfig(
        tasks=["T2"],
        scenarios=["none", "late_failure", "stochastic"],
        systems=["fa_mas", "full_replan"],
        seeds=[0, 1],
    )
    rows = await run_benchmark(cfg)
    assert len(rows) == 2 * 1 + 2 * 1 + 2 * 2
    s = summarize(rows)
    assert set(s["overall"]) == {"fa_mas", "full_replan"}
    assert s["per_scenario"]["late_failure"]["fa_mas"]["reuse_rate"] > 0
    assert s["per_scenario"]["late_failure"]["full_replan"]["redundant_executions"] > 0
    write_rows(rows, tmp_path)
    write_summary(s, tmp_path)
    assert (tmp_path / "results.csv").exists() and "FA-MAS" in summary_markdown(s)
    pytest.importorskip("matplotlib")
    assert len(write_plots(s, tmp_path)) == 3


def test_cli_lists(capsys):
    from famas.cli import main

    assert main(["scenarios"]) == 0
    assert "corruption" in capsys.readouterr().out
    assert main(["systems"]) == 0


def test_cost_totals_do_not_depend_on_reporting_order():
    """Concurrent tasks report usage in runtime-dependent order; totals must not change."""
    import itertools

    from famas.llm.base import LLMUsage
    from famas.monitoring.metrics import MetricsCollector

    # 0.1 + 0.2 + 0.3 != 0.3 + 0.2 + 0.1 in floating point; fsum makes the totals order-independent.
    usages = [
        ("agent:a", 0.1),
        ("agent:a", 0.2),
        ("agent:a", 0.3),
        ("failure_analyzer", 1e-7),
        ("agent:c", 0.017),
    ]
    totals = set()
    for order in itertools.permutations(usages):
        m = MetricsCollector()
        for comp, cost in order:
            m.record_llm(comp, LLMUsage(cost=cost), agent_id=comp)
        out = m.summarize(TaskGraph(), [], 0.0, True, True)
        totals.add((out.cost, out.overhead_cost, out.by_component["agent:a"].cost))
    assert len(totals) == 1
