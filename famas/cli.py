"""Command-line interface: ``famas run | serve | bench | scenarios | systems | claude``."""

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


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from famas.config import get_settings

    settings = get_settings()
    remote = args.host not in LOOPBACK_HOSTS
    if remote and settings.claude_real_calls_enabled:
        # Subscription logins are for your own, individual use: serving FA-MAS to other people
        # would route their requests through your Claude plan, which Anthropic does not permit.
        print(
            f"refusing to listen on {args.host}: the Claude Agent SDK provider is using your personal "
            "Claude subscription, so FA-MAS only serves this machine (127.0.0.1). Use an API-key "
            "provider for shared deployments."
        )
        return 2
    if remote and settings.live_available and not args.allow_remote:
        print(
            f"refusing to listen on {args.host}: the API has no authentication and a live model provider "
            "is configured, so anyone who can reach this port could spend your API credit. Serve on "
            "127.0.0.1, or pass --allow-remote if the port is protected by your own firewall/proxy auth."
        )
        return 2
    if remote:
        print(
            f"warning: listening on {args.host} without authentication. Requests must use a Host listed "
            "in FAMAS_ALLOWED_HOSTS (default: localhost only)."
        )

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


def _claude_settings():
    from famas.config import CLAUDE_PROVIDER, get_settings

    settings = get_settings()
    if settings.llm_provider != CLAUDE_PROVIDER:
        # The doctor/smoke commands always describe the Claude provider, whatever FAMAS_LLM_PROVIDER is.
        settings = settings.model_copy(update={"llm_provider": CLAUDE_PROVIDER})
    return settings


def cmd_claude_doctor(args: argparse.Namespace) -> int:
    """Report Claude Agent SDK readiness. Never sends a prompt to a model."""
    import subprocess

    from famas.llm import claude_agent_sdk_client as cs

    settings = _claude_settings()
    status = cs.provider_status(settings)
    ok = True

    def line(mark: str, text: str) -> None:
        print(f"  {mark} {text}")

    print("FA-MAS · Claude Agent SDK provider check (no model calls)")
    print("experimental: for personal local testing by the account holder only\n")
    print(f"configured provider : {get_provider_name()}")
    mode = (
        "dry run (scripted, offline)"
        if settings.claude_dry_run
        else ("REAL CALLS ENABLED" if settings.claude_allow_real_calls else "real calls disabled")
    )
    print(f"mode                : {mode}")
    print(f"models              : large={settings.claude_model}  small={settings.claude_small_model}")
    lim = status["limits"]
    print(
        f"limits              : {lim['max_calls_per_run']} calls/run, {lim['max_parallel']} parallel, "
        f"{lim['max_turns']} turns/call, {lim['timeout_s']:.0f}s timeout, "
        f"max ${lim['max_budget_usd_per_call']} est./call\n"
    )

    version = cs.sdk_version()
    if version:
        line("✓", f"claude-agent-sdk {version} installed")
    else:
        ok = False
        line("✗", f"claude-agent-sdk not installed — {cs.INSTALL_HINT}")
    cli = settings.claude_cli_path or cs.bundled_cli_path()
    if cli is None:
        import shutil

        cli = shutil.which("claude")
    if cli:
        try:
            out = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30)
            line("✓", f"Claude Code CLI: {cli} ({(out.stdout or out.stderr).strip()[:60]})")
        except (OSError, subprocess.SubprocessError) as e:
            ok = False
            line("✗", f"Claude Code CLI at {cli} did not run: {e}")
    elif version:
        ok = False
        line("✗", "Claude Code CLI not found (it is normally bundled with claude-agent-sdk)")

    conflicts = status["conflicting_env"]
    if conflicts:
        ok = False
        line(
            "✗",
            f"{', '.join(conflicts)} set — Claude Code would not use your subscription login; unset it",
        )
    else:
        line("✓", "no API-key or cloud-provider variables in the environment")
    for w in cs.user_settings_warnings():
        line("!", w)
    guard = status["usage_guard"]
    if guard["halted"]:
        ok = False
        line("✗", f"usage guard halted: {guard['reason']}")

    if args.offline_test:
        import asyncio

        from famas.llm.base import MockReply

        dry = settings.model_copy(
            update={"claude_dry_run": True, "claude_allow_real_calls": False, "claude_usage_log": None}
        )
        try:
            client = cs.ClaudeAgentSDKLLM(dry)
            resp = asyncio.run(
                client.complete(
                    component="doctor",
                    system="offline check",
                    prompt="ping",
                    tier="small",
                    mock=lambda: MockReply(text="pong"),
                )
            )
            line(
                "✓",
                f"offline SDK round trip (scripted transport): {resp.text!r}, {resp.usage.tokens_in} tok in",
            )
        except Exception as e:  # noqa: BLE001
            ok = False
            line("✗", f"offline SDK round trip failed: {type(e).__name__}: {e}")

    if args.check_login:
        import asyncio

        try:
            acct = asyncio.run(cs.inspect_login(settings))
            line(
                "✓",
                f"Claude Code login: {acct['subscription_type'] or 'subscription'} plan via "
                f"{acct['token_source']} ({acct['api_provider']}) — handshake only, no prompt sent",
            )
        except Exception as e:  # noqa: BLE001
            ok = False
            line("✗", f"login check failed: {e}")
    else:
        line("·", "login not checked (add --check-login: starts Claude Code, sends no prompt)")

    print(f"\nlive mode: {'available' if status['live_available'] else 'not available'} — {status['reason']}")
    return 0 if ok else 1


def cmd_claude_validate(args: argparse.Namespace) -> int:
    from famas.claude_validation import run_validation

    return run_validation(
        args.test, _claude_settings(), yes=args.yes, out_dir=args.out, scenario=args.scenario
    )


def get_provider_name() -> str:
    from famas.config import get_settings

    return get_settings().llm_provider


def cmd_claude_smoke(args: argparse.Namespace) -> int:
    """Two tiny calls (plain + structured) to confirm the provider end to end."""
    import asyncio

    from pydantic import BaseModel

    from famas.llm import claude_agent_sdk_client as cs
    from famas.llm.base import MockReply

    settings = _claude_settings()
    if not settings.claude_dry_run:
        if not settings.claude_allow_real_calls:
            print("refusing: real calls are disabled (FAMAS_CLAUDE_ALLOW_REAL_CALLS is not true).")
            print("Use FAMAS_CLAUDE_DRY_RUN=true for an offline smoke test.")
            return 2
        if not args.yes:
            print(
                "refusing: this makes 2 real Claude calls that count against your subscription usage "
                "limits. Re-run with --yes to confirm."
            )
            return 2

    class Pong(BaseModel):
        ok: bool
        word: str

    async def go() -> int:
        client = cs.ClaudeAgentSDKLLM(settings)
        await client.preflight()
        a = await client.complete(
            component="smoke",
            system="You are a terse test endpoint.",
            prompt="Reply with exactly the single word: pong",
            tier="small",
            mock=lambda: MockReply(text="pong"),
        )
        print(
            f"plain      : {a.text.strip()[:60]!r} ({a.usage.tokens_in} in / {a.usage.tokens_out} out, "
            f"model {a.usage.model}, API-equivalent ${a.usage.cost:.4f})"
        )
        b = await client.complete(
            component="smoke",
            system="You are a terse test endpoint.",
            prompt="Return ok=true and word='pong'.",
            schema=Pong,
            tier="small",
            mock=lambda: MockReply(parsed=Pong(ok=True, word="pong")),
        )
        print(f"structured : {b.parsed!r} ({b.usage.tokens_in} in / {b.usage.tokens_out} out)")
        note = "  (simulated: dry run)" if settings.claude_dry_run else ""
        print(f"account    : {client.account}{note}")
        return 0

    try:
        return asyncio.run(go())
    except Exception as e:  # noqa: BLE001
        print(f"smoke test failed: {type(e).__name__}: {e}")
        return 1


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
    s.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow a non-loopback --host while an API-key provider is configured (never for the "
        "Claude subscription provider); the API itself has no authentication",
    )
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

    c = sub.add_parser(
        "claude",
        help="experimental Claude Agent SDK provider tools (personal local testing, your own login)",
    )
    csub = c.add_subparsers(dest="claude_cmd", required=True)
    d = csub.add_parser("doctor", help="check SDK, CLI, environment and (optionally) login; no model calls")
    d.add_argument(
        "--check-login",
        action="store_true",
        help="start Claude Code and read the login from the handshake (sends no prompt)",
    )
    d.add_argument(
        "--offline-test",
        action="store_true",
        help="round trip through the real SDK with a scripted transport",
    )
    d.set_defaults(func=cmd_claude_doctor)
    v = csub.add_parser(
        "validate",
        help="bounded validation tests A (2-call smoke), B (real workflow), C (failure recovery); "
        "real calls only with --yes",
    )
    v.add_argument("test", choices=["a", "b", "c"])
    v.add_argument("--yes", action="store_true", help="confirm real calls against your subscription")
    v.add_argument("--out", default="data/claude-validation", help="report directory (gitignored)")
    v.add_argument("--scenario", help="test C: failure scenario (default: transient)")
    v.set_defaults(func=cmd_claude_validate)
    sm = csub.add_parser("smoke", help="2 tiny calls (plain + structured); real only with --yes")
    sm.add_argument("--yes", action="store_true", help="confirm real calls against your subscription")
    sm.set_defaults(func=cmd_claude_smoke)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
