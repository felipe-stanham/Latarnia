-- P-0008 auth: users
-- The first user created via /auth/setup is the superuser. Subsequent users
-- are invited by a superuser and complete TOTP enrollment via a setup token.
CREATE TABLE users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username                TEXT NOT NULL UNIQUE,
    is_superuser            BOOLEAN NOT NULL DEFAULT FALSE,
    is_active               BOOLEAN NOT NULL DEFAULT FALSE,
    setup_token             TEXT,                -- one-time TOTP enrollment token (nulled after use)
    setup_token_expires_at  TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at           TIMESTAMPTZ
);

CREATE UNIQUE INDEX idx_users_setup_token
    ON users (setup_token)
    WHERE setup_token IS NOT NULL;
