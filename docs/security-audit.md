# Security audit: FA-MAS v0.1.0-beta release candidate

| | |
|---|---|
| Date | 2026-10-13 |
| Performed by | Claude (AI assistant), at the maintainer's request. This is not a substitute for an independent professional audit. |
| Scope | Working tree of the release candidate, full Git history (1 commit, all refs), Python dependencies (the maintainer's `.venv` versions plus the `claude` extra), npm lockfile, Docker files, and the Claude Agent SDK integration |
| Constraints | No real model calls. No history rewrite. No credential rotation. No forced dependency upgrades. No secret values printed in this report. |

## Summary

| Severity | Found | Fixed in this release | Residual / needs a decision |
|---|---:|---:|---:|
| High | 1 | 1 | 0 |
| Medium | 4 | 4 | 0 |
| Low | 4 | 4 | 0 |
| Informational | 3 | 3 | 0 |

No secrets, credentials, private databases or personal data were found in the working tree or in Git
history. The single commit is authored by `Claude <noreply@anthropic.com>`, so it carries no personal email.

## Tools

| Tool | Version | Use | Result |
|---|---|---|---|
| gitleaks | 8.30.1 (release checksum verified) | `git` mode over all refs; `dir` mode over the release file set and over the local `data/` folder | 0 findings |
| detect-secrets | 1.5.0 | all files of the release set | 8 hits, all reviewed false positives (see below) |
| pip-audit | 2.10.1, PyPI vulnerability service* | 90 pinned packages (Mac `.venv` + `claude` extra) | 0 known vulnerabilities |
| npm audit | npm 10.9.4 / 10.9.8 | `frontend/package-lock.json` | 0 (all dependencies, and `--omit=dev`) after S-05 |
| bandit | 1.9.4 | `famas/` | 6 low/medium hits, reviewed (S-08 fixed, the rest accepted) |
| pytest | | regression tests for every fix (`tests/test_security.py`, `tests/test_claude_agent_sdk.py`) | pass |
| manual review | | API, CLI, sandbox, agents, tools, persistence, Claude provider, Docker | findings below |

\*The OSV service (`api.osv.dev`) was not reachable from the audit environment. Results come from the PyPI
advisory data.

**detect-secrets false positives.** The final scan of the release set found 8 hits. All were reviewed by
location. No values are reproduced here.

* `.env.example:26`: a commented example PostgreSQL URL with the local-development placeholder password.
* `famas/persistence/store.py:8`: a docstring example URL (`user:pass`).
* `docs/claude-agent-sdk.md`: the variable *name* `ANTHROPIC_API_KEY`.
* `tests/test_claude_agent_sdk.py` (4 hits) and `tests/test_security.py` (1 hit): dummy strings such as
  `sk-test-not-real`, used to prove that values are never echoed and that exposure rules trigger.

Before the fix in I-01, `docker-compose.yml` also had a hard-coded local-development password. It is no
longer flagged.

## Findings

### S-01: Model-written code executed on the host in live mode (High), fixed

**Problem.** With a real model, the coding agent asked the model for Python and executed it in
`tools/sandbox.py`. The sandbox limits CPU and memory and scrubs the environment. It does not isolate the
network or the user's files, and it ran in the caller's working directory, the project folder. Model output
can be steered by untrusted repository text, so on a personal Mac this is code execution with the user's
rights.

**Fix.**

* Executing model-written code in live mode is now opt-in with `FAMAS_ALLOW_GENERATED_CODE` (default off).
  Without it, the coding agent runs FA-MAS's built-in reviewed program, makes no code-generation call, and
  says so in its output.
* The sandbox now runs in an empty temporary directory, with `HOME` and `TMPDIR` pointing there.
* New limits: file size 16 MB and 64 open files, in addition to CPU and memory.
* Mock mode is unchanged: it executes FA-MAS's own constant program.

**Evidence.** `test_live_mode_does_not_execute_model_written_code_by_default`,
`test_model_written_code_runs_only_when_opted_in`, `test_sandbox_runs_in_empty_temporary_directory`,
`test_sandbox_limits_file_size`.

**Residual risk.** With the opt-in enabled, the sandbox still contains accidents, not adversaries. This is
documented.

### S-02: Any website could call the local API (Medium), fixed

**Problem.** The CORS default was `*`. Any page the user visited could start runs and benchmarks against
`127.0.0.1:8000`. With a live provider configured, that spends API credit or subscription usage.

**Fix.**

* The default origins are now the local UI only: `:8000` and the Vite dev server on `:5173`.
* A configured `*` is ignored whenever a non-mock provider is configured.
* Allowed methods and headers were narrowed.

**Evidence.** `test_cors_and_host_guards`.

### S-03: DNS rebinding against the local API (Medium), fixed

**Problem.** The API accepted any `Host` header. A malicious page could re-point its own domain to
`127.0.0.1` and call the API same-origin, bypassing CORS.

**Fix.** Added `TrustedHostMiddleware` (`FAMAS_ALLOWED_HOSTS`, default `localhost,127.0.0.1`).

**Evidence.** `test_cors_and_host_guards` (a foreign Host returns 400); live check against a running server.

### S-04: Accidental network exposure (Medium), fixed

**Problem.** `famas serve --host 0.0.0.0` was accepted with an API-key provider configured. The API has no
authentication. `docker-compose.yml` published port 8000 on all host interfaces.

**Fix.**

* `famas serve` refuses a non-loopback host while a live provider is usable, unless `--allow-remote` is
  passed.
* With the Claude subscription provider and real calls enabled, it always refuses, with no override.
* Mock-only servers print a warning.
* Compose now publishes on `127.0.0.1:8000` only.

**Evidence.** `test_serve_exposure_rules`, `test_serve_refuses_non_loopback_with_real_claude_calls`.

### S-05: Vulnerable Vite / esbuild in the dev toolchain (Medium, dev-only), fixed

**Problem.** `npm audit` reported Vite 5.4.11 with 12 advisories, including GHSA-vg6x-rcgg-rjx6 (high: any
website could read dev-server responses), and esbuild 0.21.5 (GHSA-67mh-4wv8-2f99).

**Fix, step 1.** Vite was upgraded within its major version to **5.4.21**. This fixed most advisories,
including GHSA-vg6x. Four remained, all needing a major upgrade:

* GHSA-4w7w-66w2-5vf9 (moderate);
* GHSA-v6wh-96g9-6wx3 (moderate, Windows only);
* GHSA-fx2h-pf6j-xcff (high, Windows only);
* esbuild GHSA-67mh-4wv8-2f99 (moderate).

**Fix, step 2 (maintainer approved, conditional on the checks below passing).** Vite was upgraded to **6.4.4**, the latest 6.x patch (published
2026-10-06). The upgrade was tested in an isolated copy of `frontend/` before it was applied. Lockfile changes:

* vite 5.4.21 → 6.4.4;
* esbuild 0.21.5 → 0.25.12;
* three new MIT build-time packages: `fdir`, `picomatch` 4, `tinyglobby`.

Rollup and every runtime dependency are unchanged.

**Evidence** (2026-10-13, Linux, Node 22.22.0):

* `@vitejs/plugin-react` 4.3.4 accepts `vite ^6.0.0` as a peer.
* `npm ci`, `tsc --noEmit` and `vite build` all pass. The bundle changes only in minifier formatting
  (550.4 kB → 555.9 kB, CSS identical apart from whitespace).
* `npm audit`: 0 vulnerabilities, and `npm audit --omit=dev`: 0. Before the upgrade it reported 2: 1 high, 1 moderate.
* Dev server checks:
  * `/api` proxy and the SSE run stream work;
  * `/@fs/etc/passwd`, `?raw` and `?import&raw` variants, other directories outside the root, and `.env`
    all return 403;
  * `..` paths fall back to `index.html`;
  * a foreign `Host` header gets 403.
* Browser E2E (Chromium, Playwright):
  * built UI against a mock server and a Claude dry-run server, plus a run started through the dev server;
  * all runs completed;
  * no console errors, page errors or failed requests.

The lockfile includes the macOS arm64 binaries (`@esbuild/darwin-arm64`, `@rollup/rollup-darwin-arm64`).
`npm ci && npm run build` on the maintainer's Mac is part of the local verification steps.

### S-06: Prompt injection from fetched repository content (Low), mitigated

**Problem.** In live mode, README text from the GitHub API is pasted into research prompts.

**Fix.**

* Fetched material is now wrapped in an explicit "untrusted third-party content, ignore instructions"
  fence. The fence cannot be closed from inside, and its size is limited so truncation never cuts it.
* The Claude provider runs with **no tools**, so injected text cannot cause actions.
* Model output is only parsed into schemas and rendered as Markdown. React Markdown does not render raw HTML
  and filters dangerous URLs.

**Evidence.** `test_fetched_repository_material_is_fenced_as_untrusted`,
`test_untrusted_fence_cannot_be_closed_from_inside`, `test_mock_mode_prompts_unchanged_by_fencing`.

**Residual risk.** Injected text can still bias a report's *content*. Treat live reports as drafts.

### S-07: Model-supplied repository names used in token-bearing URLs (Low), fixed

**Problem.** Repository names can come from the analyzer's model output. They were interpolated into GitHub
API URLs that may carry `GITHUB_TOKEN`.

**Fix.** Names are validated as `owner/name` before any request. Redirects to other hosts already drop the
`Authorization` header.

**Evidence.** `test_github_repo_names_are_validated`.

### S-08: SQL column names interpolated in `Store.update_run` (Low, bandit B608), fixed

**Problem.** Column names were interpolated into SQL. The keys only ever came from internal code, but the
pattern was fragile.

**Fix.** Updatable columns are now whitelisted. Values were already parameterised.

**Evidence.** `test_store_rejects_unknown_columns`.

### S-09: FA-MAS secrets inherited by the Claude Code process (Low), fixed

**Problem.** The SDK passes the parent environment to Claude Code.

**Fix.** `GITHUB_TOKEN`, `FAMAS_LLM_API_KEY` and `OPENAI_API_KEY` are blanked in the child's environment.
Claude Code runs without tools in any case.

**Evidence.** `test_options_are_tool_less_and_isolated`.

### Informational

* **I-01 (fixed): Docker hardening.**
  * The UI build uses `npm ci`.
  * The PostgreSQL password is configurable.
  * The image includes `NOTICE`.
  * The container must bind `0.0.0.0` internally; `--allow-remote` is explicit and documented.
* **I-02 (fixed): local-only files.** `.gitignore` now also covers `.env.*` (except `.env.example`),
  `*.log`, `*.zip`, `*.patch`, editor folders and `.claude/settings.local.json`.
* **I-03 (accepted): bandit B404/B603/B110.**
  * `subprocess` is used only for `claude --version`, with a list of arguments and no shell, and for the
    sandbox interpreter.
  * The `try/except/pass` blocks are deliberate best-effort paths: event listeners and rlimits.

## Checks with no finding

| Area | Evidence |
|---|---|
| Exposed API keys / credentials / Claude tokens | gitleaks and detect-secrets: none. The code never reads token values, credential files or the macOS Keychain. Environment values are only tested for presence, and only variable names are reported. |
| Secrets in Git history | gitleaks `git` mode over all refs: 0. No `.env`, database, key or credential file was ever committed. |
| Sensitive environment files | Only `.env.example` (placeholders) is tracked. `.env` is absent and ignored. |
| Private data | `data/famas.db` (local run history) is gitignored and was never committed. No absolute user paths or emails in the release set. |
| File permissions | Every file in the release set is a regular, non-executable file (Git mode 100644). No symlinks. No file is larger than 1 MB. |
| Unrestricted command execution | No `shell=True`, `os.system`, `eval` or `exec` outside the documented sandbox. |
| Path traversal | The static UI handler resolves paths and serves only files inside `frontend/dist`. Traversal probes, encoded and backslash variants included, never return outside files (`test_static_ui_handler_does_not_serve_files_outside_dist`). |
| Agent tool permissions | The GitHub tool is read-only (GET). The Claude provider runs `tools=[]` with `permission_mode="dontAsk"` and `setting_sources=[]`, so no hooks or settings are loaded. |
| Untrusted tool output | Parsed into pydantic schemas. Rendered without raw HTML. |
| Sensitive data in logs | FA-MAS has no logging of prompts or keys. The Claude usage log and validation reports hold metadata only (`test_usage_records_hold_metadata_only`). |
| Vulnerable Python dependencies | pip-audit: 0 / 90. |
| Bounded retries and concurrency | 4 attempts per task, 8 recovery rounds (2 in validation runs), concurrency ≤ 32 in the API. For Claude: 40 calls per run, 2 parallel sessions, 3 turns, 300 s per call, $1 estimated per call; fatal errors abort without retry. |

## Claude Agent SDK integration

| Requirement | Status | Evidence |
|---|---|---|
| Real model calls disabled by default | **Pass** | `claude_allow_real_calls=False`. Runs fail in preflight before planning. `validate` also needs `--yes`. Tests: `test_real_calls_disabled_by_default`, `test_disabled_provider_fails_run_before_planning`, `test_validation_refuses_real_calls_without_both_switches`. |
| Only official authentication methods | **Pass** | Claude Code's own `/login`, or `claude setup-token`. FA-MAS implements no login flow and stores no tokens. |
| No credentials extracted or stored | **Pass** | Code review and grep. Account summaries contain plan type and credential source only, never email or token. |
| No accidental use of separately billed API credentials | **Pass** | Environment gate: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_PROFILE`, `ANTHROPIC_FEDERATION_RULE_ID` and `CLAUDE_CODE_USE_*`. Handshake gate, fail-closed, **before the prompt is sent**: verified against the real Claude Code CLI 2.1.294 without credentials (no login → refused; dummy `ANTHROPIC_API_KEY` → refused; 0 prompts sent). |
| Model execution cannot be triggered remotely without authorization | **Pass**, with one residual | Loopback-only serving with no override, CORS and Host checks. **Residual:** other programs or users on the same machine can call the local API, which has no authentication. |
| Extra usage (usage credits) never enabled automatically | **Pass**, with one residual | No code path touches account or billing settings, and no SDK option enables overage. The provider stops on rejected or overage rate-limit events and halts until restart. **Residual:** up to `FAMAS_CLAUDE_MAX_PARALLEL` (2) requests already in flight may complete first. Keeping usage credits off in the account is the only hard guarantee. |
| Bounded retries and concurrency | **Pass** | See the table above. Tests: `test_call_budget_per_run`, `test_parallel_sessions_are_capped`, `test_timeout_closes_session`, `test_usage_limit_mid_run_aborts_instead_of_replanning`, `test_fatal_errors_abort_baselines_too`. |

## Actions for the maintainer

1. **Confirm the Vite 6.4.4 build on your Mac** (S-05): `cd frontend && npm ci && npm run build && npm audit`.
2. **Keep usage credits off** in your Claude account (Settings → Usage) before any real call.
3. **Apply the CI/Makefile patch** by hand. `.github/workflows/ci.yml` and `Makefile` are write-protected for
   remote tools.
4. **Do not expose** `famas serve` or the Docker port beyond `127.0.0.1`. There is no authentication layer
   in v0.1.0-beta.
