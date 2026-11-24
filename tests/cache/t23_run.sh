#!/usr/bin/env bash
# T-0023 acceptance runner (docs/Tasks/T-0023.md ## Acceptance Criteria), local ENV=dev.
#   001-013, 016, 016b  -> tests/cache/t23_check.py (independent in-process / throwaway Postgres DB / caddy adapt)
#   014, 015, 017, 023  -> tests/cache/t23_0NN.sh
#   018-022, 024-026    -> tests/cache/t23_realchain.sh (real caddy; zero skips; mutation check)
#   027                 -> live on tst only (tests/cache/regression_live.sh with INGRESS=...): not run here
# Usage: tests/cache/t23_run.sh   (PY overrides the interpreter; caddy must be on PATH)
cd "$(dirname "$0")/../.." || exit 2
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
export PY=${PY:-$PWD/.venv/bin/python}
fails=0
for n in 001 002 003 004 005 006 007 008 009 010 011 012 013 016 016b; do
  ENV=dev PYTHONPATH=src "$PY" tests/cache/t23_check.py "$n" 2>&1 | grep -E '^(PASS|FAIL)|Error|assert' | tail -3
  [ "${PIPESTATUS[0]}" = 0 ] || { echo "FAIL t23-$n"; fails=$((fails+1)); }
done
for s in t23_014 t23_015 t23_017 t23_023 t23_realchain; do
  bash tests/cache/$s.sh || fails=$((fails+1))
done
echo "NOT RUN t23-027 (live, tst after deploy: INGRESS=https://<tst> ... bash tests/cache/regression_live.sh)"
[ "$fails" = 0 ] && echo "ALL PASS" || { echo "FAILED: $fails"; exit 1; }
