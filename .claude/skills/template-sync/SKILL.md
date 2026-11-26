---
name: template-sync
description: Syncs this project with the latest ClaudeCodeTemplate. Updates CLAUDE.md and every template-owned file (skills, agents, hooks, dispatchers, doc templates, .githooks) listed in .claude/.template-manifest, and merges the template's hook entries into settings.json — without touching docs/SYSTEM.md, MEMORY.md, project skills/agents, or project hook scripts. Also repairs incomplete upgrades. Run when you want to pull template updates.
---

## What This Skill Does

Pulls the latest version of [ClaudeCodeTemplate](https://github.com/felipe-stanham/ClaudeCodeTemplate) and applies it:

- `CLAUDE.md` — full overwrite
- Every file listed in the template's `.claude/.template-manifest` — copied (skills, agents,
  `hooks/*/template/`, `hooks/*/dispatcher.sh`, `docs/templates/`, `.githooks/`)
- `seed` entries in the manifest (e.g. `deployment-process`, which each project adapts) — copied only if
  the project doesn't have them yet
- Files the template dropped since your last sync (in your old manifest, not in the new one) — deleted
- `.claude/settings.json` — only the template's hook entries (those pointing at
  `.claude/hooks/*/dispatcher.sh`) are replaced; every other key and hook entry is kept
- `.claude/.template-manifest` — replaced with the new one

Projects on 1.2.0 or later are told at session start when a newer version is out (hook
`session-start/template/01-template-update-check.sh`, release notes from `CHANGELOG.md`); this skill is
what applies it.

**Never touched:** `docs/SYSTEM.md`, `MEMORY.md`, `.claude/arch-check.sh`, `.claude/hooks/*/project/`,
and any skill or agent not listed in the manifest (those belong to the project).

It also **repairs** projects upgraded by a pre-1.0 `template-sync` (which deleted project skills/agents and
wrote an old dispatcher). The repair runs on every sync — including when the version is already current.

---

## Steps

### 1. Fetch the latest template

```bash
TEMPLATE_DIR="$HOME/.claude/ClaudeCodeTemplate"
TEMPLATE_REPO="https://github.com/felipe-stanham/ClaudeCodeTemplate.git"
TEMPLATE_SRC="$TEMPLATE_DIR/Template"

if [ -d "$TEMPLATE_DIR/.git" ]; then
  echo "Updating cached template..."
  git -C "$TEMPLATE_DIR" pull --quiet
else
  echo "Cloning template..."
  git clone --quiet "$TEMPLATE_REPO" "$TEMPLATE_DIR"
fi
```

### 2. Follow the newest version of this skill

```bash
cmp -s "$TEMPLATE_SRC/.claude/skills/template-sync/SKILL.md" .claude/skills/template-sync/SKILL.md \
  && echo "skill current" || echo "skill changed"
```

If it prints `skill changed`, **stop following this file**. Read
`$TEMPLATE_SRC/.claude/skills/template-sync/SKILL.md` and follow its steps from step 3 instead.

### 3. Preflight

```bash
git branch --show-current
git status --porcelain
```

- Run the sync on `dev` in the primary checkout. If on another branch, ask the user before continuing.
- The working tree must be clean (the repair step reads deletions from git). If it isn't, ask the user to
  commit or stash first. Exception: if the only changes are deletions under `.claude/skills/` or
  `.claude/agents/` left by a previous sync, continue — step 8 handles them.

### 4. Read versions

```bash
CURRENT_VERSION=$(head -5 CLAUDE.md | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "unknown")
INCOMING_VERSION=$(head -5 "$TEMPLATE_SRC/CLAUDE.md" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "unknown")
echo "Current: v$CURRENT_VERSION → Incoming: v$INCOMING_VERSION"
```

If the versions match, run only the **repair checks** (step 9). If all pass, report "already up to date"
and stop. If any fails, run steps 6–8 to repair, then report.

### 5. Sync CLAUDE.md

```bash
cp "$TEMPLATE_SRC/CLAUDE.md" CLAUDE.md
echo "✓ CLAUDE.md"
```

### 6. Sync template-owned files

```bash
NEW_MANIFEST="$TEMPLATE_SRC/.claude/.template-manifest"
OLD_MANIFEST=".claude/.template-manifest"
entries() { grep -vE '^[[:space:]]*(#|$)|^seed ' "$1" | sort -u; }   # overwritten every sync
seeds()   { sed -n 's/^seed //p' "$1" | sort -u; }                    # copied only if missing

# Copy every template-owned file (cp -p keeps the executable bit)
entries "$NEW_MANIFEST" | while IFS= read -r f; do
  mkdir -p "$(dirname "$f")"
  cp -p "$TEMPLATE_SRC/$f" "$f"
done
seeds "$NEW_MANIFEST" | while IFS= read -r f; do
  [ -e "$f" ] && continue
  mkdir -p "$(dirname "$f")"
  cp -p "$TEMPLATE_SRC/$f" "$f" && echo "  seeded $f"
done
echo "✓ template-owned files"

# Delete files the template dropped since the last sync
if [ -f "$OLD_MANIFEST" ]; then
  comm -23 <(entries "$OLD_MANIFEST") <(entries "$NEW_MANIFEST") | while IFS= read -r f; do
    rm -f "$f" && echo "  removed $f"
  done
else
  echo "  no previous manifest — nothing deleted outside hooks/*/template/"
fi

# hooks/*/template/ is template-owned as a whole: drop anything the template doesn't ship
for HOOK_TYPE in pre-tool post-tool stop session-start; do
  mkdir -p ".claude/hooks/$HOOK_TYPE/project"
  touch ".claude/hooks/$HOOK_TYPE/project/.gitkeep"
  find ".claude/hooks/$HOOK_TYPE/template" -type f 2>/dev/null | while IFS= read -r f; do
    entries "$NEW_MANIFEST" | grep -qxF "$f" || { rm -f "$f" && echo "  removed $f"; }
  done
done

cp "$NEW_MANIFEST" "$OLD_MANIFEST"
echo "✓ .claude/.template-manifest"
```

Without a previous manifest (first sync from a pre-1.0 project), list in the report any skill or agent
directory that is not in the new manifest, marked "project-owned — kept".

### 7. Merge settings.json hook entries

```bash
python3 - "$TEMPLATE_SRC/.claude/settings.json" .claude/settings.json <<'PY'
import json, os, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = json.load(open(src_path))
dst = json.load(open(dst_path)) if os.path.exists(dst_path) else {}

def is_template_entry(entry):
    return any(".claude/hooks/" in h.get("command", "") and "dispatcher.sh" in h.get("command", "")
               for h in entry.get("hooks", []))

hooks = dst.setdefault("hooks", {})
for event, entries in src.get("hooks", {}).items():
    project_entries = [e for e in hooks.get(event, []) if not is_template_entry(e)]
    hooks[event] = entries + project_entries
for key, value in src.items():
    if key != "hooks":
        dst.setdefault(key, value)

with open(dst_path, "w") as f:
    json.dump(dst, f, indent=2)
    f.write("\n")
PY
echo "✓ .claude/settings.json (template hook entries)"
```

Then install the git-level secrets backstop:

```bash
if [ -n "$(git config core.hooksPath)" ]; then
  echo "  core.hooksPath already set to $(git config core.hooksPath) — not changed. Add .githooks/pre-commit to it by hand."
elif ls .git/hooks 2>/dev/null | grep -qv '\.sample$'; then
  echo "  custom hooks found in .git/hooks — core.hooksPath not set. Move them to .githooks/ first."
else
  git config core.hooksPath .githooks && echo "✓ core.hooksPath → .githooks"
fi
```

### 8. Restore project files an old sync deleted or overwrote

Pre-1.0 syncs ran `rsync --delete`, which removed project-owned skills and agents and overwrote seed files
the project had adapted (e.g. `deployment-process`). Find them:

```bash
SYNC_COMMIT=$(git log -1 --format=%H --grep='sync ClaudeCodeTemplate')
SEEDS=$(sed -n 's/^seed //p' "$TEMPLATE_SRC/.claude/.template-manifest")
{
  # Deleted project skills/agents — in the working tree, or by the last sync commit
  git diff --name-only --diff-filter=D HEAD -- .claude/skills .claude/agents | sed 's/^/HEAD /'
  [ -n "$SYNC_COMMIT" ] && git diff --name-only --diff-filter=D "$SYNC_COMMIT^" "$SYNC_COMMIT" -- .claude/skills .claude/agents | sed "s/^/$SYNC_COMMIT^ /"
} | while read -r SOURCE f; do
  # A path that ever existed in the template repo is template-owned — the template removed it on purpose.
  [ -n "$(git -C "$TEMPLATE_DIR" log --oneline -1 -- "Template/$f")" ] && continue
  echo "$SOURCE $f"
done
for f in $SEEDS; do
  # Seed files a sync overwrote with the template's copy — in the working tree, or by the last sync commit
  cmp -s "$f" "$TEMPLATE_SRC/$f" || continue
  if ! git diff --quiet HEAD -- "$f" 2>/dev/null; then echo "HEAD $f"
  elif [ -n "$SYNC_COMMIT" ] && ! git diff --quiet "$SYNC_COMMIT^" "$SYNC_COMMIT" -- "$f" 2>/dev/null; then echo "$SYNC_COMMIT^ $f"
  fi
done
```

Each output line is `<source> <path>`: a project file that was deleted, or a seed file whose project
version was overwritten. Show the list to the user. On approval, restore each one with
`git checkout <source> -- <path>`. If the list is empty, say nothing.

### 9. Repair checks

All must pass. These run on every sync, even at equal versions:

```bash
for t in pre-tool post-tool stop session-start; do
  grep -q TEMPLATE-DISPATCH-STUB ".claude/hooks/$t/dispatcher.sh" 2>/dev/null || echo "FAIL dispatcher $t is not the v1 stub"
done
[ -f .claude/.template-manifest ] || echo "FAIL .claude/.template-manifest missing"
grep -q 'CLAUDE_PROJECT_DIR' .claude/settings.json 2>/dev/null || echo "FAIL settings.json hook entries not migrated"
```

Plus step 8 must return an empty list. Any `FAIL` → run steps 6–8, then re-check.

### 10. Report and suggest commit

```
template-sync complete
  v{CURRENT_VERSION} → v{INCOMING_VERSION}
  Updated: CLAUDE.md, template-owned files (manifest), settings.json hook entries, dispatchers
  Removed: {files the template dropped, or "none"}
  Restored: {project skills/agents restored, or "none"}
  Kept (project-owned): docs/SYSTEM.md, MEMORY.md, .claude/arch-check.sh, hooks/*/project/, {project skills/agents}
```

Then show the user what changed, for every version newer than `CURRENT_VERSION` and up to `INCOMING_VERSION`:

- the release notes from `.claude/skills/template-sync/CHANGELOG.md` — a few lines per version;
- every entry in `.claude/skills/template-sync/MIGRATIONS.md` — the breaking changes and manual steps, in full.

Suggest the user stage and commit on `dev` (keep this message — step 8 searches for it):
```
git add -A && git commit -m "chore: sync ClaudeCodeTemplate v{INCOMING_VERSION}"
```

---

## Rules

- Do NOT modify `docs/SYSTEM.md`, `MEMORY.md`, `.claude/arch-check.sh`, any file under `hooks/*/project/`,
  or any skill/agent not listed in the template manifest.
- Never delete a file that is not in the old manifest (except inside `hooks/*/template/`, which is template-owned).
- If `python3` is not available, stop and tell the user — do not hand-edit `settings.json`.
- If the clone/pull fails (no network, auth issue), stop and report the error clearly.
- Equal versions are not a reason to skip the repair checks.
