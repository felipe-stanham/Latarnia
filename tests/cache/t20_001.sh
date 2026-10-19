#!/usr/bin/env bash
# t20-001: Generated Caddyfile -> no block serves /docs*, /openapi.json, /apps/{app}/docs*, /apps/{app}/openapi.json without forward_auth.
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
PYTHONPATH=src $PY $CACHE/t20_caddy_check.py 001 || FAILS=1
finish t20-001
