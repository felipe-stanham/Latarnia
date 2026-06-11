-- P-0008 auth: per-(user, auth_method) credential rows.
-- V1 always uses auth_method = 'totp'. credential_data holds method-specific
-- data; for TOTP: {"totp_secret_enc": "<base64 nonce+ciphertext>",
-- "last_totp_window": <int>}. The TOTP secret is AES-256-GCM encrypted with
-- LATARNIA_TOTP_ENC_KEY and is never stored in plaintext.
CREATE TABLE user_credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    auth_method     TEXT NOT NULL,
    credential_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, auth_method)
);

CREATE INDEX idx_user_credentials_user ON user_credentials (user_id);
