# FA-MAS — Failure-Aware Adaptive Multi-Agent System

**FA-MAS** turns a complex natural-language request into a dynamically generated task DAG, assigns each
subtask to the most suitable agent, executes independent work in parallel, validates every intermediate
result — and when something fails, it **replans only the affected part of the graph** while reusing every
result that is provably still valid.

```
                 Task A (REUSED)
                    │
             ┌──────┴──────┐
             ▼             ▼
      Task B (RETRY)   Task C (REUSED)
             └──────┬──────┘
                    ▼
            Task D (RECOMPUTE)
                    ▼
             Task E (EXECUTE)
```

It is built as a research platform: every design choice (planning, agent selection, recovery policy) can be
swapped, and a deterministic simulator with controlled failure injection compares FA-MAS against single-agent,
fixed-workflow, full-replanning and naive-retry baselines on identical tasks.

| | |
|---|---|
| Orchestration | LangGraph `StateGraph` (analyse → plan → validate → assign → execute → diagnose → assess impact → replan) |
| Task graph | NetworkX DAG with Merkle-style content fingerprints for reuse/invalidation |
| Agents | research (fast / deep), analysis (standard / expert), coding (sandboxed execution), writing, generalist |
| LLMs | any OpenAI-compatible endpoint or Anthropic via LangChain — or a deterministic mock (default) |
| Backend | FastAPI + SSE event stream, SQLite or PostgreSQL persistence |
| Frontend | React + React Flow: live DAG, agent activity, failure & recovery timeline, metrics, reports, benchmarks |

---

## Why

Fixed multi-agent pipelines handle failures badly: one bad intermediate result often restarts large parts of
the workflow, re-spending tokens and time on work that was fine. Naive retries are cheap but blind — retrying a
task whose *input* is wrong just reproduces the failure. FA-MAS treats recovery as a dependency problem:

1. **Classify** the failure (transient, agent capability, malformed / incomplete output, missing information,
   context inconsistency, dependency error) and **attribute a root cause** — possibly an upstream task.
2. **Compute the impact**: the repair set and its descendants are affected; every other completed result is
   reused.
3. **Patch the DAG**: retry, reassign to another agent, split an oversized task (salvaging partial output),
   add a forgotten dependency, insert a missing prerequisite, or re-execute the upstream root cause with
   feedback.
4. **Re-validate by fingerprint** at dispatch time: if a re-executed upstream task produces identical output,
   downstream results are reused anyway (early cut-off).

## Results at a glance

Deterministic simulation, 468 runs: 6 tasks × 9 failure scenarios × 6 systems (stochastic scenario × 5 seeds).
Mock LLM with modelled token counts, latency and cost — see [docs/evaluation.md](docs/evaluation.md) for the
model and its limits. Numbers are means over the 8 scenarios that inject failures.

| System | Correct output | Latency | Tokens | Cost | Redundant executions | Reuse rate |
|---|---:|---:|---:|---:|---:|---:|
| A: single agent | 71% | 73.6 s | 8,641 | $0.0434 | 0.00 | – |
| B: fixed workflow | 88% | 69.1 s | 15,069 | $0.0171 | 0.86 | 0% |
| C: dynamic + full replanning | 93% | 89.0 s | 20,574 | $0.0500 | 4.97 | 0% |
| D: dynamic + naive retry (ablation) | 71% | 56.1 s | 12,121 | $0.0331 | 0.00 | 100% |
| **FA-MAS** | **97%** | **60.5 s** | **12,418** | **$0.0315** | **0.00** | **96%** |
| FA-MAS + reuse audit | 97% | 60.8 s | 13,424 | $0.0314 | 0.00 | 96% |

Against full replanning (C), FA-MAS used ~40% fewer tokens and ~32% less time at equal or better correctness;
against naive retry (D) it was correct far more often at a similar cost, because D cannot repair upstream causes,
decompose tasks or add missing dependencies. With *imperfect* error detection, incremental reuse can preserve a
corrupted result nobody has flagged yet; the optional reuse audit narrows that gap (see the
[research report](docs/research_report.md)). These are simulation results that demonstrate the mechanisms — they
are not measurements of real LLM behaviour.

![Mean tokens per run by scenario](benchmarks/results/fig_tokens_by_scenario.png)

## Quick start

Requires Python 3.11+ (Node 18+ for the UI).

```bash
pip install -e ".[all]"          # or: pip install -e ".[dev]" for the minimal offline setup
pytest                           # 80 tests, ~10 s, fully offline

# one run in the terminal: watch FA-MAS trace a silently corrupted result to its source
famas run "Analyze LangGraph, CrewAI, AutoGen, and Microsoft Agent Framework. Compare their system \
architectures, agent communication mechanisms, scheduling strategies, and failure recovery capabilities." \
  --scenario corruption --out report.md

famas bench --plots              # full baseline comparison -> benchmarks/results/
```

### Web console

```bash
famas serve                      # API on http://127.0.0.1:8000
cd frontend && npm install && npm run dev    # UI on http://localhost:5173
```

Or build the UI once (`npm run build`) and `famas serve` serves it at http://127.0.0.1:8000.
With Docker: `docker compose up --build` (FastAPI + UI + PostgreSQL).

### Live LLMs

Mock mode needs nothing. For real models, copy `.env.example` to `.env`:

```bash
FAMAS_LLM_PROVIDER=openai          # any OpenAI-compatible API (OpenAI, DeepSeek, Qwen, vLLM, Ollama, ...)
FAMAS_LLM_MODEL=gpt-4o
FAMAS_LLM_SMALL_MODEL=gpt-4o-mini
FAMAS_LLM_API_KEY=sk-...
# FAMAS_LLM_BASE_URL=https://api.deepseek.com/v1
GITHUB_TOKEN=ghp_...               # optional, raises GitHub API limits for repository research
```

then `famas run "..." --mode live` or pick *Live LLM* in the UI. Research agents read repository metadata,
READMEs and docs listings from the GitHub API; the coding agent writes Python and runs it in a sandboxed
subprocess. Failure injection also works in live mode, so recovery can be studied on real models.

## How it works

```
USER REQUEST → Task Analyzer → Planner → DAG Validator → Agent Selector → Dynamic Scheduler
                                   ▲                                         │  parallel agents
                                   │                                         ▼
                      Incremental Replanner ← Dependency Analyzer ← Failure Analyzer ← Result Evaluator
                                                                                         │ valid
                                                                                Result cache → Writer → report
```

| Layer | Modules |
|---|---|
| Planning | `planning/analyzer.py`, `planning/planner.py`, `planning/templates.py`, `planning/dag.py`, `planning/validator.py` |
| Orchestration | `orchestration/workflow.py` (LangGraph), `orchestration/dispatcher.py`, `orchestration/selector.py`, `orchestration/engine.py` |
| Execution | `agents/` (research, analysis, coding, writing, generalist, aggregator), `tools/github.py`, `tools/sandbox.py` |
| Reliability & recovery | `recovery/evaluator.py`, `recovery/failure_analyzer.py`, `recovery/dependency_analyzer.py`, `recovery/replanner.py`, `recovery/policies.py` |
| State & monitoring | `core/events.py`, `monitoring/metrics.py`, `persistence/store.py`, `api/` |
| Experiments | `simulation/` (failure injection, scenarios, ground truth), `benchmark/` |

Details: [architecture](docs/architecture.md) · [algorithms](docs/algorithms.md) ·
[evaluation methodology](docs/evaluation.md) · [API](docs/api.md) · [development guide](docs/development.md) ·
[research report](docs/research_report.md).

## Systems compared

| Key | System | Planning | Agents | On failure |
|---|---|---|---|---|
| `single_agent` | A — single agent | one monolithic task | generalist | restart |
| `fixed_workflow` | B — fixed pipeline | research → analysis → (code) → report | fixed | restart pipeline |
| `full_replan` | C — dynamic, full replanning | dynamic DAG | adaptive | same diagnosis, then re-plan and recompute everything |
| `naive_incremental` | D — dynamic, naive retry | dynamic DAG | adaptive | retry / reassign the failed task only |
| `fa_mas` | **FA-MAS** | dynamic DAG | adaptive | failure-aware, dependency-aware incremental replanning |
| `fa_mas_verify` | FA-MAS + reuse audit | dynamic DAG | adaptive | as FA-MAS, plus re-verification of preserved upstream results |

## Failure scenarios

`none`, `transient` (timeout), `agent_failure` (the chosen agent cannot do the task), `complex_task` (output
truncated when a task is too large), `corruption` (a plausible but wrong claim that passes its own checks),
`missing_dependency` (the planner forgot an edge), `late_failure` (the final report fails), `cascade` (several at
once), `stochastic` (random failures, seeded). `famas scenarios` lists them; custom `InjectionRule`s can be passed
per run.

## Project status and limitations

* The evaluation uses a simulator whose failure model was designed together with the system; it shows *that*
  the mechanisms work and what they cost, not how often real LLM agents fail in these ways.
* Incremental reuse keeps results nobody has flagged. If a corruption is never detected, FA-MAS (like every
  system that does not re-execute everything) delivers it; the reuse audit only helps during recovery rounds.
* When FA-MAS decides a request is simple enough for one agent, it inherits the single agent's lack of
  cross-checking (it does escalate to a multi-agent plan when that agent fails).
* The code sandbox contains accidents, not adversaries; use the Docker image or a dedicated sandbox for untrusted code.

MIT licensed.
