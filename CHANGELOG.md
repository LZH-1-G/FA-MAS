# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0-beta.1] — 2026-10-13

### Fixed

- Fixed pytest module imports in GitHub Actions by adding
  `tests/__init__.py`.
- Verified that the updated main branch passes GitHub CI.

## [0.1.0-beta] — 2026-10-13

This is the first public beta. Python package version: `0.1.0b0`.

### Added

* **FA-MAS core.** Covers:
  * a LangGraph workflow: analyse → plan → validate → assign → execute → diagnose → assess impact →
    replan → finalize;
  * a NetworkX task DAG with content fingerprints;
  * adaptive agent selection;
  * an eager DAG dispatcher;
  * failure classification with root-cause attribution;
  * dependency-aware incremental replanning: retry, reassign, decompose with salvage, add dependency,
    add prerequisite, repair upstream, skip, abort;
  * an optional reuse audit.
* **Baselines and benchmark.** Four baselines (single agent, fixed workflow, full replanning, naive retry),
  a deterministic simulator with 9 failure scenarios, and a reproducible benchmark with committed results.
* **Interfaces.** A FastAPI backend with an SSE event stream, SQLite/PostgreSQL persistence, the `famas`
  CLI, and a React + React Flow console.
* **Experimental Claude Agent SDK provider** (`FAMAS_LLM_PROVIDER=claude_agent_sdk`), for personal local
  testing only. Mock mode remains the default and the supported public execution mode. The provider uses
  Claude Code with the account holder's own Claude login on their own machine. The maintainer ran the three
  bounded real tests (A–C) on 2026-10-13 and all completed; results are in `docs/real-claude-validation.md`.
  It includes:
  * real calls off by default;
  * an offline dry-run mode through the real SDK client;
  * credential-source checks before any prompt;
  * usage-limit and overage stops;
  * per-run, parallel, turn, time and cost caps;
  * per-call usage metadata logging;
  * `famas claude doctor`, `famas claude smoke` and bounded validation tests
    `famas claude validate a|b|c`.
* **Security and licensing.** `SECURITY.md`, `docs/security-audit.md`, `docs/real-claude-validation.md`,
  `NOTICE` and `THIRD_PARTY_NOTICES.md`.
* **`simulated_llm_calls` run metric.** It reports how many of a run's `llm_calls` were injected timeouts. These
  are charged like a timed-out call, but no prompt is sent. Claude validation reports add an `accounting` block
  that reconciles `llm_calls − simulated_llm_calls` with the prompts that reached Claude. Metric definitions are
  in `docs/evaluation.md`.
* **`scripts/verify-offline.sh`.** Runs every offline release check in one command.

### Security

* The local API now restricts CORS to the local UI and validates Host headers (DNS rebinding).
* `famas serve` refuses to listen on a public interface while a live provider is configured; with the
  experimental Claude provider there is no override. docker-compose publishes on `127.0.0.1` only.
* Model-written code is no longer executed in live mode unless `FAMAS_ALLOW_GENERATED_CODE=true`. The sandbox
  runs in an empty temporary directory with file-size and open-file limits.
* Fetched repository text is fenced as untrusted data in prompts. GitHub repository names are validated.
  `Store.update_run` columns are whitelisted.
* FA-MAS's own secrets are not passed to the Claude Code process.
* Vite was upgraded from 5.4.11 to 6.4.4 and esbuild to 0.25.12, fixing all dev-server advisories (`npm audit`: 0).
  This affects the build toolchain only; runtime UI dependencies are unchanged.

### Changed

* The project is now licensed under the **Apache License 2.0**. The earlier, unpublished drafts used MIT.
* Run cost totals are summed order-independently (`math.fsum`), so benchmark results are bit-for-bit
  identical with the LangGraph runtime and the fallback runtime. Six committed rows changed by $0.000001 in a
  cost field (3 in the main set, 1 at p = 0.5 and 2 at p = 0.8). Each is an exact half-micro-dollar tie whose
  rounding direction changed. No token count, timing, outcome or summary changed. Details and the six values
  are in `docs/evaluation.md`.
* The Claude validation plan no longer states fixed call counts for Tests B and C: a real plan can be larger
  than the dry run's.

### Fixed

* The New Run page no longer overflows horizontally when an example request is long.
* The sandbox test compares canonical paths, so it passes on macOS, where temporary directories are reached
  through the `/var` → `/private/var` symlink.
