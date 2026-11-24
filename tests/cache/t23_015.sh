#!/usr/bin/env bash
# t23-015: full suite green on ENV=dev: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -q`.
# With caddy on PATH the real-chain module and the `caddy adapt` unit test must run (not skip).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
PY=${PY:-.venv/bin/python}
out=$(ENV=dev PYTHONPATH=src "$PY" -m pytest tests/ -q -rs -p no:cacheprovider 2>&1)
last=$(printf '%s\n' "$out" | grep -E '(passed|failed|error).* in [0-9.]+s' | tail -1); echo "$last"
printf '%s\n' "$out" | grep -E '^SKIPPED' || true
rc=0
printf '%s\n' "$last" | grep -qE 'failed|error' && rc=1
printf '%s\n' "$last" | grep -qE 'passed' || rc=1
if command -v caddy >/dev/null && printf '%s\n' "$out" | grep -E '^SKIPPED' | grep -qiE 'caddy'; then
  echo "caddy-dependent tests skipped although caddy is on PATH"; rc=1; fi
[ $rc = 0 ] && echo "PASS t23-015" || { printf '%s\n' "$out" | grep -E '^(FAILED|ERROR)' ; echo "FAIL t23-015"; exit 1; }
