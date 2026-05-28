# Glossary — Ubiquitous Language

This file is the single source of truth for domain vocabulary across this project. Every pitch, spec, doc, and code identifier must use the canonical terms defined here. If a concept does not yet have an entry, it is not yet part of the Ubiquitous Language — invoke the `glossary` skill to add it before using the term in artifacts.

---

## Domain Terms

### App

**Definition:** The fundamental deployable unit on the Latarnia platform. An App is a self-contained folder containing a manifest, entry point, and optional migrations, discoverable by the platform via `latarnia.json`. Every App is either a Service App or a Streamlit App.
**Not to be confused with:** The Latarnia platform process itself (prefer "the platform" in speech and docs; "the main app" is informal and ambiguous).

---

### App Registry

**Definition:** The in-memory store of all discovered Apps and their runtime state. Rebuilt on every platform start via discovery and Reconciliation; not persisted to disk between restarts. Each record is an App Registry Entry.
**Not to be confused with:** The `registry/` directory that previously stored JSON persistence — no on-disk registry exists today.

---

### Health Check

**Definition:** An HTTP GET probe sent by the platform's Health Monitor to an App's `/health` endpoint, which must return one of `good`, `warning`, or `error`. Service Apps must implement this endpoint; the platform uses the result to determine overall app status and MCP tool availability.

---

### Manifest

**Definition:** The `latarnia.json` file at the root of every App folder. Declares the App's name, type, version, capabilities (database, MCP, Redis Streams, secrets), and lifecycle configuration. The platform discovers, validates, and registers Apps based solely on this file.

---

### Migration

**Definition:** A SQL file in an App's `migrations/` directory, named with a numeric prefix (e.g., `001_initial.sql`). The DB Provisioner executes pending migrations against the App's Postgres database in numeric order during discovery. Each migration runs exactly once and is tracked by checksum in `schema_versions`.

---

### Redis Stream

**Definition:** A guaranteed-delivery, ordered message channel between Apps, backed by Redis Streams (`XADD` / `XREADGROUP`). Each stream has exactly one publisher App; multiple Apps may subscribe via their own consumer groups, provisioned by the Stream Manager.
**Not to be confused with:** Message Bus, which uses Redis pub/sub and provides no delivery guarantee or persistence.

---

### Secret

**Definition:** A runtime credential (API key, token, etc.) declared by an App in its manifest under `requires_secrets`. The platform reads secrets from the per-env master file (`secrets.env`, operator-maintained, mode 600) and injects them into the App's process environment at launch. The platform refuses to start an App if any declared secret is absent from the master file.

---

### Service App

**Definition:** An App of type `"service"` in its manifest. Runs as a long-running process — managed via a systemd user unit on Linux, or as a Popen child on macOS dev. Must implement `GET /health`. Survives platform restarts when managed by systemd.
**Not to be confused with:** Streamlit App.

---

### Streamlit App

**Definition:** An App of type `"streamlit"` in its manifest. Started on-demand when a user opens its UI; automatically terminated after a configurable TTL. Always has a UI. Does not support systemd lifecycle or crash recovery.
**Not to be confused with:** Service App.

---

## System Terms

### App Manager

**Definition:** The platform component responsible for scanning the `apps/` directory, parsing Manifests, installing Python dependencies, allocating ports, and populating the App Registry on startup and manual refresh.

---

### DB Provisioner

**Definition:** The platform component that creates a per-App Postgres database and role, enables platform-default extensions (currently `vector`), and executes pending Migrations during App discovery. Tears down the database on initial provisioning failure (clean-slate). Maps to `db_provisioner.py`.

---

### Launch Router

**Definition:** A stateless dispatch function (`pick_launcher`) that selects the correct launcher for an App based on OS and manifest type: `service` + Linux → Service Manager; `service` + Darwin → Subprocess Launcher; `streamlit` → UI Manager.
**Not to be confused with:** The individual launchers themselves. The Launch Router picks; the launchers act.

---

### MCP Gateway

**Definition:** The platform's single SSE endpoint at `/mcp` that aggregates MCP tools from all running MCP-enabled Apps into a unified, namespaced Tool Index. External AI clients (e.g., Claude Desktop) connect here — not to individual App MCP servers. Maps to `mcp_gateway.py`.
**Not to be confused with:** An App's own per-app MCP server, which runs on a separately allocated port and is consumed by the Gateway.

---

### Message Bus

**Definition:** The Redis pub/sub channel set (`latarnia:events:*`) used for platform-level status events (app_started, app_stopped, health_check, etc.). Provides no delivery guarantee or message persistence.
**Not to be confused with:** Redis Stream, which is used for durable, guaranteed-delivery App-to-App messaging.

---

### Reconciliation

**Definition:** The startup process in which the Service Manager scans for already-running `latarnia-{env}-*.service` systemd units, parses their port assignments from `ExecStart`, claims those ports in the Port Manager, and marks the corresponding Apps as `RUNNING` in the App Registry. Prevents double-launching Apps that survived a platform restart.

---

### Secret Manager

**Definition:** The platform component that reads the per-env master `secrets.env`, builds a per-App filtered view containing only declared secrets, injects those values at launch (via `EnvironmentFile` on Linux, `Popen env=` on Darwin), and enforces the refuse-to-start gate when a declared secret is missing. Maps to `secret_manager.py`.

---

### Service Manager

**Definition:** The platform component that manages Service App lifecycle on Linux via `systemctl --user`. Generates per-App systemd unit files, handles start/stop/restart operations, and performs Reconciliation at startup. Maps to `service_manager.py`.
**Not to be confused with:** Subprocess Launcher (the macOS dev fallback).

---

### Stream Manager

**Definition:** The platform component that provisions Redis Streams and consumer groups for Apps during discovery, enforces the one-publisher-per-stream rule, and cleans up streams on App unregistration. Maps to `stream_manager.py`.

---

### Subprocess Launcher

**Definition:** The macOS-only fallback launcher that spawns Service Apps as direct `Popen` children of the platform process. Provides no crash recovery. Used only in the `dev` environment on Darwin. Maps to `SubprocessLauncher` in `subprocess_launcher.py`.
**Not to be confused with:** Service Manager (the Linux systemd launcher).
**Aliases (deprecated):** MacOSProcessManager — renamed in P-0005 Scope 2.

---

### Tool Index

**Definition:** The in-memory map maintained by the MCP Gateway, keyed by `"{app_name}.{tool_name}"`, recording each tool's originating App, MCP port, and schema. Rebuilt from running Apps on platform startup; not persisted to disk.

---

### UI Manager

**Definition:** The platform component that manages Streamlit App lifecycle: spawning processes on demand, enforcing TTL-based cleanup (default 300 s), and tracking active instances. Maps to `streamlit_manager.py` (or equivalent).

---

## Deprecated

~~MacOSProcessManager~~ — replaced by [Subprocess Launcher](#subprocess-launcher) in P-0005 Scope 2.
