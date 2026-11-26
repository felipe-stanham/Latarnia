#!/bin/bash
# Task-before-code: deny Edit/Write on code unless the file's repo is on a task-T-* or
# scope-P-* branch whose task/project file exists (in the working tree or on dev).
# Exempt: docs/, *.md, .claude/, .gitignore, .githooks/, tests/cache/.
# Only applies to repos that use this template (CLAUDE.md carries TEMPLATE VERSION).
. "$(dirname "$0")/lib/common.sh"

case "$HOOK_TOOL" in
  Edit|Write|MultiEdit|NotebookEdit) ;;
  *) exit 0 ;;
esac
[ -n "$HOOK_FILE" ] || exit 0

FILE="$HOOK_FILE"
case "$FILE" in
  /*) ;;
  *) FILE="$HOOK_CWD/$FILE" ;;
esac
DIR="$(dirname "$FILE")"
while [ ! -d "$DIR" ]; do DIR="$(dirname "$DIR")"; done
ROOT="$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null)" || exit 0
head -5 "$ROOT/CLAUDE.md" 2>/dev/null | grep -q 'TEMPLATE VERSION' || exit 0

# Path relative to the repo root, resolving symlinks such as /tmp → /private/tmp.
REAL_DIR="$(cd "$DIR" && pwd -P)"
REAL_ROOT="$(cd "$ROOT" && pwd -P)"
REAL_FILE="$REAL_DIR${FILE#"$DIR"}"
REL="${REAL_FILE#"$REAL_ROOT"/}"

case "$REL" in
  docs/*|*.md|.claude/*|.gitignore|.githooks/*|tests/cache/*) exit 0 ;;
esac

BRANCH="$(git -C "$ROOT" branch --show-current 2>/dev/null)"
if [[ $BRANCH =~ ^task-(T-[0-9]+)(-|$) ]]; then
  ARTIFACT="docs/Tasks/${BASH_REMATCH[1]}.md"
elif [[ $BRANCH =~ ^scope-(P-[0-9]+)- ]]; then
  ARTIFACT="docs/Projects/${BASH_REMATCH[1]}.md"
else
  deny "code edits need a task or scope branch (current: '${BRANCH:-detached HEAD}', file: $REL).
Create the task with the \`task\` skill (or use the project's scope), then work in its
worktree on task-T-xxxx-* / scope-P-xxxx-N-*. This applies to every change, however small."
fi

[ -f "$ROOT/$ARTIFACT" ] && exit 0
git -C "$ROOT" cat-file -e "dev:$ARTIFACT" 2>/dev/null && exit 0
git -C "$ROOT" cat-file -e "origin/dev:$ARTIFACT" 2>/dev/null && exit 0
deny "branch '$BRANCH' points to $ARTIFACT, which does not exist here or on dev.
Create it with the \`task\` (or \`spec\`) skill and push it to dev first."
