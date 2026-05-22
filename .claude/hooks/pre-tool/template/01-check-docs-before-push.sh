#!/bin/bash
# PreToolUse hook: blocks git push if source files changed but docs/System/ was not updated.
# Receives tool input as JSON on stdin.

COMMAND=$(python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
")

# Only intercept git push
if ! echo "$COMMAND" | grep -qE "git\s+push"; then
    exit 0
fi

# If no upstream exists yet (first push), allow it
if ! git rev-parse @{u} &>/dev/null 2>&1; then
    exit 0
fi

# Count unpushed commits that touch source files vs docs/System/
UNPUSHED_SRC=$(git log @{u}..HEAD --name-only --pretty=format: 2>/dev/null \
    | grep -v "^docs/System" \
    | grep -v "^\.claude" \
    | grep -v "^$" \
    | wc -l | tr -d ' ')

UNPUSHED_DOCS=$(git log @{u}..HEAD --name-only --pretty=format: 2>/dev/null \
    | grep "^docs/System" \
    | wc -l | tr -d ' ')

# No source changes → nothing to document, allow push
if [ "$UNPUSHED_SRC" -eq 0 ]; then
    exit 0
fi

# Source changed but docs/System/ not touched → block and ask Claude to run doc-updater
if [ "$UNPUSHED_DOCS" -eq 0 ]; then
    echo "BLOCKED: $UNPUSHED_SRC source file(s) changed in unpushed commits but docs/System/ has no updates."
    echo "Invoke the @doc-updater agent to update docs/System/ before pushing."
    exit 1
fi

exit 0
