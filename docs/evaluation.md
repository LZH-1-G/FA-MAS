# Evaluation methodology

## Goal

Measure whether failure-aware incremental replanning improves efficiency (tokens, cost, latency, redundant work)
**without** reducing task success or result correctness, compared with the baselines from the project
specification — and find out where it does not.

## Systems under test

All systems share the same engine, agents, evaluator, LLM model and failure injection; only planning, agent
selection and recovery policy differ (`famas/orchestration/systems.py`).

| Key | Planning | Selection | Recovery | Result cache |
|---|---|---|---|---|
| `single_agent` (A) | one task covering all work units | fixed (generalist) | restart | no |
| `fixed_workflow` (B) | research → analysis → (code) → report, each stage covering all subjects/aspects | fixed | restart pipeline | no |
| `full_replan` (C) | dynamic DAG | adaptive | full diagnosis → same fixes → planner call → recompute all | no |
| `naive_incremental` (D) | dynamic DAG | adaptive | retry/reassign failed task only | yes |
| `fa_mas` | dynamic DAG | adaptive | failure-aware incremental replanning | yes |
| `fa_mas_verify` | dynamic DAG | adaptive | as FA-MAS + reuse audit | yes |

Budgets are identical: at most 4 failed attempts per task and 8 recovery rounds per run.

## Tasks

`famas/benchmark/dataset.py` — six repository-research requests:

| Id | Complexity | Subjects × aspects | Code task |
|---|---|---|---|
| T1 | simple | 1 × 1 | – |
| T2 | medium | 2 × 1 | – |
| T3 | medium | 2 × 2 | – |
| T4 | complex | 4 × 4 | – |
| T5 | complex | 5 × 5 | feature matrix |
| T6 | complex | 3 × 3 | – |

## Failure scenarios

Faults attach to **work units** (e.g. `research:{s1}:*`), not to task ids, so the same scenario applies fairly to a
monolithic task, a pipeline stage or a fine-grained DAG node. Each rule counts executions that reach a matching
unit; a monolithic task that fails on an early unit does not consume faults on later units it never reached.

| Scenario | Fault | What it tests |
|---|---|---|
| `none` | – | baseline efficiency |
| `transient` | first research execution on subject s0 times out (20 s) | retry, reuse of siblings |
| `agent_failure` | whichever agent first analyses aspect a0 can never do it | reassignment / escalation |
| `complex_task` | research on s1 truncates output beyond 2 aspects per pass | decomposition, salvage |
| `corruption` | research on the last subject returns a plausible, contradictory claim that passes its own checks | root-cause attribution, upstream repair |
| `missing_dependency` | first plan omits the edge research(s1) → analysis(a0) | dependency repair |
| `late_failure` | first execution of the final report is malformed | cost of late failures |
| `cascade` | timeout + corruption + late failure | interaction of several faults |
| `stochastic` | each execution fails with p ≈ 12 % × tier factor (small 1.5, large 0.75), random type | robustness under random faults (5 seeds) |

**Detection model.** A downstream analysis agent notices a corrupted input with probability
`detection_probability` (default 1.0; seeded by consumer, attempt and the input's content hash). The single
agent has no downstream consumer and therefore never detects its own corruption — an explicit assumption that
favours multi-agent cross-checking. The reuse audit detects with the same probability.

## Simulation model

* **Tokens**: input = system prompt + prompt (incl. upstream content and tool material), output = reply length;
  ≈ 4 characters per token (1 per CJK character).
* **Latency**: `base + per_output_token · out + per_input_token · in`, tier-dependent (small: 350 ms + 9 ms/token;
  large: 700 ms + 22 ms/token), ±10 % deterministic jitter; tool calls and sandbox runs add fixed times; a timeout
  costs 20 s plus the prompt tokens.
* **Cost**: nominal list prices (small $0.15/$0.60, large $2.50/$10 per 1M input/output tokens).
* **Clock**: discrete-event simulation of the eager scheduler (max concurrency 4), including planning and
  recovery calls on the critical path.

## Metrics

Defined in `famas/monitoring/metrics.py` and `famas/benchmark/runner.py`:

| Metric | Definition |
|---|---|
| Completed | run finished with every task completed or skipped |
| Correct | completed **and** the final output does not derive from a corrupted result (simulation ground truth) |
| Latency | virtual end-to-end time, including planning and recovery |
| Tokens / cost | all LLM calls: agents, analyzer, planner, evaluator, recovery components |
| Agent invocations | task executions (including failed ones) |
| Recovery success | share of runs that met ≥ 1 failure and still produced a correct output |
| Reuse rate | Σ preserved completed results / Σ (preserved + invalidated) over recovery rounds |
| Invalid reuse rate | preserved results that were in fact corrupted or derived from corruption / preserved results |
| Redundant executions | executions whose exact inputs (fingerprint) had already produced a valid result in the run |
| Wasted tokens | agent tokens spent on executions whose results are not in the final valid graph |
| Replanning overhead | tokens/cost/time of failure analysis, replanner, re-planning and audit calls |

## Reproduce

```bash
famas bench --plots --out benchmarks/results                      # main table (468 runs, ~10 s)
famas bench --scenarios corruption,cascade --tasks T2,T3,T4,T5,T6 \
  --systems full_replan,naive_incremental,fa_mas,fa_mas_verify \
  --det-seeds 0,1,2,3,4,5,6,7,8,9 --detect 0.5 --plots --out benchmarks/results/detect_0.5
```

Outputs: `results.csv` (one row per run), `summary.json`, `summary.md`, and figures.

## Threats to validity

* **Synthetic failure model.** Scenarios, detection and the latency/token model were designed by the authors of
  the system under test. They demonstrate mechanisms and trade-offs; they do not estimate real-world failure
  frequencies or effect sizes. Real LLM failures are messier (partial correctness, inconsistent self-reports).
* **No quality model beyond correctness.** Tiers differ in cost, latency and (for stochastic faults) failure rate,
  but not in output quality; a real study needs graded quality (human or LLM-judge) scores.
* **Detection is binary and independent.** Real detectors are correlated with each other and with the error.
* **Deterministic templates.** Mock planning is template-based; live LLM plans vary, which affects reuse.
* **Small task set.** Six tasks from one domain; extend `dataset.py` (and use live mode) before generalising.

A real-model study should rerun the same harness with `mode="live"` (failure injection works there too) and
add quality grading.
