#!/usr/bin/env bash
# t20-003: logged-in user with no usable role on any App (t20-none: role none on example_full_app, nothing elsewhere) -> GET /docs 200 (platform Swagger).
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
check "/docs as role-none user" 200 "$(code -b latarnia_session=$S_NONE $BASE/docs)"
BODY=$(curl -s -b latarnia_session=$S_NONE $BASE/docs)
echo "$BODY" | grep -q "Swagger UI" && echo "  ok   body is Swagger UI" || { echo "  FAIL body not Swagger UI"; FAILS=$((FAILS+1)); }
echo "$BODY" | grep -q "url: '/openapi.json'" && echo "  ok   Swagger points at /openapi.json" || { echo "  FAIL url"; FAILS=$((FAILS+1)); }
check "/openapi.json as role-none user" 200 "$(code -b latarnia_session=$S_NONE $BASE/openapi.json)"
finish t20-003
