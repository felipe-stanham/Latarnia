"""
MCP Gateway for Latarnia

Aggregates MCP tools from all MCP-enabled apps and exposes them to
external clients through a single MCP server endpoint. The gateway
acts as an MCP server (SSE transport) to external clients and as an
MCP client (SSE transport) to individual app MCP servers.
"""

import asyncio
import logging
from contextvars import ContextVar
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from mcp import types as mcp_types
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount

logger = logging.getLogger("latarnia.mcp_gateway")

# Per-connection JWT claims for the in-flight MCP SSE session. Set in handle_sse
# before the MCP server runs; read by the tool list/call handlers so tools are
# scoped to the token's apps and the per-app role header can be injected.
# ContextVars propagate into the same-task handler dispatch.
_request_claims: ContextVar[Optional[dict]] = ContextVar("mcp_request_claims", default=None)


@dataclass
class ToolIndexEntry:
    """Maps a namespaced tool to its source app and routing info."""
    app_id: str
    app_name: str
    mcp_port: int
    original_tool_name: str
    tool_schema: dict  # {name, description, inputSchema}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ToolIndexEntry":
        return cls(**data)


class MCPGateway:
    """
    Platform-level MCP gateway that aggregates tools from all
    MCP-enabled apps and proxies tool calls to the appropriate app.
    """

    def __init__(self, config_manager, app_manager, jwt_auth=None, token_store=None):
        self.config_manager = config_manager
        self.app_manager = app_manager
        # P-0008: when set, the gateway requires a valid, non-revoked Bearer JWT
        # to open an MCP session, scopes the tool list to the token's apps, and
        # forwards X-Latarnia-App-Role to per-app MCP servers on tool calls.
        self.jwt_auth = jwt_auth
        self.token_store = token_store

        # Tool index: "app_name.tool_name" -> ToolIndexEntry
        self._tool_index: Dict[str, ToolIndexEntry] = {}

        # MCP server instance
        self._mcp_server: Optional[Server] = None

    async def initialize(self) -> Starlette:
        """
        Create the MCP server, register handlers, build the initial
        tool index, and return a Starlette ASGI app for mounting.
        """
        self._mcp_server = Server("latarnia-gateway")

        @self._mcp_server.list_tools()
        async def handle_list_tools():
            return self._handle_list_tools()

        @self._mcp_server.call_tool()
        async def handle_call_tool(name: str, arguments: dict):
            return await self._handle_call_tool(name, arguments)

        # Build SSE transport and Starlette app
        sse_transport = SseServerTransport("/messages/")

        async def handle_sse(scope, receive, send):
            claims_token = None
            if self.jwt_auth is not None:
                claims = self._authorize_scope(scope)
                if claims is None:
                    await self._send_401(send)
                    return
                claims_token = _request_claims.set(claims)
            try:
                async with sse_transport.connect_sse(
                    scope, receive, send
                ) as streams:
                    await self._mcp_server.run(
                        streams[0],
                        streams[1],
                        self._mcp_server.create_initialization_options(),
                    )
            finally:
                if claims_token is not None:
                    _request_claims.reset(claims_token)

        sse_route = Route("/sse", endpoint=lambda _: None)
        sse_route.app = handle_sse

        asgi_app = Starlette(
            routes=[
                sse_route,
                Mount("/messages/", app=sse_transport.handle_post_message),
            ]
        )

        # Build initial tool index from currently healthy MCP apps
        await self._build_tool_index()

        logger.info(
            "MCP gateway initialized with %d tools from %d apps",
            len(self._tool_index),
            len({e.app_id for e in self._tool_index.values()}),
        )
        return asgi_app

    # ------------------------------------------------------------------
    # Tool index management
    # ------------------------------------------------------------------

    async def _build_tool_index(self) -> None:
        """Rebuild the entire tool index from all healthy MCP-enabled apps."""
        self._tool_index.clear()

        for app_entry in self.app_manager.registry.get_all_apps():
            if not app_entry.mcp_info or not app_entry.mcp_info.enabled:
                continue
            if not app_entry.mcp_info.healthy:
                logger.debug(
                    "Skipping unhealthy MCP app %s during index build",
                    app_entry.name,
                )
                continue
            if not app_entry.mcp_info.mcp_port:
                continue

            entries = await self._fetch_tools_from_app(
                app_entry.app_id,
                app_entry.name,
                app_entry.mcp_info.mcp_port,
            )
            for entry in entries:
                namespaced = entry.tool_schema["name"]
                self._tool_index[namespaced] = entry

            # Update registered_tools in registry
            tool_names = [e.original_tool_name for e in entries]
            app_entry.mcp_info.registered_tools = tool_names
            app_entry.mcp_info.last_tool_sync = datetime.now()
            self.app_manager.registry.update_app(
                app_entry.app_id, mcp_info=app_entry.mcp_info
            )

    async def _fetch_tools_from_app(
        self, app_id: str, app_name: str, mcp_port: int,
        retries: int = 3, retry_delay: float = 2.0,
    ) -> List[ToolIndexEntry]:
        """
        Connect to an app's MCP server, call list_tools, and return
        ToolIndexEntry objects with namespaced tool names.

        Retries on failure to allow time for the app's MCP server to boot.
        """
        entries: List[ToolIndexEntry] = []
        last_error = None
        for attempt in range(1, retries + 1):
            entries = []
            try:
                from mcp.client.sse import sse_client
                from mcp.client.session import ClientSession

                # Apps run on the same host as the platform (localhost assumption, v1)
                async with sse_client(
                    f"http://localhost:{mcp_port}/sse"
                ) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.list_tools()

                        for tool in result.tools:
                            namespaced_name = f"{app_name}.{tool.name}"
                            entries.append(
                                ToolIndexEntry(
                                    app_id=app_id,
                                    app_name=app_name,
                                    mcp_port=mcp_port,
                                    original_tool_name=tool.name,
                                    tool_schema={
                                        "name": namespaced_name,
                                        "description": tool.description or "",
                                        "inputSchema": tool.inputSchema
                                        if tool.inputSchema
                                        else {"type": "object", "properties": {}},
                                    },
                                )
                            )

                logger.info(
                    "Fetched %d tools from app %s (port %d)",
                    len(entries), app_name, mcp_port,
                )
                return entries
            except Exception as e:
                last_error = e
                if attempt < retries:
                    logger.debug(
                        "Attempt %d/%d failed to fetch tools from %s on port %d: %s — retrying in %.1fs",
                        attempt, retries, app_name, mcp_port, e, retry_delay,
                    )
                    await asyncio.sleep(retry_delay)

        logger.warning(
            "Failed to fetch tools from app %s on port %d after %d attempts: %s",
            app_name, mcp_port, retries, last_error,
        )
        return entries

    # ------------------------------------------------------------------
    # MCP server handlers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Auth helpers (P-0008)
    # ------------------------------------------------------------------

    def _authorize_scope(self, scope) -> Optional[dict]:
        """Validate the Bearer JWT on an incoming MCP SSE connection.

        Returns claims for a valid, non-revoked token, else None (-> 401).
        """
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[7:].strip()
        claims = self.jwt_auth.validate(token)
        if claims is None:
            return None
        if self.token_store is not None and \
                not self.token_store.is_active(self.jwt_auth.token_hash(token)):
            return None
        return claims

    @staticmethod
    async def _send_401(send) -> None:
        body = b'{"detail":"Authentication required"}'
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _role_for_app(claims: Optional[dict], app_name: str) -> str:
        if claims is None or claims.get("super"):
            return "full"
        return claims.get("apps", {}).get(app_name, "none")

    @staticmethod
    def _in_scope(claims: Optional[dict], app_name: str) -> bool:
        """Whether the current connection may see/call tools for `app_name`."""
        if claims is None or claims.get("super"):
            return True
        return claims.get("apps", {}).get(app_name, "none") != "none"

    def _handle_list_tools(self) -> list:
        """Return tools in the index, scoped to the connection's JWT claim."""
        claims = _request_claims.get()
        tools = []
        for entry in self._tool_index.values():
            if not self._in_scope(claims, entry.app_name):
                continue
            tools.append(
                mcp_types.Tool(
                    name=entry.tool_schema["name"],
                    description=entry.tool_schema.get("description", ""),
                    inputSchema=entry.tool_schema.get(
                        "inputSchema", {"type": "object", "properties": {}}
                    ),
                )
            )
        return tools

    async def _handle_call_tool(
        self, name: str, arguments: dict
    ) -> list:
        """
        Parse the namespaced tool name, check app health, and proxy
        the call to the app's MCP server.
        """
        if name not in self._tool_index:
            return [
                mcp_types.TextContent(
                    type="text",
                    text=f"Error: Unknown tool '{name}'",
                )
            ]

        entry = self._tool_index[name]

        # Scope enforcement: a token not granted this app cannot call its tools.
        claims = _request_claims.get()
        if not self._in_scope(claims, entry.app_name):
            return [
                mcp_types.TextContent(
                    type="text",
                    text=f"Error: not authorized for app '{entry.app_name}'",
                )
            ]

        # Check app health via registry
        app = self.app_manager.registry.get_app(entry.app_id)
        if not app or not app.mcp_info or not app.mcp_info.healthy:
            return [
                mcp_types.TextContent(
                    type="text",
                    text=f"Error: App '{entry.app_name}' is currently unavailable",
                )
            ]

        # Forward the caller's role for this app to the per-app MCP server so it
        # can apply role-aware behaviour (cap-016/021). Only when auth is active.
        headers = None
        if claims is not None:
            headers = {"X-Latarnia-App-Role": self._role_for_app(claims, entry.app_name)}

        try:
            from mcp.client.sse import sse_client
            from mcp.client.session import ClientSession

            async with sse_client(
                f"http://localhost:{entry.mcp_port}/sse", headers=headers
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        entry.original_tool_name, arguments
                    )
                    return result.content
        except Exception as e:
            logger.error("Tool call '%s' failed: %s", name, e)
            return [
                mcp_types.TextContent(
                    type="text",
                    text=f"Error: Tool call failed: {e}",
                )
            ]

    # ------------------------------------------------------------------
    # App lifecycle hooks
    # ------------------------------------------------------------------

    async def on_app_started(self, app_id: str) -> bool:
        """
        Called after an app starts and its MCP health probe passes.
        Fetches tools from the app and adds them to the index.
        Returns False if backward compatibility check fails.
        """
        app = self.app_manager.registry.get_app(app_id)
        if not app or not app.mcp_info or not app.mcp_info.enabled:
            return True
        if not app.mcp_info.mcp_port:
            return True

        old_tools = list(app.mcp_info.registered_tools)

        entries = await self._fetch_tools_from_app(
            app.app_id, app.name, app.mcp_info.mcp_port
        )

        if not entries:
            logger.warning(
                "No tools fetched from app %s — MCP index not updated", app.name
            )
            return True

        new_tool_names = [e.original_tool_name for e in entries]

        # Backward compatibility check (cap-011)
        if old_tools:
            compat_ok, removed = self.check_backward_compatibility(
                old_tools, new_tool_names
            )
            if not compat_ok:
                logger.error(
                    "Backward compatibility violation for app %s: "
                    "removed tools %s. MCP tools NOT updated.",
                    app.name, removed,
                )
                app.mcp_info.healthy = False
                self.app_manager.registry.update_app(
                    app_id, mcp_info=app.mcp_info
                )
                return False

        # Remove old tools for this app from the index
        self._remove_app_from_index(app_id)

        # Add new tools
        for entry in entries:
            self._tool_index[entry.tool_schema["name"]] = entry

        # Update registry
        app.mcp_info.registered_tools = new_tool_names
        app.mcp_info.last_tool_sync = datetime.now()
        app.mcp_info.healthy = True
        self.app_manager.registry.update_app(app_id, mcp_info=app.mcp_info)

        logger.info(
            "Synced %d tools for app %s", len(entries), app.name
        )
        return True

    async def on_app_stopped(self, app_id: str) -> None:
        """Remove all tools for a stopped app from the index."""
        removed_count = self._remove_app_from_index(app_id)
        if removed_count > 0:
            logger.info(
                "Removed %d tools for stopped app %s", removed_count, app_id
            )

    def _remove_app_from_index(self, app_id: str) -> int:
        """Remove all tool index entries belonging to the given app."""
        keys_to_remove = [
            k for k, v in self._tool_index.items() if v.app_id == app_id
        ]
        for key in keys_to_remove:
            del self._tool_index[key]
        return len(keys_to_remove)

    # ------------------------------------------------------------------
    # Backward compatibility
    # ------------------------------------------------------------------

    @staticmethod
    def check_backward_compatibility(
        old_tools: List[str], new_tools: List[str]
    ) -> Tuple[bool, List[str]]:
        """
        Check that all previously registered tool names are still present.
        Returns (True, []) if compatible, (False, removed_list) otherwise.
        """
        removed = sorted(set(old_tools) - set(new_tools))
        if removed:
            return False, removed
        return True, []

    async def on_app_version_bump(self, app_id: str) -> bool:
        """
        Handle version bump for an MCP-enabled app. Fetches new tools,
        checks backward compatibility, and updates the index.
        Returns True if the bump is accepted, False if rejected.
        """
        app = self.app_manager.registry.get_app(app_id)
        if not app or not app.mcp_info or not app.mcp_info.enabled:
            return True

        return await self.on_app_started(app_id)

    # ------------------------------------------------------------------
    # Public API (for REST endpoints / dashboard)
    # ------------------------------------------------------------------

    def get_tool_index(self) -> Dict[str, dict]:
        """Return a serializable copy of the tool index."""
        return {k: v.to_dict() for k, v in self._tool_index.items()}

    def get_status(self) -> dict:
        """Return gateway status summary."""
        app_ids = {e.app_id for e in self._tool_index.values()}
        return {
            "enabled": True,
            "total_tools": len(self._tool_index),
            "connected_apps": len(app_ids),
            "app_tool_counts": {
                app_id: sum(
                    1 for e in self._tool_index.values() if e.app_id == app_id
                )
                for app_id in app_ids
            },
        }
