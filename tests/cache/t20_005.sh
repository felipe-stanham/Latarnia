#!/usr/bin/env bash
# t20-005: same user -> GET /docs/apps/example_full_app/rest 200; schema lists only the /rest/... path, relative to server URL /rest/example_full_app.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
check "REST docs page" 200 "$(code -b latarnia_session=$S_LOW $BASE/docs/apps/example_full_app/rest)"
curl -s -b latarnia_session=$S_LOW $BASE/docs/apps/example_full_app/rest | grep -q "url: '/api/docs/apps/example_full_app/rest'" && echo "  ok   page loads the rest schema" || { echo "  FAIL page schema url"; FAILS=$((FAILS+1)); }
check "REST schema" 200 "$(code -b latarnia_session=$S_LOW $BASE/api/docs/apps/example_full_app/rest)"
curl -s -b latarnia_session=$S_LOW $BASE/api/docs/apps/example_full_app/rest > "${TMPDIR:-/tmp}/t20_005.json"
$PY - "${TMPDIR:-/tmp}/t20_005.json" <<'EOF' || FAILS=$((FAILS+1))
import json, sys
d = json.load(open(sys.argv[1]))
assert list(d["paths"]) == ["/v1/items"], list(d["paths"])
assert d["servers"] == [{"url": "/rest/example_full_app"}], d["servers"]
print("  ok   paths:", list(d["paths"]), "servers:", d["servers"], "(=> /rest/example_full_app/v1/items)")
EOF
rm -f "${TMPDIR:-/tmp}"/t20_005.json
finish t20-005
