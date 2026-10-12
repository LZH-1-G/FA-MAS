"""Minimal programmatic use of FA-MAS.

Runs the flagship request on the deterministic mock LLM, injects a silent upstream
corruption, and prints how the system recovered.

    python examples/quickstart.py
"""

from famas import Engine, RunConfig

REQUEST = (
    "Analyze LangGraph, CrewAI, AutoGen, and Microsoft Agent Framework. Compare their system architectures, "
    "agent communication mechanisms, scheduling strategies, and failure recovery capabilities. "
    "Generate a comprehensive technical report."
)


def main() -> None:
    engine = Engine()
    for system in ("full_replan", "fa_mas"):
        out = engine.run_sync(REQUEST, RunConfig(system=system, scenario="cascade"))
        m = out.metrics
        print(f"\n=== {system}: {out.status} (correct={m.correct})")
        print(f"latency {m.latency_ms / 1000:.1f}s  tokens {m.total_tokens:,}  cost ${m.cost:.4f}  "
              f"agent calls {m.agent_invocations}  redundant {m.redundant_executions}")
        for r in out.replans:
            moves = ", ".join(f"{d.task_id}: {d.failure_type.value} -> {d.strategy.value}" for d in r.diagnoses)
            print(f"  round {r.round}: {moves}")
            print(f"    reused {r.reused}")
            print(f"    invalidated {r.invalidated}")
    print("\nReport excerpt:\n" + out.final_output[:600])


if __name__ == "__main__":
    main()
