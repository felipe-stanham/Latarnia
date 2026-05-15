# P-0008: Caddy Reverse Proxy + Authentication & Authorization

## Problem

Latarnia has no authentication or transport security. The dashboard, all REST APIs, and the MCP gateway are reachable by anyone on the LAN without credentials. This is the primary blocker before exposing any Latarnia capability to household members or AI agents. Additionally, the custom Python reverse proxy (`web_proxy.py`) re-implements a solved problem — adding TLS and auth on top of it would compound the technical debt.

## Context & Constraints

- **Single-user home server** — no multi-tenancy. One operator (superuser), optional additional users with scoped access.
- **Existing proxy:** `web_proxy.py` (`WebProxyManager`) handles app webUI forwarding. It must be deleted and replaced by Caddy.
- **Existing infrastructure:** Caddy is the chosen replacement per the V2 framing in `docs/SYSTEM.md`. Postgres is the platform DB (already provisioned cluster-wide). Redis and systemd are in place.
- **No password management:** The operator does not want to manage passwords. Authentication is via TOTP (RFC 6238 — Google Authenticator, Microsoft Authenticator, Authy, etc.).
- **Port isolation:** Firewall (ufw) blocks external access to all app and platform ports. Only Caddy's HTTPS port is reachable from outside localhost.
- **Multi-environment:** PRD and TST co-exist on the same Pi. Caddy serves both on different ports. Each Latarnia environment manages its own Caddyfile section.

## Proposed Solution

Replace `web_proxy.py` with Caddy as the single ingress for all Latarnia traffic. Latarnia generates and maintains a Caddyfile from the app registry, reloading Caddy on app registration/deregistration. Caddy enforces authentication via `forward_auth` (delegating session validation to Latarnia's `/auth/verify` endpoint). Latarnia maintains a `latarnia_platform_{env}` Postgres DB for users, sessions, per-app role grants, and machine JWT tokens.

### Main actors
- **Operator (superuser):** manages users, assigns roles, generates machine tokens
- **User:** authenticates via TOTP, accesses dashboard and apps per assigned roles
- **Machine client:** authenticates via JWT Bearer token (for REST API / MCP access)

### Capabilities

**Caddy Infrastructure**
- **cap-001:** Caddy installed and running as a systemd service on the Pi
- **cap-002:** Latarnia generates a Caddyfile (per-environment section) from the app registry; calls `caddy reload` on app registration and deregistration
- **cap-003:** Caddy terminates TLS using Let's Encrypt (ACME HTTP-01) when a real domain is configured; falls back to self-signed for `localhost` (dev environment only). Domain is read from `{ENV}_DOMAIN` config key — not hardcoded.
- **cap-004:** Caddy uses `forward_auth` to delegate session validation to Latarnia's `/auth/verify` on all protected routes
- **cap-005:** App swagger (`/apps/{app}/docs`, `/apps/{app}/openapi.json`) and Latarnia's own `/docs` are routed without auth (public on LAN)
- **cap-006:** `ufw` firewall rules block all app ports (8000, 8100–8199, 9001–9099) from external hosts; only Caddy's ports (443 PRD, 8443 TST) are open
- **cap-007:** `web_proxy.py` and `WebProxyManager` are deleted; all proxy responsibilities transferred to Caddy

**Auth Foundation**
- **cap-008:** Platform Postgres DB (`latarnia_platform_{env}`) created at Latarnia startup with auth schema migrations applied
- **cap-009:** TOTP first-run setup at `GET /auth/setup` — generates TOTP secret, displays QR code, requires first valid code to confirm; locked after setup
- **cap-010:** TOTP login at `GET/POST /auth/login` — accepts 6-digit code, validates, issues session cookie (HTTP-only, Secure, SameSite=Strict)
- **cap-011:** Session lifecycle — sessions stored in DB with expiry (configurable TTL, default 8h); expired sessions redirect to login
- **cap-012:** `/auth/verify` endpoint — validates session cookie, extracts app name from `X-Forwarded-Uri`, looks up user's role for that app, returns `X-Latarnia-User`, `X-Latarnia-App-Role`, `X-Latarnia-Is-Super` headers; returns 200 (proceed) or 401 (redirect to login)
- **cap-013:** User record in `latarnia_platform_{env}` — single user for V1; TOTP secret stored AES-256 encrypted using `LATARNIA_TOTP_ENC_KEY` from `secrets.env`

**Role Model & Authorization**
- **cap-014:** Per-app role assignment stored in DB — roles: `none`, `webUI-low`, `webUI-med`, `webUI-full`, `full`; default for new users is `none` for all apps
- **cap-015:** Dashboard tile visibility — tile is hidden when user's role for that app is `none`; dashboard fetches per-user role map on page load
- **cap-016:** `X-Latarnia-App-Role` header injected on all proxied requests (webUI and MCP share the same role level)
- **cap-017:** User management UI in dashboard (superuser-only tab) — list users, assign per-app roles
- **cap-018:** Only superuser can assign `full` role — enforced server-side; non-superuser assignment of `full` returns 403

**JWT & Machine Tokens**
- **cap-019:** JWT issuance — `POST /api/auth/tokens` creates a signed JWT with label, app scope (per-app role level), and optional expiry; token record stored in DB for revocation
- **cap-020:** JWT middleware in Latarnia — validates Bearer token on `/api/*` and `/mcp/*` endpoints; checks token is not revoked; extracts app scope from claims
- **cap-021:** MCP gateway JWT enforcement — validates Bearer token; passes `X-Latarnia-App-Role` to per-app MCP server connection

**Spec & Example Update**
- **cap-022:** `app-specification.md` updated with `X-Latarnia-App-Role` header contract (values, trust model, when it is set) and MCP role documentation
- **cap-023:** `example_full_app` updated to demonstrate role-aware webUI (read header, adjust UI) and role-aware MCP (read header in tool handler)

## Acceptance Criteria

### cap-001 / cap-006
- `systemctl is-active caddy` returns `active` on the Pi
- `curl -k https://localhost` returns the Latarnia dashboard HTML
- `curl http://localhost:8000` from an external host is refused (ufw DROP)
- `curl http://localhost:8101` from an external host is refused (ufw DROP)

### cap-002
- Starting a new app with `has_web_ui: true` in its manifest causes a Caddyfile reload; `https://latarnia.local/apps/{app_name}/` proxies to the app's port within 5 seconds of registration
- Stopping and deregistering the app causes a reload; the route returns 404

### cap-003
- Browser connects to `https://{PRD_DOMAIN}` with TLS; certificate is issued by Let's Encrypt (no browser trust import required)
- HTTP requests to port 80 are redirected to HTTPS
- Dev environment (`localhost`) uses Caddy's automatic self-signed cert

### cap-004
- Unauthenticated `GET /` redirects to `/auth/login`
- Unauthenticated `GET /apps/{app}/` redirects to `/auth/login`

### cap-005
- `curl https://{PRD_DOMAIN}/apps/{app}/docs` returns 200 without a session cookie
- `curl https://{PRD_DOMAIN}/docs` returns 200 without a session cookie

### cap-007
- `web_proxy.py` file does not exist in the codebase
- `WebProxyManager` is not imported or instantiated anywhere

### cap-009
- First visit to `/auth/setup` with no users in DB renders a QR code page
- Submitting a valid TOTP code completes setup; subsequent visit to `/auth/setup` redirects to `/auth/login`
- Submitting an invalid code returns an error and stays on setup page

### cap-010
- Valid TOTP code → 302 redirect to `/` with `latarnia_session` cookie set
- Invalid TOTP code → error message, no cookie
- Replayed code (same code used twice within 30s window) → rejected

### cap-011
- Session cookie with TTL 8h is valid for 8h; after expiry, `/auth/verify` returns 401
- `DELETE /auth/session` invalidates the session (logout)

### cap-012
- `GET /auth/verify` with valid session for `/apps/my_app/` returns 200 with `X-Latarnia-App-Role: webUI-med` (or whatever the user's role is)
- `GET /auth/verify` with invalid/missing session returns 401
- Superuser session returns `X-Latarnia-Is-Super: true`

### cap-014
- User with `none` role for `my_app` → tile not rendered in dashboard
- User with `webUI-low` role → tile rendered
- Role change takes effect on next page load (no restart required)

### cap-016
- App receives `X-Latarnia-App-Role: full` when user's role is `full`
- App receives `X-Latarnia-App-Role: webUI-low` when user's role is `webUI-low`
- MCP tool call with `webUI-med` role receives the same header value

### cap-018
- Non-superuser `POST /api/auth/roles` attempting to set role `full` → 403
- Superuser `POST /api/auth/roles` with role `full` → 200

### cap-019
- `POST /api/auth/tokens` returns a signed JWT with correct claims
- Token record appears in `GET /api/auth/tokens` response
- `DELETE /api/auth/tokens/{id}` marks token as revoked; subsequent API call with that token returns 401

### cap-020
- `GET /api/apps` with valid JWT → 200
- `GET /api/apps` with no token → 401
- `GET /api/apps` with expired JWT → 401
- `GET /api/apps/{id}` with JWT scoped to a different app → 403

### cap-021
- MCP connection with valid JWT returns tool list
- MCP connection without JWT returns 401
- Per-app MCP server receives `X-Latarnia-App-Role` header matching the JWT claim

## Key Flows

### flow-01: First-Run TOTP Setup
See `workflows.md#flow-01`

### flow-02: TOTP Login and Session Issuance
See `workflows.md#flow-02`

### flow-03: Caddy forward_auth with Role Injection
See `workflows.md#flow-03`

### flow-04: JWT Machine Token Issuance and API Call
See `workflows.md#flow-04`

### flow-05: App Registration → Caddy Config Reload
See `workflows.md#flow-05`

## Technical Considerations

- **Caddy admin API** (`localhost:2019`) used for `caddy reload`. Latarnia calls `curl -X POST localhost:2019/load` with the new config after writing the Caddyfile. The admin API must be bound to localhost only.
- **TOTP secret encryption:** AES-256-GCM using `LATARNIA_TOTP_ENC_KEY` (32-byte base64 key stored in `secrets.env`). Key is loaded into memory at startup; plaintext secret is never written to disk or logged.
- **JWT signing:** HS256 with `LATARNIA_JWT_SECRET` from `secrets.env`. JWTs contain: `sub` (user_id), `iat`, `exp`, `apps` (dict of app_name → role), `super` (bool).
- **Session cookie:** `latarnia_session=<opaque_token>` — the token is a random UUID stored hashed (SHA-256) in the sessions table. Not a JWT — opaque by design to allow server-side revocation.
- **Domain configuration:** Domain is not hardcoded. `CaddyConfigManager.generate_config()` reads `{ENV}_DOMAIN` from the platform config (e.g., `PRD_DOMAIN=home.stanham.com`). If the domain is `localhost` or empty, Caddy uses its automatic self-signed cert (dev). If the domain is a real hostname, Caddy provisions a Let's Encrypt cert automatically via ACME HTTP-01. No `tls` directive is needed in the Caddyfile — Caddy infers the appropriate method from the site address.
- **ACME HTTP-01:** Port 80 must be reachable from the internet for the ACME challenge. `ufw` must allow port 80/tcp. Caddy handles the challenge listener and renewal automatically.
- **Caddyfile management:** Latarnia writes `/opt/latarnia/{env}/caddy/latarnia.caddyfile`. The system Caddyfile `import`s it. On reload, Latarnia calls the Caddy admin API. On startup, Latarnia generates the file and reloads.
- **Multi-env Caddy:** PRD uses `{PRD_DOMAIN}:443`, TST uses `{TST_DOMAIN}:8443`. Both can point to the same physical domain (same cert, different ports). Each Latarnia env manages its own section via its own generated include file.
- **Platform DB:** `latarnia_platform_{env}` (e.g., `latarnia_platform_prd`). Created by Latarnia at startup using the platform's admin Postgres credentials (same as used by `db_provisioner.py`). Auth schema migrations live in `src/latarnia/auth/migrations/`.
- **Caddy version:** Caddy 2.x (current stable). Installed via official Debian repo, not snap. Managed as a systemd service.

## Risks, Rabbit Holes & Open Questions

### Risks
- **ACME HTTP-01 requires port 80 open:** The router/firewall must forward port 80 to the Pi for Let's Encrypt challenges. Caddy handles the challenge listener automatically; operator must verify the port forward exists. On VPS deployments, the provider's firewall must also allow port 80.
- **Dynamic IP / DNS propagation:** ACME challenges resolve against the configured domain. If the dynamic DNS record is stale or propagating, cert provisioning fails on first startup. Caddy will retry automatically.
- **TOTP clock drift:** Pi's system clock must be NTP-synced. If clock drifts > 30s, TOTP validation fails. Ensure `timedatectl` shows NTP sync active.
- **Caddy reload race:** If Latarnia reloads Caddy while an app is registering, config could be stale. Serialize Caddy config writes with a lock.
- **Session on Pi restart:** Sessions in DB survive Pi restarts (no TTL loss). Caddy restarts also safe (stateless).

### Rabbit Holes — DO NOT GO THERE
- **OAuth2 / OIDC integration** — Out of scope. TOTP is sufficient.
- **Multi-user TOTP with email invites** — V1 is single-user. User management UI is for role assignment only.
- **Caddy plugins / custom middleware** — Use stock Caddy with `forward_auth` only. No custom Caddy plugins.
- **Per-endpoint role granularity** — Roles are per-app, not per-endpoint. Apps that need endpoint-level control use the role header themselves.
- **Token refresh flow** — Machine tokens are long-lived by default. No refresh token mechanism.
- **Streamlit app authentication** — Streamlit apps are on-demand, short-lived. They inherit the Caddy session from the dashboard opener. Do not implement separate auth for Streamlit apps.

### Open Questions
- None remaining — design is confirmed.

## Scope: IN vs OUT

### IN
- Caddy installation, TLS, forward_auth, Caddyfile generation
- Deletion of `web_proxy.py`
- TOTP setup and login flows
- Session cookie lifecycle
- Per-app role model (none, webUI-low, webUI-med, webUI-full, full)
- Superuser role
- Dashboard tile visibility by role
- `X-Latarnia-App-Role` header injection (webUI + MCP)
- JWT issuance and validation (API + MCP)
- Machine token management (create, list, revoke)
- Swagger public routes
- ufw firewall rules
- `app-specification.md` update
- `example_full_app` update

### OUT
- OAuth2, OIDC, SSO
- Email OTP (TOTP only)
- Multi-factor recovery codes (V2 if needed)
- Per-endpoint role granularity (apps own this)
- Rate limiting (future scope)
- Audit log (future scope)
- CertBot or any external cert manager — Caddy handles ACME natively
- Streamlit app auth (inherits Caddy session transparently)
- Cross-host routing
