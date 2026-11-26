---
name: arch-check
description: Architecture invariants assistant. Declares the rules the code must never break in docs/SYSTEM.md and makes them executable in .claude/arch-check.sh, which runs before every push (hook) and in CI. Invoke to add, change or remove an invariant, or to set up the check for the first time.
---

## What This Skill Does

An architecture principle written in prose is a wish. An **invariant** is a principle with a
check that fails when the code breaks it. This skill keeps the two in sync:

- `docs/SYSTEM.md` → `## Architecture Invariants` table — *what* must hold and *what* enforces it.
- `.claude/arch-check.sh` — the single executable entry point that runs every check.
  - Run by the `05-arch-check-before-push.sh` hook before every `git push` (a failure blocks the push).
  - Run in CI from `.github/workflows/arch-check.yml` (seed: `docs/templates/ci-arch-check.template.yml`).

`.claude/arch-check.sh` is project-owned: `template-sync` never touches it.

---

## Session Startup

1. Read `docs/SYSTEM.md` — the Stack, Architecture Principles and Architecture Invariants sections.
2. Read `.claude/arch-check.sh` if it exists.
3. Run `git branch --show-current`. Invariants are planning: the SYSTEM.md table is committed on `dev`.
   The script and its tool config are code: they go through a task (`task` skill) and its branch.

---

## Steps

### 1. Pin down the invariant

Ask until the rule is concrete enough to be checked mechanically:
- Which modules/paths are involved? Use the subsystem names from `docs/SYSTEM.md` and the glossary.
- What exactly is forbidden (an import, a call, a dependency, a file location)?
- Are there allowed exceptions today? List them explicitly — a check with silent exceptions rots.

Reject invariants that cannot be checked by a tool ("code must be clean"). Suggest a narrower,
checkable version or leave it as a principle for the `code-reviewer`.

### 2. Pick the mechanism

Prefer an off-the-shelf tool for the stack over a hand-written script:

| Stack | Tool | Typical invariant |
|---|---|---|
| Python | [import-linter](https://import-linter.readthedocs.io) (`lint-imports`) | Layers, forbidden imports, independent modules |
| JS / TS | [dependency-cruiser](https://github.com/sverweij/dependency-cruiser) (`depcruise`) | Forbidden/required dependencies, no cycles, no orphans |
| .NET | [NetArchTest](https://github.com/BenMorris/NetArchTest) (as a test project) | Layer dependencies, naming, sealed/abstract rules |
| Java / Kotlin | ArchUnit (as a test) | Layers, package cycles |
| Any | `grep` / `rg` in the script | "No `requests` outside `clients/`", "no SQL strings outside `repo/`" |

### 3. Update the table in `docs/SYSTEM.md`

Add one row per invariant: `# | Invariant | Enforced by | Mechanism`. "Enforced by" names the
concrete rule (e.g. the import-linter contract name); "Mechanism" is `.claude/arch-check.sh`.
A row with an empty "Enforced by" column is not an invariant — do not add it.

### 4. Write or extend `.claude/arch-check.sh` (via a task branch)

Create a task with the `task` skill if there isn't one, then in its worktree:

```bash
#!/bin/bash
# Architecture invariants — see docs/SYSTEM.md "Architecture Invariants".
# Exit non-zero if any invariant is broken. Run by the pre-push hook and by CI.
set -u
FAILED=0
run() { echo "== $1"; shift; "$@" || FAILED=1; }

run "1 domain-independent" lint-imports --contract domain-independent
# run "2 no-cycles" npx depcruise src --config .dependency-cruiser.js
# run "3 http-only-in-clients" sh -c '! rg -l "import requests" src --glob "!src/clients/**"'

exit $FAILED
```

- Keep it fast (seconds): it runs on every push.
- Print the invariant number before each check so a failure points to the table row.
- Make it executable (`chmod +x .claude/arch-check.sh`, or `git update-index --chmod=+x` when `chmod` is
  denied) — the hook denies every push while the script exists but is not executable.
- Add tool config files (`.importlinter`, `.dependency-cruiser.js`, …) in the same commit.

### 5. Wire CI (first time only)

Copy `docs/templates/ci-arch-check.template.yml` to `.github/workflows/arch-check.yml` and fill in
the setup step for the stack.

### 6. Verify

Run `.claude/arch-check.sh` and confirm it passes. Then break the invariant on purpose (one line),
confirm the script fails with a clear message, and revert.

---

## Rules

- Every invariant in the table has a working check; every check in the script has a table row.
- Do not weaken a failing check to make a push pass. A failing invariant is either a real
  violation (fix the code) or an outdated rule (change the invariant with the user's approval).
- Exceptions are listed explicitly in the tool config, with a one-line reason each.
