#!/bin/bash
# Secrets gate: on `git add` / `git commit`, deny when .env, .deploy-secrets or docs/local/
# are not gitignored, are tracked, are staged, or are being force-added.
# *.example files (e.g. .env.example) are allowed.
. "$(dirname "$0")/lib/common.sh"

hook_runs_git add commit || exit 0
REPO="$(hook_git_dir)"
ROOT="$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null)" || exit 0

SECRET_PATHS='(^|/)\.env$|(^|/)\.deploy-secrets$|^docs/local/'
PROBLEMS=""

for path in .env .deploy-secrets docs/local/notes; do
  if ! git -C "$ROOT" check-ignore -q --no-index "$path"; then
    PROBLEMS="$PROBLEMS- ${path%/notes} is not in .gitignore"$'\n'
  fi
done

TRACKED="$(git -C "$ROOT" ls-files | grep -E "$SECRET_PATHS")"
[ -n "$TRACKED" ] && PROBLEMS="$PROBLEMS- tracked by git (remove with: git rm --cached <file>): $(echo $TRACKED)"$'\n'

STAGED="$(git -C "$ROOT" diff --cached --name-only | grep -E "$SECRET_PATHS")"
[ -n "$STAGED" ] && PROBLEMS="$PROBLEMS- staged (unstage with: git restore --staged <file>): $(echo $STAGED)"$'\n'

if hook_runs_git add; then
  for arg in $(hook_git_args add); do
    arg="${arg#./}"
    if printf '%s\n' "$arg" | grep -qE "$SECRET_PATHS|^docs/local$"; then
      PROBLEMS="$PROBLEMS- the command adds $arg"$'\n'
    fi
  done
fi

[ -z "$PROBLEMS" ] && exit 0
deny "secrets must never be committed:
$PROBLEMS"
