# Security policy

## Supported versions

FA-MAS is a research prototype. Only the latest `0.1.x` beta receives security fixes.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use the repository's private vulnerability
reporting instead (GitHub: *Security → Report a vulnerability*). Include steps to reproduce, the affected
version or commit, and the impact you expect. You should receive an acknowledgement within a week.

## Deployment model and assumptions

* The API and web UI are **single-user and local**. They have no authentication. `famas serve` binds to
  `127.0.0.1`, CORS only admits the local UI, and Host headers are restricted to `localhost` / `127.0.0.1`.
  Do not publish the port on a network. If you must, put it behind your own authentication, then use
  `--allow-remote` and `FAMAS_ALLOWED_HOSTS`.
* **Credentials.** Provide credentials only through your environment or a local `.env`, which is gitignored.
  FA-MAS never stores them.
* **The Claude Agent SDK provider (experimental).** It relies on Claude Code's own login and is intended only
  for personal, local testing by the account holder. It is not a supported public execution mode: do not
  host it or offer it to other people (use an API-key provider for that). It refuses to start if Claude Code
  would bill an API key or a third-party backend, and `famas serve` keeps it on `127.0.0.1`.
* **Model-written code.** It is **not** executed in live mode unless `FAMAS_ALLOW_GENERATED_CODE=true`. The
  code sandbox does not isolate network or filesystem access. Run untrusted code only in a container or VM.
* **Live research.** It reads untrusted third-party text (GitHub READMEs). That text is fenced as data in
  prompts, but it can still influence report content.

The most recent audit is in [docs/security-audit.md](docs/security-audit.md).
