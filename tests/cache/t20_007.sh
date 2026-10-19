#!/usr/bin/env bash
# t20-007: Machine Token scoped {example_full_app: webUI-low} -> GET /api/docs/apps/example_full_app/rest 200; token scoped to another App only -> 403.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
U=/api/docs/apps/example_full_app/rest
check "token scoped to example_full_app" 200 "$(code -H "Authorization: Bearer $TOK_OK" $BASE$U)"
check "token scoped to example_companion only" 403 "$(code -H "Authorization: Bearer $TOK_OTHER" $BASE$U)"
check "no credentials" 401 "$(code $BASE$U)"
check "bogus token" 401 "$(code -H "Authorization: Bearer not.a.token" $BASE$U)"
finish t20-007
