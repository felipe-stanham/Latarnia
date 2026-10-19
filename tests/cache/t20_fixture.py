"""Create dev-only test principals for T-0020 live checks. Prints shell exports."""
import os, sys
assert os.environ.get("ENV") == "dev", "dev only"
from latarnia.core.config import config_manager
config_manager.load_config()
from latarnia.core.pg_client import PgClient
from latarnia.auth.db import AuthDB
from latarnia.auth.users import UserStore
from latarnia.auth.sessions import SessionStore
from latarnia.auth.roles import RoleStore
from latarnia.auth.jwt_auth import JWTAuth
from latarnia.auth.tokens import MachineTokenStore

pg = PgClient(config_manager)
db = AuthDB(config_manager, pg)
users = UserStore(db); sessions = SessionStore(db, config_manager); roles = RoleStore(db, users)
su = users.get_or_create_bootstrap_superuser()

def ensure(name):
    u = users.get_user_by_username(name)
    if not u:
        users.create_user(name)
        u = users.get_user_by_username(name)
    users.activate_user(u["id"])
    return users.get_user_by_username(name)

low = ensure("t20-low"); none_u = ensure("t20-none")
roles.set_role(low["id"], "example_full_app", "webUI-low", su["id"])
roles.set_role(low["id"], "example_companion", "webUI-low", su["id"])
roles.set_role(none_u["id"], "example_full_app", "none", su["id"])

secret = None
for line in open(sys.argv[1]):
    if line.startswith("LATARNIA_JWT_SECRET="):
        secret = line.split("=", 1)[1].strip()
jwt = JWTAuth(lambda: secret); tokens = MachineTokenStore(db, jwt)
from datetime import datetime, timedelta, timezone
exp = datetime.now(timezone.utc) + timedelta(hours=2)
tok_ok = tokens.create_token(low["id"], "t20-ok", {"example_full_app": "webUI-low"}, False, su["id"], exp)
tok_other = tokens.create_token(low["id"], "t20-other", {"example_companion": "webUI-low"}, False, su["id"], exp)
print(f"S_LOW={sessions.create_session(low['id'])}")
print(f"S_NONE={sessions.create_session(none_u['id'])}")
print(f"TOK_OK={tok_ok if isinstance(tok_ok, str) else tok_ok[0]}")
print(f"TOK_OTHER={tok_other if isinstance(tok_other, str) else tok_other[0]}")
print(f"S_SU={sessions.create_session(su['id'])}")
