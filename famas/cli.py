"""Command-line interface: ``famas run | serve | bench | scenarios | systems``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def cmd_run(args: argparse.Namespace) -> int:
    from famas.core.models import RunConfig
    from famas.orchestration.engine import Engine

    cfg = RunConfig(
        system=args.system,
        mode=args.mode,
        scenario=args.scenario,
        seed=args.seed,
        max_concurrency=args.concurrency,
        simulated_clock=not args.realtime,
        time_scale=args.time_scale,
        detection_probability=args.detect,
        use_llm_judge=args.judge,
    )

    def on_event(ev) -> None:
        if args.quiet:
            return
        d = ev.data
        t = f"[{ev.t_ms / 1000:7.1f}s]"
        if ev.type == "plan_created":
            layers = d.get("layers") or []
            print(f"{t} plan: {len(d['view']['nodes'])} tasks in {len(layers)} layers")
            for i, layer in enumerate(layers):
                print(f"           L{i}: {', '.join(layer)}")
        elif ev.type == "task_started":
            print(
                f"{t} ▶ {ev.task_id} ← {d['agent']} (attempt {d['attempt']}{', ' + d['tag'] if d.get('tag') else ''})"
            )
        elif ev.type == "task_completed":
            print(f"{t} ✓ {ev.task_id} ({d['tokens']} tok, {d['duration_ms'] / 1000:.1f}s)")
        elif ev.type == "task_failed":
            print(f"{t} ✗ {ev.task_id}: {d['error_type']} — {d['message'][:90]}")
        elif ev.type == "task_reused":
            print(f"{t} ↺ {ev.task_id} reused ({d['how']})")
        elif ev.type == "failure_diagnosed":
            dg = d["diagnosis"]
            rc = f" root cause: {dg['root_cause_tasks']}" if dg.get("root_cause_tasks") else ""
            print(f"{t} ⚑ {ev.task_id}: {dg['failure_type']} → {dg['strategy']}{rc}")
        elif ev.type == "replan_applied":
            r = d["record"]
            print(
                f"{t} ⟳ round {r['round']}: reused {len(r['reused'])}, invalidated {len(r['invalidated'])}, "
                f"retried {len(r['retried'])}, added {len(r['added'])}"
            )

    outcome = Engine().run_sync(args.request, cfg, on_event=on_event)
    m = outcome.metrics
    print()
    print(f"status: {outcome.status}" + (f"  ({outcome.error})" if outcome.error else ""))
    print(
        f"latency {m.latency_ms / 1000:.1f}s | tokens {m.total_tokens:,} | cost ${m.cost:.4f} | "
        f"agent calls {m.agent_invocations} | recovery rounds {m.recovery_rounds} | reused {m.reused_results} | "
        f"redundant {m.redundant_executions}"
    )
    if args.out and outcome.final_output:
        Path(args.out).write_text(outcome.final_output)
        print(f"report written to {args.out}")
    elif outcome.final_output and not args.quiet:
        print("\n" + outcome.final_output)
    if args.json:
        Path(args.json).write_text(outcome.model_dump_json(indent=1, exclude={"events"}))
    return 0 if outcome.success else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("famas.api.app:app_factory", factory=True, host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from famas.benchmark.report import write_plots, write_rows, write_summary
    from famas.benchmark.runner import BenchmarkConfig, run_benchmark, summarize

    cfg = BenchmarkConfig(detection_probability=args.detect)
    if args.tasks:
        cfg.tasks = _csv(args.tasks)
    if args.scenarios:
        cfg.scenarios = _csv(args.scenarios)
    if args.systems:
        cfg.systems = _csv(args.systems)
    if args.seeds:
        cfg.seeds = [int(s) for s in _csv(args.seeds)]
    if args.det_seeds:
        cfg.deterministic_seeds = [int(s) for s in _csv(args.det_seeds)]

    def progress(i: int, n: int, row: dict) -> None:
        if i % 10 == 0 or i == n:
            print(f"  {i}/{n} runs", file=sys.stderr)

    rows = asyncio.run(run_benchmark(cfg, progress=progress))
    out = Path(args.out)
    summary = summarize(rows)
    write_rows(rows, out)
    write_summary(summary, out)
    plots = write_plots(summary, out) if args.plots else []
    print((out / "summary.md").read_text())
    print(
        f"\nwrote {out}/results.csv, summary.md, summary.json"
        + (f" and {len(plots)} figures" if plots else "")
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    if args.cmd == "scenarios":
        from famas.simulation.scenarios import SCENARIOS

        for s in SCENARIOS.values():
            print(f"{s.name:20} {s.title:32} {s.description}")
    else:
        from famas.orchestration.systems import SYSTEMS

        for s in SYSTEMS.values():
            print(f"{s.name:20} {s.title}\n{'':20} {s.description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="famas", description="FA-MAS: failure-aware adaptive multi-agent system")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="execute one request")
    r.add_argument("request")
    r.add_argument(
        "--system",
        default="fa_mas",
        choices=[
            "fa_mas",
            "fa_mas_verify",
            "single_agent",
            "fixed_workflow",
            "full_replan",
            "naive_incremental",
        ],
    )
    r.add_argument("--mode", default="mock", choices=["mock", "live"])
    r.add_argument("--scenario", default="none")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument(
        "--realtime", action="store_true", help="real clock instead of the virtual clock (mock mode)"
    )
    r.add_argument(
        "--time-scale", type=float, default=0.0, help="with --realtime: pace mock agents (e.g. 0.05)"
    )
    r.add_argument(
        "--detect", type=float, default=1.0, help="probability that corrupted inputs are detected downstream"
    )
    r.add_argument("--judge", action="store_true", help="enable the LLM judge in the evaluator")
    r.add_argument("--out", help="write the final report (Markdown) here")
    r.add_argument("--json", help="write the full run outcome (JSON) here")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("serve", help="start the API server (and the built UI)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    b = sub.add_parser("bench", help="run the baseline comparison benchmark")
    b.add_argument("--tasks", help="comma-separated task ids (T1..T6)")
    b.add_argument("--scenarios", help="comma-separated scenario names")
    b.add_argument("--systems", help="comma-separated system names")
    b.add_argument("--seeds", help="seeds for stochastic scenarios, e.g. 0,1,2,3,4")
    b.add_argument(
        "--det-seeds", help="seeds for deterministic scenarios (matters when --detect < 1), e.g. 0,1,2"
    )
    b.add_argument(
        "--detect", type=float, default=1.0, help="probability that downstream agents notice corrupted inputs"
    )
    b.add_argument("--out", default="benchmarks/results")
    b.add_argument("--plots", action="store_true", help="also write PNG charts (needs matplotlib)")
    b.set_defaults(func=cmd_bench)

    for name in ("scenarios", "systems"):
        sp = sub.add_parser(name, help=f"list available {name}")
        sp.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
