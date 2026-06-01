# P-0008: Workflows

## flow-01: First-Run TOTP Setup [cap-009, cap-013]

First visit to any protected route when no users exist in `latarnia_platform_{env}`.

```mermaid
flowchart TD
    A([Browser: GET /]) --> B{Users exist in DB?}
    B -- No --> C[Redirect to /auth/setup]
    B -- Yes --> D[Normal auth flow]
    C --> E[TOTPAuthProvider.setup_credentials:\ngenerate TOTP secret, AES-256-GCM encrypt,\nwrite to user_credentials table]
    E --> F[Render setup page: QR code + manual key]
    F --> G([User scans QR code in Authenticator app])
    G --> H([User submits first 6-digit code])
    H --> I{TOTPAuthProvider.validate?}
    I -- No --> F
    I -- Yes --> J[Mark user as superuser\nSet last_login_at]
    J --> K[Issue session cookie]
    K --> L([Redirect to /])
    L --> M([Dashboard rendered])
```

## flow-01b: Superuser Adds a New User [cap-009, cap-024]

After initial setup, superuser invites additional users.

```mermaid
flowchart TD
    A([Superuser: Users & Roles tab]) --> B[Click Add User\nenter username]
    B --> C[POST /api/auth/users]
    C --> D[Create user row is_active=false\nGenerate setup_token expires 24h]
    D --> E[Return setup_url]
    E --> F([Superuser copies URL and sends to new user])
    F --> G([New user: GET /auth/setup?token=setup_token])
    G --> H{Token valid & not expired?}
    H -- No --> I([Error: invalid or expired link])
    H -- Yes --> J[TOTPAuthProvider.setup_credentials:\ngenerate secret, write to user_credentials]
    J --> K[Render QR code page]
    K --> L([New user scans QR, submits code])
    L --> M{TOTPAuthProvider.validate?}
    M -- No --> K
    M -- Yes --> N[Set is_active=true, null out setup_token\nSet last_login_at]
    N --> O[Issue session cookie]
    O --> P([Redirect to /])
```

## flow-02: TOTP Login [cap-010, cap-011]

Normal login flow after initial setup.

```mermaid
flowchart TD
    A([Browser: GET protected route]) --> B{Valid session cookie?}
    B -- Yes --> C[/auth/verify proceeds]
    B -- No --> D[Caddy redirects to /auth/login]
    D --> E([User enters username + 6-digit TOTP code])
    E --> F{User exists & is_active?}
    F -- No --> G[Return error message]
    G --> E
    F -- Yes --> H{TOTPAuthProvider.validate\ncode valid & not replayed?}
    H -- No --> G
    H -- Yes --> I[Generate random UUID session token\nStore SHA-256 hash in sessions table\nSet expires_at = now + TTL]
    I --> J[Set latarnia_session cookie\nHTTP-only, Secure, SameSite=Strict]
    J --> K([Redirect to original destination])
```

## flow-03: Caddy forward_auth with Role Injection [cap-004, cap-012, cap-015, cap-016]

Every request to a protected route goes through this flow.

```mermaid
sequenceDiagram
    actor Browser
    participant Caddy
    participant AuthVerify as Latarnia /auth/verify
    participant DB as latarnia_platform_{env}
    participant Target as App or Latarnia

    Browser->>Caddy: GET /apps/my_app/dashboard (with session cookie)
    Caddy->>AuthVerify: GET /auth/verify\nX-Forwarded-Uri: /apps/my_app/dashboard\nCookie: latarnia_session=<token>

    AuthVerify->>DB: SELECT session WHERE token_hash=SHA256(token) AND expires_at > now()
    alt Session invalid or expired
        AuthVerify-->>Caddy: 401 Unauthorized
        Caddy-->>Browser: 302 → /auth/login
    else Session valid
        AuthVerify->>DB: SELECT role FROM app_roles WHERE user_id=? AND app_name='my_app'
        AuthVerify-->>Caddy: 200 OK\nX-Latarnia-User: <user_id>\nX-Latarnia-App-Role: webUI-med\nX-Latarnia-Is-Super: false
        Caddy->>Target: Proxy request with injected headers
        Target-->>Browser: Response
    end
```

## flow-04: JWT Machine Token Issuance and API Call [cap-019, cap-020]

Machine clients (scripts, AI agents) authenticate with a long-lived JWT.

```mermaid
sequenceDiagram
    actor Operator
    participant Dashboard
    participant Latarnia
    participant DB as latarnia_platform_{env}
    actor Client as Machine Client

    Note over Operator,Dashboard: Token creation (one-time, in dashboard)
    Operator->>Dashboard: POST /api/auth/tokens\n{label, app_scope, expires_at}
    Dashboard->>Latarnia: (superuser session required for full-role tokens)
    Latarnia->>DB: INSERT machine_tokens (token_hash, app_scope, ...)
    Latarnia->>DB: Sign JWT {sub, iat, exp, apps, super}
    Latarnia-->>Dashboard: {token: "<raw_jwt>"}
    Dashboard-->>Operator: Show token once (not stored in plaintext)

    Note over Client,Latarnia: API call
    Client->>Latarnia: GET /api/apps\nAuthorization: Bearer <jwt>
    Latarnia->>Latarnia: Validate JWT signature and expiry
    Latarnia->>DB: SELECT machine_tokens WHERE token_hash=SHA256(jwt) AND revoked_at IS NULL
    alt Token invalid or revoked
        Latarnia-->>Client: 401 Unauthorized
    else Token valid
        Latarnia->>Latarnia: Extract app_scope from JWT claims
        Latarnia-->>Client: 200 OK with filtered response (apps in scope only)
    end
```

## flow-05: App Registration → Caddy Config Reload [cap-002, cap-007]

When an app with `has_web_ui: true` is registered or deregistered.

```mermaid
sequenceDiagram
    participant Registry as AppRegistry
    participant CaddyMgr as CaddyConfigManager
    participant FS as File System
    participant CaddyAPI as Caddy Admin API (localhost:2019)

    Registry->>CaddyMgr: on_app_registered(app_id, port, has_web_ui)
    CaddyMgr->>CaddyMgr: Acquire config write lock
    CaddyMgr->>Registry: Get all registered apps with has_web_ui=true
    CaddyMgr->>FS: Write /opt/latarnia/{env}/caddy/latarnia.caddyfile
    Note over FS: Per-app blocks:\n- /apps/{app}/docs* → no auth, proxy to port\n- /apps/{app}/* → forward_auth + proxy to port
    CaddyMgr->>CaddyAPI: POST /load (new full config)
    CaddyAPI-->>CaddyMgr: 200 OK
    CaddyMgr->>CaddyMgr: Release lock
```

## flow-06: MCP Authentication [cap-021]

AI agent connecting to the MCP gateway.

```mermaid
sequenceDiagram
    actor Agent as AI Agent (e.g. Claude)
    participant Caddy
    participant MCPGateway as Latarnia MCP Gateway
    participant DB as latarnia_platform_{env}
    participant AppMCP as App MCP Server

    Agent->>Caddy: GET /mcp/sse\nAuthorization: Bearer <jwt>
    Caddy->>MCPGateway: Proxy (no forward_auth on MCP — JWT validated internally)
    MCPGateway->>MCPGateway: Validate JWT signature and expiry
    MCPGateway->>DB: Check token not revoked
    alt Invalid
        MCPGateway-->>Agent: 401
    else Valid
        MCPGateway->>MCPGateway: Extract app_scope from JWT
        Note over MCPGateway: Only expose tools from apps\nthe token has access to
        MCPGateway->>AppMCP: Connect with X-Latarnia-App-Role: <role>
        MCPGateway-->>Agent: Aggregated tool list (scoped)
    end
```
