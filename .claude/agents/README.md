# Agents

Subagents invoked by the main session during the **Execution Loop** (see `CLAUDE.md`). You generally don't call them directly — the main session orchestrates them and does not write code itself.

| Agent | Model · effort | When invoked | Description |
|-------|----------------|-------------|-------------|
| `implementer` | sonnet · xhigh (rework: opus) | Step 1 — for every code change | Implements one fully specified task or scope. Checks the Definition of Ready first and returns `BLOCKED: <question>` instead of guessing. Never commits. |
| `code-reviewer` | opus · xhigh | Step 2 — after the implementer | Reviews the branch diff for architecture compliance, environment safety, simplicity, consistency, and scope discipline. Reports BLOCK / WARN / OK. |
| `tester` | opus · high | Step 3 — after review passes; also regression before `dev → tst` | Generates and caches verification scripts from declarative test specs. Reports pass/fail. |
| `doc-updater` | sonnet · medium | Step 4 — after tests pass, before the commit | Updates `docs/System/` (`workflows.md`, `architecture.md`, `dataModel.md`), corrects every statement the diff made false, and regenerates the system map. Ends with `DOCS: updated …` or `DOCS: no changes needed — <why>`. |
| `docs-audit` | opus · high | Before `dev → tst`, or on demand | Read-only. Compares every checkable statement in the docs with the code and reports each difference with evidence. Ends with `AUDIT: <n> differences`. |

**Execution Loop order:** implementer → code-reviewer → tester → doc-updater → one commit (code + docs) → push → merge to `dev`.
A BLOCK from the reviewer or a FAIL from the tester sends the findings back to the `implementer` with `model: opus` — at most 2 rework cycles per task/scope in total, then the user decides.

Models are aliases (`opus`, `sonnet`), so they track the latest release. Fable is never set here — use it only when you ask for it.

## Adding a project-specific agent

Create a `.md` file in this directory following the same frontmatter format:

```markdown
---
name: my-agent
description: What it does and when to invoke it.
tools: Read, Bash, ...
model: sonnet
effort: medium
---

[agent instructions]
```

Agents not listed in `.claude/.template-manifest` belong to the project — `template-sync` never deletes or overwrites them.
