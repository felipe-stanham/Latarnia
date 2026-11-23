---
name: task
description: Task creation assistant. Writes a ready-to-implement docs/Tasks/T-xxxx.md — either from a pitch the `pitch` skill recommended as a Task, or from scratch for a small change or a hotfix. Enforces the Definition of Ready, checks the promotion triggers (hands off to `spec` if any fire), and commits the task to dev. Invoke before any code change that has no task or project scope yet.
effort: xhigh
---

## Role

You turn a piece of work into a task the `implementer` agent can build without guessing. Every code
change in this project goes through a task or a project scope — however small — so this skill is
the short path for work that does not need a full spec.

You do not write code, and you do not start the work. You produce one file, `docs/Tasks/T-xxxx.md`,
and commit it to `dev`.

> Planning runs best on Opus (or Fable, if you want it) at xhigh — frontmatter only covers this
> turn; follow-up turns use your session setting.

---

## Session Startup

1. **Planning checkout check.** Run `git branch --show-current` and `git rev-parse --git-dir --git-common-dir`.
   Continue only if the branch is `dev` and both paths are the same (the primary checkout, not a worktree).
   Otherwise stop and say: "Planning artifacts are committed on `dev` in the primary checkout — open a
   session there and re-run the `task` skill."
2. If a remote is configured, run `git pull --ff-only` so the next ID doesn't collide with tasks
   pushed from elsewhere.
3. Read `docs/SYSTEM.md` (subsystems — needed for the promotion triggers) and `docs/System/glossary.md`.
4. Read `docs/Tasks/INDEX.md` to determine the next sequential ID. If it doesn't exist, create it from
   `docs/templates/TASK_INDEX.template.md` and start at `T-0001`.
5. If invoked from a pitch, read that `docs/Pitches/I-xxxx.md`. Its Recommendation section should say Task.

---

## Entry Modes

### From a pitch

The pitch already has Problem, Solution, Boundaries, Risks and No-Gos. Do not re-litigate it. Translate
it into the task structure and ask only what the pitch does not answer: which files/modules change, the
pattern reference, and concrete acceptance criteria.

### From scratch

For a small, well-understood change (a bug fix, a config change, a copy edit in code, a dependency bump).
Ask focused questions — at most 5 at a time — until every Definition of Ready section can be filled.
If the user cannot state the goal in two lines, the work is not ready for a task: suggest the `pitch` skill.

### Hotfix

Only when the user explicitly says "hotfix". Same as from scratch, plus:
- Ask which branch is broken (`main` or `tst`) and set **Base** to it.
- The branch is still `task-T-xxxx-hotfix-<short-description>`, but it is created from that base.
- After it merges into the base (which the protected-branch hook asks the user to approve), the same
  branch is merged into `dev`.

---

## Steps

### 1. Check the promotion triggers

Before drafting, check the four triggers from CLAUDE.md ("Promotion: Task → Project"):
- More than 3 scopes of work?
- Touches more than one named subsystem in `docs/SYSTEM.md`?
- Requires schema or data-model changes?
- Requires a new external service, API, or integration?

If **any** fires, stop. Tell the user which trigger fired and hand off to the `spec` skill (passing the
pitch, if there is one). Do not write a task.

### 2. Draft the task

Use `docs/templates/TASK.template.md` (if the project doesn't have it yet, follow the Definition of Ready
sections in CLAUDE.md). Fill every section:

- **Goal** — 1–2 lines, glossary terms only.
- **Touches** — the files/modules expected to change, plus one existing file as the pattern reference.
  Look at the code to find them; do not guess paths.
- **Tasks** — action-oriented checklist.
- **Acceptance Criteria** — declarative `- **name:** input → expected output`, concrete enough for the
  `tester` agent to script.
- **OUT of Scope / No-gos** — stated as constraints.
- **Open Questions** — must end up empty.

New domain words go through the `glossary` skill before they appear in the task.

### 3. Definition of Ready review

Show the draft to the user. Walk through any section that is thin and resolve every open question.
Ask for explicit approval before writing the file.

### 4. Write and index

1. Write `docs/Tasks/T-xxxx.md`.
2. Add a one-line entry to `docs/Tasks/INDEX.md` under "Open":
   `- [T-xxxx](T-xxxx.md) — [Task Name] — [ ] Not Started — YYYY-MM-DD`
3. From a pitch: set the pitch's **Status** to `Promoted → T-xxxx`, and move its entry in
   `docs/Pitches/INDEX.md` to "Promoted", marked `[PROMOTED → T-xxxx]`.

### 5. Commit and push to `dev`

```bash
git add docs/
git commit -m "docs(tasks): add T-xxxx <task name>"
git push
```

### 6. Hand off

Tell the user the task is ready and how to start it:

```bash
git worktree add ../<repo>-wt/task-T-xxxx-<short-description> -b task-T-xxxx-<short-description> dev
```

(For a hotfix, replace the final `dev` with `main` or `tst`.) The Execution Loop in CLAUDE.md runs from
that worktree.

---

## What This Skill Does NOT Do

- Does not write code or start the Execution Loop.
- Does not produce a spec package — work that trips a promotion trigger goes to the `spec` skill.
- Does not shape raw ideas — that is the `pitch` skill's job.
- Does not update task status after creation — the main session marks `[IN PROGRESS]` / `[DONE]` on `dev`.
