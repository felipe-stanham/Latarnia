"""
Example Full App — Demonstrates all Latarnia capabilities.

This app exercises every platform feature:
- Database: connects via --db-url, queries items/events/tags tables
- MCP server: tools that read/write the database
- Web UI: HTML dashboard served at / with live data from the API
- Redis Streams: publishes events on item creation, subscribes to commands
- Logging: stdout only (Latarnia routes to journald on Linux per P-0005 Scope 4)
- Data: persists app state to --data-dir
- Dependency: requires example_companion >= 1.0.0

Usage:
    python app.py --port 8101 --mcp-port 9001 \
        --db-url postgresql://... --redis-url redis://localhost:6379/0 \
        --data-dir /opt/latarnia/data
"""

import argparse
import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import redis
except ImportError:
    redis = None
import uvicorn
from contextvars import ContextVar
from html import escape as html_escape
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from mcp import types as mcp_types
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount

# ---------------------------------------------------------------------------
# P-0008 role awareness
# ---------------------------------------------------------------------------
# Latarnia/Caddy injects X-Latarnia-App-Role on proxied webUI requests, and the
# MCP gateway forwards it on the SSE connection to this app's MCP server. The
# header is trusted because app ports are not externally reachable (ufw). When
# absent (e.g. direct access in dev with no auth in front), we default to
# "full" so the app stays fully usable — apps may ignore the header entirely.
ROLE_HEADER = "x-latarnia-app-role"
DEFAULT_ROLE = "full"
WRITE_ROLES = {"webUI-med", "webUI-full", "full"}        # may create via webUI
DESTRUCTIVE_MCP_ROLES = {"webUI-full", "full"}            # may run write MCP tools

# Per-connection role for the in-flight MCP SSE session (set in handle_sse).
_mcp_role: ContextVar[str] = ContextVar("mcp_role", default=DEFAULT_ROLE)

# ---------------------------------------------------------------------------
# Global state (set by main())
# ---------------------------------------------------------------------------

db_url: Optional[str] = None
redis_url: Optional[str] = None
mcp_port: Optional[int] = None
data_dir: Optional[Path] = None
api_key: Optional[str] = None  # EXAMPLE_API_KEY — injected via secrets.env

logger = logging.getLogger("example_full_app")


def setup_logging():
    """Log to stdout only. Latarnia routes stdout to journald (Linux) or
    captures it to a file (Darwin); apps don't manage their own log files
    (P-0005 Scope 4)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db_connection():
    """Get a psycopg database connection. Returns None if no db_url."""
    if not db_url:
        return None
    try:
        import psycopg
        return psycopg.connect(db_url)
    except Exception as e:
        logger.error("Database connection failed: %s", e)
        return None


def db_list_items():
    """Fetch all items from the database."""
    conn = _get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, description, status, created_at FROM items ORDER BY id")
            rows = cur.fetchall()
            return [
                {
                    "id": r[0], "name": r[1], "description": r[2],
                    "status": r[3], "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to list items: %s", e)
        return []
    finally:
        conn.close()


def db_add_item(name: str, description: str = "") -> dict:
    """Insert an item and return it."""
    conn = _get_db_connection()
    if not conn:
        raise RuntimeError("Database not available")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO items (name, description) VALUES (%s, %s) RETURNING id, name, created_at",
                (name, description),
            )
            row = cur.fetchone()
            conn.commit()
            return {"id": row[0], "name": row[1], "created_at": row[2].isoformat()}
    except Exception as e:
        conn.rollback()
        logger.error("Failed to add item: %s", e)
        raise
    finally:
        conn.close()


def db_list_events():
    """Fetch all events from the database."""
    conn = _get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, item_id, event_type, payload, created_at FROM events ORDER BY id DESC LIMIT 50")
            rows = cur.fetchall()
            return [
                {
                    "id": r[0], "item_id": r[1], "event_type": r[2],
                    "payload": r[3], "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ]
    except Exception as e:
        logger.error("Failed to list events: %s", e)
        return []
    finally:
        conn.close()


def db_add_event(item_id: int, event_type: str, payload: dict):
    """Record an event in the database."""
    conn = _get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (item_id, event_type, payload) VALUES (%s, %s, %s)",
                (item_id, event_type, json.dumps(payload)),
            )
            conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("Failed to add event: %s", e)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Data directory helpers
# ---------------------------------------------------------------------------

def _get_state_file() -> Optional[Path]:
    if not data_dir:
        return None
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "state.json"


def load_state() -> dict:
    """Load persistent app state from data directory."""
    state_file = _get_state_file()
    if state_file and state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass
    return {"items_created": 0, "events_published": 0, "started_at": datetime.now().isoformat()}


def save_state(state: dict):
    """Persist app state to data directory."""
    state_file = _get_state_file()
    if state_file:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2))


# App state (loaded on startup)
app_state = {}


# ---------------------------------------------------------------------------
# Redis Streams helpers
# ---------------------------------------------------------------------------

def publish_item_event(item: dict):
    """Publish an item creation event to the declared stream and pub/sub."""
    if not redis_url:
        return
    try:
        r = redis.from_url(redis_url)
        event_payload = {
            "source": "example_full_app",
            "timestamp": int(datetime.now().timestamp()),
            "version": "1.0",
            "data": {"type": "item_created", "item": item},
        }
        # Publish to Redis Stream (for stream subscribers)
        stream_key = "latarnia:streams:example.events.created"
        r.xadd(stream_key, {
            "source": event_payload["source"],
            "timestamp": str(event_payload["timestamp"]),
            "version": event_payload["version"],
            "data": json.dumps(event_payload["data"]),
        })
        # Publish to pub/sub channel (for dashboard recent activity)
        r.publish("latarnia:events:app", json.dumps(event_payload))
        logger.info("Published item_created event for item %s", item.get("id"))
    except Exception as e:
        logger.error("Failed to publish event: %s", e)


def _stream_subscriber(redis_url_str: str):
    """Background thread: consume commands from the subscribed stream."""
    try:
        r = redis.from_url(redis_url_str)
        stream_key = "latarnia:streams:example.commands.process"
        group = "example_full_app"
        consumer = "example_full_app-1"

        # Ensure consumer group exists (may already be created by platform)
        try:
            r.xgroup_create(stream_key, group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        logger.info("Stream subscriber started on %s (group: %s)", stream_key, group)

        while True:
            try:
                messages = r.xreadgroup(group, consumer, {stream_key: ">"}, count=10, block=5000)
                for stream, entries in messages:
                    for msg_id, data in entries:
                        payload = data.get(b"data", b"{}").decode()
                        logger.info("Received command: %s", payload)
                        r.xack(stream_key, group, msg_id)
            except Exception as e:
                logger.warning("Stream read error: %s", e)
                time.sleep(5)
    except Exception as e:
        logger.error("Stream subscriber failed: %s", e)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

rest_app = FastAPI(title="Example Full App")


@rest_app.get("/health")
async def health():
    return {
        "health": "good",
        "message": "Example full app is running",
        "extra_info": {
            "mcp_tools": 3,
            "db_connected": db_url is not None,
            "streams_active": redis_url is not None,
            "api_key_configured": api_key is not None,
            "items_created": app_state.get("items_created", 0),
            "data_dir": str(data_dir) if data_dir else None,
            "last_check": datetime.now().isoformat(),
        },
    }


@rest_app.get("/ui")
async def ui_resources():
    """Return list of browsable resources for the legacy UI modal."""
    return ["items", "events"]


# T-0004: public bundle route — no auth required.
# Echoes any received X-Latarnia-* request headers in the response body so
# acceptance tests can verify that Caddy strips identity headers on public blocks.
@rest_app.get("/b/test")
async def public_bundle_test(request: Request):
    latarnia_headers = {
        k: v for k, v in request.headers.items()
        if k.lower().startswith("x-latarnia-")
    }
    return {"ok": True, "latarnia_headers_received": latarnia_headers}


@rest_app.get("/api/items/{item_id}")
async def get_item(item_id: int):
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not available")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, status, created_at FROM items WHERE id = %s",
                (item_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Item not found")
            item = {
                "id": row[0], "name": row[1], "description": row[2],
                "status": row[3], "created_at": row[4].isoformat() if row[4] else None,
            }
            # Fetch tags
            cur.execute(
                "SELECT t.name FROM tags t JOIN item_tags it ON t.id = it.tag_id WHERE it.item_id = %s ORDER BY t.name",
                (item_id,),
            )
            item["tags"] = [r[0] for r in cur.fetchall()]
            # Fetch related events
            cur.execute(
                "SELECT id, event_type, payload, created_at FROM events WHERE item_id = %s ORDER BY id DESC",
                (item_id,),
            )
            item["events"] = [
                {"id": r[0], "event_type": r[1], "payload": r[2],
                 "created_at": r[3].isoformat() if r[3] else None}
                for r in cur.fetchall()
            ]
            return item
    finally:
        conn.close()


@rest_app.get("/api/items")
async def list_items():
    return db_list_items()


@rest_app.post("/api/items")
async def create_item(name: str, description: str = ""):
    try:
        item = db_add_item(name, description)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Record event in DB
    db_add_event(item["id"], "created", {"name": name})

    # Publish to Redis Stream
    publish_item_event(item)

    # Update app state
    app_state["items_created"] = app_state.get("items_created", 0) + 1
    app_state["events_published"] = app_state.get("events_published", 0) + 1
    save_state(app_state)

    return item


@rest_app.get("/api/events/{event_id}")
async def get_event(event_id: int):
    conn = _get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database not available")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.item_id, e.event_type, e.payload, e.created_at, i.name as item_name "
                "FROM events e LEFT JOIN items i ON e.item_id = i.id WHERE e.id = %s",
                (event_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Event not found")
            return {
                "id": row[0], "item_id": row[1], "event_type": row[2],
                "payload": row[3], "created_at": row[4].isoformat() if row[4] else None,
                "item_name": row[5],
            }
    finally:
        conn.close()


@rest_app.get("/api/events")
async def list_events():
    return db_list_events()


@rest_app.get("/api/state")
async def get_state():
    return app_state


# ---------------------------------------------------------------------------
# Web UI (served on the same port, proxied via /apps/example_full_app/)
# ---------------------------------------------------------------------------

WEB_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Example Full App</title>
    <style>
        body { font-family: sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
        table { border-collapse: collapse; width: 100%; margin-bottom: 1rem; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background: #f4f4f4; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
                 font-size: 0.8rem; color: white; background: #0d6efd; }
        .section { margin-bottom: 2rem; }
        form { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
        input { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; }
        button { padding: 6px 16px; background: #0d6efd; color: white; border: none;
                 border-radius: 4px; cursor: pointer; }
        button:hover { background: #0b5ed7; }
        #status { color: #666; font-size: 0.9rem; }
    </style>
</head>
<body>
    <h1>Example Full App <span class="badge">v1.0.0</span></h1>
    <p id="status">Loading...</p>
    <!--ROLE_BANNER-->

    <!--ADD_ITEM-->

    <div class="section">
        <h2>Items</h2>
        <table>
            <thead><tr><th>ID</th><th>Name</th><th>Description</th><th>Status</th><th>Created</th></tr></thead>
            <tbody id="items"><tr><td colspan="5">Loading...</td></tr></tbody>
        </table>
    </div>

    <div class="section">
        <h2>Recent Events</h2>
        <table>
            <thead><tr><th>ID</th><th>Item</th><th>Type</th><th>Created</th></tr></thead>
            <tbody id="events"><tr><td colspan="4">Loading...</td></tr></tbody>
        </table>
    </div>

    <div class="section">
        <h2>Capabilities</h2>
        <ul>
            <li>Database (Postgres) with 3 migrations</li>
            <li>MCP server exposing 3 tools: list_items, add_item, get_status</li>
            <li>Redis Streams: publishes <code>example.events.created</code>,
                subscribes to <code>example.commands.process</code></li>
            <li>Secret: <code>EXAMPLE_API_KEY</code> (operator-configured via secrets.env)</li>
            <li>Depends on <code>example_companion</code> &ge; 1.0.0</li>
        </ul>
    </div>

    <!--ADMIN-->

    <script>
        async function loadData() {
            try {
                const [items, events, state] = await Promise.all([
                    fetch('api/items').then(r => r.json()),
                    fetch('api/events').then(r => r.json()),
                    fetch('api/state').then(r => r.json()),
                ]);
                renderItems(items);
                renderEvents(events);
                document.getElementById('status').textContent =
                    `DB connected: ${state.items_created ?? 0} items created | ` +
                    `${state.events_published ?? 0} events published`;
            } catch (e) {
                document.getElementById('status').textContent = 'Error loading data: ' + e.message;
            }
        }

        function renderItems(items) {
            const tbody = document.getElementById('items');
            if (!items.length) { tbody.innerHTML = '<tr><td colspan="5">No items yet</td></tr>'; return; }
            tbody.innerHTML = items.map(i =>
                `<tr><td>${i.id}</td><td>${i.name}</td><td>${i.description || ''}</td>` +
                `<td>${i.status || ''}</td><td>${i.created_at || ''}</td></tr>`
            ).join('');
        }

        function renderEvents(events) {
            const tbody = document.getElementById('events');
            if (!events.length) { tbody.innerHTML = '<tr><td colspan="4">No events yet</td></tr>'; return; }
            tbody.innerHTML = events.map(e =>
                `<tr><td>${e.id}</td><td>${e.item_id}</td><td>${e.event_type}</td><td>${e.created_at || ''}</td></tr>`
            ).join('');
        }

        async function addItem(evt) {
            evt.preventDefault();
            const name = document.getElementById('itemName').value;
            const desc = document.getElementById('itemDesc').value;
            try {
                await fetch(`api/items?name=${encodeURIComponent(name)}&description=${encodeURIComponent(desc)}`,
                    { method: 'POST' });
                document.getElementById('itemName').value = '';
                document.getElementById('itemDesc').value = '';
                loadData();
            } catch (e) {
                alert('Failed to add item: ' + e.message);
            }
        }

        loadData();
    </script>
</body>
</html>"""


# Role-conditioned fragments injected into the page by render_web_ui().
ADD_ITEM_HTML = """    <div class="section">
        <h2>Add Item</h2>
        <form onsubmit="addItem(event)">
            <input type="text" id="itemName" placeholder="Item name" required>
            <input type="text" id="itemDesc" placeholder="Description">
            <button type="submit">Add</button>
        </form>
    </div>"""

ADMIN_HTML = """    <div class="section">
        <h2>Admin</h2>
        <p>Full-access controls (visible only with role <code>full</code>).</p>
    </div>"""


def render_web_ui(role: str) -> str:
    """Render the page adjusted for the caller's X-Latarnia-App-Role.

    - write actions (Add Item) hidden below webUI-med (cap-023)
    - admin section shown only for `full`
    Unknown/absent role defaults to `full` (backward compatible).
    """
    show_write = role in WRITE_ROLES
    show_admin = role == "full"
    # Escape: the header is trusted in prod, but apps copy this fixture and may
    # run with the port exposed in dev — model the safe pattern.
    banner = (f'<p style="color:#0b5ed7">Your access level: '
              f'<strong>{html_escape(role)}</strong></p>')
    html = WEB_UI_HTML
    html = html.replace("<!--ROLE_BANNER-->", banner)
    html = html.replace("<!--ADD_ITEM-->", ADD_ITEM_HTML if show_write else "")
    html = html.replace("<!--ADMIN-->", ADMIN_HTML if show_admin else "")
    return html


@rest_app.get("/", response_class=HTMLResponse)
async def web_ui_root(request: Request):
    role = request.headers.get(ROLE_HEADER, DEFAULT_ROLE)
    return render_web_ui(role)


@rest_app.get("/index.html", response_class=HTMLResponse)
async def web_ui_index(request: Request):
    role = request.headers.get(ROLE_HEADER, DEFAULT_ROLE)
    return render_web_ui(role)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp_server = Server("example-full-app")


@mcp_server.list_tools()
async def list_tools():
    # Role-aware tool list (cap-023): the write tool `add_item` is only exposed
    # to roles allowed to mutate (webUI-full / full). Read tools always show.
    tools = [
        mcp_types.Tool(
            name="list_items",
            description="List all items in the database",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        mcp_types.Tool(
            name="get_status",
            description="Get the current status of the example app",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]
    if _mcp_role.get() in DESTRUCTIVE_MCP_ROLES:
        tools.insert(1, mcp_types.Tool(
            name="add_item",
            description="Add a new item to the database",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Item name"},
                    "description": {"type": "string", "description": "Item description"},
                },
                "required": ["name"],
            },
        ))
    return tools


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "list_items":
        items = db_list_items()
        return [{"type": "text", "text": json.dumps(items, default=str)}]

    if name == "add_item":
        # Enforce role even if a client calls the tool without listing it first.
        if _mcp_role.get() not in DESTRUCTIVE_MCP_ROLES:
            return [{"type": "text",
                     "text": f"Error: role '{_mcp_role.get()}' may not add items"}]
        item_name = arguments.get("name", "Unnamed")
        description = arguments.get("description", "")
        try:
            item = db_add_item(item_name, description)
            db_add_event(item["id"], "created_via_mcp", {"name": item_name})
            publish_item_event(item)
            app_state["items_created"] = app_state.get("items_created", 0) + 1
            app_state["events_published"] = app_state.get("events_published", 0) + 1
            save_state(app_state)
            return [{"type": "text", "text": json.dumps(item, default=str)}]
        except Exception as e:
            return [{"type": "text", "text": f"Error: {e}"}]

    if name == "get_status":
        return [{"type": "text", "text": json.dumps({
            "health": "good",
            "db_connected": db_url is not None,
            "redis_connected": redis_url is not None,
            "mcp_port": mcp_port,
            "tools": 3,
            "data_dir": str(data_dir) if data_dir else None,
            "state": app_state,
        }, default=str)}]

    raise ValueError(f"Unknown tool: {name}")


def _build_mcp_app() -> Starlette:
    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(scope, receive, send):
        # Capture the role the gateway forwarded for this connection so the
        # tool handlers can apply role-aware behaviour (cap-016/cap-021).
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        role = headers.get(ROLE_HEADER, DEFAULT_ROLE)
        role_token = _mcp_role.set(role)
        try:
            async with sse_transport.connect_sse(
                scope, receive, send
            ) as streams:
                await mcp_server.run(
                    streams[0], streams[1],
                    mcp_server.create_initialization_options(),
                )
        finally:
            _mcp_role.reset(role_token)

    sse_route = Route("/sse", endpoint=lambda _: None)
    sse_route.app = handle_sse

    return Starlette(
        routes=[
            sse_route,
            Mount("/messages/", app=sse_transport.handle_post_message),
        ]
    )


def _run_mcp_server(port: int):
    mcp_app = _build_mcp_app()
    logger.info("MCP server starting on port %d", port)
    uvicorn.run(mcp_app, host="0.0.0.0", port=port, log_level="warning")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    global db_url, redis_url, mcp_port, data_dir, app_state, api_key

    parser = argparse.ArgumentParser(description="Example Full App")
    parser.add_argument("--port", type=int, default=8101, help="REST API port")
    parser.add_argument("--mcp-port", type=int, default=9001, help="MCP server port")
    parser.add_argument("--db-url", type=str, default=None, help="Postgres connection URL")
    parser.add_argument("--redis-url", type=str, default=None, help="Redis connection URL")
    parser.add_argument("--data-dir", type=str, default=None, help="Data directory")
    args = parser.parse_args()

    raw_db_url = args.db_url
    if raw_db_url and raw_db_url.startswith("env:"):
        db_url = os.environ.get(raw_db_url[4:])
    else:
        db_url = raw_db_url
    redis_url = args.redis_url
    mcp_port = args.mcp_port
    data_dir = Path(args.data_dir) if args.data_dir else None
    # Injected by Latarnia SecretManager via EnvironmentFile= (Linux) or Popen env= (Darwin).
    api_key = os.environ.get("EXAMPLE_API_KEY")

    # Setup logging (stdout only — Latarnia routes to journald on Linux).
    setup_logging()

    # Load persistent state from data directory
    app_state = load_state()

    logger.info("Starting Example Full App")
    logger.info("  REST port: %d", args.port)
    logger.info("  MCP port:  %d", args.mcp_port)
    logger.info("  DB URL:    %s", "set" if db_url else "not set")
    logger.info("  Redis URL: %s", "set" if redis_url else "not set")
    logger.info("  Data dir:  %s", data_dir or "not set")
    logger.info("  API key:   %s", "set" if api_key else "not set")
    logger.info("  State:     %s", app_state)

    # Start MCP server in background thread
    mcp_thread = threading.Thread(target=_run_mcp_server, args=(args.mcp_port,), daemon=True)
    mcp_thread.start()

    # Start Redis Streams subscriber in background thread
    if redis_url:
        sub_thread = threading.Thread(target=_stream_subscriber, args=(redis_url,), daemon=True)
        sub_thread.start()

    # Save initial state
    save_state(app_state)

    # Run REST API (with web UI) on main thread
    uvicorn.run(rest_app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
