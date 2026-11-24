#!/usr/bin/env bash
# t23-023: with `caddy` absent from PATH, the real-chain module is reported as skipped with a
# reason containing "caddy binary not installed". PATH is reduced to system dirs that hold no caddy;
# the interpreter is called by absolute path. PY overrides the interpreter.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
PY=${PY:-$PWD/.venv/bin/python}
NOCADDY_PATH=/usr/bin:/bin:/usr/sbin:/sbin
if PATH=$NOCADDY_PATH command -v caddy >/dev/null; then echo "FAIL: caddy still reachable on $NOCADDY_PATH"; exit 1; fi
out=$(PATH=$NOCADDY_PATH ENV=dev PYTHONPATH=src "$PY" -m pytest tests/integration/test_caddy_forward_auth.py -rs --no-cov -p no:cacheprovider 2>&1)
printf '%s\n' "$out" | grep -E 'SKIPPED|skipped|passed|failed|error' | tail -4
if printf '%s\n' "$out" | grep -qE 'SKIPPED \[[0-9]+\] tests/integration/test_caddy_forward_auth.py.*caddy binary not installed' \
   && printf '%s\n' "$out" | tail -1 | grep -qE '^=+ 1 skipped' \
   && ! printf '%s\n' "$out" | tail -1 | grep -qE 'passed|failed|error'; then
  echo "PASS t23-023"
else
  echo "FAIL t23-023"; printf '%s\n' "$out" | tail -20; exit 1
fi
