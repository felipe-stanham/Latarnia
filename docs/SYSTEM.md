# System: Latarnia

## What This System Does
Latarnia is a unified home automation platform for Raspberry Pi 5 (8GB RAM) that manages multiple independent applications through a single web dashboard. It provides auto-discovery, lifecycle management (via systemd), health monitoring, and a Redis-based message bus for inter-app communication. Apps can be either long-running **Service Apps** (FastAPI with REST APIs) or on-demand **Streamlit Apps** (with TTL-based cleanup).

## Architecture Principles
- **Manual refresh pattern**: No auto-updates to reduce Pi resource usage
- **App independence**: Each app is fully self-contained with its own dependencies, data directory, and logs
- **Centralized shared resources**: Data (`data/`) and logs (`logs/`) directories are shared but organized per-app
- **Modal UI strategy**: Both Streamlit apps and service app UIs render in modals on the dashboard
- **JSON-based persistence**: Config, registry, and app data stored as JSON files (no database)
- **Redis message bus**: All inter-app communication goes through Redis pub/sub
- **systemd integration**: Service apps managed as systemd units for reliability

## Cross-Project Constraints
- Target hardware: Raspberry Pi 5 with 8GB RAM running Raspberry Pi OS (Debian-based)
- Tech stack: Python 3.9+, FastAPI, Bootstrap 5, Redis, Postgres (with `pgvector` enabled cluster-wide as a platform-default extension)
- Port ranges: Main app on 8000, service apps on 8100-8199, MCP servers on 9001-9099, Streamlit apps on 8501+
- Environment port isolation (homeserver multi-env):

| Resource | TST Range | PRD Range |
|----------|-----------|-----------|
| REST API ports | 8100–8149 | 8150–8199 |
| MCP ports | 9001–9049 | 9050–9099 |

- Production deployment path: `/opt/latarnia/`
- All apps must provide a `latarnia.json` manifest and `requirements.txt`
- App specification details in `docs/System/app-specification.md`

## Projects
| ID      | Name            | Status      | Summary                                              |
|---------|-----------------|-------------|------------------------------------------------------|
| P-0001  | Latarnia Core | [DONE]      | Full platform: core infra, app/service/UI management, dashboard, deployment |
| P-0002  | Latarnia        | [DONE]      | Platform rename + evolved manifests, Postgres, MCP gateway, Redis Streams, web UI proxy |
| P-0003  | Dynamic MCP Port Allocation | [DONE] | Runtime allocation of MCP ports from configured range |
| P-0004  | Env-Scoped Services | [DONE]      | Env-scope per-app systemd units + bootstrap docs for main platform units |
| P-0005  | Activate Systemd Per-App | [DONE] | LaunchRouter dispatches Linux→ServiceManager, Darwin→SubprocessLauncher; per-app units use venv Python, Restart=on-failure, ENV= (no PartOf= — independent lifetimes); startup reconciliation claims ports for already-running units; linger warning on startup; `/api/apps` reports combined systemd+`/health` status (green/yellow/red/grey); logs via journald on Linux. |
| P-0006  | Secret Manager  | [DONE] | Per-env master `secrets.env` (operator-edited, mode 600) → SecretManager filters per-app to declared `requires_secrets` → systemd `EnvironmentFile=-` (Linux) / Popen `env=` (Darwin). Refuse-to-start when missing; `GET /api/secrets` listing (no values); zero secret values in any log. |
| P-0007  | LiteLLM Gateway | [CANCELLED] | Single shared `latarnia-litellm-{env}.service` (systemd, like Redis); `LiteLLMProvisioner` provisions per-app API keys and injects `LITELLM_BASE_URL`/`LITELLM_API_KEY` into app environments; model gate blocks startup if declared models are unavailable. Cancelled — no concrete need at current scale. |
| P-0008  | Caddy + Auth    | [DONE] | Replace `web_proxy.py` with Caddy (TLS, forward_auth); TOTP login (no passwords); per-app role model (none/webUI-low/webUI-med/webUI-full/full); JWT machine tokens for API/MCP; `X-Latarnia-App-Role` header injection; `latarnia_platform_{env}` Postgres DB for auth state. |
| P-0009  | App Lifecycle Cleanup | [DONE] | Orphan detection on discovery (auto-stop+unlink unit for deleted app folders); full-teardown DELETE endpoint; Delete App button in dashboard detail modal. |
| P-0010  | Auth Follow-ups & Authz Hardening | [ ] Not Started | P-0008 follow-ups before prd: root→/dashboard redirect; post-login return-to-URL + open-redirect hardening; Superuser-only platform restart & logs (API 403 + UI hide); activity feed default-deny filtered to `full`-role apps + `/ws/activity` Superuser-only; user hard-delete (migration 006 `granted_by` SET NULL; deactivate moves to POST …/deactivate) + reactivate + re-issue TOTP setup; machine-token revocation on deactivate/re-issue. |

## Testing Tools

| Tool        | Config location  | Purpose                                                |
|-------------|------------------|--------------------------------------------------------|
| pytest      | `tests/unit/`    | Unit tests with mocks — run via `python3 -m pytest tests/ -v --tb=short --no-cov` |
| Playwright MCP | `.mcp.json`  | Browser-level testing for dashboards and web UIs — available as `playwright` MCP server |
| latarnia-tst MCP | `.mcp.json` | SSE connection to TST environment — interact with deployed app tools |

MCP servers are configured in `.mcp.json` at the project root.

### Integration Test Fixtures

`example_full_app` and `example_companion` (in `examples/`) are the integration test fixtures for the platform. They exercise every platform feature: Postgres DB with migrations, MCP server with tools, Redis Streams pub/sub, web UI proxy, and app dependencies. Any change to a platform feature must be accompanied by a corresponding update to `example_full_app` that exercises that feature.

> **Source of truth:** `examples/` is committed to git. `apps/` is gitignored and populated from `examples/` during deployment. All changes to example apps must be made in `examples/`, never in `apps/`.

To run integration tests locally, copy examples to `apps/`:
```
cp -r examples/example_full_app apps/
cp -r examples/example_companion apps/
```

## Deployment Targets
| Target      | Environments | Description                        |
|-------------|--------------|------------------------------------|
| local       | dev          | Developer workstation (macOS)      |
| homeserver  | dev, tst, prd| Raspberry Pi 5 — self-hosted multi-environment |

## Direction of Travel — Future V2 (candidate P-0006, not scheduled)

**Premise (recorded 2026-04-24):** Latarnia currently re-implements several patterns that mature tools already provide — process supervision, log aggregation, reverse proxying, health polling. At today's scale (1–2 apps) this is harmless. At 10+ apps the duplication starts to sting. P-0005 activates systemd per-app as an incremental step in the right direction. A potential V2 goes further: **thin the platform down to the parts that are actually Latarnia-specific** (MCP gateway, manifest-driven provisioning, Redis Streams coordination), and delegate the rest (lifecycle → systemd, logs → journald, reverse proxy → nginx/Caddy).

This section captures the V2 framing so it survives across sessions. Do **not** schedule V2 work until P-0005 has landed and been lived with for a while.

### What a V2 would change — and what it wouldn't

| Question | P-0005 answer (systemd per-app only) | Candidate V2 answer (full restructure) |
|---|---|---|
| **1. nginx required?** | No. Keep `web_proxy.py`. | Yes — Caddy (preferred for auto-TLS) or nginx. Platform writes per-app site configs from the manifest and reloads the proxy on app registration. Caddy simpler for a single-host home setup. |
| **2. Dashboard — what changes?** | User-facing: nothing. Card layout, badges, Web UI modal, start/stop buttons identical. Only the backend status query source changes. | User-facing: still the same dashboard with the same cards and modals. Backend: dashboard becomes a thin view over `systemctl` state + app `/health` + manifest metadata. Less Python code in the dashboard service, more of it is display of external state. Web UI modal still proxies via Caddy to the app's port. |
| **3. Health — app vs systemd** | Two signals merged: systemd `is-active` (process alive) + app `/health` (working correctly). Combination rules in `P-0005/workflows.md#flow-03`. Apps keep their existing `/health` contract. | Same model. Apps continue to own their `/health` endpoint — only the app knows about upstream failures, degraded mode, etc. systemd tells the platform "process alive"; `/health` tells the platform "app working". The platform merges. V2 doesn't change this contract. |
| **4. Impact on already-developed apps** | Minimal to zero. Same CLI flags, same `/health`, same manifest, same SIGTERM handling. | Still minimal. The manifest may gain optional fields (e.g., declarative proxy routes, static asset paths, resource limits) but existing fields keep working. The big win is apps stop needing to worry about how they're launched or where their logs go. |
| **5. Streamlit apps** | Stay on subprocess (TTL lifecycle doesn't match systemd services). | Stay on subprocess, possibly via transient `systemd-run --user --unit=... --timer` for nicer ops parity, but this is optional. Streamlit's on-demand model is fundamentally different and shouldn't be forced into the long-running-service mold. |

### What V2 would actually replace

- **Supervision:** `health_monitor.py`'s restart logic (currently absent or ad-hoc) → `Restart=on-failure` in systemd units. Already delivered by P-0005.
- **Logging:** per-app file logging in `logs/{app}/` → journald (queryable with `journalctl -u latarnia-{env}-{app}`). Largely delivered by P-0005.
- **Reverse proxy:** `web_proxy.py` (custom async HTTP proxy with forwarded-headers logic) → Caddy site config per app, generated from the manifest. Deleted code: `web_proxy.py`, related tests, the `_build_forwarded_headers` helper.
- **Two launcher paths:** the `ServiceManager` + `SubprocessLauncher` split remains (macOS dev still needs a fallback), but `/api/apps/{id}/process/*` can be fully deprecated in favor of `/api/services/{id}/*`.
- **Parallel REST APIs:** collapse `/api/apps/{id}/process/*` and `/api/services/{id}/*` into one canonical lifecycle API.

### What V2 would NOT change

- The MCP gateway (`mcp_gateway.py`). Still aggregates per-app MCP servers into one SSE endpoint. This is the genuinely novel part of Latarnia and the main reason the platform exists.
- Manifest-driven provisioning: `db_provisioner.py`, Redis Streams registration (`stream_manager.py`), port allocation (`port_manager.py`). These are Latarnia-specific and stay.
- The dashboard UI itself. Backend wiring thins; the visible product does not.
- Auto-discovery via `latarnia.json`. The whole drop-a-folder developer experience stays.

### Signals that would trigger scheduling V2

Any one of these is enough to justify opening a P-0006 spec:

- You want TLS on the dashboard or any app UI (then: Caddy is the cheapest way).
- You need routing rules (path rewrites, auth headers, rate limits) that the Python proxy doesn't cleanly support.
- You hit 15+ apps and the platform's own Python process starts showing load from proxying.
- You want apps to survive a platform restart (already delivered by P-0005 Scope 4: `PartOf=` removed, units have independent lifetimes).
- A second host appears and you need cross-host routing.

If none of those are true a year from now, V2 probably isn't worth it. Leave this section as a record of the thinking and move on.
