#!/usr/bin/env bash
# t20-004: user with webUI-low on example_full_app -> GET /docs/apps/example_full_app/ui 200; schema lists only /api/... paths, server URL /apps/example_full_app.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
check "UI docs page" 200 "$(code -b latarnia_session=$S_LOW $BASE/docs/apps/example_full_app/ui)"
curl -s -b latarnia_session=$S_LOW $BASE/docs/apps/example_full_app/ui | grep -q "url: '/api/docs/apps/example_full_app/ui'" && echo "  ok   page loads the ui schema" || { echo "  FAIL page schema url"; FAILS=$((FAILS+1)); }
check "UI schema" 200 "$(code -b latarnia_session=$S_LOW $BASE/api/docs/apps/example_full_app/ui)"
curl -s -b latarnia_session=$S_LOW $BASE/api/docs/apps/example_full_app/ui > "${TMPDIR:-/tmp}/t20_004.json"
PORT=$(grep -A12 'handle_path /apps/example_full_app/' caddy/latarnia.caddyfile | grep -o 'reverse_proxy localhost:[0-9]*' | head -1 | cut -d: -f2)
curl -s localhost:$PORT/openapi.json > "${TMPDIR:-/tmp}/t20_004_raw.json"
$PY - "${TMPDIR:-/tmp}/t20_004.json" "${TMPDIR:-/tmp}/t20_004_raw.json" <<'EOF' || FAILS=$((FAILS+1))
import json, sys
d = json.load(open(sys.argv[1])); raw = json.load(open(sys.argv[2]))
paths = list(d["paths"])
non_api_raw = [p for p in raw["paths"] if not p.startswith("/api/")]
assert paths and all(p.startswith("/api/") for p in paths), paths
assert non_api_raw, "raw App schema has no non-/api paths: filter not exercised"
assert not any(p in d["paths"] for p in non_api_raw)
assert not any(p.startswith("/rest/") or p.startswith("/v1") for p in paths)
assert d["servers"] == [{"url": "/apps/example_full_app"}], d["servers"]
print("  ok   paths:", paths); print("  ok   raw non-/api paths excluded:", non_api_raw); print("  ok   servers:", d["servers"])
EOF
rm -f "${TMPDIR:-/tmp}"/t20_004*.json
finish t20-004
