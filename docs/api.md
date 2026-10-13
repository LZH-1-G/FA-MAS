# HTTP API

Start with `famas serve` (default `http://127.0.0.1:8000`). Interactive OpenAPI docs: `/docs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | status, LangGraph availability, LLM provider, `live_available` + `live_reason`, provider status (`llm`: no secrets), database dialect |
| GET | `/api/meta` | systems, scenarios, agents, example requests, benchmark tasks, `live_available`, `live_reason`, `llm_provider` |
| GET | `/api/agents` | agent registry + learned per-agent statistics |
| POST | `/api/runs` | start a run (returns `run_id`) |
| GET | `/api/runs` | recent runs with headline metrics |
| GET | `/api/runs/{id}` | snapshot: status, analysis, DAG view, replans, metrics, final output |
| GET | `/api/runs/{id}/tasks/{task_id}` | task detail: spec, agent selection rationale, evaluation, failures, result, fingerprints |
| GET | `/api/runs/{id}/events?after=N` | Server-Sent Events: replay from `N`, then live; ends with `event: end` |
| GET | `/api/runs/{id}/report` | final report (Markdown) |
| DELETE | `/api/runs/{id}` | delete a finished run |
| POST | `/api/benchmarks` | start a benchmark in the background |
| GET | `/api/benchmarks`, `/api/benchmarks/{id}` | progress and summary (`?include_rows=true` for raw rows) |

## Start a run

```bash
curl -s -X POST localhost:8000/api/runs -H 'content-type: application/json' -d '{
  "request": "Compare the architectures of LangGraph and CrewAI.",
  "system": "fa_mas",
  "scenario": "corruption",
  "mode": "mock",
  "simulated": false,
  "time_scale": 0.08,
  "max_concurrency": 4,
  "detection_probability": 1.0
}'
# {"run_id": "run_3f2a9c1d0b7e"}
```

* `simulated: true` runs instantly on the virtual clock; `false` paces mock agents in real time
  (`time_scale` × modelled duration) so the UI can be watched.
* `mode: "live"` requires a configured provider (see `.env.example`); otherwise the API answers `400` with
  the reason. With `FAMAS_LLM_PROVIDER=claude_agent_sdk` and real calls enabled, CORS is restricted to the
  local UI and `famas serve` only binds to localhost (see [claude-agent-sdk.md](claude-agent-sdk.md)).
* `injection`: optional list of custom fault rules, e.g.
  `[{"kind": "timeout", "target": "research:crewai:*", "on_executions": [1]}]`.

## Stream events

```bash
curl -N localhost:8000/api/runs/run_3f2a9c1d0b7e/events
```

```
id: 12
event: task_failed
data: {"seq": 12, "type": "task_failed", "task_id": "analyze_architecture", "t_ms": 13090.4,
       "data": {"agent": "analysis-standard", "error_type": "inconsistency", "message": "Findings from `research_crewai` contradict ..."}}
```

Event types: `run_started`, `analysis_completed`, `plan_created`, `plan_validated`, `agents_assigned`,
`task_started`, `task_completed`, `task_failed`, `task_reused`, `wave_finished`, `failure_diagnosed`,
`impact_analyzed`, `replan_applied`, `run_completed`, `run_failed`.

## Python API

```python
from famas import Engine, RunConfig

engine = Engine()
outcome = engine.run_sync(
    "Compare the architectures of LangGraph and CrewAI.",
    RunConfig(system="fa_mas", scenario="late_failure"),
)
print(outcome.status, outcome.metrics.total_tokens, outcome.metrics.reuse_rate)
for record in outcome.replans:
    print(record.round, [d.strategy for d in record.diagnoses], record.reused, record.invalidated)
print(outcome.final_output)
```
