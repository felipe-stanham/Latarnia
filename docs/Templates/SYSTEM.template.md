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

## Subsystems / Components

- [Named subsystem 1] — [one-line description]
- [Named subsystem 2] — [one-line description]
<!-- This list is what the "touches multiple subsystems" promotion trigger checks against. Keep names stable. -->

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
