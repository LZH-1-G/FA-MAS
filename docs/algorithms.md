# Algorithms

This document specifies the core algorithms precisely enough to reimplement or analyse them. Code references
point to the implementation.

## 1. Model

A plan is a DAG `G = (V, E)` of tasks. An edge `u → v` means *v consumes u's output*. Each task `v` has

* a specification `spec(v)`: type, description, structured inputs, acceptance criteria, work units `U(v)`,
  accumulated recovery feedback and a revision counter;
* a status in `{pending, running, completed, failed, skipped, cancelled}`;
* at most one current result `r(v)` and possibly a `previous_result` kept after invalidation.

`ready(G) = { v pending : ∀u ∈ pred(v), status(u) ∈ {completed, skipped} }`.

## 2. Fingerprints (reuse and invalidation)

`famas/core/fingerprint.py`, `TaskGraph.input_fingerprint`.

```
out_fp(r)  = H(content(r), public_data(r))
in_fp(v)   = H( H(spec(v)),  { u ↦ out_fp(r(u)) | u ∈ pred(v) } )
```

A stored result `r` is **valid for v** iff `r.input_fingerprint = in_fp(v)` at dispatch time. Consequences:

* Changing a task's spec (e.g. adding recovery feedback) invalidates its own result.
* Changing an upstream *output* invalidates every downstream result transitively (Merkle property).
* Re-executing an upstream task that produces an **identical** output leaves downstream fingerprints unchanged,
  so invalidated downstream results are reused (*early cut-off*, as in build systems such as Shake/Bazel).

## 3. Eager dependency-aware scheduling

`Dispatcher.run_wave` (`orchestration/dispatcher.py`):

```
stop ← false
loop:
    if not stop:
        repeat: for v in ready(G): if valid result exists for in_fp(v): reuse it (completed)   # may cascade
        launch up to (max_concurrency − |running|) ready tasks
    if nothing running: break
    wait for the earliest completion (real time or virtual time)
    evaluate it → completed | failed
    if failed: stop ← true          # drain in-flight tasks, launch nothing new
return failures
```

In simulation mode, completions are processed in virtual-time order (`now + duration`), which yields the exact
makespan of the eager schedule, including stalls while recovery runs.

## 4. Evaluation

`ResultEvaluator.rule_checks`: a result passes iff it is well-formed (structured `units`), non-empty, delivers
every unit in `U(v)` (≥ 20 chars each) and carries no blocking issue reported by the agent. Agents report
structured issues: *missing information* (with subject/aspect) and *inconsistency* (with the upstream task
suspected as `source_task`). An optional LLM judge adds a semantic verdict.

## 5. Failure classification and strategy selection

`FailureAnalyzer.classify` / `decide` (`recovery/failure_analyzer.py`). Let `f` be the failed task, `a` its
agent, `n_a` the number of failures of `f` on `a`, `alt` the capable agents not yet excluded.

| Failure type | Evidence | Strategy (full mode) |
|---|---|---|
| `timeout`, `tool_error`, `resource_limit` | exception class / transient hint | retry if transient and `n_a < 2`, else reassign |
| `agent_error` | exception from the agent | non-transient → reassign; transient → retry, reassign after 2 |
| `invalid_output` | evaluator: malformed | retry with format feedback, reassign after 2 |
| `incomplete_output` | evaluator: missing units | **decompose** if `f` is splittable and coverage < 75 %; else retry with the list of missing parts, then reassign |
| `missing_information` | agent issue with subject/aspect | provider `p` exists and is not an ancestor → **add dependency** `p → f`; no provider → **add prerequisite** task; provider is an ancestor → **repair upstream** `p` |
| `context_inconsistency` | agent issue naming `source_task ∈ ancestors(f)` | **repair upstream** root cause(s) |
| budget exhausted (`#failures(f) ≥ max_attempts`) | — | optional → skip; single-agent task → escalate to multi-agent plan; else reassign to generalist once; else abort |

When no other agent can take a failing single-agent ("monolith") task, the strategy is **escalation**: the task is
decomposed into the multi-agent plan the planner would have produced.

`mode="naive"` (ablation D) only retries or reassigns `f`; it never attributes root causes, decomposes or edits
dependencies.

## 6. Impact analysis

`analyze_impact` (`recovery/dependency_analyzer.py`). With diagnoses `D`:

```
R  = { f : f failed }  ∪  ⋃ { roots(d) : d ∈ D, strategy(d) = repair_upstream }      # repair set
A  = R ∪ desc(R)                                                                       # affected region
Invalidated = completed ∩ A
Reused      = completed \ A
```

*Claim.* Every result in `Reused` remains valid after the patch: for `v ∉ A`, no ancestor of `v` is in `R`, and
the replanner modifies only tasks in `R` (spec changes, resets) and adds new tasks/edges whose heads are in `R`
or new; hence `in_fp(v)` is unchanged. (Decomposition keeps the decomposed node's id and outgoing edges and
only replaces its incoming edges, so the argument holds.)

Baselines use `scope="global"` (`A = V`) and naive retry uses `R = failed` only.

## 7. Incremental replanning

`IncrementalReplanner.apply` (`recovery/replanner.py`) applies, per diagnosis:

* **retry** — reset `f` to pending (feedback appended when the failure was about the output).
* **reassign** — add `a` to `f.excluded_agents`; the selector picks another agent.
* **decompose** — for a research task split over aspects, or an analysis task over subjects:
  1. *salvage*: units already delivered by the failed attempt become a completed node `f__partK` holding the
     partial result (re-fingerprinted against its own spec);
  2. the remaining units are split in two halves (binary decomposition — repeated if still too large), each a new
     task with `f`'s original dependencies;
  3. `f` becomes a deterministic `aggregate` node depending on the parts, so **its id and all downstream edges
     are unchanged**.
* **escalate** — a failing single-agent task is replaced by the multi-agent sub-plan (ids prefixed `f__`) feeding
  an aggregate node that returns the sub-plan's report.
* **add dependency / prerequisite** — `graph.add_dependency` refuses edges that would create a cycle.
* **repair upstream** `u` — increment `u.revision`, append the downstream complaint as feedback (which changes
  `spec(u)`), move `r(u)` to `previous_result`, reset `u`; every completed descendant of `u` is invalidated
  (results kept as `previous_result` for early cut-off). Repeated repairs also switch `u`'s agent.
* **skip** — mark `f` skipped; dependants run degraded with a note.
* **abort** — fail the run.

The patched DAG is re-validated before execution resumes.

### Reuse audit (optional, `fa_mas_verify`)

Incremental reuse keeps every result nobody has complained about — including a corrupted one nobody has
detected yet. The audit re-verifies, once per run each, the completed ancestors of tasks about to (re-)execute
using one small-model call per result; a flagged result is repaired like a root cause. It trades a bounded
amount of extra verification for fewer latent errors (see the research report for measurements).

## 8. Baseline policies

* **Restart** (A, B): reset every task, no diagnosis-driven changes; abort when the failing task's budget is spent.
* **Full replanning** (C): same diagnosis and the same graph fixes as FA-MAS (so the new plan addresses the
  failure), plus a planner call with the failure feedback, then **every** task is recomputed (no cache, no
  early cut-off). This isolates the effect of *recovery scope*.
* **Naive retry** (D): local scope, no root-cause attribution. This isolates the effect of *failure-aware
  dependency analysis*.

## 9. Agent selection

`AgentSelector._adaptive` (`orchestration/selector.py`). For capable, non-excluded agents:

```
rel   = posterior mean of Beta(4·p0 + successes, 4·(1−p0) + failures)       (per agent × task type)
fit   = c if large model else 1 − c        where c = task complexity ∈ [0,1] (work units, type, feedback)
spec  = 1 if specialist for the task type else 0.3
cost  = minmax( est_tokens × tier price / rel )      # expected cost per successful completion
lat   = minmax( latency_factor / rel )
score = 0.50·rel + 0.15·fit + 0.15·spec − 0.15·cost − 0.10·lat − 0.10·load
```

The breakdown is stored on the task and shown in the UI.

## 10. Complexity

Per recovery round: classification is O(|issues|); impact analysis is O(|V| + |E|) per repair-set member
(descendant search); decomposition and edge insertion are O(|V| + |E|) for cycle checks and graph rebuilds.
Fingerprints are O(|pred(v)|) per dispatch. For the plan sizes produced here (≤ 20 tasks) all of this is
negligible next to a single LLM call; the recovery *overhead* that matters is the optional LLM explanation and
replanner calls, which the metrics report separately.
