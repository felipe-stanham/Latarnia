#!/usr/bin/env bash
# t20-010: Dashboard -> "API docs" header button links to /docs; example_full_app tile shows "UI API" and "REST API" buttons linking to the two docs pages.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
load_principals
node $CACHE/t20_dashboard.js $BASE "$S_LOW" || FAILS=$((FAILS+1))
finish t20-010
