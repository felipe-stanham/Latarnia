# Skills

Skills available in this project. Invoke with the Claude Code skill picker or by saying **"Run skill: `<name>`"**.

| Skill | Trigger | Description |
|-------|---------|-------------|
| `pitch` | Starting a new idea | Pressure-tests a raw idea into a structured pitch (`docs/Pitches/I-xxxx.md`). Decides whether to promote to Task, Project, or archive. |
| `spec` | Starting a new project | Produces a full specification package: `spec.md`, `data_model.md`, `workflows.md`, `architecture.md`, and `P-xxxx.md`. |
| `deployment-process` | Deploying to any target | Deploys to a target defined in `docs/local/deployment.md`. Logs every deployment to `DEPLOYMENTS.md`. |
| `template-sync` | Pulling template updates | Syncs `CLAUDE.md`, skills, agents, and template hooks from the latest `ClaudeCodeTemplate`. Never touches `docs/SYSTEM.md` or project hooks. |

## Adding a project-specific skill

Create a subdirectory here with a `SKILL.md` file:

```
.claude/skills/my-skill/SKILL.md
```

Project skills live alongside template skills but are **not** overwritten by `template-sync`.
To protect a skill from sync, place it in a directory not present in the template.
