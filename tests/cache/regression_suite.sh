#!/usr/bin/env bash
# Regression suite runner for Latarnia.
# Spec hash is computed from all "- **test_name:**" entry lines in TESTS.md
# (via `grep -E '^-\s+\*\*test_' TESTS.md | sha256sum`).
# Run from repo root. Live parts need the dev server on :8000 with example apps running
# (example_full_app + example_companion started via POST /api/apps/<id>/process/start).
# Post-deploy entries (tst host): run regression_live.sh with INGRESS=... separately.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

if [ "${ENV:-dev}" = "prod" ] || ! grep -qE '^ENV=dev' .env; then
  echo "ERROR: ENV is not dev — refusing to run tests." >&2
  exit 2
fi
export ENV=dev
export PY=${PY:-$PWD/.venv/bin/python}
RC=0

# 1) Unit + integration (Postgres reachable locally). -x omitted: show every failure.
PYTHONPATH=src "$PY" -m pytest tests/unit/ tests/integration/ --no-cov -q || RC=1
# 2) test_real_chain_per_app_authz (T-0023): real caddy, 0 skips required (a skip is a FAIL)
command -v caddy >/dev/null || { echo "FAIL test_real_chain_per_app_authz: caddy not on PATH"; RC=1; }
out=$(PYTHONPATH=src "$PY" -m pytest tests/integration/test_caddy_forward_auth.py -rs --no-cov -q -p no:cacheprovider 2>&1)
last=$(printf '%s\n' "$out" | tail -1); echo "real-chain: $last"
if printf '%s\n' "$last" | grep -qE ' passed' && ! printf '%s\n' "$last" | grep -qE 'skipped|failed|error'; then
  echo "PASS test_real_chain_per_app_authz"; else echo "FAIL test_real_chain_per_app_authz"; RC=1; fi
# 3) Task suites behind TESTS.md entries: T-0021 (DB password), T-0023 (per-App authz), T-0024 (App name pattern)
bash tests/cache/t21_run.sh || RC=1
bash tests/cache/t23_run.sh || RC=1
bash tests/cache/t24_run.sh 001 002 003 004 || RC=1
# 4) Dashboard JS function entries
node tests/cache/regression_dashboard_js.js || RC=1
# 5) T-0020 API docs entries (Caddyfile + role gating) and live API/MCP/token entries
for t in t20_001 t20_002 t20_004 t20_005 t20_006 t20_007; do bash tests/cache/$t.sh >/dev/null 2>&1 && echo "PASS $t" || { echo "FAIL $t"; RC=1; }; done
bash tests/cache/regression_live.sh || RC=1
# 6) Playwright Dashboard / App Web UI entries (superuser session from the t20 fixture)
eval "$(PYTHONPATH=src "$PY" tests/cache/t20_fixture.py secrets.env 2>/dev/null)"
S_SU="${S_SU:-}" "$PY" tests/cache/regression_playwright.py || RC=1
exit $RC
