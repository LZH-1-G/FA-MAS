# Real Claude validation plan (Tests A, B, C)

> **Experimental, personal local testing only.** The Claude Agent SDK provider is experimental. It is meant
> for the account holder to test FA-MAS on their own machine with their own Claude login. Mock mode is the
> default and the supported public execution mode. FA-MAS does not offer, host or broker Claude sign-in for
> anyone else. See [claude-agent-sdk.md](claude-agent-sdk.md) for Anthropic's requirements.

**Status: executed on 2026-10-13.** The maintainer ran Tests A, B and C on their own Mac with their own Claude Pro
login, in the order and with the approvals below. All three completed; see [Results](#results-2026-10-13).
Every step below that makes a model call is marked **REAL CALL** and only runs when you type the command yourself.

**Order and approvals.**

1. Local offline verification on your Mac (section 0). This makes no model calls.
2. **Test A** (2 calls), once the offline checks pass.
3. **Test B**, only after a separate approval, once Test A's report has been reviewed.
4. **Test C**, only after another separate approval, once Test B's report has been reviewed.

Do not chain the tests in one command or script.

The tests use `famas claude validate`, which:

* tightens the provider limits;
* refuses to run real calls unless `FAMAS_CLAUDE_ALLOW_REAL_CALLS=true` is set **and** you pass `--yes`;
* writes a JSON report plus a JSONL usage log to `data/claude-validation/` (gitignored).

Reports contain metadata only: tokens, models, timings, Claude Code's API-equivalent cost estimate and
rate-limit status. They contain no prompts, no outputs, no session ids and no credentials.

## Results (2026-10-13)

The maintainer ran the tests on macOS with claude-agent-sdk 0.2.165, signed in with a Claude Pro plan through
the claude.ai login (first-party, no API key). All three completed within their call caps.

* No rate-limit rejection or overage was reported.
* FA-MAS's usage guard never halted.
* No usage credits were used.

The figures below come from the metadata-only reports (`data/claude-validation/`, gitignored). Those reports
are kept unchanged as the record of the runs.

| | Test A: smoke | Test B: real workflow | Test C: failure recovery |
|---|---|---|---|
| Report | `20261013-145423-test-a.json` | `20261013-145714-test-b.json` | `20261013-150803-test-c.json` |
| Status | completed; plain reply `pong` ✓, structured reply valid ✓ | completed: 11/11 tasks | completed: 10/10 tasks, 1 recovery round |
| Prompts sent to Claude (cap) | 2 (2) | 11 (12) | 12 (16) |
| Prompts by component | 2 × smoke | analyzer 1, research 2, analysis 7, writer 1 | analyzer 1, research 2, failure analyzer 1, analysis 7, writer 1 |
| Run metrics | – | `llm_calls` 11, `agent_invocations` 11 | `llm_calls` 13 (12 real + 1 simulated), `agent_invocations` 11 |
| Recovery | – | none needed | the injected timeout of `research_langgraph` was diagnosed; that task was retried, `research_crewai` reused, nothing invalidated |
| Models (prompts) | Haiku ×2 | Haiku ×9, Sonnet ×2 (deep research, writer) | Haiku ×10, Sonnet ×2 (deep research, writer) |
| Tokens: input / output / cache write / cache read | 4 / 78 / 1,831 / 0 | 22 / 19,198 / 29,108 / 5,808 | 24 / 19,763 / 37,092 / 10,206 |
| API-equivalent estimate* | $0.000656 | $0.123258 | $0.122186 |
| Wall time | 17.8 s | 123.6 s | 133.2 s |
| Report length | – | 11,783 characters | 12,194 characters |

\*Claude Code's client-side estimate at API list prices. Usage counted against the Pro plan's limits; there was
no per-call charge.

**Observations.**

* **Larger plans than the dry run.** Real request analysis planned 7 analysis tasks where the scripted dry run
  plans 1, so Tests B and C sent 11 and 12 prompts instead of 5 and 6. Test B used 11 of its 12-call cap. A
  richer plan would make the run stop at the cap, by design, rather than exceed it.
* **Test C, 12 prompts vs 13 `llm_calls`.** The difference is the injected failure. The `transient` scenario
  makes the first execution of `research_langgraph` time out *without calling Claude*. FA-MAS still charges that
  attempt like a call that ran into the client timeout (prompt tokens, cost, 20 s), so the run's failure
  analysis, overhead and cost metrics see a realistic failure. That simulated call is counted once in
  `llm_calls`.
  * From v0.1.0-beta the run metrics report it separately as `simulated_llm_calls`, and each validation report
    adds an `accounting` block with `model_calls = llm_calls − simulated_llm_calls`. It must equal
    `prompts_sent`; for this run, 13 − 1 = 12.
  * `agent_invocations` (11) counts attempts: 10 tasks plus the retried one, including the failed attempt that
    made no call.
* **Per-call costs.** The API-equivalent totals above come from Claude Code's per-call reports. The run-level
  `cost` in Test C's run metrics also contains the modelled cost of the simulated call.

## 0. Before any real call (free, no model calls)

Run these on your Mac, in the project folder:

```bash
cd ~/Developer/AI/projects/fa-mas
source .venv/bin/activate
pip install -e ".[dev,claude]"              # adds claude-agent-sdk (bundles the Claude Code CLI)

scripts/verify-offline.sh                   # every offline check, including scripted dry runs of
                                            # Tests A, B and C; must end with "RESULT: all checks passed"

env | grep -E '^(ANTHROPIC_|CLAUDE_CODE_USE_)' | cut -d= -f1   # must print nothing (names only)
claude                                      # sign in with /login using your Claude (Pro) account,
                                            # check /status shows your Claude account, then /exit
famas claude doctor --offline-test --check-login   # handshake only: confirms "pro plan via claude.ai login"
```

The script runs the dry runs for you. To repeat one by hand (scripted responses, no network, no usage):
`FAMAS_LLM_PROVIDER=claude_agent_sdk FAMAS_CLAUDE_DRY_RUN=true famas claude validate a` (or `b`, `c`).

**Keep usage credits off.** Anthropic's Help Center calls extra usage "usage credits". Check that usage
credits are **not enabled** in Claude → Settings → Usage. They are off unless you enable and prepay them
([Help Center](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans)).
FA-MAS cannot see or change this setting. It stops at the first sign of overage, but only the setting
itself guarantees that nothing is billed beyond your plan.

## The three tests

Run one test at a time. Enable real calls for that single command only, so nothing stays switched on in
your terminal afterwards:

```bash
# Test A (REAL CALL, 2 calls). Run only after section 0 passes.
FAMAS_LLM_PROVIDER=claude_agent_sdk FAMAS_CLAUDE_ALLOW_REAL_CALLS=true famas claude validate a --yes
```

Tests B and C use the same form with `b` or `c`. **Each needs its own separate approval** after the previous
report has been reviewed.

| | Test A: smoke | Test B: real workflow | Test C: failure recovery |
|---|---|---|---|
| Approval | after the offline checks | **separate approval**, after Test A | **separate approval**, after Test B |
| Command (**REAL CALL**) | `famas claude validate a --yes` | `famas claude validate b --yes` | `famas claude validate c --yes` |
| What runs | 1 plain + 1 structured reply | analysis → 2 research agents → 1 analysis agent → report, for "Compare the architectures of LangGraph and CrewAI." | the same workflow with scenario `transient`: the first research execution times out (injected, makes no call), FA-MAS diagnoses it, retries only that task and reuses the other research result |
| Model calls (dry-run count) | 2 | 5 (real run: 11) | 6 (real run: 12) |
| Hard cap (aborts beyond) | 2 | 12 | 16 |
| Models (dry run) | haiku ×2 | haiku ×4, sonnet ×1 (writer) | haiku ×5, sonnet ×1 (writer) |
| Est. tokens* | < 2k | ~15k–40k input, ~4k–10k output | ~18k–45k input, ~5k–11k output |
| Est. API-equivalent cost* | < $0.01 | ~$0.05–$0.25 | ~$0.06–$0.30 |
| Other network | none | ~6 unauthenticated GitHub API requests (limit 60/h) | same (cached) |
| Wall time | ~10–30 s | ~1–3 min | ~1–4 min |

\*These are pre-run estimates from prompt sizes and API list prices. They are not measurements, and the
cost is not a charge. The measured figures are under [Results](#results-2026-10-13). Output tokens (about 19k
for each of B and C) exceeded the estimate because the real plans were larger. The API-equivalent cost stayed
inside the estimated range.

**What the calls consume.** On Claude Pro, all calls draw on your plan's shared usage limits: a session
limit that resets every five hours and a weekly limit
([Help Center](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)). This is
the same allowance as your claude.ai chats. Anthropic does not publish exact Pro quotas, so FA-MAS cannot
predict what percentage a test uses. If Claude Code reports utilization, it appears in each report under
`rate_limit`.

**API-equivalent cost.** Claude Code computes `total_cost_usd` locally from list prices. The docs call it a
"client-side estimate, not authoritative billing data"
([cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)). FA-MAS records it as
`api_equivalent_cost_usd` to compare runs across providers. With your Pro login and usage credits off,
there is no per-call charge.

**Limits enforced for every test:**

* at most 2 parallel Claude Code sessions;
* at most 3 turns and 180 s per call;
* an SDK budget cap of $0.50 (estimated) per call;
* at most 2 recovery rounds per run;
* **no retries** after a usage-limit, credential or budget error: those abort the run immediately.

**Optional variant of Test C.** `famas claude validate c --yes --scenario missing_dependency` exercises
dependency repair instead of a retry. The plan omits an edge, the analysis agent reports the missing
input, and FA-MAS adds the dependency and recomputes only the affected node, reusing both research results.
It makes 7 calls in the dry run; the cap is 16.

**When finished:** if you exported `FAMAS_CLAUDE_ALLOW_REAL_CALLS` instead of using the one-line form,
run `unset FAMAS_CLAUDE_ALLOW_REAL_CALLS`.

## Reading the results

Each run prints a summary and writes `data/claude-validation/<time>-test-<x>.json`:

| Field | Meaning |
|---|---|
| `outcome.status` | `completed`, `failed` or `completed_with_unexpected_output` (Test A checks) |
| `outcome.account` | non-secret login summary from Claude Code's handshake, e.g. `{"subscription_type": "pro", "api_provider": "firstParty"}` |
| `totals.prompts_sent` | prompts that actually reached Claude, from the provider's per-call records |
| `outcome.llm_calls` | LLM calls charged by the run metrics: real calls plus simulated calls (Tests B and C) |
| `outcome.simulated_llm_calls` | injected failures charged like a timed-out call; **no prompt is sent** (Test C: 1) |
| `outcome.agent_invocations` | agent task executions, including failed attempts that made no call |
| `accounting` | reconciliation: `model_calls = llm_calls − simulated_llm_calls`, `consistent` when it equals `prompts_sent`. Reports written before this field existed (the 2026-10-13 runs) lack it; apply the formula by hand. |
| `totals.*_tokens` | tokens reported by Claude Code (input, output, cache write, cache read) |
| `totals.api_equivalent_cost_usd_estimate` | sum of Claude Code's estimates. **Not a charge.** |
| `calls[]` | one metadata record per call: component, model, tokens, turns, durations, outcome, last rate-limit event |
| `rate_limit.after` | the last rate-limit status Claude Code reported (`allowed`, `allowed_warning`, `rejected`, overage flags) |

## What to do if something goes wrong

| Symptom | Meaning | Action |
|---|---|---|
| `LLMAuthError … API key` | Claude Code would bill an API key, Console login or gateway | Remove the key or `apiKeySource`, then `claude` → `/login` with your Claude account |
| `LLMAuthError … not signed in` | no subscription login found | `claude` → `/login` |
| `LLMUsageLimit … usage limit reached` | your Pro window is exhausted | wait for the reset time shown; FA-MAS halts until then |
| `LLMUsageLimit … usage credits / extra usage` | Claude reported overage | turn usage credits off; restart FA-MAS (the halt lasts until restart) |
| `LLMOutputError … structured output` | the native schema mode misbehaved | `FAMAS_CLAUDE_STRUCTURED_OUTPUT=prompt`, then re-run Test A |
| any stall | | Ctrl-C. Sessions are closed and no further calls are made. |

Claude Code keeps its own local session transcripts, including prompts and fetched README text, under
`~/.claude/projects/`. They stay on your Mac. Delete that run's folder if you do not want to keep them.
