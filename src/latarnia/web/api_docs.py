"""
Platform-owned API docs for Apps (T-0020).

Every App's API docs live under the platform's `/docs` namespace, never inside
the App's own URL space:

    /docs/apps/{app}/ui        Swagger page for the App's UI API   (/api/...)
    /docs/apps/{app}/rest      Swagger page for the App REST API   (/rest/...)
    /api/docs/apps/{app}/{kind} the split OpenAPI schema behind each page
    /api/docs/apps/{app}        which halves exist (for the dashboard tile)

The platform fetches the App's `/openapi.json` straight from its port (never
published by Caddy), keeps only the paths under the half's prefix, prunes the
component schemas that half doesn't reference, and points `servers` at the
public path so "Try it out" hits `/apps/{app}/api/...` or `/rest/{app}/...`.

Access: a role other than `none` on that App. The pages sit behind Caddy's
session `forward_auth`; the schemas sit under `/api/*`, so JWTAuthMiddleware
also accepts a Machine Token scoped to the App.
"""
from __future__ import annotations

import copy
import logging
from html import escape
from typing import Callable, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

logger = logging.getLogger("latarnia.web.api_docs")

# Docs kind -> path prefix inside the App.
DOC_PREFIXES = {"ui": "/api/", "rest": "/rest/"}
DOC_TITLES = {"ui": "UI API", "rest": "App REST API"}

FETCH_TIMEOUT_SECONDS = 3.0


class AppNotRunning(Exception):
    """The App has no assigned port or did not answer on it."""


def _component_key(ref) -> Optional[tuple]:
    """`#/components/{section}/{name}[/...]` -> (section, name); else None."""
    if not isinstance(ref, str) or not ref.startswith("#/components/"):
        return None
    parts = ref.split("/")
    if len(parts) < 4:
        return None
    return parts[2], parts[3]


def _collect_refs(node, refs: set) -> None:
    if isinstance(node, dict):
        key = _component_key(node.get("$ref"))
        if key is not None:
            refs.add(key)
        for value in node.values():
            _collect_refs(value, refs)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, refs)


def _prune_components(components: dict, paths: dict) -> dict:
    """Keep only the components reachable from `paths` (transitively).

    `securitySchemes` are always kept: operations reference them by name in
    `security`, not by `$ref`, and dropping them would hide "Authorize".
    """
    seen: set = set()
    pending: set = set()
    _collect_refs(paths, pending)
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        section, name = key
        target = (components.get(section) or {}).get(name)
        if target is not None:
            _collect_refs(target, pending)

    pruned: dict = {}
    for section, name in seen:
        target = (components.get(section) or {}).get(name)
        if target is not None:
            pruned.setdefault(section, {})[name] = target
    if components.get("securitySchemes"):
        pruned["securitySchemes"] = components["securitySchemes"]
    return pruned


def split_openapi(spec: dict, kind: str, app_name: str) -> Optional[dict]:
    """Return the half of an App's OpenAPI schema for `kind`, or None if empty.

    `ui` keeps `/api/...` paths and is served from `/apps/{app}`; `rest` keeps
    `/rest/...` paths, re-rooted under `/rest/{app}` so the paths read relative
    to that server URL.
    """
    prefix = DOC_PREFIXES[kind]
    paths = {p: v for p, v in (spec.get("paths") or {}).items() if p.startswith(prefix)}
    if not paths:
        return None

    if kind == "rest":
        server = f"/rest/{app_name}"
        paths = {p[len("/rest"):]: v for p, v in paths.items()}
    else:
        server = f"/apps/{app_name}"

    result = {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("paths", "components", "servers")}
    result["paths"] = copy.deepcopy(paths)
    result["servers"] = [{"url": server}]
    info = result.get("info") if isinstance(result.get("info"), dict) else {}
    result["info"] = info
    info["title"] = f"{info.get('title') or app_name} — {DOC_TITLES[kind]}"
    components = _prune_components(spec.get("components") or {}, paths)
    if components:
        result["components"] = copy.deepcopy(components)
    return result


async def fetch_app_openapi(port: Optional[int]) -> Optional[dict]:
    """GET the App's internal `/openapi.json`. None when the App serves none."""
    if not port:
        raise AppNotRunning()
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"http://localhost:{port}/openapi.json")
    except httpx.HTTPError as exc:
        raise AppNotRunning() from exc
    if resp.status_code != 200:
        return None
    try:
        spec = resp.json()
    except ValueError:
        return None
    return spec if isinstance(spec, dict) else None


def build_api_docs_router(app_manager, role_store, session_resolver: Callable) -> APIRouter:
    """Build the `/docs/apps/...` pages and `/api/docs/apps/...` schemas.

    `session_resolver` is a callable (request) -> user_row | None (cookie only).
    """
    router = APIRouter()

    def _can_view(request: Request, app_name: str) -> bool:
        claims = getattr(request.state, "jwt_claims", None)
        if claims is not None:
            return bool(claims.get("super")) or \
                claims.get("apps", {}).get(app_name, "none") != "none"
        user = session_resolver(request)
        if user is None and request.url.path.startswith("/api/"):
            # The middleware gated this route; a session revoked since then
            # must fail closed (same rule as /api/apps).
            raise HTTPException(status_code=401, detail="Authentication required")
        # user None on a page = dev direct access (Caddy forward_auth guarantees
        # a session in tst/prd); RoleStore.is_visible allows it like the dashboard.
        return role_store.is_visible(user, app_name)

    def _accessible_service_app(request: Request, app_name: str):
        # Access is checked before existence, so a caller without access
        # cannot tell a missing App from a forbidden one.
        if not _can_view(request, app_name):
            raise HTTPException(status_code=403, detail="No access to this app")
        app = app_manager.registry.get_app_by_name(app_name)
        if app is None or getattr(app, "type", None) != "service":
            raise HTTPException(status_code=404, detail=f"App {app_name} not found")
        return app

    async def _split(request: Request, app_name: str, kind: str) -> dict:
        if kind not in DOC_PREFIXES:
            raise HTTPException(status_code=404, detail=f"Unknown docs kind: {kind}")
        app = _accessible_service_app(request, app_name)
        try:
            spec = await fetch_app_openapi(app.runtime_info.assigned_port)
        except AppNotRunning:
            raise HTTPException(status_code=503, detail=f"App {app_name} is not running")
        half = split_openapi(spec, kind, app_name) if spec else None
        if half is None:
            raise HTTPException(
                status_code=404,
                detail=f"App {app_name} has no {DOC_TITLES[kind]} routes",
            )
        return half

    @router.get("/api/docs/apps/{app_name}")
    async def api_docs_summary(app_name: str, request: Request):
        """Which docs halves an App has — drives the dashboard tile buttons."""
        app = _accessible_service_app(request, app_name)
        try:
            spec = await fetch_app_openapi(app.runtime_info.assigned_port)
        except AppNotRunning:
            return {"app": app_name, "running": False, "ui": False, "rest": False}
        return {
            "app": app_name,
            "running": True,
            "ui": bool(spec) and split_openapi(spec, "ui", app_name) is not None,
            "rest": bool(spec) and split_openapi(spec, "rest", app_name) is not None,
        }

    @router.get("/api/docs/apps/{app_name}/{kind}")
    async def api_docs_schema(app_name: str, kind: str, request: Request):
        return await _split(request, app_name, kind)

    @router.get("/docs/apps/{app_name}/{kind}", response_class=HTMLResponse)
    async def api_docs_page(app_name: str, kind: str, request: Request):
        try:
            await _split(request, app_name, kind)
        except HTTPException as exc:
            return HTMLResponse(_error_page(app_name, exc.detail), status_code=exc.status_code)
        # get_swagger_ui_html interpolates both values unescaped (the URL into a
        # JS string), and manifest names have no character pattern.
        return get_swagger_ui_html(
            openapi_url=f"/api/docs/apps/{quote(app_name, safe='')}/{kind}",
            title=escape(f"{app_name} — {DOC_TITLES[kind]}"),
        )

    return router


def _error_page(app_name: str, detail) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{escape(app_name)} — API docs</title></head>"
        "<body style='font-family:system-ui,sans-serif;margin:3rem'>"
        f"<h1>{escape(app_name)}</h1><p>{escape(str(detail))}</p>"
        "<p><a href='/dashboard'>Back to dashboard</a></p></body></html>"
    )
