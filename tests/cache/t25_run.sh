#!/usr/bin/env bash
# T-0025 acceptance runner (docs/Tasks/T-0025.md ## Acceptance Criteria), local ENV=dev.
#   001-006 -> tests/cache/t25_check.py <n>  (independent of tests/integration)
#   007     -> (s) t25_check.py 007diff: AST diff vs BASE — pre-existing tests unchanged except
#              test_token_scope_enforced (setup lines added, assertions identical);
#              (a) the BASE-ref test_token_flow.py, run unmodified against this code: only
#              test_token_scope_enforced may fail (it minted a token above the caller's role,
#              the hole T-0025 closes); (b) the full pytest suite is green.
# Usage: tests/cache/t25_run.sh [001 ...]
#   PY   interpreter (default: .venv/bin/python of the primary checkout).
#   SRC  code under test (default: src). Point it at another checkout's src/ to run 001-006
#        against that code (e.g. the pre-T-0025 dev checkout, where 001/002/003/006 must FAIL).
#   BASE git ref holding the pre-change test file for 007a (default: dev).
cd "$(dirname "$0")/../.." || exit 2
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
PRIMARY=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
PY=${PY:-$PRIMARY/.venv/bin/python}
SRC=${SRC:-src}
BASE=${BASE:-dev}
fails=0
for n in ${@:-001 002 003 004 005 006 007}; do
  if [ "$n" = 007 ]; then
    ENV=dev PYTHONPATH="$SRC" "$PY" tests/cache/t25_check.py 007diff "$BASE" || fails=$((fails+1))
    tmp=$(mktemp -d)
    git show "$BASE:tests/integration/test_token_flow.py" > "$tmp/test_t25_base_token_flow.py" || exit 2
    out=$(ENV=dev PYTHONPATH="$SRC" "$PY" -m pytest "$tmp/test_t25_base_token_flow.py" -q -p no:cacheprovider -rf 2>&1)
    rm -rf "$tmp"
    failed=$(printf '%s\n' "$out" | sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' | sort -u | tr '\n' ' ')
    passed=$(printf '%s\n' "$out" | grep -Eo '[0-9]+ passed' | tail -1)
    echo "  $BASE test_token_flow.py (unmodified) vs this code: ${passed:-0 passed}; failed: ${failed:-none}"
    if [ "$failed" = "test_token_scope_enforced " ]; then
      echo "  ok   only test_token_scope_enforced fails (it minted webUI-med for a role-none user)"
    else
      echo "FAIL t25-007a (expected only test_token_scope_enforced to fail)"; fails=$((fails+1))
    fi
    ENV=dev PYTHONPATH="$SRC" "$PY" -m pytest tests/ -q -p no:cacheprovider
    rc=$?
    [ $rc = 0 ] && echo "PASS t25-007" || { echo "FAIL t25-007 (pytest exit $rc)"; fails=$((fails+1)); }
  else
    ENV=dev PYTHONPATH="$SRC" "$PY" tests/cache/t25_check.py "$n" || fails=$((fails+1))
  fi
done
[ "$fails" = 0 ] && echo "ALL PASS" || { echo "FAILED: $fails"; exit 1; }
