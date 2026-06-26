# Memory

## Index

- **test-before-deploy:** Always run unit tests and local dev verification before deploying to TST. → [memory/testing-before-deploy.md](memory/testing-before-deploy.md)
- **log-deployments:** After every deployment, add a row to `DEPLOYMENTS.md` (date, target, branch, commit, notes). Do not skip.
- **claude-md-agnostic:** CLAUDE.md is a reusable template. Project-specific rules go in `docs/SYSTEM.md`, learnings go here.
- **example-apps-are-fixtures:** `examples/` is the source of truth for example apps (committed). `apps/` is gitignored. Platform changes must update `example_full_app` to exercise the new feature.
- **mcp-server-config:** MCP servers go in `.mcp.json`, not `settings.json`. User runs `claude-dor` profile. → [memory/mcp-server-config.md](memory/mcp-server-config.md)
- **scope-branch-naming:** Branch naming uses dashes not slashes: `scope-P-XXXX-N-description` (not `scope/`).
- **run-local-dev:** `cd /Users/felipestanham/Desktop/MyProjects/Latarnia && ENV=dev .venv/bin/python -m uvicorn latarnia.main:app --host 0.0.0.0 --port 8000 --app-dir src`
- **pi-remote-reverts:** The Pi's `/opt/latarnia/tst` git remote keeps reverting to an old repo name. Fix with: `ssh -i ~/.ssh/homeserver felipe@192.168.68.100 "cd /opt/latarnia/tst && git remote set-url origin https://github.com/felipe-stanham/Latarnia.git"`. Happens because rsync doesn't touch `.git/config`, so the stale remote survives deploys.
- **deploy-via-github-actions:** Deployment to `tst` and `prd` is handled by GitHub Actions, not manual SSH or the deployment-process skill. Push the branch and let the action run.
- **caddy-include-owner:** The per-env Caddy dir `/opt/latarnia/{env}/caddy/` and its `latarnia.caddyfile` must be owned by `felipe` (the service user), not root. Creating the placeholder with `sudo touch` leaves it root-owned and the platform fails Caddy config generation with `[Errno 13] Permission denied`. After creating it, `sudo chown -R felipe:felipe /opt/latarnia/{env}/caddy`.
