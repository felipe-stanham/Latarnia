<!-- TEMPLATE VERSION: 1.2.0 -->
<!-- DO NOT EDIT — managed by ClaudeCodeTemplate -->
<!-- Project-specific instructions go in docs/SYSTEM.md -->

# Claude Code — Project Instructions

## Session Startup

**ALWAYS do this before anything else, regardless of task complexity:**
1. Read `MEMORY.md` if it exists.
2. Read `docs/SYSTEM.md` if it exists.
3. Read `docs/System/glossary.md` if it exists — this is the project's Ubiquitous Language. Every artifact and conversation must use its canonical terms.
4. Confirm with one line: "Loaded MEMORY.md ✓ / docs/SYSTEM.md ✓ / glossary.md ✓"
5. Do NOT read the artifact indexes (`docs/Pitches/INDEX.md`, `docs/Tasks/INDEX.md`, `docs/Projects/INDEX.md`) or any P/T/I file at startup. Load these only when the user asks to see open work or names a specific artifact.
6. Run `git branch --show-current` and act on the result:
   - `main` or `tst` → **STOP** and output: "WARNING: current branch is `{branch}` — a protected branch. Work happens on `dev` (planning) or in a task/scope worktree (code)." Do not write anything until the user says what to do.
   - `dev` → this is the planning checkout: pitches, tasks, specs, glossary. To write code, create or enter the task/scope worktree (see "Checkouts and Worktrees").
   - `task-T-*` / `scope-P-*` → this is a code worktree: run the Execution Loop for that task or scope.
7. If `.claude/.template-manifest` does not exist, output: "Template migration incomplete — run the `template-sync` skill." (The manifest is written by template-sync v1.0.0 or later.)
8. If the session context has a "ClaudeCodeTemplate update available" notice (from the session-start hook), tell the user once: the new version and its release notes in a few lines, and offer to run `template-sync`. Never run it without the user's yes.

## System Context

- `docs/SYSTEM.md` is the always-loaded system index. It describes the existing system at a high level without requiring all project, task, or pitch files to be read.
- `docs/SYSTEM.md` must stay under ~150 lines. Detail belongs in individual P-xxxx.md, T-xxxx.md, or I-xxxx.md files. Lists of those artifacts live in their own index files (see below), not in docs/SYSTEM.md.
- `docs/SYSTEM.md` contains:
  - A brief description of what the system does and its main components
  - Key architectural decisions that must be respected across all projects
  - Architecture invariants and what enforces them (see the `arch-check` skill)
  - Cross-project constraints that apply to every session
  - Pointers to the artifact indexes (`docs/Pitches/INDEX.md`, `docs/Tasks/INDEX.md`, `docs/Projects/INDEX.md`)
  - A pointer to `docs/local/deployment.md` for deployment targets (do NOT inline targets here)
- Artifact indexes (one-line entries, link to the full file):
  - `docs/Pitches/INDEX.md` — open and archived pitches (`I-xxxx.md`)
  - `docs/Tasks/INDEX.md` — open and completed ad-hoc tasks (`T-xxxx.md`)
  - `docs/Projects/INDEX.md` — active and completed projects (`P-xxxx.md`)
- Never load individual project, task, or pitch files unless the user specifies which one to work on. Never load the indexes at session startup — load them only when the user asks "what's open" or to pick something to work on.
- When a project's final scope is marked `[DONE]`, update its entry in `docs/Projects/INDEX.md` to reflect completed status.
- If creating a new docs/SYSTEM.md or INDEX files, use templates at `docs/templates/`.

## Ubiquitous Language

- `docs/System/glossary.md` is the project's **Ubiquitous Language** — the canonical glossary of domain and system terms. It is always loaded at session startup.
- Use only the terms defined there in pitches, specs, code, commits, and conversation. Do not silently substitute synonyms — drift between speech and code is the bug this file exists to prevent.
- To add, edit, or remove a term — or to challenge terminology drift between artifacts — invoke the `glossary` skill. Do not edit `docs/System/glossary.md` by hand.
- If the user introduces a new domain word that isn't in the glossary, stop and propose adding it via the `glossary` skill before using it in any artifact.
- If creating a new `docs/System/glossary.md`, use the template at `docs/templates/GLOSSARY.template.md`.

## Git Ignore

Every project must have a `.gitignore` that excludes at minimum:
- `.env` — contains environment credentials
- `.deploy-secrets` — contains deployment credentials
- `docs/local/` — contains deployment targets and other host-specific notes that must not be shared
- Verify these are present before the first commit. If `.gitignore` does not exist, create one.
- *Enforced by* `03-no-secrets.sh` (denies `git add` / `git commit` while any of these is not ignored, tracked, or staged) and `.githooks/pre-commit`.

---

## General

- Work artifacts follow a maturity ladder: **Pitch (`I-xxxx.md`) → Task (`T-xxxx.md`) → Project (`P-xxxx.md`)**. See the "Work Artifacts" section below for details.
- **Every code change — however small — has a task or a project scope on record before any code is written.** No task yet? Create one with the `task` skill. This applies even in Auto Mode. *Enforced by* `04-task-before-code.sh`: code edits are denied outside a `task-T-*` / `scope-P-*` branch whose task/project file exists.
- The main session plans and orchestrates; it does not write production code. Code is written by the `implementer` agent through the Execution Loop (see below).
- When coding, do not modify the spec — if something in it is wrong or unclear, stop and ask.
- Do NOT work on a project, task, or pitch you have not been instructed to implement. I will name the specific file.
- Do NOT read all the files in `docs/Projects/`, `docs/Tasks/`, or `docs/Pitches/` unless instructed to. Use the corresponding `INDEX.md` if you need to scan what's open.
- Commit once per completed task or scope, at the end of the Execution Loop (not mid-implementation). Use clear, descriptive commit messages.
- When blocked or at a decision point with no clear answer, stop and ask rather than guessing.
- Prefer the simplest solution that works. Do not create abstractions unless there are at least two concrete use cases today.

---

## Work Artifacts

There are three kinds of work artifact, ordered by maturity:

1. **Pitch — `docs/Pitches/I-xxxx.md`.** A half-formed idea being shaped. Use the `pitch` skill to iterate on a problem statement, constraints, success criteria, and out-of-scope items before deciding whether the idea is real. Pitches can sit indefinitely; they cost nothing.
2. **Task — `docs/Tasks/T-xxxx.md`.** A bounded ad-hoc piece of work that does not justify the full project ceremony. One scope, one branch, no separate spec package. Created with the `task` skill — either directly (small, well-understood change, or a hotfix) or from a pitch recommended as a Task.
3. **Project — `docs/Projects/P-xxxx.md`** plus a `docs/Projects/P-xxxx/` folder containing `spec.md`, `workflows.md`, `data_model.md`, `architecture.md`. Multi-scope work with cross-cutting impact. Created with the `spec` skill — directly, from a pitch recommended as a Project, or by promoting a task that has outgrown its bounds.

All three are planning artifacts: they live on `dev` and are committed and pushed as they are written (see "Checkouts and Worktrees").

### Pitches

- A pitch is started by invoking the `pitch` skill with a working title. The skill runs a back-and-forth refinement: clarify the problem, list constraints, define success criteria, identify what's explicitly out of scope.
- Output is `docs/Pitches/I-xxxx.md` with the refined writeup and a **Recommendation** (Task, Project, or Archive). Add a one-line entry to `docs/Pitches/INDEX.md`.
- A pitch ends in one of three ways: promoted to a Task, promoted to a Project, or archived (mark as `[ARCHIVED]` in the index with a one-line reason).

### Promotion: Task → Project

Promote a task to a project (and stop work on the task) when **any** of the following becomes true:

- The work naturally decomposes into more than 3 scopes.
- It touches more than one named subsystem/component in docs/SYSTEM.md.
- It requires schema or data-model changes (i.e., any change to `dataModel.md`).
- It requires a new external service, API, or integration.

When promoting (the `spec` skill does this): create `P-xxxx.md` and the spec package, link the originating `T-xxxx.md` from the project's spec, mark the task `[PROMOTED → P-xxxx]` in `docs/Tasks/INDEX.md`, and stop further work on the task file. Do not delete the task — it is the historical record of how the project started.

### Promotion: Pitch → Task or Project

The `pitch` skill applies the same four triggers above at the end of refinement and **recommends** the next step; it does not create the next artifact. If none fire, it recommends a Task and the `task` skill performs the promotion; if any fire, it recommends a Project and the `spec` skill performs it. The promoting skill sets the pitch to `Promoted → T-xxxx / P-xxxx` and updates `docs/Pitches/INDEX.md`.

### Definition of Ready

A task, or a project scope, may be handed to the `implementer` only when it has:

- **Goal** — 1–2 lines, glossary terms only
- **Touches** — the files/modules expected to change, plus an existing file to use as the pattern reference
- **Tasks** — action-oriented checklist
- **Acceptance Criteria** — declarative `- **name:** input → expected output`
- **OUT of scope / No-gos** — stated as constraints
- **Open Questions** — empty

The `task` skill, the `spec` skill, and `docs/templates/TASK.template.md` follow this list. The `implementer` checks it first and returns `BLOCKED: <question>` when something is missing.

---

## Memory

- `MEMORY.md` is the index for persistent learnings. Auto memory (Claude Code's built-in system) is disabled for this project.
- Never solve the same problem twice — if you're re-discovering something, it belongs in memory.

### Structure

- `MEMORY.md` — lightweight index. Each entry is a one-line description. Simple learnings (one or two sentences) live inline here.
- `memory/` — detailed memory files. When an entry needs more context, explanation, or examples, create a file in `memory/` and link it from the index.

### Format

```
## Index

- **[date] env-pip-flag:** pip on this system requires `--break-system-packages` flag.
- **[date] api-auth-retry:** Auth token refresh logic and retry pattern. → [memory/api-auth-retry.md](memory/api-auth-retry.md)
- **[date] deploy-homeserver-quirks:** SSH and restart sequence for homeserver. → [memory/deploy-homeserver-quirks.md](memory/deploy-homeserver-quirks.md)
```

### Rules

- At session startup, read `MEMORY.md` (the index only). Load individual memory files only when relevant to the current task.
- Keep the index scannable — one line per entry, no paragraphs.
- If a learning applies only to a specific project and is already captured in that project's docs, do not duplicate it in memory.
- Entries must be self-contained plain text — no links to external files or ~/.claude paths.

---

## Environments

Every project must support at least two environment configurations: **dev** (local development and testing) and **prod** (production).

### Configuration

- Environment is selected via `ENV` variable in `.env` (values: `dev`, `tst`, `prod`). Default is `dev`.
- Each environment has its own configuration block in `.env`. Use a prefix convention:
  ```
  ENV=dev
  # Dev environment
  DEV_API_URL=https://dev.example.com
  DEV_API_USER=dev-user@example.com
  DEV_API_TOKEN=dev-token
  # Prod environment
  PROD_API_URL=https://prod.example.com
  PROD_API_USER=prod-user@example.com
  PROD_API_TOKEN=prod-token
  ```
- The config module must load the correct set of credentials based on `ENV`. Shared settings that are the same across environments do not need a prefix.
- If a project does not have external services (pure local app with no API), the `ENV` variable is still required but may only affect logging verbosity or output paths.

### Rules

- **Never run tests against prod.** All automated and manual testing uses `dev` environment only.
- **Never run destructive operations (create, update, delete) against prod** unless the user explicitly confirms.
- Before executing any test or destructive operation, verify the active environment. If `ENV=prod`, stop and warn the user.

---

## Deployment

- Use the appropriate skill when deploying.
- **Targets and procedures** live in `docs/local/deployment.md` (gitignored — never committed). This file lists `dev` / `tst` / `prd` hosts, SSH details, restart sequences, and host-specific quirks. Keep `docs/local/` gitignored so the repo can be shared publicly without leaking infrastructure detail.
- **Secrets** are in `.deploy-secrets` (gitignored, never committed). Keep secrets out of `docs/local/deployment.md` — it documents *where* and *how*, not credentials.
- `docs/SYSTEM.md` may reference `docs/local/deployment.md` by name but must not inline target details.
- After any deployment incident or procedural change, update both the corresponding skill and `docs/local/deployment.md` to reflect what actually works.
- Never deploy to `prd` without explicit user confirmation.

---

## Logging

- Use the language-appropriate logging framework. Never use `print()`, `console.log()`, or equivalent debug output for operational output.
- Stack-specific logging setup (library, format config) belongs in `docs/SYSTEM.md`.
- Log level is controlled by environment: `dev` defaults to `DEBUG`, `prod` defaults to `WARNING`.
- Log format must include: timestamp, level, module name, and message. Example: `2026-03-20 14:30:00 [INFO] fetcher: Fetched 12 children for FEAT-100`.
- Log to stdout/stderr by default. File logging is added only if the project requires it.
- What to log:
  - **INFO:** Key operation milestones (start/end of major workflows, external API calls, file writes).
  - **DEBUG:** Detailed internal state useful during development (variable values, loop iterations, config loaded).
  - **WARNING:** Recoverable issues (missing optional config, fallback behavior triggered, retries).
  - **ERROR:** Failures that stop an operation (API errors, missing required config, file write failures).
- Never log secrets, tokens, or full API responses containing sensitive data. Mask or omit them.

---

## Branching Strategy

### Branch Hierarchy

```
main (production-ready)
 └── tst (testing / staging — deploys to tst targets)
      └── dev (integration + planning — all scope and task work merges here)
           ├── scope-P-XXXX-1-<short-description>
           ├── scope-P-XXXX-N-<short-description>
           └── task-T-XXXX-<short-description>
```

- **`main`** — Production-ready code. Only receives merges from `tst` after explicit user approval (and hotfix task branches). Maps to `prd` deployment targets.
- **`tst`** — Testing/staging branch. Receives merges from `dev` after all regression tests pass. Used to deploy to `tst` targets for validation.
- **`dev`** — Integration branch, and home of every planning artifact. Receives planning docs directly and code only through merges of task/scope branches.
- **`scope-P-XXXX-N-<short-description>`** — One branch per scope of a project, branched from `dev`.
- **`task-T-XXXX-<short-description>`** — One branch per task, branched from `dev` (from `main`/`tst` for a hotfix). If the task is promoted to a project mid-flight, stop and continue on `scope-P-YYYY-1-<...>` instead.

### Checkouts and Worktrees

- **The primary checkout stays on `dev`.** It is where planning happens: the `pitch`, `task`, `spec`, and `glossary` skills only run there, and commit + push their artifacts to `dev` as they are written.
- **All code is written in a worktree** — one per task/scope branch, even with a single session:
  `git worktree add ../<repo>-wt/<branch> -b <branch> dev` (base `main`/`tst` for a hotfix).
  Worktrees live in a sibling directory, not inside the repo. Remove them after the merge: `git worktree remove ../<repo>-wt/<branch>`.
  That directory is outside the session's folder, so some commands there (e.g. `chmod`) can be denied: add `../<repo>-wt` to the session's folders, or to `permissions.additionalDirectories` in `.claude/settings.local.json`.
- **Parallel sessions** — run one session per worktree. Never manually clone the repo into separate folders. Do not use `isolation: worktree` for the `implementer` — it must work in the task/scope worktree the main session is in.

| Change | Committed on | By |
|---|---|---|
| Pitches, tasks, projects/specs, glossary, their INDEX entries | `dev` (primary checkout), pushed as written | planning skills |
| Spec or task edits during implementation (after `BLOCKED:`) | `dev` (primary checkout), then `git merge dev` into the task/scope branch | main session |
| Code + `docs/System` updates | the task/scope branch | main session (Execution Loop) |
| `docs/System` corrections with no code change (from `docs-audit`) | `dev` (primary checkout) | main session |
| Status (`[IN PROGRESS]`, `[DONE]`, `[x]`), INDEX status, tests promoted into `TESTS.md` | `dev`, right after the branch merges | main session |

### Rules

- Scope and task branches are created from `dev`. Never branch off another scope or task branch.
- **Never commit code to `dev`.** Every code change — however small — gets a `T-xxxx.md` (via the `task` skill) and a `task-T-xxxx-*` branch. *Enforced by* `04-task-before-code.sh`.
- **Never commit directly to `main` or `tst`.** They receive merges only. *Enforced by* `02-protected-branch.sh`: committing, merging, or pushing to `main`/`master`/`tst` asks for your approval — that approval is the promotion or hotfix authorization.
- **Hotfix (rare):** only when the user explicitly says "hotfix" and the change cannot wait for a normal promotion cycle. The `task` skill creates `T-xxxx.md` on `dev` with `Base: main` (or `tst`) → branch `task-T-xxxx-hotfix-<desc>` from that base → normal Execution Loop → merge into the base (approval prompt) → merge the same branch into `dev`. Note the hotfix in the commit message. No edits directly on `main`/`tst`, ever.

### Promotion Flow

1. **Task/scope → `dev`:** When the Execution Loop finishes (review passed, tests executed and passed, docs updated, branch pushed), merge the branch into `dev`. Then, on `dev`: mark the task/scope `[DONE]`, update its INDEX entry, promote critical acceptance criteria into `TESTS.md`, and push. Delete the branch and remove its worktree.
2. **`dev` → `tst`:** When the project is complete (all scopes `[DONE]`), on `dev`: run regression tests (`tester`) and the `docs-audit` agent. Resolve every difference it reports (see "Documentation"). When both are clean, merge `dev` into `tst` and deploy to `tst` targets for validation.
3. **`tst` → `main`:** After I review and approve on `tst`, merge `tst` into `main`. Deploy to `prd` targets.

---

## Planning

Always plan before coding:
- If working on a **Pitch**, invoke the `pitch` skill — do not write code from a pitch; pitches must be promoted to a Task or Project first.
- If working on a **Project** `P-xxxx.md`, read the spec package (`spec.md`, `workflows.md`, `data_model.md`, `architecture.md`) in the corresponding `P-xxxx/` folder — these contain the plan.
- If working on a **Task** `T-xxxx.md`, read the task file directly. Before starting, re-check the four promotion triggers in "Work Artifacts" — if any now apply, stop and promote to a project instead of expanding the task.
- For new work with no task or project yet — including small changes and hotfixes — invoke the `task` skill. It writes `docs/Tasks/T-xxxx.md`, adds the `INDEX.md` entry, and pushes both to `dev`.
- A task or scope is handed to the `implementer` only when it meets the **Definition of Ready** (see "Work Artifacts").

Make sure to follow the latest documentation when coding:
1. Read `architecture.md`, `dataModel.md` and `workflows.md` from the `docs/System/` folder, and `map.md` if it exists.
2. Explore relevant files, patterns, and existing implementations.

---

## Execution Loop

How every task or scope is built. The main session runs it from the task/scope worktree and orchestrates the agents; it does **not** write production code itself — all code changes go through the `implementer`, including small ones.

1. **Ready:** confirm the branch matches the work item (`task-T-xxxx-*` / `scope-P-xxxx-N-*`) and the item meets the Definition of Ready. On `dev`, mark it `[IN PROGRESS]` and push.
2. **Implement:** invoke `implementer` (sonnet · xhigh) with the work-item path.
   - `BLOCKED: <question>` → ask the user, update the spec/task on `dev` in the primary checkout, push, `git merge dev` into the branch, re-invoke.
3. **Review:** invoke `code-reviewer` (opus · xhigh). BLOCK → rework.
4. **Test:** invoke `tester` (opus · high) on the acceptance criteria. FAIL → rework.
5. **Rework:** re-invoke `implementer` with `model: opus` and the findings verbatim, then go back to step 3. **At most 2 rework cycles per task/scope in total** (review and test failures share the counter); after that, stop and escalate to the user with the findings.
6. **Document:** invoke `doc-updater` (sonnet · medium). It updates the docs, corrects statements the change made false, and regenerates the system map if there is one. It ends with `DOCS: updated …` or `DOCS: no changes needed — <why>`, preceded by a `GLOSSARY: <term> — …` line for each glossary definition the change made false: run the `glossary` skill for those (on `dev`) before merging.
7. **Commit:** one commit with the code, the tester's `tests/cache/` scripts, and the docs (map included). If doc-updater reported `no changes needed — <why>`, add the trailer `Docs-Checked: no-changes — <why>` to the commit message. Push the branch.
8. **Merge:** follow "Promotion Flow" step 1.

Fable is used only when the user asks for it — for planning by switching the session model, for a rework by passing `model: fable` to the `implementer`.

### Review checklist (code-reviewer)

- Adherence to the project's architecture principles and invariants (from `docs/SYSTEM.md`)
- Correct environment handling (no hardcoded credentials, proper ENV checks)
- Code simplicity (no unnecessary abstractions)
- Consistency with existing patterns in the codebase
- Scope discipline (only the files listed under Touches; OUT-of-scope items respected)

Do not commit until the review passes.

---

## Testing

- **Tests must be executed, not reviewed.** No scope or task can be marked `[DONE]` based on code review alone.
- **Always test against `dev` environment.** Before running any test, verify `ENV != prod`. If it is, stop and warn the user.
- Tests that create, modify, or delete external resources must use dev/sandbox credentials only.
- Test entries use the declarative format: `- **test_name:** [concrete input] → [expected output]`
- **Scope acceptance criteria** live in the `P-xxxx.md` / `T-xxxx.md` file itself. **Regression tests** live in `TESTS.md` at the repo root.
- Acceptance tests run in step 4 of the Execution Loop. All must pass before the branch is committed and merged.
- Before merging `dev → tst`, delegate to the `tester` agent for regression tests. If any fail, do not merge.
- When a scope is marked `[DONE]`, promote critical-path acceptance criteria into `TESTS.md`. Keep it small — do not dump every scope test in.

---

## Progress Tracking

- `P-xxxx.md`, `T-xxxx.md`, and `I-xxxx.md` are the single source of truth for the status of their work item. Keep them current.
- Status is updated on `dev` (primary checkout), not on task/scope branches — parallel scopes would otherwise conflict on the same Scope Summary table.
- Scope/task status: `[ ]` → `[IN PROGRESS]` (when the Execution Loop starts) → `[DONE]` (after the branch merges). Mark sub-items `[x]` at the same time.
- Never mark a Scope `[DONE]` unless its tests have been **executed and passed** (not just reviewed).
- When a P/T/I file's top-level status changes (e.g., a project finishes, a task is promoted, a pitch is archived), update its one-line entry in the corresponding `INDEX.md` in the same commit. The indexes are a derived view but must not drift from the files they list.

---

## Documentation

- Document all APIs as part of the task that implements them. REST APIs must use OpenAPI.
- The `doc-updater` agent updates `docs/System/workflows.md`, `architecture.md`, and `dataModel.md` in step 6 of the Execution Loop, based on what actually changed. Its changes are committed with the code.
- `docs/System/dataModel.md` always reflects the **current** schema. Migrations are project-level artifacts — manage them within the project's own directory, not in `docs/System/`.
- **Don't hand-copy what the code defines.** Do not write into `docs/System/` or `docs/SYSTEM.md` data that the code defines and that changes with it — versions, counts, lists of routes/tables/config keys, allowed values. Point to the source (e.g. `SCRAPER_VERSION` in `scrapers/*.py`) or to the system map.
- **System map** (optional, recommended): `docs/System/map/index.html` and `map.md`, generated from the code by `docs/System/map/build.py` — never edited by hand. Set it up with the `system-map` skill. Its `--check` is an Architecture Invariant, so it runs before every push and in CI.
- **Audit:** the `docs-audit` agent compares every checkable statement in the docs with the code. It runs before `dev → tst` (see "Promotion Flow") and whenever you ask. Doc-side differences are fixed on `dev` (glossary ones via the `glossary` skill); a `code-suspect` difference goes to the user, and if the code is wrong, to a task.
- *Enforced by* `01-check-docs-before-push.sh`: a push whose commits change source files is denied unless they also touch the hand-written docs in `docs/System/` (the generated map does not count) or carry a `Docs-Checked: no-changes — <why>` trailer.

---

## Hard Gates

Hooks in `.claude/hooks/pre-tool/template/` that block or ask before a tool call runs. Project-specific hooks go in `.claude/hooks/<type>/project/` (`<type>`: `pre-tool`, `post-tool`, `stop`, `session-start`; contract in `.claude/hooks/pre-tool/template/lib/dispatch.sh`).

| Hook | Trigger | Effect |
|---|---|---|
| `01-check-docs-before-push.sh` | `git push` | Deny: source changed without updates to hand-written `docs/System/` docs (not the generated map) or `Docs-Checked: no-changes` |
| `02-protected-branch.sh` | `git commit` / `merge` / `cherry-pick` / `revert` on, or `git push` to, `main`/`master`/`tst` | Ask the user |
| `03-no-secrets.sh` | `git add`, `git commit` | Deny: `.env`, `.deploy-secrets`, `docs/local/` not ignored, tracked, staged, or force-added |
| `04-task-before-code.sh` | Edit/Write on code (not `docs/`, `*.md`, `.claude/`, `.gitignore`, `.githooks/`, `tests/cache/`) | Deny outside a `task-T-*` / `scope-P-*` branch with an existing task/project file |
| `05-arch-check-before-push.sh` | `git push` | Deny when `.claude/arch-check.sh` (if present) fails or is not executable — see the `arch-check` skill |

**Hard gates vs code-reviewer:** gates catch mechanical rule breaks (wrong branch, secrets, missing docs, broken invariants) before they happen; the `code-reviewer` judges what a script cannot (design, simplicity, consistency). Gates are not a sandbox: `04` sees only the Edit/Write tools, so a file written through Bash bypasses it. Never route around a gate — if one blocks you, fix the cause or ask the user.

---

## Template Sync

This project uses `ClaudeCodeTemplate` as its base template. To pull in the latest template updates (new skills, agent improvements, hook changes):

> **Run skill:** `template-sync`

The skill updates `CLAUDE.md`, the template-owned files in `.claude/skills/` and `.claude/agents/` (listed in `.claude/.template-manifest`), `.claude/hooks/*/template/`, the dispatchers, and the template's hook entries in `.claude/settings.json`. It never touches `docs/SYSTEM.md`, `MEMORY.md`, `.claude/arch-check.sh`, project skills/agents, or your hook scripts under `.claude/hooks/*/project/`. Release notes per version are in `.claude/skills/template-sync/CHANGELOG.md`; breaking changes and manual steps in `MIGRATIONS.md` next to it.

**Update check:** at the start of each new session, `.claude/hooks/session-start/template/01-template-update-check.sh` compares line 1 of this file with the latest template on GitHub (fetched at most once a day, silent when offline). When the project is behind, it adds the release notes to the session so you can offer the sync (Session Startup step 8). It never changes the project. To turn it off, set `CLAUDE_TEMPLATE_UPDATE_CHECK=0` in the `env` of `.claude/settings.local.json`.

See `.claude/skills/template-sync/SKILL.md` for details.
