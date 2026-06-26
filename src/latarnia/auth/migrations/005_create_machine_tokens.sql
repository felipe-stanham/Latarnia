-- P-0008 auth: long-lived machine (JWT) tokens.
-- The raw JWT is shown once at creation. token_hash (SHA-256 of the JWT) is
-- stored for per-request revocation lookup. app_scope is {app_name: role} and
-- is embedded in the JWT 'apps' claim. revoked_at non-null => rejected even if
-- the signature is valid and unexpired.
CREATE TABLE machine_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    app_scope   JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at  TIMESTAMPTZ,          -- null = never expires
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_by  UUID REFERENCES users(id),
    revoked_at  TIMESTAMPTZ           -- null = active
);

CREATE INDEX idx_machine_tokens_hash ON machine_tokens (token_hash);
CREATE INDEX idx_machine_tokens_user ON machine_tokens (user_id);
