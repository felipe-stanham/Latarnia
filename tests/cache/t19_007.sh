#!/usr/bin/env bash
# t19-007: full unit suite still green (baseline 486 passed, 1 skipped). Run from repo root.
set -euo pipefail
[ "${ENV:-dev}" = "prod" ] && { echo "ENV=prod refused" >&2; exit 2; }
PYTHONPATH=src ENV=dev .venv/bin/python -m pytest tests/ -q
