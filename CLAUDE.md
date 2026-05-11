# Claude Code — Project Instructions

## Session Startup

**ALWAYS do this before anything else, regardless of task complexity:**
1. Read `MEMORY.md` if it exists.
2. Read `docs/SYSTEM.md` if it exists.
3. Confirm with one line: "Loaded MEMORY.md ✓ / docs/SYSTEM.md ✓"
4. Run `git branch --show-current`. If the result is `main` or `tst`, **STOP immediately** and output: "WARNING: current branch is `{branch}` — this is a protected branch. No commits or file edits until you confirm this is a critical hotfix." Do not perform any write operation until the user explicitly authorizes work on the protected branch.

## System Context

- `docs/SYSTEM.md` is the always-loaded system index. It describes the existing system at a high level without requiring all project files to be read.
- `docs/SYSTEM.md` must stay under ~150 lines. Detail belongs in individual P-xxxx.md files.
- `docs/SYSTEM.md` contains:
  - A brief description of what the system does and its main components
  - Key architectural decisions that must be respected across all projects
  - A list of completed and active projects with one-line summaries and links to their `P-xxxx.md`
  - Cross-project constraints that apply to every session
  - Deployment targets and environments (see Deployment section below)
- Never load individual project files unless the user specifies which project to work on.
- When a project's final scope is marked `[DONE]`, update the project's entry in `docs/SYSTEM.md` to reflect its completed status.
- If creating a new SYSTEM.md, use template at `docs/templates/SYSTEM.template.md`.

## Git Ignore

Every project must have a `.gitignore` that excludes at minimum:
- `.env` — contains environment credentials
- `.deploy-secrets` — contains deployment credentials
- Verify these are present before the first commit. If `.gitignore` does not exist, create one.

---

## General

- Plans are produced externally and arrive as `docs/Projects/P-xxxx.md`. Do not modify the plan — if something in it is wrong or unclear, stop and ask.
- I will indicate in which project we will be working on by specifying the corresponding file.
- Do NOT read all the files in `docs/Projects/` folder unless instructed to.
- Commit after each completed task (not mid-implementation). Use clear, descriptive commit messages.
- When blocked or at a decision point with no clear answer, stop and ask rather than guessing.
- Prefer the simplest solution that works. Do not create abstractions unless there are at least two concrete use cases today.

## Memory

- `MEMORY.md` is the index for persistent learnings. Auto memory (Claude Code's built-in system) is disabled for this project.
- Never solve the same problem twice — if you're re-discovering something, it belongs in memory.

### Structure

- `MEMORY.md` — lightweight index. Each entry is a one-line description. Simple learnings (one or two sentences) live inline here.
- `memory/` — detailed memory files. When an entry needs more context, explanation, or examples, create a file in `memory/` and link it from the index.

### Format

```
## Index

- **env-pip-flag:** pip on this system requires `--break-system-packages` flag.
- **api-auth-retry:** Auth token refresh logic and retry pattern. → [memory/api-auth-retry.md](memory/api-auth-retry.md)
- **deploy-homeserver-quirks:** SSH and restart sequence for homeserver. → [memory/deploy-homeserver-quirks.md](memory/deploy-homeserver-quirks.md)
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

- Environment is selected via `ENV` variable in `.env` (values: `dev`, `prod`). Default is `dev`.
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

- Use the approriate skill when deploying.
- Secrets are in `.deploy-secrets` (gitignored, never committed).
- After any deployment incident or procedural change, update the corresponding skill to reflect what actually works.
- Never deploy to `prd` without explicit user confirmation.

---

## Logging

- Use Python `logging` module (or the language-appropriate equivalent). Never use `print()` for operational output.
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
      └── dev (integration — all scope work merges here)
           ├── scope-P-XXXX-1-<short-description>
           ├── scope-P-XXXX-2-<short-description>
           └── scope-P-XXXX-N-<short-description>
```

- **`main`** — Production-ready code. Only receives merges from `tst` after explicit user approval. Maps to `prd` deployment targets.
- **`tst`** — Testing/staging branch. Receives merges from `dev` after all regression tests pass. Used to deploy to `tst` targets for validation.
- **`dev`** — Integration branch for active project work. All scope branches are created from and merged back into `dev`.
- **`scope-P-XXXX-N-<short-description>`** — One branch per scope, branched from `dev`.

### Rules

- Scope branches are created from `dev`. Never branch off another scope branch.
- One agent per branch when working in parallel. Use `isolation: worktree` in agent definitions or `claude --worktree <name>` to run parallel sessions in isolated git worktrees. Do not manually clone the repo into separate folders.
- **Never commit directly to `main` or `tst`.** Before any `git commit`, verify the current branch. If it is `main` or `tst`, stop and refuse — explain that the change must go to `dev` (or a scope branch) and be promoted via the standard flow.
- Commit to `dev` only for trivial cross-scope fixes.
- **Hotfix exception (rare):** Only bypass the promotion flow if the user explicitly uses the word "hotfix" and the change cannot wait for a normal promotion cycle. In that case: (a) acknowledge the exception, (b) apply it to `main` or `tst` as directed, (c) immediately backport to `dev` in the same session. Note the exception in the commit message.

### Promotion Flow

1. **Scope → `dev`:** After code review passes, merge the scope branch into `dev`. Delete the scope branch.
2. **`dev` → `tst`:** When the project is complete (all scopes `[DONE]`), run regression tests on `dev`. If all pass, merge `dev` into `tst` and deploy to `tst` targets for validation.
3. **`tst` → `main`:** After I review and approve on `tst`, merge `tst` into `main`. Deploy to `prd` targets.

---

## Agents 

### Usage

- **Code review:** After completing a scope's implementation, delegate review to `@code-reviewer` before committing.
- **Testing:** After code review passes, delegate test execution to `@tester`. Only mark a scope `[DONE]` if the tester reports all pass.
- **Documentation:** After a scope is marked `[DONE]`, delegate documentation updates to `@doc-updater`.

### Creating New Agents

If a project requires a specialized agent (e.g., a database migration agent or a performance profiler), create a new `.md` file in `.claude/agents/` following the same frontmatter format. See the existing agents for examples.

---

## Planning

For any non-trivial task, plan before coding:
1. Explore relevant files, patterns, and existing implementations.
2. Read architecture docs and constraints.
3. If a spec package exists for the project (e.g., `spec.md`, `workflows.md`, `data_model.md`, `architecture.md` in the same folder as `P-xxxx.md`), read them before proposing an approach.
4. Propose an approach and identify all files to be modified.
5. Wait for approval before writing any code.

---

## CodeReview

After finishing a task and before commit, delegate the review to the `code-reviewer` agent. The reviewer checks for:
- Adherence to the project's architecture principles (from `SYSTEM.md`)
- Correct environment handling (no hardcoded credentials, proper ENV checks)
- Code simplicity (no unnecessary abstractions)
- Consistency with existing patterns in the codebase

Do not commit until the review passes. If the reviewer flags issues, fix them and re-review.

---

## Testing

### Rules

- **Tests must be executed, not reviewed.** Reading code and confirming it "looks correct" is not testing. No scope can be marked `[DONE]` based on code review alone.
- **Always test against `dev` environment.** Before running any test, verify `ENV != prod`. If it is, stop and warn the user.
- Tests that create, modify, or delete external resources must use dev/sandbox credentials only.

### Declarative Tests

Tests are defined declaratively in `TESTS.md` and in each scope's acceptance criteria — not as pre-written test scripts. Each entry describes **what to verify** and **what the expected result is**. The `tester` agent handles execution: it generates throwaway verification scripts, runs them, and reports pass/fail.

- `TESTS.md` is the only test artifact that is maintained. There is no `tests/` directory. If creating a new `TESTS.md`, use the template at `docs/Templates/TESTS.template.md`.
- Test descriptions must be specific enough for the `tester` agent to generate verification without guessing. Include concrete inputs and expected outputs.
- Format: `- **test_name:** [What to do] → [Expected result]`

### Scope Testing

After completing a scope and passing code review, delegate to the `tester` agent. It tests the scope's acceptance criteria. All must pass before marking the scope `[DONE]`.

### Regression Tests

`TESTS.md` is the curated regression test registry — critical-path tests only. When a scope is marked `[DONE]`, add its qualifying acceptance criteria to `TESTS.md`.

**When to run regression tests:**
- Before merging `dev` into `tst` (i.e., after all scopes are complete).
- Before deploying to any target.
- Delegate to the `tester` agent. If any fail, do not merge or deploy.

---

## Progress Tracking

- `P-xxxx.md` is the single source of truth for progress. Keep it current:
- Mark tasks `[x]` as they are completed.
- Update Scope status (`[ ]` → `[IN PROGRESS]` → `[DONE]`) after each commit.
- Never mark a Scope `[DONE]` unless its tests have been **executed and passed** (not just reviewed).

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
- For every schema change, create a migration and rollback script in `docs/System/migrations/`, named `YYYYMMDD_short_description.sql`.
