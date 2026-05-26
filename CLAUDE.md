<!-- TEMPLATE VERSION: 0.2.0 -->
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
6. Run `git branch --show-current`. If the result is `main` or `tst`, **STOP immediately** and output: "WARNING: current branch is `{branch}` — this is a protected branch. No commits or file edits until you confirm this is a critical hotfix." Do not perform any write operation until the user explicitly authorizes work on the protected branch.

## System Context

- `docs/SYSTEM.md` is the always-loaded system index. It describes the existing system at a high level without requiring all project, task, or pitch files to be read.
- `docs/SYSTEM.md` must stay under ~150 lines. Detail belongs in individual P-xxxx.md, T-xxxx.md, or I-xxxx.md files. Lists of those artifacts live in their own index files (see below), not in docs/SYSTEM.md.
- `docs/SYSTEM.md` contains:
  - A brief description of what the system does and its main components
  - Key architectural decisions that must be respected across all projects
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

---

## General

- Work artifacts follow a maturity ladder: **Pitch (`I-xxxx.md`) → Task (`T-xxxx.md`) → Project (`P-xxxx.md`)**. See the "Work Artifacts" section below for details.
- When coding, do not modify the spec — if something in it is wrong or unclear, stop and ask.
- Do NOT work on a project, task, or pitch you have not been instructed to implement. I will name the specific file.
- Do NOT read all the files in `docs/Projects/`, `docs/Tasks/`, or `docs/Pitches/` unless instructed to. Use the corresponding `INDEX.md` if you need to scan what's open.
- Commit after each completed task (not mid-implementation). Use clear, descriptive commit messages.
- When blocked or at a decision point with no clear answer, stop and ask rather than guessing.
- Prefer the simplest solution that works. Do not create abstractions unless there are at least two concrete use cases today.

---

## Work Artifacts

There are three kinds of work artifact, ordered by maturity:

1. **Pitch — `docs/Pitches/I-xxxx.md`.** A half-formed idea being shaped. Use the `pitch` skill to iterate on a problem statement, constraints, success criteria, and out-of-scope items before deciding whether the idea is real. Pitches can sit indefinitely; they cost nothing.
2. **Task — `docs/Tasks/T-xxxx.md`.** A bounded ad-hoc piece of work that does not justify the full project ceremony. One scope, one branch, no separate spec package. Created either directly (small, well-understood change) or from a promoted pitch.
3. **Project — `docs/Projects/P-xxxx.md`** plus a `docs/Projects/P-xxxx/` folder containing `spec.md`, `workflows.md`, `data_model.md`, `architecture.md`. Multi-scope work with cross-cutting impact. Created either directly (when the pitch already shows it's a project) or by promoting a task that has outgrown its bounds.

### Pitches

- A pitch is started by invoking the `pitch` skill with a working title. The skill runs a back-and-forth refinement: clarify the problem, list constraints, define success criteria, identify what's explicitly out of scope.
- Output is `docs/Pitches/I-xxxx.md` with the refined writeup. Add a one-line entry to `docs/Pitches/INDEX.md`.
- A pitch ends in one of three ways: promoted to a Task, promoted to a Project, or archived (mark as `[ARCHIVED]` in the index with a one-line reason).

### Promotion: Task → Project

Promote a task to a project (and stop work on the task) when **any** of the following becomes true:

- The work naturally decomposes into more than 3 scopes.
- It touches more than one named subsystem/component in docs/SYSTEM.md.
- It requires schema or data-model changes (i.e., any change to `dataModel.md`).
- It requires a new external service, API, or integration.

When promoting: create `P-xxxx.md` and the spec package, link the originating `T-xxxx.md` from the project's spec, mark the task `[PROMOTED → P-xxxx]` in `docs/Tasks/INDEX.md`, and stop further work on the task file. Do not delete the task — it is the historical record of how the project started.

### Promotion: Pitch → Task or Project

The `pitch` skill applies the same four triggers above at the end of refinement. If none fire, it produces a Task skeleton; if any fire, it produces a Project skeleton with the spec package stubbed.

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
      └── dev (integration — all scope and task work merges here)
           ├── scope-P-XXXX-1-<short-description>
           ├── scope-P-XXXX-N-<short-description>
           └── task-T-XXXX-<short-description>
```

- **`main`** — Production-ready code. Only receives merges from `tst` after explicit user approval. Maps to `prd` deployment targets.
- **`tst`** — Testing/staging branch. Receives merges from `dev` after all regression tests pass. Used to deploy to `tst` targets for validation.
- **`dev`** — Integration branch for active project and task work. All scope and task branches are created from and merged back into `dev`.
- **`scope-P-XXXX-N-<short-description>`** — One branch per scope of a project, branched from `dev`.
- **`task-T-XXXX-<short-description>`** — One branch per ad-hoc task, branched from `dev`. If the task is promoted to a project mid-flight, rename or replace the branch with `scope-P-YYYY-1-<...>` before continuing.

### Rules

- Scope and task branches are created from `dev`. Never branch off another scope or task branch.
- **Parallel sessions** — multiple Claude Code sessions (human-operated or agent-operated) can work simultaneously, one per scope branch. Each session must run in its own git worktree to avoid conflicts. Use `git worktree add <path> <branch>` to create an isolated working directory per scope. Use `isolation: worktree` in agent definitions for sub-agents. Never manually clone the repo into separate folders.
- **Never commit directly to `main` or `tst`.** Before any `git commit`, verify the current branch. If it is `main` or `tst`, stop and refuse — explain that the change must go to `dev` (or a scope branch) and be promoted via the standard flow.
- Commit to `dev` only for trivial cross-scope fixes.
- **Hotfix exception (rare):** Only bypass the promotion flow if the user explicitly uses the word "hotfix" and the change cannot wait for a normal promotion cycle. In that case: (a) acknowledge the exception, (b) apply it to `main` or `tst` as directed, (c) immediately backport to `dev` in the same session. Note the exception in the commit message.

### Promotion Flow

1. **Scope → `dev`:** After code review passes, merge the scope branch into `dev`. Delete the scope branch.
2. **`dev` → `tst`:** When the project is complete (all scopes `[DONE]`), run regression tests on `dev`. If all pass, merge `dev` into `tst` and deploy to `tst` targets for validation.
3. **`tst` → `main`:** After I review and approve on `tst`, merge `tst` into `main`. Deploy to `prd` targets.

---

## Planning

Always plan before coding:
- If working on a **Pitch**, invoke the `pitch` skill — do not write code from a pitch; pitches must be promoted to a Task or Project first.
- If working on a **Project** `P-xxxx.md`, read the spec package (`spec.md`, `workflows.md`, `data_model.md`, `architecture.md`) in the corresponding `P-xxxx/` folder — these contain the plan.
- If working on a **Task** `T-xxxx.md`, read the task file directly. Before starting, re-check the four promotion triggers in "Work Artifacts" — if any now apply, stop and promote to a project instead of expanding the task.
- For a brand-new Ad-Hoc task, create `docs/Tasks/T-xxxx.md` with the plan and add an entry to `docs/Tasks/INDEX.md`.

Make sure to follow the latest documentation when coding:
1. Read `architecture.md`, `dataModel.md` and `workflows.md` from the `docs/System/` folder.
2. Explore relevant files, patterns, and existing implementations.


---

## CodeReview

After finishing a task and before commit, delegate the review to the `code-reviewer` agent. The reviewer checks for:
- Adherence to the project's architecture principles (from `docs/SYSTEM.md`)
- Correct environment handling (no hardcoded credentials, proper ENV checks)
- Code simplicity (no unnecessary abstractions)
- Consistency with existing patterns in the codebase

Do not commit until the review passes. If the reviewer flags issues, fix them and re-review.

---

## Testing

### Rules

- **Tests must be executed, not reviewed.** Reading code and confirming it "looks correct" is not testing. No scope or task can be marked `[DONE]` based on code review alone.
- **Always test against `dev` environment.** Before running any test, verify `ENV != prod`. If it is, stop and warn the user.
- Tests that create, modify, or delete external resources must use dev/sandbox credentials only.

### Declarative Tests

Tests are defined declaratively — the human-maintained source of truth is the natural-language description, not the generated script. Each entry describes **what to verify**, the **concrete input(s)**, and the **expected output**. The `tester` agent generates a verification script from this spec, runs it, and reports pass/fail.

- **Scope acceptance criteria** live in the `P-xxxx.md` / `T-xxxx.md` file itself.
- **Regression tests** live in `TESTS.md` at the repo root. If creating a new `TESTS.md`, use the template at `docs/templates/TESTS.template.md`.
- Test descriptions must be specific enough for the `tester` agent to generate verification without guessing. Always include concrete sample inputs and the expected output, not just a verb.
- Format:
  ```
  - **test_name:** [What to do, with concrete input] → [Expected result, with concrete output]
  ```

### Cached Scripts

To stop wasting tokens regenerating identical verification scripts on every run:

- The `tester` agent caches generated scripts under `tests/cache/<test_name>.<ext>` alongside a `tests/cache/<test_name>.hash` file containing the SHA-256 of the test's declarative spec line.
- On each run, the agent recomputes the hash of the current declarative spec. If it matches the cached hash, **reuse the cached script** instead of regenerating. If it differs (the spec was edited) or no cache exists, regenerate and update the hash.
- `tests/cache/` is committed — it is the project's accumulated test logic, not throwaway output. It is the only thing inside `tests/` that exists; there is no hand-written test code.
- If a cached script fails and the spec is unchanged, treat it as a real regression first. Only regenerate the script if you can demonstrate the script itself is broken (e.g., the API it calls was renamed), and note this in the commit message.

### Scope Testing

After completing a scope and passing code review, delegate to the `tester` agent. It runs the scope's acceptance criteria (using the cache rules above). All must pass before marking the scope `[DONE]`.

### Regression Tests

`TESTS.md` is the curated regression test registry — critical-path tests only. When a scope is marked `[DONE]`, promote any of its acceptance criteria that protect critical paths into `TESTS.md`. Keep the registry small and meaningful; do not dump every scope test in.

**When to run regression tests:**
- Before merging `dev` into `tst` (i.e., after all scopes are complete).
- Before deploying to any target.
- Delegate to the `tester` agent. If any fail, do not merge or deploy.

---

## Progress Tracking

- `P-xxxx.md`, `T-xxxx.md`, and `I-xxxx.md` are the single source of truth for the status of their work item. Keep them current.
- Mark sub-items `[x]` as they are completed.
- Update Scope status (`[ ]` → `[IN PROGRESS]` → `[DONE]`) after each commit.
- Never mark a Scope `[DONE]` unless its tests have been **executed and passed** (not just reviewed).
- When a P/T/I file's top-level status changes (e.g., a project finishes, a task is promoted, a pitch is archived), update its one-line entry in the corresponding `INDEX.md` in the same commit. The indexes are a derived view but must not drift from the files they list.

---

## Documentation

- Document all APIs as part of the task that implements them. REST APIs must use OpenAPI.
- At the end of each scope, delegate documentation updates to the `doc-updater` agent. The following files must be kept up to date:
  - `workflows.md`
    - A set of Mermaid diagrams covering the main workflows and interactions:
    - `flowchart` for process flows and decision logic
    - `sequenceDiagram` for system/component interactions and API calls
  - `architecture.md` Mermaid diagrams covering:
    - High-level component architecture
    - Deployment topology
    - External system interactions
    - Data flow between components
  - `dataModel.md`: Mermaid `erDiagram` and/or `classDiagram` representing the data model.
    - Use `classDiagram` for objects/classes model.
    - Use `erDiagram` for databases.
    - Include field types and key relationships
- `dataModel.md` always reflects the **current** schema. Migrations are project-level implementation artifacts — if a project requires them, manage them within the project's own directory structure, not in `docs/System/`.

---

## Template Sync

This project uses `ClaudeCodeTemplate` as its base template. To pull in the latest template updates (new skills, agent improvements, hook changes):

> **Run skill:** `template-sync`

The skill updates `CLAUDE.md`, `.claude/skills/`, `.claude/agents/`, and `.claude/hooks/*/template/` in place, without touching `docs/SYSTEM.md`, `MEMORY.md`, or your project-specific hook scripts under `.claude/hooks/*/project/`.

See `.claude/skills/template-sync/SKILL.md` for details.
