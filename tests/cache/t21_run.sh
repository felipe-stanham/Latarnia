#!/usr/bin/env bash
# t21-001..006: run the in-process acceptance checks for T-0021 (see docs/Tasks/T-0021.md ## Acceptance).
# Usage: tests/cache/t21_run.sh [001 002 ...]   (default: all). PY overrides the interpreter.
cd "$(dirname "$0")/../.." || exit 2
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
PY=${PY:-.venv/bin/python}
fails=0
for n in "${@:-001 002 003 004 005 006}"; do
  for m in $n; do
    ENV=dev PYTHONPATH=src "$PY" tests/cache/t21_check.py "$m" 2>&1 | tail -3 || true
    [ "${PIPESTATUS[0]}" = 0 ] || fails=$((fails+1))
  done
done
[ "$fails" = 0 ] && echo "ALL PASS" || { echo "FAILED: $fails"; exit 1; }
