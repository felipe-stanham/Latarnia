#!/usr/bin/env bash
# t23-017: `grep -n "_extract_app_name\|_routed_path\|X-Forwarded-Uri" src/` -> no matches.
cd "$(dirname "$0")/../.." || exit 2
if out=$(grep -rn "_extract_app_name\|_routed_path\|X-Forwarded-Uri" src/); then
  echo "FAIL t23-017: matches found"; printf '%s\n' "$out"; exit 1
fi
echo "PASS t23-017 (no matches in src/)"
