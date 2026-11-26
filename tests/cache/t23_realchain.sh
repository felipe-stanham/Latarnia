#!/usr/bin/env bash
# T-0023 real-chain checks (t23-018..022, t23-024..026): the real `caddy` binary in front of
# the real auth router and stub Apps (tests/integration/test_caddy_forward_auth.py).
#  1) Runs the module: zero skips allowed (a skip is a FAIL), every test must pass.
#  2) Mutation check: breaks the generated Caddyfile at runtime (tests/cache/t23_mutation_plugin.py,
#     src/ untouched) and requires the targeted real-chain tests to FAIL — proves they are not vacuous.
# Usage: tests/cache/t23_realchain.sh        PY overrides the interpreter.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
export ENV=dev PYTHONPATH="src:tests/cache"
PY=${PY:-.venv/bin/python}
MOD=tests/integration/test_caddy_forward_auth.py
command -v caddy >/dev/null || { echo "FAIL: caddy not on PATH — real-chain tests cannot run"; exit 1; }
fails=0
out=$("$PY" -m pytest "$MOD" -rs -v --no-cov -p no:cacheprovider 2>&1)
summary=$(printf '%s\n' "$out" | tail -1)
echo "module: $summary"
if printf '%s\n' "$out" | grep -qE 'SKIPPED|[0-9]+ skipped'; then
  echo "FAIL: real-chain module skipped tests:"; printf '%s\n' "$out" | grep -E 'SKIP'; fails=$((fails+1))
fi
if ! printf '%s\n' "$summary" | grep -qE '^=+ [0-9]+ passed' || printf '%s\n' "$summary" | grep -qE 'failed|error'; then
  echo "FAIL: real-chain module not all passed"; printf '%s\n' "$out" | tail -80; fails=$((fails+1))
fi
crit() {  # crit <id> <pytest -k expr>
  local id=$1 k=$2 lines
  lines=$(printf '%s\n' "$out" | grep -E "::($k)(\[| )" )
  if [ -n "$lines" ] && ! printf '%s\n' "$lines" | grep -qvE ' PASSED'; then
    echo "PASS $id ($(printf '%s\n' "$lines" | wc -l | tr -d ' ') tests)"
  else echo "FAIL $id"; printf '%s\n' "$lines"; fails=$((fails+1)); fi
}
crit t23-018 'test_role_none_is_403_and_app_never_called'
crit t23-019 'test_granted_role_reaches_the_app'
crit t23-020 'test_no_session_redirects_to_login_with_original_path'
crit t23-021 'test_client_headers_cannot_spoof_identity_or_app|test_spoofed_app_header_cannot_borrow_another_apps_role|test_catch_all_strips_client_app_header'
crit t23-022 'test_every_target_form_is_checked_against_the_app_it_reaches'
crit t23-024 'test_connection_header_cannot_strip_identity_headers'
crit t23-025 'test_client_app_header_variants_on_app_block|test_client_app_header_variants_on_catch_all'
crit t23-026 'test_client_app_header_never_reaches_an_app'

# Mutation check: each mutation must make its targeted tests fail.
mutant() {  # mutant <mode> <-k expr>
  local mode=$1 k=$2 mo
  mo=$(T23_MUTATION=$mode "$PY" -m pytest "$MOD" -p t23_mutation_plugin -k "$k" -q --no-cov -p no:cacheprovider 2>&1 | tail -1)
  if printf '%s\n' "$mo" | grep -qE '[0-9]+ failed' && ! printf '%s\n' "$mo" | grep -qE 'skipped'; then
    echo "PASS mutation $mode detected: $mo"
  else echo "FAIL mutation $mode NOT detected: $mo"; fails=$((fails+1)); fi
}
mutant drop_app_header      'test_role_none_is_403_and_app_never_called'
mutant drop_identity_reset  'test_connection_header_cannot_strip_identity_headers'
mutant drop_catchall_strip  'test_catch_all_strips_client_app_header'
mutant drop_proxy_app_strip 'test_client_app_header_never_reaches_an_app'

[ "$fails" = 0 ] && echo "ALL PASS" || { echo "FAILED: $fails"; exit 1; }
