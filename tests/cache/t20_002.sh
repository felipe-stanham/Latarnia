#!/usr/bin/env bash
# t20-002: anonymous /docs and /openapi.json -> fall only into `handle /*` with forward_auth (401 -> redirect /auth/login).
# No Caddy locally: verified on the generated Caddyfile + /auth/verify returns 401 anonymously.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
PYTHONPATH=src $PY $CACHE/t20_caddy_check.py 002 || FAILS=1
# T-0023: verify does not read the URI, so one anonymous check covers /docs and /openapi.json.
check "/auth/verify anonymous (catch-all: /docs, /openapi.json)" 401 "$(code $BASE/auth/verify)"
finish t20-002
