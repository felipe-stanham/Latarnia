import os
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Resolve templates directory relative to project root
# File path: <project_root>/src/latarnia/web/dashboard.py
# parents[0] = web, parents[1] = latarnia, parents[2] = src
PROJECT_ROOT = Path(__file__).resolve().parents[2].parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def build_dashboard_router(session_resolver: Optional[Callable] = None) -> APIRouter:
    """Build the dashboard router.

    `session_resolver` is a callable (request) -> user_row | None that the
    platform passes in so the route can inject `is_superuser` into the template
    context without importing main.py (which would create a circular import).
    When absent (e.g. legacy callers), is_superuser defaults to False.
    """
    router = APIRouter()

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        """Main Latarnia dashboard page.

        Data is populated client-side via calls to existing JSON APIs
        (e.g. /health and /api/apps). This keeps the server-side view
        simple and aligned with the manual-refresh pattern.
        """
        env = os.environ.get("ENV", "dev").lower()
        is_superuser = False
        if session_resolver is not None:
            user = session_resolver(request)
            is_superuser = bool(user and user.get("is_superuser"))
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            context={"env": env, "is_superuser": is_superuser},
        )

    return router
