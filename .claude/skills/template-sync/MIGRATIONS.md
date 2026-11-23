# Template Migrations

Breaking changes per major version: what changes for an existing project, and any manual step.
`template-sync` shows every entry newer than the project's previous version at the end of a sync.

---

## 1.2.0 (minor — no manual step required)

- New **`session-start`** hook type (`.claude/hooks/session-start/`, with its own `dispatcher.sh` stub and
  a `SessionStart` entry in `settings.json`). Its first hook tells the session when a newer template is out.
  It makes one request to `raw.githubusercontent.com` per machine per day; opt out with
  `CLAUDE_TEMPLATE_UPDATE_CHECK=0`.
- Release notes now live in `CHANGELOG.md` next to this file; `template-sync` shows both at the end.

---

## 1.1.0 (minor — no manual step required)

- **Docs gate (`01`)**: the generated system map (`docs/System/map.md`, `docs/System/map/`) no longer counts
  as "docs updated". A push that changes code and only regenerates the map now needs a hand-written doc
  update or a `Docs-Checked: no-changes — <why>` trailer.
- **Arch-check gate (`05`)**: a `.claude/arch-check.sh` that exists but is not executable now **denies** the
  push (it used to be skipped silently). Fix: `git update-index --chmod=+x .claude/arch-check.sh` and commit.
- **`doc-updater`** searches the docs for every symbol the diff changes, regenerates the system map, reports
  glossary entries the change made false (`GLOSSARY:` lines), and writes the reason in the trailer
  (`Docs-Checked: no-changes — <why>`).
- New **`docs-audit`** agent, run before `dev → tst`. New **`system-map`** skill (optional): an HTML map of
  the system generated from the code, checked as an Architecture Invariant.

---

## 1.0.0

**Upgrading from 0.x — safest path:** do **not** run the project's old 0.x skill. Its `rsync --delete`
deletes project skills and agents and overwrites `deployment-process`. Instead tell Claude: *"Clone or pull
https://github.com/felipe-stanham/ClaudeCodeTemplate.git into ~/.claude/ClaudeCodeTemplate, read
Template/.claude/skills/template-sync/SKILL.md there, and follow it from step 3."* One run, nothing deleted.

**If the old skill already ran:** run `template-sync` **twice**. The first run uses your project's old 0.x skill: it
installs the new files and the gates already work, but it can't update `settings.json` and deletes project
skills/agents. The next session start then prints "Template migration incomplete"; the second run (now the
1.0.0 skill) migrates `settings.json`, writes `.claude/.template-manifest`, and offers to restore any
project skill or agent the old sync deleted.

What changes:

- **Hooks now block.** Before 1.0.0 no hook ever blocked (wrong exit code) and project hooks never ran.
  Now: pushes need `docs/System/` updates or a `Docs-Checked: no-changes` trailer; committing, merging, or
  pushing to `main`/`master`/`tst` asks for approval; secrets in git are denied; code edits are denied
  outside a task/scope branch; `.claude/arch-check.sh` runs before every push if present.
- **No code on `dev`, ever.** Every code change gets a `T-xxxx.md` (new `task` skill) and a
  `task-T-xxxx-*` branch. Hotfixes are task branches from `main`/`tst`.
- **Checkout model.** The primary checkout stays on `dev` and is for planning; pitches, tasks, specs and
  the glossary are committed and pushed there as they are written. Code is written in a worktree per
  task/scope branch (`../<repo>-wt/<branch>`).
- **The main session no longer writes code.** The new `implementer` agent does, inside the Execution Loop
  (implementer → code-reviewer → tester → doc-updater → one commit). `code-reviewer` and `tester` now run
  on Opus.
- **Status moves to `dev`.** `[IN PROGRESS]` / `[DONE]` and INDEX updates are made on `dev` after the
  branch merges, not on the branch. `doc-updater` no longer touches INDEX files.
- **`pitch` no longer promotes.** It recommends Task / Project / Archive; `task` or `spec` performs it.
- **Project hooks (`.claude/hooks/*/project/*.sh`) run as subprocesses**, not sourced. Read the tool call
  from `HOOK_TOOL`, `HOOK_CMD`, `HOOK_FILE`, `HOOK_CWD` (or stdin JSON). Exit `2` to deny, `3` to ask; any
  other non-zero exit is reported as an error and does not block. Pre-tool hooks only fire for
  Bash / Edit / Write / MultiEdit / NotebookEdit.
- **`template-sync` stops deleting project files.** It now tracks template-owned files in
  `.claude/.template-manifest`, and also syncs `docs/templates/` and `.githooks/`. Edits you made to files
  under `docs/templates/` are overwritten — keep project-specific templates elsewhere.

Manual steps:

- Make sure `.gitignore` contains `.env`, `.deploy-secrets`, and `docs/local/` — otherwise every
  `git add`/`git commit` is denied.
- If you work on code directly in the primary checkout, move it to a worktree:
  `git worktree add ../<repo>-wt/<branch> <branch>`, then `git checkout dev` in the primary checkout.
- The template folder is `docs/templates/` (lowercase). On macOS a project folder named `docs/Templates/`
  works by accident (case-insensitive file system) but becomes a second folder on Linux:
  `git mv docs/Templates docs/templates-tmp && git mv docs/templates-tmp docs/templates`.
- Code worktrees live in `../<repo>-wt/`, outside the session folder, where some commands (`chmod`) can be
  denied: add that directory to the session's folders or to `permissions.additionalDirectories` in
  `.claude/settings.local.json`.
- If a project hook relied on being sourced (shared variables, `exit` ending the dispatcher), rewrite it
  for the subprocess contract above.
