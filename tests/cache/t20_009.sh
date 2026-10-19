#!/usr/bin/env bash
# t20-009: App stopped -> its docs page shows "App not running", status not 500.
# Stops example_companion (dev), checks pages/schemas/summary, then always starts it again.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
SU="-b latarnia_session=$S_SU"
restart() { curl -s -o /dev/null $SU -X POST $BASE/api/apps/example-companion/process/start; }
trap restart EXIT
echo "  stop -> $(code $SU -X POST $BASE/api/apps/example-companion/process/stop)"
sleep 3
for k in ui rest; do
  OUT=$(curl -s -w '\n%{http_code}' $SU $BASE/docs/apps/example_companion/$k)
  ST=$(echo "$OUT" | tail -1); BODY=$(echo "$OUT" | sed '$d')
  echo "  page $k: status $ST; body: $(echo "$BODY" | sed 's/<[^>]*>/ /g' | tr -s ' ' | cut -c1-120)"
  [ "$ST" != "500" ] && [ "$ST" != "200" ] && echo "  ok   $k page status $ST (not 500)" || { echo "  FAIL $k page status $ST"; FAILS=$((FAILS+1)); }
  echo "$BODY" | grep -qi "not running" && echo "  ok   body says not running" || { echo "  FAIL body lacks 'not running'"; FAILS=$((FAILS+1)); }
  check "schema $k status" 503 "$(code $SU $BASE/api/docs/apps/example_companion/$k)"
done
S=$(curl -s $SU $BASE/api/docs/apps/example_companion); echo "  summary: $S"
echo "$S" | $PY -c "import json,sys; d=json.load(sys.stdin); assert d['running'] is False and not d['ui'] and not d['rest'], d" && echo "  ok   summary running=false, no buttons" || { echo "  FAIL summary"; FAILS=$((FAILS+1)); }
restart; trap - EXIT
sleep 6
echo "  restarted; summary now: $(curl -s $SU $BASE/api/docs/apps/example_companion)"
finish t20-009
