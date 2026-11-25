#!/usr/bin/env bash
# t23-017: `grep -n "_extract_app_name\|_routed_path\|X-Forwarded-Uri" src/` -> no matches.
# Searches source text only: -I skips binary files and __pycache__/ is excluded, because stale
# gitignored bytecode (e.g. a .pyc left from before T-0023 when PYTHONDONTWRITEBYTECODE=1)
# is not source and gave a false FAIL.
cd "$(dirname "$0")/../.." || exit 2
if out=$(grep -rnI --exclude-dir=__pycache__ "_extract_app_name\|_routed_path\|X-Forwarded-Uri" src/); then
  echo "FAIL t23-017: matches found"; printf '%s\n' "$out"; exit 1
fi
echo "PASS t23-017 (no matches in src/ source files)"
