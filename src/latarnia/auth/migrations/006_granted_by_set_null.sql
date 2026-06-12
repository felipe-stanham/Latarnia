-- P-0010 Scope 4 (cap-006): relax the granted_by FKs so a hard-deleted user
-- does not block or cascade-delete other users' roles / machine tokens.
-- Constraint names are the Postgres defaults for unnamed FK constraints.

ALTER TABLE app_roles DROP CONSTRAINT IF EXISTS app_roles_granted_by_fkey;
ALTER TABLE app_roles ADD CONSTRAINT app_roles_granted_by_fkey
    FOREIGN KEY (granted_by) REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE machine_tokens DROP CONSTRAINT IF EXISTS machine_tokens_granted_by_fkey;
ALTER TABLE machine_tokens ADD CONSTRAINT machine_tokens_granted_by_fkey
    FOREIGN KEY (granted_by) REFERENCES users(id) ON DELETE SET NULL;
