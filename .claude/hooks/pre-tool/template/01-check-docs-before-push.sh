#!/bin/bash
# Docs gate: on `git push`, deny when unpushed commits change source files but neither
# touch the hand-written docs in docs/System/ nor carry a "Docs-Checked: no-changes" trailer.
# The generated system map (docs/System/map.md, docs/System/map/) does not count: a rebuild
# touches it without anyone reviewing the prose. `build.py --check` verifies it instead.
# Unpushed = since the upstream, or since the fork point from dev on a branch's first push.
. "$(dirname "$0")/lib/common.sh"

hook_runs_git push || exit 0
REPO="$(hook_git_dir)"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || exit 0

if git -C "$REPO" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
  BASE='@{u}'
else
  BASE="$(git -C "$REPO" merge-base HEAD origin/dev 2>/dev/null || git -C "$REPO" merge-base HEAD dev 2>/dev/null)"
  [ -n "$BASE" ] || exit 0
fi
RANGE="$BASE..HEAD"

CHANGED="$(git -C "$REPO" log "$RANGE" --name-only --pretty=format: 2>/dev/null | grep -v '^$' | sort -u)"
[ -n "$CHANGED" ] || exit 0

# Planning docs, template files, and root markdown (MEMORY.md, TESTS.md, ...) are not source.
SRC_COUNT="$(printf '%s\n' "$CHANGED" | grep -vE '^(docs/|\.claude/|\.githooks/|\.gitignore$|[^/]+\.md$)' | grep -c .)"
[ "$SRC_COUNT" -eq 0 ] && exit 0

printf '%s\n' "$CHANGED" | grep '^docs/System/' | grep -qvE '^docs/System/(map\.md$|map/)' && exit 0
git -C "$REPO" log "$RANGE" --format=%B | grep -qiE '^Docs-Checked:[[:space:]]*no-changes' && exit 0

deny "$SRC_COUNT source file(s) changed in unpushed commits but docs/System/ has no updates
(the generated map does not count). Run the doc-updater agent and commit its changes. If it
reports that no documentation needs to change, add its trailer to the commit message:
\"Docs-Checked: no-changes — <reason>\"."
