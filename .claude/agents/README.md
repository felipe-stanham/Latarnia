# Agents

Subagents invoked automatically by Claude Code during standard workflows. You generally don't call them directly — they are delegated to at the right step.

| Agent | When invoked | Description |
|-------|-------------|-------------|
| `code-reviewer` | After scope/task implementation, before commit | Reviews code for architecture compliance, environment safety, simplicity, and consistency. Reports BLOCK / WARN / OK. |
| `doc-updater` | After marking work `[DONE]` | Updates `docs/System/` diagrams (`workflows.md`, `architecture.md`, `dataModel.md`) and the relevant `INDEX.md` files. Only touches files that actually changed. |
| `tester` | After code review (scope acceptance) and before branch promotion (regression) | Generates and caches verification scripts from declarative test specs. Reports pass/fail. |

## Adding a project-specific agent

Create a `.md` file in this directory following the same frontmatter format:

```markdown
---
name: my-agent
description: What it does and when to invoke it.
tools: Read, Bash, ...
model: sonnet
---

[agent instructions]
```
