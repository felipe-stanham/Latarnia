# Skills

Skills available in this project. Invoke with the Claude Code skill picker or by saying **"Run skill: `<name>`"**.

Planning skills (`pitch`, `task`, `spec`, `glossary`) run only on `dev` in the primary checkout and push their artifacts to `dev` as they are written.

| Skill | Trigger | Description |
|-------|---------|-------------|
| `pitch` | Starting a new idea | Pressure-tests a raw idea into a structured pitch (`docs/Pitches/I-xxxx.md`). Recommends Task, Project, or archive — `task` or `spec` performs the promotion. |
| `task` | Any code change without a task yet — small change, hotfix, or a pitch recommended as a Task | Writes a ready-to-implement `docs/Tasks/T-xxxx.md` that meets the Definition of Ready. Hands off to `spec` if a promotion trigger fires. |
| `spec` | Starting a new project, or a task that outgrew its bounds | Produces a full specification package: `spec.md`, `data_model.md`, `workflows.md`, `architecture.md`, and `P-xxxx.md` with ready scopes. |
| `glossary` | New domain term, or terminology drift | Maintains `docs/System/glossary.md`, the project's Ubiquitous Language, and challenges drift between artifacts. |
| `system-map` | Setting up or extending the system map | Generates an HTML map of the system from the code (`docs/System/map/build.py`) and wires `build.py --check` as an Architecture Invariant, so the map never goes stale. |
| `arch-check` | Declaring an architecture rule | Adds invariants to `docs/SYSTEM.md` and makes them executable in `.claude/arch-check.sh` (run before every push and in CI). |
| `deployment-process` | Deploying to any target | Deploys to a target defined in `docs/local/deployment.md`. Logs every deployment to `DEPLOYMENTS.md`. Adapt it to the project — `template-sync` installs it once and never overwrites it. |
| `template-sync` | Pulling template updates | Syncs `CLAUDE.md` and the template-owned files from the latest `ClaudeCodeTemplate`, and repairs incomplete upgrades. Never touches `docs/SYSTEM.md`, project skills/agents, or project hooks. |
| `share` | Publishing docs externally | Creates and updates shareable static HTML mini-sites from project docs. Works from `file://`. Invoke with "create a share" or "update the share at docs/shares/&lt;name&gt;". |

## Adding a project-specific skill

Create a subdirectory here with a `SKILL.md` file:

```
.claude/skills/my-skill/SKILL.md
```

Template-owned files are listed in `.claude/.template-manifest`. Anything not listed there belongs to the project: `template-sync` never overwrites or deletes it. Do not edit template-owned skills in place — copy one to a new directory and change the copy.
