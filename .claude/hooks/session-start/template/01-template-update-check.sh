#!/bin/bash
# Template update check — at session start, tells Claude when a newer ClaudeCodeTemplate is out,
# with the release notes since this project's version, so it can offer `template-sync`.
# Never changes the project. Silent when current, offline, or slow (2 s cap); fetches at most once a
# day per machine (cache in ~/.cache/claude-code-template/). Managed by template-sync — do not edit.
#
# Opt out per project or machine: CLAUDE_TEMPLATE_UPDATE_CHECK=0 (e.g. in the "env" of
# .claude/settings.local.json). CLAUDE_TEMPLATE_CHANGELOG_URL overrides the source (tests, forks).

[ "${CLAUDE_TEMPLATE_UPDATE_CHECK:-1}" = 0 ] && exit 0
# Only new conversations: a resumed or compacted session has already been told.
case "${HOOK_SOURCE:-startup}" in startup|clear) ;; *) exit 0 ;; esac

ROOT="${CLAUDE_PROJECT_DIR:-${HOOK_CWD:-$PWD}}"
LOCAL=$(head -5 "$ROOT/CLAUDE.md" 2>/dev/null | sed -nE 's/.*TEMPLATE VERSION: ([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | head -1)
[ -n "$LOCAL" ] || exit 0

URL="${CLAUDE_TEMPLATE_CHANGELOG_URL:-https://raw.githubusercontent.com/felipe-stanham/ClaudeCodeTemplate/main/Template/.claude/skills/template-sync/CHANGELOG.md}"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/claude-code-template"
CACHE="$CACHE_DIR/CHANGELOG.md"

# Refresh the cached changelog at most once a day. A failed fetch keeps the old copy for another day.
if [ -z "$(find "$CACHE" -mmin -1440 2>/dev/null)" ]; then
  mkdir -p "$CACHE_DIR" 2>/dev/null || exit 0
  TMP="$CACHE_DIR/.CHANGELOG.$$"
  if curl -fsSL --connect-timeout 2 --max-time 2 "$URL" -o "$TMP" 2>/dev/null && grep -qE '^## [0-9]+\.[0-9]+\.[0-9]+' "$TMP"; then
    mv -f "$TMP" "$CACHE"
  else
    rm -f "$TMP"
    [ -f "$CACHE" ] && touch "$CACHE"
  fi
fi
[ -f "$CACHE" ] || exit 0

python3 - "$LOCAL" "$CACHE" <<'PY' 2>/dev/null || true
import re, sys

local, path = sys.argv[1], sys.argv[2]
ver = lambda s: tuple(int(x) for x in s.split("."))
MAX_LINES = 60

entries, cur = [], None
for line in open(path, encoding="utf-8"):
    m = re.match(r"## (\d+\.\d+\.\d+)\b", line)
    if m:
        cur = [m.group(1), [line.rstrip()]]
        entries.append(cur)
    elif line.startswith("## "):
        cur = None
    elif cur is not None:
        cur[1].append(line.rstrip())

newer = sorted((e for e in entries if ver(e[0]) > ver(local)), key=lambda e: ver(e[0]), reverse=True)
if not newer:
    sys.exit(0)

latest = newer[0][0]
major = any(ver(v)[0] > ver(local)[0] for v, _ in newer)
notes = "\n".join("\n".join(lines).strip() for _, lines in newer).splitlines()
if len(notes) > MAX_LINES:
    notes = notes[:MAX_LINES] + ["… (more in .claude/skills/template-sync/CHANGELOG.md after the sync)"]

print(f"ClaudeCodeTemplate update available: this project is on v{local}, the latest is v{latest}.")
print("Tell the user once, near the start of your first reply: the new version and the release notes below "
      "in a few lines. Offer to run the `template-sync` skill (on `dev`, clean tree). Do not run it without "
      "the user's yes.")
if major:
    print("This includes a major version: after the sync, walk the user through the MIGRATIONS.md entries "
          "it shows, including any manual step.")
print("\nRelease notes:\n" + "\n".join(notes))
PY
exit 0
