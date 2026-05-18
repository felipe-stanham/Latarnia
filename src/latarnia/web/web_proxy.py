"""
Web UI Reverse Proxy for Latarnia

Proxies requests under /apps/{app_name}/ to the app's HTTP server,
allowing apps with has_web_ui=true to serve their own web UIs through
the platform. Handles HTTP requests, static assets, and WebSocket
connections with path stripping and header forwarding.
"""

import asyncio
import html
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

logger = logging.getLogger("latarnia.web_proxy")

router = APIRouter()

# Set by main.py after import
app_manager = None

# Shared httpx client — created lazily
_http_client: Optional[httpx.AsyncClient] = None


async def _get_http_client() -> httpx.AsyncClient:
    """Get or create the shared async HTTP client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
    return _http_client


async def shutdown() -> None:
    """Close the shared HTTP client. Called from main.py lifespan shutdown."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


def _error_page(status_code: int, title: str, message: str) -> HTMLResponse:
    """Return a simple, user-friendly error HTML page."""
    title = html.escape(title)
    message = html.escape(message)
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — Latarnia</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
               display: flex; justify-content: center; align-items: center;
               min-height: 100vh; margin: 0; background: #f8f9fa; color: #333; }}
        .card {{ background: white; border-radius: 8px; padding: 2rem 3rem;
                 box-shadow: 0 2px 8px rgba(0,0,0,0.1); text-align: center;
                 max-width: 480px; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
        p {{ color: #666; }}
        a {{ color: #0d6efd; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>{title}</h1>
        <p>{message}</p>
        <p><a href="/dashboard">Back to Dashboard</a></p>
    </div>
</body>
</html>"""
    return HTMLResponse(content=body, status_code=status_code)


def _lookup_app(app_name: str):
    """
    Look up an app by name and validate it can serve a web UI.
    Returns (app_entry, None) on success or (None, error_response) on failure.
    """
    if app_manager is None:
        return None, _error_page(500, "Server Error", "Platform not initialized.")

    app_entry = app_manager.registry.get_app_by_name(app_name)
    if not app_entry:
        return None, _error_page(404, "App Not Found", f"No app named '{app_name}' is registered.")

    if not app_entry.manifest.config.has_web_ui:
        return None, _error_page(404, "No Web UI", f"App '{app_name}' does not provide a web UI.")

    if app_entry.status != "running":
        return None, _error_page(
            503, "App Unavailable",
            f"App '{app_name}' is not currently running (status: {app_entry.status}).",
        )

    if not app_entry.runtime_info or not app_entry.runtime_info.assigned_port:
        return None, _error_page(503, "App Unavailable", f"App '{app_name}' has no assigned port.")

    return app_entry, None


def _build_forwarded_headers(request: Request) -> dict:
    """Build headers to forward, adding X-Forwarded-* headers."""
    headers = dict(request.headers)

    # Remove hop-by-hop headers that should not be forwarded
    for hop_header in ("host", "connection", "transfer-encoding"):
        headers.pop(hop_header, None)

    # Add forwarding headers
    client_host = request.client.host if request.client else "unknown"
    headers["x-forwarded-for"] = client_host
    headers["x-forwarded-proto"] = request.url.scheme or "http"
    headers["x-forwarded-host"] = request.headers.get("host", "")

    return headers


# ---------------------------------------------------------------------------
# HTTP Proxy — handles all standard HTTP methods
# ---------------------------------------------------------------------------

@router.api_route(
    "/apps/{app_name}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy_http(request: Request, app_name: str, path: str):
    """Proxy HTTP requests to app web UIs."""
    app_entry, error = _lookup_app(app_name)
    if error:
        return error

    port = app_entry.runtime_info.assigned_port
    target_url = f"http://localhost:{port}/{path}"

    # Preserve query string
    if request.url.query:
        target_url += f"?{request.url.query}"

    headers = _build_forwarded_headers(request)
    body = await request.body()

    client = await _get_http_client()

    try:
        response = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body if body else None,
        )
    except httpx.ConnectError:
        logger.warning("Cannot connect to app %s on port %d", app_name, port)
        return _error_page(
            503, "App Unavailable",
            f"Cannot connect to app '{app_name}'. It may be starting up.",
        )
    except httpx.TimeoutException:
        logger.warning("Timeout proxying to app %s on port %d", app_name, port)
        return _error_page(504, "Gateway Timeout", f"App '{app_name}' did not respond in time.")
    except Exception as e:
        logger.error("Proxy error for app %s: %s", app_name, e)
        return _error_page(502, "Bad Gateway", f"Error communicating with app '{app_name}'.")

    # Build response, forwarding status code and headers
    excluded_headers = {"transfer-encoding", "content-encoding", "content-length"}
    response_headers = {
        k: v for k, v in response.headers.items()
        if k.lower() not in excluded_headers
    }

    content = response.content
    content_type = response.headers.get("content-type", "")
    # FastAPI's Swagger UI HTML references /openapi.json as an absolute path.
    # Rewrite it so the browser fetches the app's schema through the proxy.
    if "text/html" in content_type and path in ("docs", "redoc"):
        content = content.replace(
            b'"/openapi.json"',
            f'"/apps/{app_name}/openapi.json"'.encode(),
        )
        response_headers.pop("content-length", None)

    return Response(
        content=content,
        status_code=response.status_code,
        headers=response_headers,
    )


# Also handle the bare /apps/{app_name} (no trailing slash) → redirect to /apps/{app_name}/
@router.get("/apps/{app_name}")
async def proxy_root_redirect(request: Request, app_name: str):
    """Redirect /apps/{app_name} to /apps/{app_name}/ for consistent routing."""
    app_entry, error = _lookup_app(app_name)
    if error:
        return error
    return Response(
        status_code=307,
        headers={"location": f"/apps/{app_name}/"},
    )


# ---------------------------------------------------------------------------
# WebSocket Proxy
# ---------------------------------------------------------------------------

@router.websocket("/apps/{app_name}/{path:path}")
async def proxy_websocket(websocket: WebSocket, app_name: str, path: str):
    """Proxy WebSocket connections to app web UIs."""
    import aiohttp

    app_entry, error = _lookup_app_for_ws(app_name)
    if not app_entry:
        await websocket.close(code=1008, reason="App unavailable")
        return

    port = app_entry.runtime_info.assigned_port
    target_url = f"http://localhost:{port}/{path}"

    # Preserve query string for WebSocket connections
    query = websocket.url.query
    if query:
        target_url += f"?{query}"

    await websocket.accept()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(target_url) as ws_backend:
                # Bidirectional message relay
                async def forward_client_to_backend():
                    try:
                        while True:
                            data = await websocket.receive()
                            if "text" in data:
                                await ws_backend.send_str(data["text"])
                            elif "bytes" in data:
                                await ws_backend.send_bytes(data["bytes"])
                            else:
                                break
                    except WebSocketDisconnect:
                        await ws_backend.close()
                    except Exception:
                        pass

                async def forward_backend_to_client():
                    try:
                        async for msg in ws_backend:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await websocket.send_text(msg.data)
                            elif msg.type == aiohttp.WSMsgType.BINARY:
                                await websocket.send_bytes(msg.data)
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
                    except Exception:
                        pass

                await asyncio.gather(
                    forward_client_to_backend(),
                    forward_backend_to_client(),
                )
    except aiohttp.ClientError as e:
        logger.warning("WebSocket proxy failed for app %s: %s", app_name, e)
    except Exception as e:
        logger.error("WebSocket proxy error for app %s: %s", app_name, e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


def _lookup_app_for_ws(app_name: str):
    """Lookup for WebSocket — returns (app_entry, None) or (None, reason)."""
    if app_manager is None:
        return None, "Platform not initialized"

    app_entry = app_manager.registry.get_app_by_name(app_name)
    if not app_entry:
        return None, "App not found"
    if not app_entry.manifest.config.has_web_ui:
        return None, "No web UI"
    if app_entry.status != "running":
        return None, "App not running"
    if not app_entry.runtime_info or not app_entry.runtime_info.assigned_port:
        return None, "No port"

    return app_entry, None
