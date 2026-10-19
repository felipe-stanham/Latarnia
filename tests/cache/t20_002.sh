#!/usr/bin/env bash
# t20-002: anonymous /docs and /openapi.json -> fall only into `handle /*` with forward_auth (401 -> redirect /auth/login).
# No Caddy locally: verified on the generated Caddyfile + /auth/verify returns 401 anonymously.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
PYTHONPATH=src $PY $CACHE/t20_caddy_check.py 002 || FAILS=1
check "/auth/verify anonymous for /docs" 401 "$(code $BASE/auth/verify -H 'X-Forwarded-Uri: /docs')"
check "/auth/verify anonymous for /openapi.json" 401 "$(code $BASE/auth/verify -H 'X-Forwarded-Uri: /openapi.json')"
finish t20-002
