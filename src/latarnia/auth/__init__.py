"""Authentication & authorization for the Latarnia platform (P-0008).

Layout:
  db.py                 AuthDB — platform DB init + migration runner + query helpers
  providers/base.py     AuthProvider protocol
  providers/totp.py     TOTPAuthProvider (V1)
  users.py              user CRUD + setup-token lifecycle
  sessions.py           opaque session create/validate/invalidate
  routes.py             /auth and /api/auth FastAPI router
"""

from .db import AuthDB

__all__ = ["AuthDB"]
