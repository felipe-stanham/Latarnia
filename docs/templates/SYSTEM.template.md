# System: [System Name]

> **Setup:** Fill in every section. Delete these instruction lines once done.
> Keep this file under ~150 lines. Detail belongs in P/T/I files and their index files.
> Do NOT list individual projects, tasks, or pitches here — those live in INDEX.md files.
> Do NOT inline deployment targets or credentials — this file is public-repo safe.

## What This System Does

[2–4 sentences describing the system's purpose and main components]

## Stack

- **Language:** [e.g., Python 3.12, TypeScript 5.x]
- **Framework:** [e.g., FastAPI, Next.js, None]
- **Database:** [e.g., PostgreSQL 16, SQLite, None]
- **Infrastructure:** [e.g., Docker on homeserver, Railway, bare metal]

## Architecture Principles

- [Key decision that must be respected, e.g., "API-first: all features exposed via REST before UI"]
- [Another constraint, e.g., "Single Postgres database — no secondary datastores"]

## Cross-Project Constraints

- [Constraint that applies to every project, e.g., "All auth uses JWT via auth-service"]

## Architecture Invariants

Rules the code must never break, and what enforces each one. Checks run from
`.claude/arch-check.sh` (before every push, and in CI). Use the `arch-check` skill to add one.

| # | Invariant | Enforced by | Mechanism |
|---|-----------|-------------|-----------|
| 1 | [e.g., "domain/ never imports from infrastructure/"] | [e.g., import-linter contract `domain-independent`] | `.claude/arch-check.sh` |
<!-- A row with an empty "Enforced by" is a wish, not an invariant. Delete this table if the project has none yet. -->
<!-- With a system map (`system-map` skill), add: | n | The system map matches the code | `python3 docs/System/map/build.py --check` | `.claude/arch-check.sh` | -->

## Subsystems / Components

- [Named subsystem 1] — [one-line description]
- [Named subsystem 2] — [one-line description]
<!-- This list is what the "touches multiple subsystems" promotion trigger checks against. Keep names stable. -->

## System Docs

- [System map](System/map/index.html) — generated from the code, open in a browser ([Markdown](System/map.md)). Set up with the `system-map` skill; delete this line until then.
- [Architecture](System/architecture.md) · [Workflows](System/workflows.md) · [Data model](System/dataModel.md) · [Glossary](System/glossary.md)
<!-- Point to the source or the map for anything the code defines (versions, lists, allowed values) — do not copy it here. -->

## Logging

- **Framework:** [e.g., Python `logging`, Node.js `winston`]
- **Format:** `YYYY-MM-DD HH:MM:SS [LEVEL] module: message`
- **dev level:** DEBUG | **prod level:** WARNING

## Work Artifact Indexes

- Pitches: [docs/Pitches/INDEX.md](Pitches/INDEX.md)
- Tasks: [docs/Tasks/INDEX.md](Tasks/INDEX.md)
- Projects: [docs/Projects/INDEX.md](Projects/INDEX.md)

## Deployment

- Targets and procedures: see `docs/local/deployment.md` (gitignored — not committed).
- Secrets: see `.deploy-secrets` (gitignored — not committed).
- Do NOT inline target details, hostnames, or credentials here.

## Current State

[1–3 sentences on where the project is right now — what's working, what's in progress, what's next.]
