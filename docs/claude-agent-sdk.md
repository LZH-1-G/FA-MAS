# Claude Agent SDK provider (experimental, personal local testing)

> **Experimental.** This provider is for **personal, local testing by the account holder only**. Mock mode
> is FA-MAS's default and its supported public execution mode. FA-MAS does **not** offer, provide, host or
> broker Claude sign-in for anyone, and must not be deployed so that other people's requests use your Claude
> plan. Anything built for other users must use API-key authentication, as Anthropic requires for products
> and services built on the Agent SDK.

`FAMAS_LLM_PROVIDER=claude_agent_sdk` runs FA-MAS's agents on Claude through the official
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) (`claude-agent-sdk`). The SDK drives the
unmodified Claude Code CLI, which is signed in to **your own Claude account** through Anthropic's own
`/login`. No API key is involved. LangGraph orchestration, DAG scheduling, agent selection and incremental
replanning are unchanged. The provider only changes where completions come from.

```
LangGraph workflow ─▶ Dispatcher ─▶ agent ─▶ LLMClient.complete()
                                               │
                         mock (default) ◀──────┼──────▶ ClaudeAgentSDKLLM
                                               │          │  one short Claude Code session per call:
                                               │          │  start → handshake → check login → prompt → result → close
                                               ▼          ▼
                                       LangChainLLM   ClaudeSDKClient ─▶ Claude Code CLI ─▶ Anthropic
                                     (API-key providers)                  (your /login session)
```

> **Status (v0.1.0-beta, experimental).** Everything in this page is implemented and tested offline. The three
> bounded real tests in [real-claude-validation.md](real-claude-validation.md) were run by the maintainer on
> 2026-10-13, with their own Claude Pro login and separate approval for each test, and all completed. Real
> calls stay disabled until you set `FAMAS_CLAUDE_ALLOW_REAL_CALLS=true` yourself.

## 1. Can a Claude Pro subscription officially run this?

**Short answer: for your own, ordinary, individual testing on your own machine, Anthropic's published
material describes this use. It counts against your plan's usage limits. It is not a way to run FA-MAS for
other people.**

The official sources, re-checked on 2026-10-13:

* **Agent SDK usage is billed to your plan's limits.** The Help Center article
  [*Use the Claude Agent SDK with your Claude plan*](https://support.claude.com/en/articles/15036540) says
  that Claude Agent SDK, `claude -p` and third-party app usage "still draw from your subscription's usage
  limits". A planned change that would have moved SDK usage to a separate monthly credit from June 15, 2026
  was paused, and that credit "isn't available".
* **Limits are shared.**
  [*Using Claude Code with your Pro or Max plan*](https://support.claude.com/en/articles/11145838) says Pro
  and Max limits are "shared across Claude and Claude Code". FA-MAS runs use the same allowance as your chats
  on claude.ai.
* **Individual use is the intended scope.** Claude Code's
  [Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance) page says that advertised Pro
  and Max limits "assume ordinary, individual usage of Claude Code and the Agent SDK". OAuth (subscription)
  sign-in is "intended exclusively for purchasers of Claude … subscription plans".
* **Products and services for others need API keys.** The same page says that developers building products
  or services, including those using the Agent SDK, should use API keys. Anthropic does not permit offering
  Claude.ai login inside third-party apps, or routing requests through Free, Pro or Max plan credentials on
  behalf of other users. Developers must not collect, store or intermediate Claude.ai credentials or session
  tokens, and sign-in must complete through Anthropic's own flow.
* **Third-party products are a different case.** The
  [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) says: "Unless previously approved,
  Anthropic does not allow third party developers to offer claude.ai login or rate limits for their
  products, including agents built on the Claude Agent SDK." The same page says use of the SDK is governed by
  Anthropic's Commercial Terms of Service. **FA-MAS's position for this release:**
  * mock mode is the default public mode;
  * the Claude provider is labelled experimental and documented only for the account holder's own local
    testing;
  * FA-MAS offers no login and adds no rate-limit or credential handling of its own;
  * `famas serve` refuses to expose it beyond `127.0.0.1`;
  * anyone serving other users is directed to API-key authentication.
* **Branding.** Following the Agent SDK branding guidelines, the UI labels this option "Claude Agent
  (experimental)". FA-MAS does not call itself or any feature "Claude Code", and it does not suggest any
  endorsement by Anthropic.
* **Pro gets no API credits.** The monthly API credits announced in October 2026 are for Max and Team only.
  [*Monthly API credits for Max and Team plans*](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-and-team-plans)
  says "Free, Pro, and Enterprise plans aren't eligible". The credits apply to API keys anyway. With a plan
  sign-in, "`claude -p` and Agent SDK usage still draw from your plan's usage limits".

### How FA-MAS stays inside those rules

FA-MAS never handles credentials. It does not read, copy, store, log or forward any token or key. Claude Code
authenticates with the login you created through Anthropic's own `/login` flow, and FA-MAS only sees a
non-secret summary of that login: plan type and credential source, never your email.

FA-MAS also adds guardrails for individual use:

* `famas serve` refuses to listen on anything other than `127.0.0.1`/`localhost` while real Claude calls
  are enabled, and there is no override.
* The API answers only `localhost`/`127.0.0.1` Host headers, which blocks DNS rebinding.
* CORS is restricted to the local UI, so other websites cannot start runs on your plan.
* Claude Code runs with no tools, so text injected into fetched README files cannot make it act. FA-MAS also
  fences that text as untrusted data in the prompt.

This is my reading of Anthropic's published terms, not legal advice. Anthropic decides how its terms apply.
If you want to share FA-MAS, host it, or run it for anyone else, use an API key with the existing
`anthropic` provider, which is billed separately. For questions about your use case, Anthropic points to
its [sales team](https://www.anthropic.com/contact-sales).

## 2. What costs money, and what FA-MAS does about it

| Situation | Billing effect | What FA-MAS does |
|---|---|---|
| Real calls with your Pro login, within your limits | No additional charge. Uses your shared Pro allowance. | Counts calls and caps them per run (`FAMAS_CLAUDE_MAX_CALLS_PER_RUN`). |
| A usage limit is reached | Requests are rejected until the window resets, unless usage credits are on. | Stops the call. Halts the provider until the reset time. Aborts the run instead of retrying. |
| **Usage credits** (formerly "extra usage") enabled in your Claude account | Requests beyond the plan are billed at standard API rates. They are off unless you enable and prepay them ([12429409](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans)). | Stops at the first sign of overage (`isUsingOverage`, `overageInUse` or an `overage` limit type). Halts until restart. FA-MAS never changes this setting. |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_PROFILE`, `ANTHROPIC_FEDERATION_RULE_ID` or `CLAUDE_CODE_USE_BEDROCK`/`VERTEX`/`FOUNDRY` set | Claude Code would use the API key, gateway, Console profile or cloud account instead of your subscription ([authentication precedence](https://code.claude.com/docs/en/authentication)). | Refuses to start. Names the variable, never its value. |
| An API key from `apiKeyHelper`, a Console login or settings files | Same as above: API billing. | FA-MAS sessions ignore settings files (`setting_sources=[]`). The handshake check below refuses any API-key credential source **before the prompt is sent**. |
| Bedrock, Vertex, Foundry or a gateway backend | Billed by that provider. | Refused unless Claude Code reports `apiProvider = firstParty`. |

**The account handshake check.** For every call, FA-MAS starts Claude Code and completes the SDK's
`initialize` handshake before any prompt is written. The handshake response includes an `account` block.
FA-MAS proceeds only if all of these hold:

* `apiProvider` is `firstParty`;
* there is **no** `apiKeySource`;
* a subscription is reported, either as `subscriptionType` or as a `CLAUDE_CODE_OAUTH_TOKEN` from
  `claude setup-token`.

Anything else raises an error and the session is closed with no prompt sent. If Claude Code does not report
the block at all, FA-MAS refuses (fail closed).

**What FA-MAS cannot guarantee:**

* **Usage credits.** Your account's usage-credits setting (formerly "extra usage") is the only hard
  protection against overage charges. Keep it **off** (Settings → Usage). FA-MAS cannot see or change it. FA-MAS reacts to Claude Code's rate-limit events, which
  arrive with or after a response, so up to `FAMAS_CLAUDE_MAX_PARALLEL` requests already in flight (default
  2) could complete before the stop takes effect.
* **Cost figures.** The `cost` figures in FA-MAS metrics and the UI are **API-equivalent estimates**
  reported by Claude Code (`total_cost_usd`). Anthropic calls these "client-side estimates, not
  authoritative billing data" ([cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)).
  They are kept so runs can be compared across providers. On a subscription they are not charges.
* **Your allowance.** A run uses several model calls, measured in dry runs:
  * 5 for a two-framework comparison;
  * 7 for the three-framework example;
  * 6 to 15 with failure recovery.

  These come out of the same five-hourly and weekly allowance as your claude.ai chats. Anthropic does not
  publish exact Pro quotas. Use `haiku` for the small tier (the default) and keep
  `FAMAS_CLAUDE_MAX_CALLS_PER_RUN` low while experimenting.

## 3. Setup

```bash
pip install -e ".[claude]"            # installs claude-agent-sdk, which bundles the Claude Code CLI
claude                                # once, in a terminal: sign in with /login using your Claude account
                                      # (check with /status that the login method is your Claude account)
unset ANTHROPIC_API_KEY               # and any other variable listed in the table above

famas claude doctor --offline-test    # SDK, CLI, environment, offline round trip: no model call
famas claude doctor --check-login     # starts Claude Code, reads the login from the handshake: no prompt sent
```

### Rehearse for free (dry run)

```bash
FAMAS_LLM_PROVIDER=claude_agent_sdk FAMAS_CLAUDE_DRY_RUN=true \
  famas run "Compare LangGraph, CrewAI and AutoGen on scheduling and failure recovery" --mode live --scenario corruption
```

A dry run uses the real SDK client (`ClaudeSDKClient`) with an offline `ScriptedTransport` that plays back each
component's deterministic reply. Planning, agents, evaluation, failure injection and incremental replanning
all run through the provider. No process is started, no network is used and nothing is billed. Research
agents use the offline knowledge fixtures in dry runs. With `FAMAS_CLAUDE_DRY_RUN=true`, the UI's *Mode* menu
offers "Claude Agent (experimental)".

### Real calls (your explicit decision)

Follow [real-claude-validation.md](real-claude-validation.md): Test A (2 calls), then B (real workflow),
then C (failure recovery). Each runs with tight caps and writes a usage report.

```bash
export FAMAS_LLM_PROVIDER=claude_agent_sdk
export FAMAS_CLAUDE_ALLOW_REAL_CALLS=true   # the switch; default false
famas claude validate a --yes               # exactly 2 tiny calls (plain + structured) on the small model
famas run "..." --mode live                 # afterwards: any run, or the UI
```

## 4. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FAMAS_CLAUDE_ALLOW_REAL_CALLS` | `false` | Master switch for real model calls. |
| `FAMAS_CLAUDE_DRY_RUN` | `false` | Scripted, offline responses through the real SDK. Takes precedence over the switch above. |
| `FAMAS_CLAUDE_MODEL` / `FAMAS_CLAUDE_SMALL_MODEL` | `sonnet` / `haiku` | Claude Code model aliases or full model ids, for large-tier and small-tier components. |
| `FAMAS_CLAUDE_MAX_CALLS_PER_RUN` | `40` | Per-run budget. Exceeding it aborts the run. |
| `FAMAS_CLAUDE_MAX_PARALLEL` | `2` | Concurrent Claude Code sessions per process. |
| `FAMAS_CLAUDE_MAX_TURNS` | `3` | Turn limit per call. More than 1 leaves room for the structured-output tool turn. |
| `FAMAS_CLAUDE_TIMEOUT_S` | `300` | Per-call timeout. |
| `FAMAS_CLAUDE_MAX_BUDGET_USD_PER_CALL` | `1.0` | SDK `max_budget_usd` on the API-equivalent estimate. Guards against runaway calls. `none` disables it. |
| `FAMAS_CLAUDE_STRUCTURED_OUTPUT` | `native` | `native`: SDK `output_format` JSON schema. `prompt`: JSON requested in the prompt. |
| `FAMAS_CLAUDE_STOP_ON_LIMIT_WARNING` | `false` | Also stop when Claude reports you are close to a limit. |
| `FAMAS_CLAUDE_CLI_PATH` | bundled | Use a specific `claude` binary. |
| `FAMAS_CLAUDE_WORKDIR` | `./data/claude-workdir` | Empty working directory for the sessions. |
| `FAMAS_CLAUDE_USAGE_LOG` | `./data/claude-usage.jsonl` | One metadata record per call: tokens, model, timings, API-equivalent estimate, rate-limit status. No prompts, outputs or credentials. `none` disables it. |
| `FAMAS_ALLOW_GENERATED_CODE` | `false` | Live mode: run Python written by the model in the code sandbox. Off by default; the coding agent then uses FA-MAS's built-in program. |

`FAMAS_CLAUDE_AUTH` accepts only `subscription`. For API-key billing, use `FAMAS_LLM_PROVIDER=anthropic`.

## 5. How a call works

`ClaudeAgentSDKLLM.complete()` (`famas/llm/claude_agent_sdk_client.py`):

1. **Before any session starts.** Checks the master switch, conflicting environment variables, the
   process-wide usage guard and the per-run call budget.
2. **Session options.** Starts a session through `ClaudeSDKClient` with:
   * `tools=[]`: no shell, file or web tools;
   * `permission_mode="dontAsk"`;
   * `setting_sources=[]` and `skills=[]`: isolated from your settings, hooks and CLAUDE.md;
   * FA-MAS's system prompt in place of Claude Code's;
   * the turn, budget and timeout limits above;
   * an empty working directory;
   * FA-MAS's own secrets (`GITHUB_TOKEN`, other providers' keys) blanked in Claude Code's environment.
3. **Handshake.** Runs `connect()` and verifies the `account` block.
4. **Prompt.** Sends the prompt and collects `AssistantMessage`, `RateLimitEvent` and `ResultMessage`.
   Rate-limit events feed the usage guard.
5. **Close.** Closes the session, which ends the Claude Code process.
6. **Record.** Appends one metadata record to the usage log.
7. **Result.** Maps the output onto `LLMResponse`:
   * text;
   * the parsed schema, from `structured_output`, falling back to JSON found in the text;
   * tokens, including cache reads;
   * the API-equivalent cost estimate;
   * the model Claude Code reports.

Errors are mapped onto FA-MAS's failure model:

* timeouts and overloads are transient, so the normal retry and replan logic applies;
* authentication problems, usage limits, overage, a disabled provider and an exhausted call budget raise
  `LLMFatalError`. The failure analyzer then picks `ABORT` for every policy, including the baselines, so
  FA-MAS never burns allowance retrying something that cannot succeed.

A run with a disabled or misconfigured provider fails in `Engine.run` before planning (`LLMClient.preflight`).

## 6. Tests

`tests/test_claude_agent_sdk.py` makes no model call and starts no Claude Code process. It covers:

* **Fake sessions, no SDK needed:** the gates and the account check (including "refused before the prompt
  is sent"), message mapping, structured output, usage-limit and overage handling, error classification,
  budgets, concurrency, timeouts, full LangGraph runs with replanning, fatal-abort behaviour across all
  baselines, and the API, CORS and `serve` guardrails.
* **The real SDK with `ScriptedTransport`:** the handshake and control protocol, message parsing,
  `RateLimitEvent` handling, error results, the dry-run engine and the CLI.

These tests are skipped automatically if `claude-agent-sdk` is not installed.

**Checked against the real Claude Code CLI.** These checks used Claude Code 2.1.294 from claude-agent-sdk
0.2.165, run without any credentials and without sending a prompt:

* The `initialize` handshake returns the `account` block, and the session options are accepted, including
  `--tools ""`, an empty `--setting-sources`, `--json-schema`, `--permission-mode dontAsk` and `--max-turns`.
* With no login, the account reports `tokenSource: "none"`, and FA-MAS refuses with "not signed in".
* With a dummy `ANTHROPIC_API_KEY`, FA-MAS refuses before Claude Code starts. With the environment check
  bypassed, the account reports `apiKeySource: "ANTHROPIC_API_KEY"` and FA-MAS refuses at the handshake.
* In both cases zero prompts were sent.

## 7. Known limitations and things to confirm on the first approved real call

* **Native structured output.** Claude Code implements it with an internal StructuredOutput tool. Test A
  confirmed one structured reply with `tools=[]` on 2026-10-13, and the schema-based request-analysis,
  research and analysis calls in Tests B and C (20 prompts) also succeeded. If it fails in your setup, set
  `FAMAS_CLAUDE_STRUCTURED_OUTPUT=prompt`.
* **Startup latency.** Each call starts a Claude Code process, adding about 1 to 3 seconds of startup per
  call. That is fine for research runs, but slower than a raw API client.
* **Rate-limit fields.** The overage fields (`isUsingOverage`, `overageInUse`) come from the bundled CLI's
  rate-limit payload and are not part of the documented SDK types, so FA-MAS also treats any `overage`
  limit type and any `rejected` status as a stop. Treat overage detection as a backstop to keeping extra
  usage off, not a replacement.
* **The `Transport` interface.** The SDK's `Transport` class is marked as an internal API that may change. It
  is used only for dry runs and tests, never for real calls.
* **Docker.** The Docker image does not include this provider. Subscription sign-in is meant for your own
  interactive machine.
* **Local transcripts.** Claude Code keeps its own session transcripts under `~/.claude/projects/`. These
  include prompts and fetched README text. They stay on your machine.
