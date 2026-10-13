# Architecture

FA-MAS separates **what work exists** (the task DAG) from **how the system moves through its phases** (the
LangGraph workflow). Five layers sit on top of a small shared data model (`famas/core/models.py`).

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ Presentation   React + React Flow console  ·  FastAPI REST + SSE  ·  CLI                  │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Planning       TaskAnalyzer → Planner (LLM / template) → TaskGraph (NetworkX) → Validator │
│ Orchestration  LangGraph workflow · AgentSelector · Dispatcher (eager DAG scheduler)      │
│ Execution      Research / Analysis / Coding / Writing / Generalist / Aggregator agents    │
│                GitHub API tool · code sandbox · LLM clients (mock / LangChain / Agent SDK)│
│ Reliability    ResultEvaluator → FailureAnalyzer → DependencyAnalyzer → IncrementalReplanner│
│ State          EventBus · MetricsCollector · ResultCache · Store (SQLite/PostgreSQL)      │
│                LangGraph checkpointer                                                     │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

## 1. The workflow (LangGraph)

`famas/orchestration/workflow.py` builds a `StateGraph`:

```
START → analyze → plan → validate ──┬──> assign → execute ──┬──> finalize → END
                    ▲               │                       │
                    └──(invalid)────┘                       └──> diagnose → assess_impact → replan
                                    ▲                                                        │
                                    └────────────── patched DAG is re-validated ─────────────┘
```

| Node | Responsibility |
|---|---|
| `analyze` | `TaskAnalyzer`: subjects, aspects, complexity, single- vs multi-agent strategy. Instantiates failure injection for experiments. |
| `plan` | `Planner`: task list with dependencies, required capabilities, expected outputs and *work units* (`covers`). Applies planner-level fault injection (dropped edges) for experiments. |
| `validate` | `validate_graph`: cycles, unknown dependencies/capabilities, self-loops, empty plans. Auto-repairs what is safe; otherwise loops back to the planner (bounded), and after recovery aborts on an invalid patch. |
| `assign` | `AgentSelector` scores every capable agent per pending task; the rationale is stored on the task. |
| `execute` | `Dispatcher.run_wave`: eager, dependency-aware, concurrent execution with inline evaluation and fingerprint-based reuse. Stops launching on the first failure, drains in-flight tasks, returns failures. |
| `diagnose` | `FailureAnalyzer` (policy-specific): failure type, transience, root cause, strategy. |
| `assess_impact` | `DependencyAnalyzer`: repair set, affected region, results to reuse/invalidate. |
| `replan` | `IncrementalReplanner` (or a baseline policy) edits the DAG; records a `ReplanRecord` with overhead, reuse and (in simulation) invalid reuse. |
| `finalize` | Collects the sink output, computes `RunMetrics`, emits `run_completed` / `run_failed`. |

Graph state is JSON (serialised task DAG, analysis, failure records) so any checkpointer can persist it after
each node. Live services (LLM client, event bus, metrics, caches, injector) are kept in a `RunContext`
registered by `run_id` (`orchestration/context.py`) rather than in graph state. If LangGraph is not installed,
`graph_runtime.py` provides a minimal runtime with the same API subset so the engine still runs (used only in
constrained environments; the real dependency is declared in `pyproject.toml`).

## 2. The task DAG

`famas/planning/dag.py` wraps a NetworkX `DiGraph`. `Task.dependencies` is the single source of truth; the
NetworkX graph is rebuilt on every structural change so they cannot drift. The DAG supports ready-set queries,
ancestors/descendants, topological layers, critical-path length, serialisation and a compact UI view.

Every task declares the **work units** it must deliver, e.g. `research:crewai:scheduling` or
`report:failure_recovery`. Units drive three things: evaluation coverage, failure-injection targeting, and
decomposition (a task is split along its units).

## 3. Agents

| Agent | Tier | Capabilities | Notes |
|---|---|---|---|
| `research-fast` / `research-deep` | small / large | research, github | one subject × several aspects; GitHub API in live mode |
| `analysis-standard` / `analysis-expert` | small / large | analysis, comparison | one aspect × several subjects; reports missing information and contradictions as structured issues |
| `coder` | large | coding, code_execution | LLM writes `main(data)`; executed in `tools/sandbox.py` |
| `writer` | large | writing, reporting | final Markdown report; references appended programmatically |
| `generalist` | large | everything | single-agent baseline, simple requests, escalation target |
| `aggregator` | none | aggregate | deterministic merge node created by decomposition |

All agents call models through `ExecContext.llm(...)`, passing a deterministic `mock` callback. The same code
path therefore runs against a real model (LangChain, or Claude Code via the Claude Agent SDK, see
[claude-agent-sdk.md](claude-agent-sdk.md)) or the mock model (tests, benchmarks). Typed tasks run by
the generalist use the specialists' procedures, so escalating to the generalist never weakens validation.

## 4. Scheduling

`Dispatcher` (`orchestration/dispatcher.py`) is LLMCompiler-style eager dispatch over the DAG:

* A task starts as soon as its dependencies are completed and validated (not at the next "round").
* Concurrency is bounded (`RunConfig.max_concurrency`).
* Before executing, the task's input fingerprint is compared with (a) the result it had before an invalidation
  and (b) the result cache. A match is reused without execution.
* Two clocks: real time (live LLMs, paced demos) and a discrete-event simulation on a virtual clock (mock runs),
  so benchmark latencies are deterministic and still reflect parallelism and recovery stalls.

## 5. Reliability and recovery

See [algorithms.md](algorithms.md) for the precise rules. The pieces:

* `ResultEvaluator` — format, non-empty, unit coverage, agent-reported blocking issues, sources; optional LLM judge.
* `FailureAnalyzer` — exception and evaluation failures → `FailureType`, transience, root cause, `RecoveryStrategy`.
  `mode="full"` for FA-MAS and full replanning, `mode="naive"` for the ablation.
* `analyze_impact` — `scope="local"` (FA-MAS), `"failed_only"` (naive), `"global"` (baselines).
* `IncrementalReplanner` — retry, reassign, decompose (with salvage), escalate a single-agent task to a
  multi-agent sub-plan, add edge, add prerequisite, repair upstream, skip, abort; optional reuse audit.
* `policies.py` — `IncrementalPolicy`, `NaivePolicy`, `FullReplanPolicy`, `RestartPolicy`.

## 6. State, events and persistence

* **Events** (`core/events.py`): every decision is an `Event` (`plan_created`, `task_started`, `task_failed`,
  `failure_diagnosed`, `impact_analyzed`, `replan_applied`, …) with a monotonically increasing sequence number and
  run-clock timestamp. The API persists them and streams them over SSE; the UI folds them into a live view.
* **Metrics** (`monitoring/metrics.py`): token/cost/latency per component and per agent, executions, failures,
  reuse, invalidation, redundant executions, wasted tokens, recovery overhead.
* **Store** (`persistence/store.py`): runs, events, a content-addressed result cache, learned agent statistics
  and benchmark results; SQLite by default, PostgreSQL via `FAMAS_DATABASE_URL`. A thin portable DB-API layer
  keeps the dependency footprint small.
* **Checkpointing**: graph state is JSON, so any LangGraph checkpointer works: pass one to
  `Engine(checkpointer=...)` (e.g. a SQLite or Postgres saver), or set `FAMAS_CHECKPOINTER=memory` for the
  in-memory saver. It is off by default because the API server is long-lived and the run's results, events and
  cache are already persisted by the store.

## 7. Extension points

| To add… | Do this |
|---|---|
| an agent | add an `AgentSpec` to `agents/specs.py` (and an implementation in `agents/registry.AGENT_CLASSES` if it is a new kind) |
| a tool | implement it in `famas/tools/` and use it from an agent via `ectx.run` |
| an LLM provider | implement `LLMClient.complete` (and optionally `preflight`, `live_tools`) and return it from `famas/llm/__init__.create_llm`; raise `LLMFatalError` subclasses for conditions that must abort a run |
| a failure type / strategy | extend `FailureAnalyzer.decide` and `IncrementalReplanner.apply` |
| a baseline | add a `RecoveryPolicy` and a `SystemSpec` in `orchestration/systems.py` |
| a scenario | add a `Scenario` to `simulation/scenarios.py` |
| a domain | add plan templates or rely on the LLM planner (generic tasks run through `run_generic`) |
