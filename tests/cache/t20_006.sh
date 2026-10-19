#!/usr/bin/env bash
# t20-006: user with role none on example_full_app -> both docs pages and both /api/docs/apps/example_full_app/* schemas return 403.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
for u in /docs/apps/example_full_app/ui /docs/apps/example_full_app/rest /api/docs/apps/example_full_app/ui /api/docs/apps/example_full_app/rest /api/docs/apps/example_full_app; do
  check "$u as role-none" 403 "$(code -b latarnia_session=$S_NONE $BASE$u)"
done
finish t20-006
