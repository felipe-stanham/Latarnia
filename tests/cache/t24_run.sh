#!/usr/bin/env bash
# T-0024 acceptance runner (docs/Tasks/T-0024.md ## Acceptance Criteria), local ENV=dev.
#   001-004 -> tests/cache/t24_check.py <n>  (independent of tests/unit)
#   005     -> full pytest suite
# Usage: tests/cache/t24_run.sh [001 ...]
#   PY overrides the interpreter (default .venv/bin/python).
#   MANIFEST_ROOT=<primary checkout> makes 004 also parse its gitignored apps/*/latarnia.json.
cd "$(dirname "$0")/../.." || exit 2
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
PY=${PY:-$PWD/.venv/bin/python}
fails=0
for n in ${@:-001 002 003 004 005}; do
  if [ "$n" = 005 ]; then
    ENV=dev PYTHONPATH=src "$PY" -m pytest tests/ -q -p no:cacheprovider
    rc=$?
    [ $rc = 0 ] && echo "PASS t24-005" || { echo "FAIL t24-005 (pytest exit $rc)"; fails=$((fails+1)); }
  else
    ENV=dev PYTHONPATH=src "$PY" tests/cache/t24_check.py "$n" || fails=$((fails+1))
  fi
done
[ "$fails" = 0 ] && echo "ALL PASS" || { echo "FAILED: $fails"; exit 1; }
