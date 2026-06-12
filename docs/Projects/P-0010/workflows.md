# P-0010 Workflows

Diagrams for the main flows, each tagged with the capability it realizes.

## flow-01 — Authenticated root redirect (cap-001)

```mermaid
flowchart TD
    A[GET /] --> B{Caddy forward_auth: valid session?}
    B -- No --> C[302 -> /auth/login?next=/]
    B -- Yes --> D[Proxy / to platform]
    D --> E[Platform route GET / ]
    E --> F[302 -> /dashboard]
    F --> G[GET /dashboard -> dashboard renders]
```

## flow-02 — Deep-link login round-trip with hardened next (cap-002)

```mermaid
sequenceDiagram
    participant B as Browser
    participant C as Caddy
    participant V as /auth/verify
    participant L as /auth/login
    B->>C: GET /apps/latarnik/page (no session)
    C->>V: forward_auth (orig_uri=/apps/latarnik/page)
    V-->>C: 401
    C-->>B: 302 /auth/login?next=/apps/latarnik/page
    B->>L: GET /auth/login?next=/apps/latarnik/page
    Note over L: render form, carry next
    B->>L: POST username, code, next=/apps/latarnik/page
    L->>L: validate creds + TOTP
    L->>L: safe_next(next): start "/" & not "//" & not "/\\" & no "://"
    alt next safe
        L-->>B: 303 Location: /apps/latarnik/page (+ cookie)
    else next unsafe/absent
        L-->>B: 303 Location: /dashboard (+ cookie)
    end
```

## flow-03 — Superuser gate on platform actions (cap-003, cap-004)

```mermaid
sequenceDiagram
    participant Cl as Client (browser session or Bearer JWT)
    participant M as JWTAuthMiddleware
    participant H as Handler (/api/system/restart | /api/logs/latarnia)
    Cl->>M: request to /api/...
    alt no valid principal
        M-->>Cl: 401
    else valid principal
        M->>H: pass (state has session_user_id or jwt_claims)
        H->>H: require_superuser(request)
        alt not superuser
            H-->>Cl: 403 Forbidden
        else superuser
            H-->>Cl: 200 (execute)
        end
    end
```

## flow-04 — Activity feed filtering (cap-005)

```mermaid
flowchart TD
    A[GET /api/activity/recent] --> B[resolve current user]
    B --> C{is_superuser?}
    C -- Yes --> Z[return all recent events]
    C -- No --> D[load events from latarnia:events:recent]
    D --> E[for each event]
    E --> F{source in system/service_manager?}
    F -- Yes --> X[drop]
    F -- No --> G[map source app_id -> App name via registry]
    G --> H{role for App == full?}
    H -- No --> X
    H -- Yes --> K[keep]
    K --> Y[return kept events]
    X --> Y
```

## flow-05 — User management: delete / reactivate / re-issue (cap-006, cap-007, cap-008)

```mermaid
flowchart TD
    subgraph Delete [cap-006 DELETE /api/auth/users/id]
        D0[request] --> D1{requester superuser?}
        D1 -- No --> D403[403]
        D1 -- Yes --> D2{target == self?}
        D2 -- Yes --> D409a[409 cannot delete self]
        D2 -- No --> D3{target is last active superuser?}
        D3 -- Yes --> D409b[409 cannot delete last superuser]
        D3 -- No --> D4[DELETE users row]
        D4 --> D5[children CASCADE; granted_by SET NULL]
        D5 --> D200[200]
    end

    subgraph Reactivate [cap-007 POST /api/auth/users/id/activate]
        R0[request] --> R1{requester superuser?}
        R1 -- No --> R403[403]
        R1 -- Yes --> R2{target has totp credential?}
        R2 -- No --> R409[409 re-issue setup instead]
        R2 -- Yes --> R3[set is_active=TRUE]
        R3 --> R200[200]
    end

    subgraph Reissue [cap-008 POST /api/auth/users/id/setup-token]
        I0[request] --> I1{requester superuser?}
        I1 -- No --> I403[403]
        I1 -- Yes --> I2[delete totp user_credentials row]
        I2 --> I3[delete sessions]
        I3 --> I4[is_active=FALSE; new setup_token + expiry]
        I4 --> I200[200 returns setup_url]
    end
```

## flow-06 — Re-enrollment after re-issue (cap-008 continued)

```mermaid
sequenceDiagram
    participant U as User
    participant S as /auth/setup?token=...
    participant DB as auth DB
    U->>S: GET /auth/setup?token=<new>
    S->>DB: get_user_by_setup_token(token)
    DB-->>S: user (inactive, no totp credential)
    S->>DB: ensure_credentials -> generate NEW secret
    S-->>U: QR + secret (new device)
    U->>S: POST code
    S->>S: validate against NEW secret
    S->>DB: activate_user (is_active=TRUE, clear token)
    S-->>U: 303 -> /dashboard (+ session)
```
