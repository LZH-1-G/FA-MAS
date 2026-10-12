# Development guide

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
pytest                    # offline, deterministic
ruff check famas tests
cd frontend && npm install && npm run dev
```

Run the API with auto-reload: `famas serve --reload`. The Vite dev server proxies `/api` to port 8000.

## Repository layout

```
famas/
  core/            data model, events, clocks, fingerprints
  planning/        task analyzer, planner, templates, DAG, validator
  orchestration/   LangGraph workflow, dispatcher, selector, engine, systems, run context
  agents/          research, analysis, coding, writing, generalist, aggregator; prompts
  recovery/        evaluator, failure analyzer, dependency analyzer, replanner, policies
  simulation/      failure injection, scenarios, ground-truth oracle
  llm/             mock client, LangChain client, pricing
  tools/           GitHub API client, code sandbox
  knowledge/       subject/aspect catalog, offline fixtures
  monitoring/      metrics
  persistence/     SQLite/PostgreSQL store
  api/             FastAPI app, run manager, live view
  benchmark/       dataset, runner, reports
  cli.py
frontend/          React + React Flow console
tests/             unit, integration, API and end-to-end tests
benchmarks/results committed benchmark outputs
docs/              architecture, algorithms, evaluation, API, research report
```

## Testing strategy

* **Unit**: DAG operations and fingerprints, validator, analyzer and templates, selector, failure injection,
  failure classification, impact analysis, decomposition and repair.
* **Integration**: dispatcher reuse/early cut-off, store, sandbox, mock LLM model, benchmark plumbing.
* **End-to-end**: every scenario through the full LangGraph workflow for FA-MAS and the baselines, including
  reproducibility of stochastic runs and the real-time clock path.
* **API**: run lifecycle over HTTP, SSE replay, validation errors, benchmark endpoint.

Everything runs on the mock LLM and the virtual clock, so the suite is fast and deterministic.

## Adding things

**An agent.** Append an `AgentSpec` in `famas/agents/specs.py`. If it is a new *kind*, implement
`BaseAgent.run(task, inputs, ectx)` and register it in `AGENT_CLASSES`. Call models only through
`ectx.llm(system=..., prompt=..., schema=..., mock=...)` and always provide a deterministic `mock` so tests and
benchmarks keep working. Return `AgentOutput(content, data={"units": {...}}, sources, issues)` — the evaluator
checks the units, and structured `issues` (missing information / inconsistency with `source_task`) are what the
failure analyzer uses for root-cause attribution.

**A scenario.** Add a `Scenario` with `InjectionRule`s to `famas/simulation/scenarios.py`. Targets are globs over
work units with placeholders `{s0}`, `{s1}`, `{s_last}`, `{a0}`.

**A recovery strategy.** Add the enum value, a rule in `FailureAnalyzer.decide` and the graph edit in
`IncrementalReplanner.apply`. Keep the invariant that only the repair set and new nodes change, and add a test
that checks reuse is still sound (see `tests/test_recovery.py`).

**A domain.** Free-form requests already work: the LLM planner (live mode) produces generic tasks executed by
`run_generic`. For structured domains, add templates in `planning/templates.py` and agents that emit units.

## Live mode notes

* `FAMAS_STRUCTURED_METHOD` selects LangChain's structured-output method (`function_calling` is the most widely
  supported; `json_schema` for strict OpenAI models). If structured output fails, the client retries with a
  JSON-only prompt and parses the reply.
* Live research uses the GitHub REST API (`/repos/{repo}`, `/readme`, `/contents/docs`). Unauthenticated
  requests are rate-limited; set `GITHUB_TOKEN`.
* The analysis agent's corruption detection in live mode is whatever the model reports in its `issues`; the
  simulation oracle is never consulted outside mock mode.
* Cost accounting uses `famas/llm/pricing.py`; override with `FAMAS_PRICE_IN_PER_1M` / `FAMAS_PRICE_OUT_PER_1M`.
