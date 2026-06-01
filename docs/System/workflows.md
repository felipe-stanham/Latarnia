# Latarnia Workflows

This document covers the main process flows and interaction patterns in Latarnia. For component architecture and lifecycle sequence diagrams, see [architecture.md](architecture.md).

## 1. Application Startup

What happens when the Latarnia main application starts (the `lifespan` function in `main.py`).

```mermaid
flowchart TD
    Start([FastAPI Lifespan Start]) --> CreateDirs[Create data/ and logs/ directories]
    CreateDirs --> SetupLog[Setup logging]
    SetupLog --> CheckRedis{Redis running?}

    CheckRedis -- Yes --> Discover[Scan ./apps/ directory]
    CheckRedis -- No --> AutoStart[Attempt to start Redis<br/>via brew/systemctl]
    AutoStart --> RedisOk{Redis started?}
    RedisOk -- Yes --> Discover
    RedisOk -- No --> LogWarn[Log error, continue without Redis]
    LogWarn --> Discover

    Discover --> LingerCheck{Linux?}
    LingerCheck -- Yes --> CheckLinger{linger_enabled?}
    CheckLinger -- No --> WarnLinger[WARNING: linger disabled<br/>sudo loginctl enable-linger]
    CheckLinger -- Yes --> Reconcile[ServiceManager.reconcile_running_units<br/>find active/activating latarnia-env-*.service units]
    WarnLinger --> Reconcile
    Reconcile --> ReconcileLoop{Next active unit?}
    ReconcileLoop -- Found --> ParseUnit[Parse --port and --mcp-port<br/>from ExecStart line]
    ParseUnit --> ClaimPorts[claim_port / claim_mcp_port<br/>in PortManager]
    ClaimPorts --> MarkRunning[Mark app RUNNING in registry<br/>populate runtime_info.assigned_port<br/>and mcp_info.mcp_port]
    MarkRunning --> ReconcileLoop
    ReconcileLoop -- Done --> Loop
    LingerCheck -- No --> Loop

    Loop{More apps<br/>to check?}
    Loop -- Yes --> AutoCheck{Service app with<br/>auto_start = true?}
    AutoCheck -- Yes --> AlreadyRunning{Already RUNNING<br/>from reconcile?}
    AlreadyRunning -- Yes --> Loop
    AlreadyRunning -- No --> PickLauncher[pick_launcher<br/>os + type → launcher]
    PickLauncher --> StartApp[launcher.start_service]
    AutoCheck -- No --> Loop
    StartApp --> Loop
    Loop -- No --> StartSub[Start Redis Event Subscriber<br/>psubscribe latarnia:events:*]
    StartSub --> Ready([Application Ready])

    Ready -.-> Shutdown([Shutdown Signal])
    Shutdown --> StopSub[Stop Event Subscriber]
    StopSub --> StopServices[Stop all managed service apps]
    StopServices --> StopStreamlit[Stop all Streamlit apps]
    StopStreamlit --> Done([Shutdown Complete])
```

## 2. App Installation and Discovery

Decision logic when the App Manager scans the `./apps/` directory and processes each app folder.

```mermaid
flowchart TD
    Trigger([Discover Apps called]) --> Scan[Scan ./apps/ directory<br/>for subdirectories]
    Scan --> NextDir{Next directory?}

    NextDir -- None left --> Persist[Persist registry to disk]
    Persist --> Done([Return discovered count])

    NextDir -- Found --> HasManifest{latarnia.json<br/>exists?}
    HasManifest -- No --> SkipDir[Skip directory, log warning]
    SkipDir --> NextDir

    HasManifest -- Yes --> Parse[Parse manifest JSON]
    Parse --> Valid{Manifest valid?<br/>Required fields present?}
    Valid -- No --> LogError[Log validation error]
    LogError --> NextDir

    Valid -- Yes --> AlreadyReg{Already in<br/>registry?}
    AlreadyReg -- Yes --> NextDir

    AlreadyReg -- No --> AllocPort[Allocate port from<br/>8100-8199 range]
    AllocPort --> PortOk{Port available?}
    PortOk -- No --> LogPortErr[Log port allocation error]
    LogPortErr --> NextDir

    PortOk -- Yes --> InstallDeps[Install requirements.txt<br/>via pip]
    InstallDeps --> RunSetup[Run setup_commands<br/>from manifest]
    RunSetup --> Register[Register app in registry<br/>with assigned port]
    Register --> PublishEvent[Publish app_discovered<br/>event to Redis]
    PublishEvent --> NextDir
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

How the HealthMonitor periodically checks service app health and tracks failures.

```mermaid
flowchart TD
    Start([Health Monitor Started]) --> Wait[Wait for check interval<br/>default: 30 seconds]
    Wait --> GetApps[Get all running<br/>service apps from registry]
    GetApps --> NextApp{Next running<br/>service app?}

    NextApp -- None left --> Wait

    NextApp -- Found --> CallHealth[HTTP GET /health<br/>on app port]
    CallHealth --> Timeout{Response within<br/>timeout?}

    Timeout -- No --> IncFail[Increment consecutive<br/>failure count]
    Timeout -- Yes --> ParseResp[Parse health response<br/>good / warning / error]

    ParseResp --> StatusGood{Status = good?}
    StatusGood -- Yes --> ResetFail[Reset failure count]
    ResetFail --> StoreResult[Store HealthCheckResult<br/>with response time]
    StoreResult --> NextApp

    StatusGood -- No --> StoreWarn[Store result with<br/>warning or error status]
    StoreWarn --> NextApp

    IncFail --> ThresholdCheck{Failures >= threshold?}
    ThresholdCheck -- No --> StoreTimeout[Store result as UNKNOWN]
    StoreTimeout --> NextApp
    ThresholdCheck -- Yes --> MarkError[Mark app health as ERROR]
    MarkError --> NextApp
```

## 5. Database Provisioning and Migration

How the DB Provisioner creates a per-app Postgres database and executes pending migrations. Called during app discovery when `database: true`. References cap-003 and cap-004 in P-0002.

```mermaid
flowchart TD
    Start([App has database: true]) --> CheckRole{Role exists?}

    CheckRole -- No --> CreateRole[CREATE ROLE latarnia_{app}_role WITH LOGIN PASSWORD]
    CheckRole -- Yes --> UpdatePwd[ALTER ROLE — rotate password]
    CreateRole --> CheckDB{Database exists?}
    UpdatePwd --> CheckDB

    CheckDB -- No --> CreateDB[CREATE DATABASE latarnia_{app} OWNER role]
    CreateDB --> Revoke[REVOKE CONNECT FROM PUBLIC]
    Revoke --> Grant[GRANT CONNECT TO role]
    Grant --> SchemaTable[CREATE TABLE IF NOT EXISTS schema_versions]
    CheckDB -- Yes --> SchemaTable

    SchemaTable --> ListFiles[List migrations/ directory,<br/>sort by numeric prefix]
    ListFiles --> QueryApplied[Query schema_versions for<br/>already-applied files]
    QueryApplied --> Pending{Pending migrations?}

    Pending -- None --> BuildURL[Build connection_url for app]
    BuildURL --> StoreInfo[Store DatabaseInfo in registry<br/>database_name, role_name, connection_url, applied_migrations]
    StoreInfo --> Done([Return ProvisioningResult success])

    Pending -- Yes --> RunTx[BEGIN transaction on app DB]
    RunTx --> NextMig{Next pending migration?}

    NextMig -- Done --> CommitTx[COMMIT transaction]
    CommitTx --> BuildURL

    NextMig -- Found --> ExecSQL[Execute migration SQL]
    ExecSQL --> SQLOk{SQL succeeded?}
    SQLOk -- Yes --> RecordMig[INSERT into schema_versions<br/>file, number, checksum, duration_ms]
    RecordMig --> NextMig

    SQLOk -- No --> Rollback[ROLLBACK transaction]
    Rollback --> DropDB[DROP DATABASE + DROP ROLE<br/>clean-slate teardown]
    DropDB --> Fail([Return ProvisioningResult failure<br/>app NOT started])
```

## 6. Redis Streams Setup

How the Stream Manager sets up streams and consumer groups during app discovery. Called after DB provisioning (if any). References cap-007 in P-0002.

```mermaid
flowchart TD
    Start([App has redis_streams_* declared]) --> PubLoop{Next publish stream?}

    PubLoop -- Done --> SubLoop{Next subscribe stream?}

    PubLoop -- Stream name --> CheckOwner{Stream already<br/>has a publisher?}
    CheckOwner -- Yes --> CollisionErr[Raise PublisherCollisionError<br/>registration fails]
    CheckOwner -- No --> CreateStream[XGROUP CREATE stream MKSTREAM<br/>create stream + delete temp group]
    CreateStream --> RecordOwner[Record stream_name → app_id<br/>in _publisher_map]
    RecordOwner --> PubLoop

    SubLoop -- Done --> StoreStreamInfo[Store StreamInfo in registry<br/>publish_streams, subscribe_streams, consumer_groups]
    StoreStreamInfo --> Done([Stream setup complete])

    SubLoop -- Stream name --> EnsureStream{Stream exists?}
    EnsureStream -- No --> CreateSubStream[Create stream via MKSTREAM]
    EnsureStream -- Yes --> CreateGroup
    CreateSubStream --> CreateGroup[XGROUP CREATE stream app_id $ MKSTREAM]
    CreateGroup --> TrackGroup[Record group in _subscriber_groups]
    TrackGroup --> SubLoop
```

On app **unregistration**, the Stream Manager calls `cleanup_app_streams(app_id)`:
- Removes all consumer groups owned by the app from their streams.
- Releases the app's publisher ownership entries from `_publisher_map`.
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

## 9. Redis Event Pub/Sub Flow

How apps publish events through Redis and how the Latarnia event subscriber captures them for the dashboard activity feed.

```mermaid
sequenceDiagram
    participant AppA as App A<br/>(Publisher)
    participant Redis as Redis<br/>(Message Bus)
    participant Sub as Latarnia<br/>Event Subscriber
    participant Store as Redis List<br/>latarnia:events:recent
    participant AppB as App B<br/>(Subscriber)
    participant Dash as Dashboard API

    Note over Sub: Background thread<br/>psubscribe latarnia:events:*

    AppA->>Redis: PUBLISH latarnia:events:motion.detected<br/>{source, event_type, timestamp, data}

    par Fan-out to all subscribers
        Redis->>Sub: Deliver message to Latarnia subscriber
        Redis->>AppB: Deliver message to App B subscriber
    end

    Sub->>Sub: Parse JSON event
    Sub->>Store: RPUSH event to recent events list
    Sub->>Store: LTRIM to max_events (default 100)

    AppB->>AppB: Handle event in<br/>subscriber callback

    Note over Dash,Store: Later, when user refreshes dashboard

    Dash->>Store: LRANGE latarnia:events:recent
    Store-->>Dash: Recent events list
    Dash->>Dash: Format timestamps,<br/>extract messages
```

## 10. Caddy Ingress — App Request Flow

How external browser requests reach Service App web UIs via Caddy (P-0008 Scope 1). The platform's Python web proxy was removed; Caddy is now the sole reverse proxy for app traffic. References P-0008 architecture.

```mermaid
sequenceDiagram
    participant Browser as User Browser
    participant C as Caddy
    participant AV as /auth/verify<br/>(Latarnia :8000)
    participant App as Service App<br/>:810x

    Note over Browser,App: Protected app page

    Browser->>C: GET /apps/crm/dashboard [Cookie: latarnia_session=abc]
    C->>AV: GET /auth/verify [X-Forwarded-Uri: /apps/crm/dashboard, Cookie: ...]
    AV-->>C: 200 [X-Latarnia-User: uid, X-Latarnia-App-Role: webUI-med, X-Latarnia-Is-Super: false]
    C->>App: GET /dashboard [X-Latarnia-User, X-Latarnia-App-Role headers]
    App-->>Browser: 200 OK HTML

    Note over Browser,App: Public Swagger (no auth check)

    Browser->>C: GET /apps/crm/docs
    C->>App: GET /docs
    App-->>Browser: 200 OK Swagger UI

    Note over Browser,App: Unauthenticated request

    Browser->>C: GET /apps/crm/dashboard [no valid session]
    C->>AV: GET /auth/verify
    AV-->>C: 401 Unauthorized
    C-->>Browser: 401 (redirects to /auth/login)
```

```mermaid
flowchart TD
    Request([Incoming HTTPS request]) --> RouteMatch{Route match?}

    RouteMatch -- "/auth/* or /docs*" --> PublicLatarnia[Proxy to Latarnia :8000<br/>no auth check]
    RouteMatch -- "/apps/name/docs* or /openapi.json" --> PublicSwagger[Proxy to App port<br/>no auth check]
    RouteMatch -- "/apps/name/*" --> ForwardAuth[forward_auth to /auth/verify]
    RouteMatch -- "/* catch-all" --> ForwardAuthCatchAll[forward_auth to /auth/verify]

    ForwardAuth --> AuthOk{/auth/verify response?}
    AuthOk -- 401 --> Deny[Return 401 to browser]
    AuthOk -- 200 + headers --> StripPrefix[handle_path strips /apps/name prefix]
    StripPrefix --> ProxyApp[Proxy to App port with<br/>X-Latarnia-* headers]

    ForwardAuthCatchAll --> AuthOkCatchAll{/auth/verify response?}
    AuthOkCatchAll -- 401 --> DenyCatchAll[Return 401 to browser]
    AuthOkCatchAll -- 200 + headers --> ProxyLatarnia[Proxy to Latarnia :8000 with<br/>X-Latarnia-* headers]
```
