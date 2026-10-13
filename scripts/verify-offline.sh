#!/usr/bin/env bash
# FA-MAS offline verification.
#
# Makes NO model calls and uses NO credentials:
#   * real Claude calls are forced off (FAMAS_CLAUDE_ALLOW_REAL_CALLS=false);
#   * the Claude steps use the scripted dry-run transport;
#   * the local .env file is ignored.
# Run outputs go to a temporary folder. Inside the repository it only touches frontend/node_modules,
# frontend/dist and tool caches, all gitignored. Written for macOS's /bin/bash 3.2 (no bash 4 features).
#
# usage:  scripts/verify-offline.sh [--skip-frontend] [--skip-bench]
#         PYTHON=/path/to/python scripts/verify-offline.sh     (default: .venv/bin/python, else python3)
# shellcheck disable=SC2329  # check functions are invoked indirectly through step()
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)

SKIP_FRONTEND=0
SKIP_BENCH=0
for arg in "$@"; do
  case "$arg" in
    --skip-frontend) SKIP_FRONTEND=1 ;;
    --skip-bench) SKIP_BENCH=1 ;;
    -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ -n "${PYTHON:-}" ]; then
  PY=$PYTHON
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PY=$ROOT/.venv/bin/python
else
  PY=$(command -v python3)
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/famas-verify.XXXXXX")
LOGS=$WORK/logs
mkdir -p "$LOGS"

# ---- safety: offline, no credentials, no repository writes --------------------------------------
export FAMAS_ENV_FILE="$WORK/no-such.env"   # never read the local .env
export FAMAS_LLM_PROVIDER=mock
export FAMAS_CLAUDE_ALLOW_REAL_CALLS=false  # real Claude calls are impossible in this script
export FAMAS_CLAUDE_DRY_RUN=false           # switched on only for the dry-run steps below
export FAMAS_DATABASE_URL="sqlite:///$WORK/famas.db"
export FAMAS_CLAUDE_USAGE_LOG="$WORK/claude-usage.jsonl"
export FAMAS_CLAUDE_WORKDIR="$WORK/claude-workdir"
export FAMAS_ALLOW_GENERATED_CODE=false

RESULTS=""
FAILED=0
N=0

record() { RESULTS="$RESULTS$1|$2
"; }

# step "<name>" command [args...]: runs the command, logs its output, records PASS or FAIL.
step() {
  local name=$1
  shift
  N=$((N + 1))
  local log
  log="$LOGS/$(printf '%02d' "$N")-$(echo "$name" | tr -c 'A-Za-z0-9\n' '-').log"
  printf '\n==> [%02d] %s\n' "$N" "$name"
  local t0=$SECONDS
  if "$@" >"$log" 2>&1; then
    printf '    PASS (%ss)\n' "$((SECONDS - t0))"
    record PASS "$name"
  else
    printf '    FAIL (%ss); last lines of %s:\n' "$((SECONDS - t0))" "$log"
    tail -n 25 "$log" | sed 's/^/    | /'
    record FAIL "$name"
    FAILED=1
  fi
}

warn() {
  printf '    WARN: %s\n' "$1"
  record WARN "$1"
}

famas() { "$PY" -m famas.cli "$@"; }

# ---- checks ---------------------------------------------------------------------------------------
check_python() {
  echo "python: $PY"
  "$PY" - <<'EOF'
import sys
from importlib.metadata import version
import famas
print("python", sys.version.split()[0], "| famas", famas.__version__)
for name in ("langgraph", "fastapi", "pydantic", "claude-agent-sdk"):
    try:
        print(f"{name:18} {version(name)}")
    except Exception:
        print(f"{name:18} NOT INSTALLED")
assert sys.version_info >= (3, 11), "Python 3.11+ required"
version("claude-agent-sdk")  # required for the SDK tests and the dry runs
EOF
}

check_env_names() {
  # Uses the provider's own check. Prints variable NAMES only, never values.
  "$PY" - <<'EOF'
import os, sys
from famas.llm.claude_agent_sdk_client import conflicting_env
bad = conflicting_env()
other = sorted(
    k for k in os.environ
    if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_")) and k not in bad
)
if other:
    print("note: also set, but not treated as a billing override:", ", ".join(other))
if bad:
    print("These variables would stop Claude Code from using your Claude login. Unset them before Test A:")
    print("\n".join(bad))
    sys.exit(1)
print("no API-key, gateway, profile or cloud-provider variables set")
EOF
}

lint() {
  "$PY" -m ruff check famas tests || return 1
  "$PY" -m ruff format --check famas tests || return 1
}

mock_run() {
  famas run "Analyze LangGraph, CrewAI, AutoGen, and Microsoft Agent Framework. Compare their system architectures, agent communication mechanisms, scheduling strategies, and failure recovery capabilities." \
    --scenario corruption --out "$WORK/mock-report.md" --json "$WORK/mock-run.json" --quiet || return 1
  "$PY" - "$WORK/mock-run.json" <<'EOF' || return 1
import json, sys
d = json.load(open(sys.argv[1]))
print("status:", d.get("status"), "| recovery rounds:", len(d.get("replans") or []))
assert d.get("status") == "completed", d.get("error")
assert d.get("replans"), "expected a recovery round for the corruption scenario"
EOF
}

bench_compare() {
  local out=$1 ref=$2
  shift 2
  famas bench "$@" --out "$out" || return 1
  local f
  for f in results.csv results.json summary.json summary.md; do
    cmp "$out/$f" "$ref/$f" || return 1
    echo "identical: $ref/$f"
  done
}

doctor() {
  FAMAS_LLM_PROVIDER=claude_agent_sdk famas claude doctor --offline-test
}

dry_run() {
  local t=$1
  FAMAS_LLM_PROVIDER=claude_agent_sdk FAMAS_CLAUDE_DRY_RUN=true \
    famas claude validate "$t" --out "$WORK/claude-validation" || return 1
  "$PY" - "$WORK/claude-validation" "$t" <<'EOF' || return 1
import glob, json, sys
path = sorted(glob.glob(f"{sys.argv[1]}/*-test-{sys.argv[2]}.json"))[-1]
d = json.load(open(path))
models = {c.get("model") for c in d.get("calls", [])}
print("report:", path)
print("status:", d["outcome"]["status"], "| real_calls:", d["real_calls"], "| models:", sorted(models))
assert d["real_calls"] is False, "not a dry run"
assert all(str(m).startswith("dry-run:") for m in models), "unexpected non-dry-run model"
assert d["outcome"]["status"] == "completed", d["outcome"]
EOF
}

npm_ci() { (cd frontend && npm ci --no-audit --no-fund); }
npm_build() { (cd frontend && npm run build); }
npm_audit_all() { (cd frontend && npm audit); }
npm_audit_prod() { (cd frontend && npm audit --omit=dev); }

hygiene() {
  # Files that `git add -A` would include: tracked files plus untracked files that are not ignored.
  local files bad
  files=$( (git ls-files; git ls-files --others --exclude-standard) | sort -u)
  bad=$(echo "$files" | grep -E '(^|/)\.env$|(^|/)\.env\.[^e]|\.db$|\.sqlite3?$|\.jsonl$|\.patch$|\.zip$|(^|/)node_modules/|(^|/)\.venv/|^data/[^.]|(^|/)\.DS_Store$' || true)
  if [ -n "$bad" ]; then
    echo "These files would be committed but should not be:"
    echo "$bad"
    return 1
  fi
  echo "$(echo "$files" | wc -l | tr -d ' ') files in the release set; none match private-file patterns"
}

# ---- run ------------------------------------------------------------------------------------------
echo "FA-MAS offline verification (no model calls, no credentials)"
echo "repository : $ROOT"
echo "work folder: $WORK"

step "Python environment and claude-agent-sdk" check_python
step "No API-key / gateway variables in the environment (names only)" check_env_names
step "ruff check + format check" lint
step "pytest (full suite, offline)" "$PY" -m pytest -rs
step "Mock run with failure recovery (corruption scenario)" mock_run

if [ "$SKIP_BENCH" = 0 ]; then
  step "Benchmark reproducibility: main set (468 runs)" \
    bench_compare "$WORK/bench-main" benchmarks/results
  step "Benchmark reproducibility: detect 0.5 (400 runs)" \
    bench_compare "$WORK/bench-d05" benchmarks/results/detect_0.5 \
    --scenarios corruption,cascade --tasks T2,T3,T4,T5,T6 \
    --systems full_replan,naive_incremental,fa_mas,fa_mas_verify \
    --det-seeds 0,1,2,3,4,5,6,7,8,9 --detect 0.5
  step "Benchmark reproducibility: detect 0.8 (400 runs)" \
    bench_compare "$WORK/bench-d08" benchmarks/results/detect_0.8 \
    --scenarios corruption,cascade --tasks T2,T3,T4,T5,T6 \
    --systems full_replan,naive_incremental,fa_mas,fa_mas_verify \
    --det-seeds 0,1,2,3,4,5,6,7,8,9 --detect 0.8
fi

step "Claude doctor --offline-test (scripted SDK round trip, no login used)" doctor
step "Claude dry run: Test A (scripted, no model calls)" dry_run a
step "Claude dry run: Test B (scripted, no model calls)" dry_run b
step "Claude dry run: Test C (scripted, no model calls)" dry_run c

if [ "$SKIP_FRONTEND" = 0 ]; then
  echo
  echo "node $(node --version 2>/dev/null || echo 'not found') | npm $(npm --version 2>/dev/null || echo 'not found')"
  step "npm ci (frontend, from the lockfile)" npm_ci
  step "npm run build (TypeScript + Vite)" npm_build
  step "npm audit (all dependencies)" npm_audit_all
  step "npm audit --omit=dev" npm_audit_prod
fi

step "Release hygiene: no private files in the release set" hygiene
if grep -q '\[COPYRIGHT HOLDER\]' NOTICE; then
  warn "NOTICE still has the [COPYRIGHT HOLDER] placeholder (expected until you choose a name)"
fi
if [ -n "$(git status --porcelain)" ]; then
  warn "working tree has uncommitted changes (expected before the release commit)"
fi

echo
echo "==> git status --short"
git status --short
echo
echo "==> git diff --stat (tracked files)"
git diff --stat | tail -n 1

echo
echo "================ summary ================"
printf '%s' "$RESULTS" | while IFS='|' read -r status name; do
  [ -n "$status" ] && printf '%-5s %s\n' "$status" "$name"
done
echo "logs and reports: $WORK"
if [ "$FAILED" = 0 ]; then
  echo "RESULT: all checks passed (no model calls were made)"
else
  echo "RESULT: some checks FAILED (no model calls were made)"
fi
exit "$FAILED"
