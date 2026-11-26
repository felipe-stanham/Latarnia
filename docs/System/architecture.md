# Latarnia Architecture

This document describes the overall architecture of Latarnia, a self-hosted platform for exposing Apps.

## System Overview

```mermaid
graph TB
    subgraph "External"
        Browser[Web Browser]
        MCPClient[MCP Client<br/>Claude Desktop etc.]
        User[User]
    end

    subgraph "Caddy (systemd system)"
        CaddyHTTPS[Caddy HTTPS :443<br/>PRD + TST by domain]
        CaddyTLS[TLS — Let's Encrypt<br/>self-signed for localhost]
    end

    subgraph "Raspberry Pi 5"
        subgraph "Latarnia Main Application (localhost:8000)"
            FastAPI[FastAPI Web Server<br/>Port 8000]
            AppMgr[App Manager<br/>Discovery & Registry]
            Router[LaunchRouter<br/>os + type → launcher]
            SvcMgr[Service Manager<br/>systemd --user]
            SubLaunch[SubprocessLauncher<br/>macOS fallback]
            UIMgr[UI Manager<br/>Streamlit TTL]
            SysMon[System Monitor<br/>Hardware Metrics]
            MCPGateway[MCP Gateway<br/>/mcp SSE endpoint]
            CaddyMgr[CaddyConfigManager<br/>generates Caddyfile + reload]
            AuthVerify[/auth/verify<br/>session validation + role lookup]
            AuthRoutes[/auth/* and /api/auth/*<br/>login, setup, users, roles, tokens]
            AuthDB[AuthDB<br/>platform DB init + migrations]
            AuthProvider[TOTPAuthProvider<br/>AES-GCM encrypted TOTP]
            SessionStore[SessionStore<br/>session create/validate/expire]
            UserStore[UserStore<br/>user CRUD + setup tokens]
            RoleStore[RoleStore<br/>per-app role lookup + assignment]
            JWTAuth[JWTAuth + MachineTokenStore<br/>HS256 sign/validate/revoke]
            JWTMiddleware[JWTAuthMiddleware<br/>gates /api/* — Bearer or session]
            APIDocs[API Docs router<br/>/docs/apps/... pages + schemas]
        end

        subgraph "Redis Streams"
            Redis[(Redis Streams<br/>Port 6379)]
        end

        subgraph "systemd --user (Linux, linger on)"
            UA[latarnia-{env}-app_a.service]
            UB[latarnia-{env}-app_b.service]
        end

        subgraph "Applications"
            SvcApp1[Service App 1<br/>assigned port]
            SvcApp2[Service App 2<br/>assigned port]
            StreamlitApp1[Streamlit App 1<br/>Port from Service App range]
            StreamlitApp2[Streamlit App 2<br/>Port from Service App range]
        end

        subgraph "System Services"
            FileSystem[Shared Storage<br/>/opt/latarnia/]
            CaddyFile[Caddyfile include<br/>/opt/latarnia/{env}/caddy/]
        end

        subgraph "Platform Auth DB"
            PlatformDB[(latarnia_platform_{env}<br/>Postgres)]
        end
    end

    User --> Browser
    Browser -->|HTTPS| CaddyHTTPS
    MCPClient -->|HTTPS + Bearer JWT| CaddyHTTPS

    CaddyHTTPS -->|forward_auth| AuthVerify
    CaddyHTTPS -->|/auth/* no auth| AuthRoutes
    CaddyHTTPS -->|after auth| FastAPI
    AuthVerify --> SessionStore
    AuthVerify --> UserStore
    AuthVerify --> RoleStore
    AuthRoutes --> AuthDB
    AuthRoutes --> AuthProvider
    AuthRoutes --> SessionStore
    AuthRoutes --> UserStore
    AuthRoutes --> RoleStore
    AuthRoutes --> JWTAuth
    AuthDB --> PlatformDB
    SessionStore --> PlatformDB
    UserStore --> PlatformDB
    RoleStore --> PlatformDB
    JWTAuth --> PlatformDB
    JWTMiddleware --> JWTAuth
    JWTMiddleware --> SessionStore
    CaddyHTTPS -->|after auth, handle_path strips prefix| SvcApp1
    CaddyHTTPS -->|after auth, handle_path strips prefix| SvcApp2
    APIDocs -->|GET /openapi.json on app port| SvcApp1
    FastAPI --> APIDocs
    APIDocs --> RoleStore

    FastAPI --> AppMgr
    FastAPI --> Router
    FastAPI --> UIMgr
    FastAPI --> SysMon

    Router -->|Linux + service| SvcMgr
    Router -->|Darwin + service| SubLaunch
    Router -->|any + streamlit| UIMgr

    CaddyMgr --> CaddyFile
    CaddyMgr -->|caddy reload| CaddyHTTPS

    AppMgr -->|stream setup| Redis
    SvcMgr -.systemctl --user.-> UA
    SvcMgr -.systemctl --user.-> UB
    UA -->|ExecStart venv python| SvcApp1
    UB -->|ExecStart venv python| SvcApp2
    SubLaunch -.Popen.-> SvcApp1
    UIMgr --> StreamlitApp1
    UIMgr --> StreamlitApp2

    MCPGateway -->|MCP SSE| SvcApp1
    MCPGateway -->|MCP SSE| SvcApp2

    SvcApp1 --> Redis
    SvcApp2 --> Redis
    StreamlitApp1 --> Redis
    StreamlitApp2 --> Redis

    SvcApp1 --> FileSystem
    SvcApp2 --> FileSystem
    StreamlitApp1 --> FileSystem
    StreamlitApp2 --> FileSystem
```

## Core Components

### 1. FastAPI Main Application
- **Purpose**: Central web server and API gateway
- **Port**: 8000 (configurable)
- **Responsibilities**:
  - Web dashboard serving
  - Health monitoring endpoints
  - System metrics API
  - App management API
  - Configuration management

### 2. App Manager
- **Purpose**: Application discovery and lifecycle management
- **Responsibilities**:
  - Auto-discovery of apps in `./apps/` directory
  - Manifest parsing (`latarnia.json`)
  - In-memory app registry (not persisted; rebuilt by `discover_apps()` from the `apps/` folders on every start)
  - Discovery-time provisioning of declared resources (Postgres database, Redis Streams) and `requires` checks
  - Preparation on `POST /api/apps/{id}/prepare` (`prepare_app`): Python dependency installation, `setup_commands`, port allocation from the range configured at `process_manager.port_range` (`src/latarnia/core/config.py`)

### 3. Launch Router
- **Purpose**: Stateless dispatch function (`pick_launcher`) that selects the correct launcher for each app based on `(platform.system(), manifest.type)`
- **Module**: `latarnia.managers.launcher_router`
- **Routing rules**:
  - `streamlit` type → `StreamlitManager`
  - `service` + Linux → `ServiceManager` (systemctl --user)
  - `service` + Darwin → `SubprocessLauncher` (Popen fork)
- **Used by**: lifespan auto-start loop and `/api/apps/{id}/process/{start,stop,restart}` endpoints

### 4. Service Manager
- **Purpose**: systemd --user lifecycle controller for service apps on Linux
- **Responsibilities**:
  - Per-app unit file generation (`~/.config/systemd/user/latarnia-{env}-{app}.service`)
  - `ExecStart` uses absolute venv Python (`sys.executable`); `Environment=ENV={env}` injected
  - Default `Restart=on-failure` / `RestartSec=5`; overridable via `manifest.config.restart_policy`
  - Units have **independent lifetimes** from the platform (no `PartOf=`): apps survive a platform restart, which is the desired robustness story
  - `linger_enabled()` helper — shells out to `loginctl` on Linux; startup emits `WARNING` if linger is off
  - `reconcile_running_units()` — called during `lifespan()` after discovery on Linux; finds units already `active`/`activating`, parses their `--port` and `--mcp-port` from `ExecStart`, claims those ports in `PortManager`, and marks the app `RUNNING` in the registry so the auto-start loop skips it
  - Service start/stop/restart operations via `systemctl --user`
  - Log access via `journalctl _SYSTEMD_USER_UNIT=latarnia-{env}-{app}.service` (system journal; no `--user` flag, as the Pi has no persistent user-mode journald)

### 5. Subprocess Launcher
- **Purpose**: macOS-only fallback launcher; spawns service apps as direct `Popen` children of the platform
- **Module**: `latarnia.managers.subprocess_launcher` (`SubprocessLauncher`)
- **Responsibilities**:
  - `start_service / stop_service / restart_service` (verbs harmonized with `ServiceManager`)
  - Process tracking by PID; graceful SIGTERM with force-kill fallback
  - No crash recovery (macOS dev path only — systemd restart policy not available)

### 6. UI Manager
- **Purpose**: On-demand Streamlit application management
- **Responsibilities**:
  - Streamlit process spawning
  - TTL-based idle cleanup (`default_ttl` in `src/latarnia/managers/streamlit_manager.py`; the config keys `process_manager.streamlit_port` and `process_manager.streamlit_ttl_seconds` currently have no effect)
  - Port allocation for Streamlit apps via `port_manager.allocate_port(app_id, 'streamlit')` from the Service App range (`process_manager.port_range`)
  - Modal integration with main dashboard
  - Resource monitoring and cleanup

### 7. System Monitor
- **Purpose**: Hardware and system metrics collection
- **Responsibilities**:
  - CPU usage monitoring
  - Memory utilization tracking
  - Disk space monitoring
  - Temperature sensors (Raspberry Pi specific)
  - Process metrics collection
  - Health status determination

### 8. MCP Gateway
- **Purpose**: Aggregates MCP tools from all MCP-enabled apps and exposes them to external clients through a single endpoint
- **Mount path**: `/mcp` (configurable via `MCPConfig.gateway_path`)
- **Transport**: SSE (`mcp.server.sse.SseServerTransport`) — gateway acts as MCP server to clients and MCP client to apps
- **Responsibilities**:
  - Build and maintain a namespaced tool index (`app_name.tool_name`)
  - Proxy `list_tools` responses from the in-memory index
  - Proxy `call_tool` requests to the appropriate app's MCP server (localhost on the port allocated by `PortManager.allocate_mcp_port`, held in `MCPInfo.mcp_port`)
  - Skip unhealthy apps on tool calls (return error immediately)
  - Sync tool index on app start and stop (the version-bump hook `on_app_version_bump` exists but is not called — T-0029; startup sync misses Apps not yet marked healthy — T-0028)
  - Enforce backward compatibility on version bumps (set-difference check; reject and stop app on violation)
  - Expose `GET /api/mcp/status` and `GET /api/mcp/tools` REST endpoints

### 9. DB Provisioner
- **Purpose**: Provision per-app Postgres databases for apps with `database: true` in their manifest. Apps never touch superuser; the platform handles role + DB creation, privilege grants, and migration execution.
- **Module**: `src/latarnia/managers/db_provisioner.py` (uses `core/pg_client.py` as the superuser-credential client).
- **Responsibilities**:
  - Generate per-app names: `{database_prefix}{app_name}` and `{role_prefix}{app_name}_role`.
  - Create role + database if absent; revoke `PUBLIC` connect; grant connect to the per-app role.
  - **Ensure platform-default extensions** via `CREATE EXTENSION IF NOT EXISTS <ext>` for every entry in `db_provisioner.DEFAULT_EXTENSIONS`. Idempotent — runs on both new and reused DBs so existing app DBs get backfilled at the next provisioning pass. Missing OS-level binaries log a `WARNING`, not a failure.
  - Create the `schema_versions` tracking table.
  - Run pending migrations from the app's `migrations/*.sql` directory in numeric order, in a single transaction; record file name + sha256 checksum + duration.
  - Drop database + role when a migration fails and the database was created in that run (clean slate) — but preserve them when the database already existed and on version-bump migration failure.
  - Grant `ALL PRIVILEGES ON ALL TABLES / SEQUENCES IN SCHEMA public` to the per-app role after migrations so the app can use objects created by the superuser.
  - Build the connection URL passed to apps via `--db-url env:DATABASE_URL` + `Environment=DATABASE_URL=...` in the systemd unit. The URL (`DatabaseInfo.connection_url`) carries the App role password, so it is held in memory only: `DatabaseInfo.to_dict()` omits it (no App API response includes it) and it is `repr=False` on `DatabaseInfo` and `ProvisioningResult`. Only the Service Manager and Subprocess Launcher read it, to inject `DATABASE_URL`.
- **Extensions list (`DEFAULT_EXTENSIONS`)**: a hardcoded list in `db_provisioner.py`; the source is the authority on its current contents. Adding an extension is a one-line edit; if per-app extension overrides ever become needed, refactor to manifest-driven at that point.

### 10. Secret Manager
- **Purpose**: Inject runtime secrets (API keys, tokens) into per-app process environments without leaking values to logs, unit files, or other apps. Owns the per-env master secret store and per-app filtered files (P-0006).
- **Module**: `src/latarnia/managers/secret_manager.py`. Wired into both `ServiceManager` (Linux) and `SubprocessLauncher` (Darwin) so refuse-to-start + injection work uniformly across launchers.
- **Master file**: `/opt/latarnia/{env}/secrets.env`, mode 600, operator-edited via `$EDITOR`. Dotenv format (`KEY=value` per line, `#` comments, blank lines, single-quoted values for `$`/`=`/spaces). The platform refuses to read a wider-mode file and logs a warning.
- **Per-app files**: `/opt/latarnia/{env}/secrets/{app_id}.env`, mode 600, written by `materialize()` before each `start_service`. Contains exactly the keys declared in `manifest.config.requires_secrets`. Overwritten on every launch (idempotent).
- **Linux injection**: generated systemd unit references the per-app file via `EnvironmentFile=-/opt/latarnia/{env}/secrets/{app_id}.env` (leading `-` = ignore-if-missing).
- **Darwin injection**: filtered key/value map merged into `subprocess.Popen(env=...)`; no file written.
- **Refuse-to-start gate**: runs before port allocation in both launchers. On missing key, sets `app.runtime_info.error_message = "missing required secret: <name>"`, marks app `ERROR`, returns `False`. Surfaces as `overall_status: red` via the existing P-0005 cap-005 plumbing — no new fields.
- **REST**: `GET /api/secrets` returns `{env, secrets: [{name, set_at, used_by: [app_id, ...]}]}`. **Never** returns values. Listing is read-only; rotation is `$EDITOR` + restart consuming apps.
- **Logging contract**: no method on this manager ever logs a secret value. Verified by a sentinel-value unit test.
- **Out of v1**: encryption at rest, audit log, rotation automation, CLI binary, dashboard panel, multi-line values, cross-env sharing.

### 11. Redis Streams
- **Purpose**: App-to-App messaging over Redis Streams (`latarnia:streams:*`), and the source of the dashboard activity feed
- **Responsibilities**:
  - Stream and consumer-group setup at App discovery (`StreamManager`, `src/latarnia/managers/stream_manager.py`): one publisher per stream, one consumer group per subscribing App
  - Platform consumer (`AsyncStreamConsumer`, `src/latarnia/core/event_subscriber.py`): reads every `latarnia:streams:*` stream as group `latarnia-dashboard`, keeps the latest messages in the `latarnia:events:recent` list, and pushes them to `/ws/activity`
- No pub/sub is used, and the platform does not publish lifecycle events (App started/stopped) to Redis.

### 12. Caddy Ingress
- **Purpose**: Caddy (systemd system service) is the single external HTTPS ingress and reverse proxy. The platform's own Python web proxy (`web_proxy.py`) was removed in P-0008.
- **TLS**: Let's Encrypt ACME (real domains); Caddy self-signed (localhost dev). Domain read from `{ENV}_DOMAIN` config.
- **Ports**: `:443` for both PRD and TST, each on its own domain (`{PRD_DOMAIN}` / `{TST_DOMAIN}`) and routed by SNI/Host; `:80` ACME HTTP-01 challenge (T-0017 — TST was `:8443` before it had its own domain).
- **Route structure** (generated per environment by `CaddyConfigManager`):
  - `/auth/*` — public, proxied to Latarnia `:8000`
  - `/api/*`, `/mcp/*` — proxied to Latarnia `:8000` **without** `forward_auth`; Latarnia authenticates internally (`JWTAuthMiddleware` accepts a Bearer JWT or session cookie on `/api/*`; the MCP gateway validates the Bearer JWT on `/mcp`). `forward_auth` only checks the session cookie, so it would reject machine tokens (T-0002).
  - `/apps/{name}/*` — protected via `forward_auth` to `/auth/verify`; Caddy strips the `/apps/{name}` prefix with `handle_path` before proxying to app port (Service Apps only; Streamlit Apps have no Caddy route block)
  - `/*` catch-all — protected via `forward_auth`; proxied to Latarnia `:8000` (dashboard, platform `/docs` and `/openapi.json`, API docs pages, and other browser routes). Any logged-in user can open them; the API docs router applies the per-App role check.
  - Apps' own `/docs` and `/openapi.json` are **not** published publicly (T-0020): `/apps/{name}/docs` and `/apps/{name}/openapi.json` are ordinary protected App routes, and the platform serves App API docs itself (component 14).
- **Auth headers copied**: `X-Latarnia-User`, `X-Latarnia-App-Role`, `X-Latarnia-Is-Super`. In each protected `/apps/{name}/*` block the App's `reverse_proxy` re-sets them from verify's values (so a client `Connection` header cannot strip them).
- **App identification for verify**: each per-App `forward_auth` block sends `X-Latarnia-App: {name}` to `/auth/verify`; the catch-all deletes it. Verify does not read `X-Forwarded-Uri`. `X-Latarnia-App` is a Caddy → verify header only: protected and public-route blocks delete it before the request reaches the App. Verify returns 403 ("No access to this app") when the caller's role is `none`; Caddy passes the 403 through.
- **UI API proxy**: `/api/apps/{app_id}/ui/*` endpoints enforce the same per-App access as `GET /api/apps/{id}` (session role via `RoleStore`, or Machine Token scope; 401/403) and forward the three `X-Latarnia-*` headers to the App.
- **CaddyConfigManager** (`src/latarnia/caddy/manager.py`): reads the App Registry, generates the per-environment Caddyfile include at `/opt/latarnia/{env}/caddy/latarnia.caddyfile`, then reloads Caddy with `caddy reload --config /etc/caddy/Caddyfile` so every imported per-env section is re-read together. Only when the `caddy` binary or that file is missing (single-env dev) does it fall back to `POST /load` on the Caddy admin API. Called on every App registration change.
- **Firewall**: `ufw` blocks Latarnia `:8000` and the App ports from external access — all external traffic enters through Caddy only.

### 13. Auth Components (P-0008 Scopes 2–4)

These components implement persistent authentication, authorization, and machine-token issuance. They all operate against `latarnia_platform_{env}` and are distinct from the per-app DB Provisioner path.

- **AuthDB** (`src/latarnia/auth/db.py`): Creates `latarnia_platform_{env}` at startup and applies pending platform migrations from `src/latarnia/auth/migrations/` using the same sequential runner and `schema_versions` checksum pattern as the DB Provisioner.
- **AuthProvider / TOTPAuthProvider** (`src/latarnia/auth/providers/`): `AuthProvider` is a Protocol defining `setup_credentials`, `validate`, and `get_setup_form_spec`. `TOTPAuthProvider` is the V1 implementation — generates TOTP secrets, encrypts them with AES-256-GCM (key from `LATARNIA_TOTP_ENC_KEY` in `secrets.env`), and validates TOTP codes.
- **UserStore** (`src/latarnia/auth/users.py`): User CRUD — create user with setup token, list, deactivate. The first user created via `/auth/setup` becomes the superuser.
- **SessionStore** (`src/latarnia/auth/sessions.py`): Session create/validate/expire. Cookie value is a random UUIDv4; only its SHA-256 hash is stored. Expired sessions are lazily garbage-collected at login time.
- **RoleStore** (`src/latarnia/auth/roles.py`): Per-app role lookup and assignment over the `app_roles` table. Role enum: `none | webUI-low | webUI-med | webUI-full | full`. Superuser is a user attribute (not a per-app role) and always resolves to `full`. `is_visible(user, app_name)` returns false when the user's role on the App is `none` (Superusers and unauthenticated dev access see all), controlling dashboard tile visibility. Role management API: `GET /api/auth/roles`, `GET /api/auth/roles/all` (superuser), `POST /api/auth/roles/{app}` (superuser).
- **JWTAuth / MachineTokenStore** (`src/latarnia/auth/jwt_auth.py`, `tokens.py`): Signs and validates HS256 JWTs (key: `LATARNIA_JWT_SECRET` from `secrets.env`). JWT claims: `{sub, iat, jti, exp, apps, super}` (`jti` is random per token, so identical mints never share a hash — T-0022). A `super` claim grants every App on `/api/apps` and `/api/apps/{id}`; otherwise `apps` scopes access. `MachineTokenStore` persists tokens to the `machine_tokens` table (SHA-256 of raw JWT stored, never plaintext) and supports create/list/revoke. Token management API: `POST /api/auth/tokens`, `GET /api/auth/tokens`, `DELETE /api/auth/tokens/{id}`.
- **JWTAuthMiddleware** (`src/latarnia/auth/middleware.py`): Pure-ASGI middleware that gates all `/api/*` requests. Accepts either a `Bearer <jwt>` (validated + revocation-checked against `machine_tokens`) or a session cookie. Passes `/mcp` and websocket connections through without session-cookie gating — the MCP Gateway validates its own JWT. Returns 401 on any other unauthenticated request to `/api/*`.
- **Auth Routes** (`src/latarnia/auth/routes.py`): FastAPI router mounting `/auth/*` (login, setup, logout — browser flows) and `/api/auth/*` (user management, role management, machine token management — JSON API). The `/auth/verify` endpoint is the Caddy `forward_auth` target; it validates the session hash, looks up the per-app role via `RoleStore`, and returns `X-Latarnia-User`, `X-Latarnia-App-Role`, and `X-Latarnia-Is-Super` headers.

### 14. API Docs (T-0020)
- **Purpose**: Platform-owned Swagger docs for each Service App's APIs, served from the platform's `/docs` namespace instead of the App's own URL space.
- **Module**: `src/latarnia/web/api_docs.py` (`build_api_docs_router(app_manager, role_store, session_resolver)`), included in `main.py`.
- **Behaviour**: fetches the App's internal `/openapi.json` straight from its registry port (never published by Caddy), splits it by path prefix — `/api/` is the **UI API**, `/rest/` is the **App REST API** — prunes component schemas the half does not reference (`securitySchemes` are kept so "Authorize" still works), and rewrites `servers` to `/apps/{name}` (UI API) or `/rest/{name}` (App REST API; paths re-rooted under that server URL) so "Try it out" hits the public path. The `/rest/{name}` public path is **planned, not yet served** (pitch I-0002 is still open; Caddy has no route for it), so the App REST API half documents an intended contract and "Try it out" there does not reach the App yet.
- **Routes**:

| Route | Returns | Auth path |
|---|---|---|
| `GET /docs/apps/{name}/{ui\|rest}` | Swagger UI page (HTML; error page on failure) | Session, via Caddy `/*` `forward_auth` |
| `GET /api/docs/apps/{name}/{ui\|rest}` | Split OpenAPI schema | `JWTAuthMiddleware`: session or Machine Token |
| `GET /api/docs/apps/{name}` | `{app, running, ui, rest}` — which halves exist (drives dashboard buttons) | `JWTAuthMiddleware`: session or Machine Token |

- **Access**: a role other than `none` on that App (superuser always passes; a Machine Token needs the App in its scope). Checked before App existence, so callers cannot distinguish a missing App from a forbidden one.
- **Errors**: `403` no access; `404` unknown App, unknown kind, non-Service App, or the App has no routes under that prefix; `503` App not running (no port or no answer within 3 s). The summary endpoint returns `running: false` instead of 503.
- **Dashboard**: header link "API docs" to `/docs`; running Service App tiles show "UI API" / "REST API" buttons based on the summary endpoint.
- **Reserved paths**: `public_routes` no longer reserves `/docs`; `/health` and `/openapi.json` stay reserved (see `app-specification.md`).

## Application Types

### Service Apps
```mermaid
graph LR
    subgraph "Service App"
        Main[main.py<br/>FastAPI/Flask]
        Health[&sol;health endpoint]
        UI[&sol;ui endpoint<br/>REST API]
        Logic[Business Logic]
        Data[Data Processing]
    end
    
    subgraph "Latarnia Integration"
        Manifest[latarnia.json]
        Requirements[requirements.txt]
        Setup[setup.py/commands]
    end
    
    subgraph "System Integration"
        systemd[systemd user unit<br/>latarnia-{env}-{app_id}.service]
        Redis[Redis Streams]
        Storage[/opt/latarnia/{env}/data/{app_id}/]
        Journal[journald<br/>system journal]
    end
    
    Main --> Health
    Main --> UI
    Main --> Logic
    Logic --> Data
    
    Manifest --> systemd
    Requirements --> systemd
    Setup --> systemd
    
    Main --> Redis
    Main --> Storage
    Main -->|stdout/stderr| Journal
```

### Streamlit Apps
```mermaid
graph LR
    subgraph "Streamlit App"
        App[app.py<br/>Streamlit UI]
        Components[UI Components]
        Widgets[Interactive Widgets]
        Charts[Data Visualization]
    end
    
    subgraph "Latarnia Integration"
        Manifest[latarnia.json]
        Requirements[requirements.txt]
    end
    
    subgraph "Runtime Management"
        TTL[TTL Manager<br/>idle TTL = default_ttl in streamlit_manager.py]
        Process[Process Spawning]
        Cleanup[Resource Cleanup]
    end
    
    subgraph "System Integration"
        Redis[Redis Streams]
        Storage[/opt/latarnia/{env}/data/{app_id}/]
        Logs[/opt/latarnia/{env}/logs/{app_id}-streamlit.log]
    end
    
    App --> Components
    App --> Widgets
    App --> Charts
    
    Manifest --> Process
    Requirements --> Process
    
    Process --> TTL
    TTL --> Cleanup
    
    App --> Redis
    App --> Storage
    App --> Logs
```

## Log Access Dispatch

The `/api/apps/{id}/logs` endpoint selects the log source based on `(OS, app type)`:

| OS | Type | Source |
|----|------|--------|
| Linux | service | `journalctl _SYSTEMD_USER_UNIT=latarnia-{env}-{app}.service` (system journal, no `--user`) |
| Darwin | service | SubprocessLauncher stdout-redirect file (`logs_dir/{app_id}.log`) |
| Any | streamlit | StreamlitManager stdout-redirect file (`logs_dir/{app_id}-streamlit.log`) |

Apps log to stdout/stderr only; they no longer receive a `--logs-dir` flag. The `config.logs_dir` manifest field is deprecated (kept for backward compatibility, ignored at runtime).

## Data Flow

### App Discovery Flow
```mermaid
sequenceDiagram
    participant AM as App Manager
    participant FS as File System
    participant DB as DB Provisioner
    participant SM as Stream Manager
    participant Reg as App Registry (in-memory)

    AM->>FS: Scan ./apps/ directory
    FS->>AM: Return app directories

    loop For each app directory (Apps without requires first)
        AM->>FS: Read latarnia.json (homehelper.json as deprecated fallback)
        FS->>AM: Return manifest data
        AM->>AM: Validate manifest, check requires
        opt database: true
            AM->>DB: provision_database
            DB->>AM: ProvisioningResult
        end
        opt redis_streams declared
            AM->>SM: setup_streams
            SM->>AM: StreamSetupResult
        end
        AM->>Reg: Register app (status DISCOVERED)
    end
```

Discovery does not allocate ports, install dependencies or run `setup_commands`: those happen in `prepare_app` (`POST /api/apps/{id}/prepare`). The registry is rebuilt on every platform start. See [workflows.md](workflows.md) section 2 for the decision logic.

### Service App Lifecycle
```mermaid
sequenceDiagram
    participant UI as Web Dashboard
    participant API as FastAPI
    participant Router as LaunchRouter
    participant SM as ServiceManager
    participant Sub as SubprocessLauncher
    participant systemd as systemd --user
    participant App as Service App

    UI->>API: POST /api/apps/{id}/process/start
    API->>Router: pick_launcher(app_entry)
    alt Linux + service type
        Router-->>API: ServiceManager
        API->>SM: start_service(app_id)
        SM->>systemd: generate unit file
        SM->>systemd: systemctl --user start
        systemd->>App: ExecStart (venv python)
    else Darwin + service type
        Router-->>API: SubprocessLauncher
        API->>Sub: start_service(app_id)
        Sub->>App: Popen(main_file)
    end
    API-->>UI: Return app status

    Note over App: App runs continuously

    UI->>API: POST /api/apps/{id}/process/stop
    API->>Router: pick_launcher(app_entry)
    alt Linux
        Router-->>API: ServiceManager
        API->>SM: stop_service(app_id)
        SM->>systemd: systemctl --user stop
        systemd->>App: Terminate process
    else Darwin
        Router-->>API: SubprocessLauncher
        API->>Sub: stop_service(app_id)
        Sub->>App: SIGTERM / force kill
    end
    API-->>UI: Return app status
```

### Streamlit App Lifecycle
```mermaid
sequenceDiagram
    participant UI as Web Dashboard
    participant UM as UI Manager
    participant Process as Streamlit Process
    participant TTL as TTL Manager
    
    UI->>UM: Launch Streamlit app
    UM->>Process: Spawn streamlit run
    UM->>TTL: Start TTL timer
    UM->>UI: Return app URL/modal
    
    Note over Process: User interacts with app
    
    TTL->>TTL: Timer expires
    TTL->>Process: Terminate process
    TTL->>UM: Cleanup resources
```

### Caddy Authenticated App Request Flow
```mermaid
sequenceDiagram
    participant Browser as User Browser
    participant C as Caddy
    participant AV as /auth/verify<br/>(Latarnia :8000)
    participant App as Service App<br/>(assigned port)

    Browser->>C: GET /apps/crm/dashboard [Cookie: latarnia_session=abc]
    C->>AV: GET /auth/verify [X-Latarnia-App: crm (set by Caddy), Cookie: ...]
    AV-->>C: 200 [X-Latarnia-User: uid, X-Latarnia-App-Role: webUI-med, X-Latarnia-Is-Super: false]
    C->>App: GET /dashboard [X-Latarnia-User: uid, X-Latarnia-App-Role: webUI-med]
    App-->>Browser: 200 OK HTML

    Note over Browser,App: App's own /docs is an ordinary protected route (no public bypass)

    Browser->>C: GET /apps/crm/docs [Cookie: latarnia_session=abc]
    C->>AV: GET /auth/verify [X-Latarnia-App: crm]
    AV-->>C: 200 [role headers]
    C->>App: GET /docs
    App-->>Browser: 200 OK

    Note over Browser,App: Auth failure

    Browser->>C: GET /apps/crm/dashboard [no valid session]
    C->>AV: GET /auth/verify [X-Latarnia-App: crm]
    AV-->>C: 401 Unauthorized
    C-->>Browser: 302 redirect to /auth/login?next=/apps/crm/dashboard

    Note over Browser,App: Authenticated, role none on the App

    Browser->>C: GET /apps/crm/dashboard [valid session, no role on crm]
    C->>AV: GET /auth/verify [X-Latarnia-App: crm]
    AV-->>C: 403 No access to this app
    C-->>Browser: 403 (passed through; only 401 redirects to login)
```

## Security Model

### Process Isolation
- Each app runs as separate systemd service
- Apps cannot access each other's data directly
- Shared resources through well-defined interfaces only

### File System Security
- Apps have dedicated data directories
- No cross-app file access
- Logs isolated per application
- Configuration files protected

### Network Security
- Port allocation managed centrally
- Apps communicate via Redis Streams
- No direct inter-app network connections
- Caddy is the sole external ingress; app ports and Latarnia :8000 are blocked from external access by ufw
- All external traffic to app web UIs passes through Caddy's `forward_auth` gate
- All `/api/*` requests are gated by `JWTAuthMiddleware`: accepts Bearer JWT (validated + revocation-checked) or a valid session cookie; 401 otherwise
- Machine clients (scripts, AI agents) authenticate with long-lived HS256 JWTs issued via `POST /api/auth/tokens` (any logged-in user; a non-superuser's per-App roles are capped at their own role on that App, and only a superuser may grant `full`, else 403). JWT revocation is a DB lookup on every request
- MCP Gateway requires a Bearer JWT to open the SSE session; per-connection `ContextVar` carries the token's app scope to isolate concurrent agent connections

### Resource Management
- No systemd resource limits (memory, CPU) are set on the generated App units
- TTL-based cleanup for temporary Streamlit processes
- Hardware metrics via the System Monitor (display only, no alerting)

## Deployment Architecture

### Development Environment
```
localhost (Caddy self-signed TLS, port from {ENV}_DOMAIN config)
└── forward_auth → localhost:8000 (/auth/verify — added Scope 2)
localhost:8000 (Main Dashboard + API + MCP Gateway at /mcp)
├── localhost:<allocated> (Service App REST servers — internal only, process_manager.port_range)
├── localhost:<allocated> (Service App MCP servers — internal only, process_manager.mcp_port_range)
├── localhost:<allocated> (Streamlit Apps — process_manager.port_range)
└── localhost:6379      (Redis)
```

### Production Environment (Raspberry Pi)
```
Caddy :443 PRD + TST by domain (Let's Encrypt TLS, ufw allows 80/443/22)
└── forward_auth → localhost:8000 (/auth/verify — added Scope 2)
localhost:8000 (Main Dashboard + API + MCP Gateway at /mcp — internal only)
├── Internal:<allocated> (Service App REST servers — internal only, process_manager.port_range)
├── Internal:<allocated> (Service App MCP servers — internal only, process_manager.mcp_port_range)
├── Internal:<allocated> (Streamlit Apps — process_manager.port_range)
└── Internal:6379      (Redis)

systemd system unit:
/etc/systemd/system/
└── caddy.service  (ports 80 ACME, 443 PRD + TST, 2019 admin)

systemd --user units (per-app, generated at runtime):
~/.config/systemd/user/
├── latarnia-tst-{app}.service   (TST env apps)
└── latarnia-prd-{app}.service   (PRD env apps)
Each unit: ExecStart=<venv>/bin/python, Restart=on-failure, RestartSec=5
           Environment=ENV={env}
           (No PartOf= — units have independent lifetimes from the platform)
Logs: journalctl _SYSTEMD_USER_UNIT=latarnia-{env}-{app}.service (system journal)
Prerequisite: sudo loginctl enable-linger {user}
```

### File System Layout
```
/opt/latarnia/{env}/               (env-scoped; full layout in dataModel.md "File System Structure")
├── src/                           (Platform code)
├── apps/                          (Discovered applications)
├── data/                          (Per-app data directories)
├── logs/                          (Per-app log files)
└── caddy/latarnia.caddyfile       (generated by CaddyConfigManager)

/etc/caddy/Caddyfile  (manually configured; imports the per-env includes above)
```

## Performance Considerations

### Resource Optimization
- Manual refresh pattern (no auto-updates)
- TTL-based Streamlit cleanup
- Redis Streams with a capped recent-events list

### Scalability Targets
- Support 10-20 concurrent apps
- Raspberry Pi 5 with 8GB RAM
- Minimal CPU overhead for main app
- Efficient memory usage patterns

### Monitoring Strategy
- Hardware metrics collection
- Process resource monitoring
- Redis connectivity and metrics on the dashboard
- App health check polling
