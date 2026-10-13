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
| Tokens / cost (`tokens`, `cost_usd`) | all LLM calls charged to the run: agents, analyzer, planner, evaluator, recovery components, plus the modelled cost of injected timeouts. Cost is in USD and rounded to 6 decimals (see below). |
| LLM calls (`llm_calls`) | every usage record charged to the run: real model requests **plus simulated calls** |
| Simulated LLM calls (`simulated_llm_calls`, run metrics only) | injected timeouts. Each is charged like a request that hit the client timeout (prompt tokens, cost, 20 s) but **sends nothing to a model**. Real model requests = `llm_calls − simulated_llm_calls`. |
| Agent invocations (`agent_invocations`, "Agent calls" in tables) | agent task executions, one per attempt, including failed attempts that never reached a model. Not the number of LLM requests. |
| Task executions / failed executions | attempts started / attempts that ended in a failure |
| Recovery success | share of runs that met ≥ 1 failure and still produced a correct output |
| Reuse rate | Σ preserved completed results / Σ (preserved + invalidated) over recovery rounds |
| Invalid reuse rate | preserved results that were in fact corrupted or derived from corruption / preserved results |
| Redundant executions | executions whose exact inputs (fingerprint) had already produced a valid result in the run |
| Wasted tokens | agent tokens spent on executions whose results are not in the final valid graph |
| Replanning overhead | tokens/cost/time of failure analysis, replanner, re-planning and audit calls |

### Cost arithmetic and reproducibility

Per-call cost is `(tokens_in × input price + tokens_out × output price) / 1,000,000`. With the nominal prices
above, every per-call cost is an exact multiple of $0.00000005. A run total is the sum of its per-call costs,
computed with `math.fsum`: the result is the float nearest the exact sum, whatever order concurrent tasks
report their usage in. It is then rounded to 6 decimals.

Because costs move in steps of half a ten-millionth, a total can fall **exactly halfway** between two
micro-dollars (for example $0.0920085). Binary floating point cannot represent that midpoint, so the rounding
direction of such a tie is decided by representation error. It is deterministic and platform-independent, but
it follows no decimal rounding rule. Every other total equals the exact decimal cost rounded to 6 places.

This was checked against all 2,536 cost values in the committed results (1,268 runs × `cost_usd` and
`overhead_cost_usd`):

* 2,348 values equal the exactly rounded decimal cost;
* 188 are exact ties, and each holds one of its two valid 6-decimal roundings.

**Why six committed values changed in v0.1.0-beta.** Earlier totals were accumulated with sequential `+=`,
plus a per-component `sum()`. Switching to `math.fsum` (to make totals independent of reporting order) changed
the direction of six ties by ±$0.000001. No token count, timing, outcome or other metric changed, and every
`summary.json` and `summary.md` is byte-identical:

| Set | Run (task / scenario / system / seed) | Field | Exact cost | Before | After |
|---|---|---|---|---:|---:|
| main | T4 / corruption / naive_incremental / 0 | `cost_usd` | 0.0920085 | 0.092009 | 0.092008 |
| main | T4 / cascade / naive_incremental / 0 | `cost_usd` | 0.0921885 | 0.092189 | 0.092188 |
| main | T6 / stochastic / fa_mas_verify / 3 | `overhead_cost_usd` | 0.0006585 | 0.000658 | 0.000659 |
| detect 0.5 | T5 / cascade / fa_mas_verify / 2 | `overhead_cost_usd` | 0.0010785 | 0.001079 | 0.001078 |
| detect 0.8 | T4 / corruption / naive_incremental / 8 | `cost_usd` | 0.0920085 | 0.092009 | 0.092008 |
| detect 0.8 | T4 / cascade / naive_incremental / 8 | `cost_usd` | 0.0921885 | 0.092189 | 0.092188 |

The old values remain in the Git history. `tests/test_metrics_accounting.py` pins these rows and checks the
exact-decimal property on random inputs.

Rounding ties with an explicit decimal rule (half-even or half-up) would need exact decimal accounting. It would
change 75 (half-even) or 107 (half-up) stored values by $0.000001, with every summary unchanged. It is left for
a future release so that the published datasets stay as they are.

## Reproduce

```bash
famas bench --plots --out benchmarks/results                      # main table (468 runs, ~10 s)
famas bench --scenarios corruption,cascade --tasks T2,T3,T4,T5,T6 \
  --systems full_replan,naive_incremental,fa_mas,fa_mas_verify \
  --det-seeds 0,1,2,3,4,5,6,7,8,9 --detect 0.5 --plots --out benchmarks/results/detect_0.5
# the detect 0.8 set uses the same command with --detect 0.8 --out benchmarks/results/detect_0.8
```

`scripts/verify-offline.sh` regenerates all three sets and checks that they are byte-identical to the committed files.

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
