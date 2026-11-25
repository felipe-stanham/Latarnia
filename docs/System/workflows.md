# Latarnia Workflows

This document covers the main process flows and interaction patterns in Latarnia. For component architecture and lifecycle sequence diagrams, see [architecture.md](architecture.md).

## 1. Application Startup

What happens when the Latarnia main application starts and stops (the `lifespan` function in `main.py`; the code is the source of truth for the order).

```mermaid
flowchart TD
    Start([FastAPI Lifespan Start]) --> CreateDirs[Create data/ and logs/ directories]
    CreateDirs --> SetupLog[Setup logging]
    SetupLog --> CheckRedis{Redis running?}

    CheckRedis -- Yes --> CheckPg
    CheckRedis -- No --> AutoStart[Attempt to start Redis<br/>via brew/systemctl]
    AutoStart --> CheckPg[Check Postgres connectivity<br/>log warning if unreachable]

    CheckPg --> AuthDb{Postgres reachable?}
    AuthDb -- Yes --> InitAuth[Create + migrate<br/>latarnia_platform_env DB]
    AuthDb -- No --> SkipAuth[Skip auth DB init, log warning]
    InitAuth --> Secrets[Check LATARNIA_TOTP_ENC_KEY and<br/>LATARNIA_JWT_SECRET, log error if missing]
    SkipAuth --> Secrets

    Secrets --> Linger{Linux and linger disabled?}
    Linger -- Yes --> WarnLinger[WARNING: sudo loginctl enable-linger]
    Linger -- No --> Discover
    WarnLinger --> Discover

    Discover[AppManager.discover_apps<br/>scan ./apps/, see section 2] --> Orphans[Clean up orphaned Apps<br/>whose folder was deleted]
    Orphans --> Reconcile[ServiceManager.reconcile_running_units<br/>claim ports, mark surviving units RUNNING]
    Reconcile --> Loop{Next Service App<br/>with auto_start = true?}

    Loop -- Yes --> Skip{Already RUNNING, or declared<br/>Redis/Postgres unreachable?}
    Skip -- Yes --> Loop
    Skip -- No --> StartApp[pick_launcher then<br/>launcher.start_service]
    StartApp --> Loop

    Loop -- No --> Mcp[Initialize MCP Gateway and mount it,<br/>sync tools of running MCP Apps]
    Mcp --> Caddy[Generate Caddyfile include and reload Caddy<br/>failure logged, not fatal]
    Caddy --> StartSub[Start AsyncStreamConsumer<br/>for the activity feed]
    StartSub --> StartHealth[Start HealthMonitor loop]
    StartHealth --> Ready([Application Ready])

    Ready -.-> Shutdown([Shutdown Signal])
    Shutdown --> StopHealth[Stop HealthMonitor]
    StopHealth --> StopSub[Stop AsyncStreamConsumer]
    StopSub --> StopSvc[SubprocessLauncher.stop_all]
    StopSvc --> StopStreamlit[StreamlitManager.stop_all]
    StopStreamlit --> Done([Shutdown Complete])
```

Per-App systemd units have independent lifetimes, so they are not stopped at shutdown; `reconcile_running_units` re-adopts them on the next start.

## 2. App Installation and Discovery

Decision logic when the App Manager scans the `./apps/` directory (`AppManager.discover_apps`). Discovery only **registers** an App in the in-memory App Registry; ports, dependency installation and `setup_commands` happen later in `prepare_app` (`POST /api/apps/{id}/prepare`). The registry is not persisted: it is rebuilt from the `apps/` folders on every start.

```mermaid
flowchart TD
    Trigger([discover_apps called]) --> Scan[List ./apps/ subdirectories,<br/>Apps without requires first]
    Scan --> NextDir{Next directory?}

    NextDir -- None left --> Done([Return count of new Apps])

    NextDir -- Found --> HasManifest{latarnia.json exists?}
    HasManifest -- No --> Legacy{homehelper.json exists?}
    Legacy -- No --> SkipDir[Skip directory, log at DEBUG]
    SkipDir --> NextDir
    Legacy -- Yes --> Deprecated[Log deprecation warning,<br/>use it as the manifest]
    Deprecated --> Parse

    HasManifest -- Yes --> Parse[Parse and validate manifest<br/>mcp_port rejected, main_file must exist]
    Parse --> Valid{Manifest valid?}
    Valid -- No --> LogError[Log error]
    LogError --> NextDir

    Valid -- Yes --> AlreadyReg{Already in registry<br/>by folder path?}
    AlreadyReg -- Yes --> Bump{Version changed?}
    Bump -- No --> NextDir
    Bump -- Yes --> Update[Run pending migrations if DB provisioned,<br/>update manifest, status = DISCOVERED]
    Update --> NextDir

    AlreadyReg -- No --> Requires{requires satisfied?<br/>App registered, min_version met}
    Requires -- No --> LogReq[Log error, skip]
    LogReq --> NextDir

    Requires -- Yes --> HasDb{database: true?}
    HasDb -- Yes --> Prov[DB Provisioner, see section 5]
    Prov --> ProvOk{Success?}
    ProvOk -- No --> NextDir
    ProvOk -- Yes --> HasStreams
    HasDb -- No --> HasStreams{redis_streams declared?}
    HasStreams -- Yes --> Streams[Stream Manager, see section 6]
    Streams --> StreamsOk{Success?}
    StreamsOk -- No --> NextDir
    StreamsOk -- Yes --> Register
    HasStreams -- No --> Register[Register entry in the in-memory registry<br/>status = DISCOVERED, app_id from folder name]
    Register --> NextDir
```

## 3. Dashboard UI Interaction

How a user navigates the web dashboard and interacts with apps through modals.

```mermaid
sequenceDiagram
    participant User as User Browser
    participant Dash as Dashboard<br/>(FastAPI + Bootstrap)
    participant API as Latarnia API
    participant SvcApp as Service App
    participant StMgr as Streamlit Manager
    participant StApp as Streamlit Process

    User->>Dash: Open dashboard (GET /)
    Dash->>API: GET /api/apps
    Dash->>API: GET /api/system/metrics
    Dash->>API: GET /api/activity/recent
    API-->>Dash: App list, metrics, events
    Dash-->>User: Render app cards + system status

    User->>Dash: Click Refresh button
    Dash->>API: Re-fetch all data
    API-->>Dash: Updated data
    Dash-->>User: Re-render dashboard

    Note over User,StApp: Service App UI Flow

    User->>Dash: Click service app card
    Dash->>API: GET /api/apps/{id}/ui/resources
    API->>SvcApp: GET /ui
    SvcApp-->>API: ["readings", "alerts"]
    API-->>Dash: Resource list
    Dash-->>User: Open modal with resource tabs

    User->>Dash: Select resource tab
    Dash->>API: GET /api/apps/{id}/ui/{resource}
    API->>SvcApp: GET /api/{resource}
    SvcApp-->>API: Resource data (JSON)
    API-->>Dash: Rendered HTML table
    Dash-->>User: Display table in modal

    Note over User,StApp: Streamlit App UI Flow

    User->>Dash: Click Streamlit app card
    Dash->>API: POST /api/apps/{id}/streamlit/launch
    API->>StMgr: Launch or get existing instance
    StMgr->>StApp: Spawn streamlit process (if needed)
    StMgr-->>API: {url, port, ttl}
    API-->>Dash: Streamlit URL
    Dash-->>User: Open modal with iframe to Streamlit

    User->>Dash: Interact with Streamlit iframe
    Dash->>API: POST /api/apps/{id}/streamlit/touch
    Note right of StMgr: TTL timer resets on each touch

    Note over User,StApp: Service App Lifecycle Buttons (start / stop / restart)

    User->>Dash: Click start/stop/restart button on service app card
    Dash->>API: POST /api/apps/{id}/process/{action}
    API->>API: pick_launcher(app_entry)<br/>os + type → launcher
    alt Linux + service
        API->>API: ServiceManager.start/stop/restart_service
        Note right of API: systemctl --user {action} latarnia-{env}-{id}.service
    else Darwin + service
        API->>API: SubprocessLauncher.start/stop/restart_service
        Note right of API: Popen / SIGTERM
    end
    API-->>Dash: Updated app status
    Dash-->>User: Refresh card badge
```

## 4. Health Check Monitoring

How the HealthMonitor periodically checks Service App health and handles failures. Interval, timeout, `failure_threshold`, `max_failures` and `startup_grace_period` are the `HealthCheckConfig` defaults in `src/latarnia/managers/health_monitor.py`.

```mermaid
flowchart TD
    Start([Health Monitor Started]) --> Wait[Wait for check interval]
    Wait --> GetApps[Select Service Apps that are RUNNING,<br/>or ERROR with a live process,<br/>and have config.has_UI true]
    GetApps --> NextApp{Next App?}

    NextApp -- None left --> Wait

    NextApp -- Found --> CallHealth[HTTP GET /health on the App port]
    CallHealth --> Reply{Reply within timeout<br/>and HTTP 200?}

    Reply -- Yes --> ParseResp[Parse health: good / warning / error]
    ParseResp --> Ok{Status good or warning?}
    Ok -- Yes --> ResetFail[Reset failure count,<br/>recover ERROR App to RUNNING,<br/>probe the MCP server if enabled]
    ResetFail --> NextApp
    Ok -- No --> Failure

    Reply -- No --> Failure[Handle failure]
    Failure --> Grace{Within startup grace period<br/>of started_at?}
    Grace -- Yes --> Ignore[Ignore failure]
    Ignore --> NextApp
    Grace -- No --> IncFail[Increment consecutive failure count,<br/>store ERROR result]
    IncFail --> Threshold{Failures >= failure_threshold?}
    Threshold -- No --> NextApp
    Threshold -- Yes --> MarkError[Mark App status ERROR]
    MarkError --> Max{Failures >= max_failures?}
    Max -- No --> NextApp
    Max -- Yes --> Restart[ServiceManager.restart_service,<br/>reset failure count on success]
    Restart --> NextApp
```

A `warning` health status counts as success. Per-App restarts after repeated failures are done by the HealthMonitor through `ServiceManager.restart_service`; systemd's `Restart=on-failure` covers process crashes independently.

**Known limitation:** the HealthMonitor currently selects only Service Apps whose manifest sets `config.has_UI: true` (`_perform_health_checks` in `health_monitor.py`). Service Apps without a UI are not probed: no automatic restart on failed health and no MCP liveness probe. A follow-up task will fix this.

## 5. Database Provisioning and Migration

How the DB Provisioner creates a per-app Postgres database and executes pending migrations (`DbProvisioner.provision_database`). Called during App discovery when `database: true`. References cap-003 and cap-004 in P-0002. The database and role names come from `db_provisioner._generate_names` (`postgres.database_prefix` / `postgres.role_prefix` in config, with `-` in the App name mapped to `_`).

```mermaid
flowchart TD
    Start([App has database: true]) --> StoredPwd{Stored role password<br/>in data/.db_credentials?}

    StoredPwd -- Yes --> RoleThere{Role exists?}
    RoleThere -- Yes --> ReusePwd[Reuse stored password]
    RoleThere -- No --> RecreateRole[CREATE ROLE with stored password]
    StoredPwd -- No --> RoleExists{Role exists?}
    RoleExists -- No --> CreateRole[CREATE ROLE with new random password]
    RoleExists -- Yes --> UpdatePwd[ALTER ROLE, new random password]
    CreateRole --> StorePwd[Store password, mode 600]
    UpdatePwd --> StorePwd

    ReusePwd --> CheckDB{Database exists?}
    RecreateRole --> CheckDB
    StorePwd --> CheckDB

    CheckDB -- No --> CreateDB[CREATE DATABASE OWNER role<br/>REVOKE CONNECT FROM PUBLIC, GRANT CONNECT to role]
    CreateDB --> Ext
    CheckDB -- Yes --> Ext[Ensure platform-default extensions]
    Ext --> SchemaTable[CREATE TABLE IF NOT EXISTS schema_versions]

    SchemaTable --> ListFiles[List migrations/ directory,<br/>sort by numeric prefix]
    ListFiles --> QueryApplied[Query schema_versions for<br/>already-applied files]
    QueryApplied --> Pending{Pending migrations?}

    Pending -- None --> Grant
    Pending -- Yes --> RunTx[Run all pending in one transaction]
    RunTx --> SQLOk{All succeeded?}
    SQLOk -- Yes --> Record[Record each in schema_versions,<br/>COMMIT]
    Record --> Grant[GRANT privileges on tables and sequences to role]

    Grant --> BuildURL[Build connection_url for the App]
    BuildURL --> StoreInfo[Store DatabaseInfo in registry<br/>connection_url in-memory only, never in API responses]
    StoreInfo --> Done([Return ProvisioningResult success])

    SQLOk -- No --> Rollback[ROLLBACK]
    Rollback --> WasNew{Database was created<br/>in this run?}
    WasNew -- Yes --> DropDB[DROP DATABASE + DROP ROLE,<br/>delete stored password]
    WasNew -- No --> KeepDB[Keep database and role]
    DropDB --> Fail([Return failure, App NOT registered])
    KeepDB --> Fail
```

## 6. Redis Streams Setup

How the Stream Manager sets up streams and consumer groups during App discovery. Called after DB provisioning (if any). References cap-007 in P-0002.

```mermaid
flowchart TD
    Start([App has redis_streams_* declared]) --> PubLoop{Next publish stream?}

    PubLoop -- Done --> SubLoop{Next subscribe stream?}

    PubLoop -- Stream name --> CheckOwner{Stream already has<br/>another publisher?}
    CheckOwner -- Yes --> CollisionErr[Raise PublisherCollisionError<br/>registration fails]
    CheckOwner -- No --> CreateStream[Create stream if missing<br/>XADD sentinel entry then XDEL]
    CreateStream --> RecordOwner[Record stream_name → app_id<br/>in _publisher_map]
    RecordOwner --> PubLoop

    SubLoop -- Done --> StoreStreamInfo[Store StreamInfo in registry<br/>publish_streams, subscribe_streams, consumer_groups]
    StoreStreamInfo --> Done([Stream setup complete])

    SubLoop -- Stream name --> EnsureStream[Create stream if missing]
    EnsureStream --> CreateGroup[XGROUP CREATE stream app_id 0 MKSTREAM<br/>BUSYGROUP ignored]
    CreateGroup --> TrackGroup[Record group in _subscriber_groups]
    TrackGroup --> SubLoop
```

The consumer group starts at id `0`, so a new subscriber also receives entries already in the stream. On App **unregistration**, the Stream Manager calls `cleanup_app(app_id)`:
- Removes all consumer groups owned by the App from their streams.
- Releases the App's publisher ownership entries from `_publisher_map`.
- Does NOT delete streams — other consumers may still be reading from them.

## 7. MCP Gateway — Tool Discovery and Routing

How the MCP gateway aggregates tools from all MCP-enabled apps at startup and routes tool calls from external clients. References cap-006 and flow-05 in P-0002.

```mermaid
sequenceDiagram
    participant Client as MCP Client
    participant GW as MCP Gateway :8000/mcp
    participant Reg as App Registry
    participant App1 as App1 MCP Server
    participant App2 as App2 MCP Server

    Note over GW: Platform startup — build tool index

    GW->>Reg: Get all MCP-enabled apps
    Reg-->>GW: app1 (port 9001), app2 (port 9002)

    GW->>App1: MCP SSE connect + list_tools
    App1-->>GW: [tool_a, tool_b]
    GW->>App2: MCP SSE connect + list_tools
    App2-->>GW: [tool_c]

    GW->>GW: Build namespaced index<br/>app1.tool_a, app1.tool_b, app2.tool_c

    Note over Client,App2: Runtime — client connects via SSE

    Client->>GW: GET /mcp/sse (SSE connect)
    Client->>GW: initialize + list_tools
    GW-->>Client: app1.tool_a, app1.tool_b, app2.tool_c

    Client->>GW: call_tool(app1.tool_a, args)
    GW->>Reg: Is app1 healthy?
    Reg-->>GW: Yes
    GW->>App1: call_tool(tool_a, args)
    App1-->>GW: result content
    GW-->>Client: result content

    Note over Client,App2: Unhealthy app scenario

    Client->>GW: call_tool(app2.tool_c, args)
    GW->>Reg: Is app2 healthy?
    Reg-->>GW: No (health check failed)
    GW-->>Client: Error: App app2 is currently unavailable
```

## 8. MCP Tool Sync on App Lifecycle Events

How the gateway keeps the tool index in sync when apps start, stop, or undergo a version bump. References cap-006, cap-011.

```mermaid
flowchart TD
    Event([App lifecycle event]) --> Type{Event type?}

    Type -- App started --> HasMCP{"App has
    mcp_server: true?"}
    HasMCP -- No --> Done([No MCP action])
    HasMCP -- Yes --> FetchTools[list_tools from app MCP server]
    FetchTools --> GotTools{Tools returned?}
    GotTools -- No --> LogWarn[Log warning, index unchanged]
    LogWarn --> Done

    GotTools -- Yes --> HasOldTools{Prior tools
    registered?}
    HasOldTools -- No --> AddToIndex[Add namespaced tools to index]
    HasOldTools -- Yes --> CompatCheck{Backward compat OK?
    set_diff old minus new = empty?}
    CompatCheck -- No --> MarkUnhealthy["Mark app mcp_info.healthy = False.
    Return HTTP 409.
    App stopped."]
    MarkUnhealthy --> Done

    CompatCheck -- Yes --> RemoveOld[Remove old tools for app from index]
    RemoveOld --> AddToIndex
    AddToIndex --> UpdateReg["Update registry: registered_tools,
    last_tool_sync, healthy=True"]
    UpdateReg --> Done

    Type -- App stopped --> RemoveTools[Remove all tools for app from index]
    RemoveTools --> Done

    Type -- Version bump --> FetchTools
```

## 9. Redis Streams Activity Feed

How App messages on Redis Streams reach the dashboard activity feed. App-to-App messaging uses Redis Streams only (no pub/sub); the platform reads the same streams through the `AsyncStreamConsumer` in `src/latarnia/core/event_subscriber.py`.

```mermaid
sequenceDiagram
    participant AppA as App A<br/>(Publisher)
    participant Redis as Redis Streams<br/>latarnia:streams:*
    participant Cons as AsyncStreamConsumer<br/>(group latarnia-dashboard)
    participant AppB as App B<br/>(Subscriber group)
    participant Store as Redis List<br/>latarnia:events:recent
    participant WS as Dashboard WebSocket<br/>/ws/activity
    participant Dash as Dashboard API

    AppA->>Redis: XADD latarnia:streams:name {source, timestamp, data}

    par Each consumer group reads independently
        Redis->>Cons: XREADGROUP (SCAN finds all latarnia:streams:* keys)
        Redis->>AppB: XREADGROUP with its own group
    end

    Cons->>Store: RPUSH event record, LTRIM to max_events
    Cons->>WS: Broadcast message to connected clients
    Cons->>Redis: XACK

    Note over Dash,Store: Dashboard load (initial feed)
    Dash->>Store: LRANGE latarnia:events:recent
    Store-->>Dash: Recent events (filtered per user role)
```

## 10. Role-Based App Tile Filtering and forward_auth Role Injection

How the dashboard filters app tiles server-side by role and how `/auth/verify` injects per-app role headers into every forwarded request. References cap-015, cap-016, flow-03 in P-0008.

```mermaid
sequenceDiagram
    participant Browser as User Browser
    participant Caddy
    participant AV as /auth/verify
    participant RS as RoleStore
    participant DB as latarnia_platform_{env}
    participant Dash as Dashboard /api/apps

    Note over Browser,Dash: Dashboard load — server-side tile filtering

    Browser->>Caddy: GET / [Cookie: latarnia_session=abc]
    Caddy->>AV: GET /auth/verify [catch-all: no X-Latarnia-App]
    AV->>DB: Validate session hash
    AV-->>Caddy: 200 [X-Latarnia-User: uid, X-Latarnia-Is-Super: false, X-Latarnia-App-Role: none] (no App named; role is full for a Superuser, none otherwise)
    Caddy->>Dash: GET /api/apps [X-Latarnia-User: uid, X-Latarnia-Is-Super: false]
    Dash->>RS: get_role(uid, app) for each app in registry
    Note over Dash: Filter: hide tiles where role = none<br/>Superuser bypasses filter (all apps visible, role = full)
    Dash-->>Browser: App list (only apps with role != none)

    Note over Browser,DB: Protected app request — role injected into proxy headers

    Browser->>Caddy: GET /apps/crm/dashboard [Cookie: ...]
    Caddy->>AV: GET /auth/verify [X-Latarnia-App: crm (set by Caddy), Cookie: ...]
    AV->>DB: Validate session
    AV->>RS: get_role(uid, "crm")
    alt role = none
        AV-->>Caddy: 403 "No access to this app" (no X-Latarnia-* headers)
        Caddy-->>Browser: 403 (passed through, no login redirect)
    else role != none
        AV-->>Caddy: 200 [X-Latarnia-User: uid, X-Latarnia-App-Role: webUI-med, X-Latarnia-Is-Super: false]
        Caddy->>Caddy: reverse_proxy re-sets the three X-Latarnia-* headers from verify's values and deletes X-Latarnia-App
    end
    Note right of Caddy: App receives role header;<br/>may adjust content accordingly
```

Role assignment is managed via `GET /api/auth/roles`, `GET /api/auth/roles/all` (superuser only), and `POST /api/auth/roles/{app}` (superuser only). The `RoleStore` (`src/latarnia/auth/roles.py`) reads and writes the `app_roles` table; default effective role for any unassigned (user, app) pair is `none`.

## 11. JWT Machine Token Issuance and API Call

How machine clients (scripts, AI agents) obtain and use long-lived JWT tokens to call the Latarnia REST API. References cap-019, cap-020, flow-04 in P-0008.

```mermaid
sequenceDiagram
    actor Operator
    participant Dashboard as Dashboard (API Tokens modal)
    participant Auth as /api/auth/tokens
    participant MTS as MachineTokenStore
    participant DB as latarnia_platform_{env}
    actor Client as Machine Client

    Note over Operator,DB: Token creation (one-time, any logged-in user; scope capped by the caller's own roles)

    Operator->>Dashboard: POST /api/auth/tokens {label, app_scope, expires_at}
    Dashboard->>Auth: (session cookie required; non-superuser scope checked against own roles)
    Auth->>MTS: create(label, app_scope, expires_at, granted_by)
    MTS->>MTS: Sign HS256 JWT {sub, iat, jti, exp, apps, super}
    MTS->>DB: INSERT machine_tokens (token_hash=SHA256(jwt), app_scope, ...)
    MTS-->>Auth: raw JWT string
    Auth-->>Dashboard: {token: "<raw_jwt>"}
    Dashboard-->>Operator: Show token once (plaintext never stored)

    Note over Client,DB: API call with Bearer token

    Client->>Client: GET /api/apps\nAuthorization: Bearer <jwt>
    Note over Client: JWTAuthMiddleware intercepts all /api/* requests

    Client->>Client: JWTAuthMiddleware.validate_jwt(token)
    Client->>DB: SELECT machine_tokens WHERE token_hash=SHA256(jwt) AND revoked_at IS NULL
    alt JWT invalid, expired, or revoked
        Client-->>Client: 401 Unauthorized
    else Valid
        Client->>Client: Extract app_scope from JWT claims
        Client-->>Client: 200 OK (response filtered to apps in scope)
    end

    Note over Operator,DB: Token management
    Operator->>Auth: GET /api/auth/tokens  →  list active tokens (no plaintext values)
    Operator->>Auth: DELETE /api/auth/tokens/{id}  →  set revoked_at = now()
```

`LATARNIA_JWT_SECRET` (from `secrets.env`) is the HS256 signing key. Any logged-in user may create a token. A superuser may grant any role. A non-superuser may grant on each App only a role no higher than their own role on that App (order: `ROLE_VALUES` in `src/latarnia/auth/roles.py`), and only a superuser may grant `full`; otherwise the request returns 403 and no token is created.

## 12. MCP Gateway Bearer Auth Flow

How an AI agent authenticates to the MCP Gateway with a Bearer JWT and receives a scoped tool list. References cap-021, flow-06 in P-0008.

```mermaid
sequenceDiagram
    actor Agent as AI Agent (e.g. Claude Desktop)
    participant Caddy
    participant GW as MCP Gateway /mcp/sse
    participant DB as latarnia_platform_{env}
    participant AppMCP as App MCP Server

    Agent->>Caddy: GET /mcp/sse\nAuthorization: Bearer <jwt>
    Note over Caddy: /mcp/* has NO forward_auth<br/>(gateway validates the JWT internally)
    Caddy->>GW: Proxy request (Bearer header forwarded)

    GW->>GW: Validate JWT signature + expiry
    GW->>DB: SELECT machine_tokens WHERE token_hash=SHA256(jwt) AND revoked_at IS NULL
    alt Invalid or revoked
        GW-->>Agent: 401 Unauthorized
    else Valid
        GW->>GW: Extract app_scope from JWT claims (ContextVar per connection)
        Note over GW: Tool list filtered to apps the token has access to
        GW->>AppMCP: Connect to app MCP server\nX-Latarnia-App-Role: <role_from_scope>
        GW-->>Agent: SSE stream open; aggregated tool list (scoped)

        Agent->>GW: call_tool(app.tool_name, args)
        GW->>GW: Check token has access to app
        GW->>AppMCP: Forward call with X-Latarnia-App-Role header
        AppMCP-->>GW: Tool result
        GW-->>Agent: Tool result
    end
```

The MCP Gateway uses a per-connection `ContextVar` to carry the token's app scope — this isolates concurrent connections. Websocket and `/mcp` traffic passes through `JWTAuthMiddleware` without session-cookie gating; JWT is the sole credential path for machine clients on `/mcp`.

## 13. Caddy Ingress — App Request Flow

How external browser requests reach Service App web UIs via Caddy (P-0008 Scope 1). The platform's Python web proxy was removed; Caddy is now the sole reverse proxy for app traffic. References P-0008 architecture.

```mermaid
sequenceDiagram
    participant Browser as User Browser
    participant C as Caddy
    participant AV as /auth/verify<br/>(Latarnia :8000)
    participant App as Service App<br/>(assigned port)

    Note over Browser,App: Protected app page

    Browser->>C: GET /apps/crm/dashboard [Cookie: latarnia_session=abc]
    C->>AV: GET /auth/verify [X-Latarnia-App: crm (set by Caddy), Cookie: ...]
    AV-->>C: 200 [X-Latarnia-User: uid, X-Latarnia-App-Role: webUI-med, X-Latarnia-Is-Super: false]
    C->>App: GET /dashboard [X-Latarnia-User, X-Latarnia-App-Role headers]
    App-->>Browser: 200 OK HTML

    Note over Browser,App: App's own /docs is an ordinary protected route (no public bypass)

    Browser->>C: GET /apps/crm/docs [Cookie: latarnia_session=abc]
    C->>AV: GET /auth/verify [X-Latarnia-App: crm]
    AV-->>C: 200 [role headers]
    C->>App: GET /docs
    App-->>Browser: 200 OK

    Note over Browser,App: Unauthenticated request

    Browser->>C: GET /apps/crm/dashboard [no valid session]
    C->>AV: GET /auth/verify
    AV-->>C: 401 Unauthorized (no or expired session)
    C-->>Browser: 302 redirect to /auth/login?next=/apps/crm/dashboard
```

```mermaid
flowchart TD
    Request([Incoming HTTPS request]) --> RouteMatch{Route match?}

    RouteMatch -- "/auth/*" --> PublicLatarnia[Proxy to Latarnia :8000<br/>no auth check]
    RouteMatch -- "/api/* and /mcp/*" --> NoFwd[Proxy to Latarnia :8000<br/>NO forward_auth; JWTAuthMiddleware / MCP Gateway<br/>accept a Bearer JWT or a session]
    RouteMatch -- "/apps/name/public_route*<br/>(public_routes in manifest)" --> PublicApp[Strip /apps/name, strip X-Latarnia-* headers,<br/>proxy to App port, no auth]
    RouteMatch -- "/apps/name/*" --> ForwardAuth[forward_auth to /auth/verify<br/>X-Latarnia-App: name]
    RouteMatch -- "/* catch-all (dashboard, /docs)" --> ForwardAuthCatchAll[forward_auth to /auth/verify]

    ForwardAuth --> AuthOk{/auth/verify response?}
    AuthOk -- 401 --> Redirect[302 redirect to /auth/login?next=original URL]
    AuthOk -- 403 --> Deny[403 passed through to browser, no redirect]
    AuthOk -- 200 + headers --> StripPrefix[handle_path strips /apps/name prefix]
    StripPrefix --> ProxyApp[Proxy to App port with<br/>X-Latarnia-* headers re-set from verify]

    ForwardAuthCatchAll --> AuthOkCatchAll{/auth/verify response?}
    AuthOkCatchAll -- 401 --> RedirectCatchAll[302 redirect to /auth/login?next=original URL]
    AuthOkCatchAll -- 200 + headers --> ProxyLatarnia[Proxy to Latarnia :8000 with<br/>X-Latarnia-* headers]
```

## 14. API Docs Flow (T-0020)

How a logged-in user (or a Machine Token holder) reads an App's UI API and App REST API docs. The docs live in the platform's `/docs` namespace, behind login; an App's own `/docs` is never published. See `src/latarnia/web/api_docs.py`.

The **App REST API** half documents a contract that is **planned, not yet available**: the public `/rest/{app}/` path it points to is not served by Caddy (pitch I-0002 is still open), so "Try it out" there does not reach the App. The UI API half is live.

```mermaid
sequenceDiagram
    participant Browser as User Browser
    participant C as Caddy
    participant L as Latarnia :8000<br/>(API Docs router)
    participant App as Service App<br/>(assigned port)

    Browser->>C: GET /docs/apps/crm/rest [Cookie: latarnia_session]
    C->>L: forward_auth /auth/verify, then proxy (catch-all)
    L->>L: role on crm != none? (403 if not, before existence check)
    L->>App: GET /openapi.json (App's internal port)
    App-->>L: full OpenAPI schema
    L->>L: keep /rest/ paths, prune unreferenced components,<br/>servers = /rest/crm
    L-->>Browser: Swagger UI page
    Browser->>C: GET /api/docs/apps/crm/rest [session or Bearer Machine Token]
    C->>L: proxy (no forward_auth; JWTAuthMiddleware authenticates)
    L->>App: GET /openapi.json
    L-->>Browser: split OpenAPI schema (UI API: /api/ half, App REST API: /rest/ half)
```

```mermaid
flowchart TD
    Req([GET /docs/apps/name/kind or /api/docs/apps/name/kind]) --> Kind{kind is ui or rest?}
    Kind -- no --> E404[404]
    Kind -- yes --> Role{role on App is not none?}
    Role -- no --> E403[403]
    Role -- yes --> Exists{Service App registered?}
    Exists -- no --> E404
    Exists -- yes --> Fetch[Fetch App /openapi.json from its port]
    Fetch -- no port or no answer --> E503[503 App not running]
    Fetch -- ok --> Split[Keep paths under /api/ or /rest/]
    Split --> Empty{any paths left?}
    Empty -- no --> E404
    Empty -- yes --> Out[Prune components, rewrite servers,<br/>return page or schema]
```
