#!/usr/bin/env bash
# t20-012: unit suite still green: PYTHONPATH=src python -m pytest tests/ -q
cd "$(dirname "$0")/../.." || exit 2; . tests/cache/t20_lib.sh
PYTHONPATH=src $PY -m pytest tests/ -q || FAILS=1
finish t20-012
