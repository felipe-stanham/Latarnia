# Template Changelog

Release notes per version, newest first: what a project gains from syncing, in a few lines each.
Breaking changes and manual steps are in `MIGRATIONS.md`.

The session-start update check reads this file from the template repo: the first `## X.Y.Z` heading is
the latest version, and every entry newer than the project's version is shown. Keep one `## X.Y.Z — date`
heading per release, matching line 1 of `CLAUDE.md`.

---

## 1.2.0 — 2026-11-23

- **Update check at session start.** A new `session-start` hook compares this project's template version
  with the latest on GitHub and, when it is behind, brings these release notes into the session so Claude
  offers to run `template-sync`. Never syncs by itself; silent when offline; fetches at most once a day.
  Opt out with `CLAUDE_TEMPLATE_UPDATE_CHECK=0`.
- **`template-sync`** shows these release notes alongside the `MIGRATIONS.md` entries at the end of a sync.
- Hook runner: session-start hooks can add text to the session's context (their stdout).

## 1.1.0 — 2026-11-20

- **System map** (new `system-map` skill): an HTML page, Markdown and SVGs generated from the code, with a
  `--check` that runs as an Architecture Invariant before every push and in CI.
- **`docs-audit`** agent (new): compares the hand-written docs with the code before `dev → tst`.
- **`doc-updater`** searches the docs for every symbol a change touches, regenerates the map, and reports
  glossary entries the change made false.
- Docs gate `01` ignores the generated map; `Docs-Checked: no-changes — <why>` carries a reason.
  Arch gate `05` blocks a non-executable `arch-check.sh` instead of skipping it.

## 1.0.0 — 2026-10-19

- **Hard gates that block**: docs before push, protected branches ask, no secrets in git, no code outside a
  task/scope branch, architecture check before push.
- **Execution Loop** with model routing: `implementer` → `code-reviewer` → `tester` → `doc-updater` →
  one commit. New `task` and `arch-check` skills.
- **Checkout model**: the primary checkout stays on `dev` for planning; code is written in worktrees.
- **`template-sync`** tracks template-owned files in a manifest and stops deleting project files.
  Upgrading from 0.x needs care: see `MIGRATIONS.md`.
