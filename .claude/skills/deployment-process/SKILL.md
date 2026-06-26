---
name: deployment-process
description: Deployment procedure for this project. Reads targets from docs/local/deployment.md (gitignored) and secrets from .deploy-secrets (gitignored). Logs deployments to DEPLOYMENTS.md. Invoke when deploying to a target.
---

# Rules

- **This skill itself is committed** — it documents the *process*. The host-specific details it consults are not.
- Before any deployment:
  1. Read `docs/local/deployment.md` and confirm the requested target exists. If the file is missing, stop and ask the user to provide it (use template at `docs/templates/deployment.template.md` if present).
  2. Read `.deploy-secrets` and confirm credentials exist for that target+environment. If missing, stop and ask the user to provide them (use template at `docs/templates/deploy-secrets.template.md`).
- **Never deploy to a `prd` target without explicit user confirmation.**
- Warn if regression tests have not been run before deploying to `prd` (delegate to the `tester` agent — see project Testing rules).
- Deploy to `tst` targets from the `tst` branch; deploy to `prd` targets from `main` only.
- Verify the current branch matches the target environment before proceeding. If it does not, stop and refuse.
- Log every deployment action — target, environment, timestamp, commit hash, deployer — in `DEPLOYMENTS.md`.
- If adding a new deploy target, add it to `docs/local/deployment.md` (procedural notes) AND `.deploy-secrets` (credentials). Both files are gitignored.

# Where Things Live

- `docs/local/deployment.md` — gitignored. The list of targets, environments each target serves, restart sequences, host-specific quirks, and any procedural notes that vary per project.
- `.deploy-secrets` — gitignored. Per-target credentials (SSH hosts, keys, ports, paths). One section per `target.environment` combination.
- This skill (`SKILL.md`) — committed. Process rules and the deployment procedure outline below.
- `DEPLOYMENTS.md` at the repo root — committed. The deployment log.

# Procedure

The concrete steps depend on the target and live in `docs/local/deployment.md`. The general shape is:

1. Confirm target, environment, and current branch are consistent.
2. Run regression tests (`tester` agent) if deploying to `tst` or `prd`.
3. Pull the target's host, path, and credentials from `.deploy-secrets`.
4. Follow the target-specific procedure documented in `docs/local/deployment.md`.
5. Verify the deployed service is healthy (smoke test specified in `docs/local/deployment.md`).
6. **Last step:** Append an entry to `DEPLOYMENTS.md` with target, environment, timestamp, commit hash, and outcome.

# After Incidents or Procedural Changes

If a deployment fails or the procedure needed adjustment, update **both**:
- `docs/local/deployment.md` — the host-specific procedural notes.
- This `SKILL.md` — only if the failure exposed a gap in the general rules (e.g., a missing safety check that should apply to every project).
