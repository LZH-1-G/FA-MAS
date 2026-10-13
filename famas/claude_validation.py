"""Bounded validation runs for the Claude Agent SDK provider (``famas claude validate a|b|c``).

Each test runs with strict caps, records per-call usage metadata (tokens, model, timing,
Claude Code's API-equivalent cost *estimate*, rate-limit status) and writes a JSON report.
Nothing here enables real calls: real runs still require ``FAMAS_CLAUDE_ALLOW_REAL_CALLS=true``
*and* ``--yes``; with ``FAMAS_CLAUDE_DRY_RUN=true`` the same tests run offline for free.

Reports never contain prompts, model outputs, session ids or credentials.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from famas.config import CLAUDE_PROVIDER, Settings

REQUEST_B = "Compare the architectures of LangGraph and CrewAI."


@dataclass(frozen=True)
class TestPlan:
    key: str
    title: str
    what: str
    max_calls: int  # hard cap: the run aborts instead of exceeding it
    expected_calls: str
    scenario: str | None = None
    request: str | None = None


PLANS: dict[str, TestPlan] = {
    "a": TestPlan(
        "a",
        "Minimal smoke test",
        "2 tiny calls on the small model: one plain reply, one structured (JSON-schema) reply.",
        max_calls=2,
        expected_calls="exactly 2",
    ),
    "b": TestPlan(
        "b",
        "Real multi-agent workflow: LangGraph vs CrewAI",
        "FA-MAS plans a DAG (request analysis, research agents, analysis agents, a report writer) and "
        "runs it with real Claude agents. Research agents read public repository data from the GitHub API.",
        max_calls=12,
        expected_calls=(
            "depends on the plan Claude produces: 5 in the dry run (request analysis, 2 research, "
            "1 analysis, 1 writer); a real plan may contain more analysis tasks"
        ),
        scenario="none",
        request=REQUEST_B,
    ),
    "c": TestPlan(
        "c",
        "Controlled failure recovery with real Claude agents",
        "Same workflow with deterministic failure injection: the scenario forces a failure, FA-MAS "
        "diagnoses it, replans only the affected part of the DAG and reuses valid results.",
        max_calls=16,
        expected_calls=(
            "Test B's count + 1 failure diagnosis (6 in the dry run). The injected failure sends no "
            "prompt; the run metrics count it once as a simulated LLM call"
        ),
        scenario="transient",
        request=REQUEST_B,
    ),
}


def capped_settings(settings: Settings, plan: TestPlan) -> Settings:
    """Tighten (never loosen) the provider limits for a validation run."""
    s = settings
    budget = s.claude_max_budget_usd_per_call
    return s.model_copy(
        update={
            "llm_provider": CLAUDE_PROVIDER,
            "claude_max_calls_per_run": min(s.claude_max_calls_per_run, plan.max_calls),
            "claude_max_parallel": min(s.claude_max_parallel, 2),
            "claude_max_turns": min(s.claude_max_turns, 3),
            "claude_timeout_s": min(s.claude_timeout_s, 180.0),
            "claude_max_budget_usd_per_call": 0.5 if budget is None else min(budget, 0.5),
        }
    )


def describe_plan(plan: TestPlan, settings: Settings) -> str:
    real = not settings.claude_dry_run
    lines = [
        f"Test {plan.key.upper()}: {plan.title}",
        f"  what       : {plan.what}",
        f"  mode       : {'REAL Claude calls (your Claude subscription)' if real else 'dry run (scripted, offline, free)'}",
        f"  calls      : {plan.expected_calls}; hard cap {settings.claude_max_calls_per_run} "
        f"(the run aborts rather than exceed it)",
        f"  models     : large={settings.claude_model} small={settings.claude_small_model}",
        f"  limits     : {settings.claude_max_parallel} parallel, {settings.claude_max_turns} turns/call, "
        f"{settings.claude_timeout_s:.0f}s/call, max ${settings.claude_max_budget_usd_per_call} "
        "API-equivalent estimate per call",
        "  retries    : at most 2 recovery rounds; usage-limit, credential and budget errors abort at once",
    ]
    if plan.scenario and plan.scenario != "none":
        lines.append(f"  failure    : injected scenario '{plan.scenario}'")
    if real:
        lines.append(
            "  billing    : counts against your Claude plan's shared usage limits (same allowance as "
            "claude.ai). No API key is used. FA-MAS stops if Claude reports usage credits (extra usage)."
        )
    return "\n".join(lines)


def _totals(records: list[dict[str, Any]]) -> dict[str, Any]:
    def total(key: str) -> int:
        return sum(int(r.get(key) or 0) for r in records)

    costs = [r["api_equivalent_cost_usd"] for r in records if r.get("api_equivalent_cost_usd") is not None]
    return {
        "calls_recorded": len(records),
        "prompts_sent": sum(1 for r in records if r.get("prompt_sent")),
        "calls_ok": sum(1 for r in records if r.get("outcome") == "ok"),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "cache_creation_input_tokens": total("cache_creation_input_tokens"),
        "cache_read_input_tokens": total("cache_read_input_tokens"),
        "api_equivalent_cost_usd_estimate": round(sum(costs), 6) if costs else None,
        "models": sorted({r["model"] for r in records if r.get("model")}),
    }


CALL_DEFINITIONS = {
    "prompts_sent": "prompts that actually reached Claude, counted from the provider's per-call records",
    "llm_calls": "LLM calls charged by the FA-MAS run metrics: real model calls + simulated calls",
    "simulated_llm_calls": "injected failures charged like a timed-out call; no prompt is sent",
    "model_calls": "llm_calls - simulated_llm_calls; should equal prompts_sent",
    "agent_invocations": "agent task executions, including failed attempts that made no model call",
}


def _accounting(outcome: dict[str, Any], totals: dict[str, Any]) -> dict[str, Any] | None:
    """Reconcile the provider's call records with the run metrics (workflow tests only)."""
    if "llm_calls" not in outcome:
        return None
    model_calls = outcome["llm_calls"] - outcome.get("simulated_llm_calls", 0)
    return {
        "prompts_sent": totals["prompts_sent"],
        "llm_calls": outcome["llm_calls"],
        "simulated_llm_calls": outcome.get("simulated_llm_calls", 0),
        "model_calls": model_calls,
        "agent_invocations": outcome.get("agent_invocations"),
        "consistent": model_calls == totals["prompts_sent"],
        "definitions": CALL_DEFINITIONS,
    }


async def _test_a(settings: Settings) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from famas.llm.base import MockReply
    from famas.llm.claude_agent_sdk_client import ClaudeAgentSDKLLM

    class Pong(BaseModel):
        ok: bool
        word: str

    client = ClaudeAgentSDKLLM(settings)
    await client.preflight()
    outcome: dict[str, Any] = {"status": "completed", "error": None}
    try:
        plain = await client.complete(
            component="validate:a:plain",
            system="You are a terse test endpoint.",
            prompt="Reply with exactly the single word: pong",
            tier="small",
            mock=lambda: MockReply(text="pong"),
        )
        structured = await client.complete(
            component="validate:a:structured",
            system="You are a terse test endpoint.",
            prompt="Return ok=true and word='pong'.",
            schema=Pong,
            tier="small",
            mock=lambda: MockReply(parsed=Pong(ok=True, word="pong")),
        )
        outcome["checks"] = {
            "plain_reply_is_pong": plain.text.strip().lower().strip(".") == "pong",
            "structured_reply_valid": structured.parsed == Pong(ok=True, word="pong"),
        }
        if not all(outcome["checks"].values()):
            outcome["status"] = "completed_with_unexpected_output"
    except Exception as e:  # noqa: BLE001 - reported, not raised
        outcome = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
    outcome["account"] = client.account
    return outcome, client.records


async def _test_workflow(settings: Settings, plan: TestPlan, scenario: str) -> tuple[dict, list]:
    from famas.core.models import RunConfig
    from famas.llm import claude_agent_sdk_client as cs
    from famas.llm import create_llm
    from famas.orchestration.engine import Engine
    from famas.orchestration.stats import AgentStatsStore

    clients: list[cs.ClaudeAgentSDKLLM] = []

    def tracking_create_llm(mode: str, s: Settings | None = None, time_scale: float = 0.0):
        llm = create_llm(mode, s, time_scale)
        if isinstance(llm, cs.ClaudeAgentSDKLLM):
            clients.append(llm)
        return llm

    engine = Engine(settings, stats=AgentStatsStore(), llm_factory=tracking_create_llm)
    out = await engine.run(
        plan.request or REQUEST_B,
        RunConfig(
            mode="live",
            system="fa_mas",
            scenario=scenario,
            max_concurrency=2,
            max_recovery_rounds=2,
            label=f"claude-validation-{plan.key}",
        ),
    )
    m = out.metrics
    outcome = {
        "status": out.status,
        "error": out.error,
        "run_id": out.run_id,
        "tasks_total": m.tasks_total,
        "tasks_completed": m.tasks_completed,
        "agent_invocations": m.agent_invocations,
        "llm_calls": m.llm_calls,
        "simulated_llm_calls": m.simulated_llm_calls,
        "model_calls": m.llm_calls - m.simulated_llm_calls,
        "recovery_rounds": m.recovery_rounds,
        "reused_results": m.reused_results,
        "invalidated_results": m.invalidated_results,
        "latency_s": round(m.latency_ms / 1000, 1),
        "report_chars": len(out.final_output or ""),
        "replans": [
            {"round": r.round, "retried": r.retried, "reused": r.reused, "invalidated": r.invalidated}
            for r in out.replans
        ],
        "account": clients[0].account if clients else None,
    }
    records = [r for c in clients for r in c.records]
    return outcome, records


def run_validation(
    test: str,
    settings: Settings,
    *,
    yes: bool,
    out_dir: str = "data/claude-validation",
    scenario: str | None = None,
) -> int:
    from famas.llm import claude_agent_sdk_client as cs

    plan = PLANS[test]
    s = capped_settings(settings, plan)
    real = not s.claude_dry_run
    print(describe_plan(plan, s))
    if real and not s.claude_allow_real_calls:
        print("\nrefusing: real calls are disabled (FAMAS_CLAUDE_ALLOW_REAL_CALLS is not true).")
        print("Set FAMAS_CLAUDE_DRY_RUN=true to rehearse this test offline.")
        return 2
    if real and not yes:
        print("\nrefusing: this test makes real Claude calls. Re-run with --yes to confirm.")
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    usage_log = out / f"{stamp}-test-{test}.usage.jsonl"
    s = s.model_copy(update={"claude_usage_log": str(usage_log)})
    guard_before = cs.USAGE_GUARD.snapshot()
    started = time.time()
    print("\nrunning…")
    if test == "a":
        outcome, records = asyncio.run(_test_a(s))
    else:
        outcome, records = asyncio.run(_test_workflow(s, plan, scenario or plan.scenario or "none"))
    report = {
        "test": test,
        "title": plan.title,
        "real_calls": real,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "duration_s": round(time.time() - started, 1),
        "sdk_version": cs.sdk_version(),
        "limits": {
            "max_calls": s.claude_max_calls_per_run,
            "max_parallel": s.claude_max_parallel,
            "max_turns": s.claude_max_turns,
            "timeout_s": s.claude_timeout_s,
            "max_budget_usd_per_call": s.claude_max_budget_usd_per_call,
        },
        "models": {"large": s.claude_model, "small": s.claude_small_model},
        "outcome": outcome,
        "totals": _totals(records),
        "accounting": None,
        "billing_note": (
            "Real calls draw on your Claude subscription's usage limits; Claude Code reports no per-call "
            "charge. api_equivalent_cost_usd is Claude Code's client-side estimate at API list prices, "
            "for comparison only - it is not what you pay on a subscription."
            if real
            else "Dry run: scripted responses, nothing was sent to Anthropic and nothing was billed."
        ),
        "rate_limit": {"before": guard_before, "after": cs.USAGE_GUARD.snapshot()},
        "calls": records,
        "usage_log": str(usage_log),
    }
    report["accounting"] = _accounting(outcome, report["totals"])
    report_path = out / f"{stamp}-test-{test}.json"
    report_path.write_text(json.dumps(report, indent=1, default=str))
    t = report["totals"]
    print(
        f"\nstatus     : {outcome.get('status')}" + (f" ({outcome['error']})" if outcome.get("error") else "")
    )
    print(
        f"calls      : {t['prompts_sent']} prompts sent, {t['calls_ok']} ok; models {', '.join(t['models']) or '-'}"
    )
    acc = report["accounting"]
    if acc:
        print(
            f"run metrics: {acc['llm_calls']} llm_calls = {acc['model_calls']} model calls + "
            f"{acc['simulated_llm_calls']} simulated (injected failure, no prompt); "
            f"{acc['agent_invocations']} agent invocations"
            + ("" if acc["consistent"] else "  ** MISMATCH with prompts sent **")
        )
    print(
        f"tokens     : {t['input_tokens']:,} in + {t['cache_read_input_tokens']:,} cache-read + "
        f"{t['cache_creation_input_tokens']:,} cache-write, {t['output_tokens']:,} out"
    )
    est = t["api_equivalent_cost_usd_estimate"]
    est_text = f"${est:.4f}" if est is not None else "-"
    print(f"API-equiv. : {est_text} (estimate, not a subscription charge)")
    last = report["rate_limit"]["after"].get("last_event")
    if last:
        print(
            f"rate limit : {last.get('status')} ({last.get('rate_limit_type')}), utilization {last.get('utilization')}"
        )
    print(f"report     : {report_path}")
    return 0 if outcome.get("status") == "completed" else 1
