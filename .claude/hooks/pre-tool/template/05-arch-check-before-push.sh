#!/bin/bash
# Architecture gate: on `git push`, run the project's .claude/arch-check.sh (if present) and deny
# the push when it fails. The same script runs in CI. A script that exists but is not executable
# is denied too: skipping it silently would turn the gate off without anyone noticing.
. "$(dirname "$0")/lib/common.sh"

hook_runs_git push || exit 0
REPO="$(hook_git_dir)"
ROOT="$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null)" || exit 0
SCRIPT="$ROOT/.claude/arch-check.sh"
[ -e "$SCRIPT" ] || exit 0
[ -x "$SCRIPT" ] || deny ".claude/arch-check.sh exists but is not executable, so the architecture check
would not run. Fix it in the task/scope branch and commit the mode change:
  chmod +x .claude/arch-check.sh && git add .claude/arch-check.sh
(If chmod is not allowed: git update-index --chmod=+x .claude/arch-check.sh)"

OUT="$(mktemp)"
(cd "$ROOT" && "$SCRIPT") >"$OUT" 2>&1
CODE=$?
if [ $CODE -ne 0 ]; then
  TAIL="$(tail -20 "$OUT")"
  rm -f "$OUT"
  deny "architecture check failed (.claude/arch-check.sh exited $CODE). Last lines:
$TAIL"
fi
rm -f "$OUT"
exit 0
