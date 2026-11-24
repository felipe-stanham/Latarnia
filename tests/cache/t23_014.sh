#!/usr/bin/env bash
# t23-014: the T-0022 tests in tests/unit/test_machine_token_access.py pass unchanged.
# Runs the eight T-0022 test functions by name; all must be collected and pass.
# Also shows the lines removed from that file vs the dev branch (expect only docstring/import).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
PY=${PY:-.venv/bin/python}
F=tests/unit/test_machine_token_access.py
T22="test_superuser_token_gets_app or test_scoped_token_get_app or test_no_claims_no_session_get_app_is_401 or test_no_claims_no_session_list_is_401 or test_superuser_token_lists_all_apps or test_scoped_token_lists_only_scoped_apps or test_same_second_identical_issue_is_unique or test_same_second_identical_create_token_inserts_distinct_hashes"
echo "removed vs dev:"; git diff dev -- "$F" | grep -E '^-[^-]' || echo "  (none)"
out=$(ENV=dev PYTHONPATH=src "$PY" -m pytest "$F" -k "$T22" -q --no-cov -p no:cacheprovider 2>&1)
last=$(printf '%s\n' "$out" | tail -1); echo "$last"
if printf '%s\n' "$last" | grep -qE '^8 passed' && ! printf '%s\n' "$last" | grep -qE 'failed|error|skipped'; then
  echo "PASS t23-014"; else printf '%s\n' "$out" | tail -30; echo "FAIL t23-014"; exit 1; fi
