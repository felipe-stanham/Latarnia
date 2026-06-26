-- P-0008 auth: per-(user, app) role grants.
-- role in (none, webUI-low, webUI-med, webUI-full, full). Absence of a row
-- means effective role 'none'. Rows are retained when an app deregisters so
-- the assignment survives re-registration.
CREATE TABLE app_roles (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    app_name    TEXT NOT NULL,
    role        TEXT NOT NULL,
    granted_by  UUID REFERENCES users(id),
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, app_name)
);

CREATE INDEX idx_app_roles_user ON app_roles (user_id);
