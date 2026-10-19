#!/usr/bin/env bash
# Regression suite runner for Latarnia.
# Spec hash is computed from all "- **test_name:**" entry lines in TESTS.md
# (via `grep -E '^-\s+\*\*test_' TESTS.md | sha256sum`).
# Run from repo root. Live parts need the dev server on :8000 with example apps running.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

if [ "${ENV:-dev}" = "prod" ] || ! grep -qE '^ENV=dev' .env; then
  echo "ERROR: ENV is not dev — refusing to run tests." >&2
  exit 2
fi
export ENV=dev
RC=0

# 1) Unit + integration (Postgres reachable locally). -x omitted: show every failure.
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ tests/integration/ --no-cov -q || RC=1
# 2) Dashboard JS function entries
node tests/cache/regression_dashboard_js.js || RC=1
# 3) T-0020 API docs entries (Caddyfile + role gating) and live API/MCP/token entries
for t in t20_001 t20_002 t20_004 t20_005 t20_006 t20_007; do bash tests/cache/$t.sh >/dev/null 2>&1 && echo "PASS $t" || { echo "FAIL $t"; RC=1; }; done
bash tests/cache/regression_live.sh || RC=1
exit $RC
