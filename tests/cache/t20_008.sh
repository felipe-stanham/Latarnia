#!/usr/bin/env bash
# t20-008: App with no /rest/ routes (example_companion) -> /docs/apps/example_companion/rest 404 and no "REST API" button on its tile.
# Tile buttons are driven by /api/docs/apps/{app} (see loadApiDocsButtons in templates/dashboard.html): rest must be false.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
check "companion rest page" 404 "$(code -b latarnia_session=$S_LOW $BASE/docs/apps/example_companion/rest)"
check "companion rest schema" 404 "$(code -b latarnia_session=$S_LOW $BASE/api/docs/apps/example_companion/rest)"
S=$(curl -s -b latarnia_session=$S_LOW $BASE/api/docs/apps/example_companion)
echo "  summary: $S"
echo "$S" | $PY -c "import json,sys; d=json.load(sys.stdin); assert d['rest'] is False and d['ui'] is False, d" && echo "  ok   rest=false (button hidden)" || { echo "  FAIL rest flag"; FAILS=$((FAILS+1)); }
finish t20-008
