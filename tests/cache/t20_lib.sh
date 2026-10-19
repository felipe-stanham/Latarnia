# Shared helpers for t20 scripts. Source from a t20_00N.sh; repo root = cwd.
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
grep -qE '^ENV=dev' .env || { echo ".env ENV is not dev" >&2; exit 2; }
export ENV=dev
BASE=${BASE:-http://localhost:8000}
PY=.venv/bin/python
CACHE=tests/cache
FAILS=0
check() {  # check <label> <expected> <actual>
  if [ "$2" = "$3" ]; then echo "  ok   $1 (= $3)"; else echo "  FAIL $1: expected $2, got $3"; FAILS=$((FAILS+1)); fi
}
load_principals() {
  for _i in 1 2 3; do eval "$(PYTHONPATH=src $PY $CACHE/t20_fixture.py secrets.env 2>/dev/null)"; [ -n "${TOK_OK:-}" ] && break; sleep 1; done
  [ -n "${S_LOW:-}" ] && [ -n "${TOK_OK:-}" ] || { echo "fixture failed" >&2; exit 2; }
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
finish() { [ "$FAILS" = 0 ] && { echo "PASS $1"; exit 0; } || { echo "FAIL $1 ($FAILS)"; exit 1; }; }
