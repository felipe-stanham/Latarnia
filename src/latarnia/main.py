"""
Main FastAPI application for Latarnia
"""
import asyncio
import base64
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import shutil

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from latarnia.core.config import config_manager
from latarnia.core.redis_client import RedisHealthMonitor
from latarnia.core.event_subscriber import AsyncStreamConsumer
from latarnia.utils.system_monitor import SystemMonitor
from latarnia.web.dashboard import router as dashboard_router


# Module-level logger so endpoint handlers can log errors. setup_logging()
# below configures handlers/level later in lifespan; getLogger() is
# idempotent so the same logger instance is returned then.
logger = logging.getLogger("latarnia.main")


# Initialize logging
def setup_logging():
    """Setup logging configuration"""
    config = config_manager.config
    
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format=config.logging.format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                config_manager.get_logs_dir() / "latarnia-main.log"
            )
        ]
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    # Ensure required directories exist BEFORE logging setup
    config_manager.get_data_dir().mkdir(parents=True, exist_ok=True)
    config_manager.get_logs_dir().mkdir(parents=True, exist_ok=True)
    
    setup_logging()
    logger = logging.getLogger("latarnia.main")
    logger.info("Starting Latarnia main application")
    
    # Auto-start Redis if not running
    logger.info("Checking Redis status...")
    redis_status = redis_monitor.get_redis_metrics()
    if redis_status.get("status") != "connected":
        logger.warning("Redis is not running, attempting to start...")
        try:
            import subprocess
            import platform
            if platform.system() == "Darwin":  # macOS
                subprocess.run(["brew", "services", "start", "redis"],
                             capture_output=True, check=False)
                logger.info("Started Redis via brew services")
            else:  # Linux
                subprocess.run(["sudo", "systemctl", "start", "redis"],
                             capture_output=True, check=False)
                logger.info("Started Redis via systemctl")
        except Exception as e:
            logger.error(f"Failed to auto-start Redis: {e}")
    else:
        logger.info("Redis is already running")

    # Re-check Redis after potential auto-start attempt
    redis_status = redis_monitor.get_redis_metrics()
    redis_ok = redis_status.get("status") == "connected"
    if not redis_ok:
        logger.warning("Redis is not reachable — app auto-start will be skipped")

    # Check Postgres connectivity
    logger.info("Checking Postgres connectivity...")
    pg_ok = pg_client.check_connectivity()
    if pg_ok:
        logger.info("Postgres is reachable")
    else:
        logger.warning("Postgres is not reachable — app auto-start will be skipped")

    # Initialize the platform auth DB (create + migrate). Requires Postgres.
    if pg_ok:
        logger.info("Initializing platform auth DB (%s)...", auth_db.db_name)
        if auth_db.initialize():
            logger.info("Platform auth DB ready")
        else:
            logger.error("Auth DB init failed — auth endpoints will be unavailable")
    else:
        logger.warning("Skipping auth DB init — Postgres unreachable")

    # Fail loud-but-soft if the TOTP encryption key is absent: TOTP setup and
    # login can't work without it. Logged once at startup with a clear fix.
    try:
        _totp_key_loader()
    except Exception:
        logger.error(
            "LATARNIA_TOTP_ENC_KEY is missing or invalid — TOTP setup/login "
            "will fail. Add a 32-byte base64 key to secrets.env (mode 600)."
        )
    try:
        _jwt_secret_loader()
    except Exception:
        logger.error(
            "LATARNIA_JWT_SECRET is missing — machine-token issuance/validation "
            "will fail. Add it to secrets.env (mode 600)."
        )

    # Linger check: per-app user units only survive logout when linger is on.
    # Warn loudly but do not block startup — the main platform itself runs as
    # a system-scope unit and is unaffected by user-mode linger.
    import getpass
    import platform
    if platform.system() == "Linux":
        linger_user = getpass.getuser()
        if not service_manager.linger_enabled(linger_user):
            logger.warning(
                "systemd --user linger is disabled for %s. Per-app units may "
                "not survive logout. Enable with: sudo loginctl enable-linger %s",
                linger_user,
                linger_user,
            )

    # Discover apps on startup
    logger.info("Discovering applications...")
    discovered_count = app_manager.discover_apps()
    logger.info(f"Discovered {discovered_count} applications")

    # Clean up apps whose folders were deleted since last run
    orphan_count = await _cleanup_orphaned_apps()
    if orphan_count:
        logger.info("Cleaned up %d orphaned app(s) on startup", orphan_count)

    # Reconcile the registry with surviving per-app systemd units (Linux
    # only). Per-app units have independent lifetimes, so they typically
    # survive a platform restart. Without this step the dashboard shows
    # them as "stopped" and HealthMonitor skips them.
    reconciled = service_manager.reconcile_running_units()
    if reconciled:
        logger.info(f"Reconciled {reconciled} already-running service apps")

    # Auto-start service apps with auto_start=true. The launcher is chosen
    # per-app (OS, type) by pick_launcher: Linux+service → systemd, Darwin →
    # subprocess fallback. Apps already RUNNING (reconciled above) are
    # skipped — their unit/process is already up.
    # Per-app infra guard: skip apps whose declared service requirements
    # (redis_required, database) cannot be satisfied right now.
    logger.info("Auto-starting service apps...")
    auto_start_count = 0
    for app_entry in app_manager.registry.get_all_apps():
        if app_entry.type != AppType.SERVICE or not app_entry.manifest.config.auto_start:
            continue
        if app_entry.status == AppStatus.RUNNING:
            logger.info(f"Skipping auto-start of {app_entry.name}: already running (reconciled)")
            continue
        if not redis_ok and app_entry.manifest.config.redis_required:
            logger.warning(
                "Skipping auto-start of %s: Redis not reachable and app declares redis_required",
                app_entry.name,
            )
            continue
        if not pg_ok and app_entry.manifest.config.database:
            logger.warning(
                "Skipping auto-start of %s: Postgres not reachable and app declares database",
                app_entry.name,
            )
            continue
        logger.info(f"Auto-starting service app: {app_entry.name} ({app_entry.app_id})")
        launcher = pick_launcher(app_entry)
        if launcher.start_service(app_entry.app_id):
            auto_start_count += 1
            logger.info(f"Successfully started {app_entry.name}")
        else:
            logger.error(f"Failed to start {app_entry.name}")
    logger.info(f"Auto-started {auto_start_count} service apps")

    # Initialize MCP gateway if enabled
    if mcp_gateway:
        logger.info("Initializing MCP gateway...")
        mcp_asgi_app = await mcp_gateway.initialize()
        gateway_path = config_manager.config.mcp.gateway_path
        app.mount(gateway_path, mcp_asgi_app)
        logger.info(f"MCP gateway mounted at {gateway_path}")

        # Sync tools for any auto-started MCP-enabled apps
        for app_entry in app_manager.registry.get_all_apps():
            if (
                app_entry.mcp_info
                and app_entry.mcp_info.enabled
                and app_entry.mcp_info.healthy
            ):
                await mcp_gateway.on_app_started(app_entry.app_id)

    # Generate the Caddyfile from current registry state and reload Caddy.
    # Runs after auto-start so running apps already have assigned ports and
    # get webUI route blocks. A reload failure (e.g. Caddy not installed in
    # local dev) is logged but does not block platform startup.
    logger.info("Generating Caddy configuration...")
    try:
        caddy_manager.generate_config()
        if caddy_manager.reload():
            logger.info("Caddy configuration loaded")
        else:
            logger.warning("Caddy reload did not succeed (see logs above)")
    except Exception as exc:
        logger.error("Caddy config generation failed: %s", exc)

    # Start Redis stream consumer
    logger.info("Starting Redis stream consumer...")
    await event_subscriber.start()

    # Start health monitoring. The dashboard's combined `overall_status`
    # (P-0005 cap-005) needs this loop running to refresh /health results.
    # Without it the dashboard stays at "yellow / unreachable" even when
    # apps are fine.
    logger.info("Starting health monitor...")
    await health_monitor.start_monitoring()

    logger.info("Latarnia main application started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Latarnia main application")
    logger.info("Stopping health monitor...")
    await health_monitor.stop_monitoring()
    logger.info("Stopping Redis stream consumer...")
    await event_subscriber.stop()
    logger.info("Stopping all managed service apps...")
    subprocess_launcher.stop_all()
    logger.info("Stopping all Streamlit apps...")
    streamlit_manager.stop_all()
    logger.info("Shutdown complete")


# Initialize components at module level for testing
system_monitor = SystemMonitor()
redis_monitor = RedisHealthMonitor(config_manager.get_redis_url())
event_subscriber = AsyncStreamConsumer(
    config_manager.get_redis_url(),
    max_events=config_manager.config.event_subscriber.max_events,
)

# Initialize app management components
from .managers import AppManager, AppStatus, AppType, PortManager, ServiceManager
from .managers.health_monitor import HealthMonitor
from .managers.secret_manager import SecretManager
from .managers.subprocess_launcher import SubprocessLauncher
from .managers.streamlit_manager import StreamlitManager
from .core.pg_client import PgClient
from .managers.db_provisioner import DbProvisioner
from .managers.stream_manager import StreamManager

pg_client = PgClient(config_manager)
db_provisioner = DbProvisioner(config_manager, pg_client)
stream_manager = StreamManager(config_manager)

port_manager = PortManager(config_manager)
app_manager = AppManager(
    config_manager, port_manager,
    db_provisioner=db_provisioner, stream_manager=stream_manager,
)
# P-0006: SecretManager owns the per-env master secrets file and per-app
# filtered files. Wired into both launchers so refuse-to-start + injection
# work uniformly across (Linux+systemd) and (macOS+subprocess) paths.
secret_manager = SecretManager(config_manager, app_manager)
service_manager = ServiceManager(
    config_manager, app_manager, port_manager,
    secret_manager=secret_manager,
)
health_monitor = HealthMonitor(config_manager, app_manager, service_manager)
subprocess_launcher = SubprocessLauncher(
    config_manager, app_manager, port_manager,
    secret_manager=secret_manager,
)
streamlit_manager = StreamlitManager(config_manager, app_manager, port_manager)


from .managers.launcher_router import pick_launcher as _pick_launcher_router


def pick_launcher(app_entry):
    """Pick the lifecycle launcher for `app_entry`. See launcher_router.py."""
    return _pick_launcher_router(
        app_entry, service_manager, subprocess_launcher, streamlit_manager,
    )

# Initialize MCP gateway (conditional on config)
from .managers.mcp_gateway import MCPGateway

mcp_gateway: Optional[MCPGateway] = None
if config_manager.config.mcp.enabled:
    mcp_gateway = MCPGateway(config_manager, app_manager)
    app_manager.mcp_gateway = mcp_gateway

# Initialize Caddy config manager (P-0008). Caddy is now the single ingress
# and reverse proxy; the old Python web_proxy has been removed. The manager
# regenerates the per-env Caddyfile from registry state on app lifecycle
# events and asks Caddy to reload.
from .caddy import CaddyConfigManager

caddy_manager = CaddyConfigManager(config_manager, app_manager)
app_manager.caddy_manager = caddy_manager

# Initialize auth foundation (P-0008 Scope 2). Platform auth state lives in
# `latarnia_platform_{env}`; the DB is created + migrated at startup.
from .auth import AuthDB
from .auth.users import UserStore
from .auth.sessions import SessionStore
from .auth.roles import RoleStore
from .auth.providers import TOTPAuthProvider
from .auth.jwt_auth import JWTAuth
from .auth.tokens import MachineTokenStore
from .auth.middleware import JWTAuthMiddleware
from .auth.routes import build_auth_router, resolve_session_user


def _load_platform_secret(name: str) -> Optional[str]:
    """Read a platform-wide secret from the env, then the master secrets file.

    These (LATARNIA_TOTP_ENC_KEY, LATARNIA_JWT_SECRET) are platform-level, not
    per-app, so they are read directly from the master file rather than via the
    per-app filtered view. Never logged.
    """
    val = os.environ.get(name)
    if not val:
        val = secret_manager.load().get(name)
    return val


def _totp_key_loader() -> bytes:
    raw = _load_platform_secret("LATARNIA_TOTP_ENC_KEY")
    if not raw:
        raise ValueError(
            "LATARNIA_TOTP_ENC_KEY is not set (add it to secrets.env, mode 600)"
        )
    return base64.b64decode(raw)


def _jwt_secret_loader() -> str:
    raw = _load_platform_secret("LATARNIA_JWT_SECRET")
    if not raw:
        raise ValueError(
            "LATARNIA_JWT_SECRET is not set (add it to secrets.env, mode 600)"
        )
    return raw


auth_db = AuthDB(config_manager, pg_client)
user_store = UserStore(auth_db)
session_store = SessionStore(auth_db, config_manager)
role_store = RoleStore(auth_db, user_store)
totp_provider = TOTPAuthProvider(
    auth_db, _totp_key_loader, issuer=config_manager.config.auth.totp_issuer
)
jwt_auth = JWTAuth(_jwt_secret_loader)
token_store = MachineTokenStore(auth_db, jwt_auth)
# Enforce JWT on the MCP gateway (cap-021): Bearer required, tool list scoped,
# X-Latarnia-App-Role forwarded to per-app MCP servers.
if mcp_gateway is not None:
    mcp_gateway.jwt_auth = jwt_auth
    mcp_gateway.token_store = token_store
auth_router = build_auth_router(
    auth_db, user_store, session_store, totp_provider, config_manager,
    role_store=role_store, app_manager=app_manager,
    jwt_auth=jwt_auth, token_store=token_store,
)


def _session_user(request):
    """Resolve the browser session's user (cookie-only), or None.

    Delegates to the auth module's single resolver so dashboard data scoping
    (e.g. /api/apps) and the auth router never diverge. Behind Caddy the
    session cookie is forwarded, so this works there too.
    """
    return resolve_session_user(
        request, session_store, user_store, config_manager.config.auth.cookie_name
    )


# Create FastAPI app
app = FastAPI(
    title="Latarnia",
    description="Unified home automation platform for Raspberry Pi",
    version="0.1.0",
    lifespan=lifespan
)

# Auth gate for /api/* (P-0008 Scope 4): require a valid Bearer JWT or session
# cookie. Pure-ASGI so /auth/*, /mcp (SSE) and websockets pass through
# untouched. MCP auth is enforced inside the gateway.
app.add_middleware(
    JWTAuthMiddleware,
    jwt_auth=jwt_auth,
    token_store=token_store,
    session_store=session_store,
    cookie_name=config_manager.config.auth.cookie_name,
)

# Include auth routes (/auth/* and /api/auth/*) — /auth/* must be reachable
# without a session (Caddy routes /auth/* publicly; verify is the forward_auth
# target). /api/auth/* is gated by the middleware above.
app.include_router(auth_router)

# Include web dashboard routes
app.include_router(dashboard_router)


async def _cleanup_orphaned_apps() -> int:
    """Stop, remove units, and unregister apps whose app folder has been deleted."""
    orphans = app_manager.get_orphaned_apps()
    count = 0
    for app_entry in orphans:
        logger.warning(
            "Orphaned app detected: %s (path %s missing) — cleaning up",
            app_entry.app_id, app_entry.path,
        )
        try:
            if app_entry.type == AppType.SERVICE:
                pick_launcher(app_entry).stop_service(app_entry.app_id)
            else:
                streamlit_manager.stop_streamlit_app(app_entry.app_id)
        except Exception as exc:
            logger.warning("Could not stop orphaned app %s: %s", app_entry.app_id, exc)
        service_manager.remove_service(app_entry.app_id)
        if app_entry.runtime_info.assigned_port:
            port_manager.release_port(app_entry.app_id)
        if app_entry.mcp_info and app_entry.mcp_info.mcp_port:
            port_manager.release_mcp_port(app_entry.app_id)
        app_manager.unregister_app(app_entry.app_id)
        count += 1
        logger.info("Cleaned up orphaned app: %s", app_entry.app_id)
    return count


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "Latarnia is running", "version": "0.1.0"}


@app.get("/health")
async def health_check():
    """Main application health check"""
    try:
        config = config_manager.config
        
        # Get system metrics
        hardware_metrics = system_monitor.get_hardware_metrics()
        redis_metrics = redis_monitor.get_redis_metrics()
        pg_metrics = pg_client.get_postgres_metrics()

        # Determine overall health
        health_status = "good"
        issues = []

        # Check hardware thresholds
        if "error" in hardware_metrics:
            health_status = "error"
            issues.append("Hardware monitoring failed")
        else:
            cpu_usage = hardware_metrics.get("cpu", {}).get("usage_percent", 0)
            memory_usage = hardware_metrics.get("memory", {}).get("percent", 0)
            disk_usage = hardware_metrics.get("disk", {}).get("percent", 0)

            if cpu_usage > 80:
                health_status = "warning"
                issues.append(f"High CPU usage: {cpu_usage}%")
            if memory_usage > 85:
                health_status = "warning"
                issues.append(f"High memory usage: {memory_usage}%")
            if disk_usage > 90:
                health_status = "warning"
                issues.append(f"High disk usage: {disk_usage}%")

        # Check Redis connection
        if redis_metrics.get("status") != "connected":
            health_status = "error"
            issues.append("Redis connection failed")

        # Check Postgres connection
        if pg_metrics.get("status") != "connected":
            health_status = "error"
            issues.append("Postgres connection failed")

        return {
            "health": health_status,
            "message": "System operational" if not issues else "; ".join(issues),
            "extra_info": {
                "hardware": hardware_metrics,
                "redis": redis_metrics,
                "postgres": pg_metrics,
                "config_loaded": config is not None,
                "data_dir_exists": config_manager.get_data_dir().exists(),
                "logs_dir_exists": config_manager.get_logs_dir().exists()
            }
        }
        
    except Exception as e:
        logging.getLogger("latarnia.main").error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "health": "error",
                "message": f"Health check failed: {str(e)}"
            }
        )


@app.get("/api/system/metrics")
async def get_system_metrics():
    """Get detailed system metrics"""
    try:
        return system_monitor.get_system_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/system/redis")
async def get_redis_metrics():
    """Get Redis metrics and status"""
    try:
        return redis_monitor.get_redis_metrics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/system/postgres")
async def get_postgres_metrics():
    """Get Postgres connectivity status"""
    try:
        return pg_client.get_postgres_metrics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
async def get_config():
    """Get current configuration (sanitized)"""
    try:
        config = config_manager.config
        
        # Return sanitized config (no sensitive data)
        return {
            "redis": {
                "host": config.redis.host,
                "port": config.redis.port,
                "db": config.redis.db
            },
            "logging": {
                "level": config.logging.level,
                "format": config.logging.format
            },
            "process_manager": {
                "data_dir": config.process_manager.data_dir,
                "logs_dir": config.process_manager.logs_dir,
                "streamlit_port": config.process_manager.streamlit_port,
                "streamlit_ttl_seconds": config.process_manager.streamlit_ttl_seconds,
                "port_range": {
                    "start": config.process_manager.port_range.start,
                    "end": config.process_manager.port_range.end
                }
            },
            "health_check_interval_seconds": config.health_check_interval_seconds,
            "system": {
                "main_port": config.system.main_port,
                "host": config.system.host
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# App Management API Endpoints

@app.post("/api/apps/discover")
async def discover_apps():
    """Discover new applications and clean up orphaned ones."""
    try:
        count = app_manager.discover_apps()
        orphan_count = await _cleanup_orphaned_apps()
        # Regenerate unconditionally: a manual discovery may have picked up an
        # externally-started app whose port wasn't reflected in the Caddyfile.
        await asyncio.to_thread(caddy_manager.on_app_registered)
        return {
            "discovered_count": count,
            "orphans_cleaned": orphan_count,
            "message": f"Discovered {count} new application(s), cleaned {orphan_count} orphaned app(s)",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/apps")
async def get_all_apps(request: Request):
    """Get registered apps with combined systemd+/health status.

    Role-scoped (P-0008 cap-015): when a browser session is present, a
    non-superuser only sees apps where their role for that app is not `none`,
    so dashboard tiles are filtered server-side before the client renders them.
    Superusers (and, in dev, unauthenticated direct access) see all apps.
    Machine-token (JWT) scoping is layered on in Scope 4.

    Security invariant: the `user=None` (unauthenticated) branch returns all
    apps. This is safe only because in prod ufw blocks port 8000 and Caddy's
    forward_auth guarantees an authenticated session reaches this endpoint;
    direct unauthenticated access is a dev-only convenience.
    """
    try:
        claims = getattr(request.state, "jwt_claims", None)
        user = _session_user(request)
        # The middleware already gated this route; if neither identity resolves
        # now (e.g. session revoked in the TOCTOU window), fail closed.
        if claims is None and user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        apps = app_manager.registry.get_all_apps()
        payload = []
        for app_entry in apps:
            if claims is not None and not claims.get("super"):
                # Machine token: scoped to the apps named in its JWT claim.
                if claims.get("apps", {}).get(app_entry.name, "none") == "none":
                    continue
            elif not role_store.is_visible(user, app_entry.name):
                continue
            entry = app_entry.to_dict()
            combined = health_monitor.get_overall_status(app_entry.app_id)
            entry["overall_status"] = combined["overall_status"]
            entry["overall_status_detail"] = combined["detail"]
            payload.append(entry)
        return {
            "apps": payload,
            "total_count": len(payload)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/apps/{app_id}")
async def get_app(app_id: str, request: Request):
    """Get a specific application by ID (role/scope enforced).

    A machine token scoped to other apps (cap-020) gets 403; a non-superuser
    session with role `none` for the app likewise gets 403.
    """
    try:
        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")

        claims = getattr(request.state, "jwt_claims", None)
        if claims is not None and not claims.get("super"):
            if claims.get("apps", {}).get(app.name, "none") == "none":
                raise HTTPException(status_code=403, detail="Token not scoped to this app")
        else:
            user = _session_user(request)
            if user is None:
                # Session vanished after the middleware gate — fail closed.
                raise HTTPException(status_code=401, detail="Authentication required")
            if not user["is_superuser"] and \
                    role_store.get_role(user["id"], app.name) == "none":
                raise HTTPException(status_code=403, detail="No access to this app")

        return app.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/apps/type/{app_type}")
async def get_apps_by_type(app_type: str):
    """Get applications by type (service or streamlit)"""
    try:
        from .managers.app_manager import AppType
        
        if app_type not in [AppType.SERVICE, AppType.STREAMLIT]:
            raise HTTPException(status_code=400, detail=f"Invalid app type: {app_type}")
        
        apps = app_manager.registry.get_apps_by_type(AppType(app_type))
        return {
            "apps": [app.to_dict() for app in apps],
            "type": app_type,
            "count": len(apps)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/apps/status/{status}")
async def get_apps_by_status(status: str):
    """Get applications by status"""
    try:
        from .managers.app_manager import AppStatus
        
        valid_statuses = [s.value for s in AppStatus]
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}. Valid statuses: {valid_statuses}")
        
        apps = app_manager.registry.get_apps_by_status(AppStatus(status))
        return {
            "apps": [app.to_dict() for app in apps],
            "status": status,
            "count": len(apps)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/apps/{app_id}/prepare")
async def prepare_app(app_id: str):
    """Prepare an application (install dependencies, run setup)"""
    try:
        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")
        
        success = app_manager.prepare_app(app_id)
        if success:
            updated_app = app_manager.registry.get_app(app_id)
            return {
                "success": True,
                "message": f"App {app_id} prepared successfully",
                "app": updated_app.to_dict()
            }
        else:
            updated_app = app_manager.registry.get_app(app_id)
            error_msg = updated_app.runtime_info.error_message if updated_app.runtime_info.error_message else "Unknown error"
            return {
                "success": False,
                "message": f"Failed to prepare app {app_id}: {error_msg}",
                "app": updated_app.to_dict()
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/apps/{app_id}")
async def delete_app(app_id: str, delete_folder: bool = False):
    """Stop, remove systemd unit, release ports, unregister, and optionally delete folder.

    Query params:
      delete_folder: if true, deletes the app's folder from the apps/ directory.
                     The data directory and Postgres database are NOT touched.
    """
    try:
        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")

        app_path = app.path  # capture before unregistering

        # Stop service; tolerate errors (app may already be stopped)
        try:
            if app.type == AppType.SERVICE:
                pick_launcher(app).stop_service(app_id)
            else:
                streamlit_manager.stop_streamlit_app(app_id)
        except Exception as exc:
            logger.warning("Could not stop app %s during delete: %s", app_id, exc)

        # Remove systemd unit (stop + disable + unlink unit file)
        service_manager.remove_service(app_id)

        # Release ports
        if app.runtime_info.assigned_port:
            port_manager.release_port(app_id)
        if app.mcp_info and app.mcp_info.mcp_port:
            port_manager.release_mcp_port(app_id)

        # Unregister (also cleans up Redis stream consumer groups)
        if not app_manager.unregister_app(app_id):
            raise HTTPException(status_code=500, detail=f"Failed to unregister app {app_id}")

        # Optionally delete the app folder; log a warning if it fails — the app
        # is already fully unregistered at this point so the delete is best-effort.
        folder_deleted = False
        if delete_folder and app_path.exists():
            try:
                shutil.rmtree(app_path)
                folder_deleted = True
                logger.info("Deleted app folder: %s", app_path)
            except OSError as exc:
                logger.warning("Could not delete app folder %s: %s", app_path, exc)

        # App is gone from the registry — regenerate Caddy routes (drops its
        # webUI/swagger blocks so they 404).
        await asyncio.to_thread(caddy_manager.on_app_deregistered, app_id)

        return {
            "success": True,
            "message": f"App {app_id} deleted successfully",
            "folder_deleted": folder_deleted,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/apps/statistics")
async def get_app_statistics():
    """Get application statistics"""
    try:
        stats = app_manager.get_app_statistics()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Port Management API Endpoints

@app.get("/api/ports")
async def get_port_allocations():
    """Get all port allocations"""
    try:
        allocations = port_manager.get_allocated_ports()
        return {
            "allocations": [alloc.to_dict() for alloc in allocations],
            "count": len(allocations)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ports/available")
async def get_available_ports():
    """Get available ports"""
    try:
        available = port_manager.get_available_ports()
        return {
            "available_ports": available,
            "count": len(available)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ports/statistics")
async def get_port_statistics():
    """Get port allocation statistics"""
    try:
        stats = port_manager.get_statistics()
        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"Failed to get port statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Service Management API Endpoints

@app.post("/api/services/{app_id}/create")
async def create_service(app_id: str):
    """Create systemd service file for an app"""
    try:
        success = service_manager.create_service_file(app_id)
        if success:
            return {"success": True, "message": f"Service file created for app {app_id}"}
        else:
            raise HTTPException(status_code=400, detail=f"Failed to create service file for app {app_id}")
    except Exception as e:
        logger.error(f"Failed to create service for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/services/{app_id}/start")
async def start_service(app_id: str):
    """Start systemd service for an app"""
    try:
        success = service_manager.start_service(app_id)
        if success:
            mcp_compat = True
            if mcp_gateway:
                mcp_compat = await mcp_gateway.on_app_started(app_id)
            if not mcp_compat:
                service_manager.stop_service(app_id)
                raise HTTPException(
                    status_code=409,
                    detail=f"MCP backward compatibility violation for app {app_id}. "
                    "Previously registered tools were removed. App stopped.",
                )
            await asyncio.to_thread(caddy_manager.on_app_registered, app_id)
            return {"success": True, "message": f"Service started for app {app_id}"}
        else:
            raise HTTPException(status_code=400, detail=f"Failed to start service for app {app_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start service for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/services/{app_id}/stop")
async def stop_service(app_id: str):
    """Stop systemd service for an app"""
    try:
        success = service_manager.stop_service(app_id)
        if success:
            if mcp_gateway:
                await mcp_gateway.on_app_stopped(app_id)
            await asyncio.to_thread(caddy_manager.on_app_deregistered, app_id)
            return {"success": True, "message": f"Service stopped for app {app_id}"}
        else:
            raise HTTPException(status_code=400, detail=f"Failed to stop service for app {app_id}")
    except Exception as e:
        logger.error(f"Failed to stop service for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/services/{app_id}/restart")
async def restart_service(app_id: str):
    """Restart systemd service for an app"""
    try:
        success = service_manager.restart_service(app_id)
        if success:
            mcp_compat = True
            if mcp_gateway:
                mcp_compat = await mcp_gateway.on_app_started(app_id)
            if not mcp_compat:
                service_manager.stop_service(app_id)
                raise HTTPException(
                    status_code=409,
                    detail=f"MCP backward compatibility violation for app {app_id}. "
                    "Previously registered tools were removed. App stopped.",
                )
            await asyncio.to_thread(caddy_manager.on_app_registered, app_id)
            return {"success": True, "message": f"Service restarted for app {app_id}"}
        else:
            raise HTTPException(status_code=400, detail=f"Failed to restart service for app {app_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to restart service for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/services/{app_id}/status")
async def get_service_status(app_id: str):
    """Get detailed service status for an app"""
    try:
        status = service_manager.get_service_status(app_id)
        if status:
            return {"success": True, "data": status.to_dict()}
        else:
            raise HTTPException(status_code=404, detail=f"Service status not found for app {app_id}")
    except Exception as e:
        logger.error(f"Failed to get service status for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/services/{app_id}/logs")
async def get_service_logs(app_id: str, lines: int = 50):
    """Get recent service logs for an app (systemd-based)"""
    try:
        logs = service_manager.get_service_logs(app_id, lines)
        return {"success": True, "data": {"logs": logs, "lines": len(logs)}}
    except Exception as e:
        logger.error(f"Failed to get service logs for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/apps/{app_id}/logs")
async def get_app_logs(app_id: str, lines: int = 100):
    """Get recent logs for an app.

    Dispatches by (OS, app type) — single canonical source per app
    (P-0005 Scope 4):
      - Linux + service app → journald via `service_manager.get_service_logs`.
      - Darwin + service app → SubprocessLauncher's stdout-redirect file.
      - Streamlit (any OS) → StreamlitManager's per-app log file.
    """
    try:
        import platform as _platform
        from pathlib import Path

        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")

        # Service apps on Linux: journald is the canonical sink.
        if app.type == "service" and _platform.system() == "Linux":
            log_lines = service_manager.get_service_logs(app_id, lines)
            if not log_lines:
                return {"success": True, "data": {
                    "logs": [], "lines": 0, "source": "journald",
                    "message": "No journal entries (unit may not have started yet)"
                }}
            return {"success": True, "data": {
                "logs": log_lines, "lines": len(log_lines), "source": "journald",
            }}

        # Darwin service or Streamlit: read the launcher's redirected file.
        logs_dir = config_manager.get_logs_dir()
        candidates = [
            logs_dir / f"{app_id}.log",            # SubprocessLauncher (Darwin)
            logs_dir / f"{app_id}-streamlit.log",  # StreamlitManager
        ]
        for log_file in candidates:
            if log_file.exists():
                with open(log_file, "r") as f:
                    all_lines = f.readlines()
                trimmed = all_lines[-lines:] if len(all_lines) > lines else all_lines
                return {"success": True, "data": {
                    "logs": [ln.rstrip("\n") for ln in trimmed],
                    "lines": len(trimmed),
                    "source": "file",
                    "file": str(log_file),
                }}

        return {"success": True, "data": {
            "logs": [], "lines": 0, "source": "none",
            "message": "No log source available for this app"
        }}

    except HTTPException:
        raise
        
    except Exception as e:
        logger.error(f"Failed to get logs for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs/latarnia")
async def get_latarnia_logs(lines: int = 100):
    """Get recent Latarnia main application logs"""
    try:
        from pathlib import Path
        
        log_file = config_manager.get_logs_dir() / "latarnia-main.log"
        
        if not log_file.exists():
            return {"success": True, "data": {"logs": [], "lines": 0, "message": "Log file not found"}}
        
        try:
            with open(log_file, 'r') as f:
                all_lines = f.readlines()
                # Get last N lines
                log_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
                # Strip newlines
                log_lines = [line.rstrip('\n') for line in log_lines]
            
            return {"success": True, "data": {"logs": log_lines, "lines": len(log_lines)}}
            
        except Exception as e:
            logger.error(f"Failed to read Latarnia log file: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        
    except Exception as e:
        logger.error(f"Failed to get Latarnia logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/activity/recent")
async def get_recent_activity(limit: int = 10):
    """Get recent Redis pub/sub events"""
    try:
        import redis
        import json
        from datetime import datetime
        
        # Connect to Redis
        redis_client = redis.from_url(config_manager.get_redis_url())
        
        activities = []
        
        # Get events from the recent events list (stored by background subscriber)
        events_key = "latarnia:events:recent"
        
        # Get the latest events
        total_events = redis_client.llen(events_key)
        
        if total_events > 0:
            # Get the last N events (newest at the end of the list)
            start_index = max(0, total_events - limit)
            events = redis_client.lrange(events_key, start_index, -1)
            
            # Reverse to show newest first
            events.reverse()
            
            for event in events:
                try:
                    event_data = json.loads(event)
                    
                    # Format timestamp
                    timestamp_val = event_data.get('timestamp', '')
                    if isinstance(timestamp_val, (int, float)):
                        timestamp_str = datetime.fromtimestamp(timestamp_val).strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        timestamp_str = str(timestamp_val)
                    
                    # Extract message from event data
                    message = ''
                    if 'data' in event_data and 'content' in event_data['data']:
                        message = event_data['data']['content']
                    elif 'event_type' in event_data:
                        message = f"Event: {event_data['event_type']}"
                    else:
                        message = json.dumps(event_data.get('data', {}))
                    
                    activities.append({
                        'timestamp': timestamp_str,
                        'message': message,
                        'sender': event_data.get('source', 'unknown'),
                        'data': event_data
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse Redis event: {e}")
                    continue
        
        return {"success": True, "data": {"activities": activities, "count": len(activities)}}
        
    except Exception as e:
        logger.error(f"Failed to get recent activity: {e}")
        return {"success": True, "data": {"activities": [], "count": 0, "error": str(e)}}


@app.websocket("/ws/activity")
async def activity_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time stream activity updates."""
    await event_subscriber.ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_subscriber.ws_manager.disconnect(websocket)
    except Exception:
        event_subscriber.ws_manager.disconnect(websocket)


@app.post("/api/services/{app_id}/enable")
async def enable_service(app_id: str):
    """Enable service to start automatically"""
    try:
        success = service_manager.enable_service(app_id)
        if success:
            return {"success": True, "message": f"Service enabled for app {app_id}"}
        else:
            raise HTTPException(status_code=400, detail=f"Failed to enable service for app {app_id}")
    except Exception as e:
        logger.error(f"Failed to enable service for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/services/{app_id}/disable")
async def disable_service(app_id: str):
    """Disable service from starting automatically"""
    try:
        success = service_manager.disable_service(app_id)
        if success:
            return {"success": True, "message": f"Service disabled for app {app_id}"}
        else:
            raise HTTPException(status_code=400, detail=f"Failed to disable service for app {app_id}")
    except Exception as e:
        logger.error(f"Failed to disable service for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/services/{app_id}")
async def remove_service(app_id: str):
    """Remove service file and stop service"""
    try:
        success = service_manager.remove_service(app_id)
        if success:
            return {"success": True, "message": f"Service removed for app {app_id}"}
        else:
            raise HTTPException(status_code=400, detail=f"Failed to remove service for app {app_id}")
    except Exception as e:
        logger.error(f"Failed to remove service for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/services")
async def get_all_service_statuses():
    """Get status for all managed services"""
    try:
        statuses = service_manager.get_all_service_statuses()
        return {"success": True, "data": {app_id: status.to_dict() for app_id, status in statuses.items()}}
    except Exception as e:
        logger.error(f"Failed to get all service statuses: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/services/statistics")
async def get_service_statistics():
    """Get service management statistics"""
    try:
        stats = service_manager.get_service_statistics()
        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"Failed to get service statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Health Monitoring API Endpoints

@app.post("/api/health/start")
async def start_health_monitoring():
    """Start health monitoring system"""
    try:
        await health_monitor.start_monitoring()
        return {"success": True, "message": "Health monitoring started"}
    except Exception as e:
        logger.error(f"Failed to start health monitoring: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/health/stop")
async def stop_health_monitoring():
    """Stop health monitoring system"""
    try:
        await health_monitor.stop_monitoring()
        return {"success": True, "message": "Health monitoring stopped"}
    except Exception as e:
        logger.error(f"Failed to stop health monitoring: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# NOTE: static-path routes (/status, /statistics, /config) MUST come before
# the dynamic `/api/health/{app_id}` route — FastAPI matches in declaration
# order, otherwise GET /api/health/status falls into the {app_id} handler
# with app_id="status" and returns a misleading 404/500.

@app.get("/api/health")
async def get_all_health_statuses():
    """Get health status for all monitored apps"""
    try:
        statuses = health_monitor.get_all_health_statuses()
        return {"success": True, "data": {app_id: status.to_dict() for app_id, status in statuses.items()}}
    except Exception as e:
        logger.error(f"Failed to get all health statuses: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health/status")
async def get_health_monitoring_status():
    """Get health monitoring system status"""
    try:
        is_running = health_monitor.is_monitoring()
        return {"success": True, "data": {"monitoring": is_running}}
    except Exception as e:
        logger.error(f"Failed to get health monitoring status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health/statistics")
async def get_health_statistics():
    """Get health monitoring statistics"""
    try:
        stats = health_monitor.get_health_statistics()
        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"Failed to get health statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/health/config")
async def update_health_config(config: dict):
    """Update health monitoring configuration"""
    try:
        health_monitor.update_config(**config)
        return {"success": True, "message": "Health monitoring configuration updated"}
    except Exception as e:
        logger.error(f"Failed to update health config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health/{app_id}")
async def get_app_health(app_id: str):
    """Get health status for a specific app"""
    try:
        health = health_monitor.get_health_status(app_id)
        if health:
            return {"success": True, "data": health.to_dict()}
        raise HTTPException(status_code=404, detail=f"Health status not found for app {app_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get health status for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# P-0006: Secret listing endpoint. Returns names + mtime + consuming apps;
# NEVER values. Listing is read-only — to set/rotate, the operator edits
# /opt/latarnia/{env}/secrets.env directly with $EDITOR.
@app.get("/api/secrets")
async def list_secrets():
    """List declared secret names + metadata. No values."""
    try:
        items = secret_manager.list_secrets()
        return {
            "env": secret_manager.env,
            "secrets": [m.to_dict() for m in items],
        }
    except Exception as e:
        logger.error(f"Failed to list secrets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ports/cleanup")
async def cleanup_stale_ports():
    """Clean up stale port allocations"""
    try:
        cleaned = port_manager.cleanup_stale_allocations()
        return {
            "cleaned_count": cleaned,
            "message": f"Cleaned up {cleaned} stale port allocations"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# UI Integration Endpoints

@app.get("/api/apps/{app_id}/ui/resources")
async def get_app_ui_resources(app_id: str):
    """Discover UI resources available from a service app"""
    try:
        from .web.ui_renderer import ui_renderer
        
        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")
        
        if app.type != "service":
            raise HTTPException(status_code=400, detail="Only service apps can have UI resources")
        
        port = app.runtime_info.assigned_port
        if not port:
            raise HTTPException(status_code=400, detail="App is not running or has no assigned port")
        
        base_url = f"http://localhost:{port}"
        resources = await ui_renderer.discover_ui_resources(base_url)
        
        if resources is None:
            return {"has_ui": False, "resources": []}
        
        return {"has_ui": True, "resources": resources, "base_url": base_url}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to discover UI resources for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/apps/{app_id}/ui/{resource}")
async def get_app_ui_resource(app_id: str, resource: str):
    """Fetch data for a specific UI resource"""
    try:
        from .web.ui_renderer import ui_renderer
        
        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")
        
        port = app.runtime_info.assigned_port
        if not port:
            raise HTTPException(status_code=400, detail="App is not running")
        
        base_url = f"http://localhost:{port}"
        data = await ui_renderer.fetch_resource_list(base_url, resource)
        
        if data is None:
            raise HTTPException(status_code=404, detail=f"Resource {resource} not found")
        
        # Render as HTML table
        html = ui_renderer.render_table_html(data, resource)
        
        return {
            "resource": resource,
            "data": data,
            "html": html,
            "count": len(data)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch UI resource {resource} for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/apps/{app_id}/ui/{resource}/{item_id}")
async def get_app_ui_resource_detail(app_id: str, resource: str, item_id: str):
    """Fetch detail for a specific resource item"""
    try:
        from .web.ui_renderer import ui_renderer

        app_entry = app_manager.registry.get_app(app_id)
        if not app_entry:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")

        port = app_entry.runtime_info.assigned_port
        if not port:
            raise HTTPException(status_code=400, detail="App is not running")

        base_url = f"http://localhost:{port}"
        data = await ui_renderer.fetch_resource_detail(base_url, resource, item_id)

        if data is None:
            raise HTTPException(status_code=404, detail=f"{resource}/{item_id} not found")

        html = ui_renderer.render_detail_html(data, resource)
        return {"resource": resource, "item_id": item_id, "data": data, "html": html}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch UI detail {resource}/{item_id} for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Process Management Endpoints (macOS compatible)

@app.post("/api/apps/{app_id}/process/start")
async def start_app_process(app_id: str):
    """Start an app via the launcher chosen by pick_launcher (systemd on Linux, subprocess on macOS)."""
    try:
        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")

        if app.type != "service":
            raise HTTPException(status_code=400, detail="Only service apps can be started this way")

        launcher = pick_launcher(app)
        success = launcher.start_service(app_id)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to start app")

        # Sync MCP tools and check backward compatibility
        mcp_compat = True
        if mcp_gateway:
            mcp_compat = await mcp_gateway.on_app_started(app_id)
        if not mcp_compat:
            launcher.stop_service(app_id)
            raise HTTPException(
                status_code=409,
                detail=f"MCP backward compatibility violation for app {app_id}. "
                "Previously registered tools were removed. App stopped.",
            )

        await asyncio.to_thread(caddy_manager.on_app_registered, app_id)
        return {"success": True, "message": f"App {app_id} started successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/apps/{app_id}/process/stop")
async def stop_app_process(app_id: str):
    """Stop an app via the launcher chosen by pick_launcher."""
    try:
        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")
        if app.type != "service":
            raise HTTPException(status_code=400, detail="Only service apps can be stopped this way")

        launcher = pick_launcher(app)
        success = launcher.stop_service(app_id)

        if not success:
            raise HTTPException(status_code=404, detail="App is not running")

        # Remove MCP tools if gateway is active
        if mcp_gateway:
            await mcp_gateway.on_app_stopped(app_id)

        await asyncio.to_thread(caddy_manager.on_app_deregistered, app_id)
        return {"success": True, "message": f"App {app_id} stopped successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stop app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/apps/{app_id}/process/restart")
async def restart_app_process(app_id: str):
    """Restart an app via the launcher chosen by pick_launcher."""
    try:
        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")

        if app.type != "service":
            raise HTTPException(status_code=400, detail="Only service apps can be restarted this way")

        launcher = pick_launcher(app)
        success = launcher.restart_service(app_id)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to restart app")

        # Re-sync MCP tools and check backward compatibility
        mcp_compat = True
        if mcp_gateway:
            mcp_compat = await mcp_gateway.on_app_started(app_id)
        if not mcp_compat:
            launcher.stop_service(app_id)
            raise HTTPException(
                status_code=409,
                detail=f"MCP backward compatibility violation for app {app_id}. "
                "Previously registered tools were removed. App stopped.",
            )

        await asyncio.to_thread(caddy_manager.on_app_registered, app_id)
        return {"success": True, "message": f"App {app_id} restarted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to restart app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/apps/{app_id}/process/info")
async def get_app_process_info(app_id: str):
    """Get detailed process information including uptime.

    Subprocess-launched apps (macOS) report PID/uptime here. Systemd-managed
    apps (Linux) return None — query /api/services/{id}/status for those.
    """
    try:
        process_info = subprocess_launcher.get_process_info(app_id)

        if process_info is None:
            # Also check Streamlit manager
            streamlit_info = streamlit_manager.get_running_apps()
            if app_id in streamlit_info:
                return {"success": True, "data": streamlit_info[app_id]}

            return {"success": True, "data": None, "message": "Process not running"}

        return {"success": True, "data": process_info}

    except Exception as e:
        logger.error(f"Failed to get process info for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/system/restart")
async def restart_latarnia():
    """Restart Latarnia application"""
    import asyncio
    import os
    import platform
    import subprocess

    logger.info("Latarnia restart requested via API")

    if platform.system() != "Linux":
        logger.warning("Restart requested — no-op on non-Linux platform")
        return {"success": True, "message": "Restart is a no-op on non-Linux platforms"}

    env = os.environ.get("ENV", "dev").lower()
    unit = f"latarnia-{env}.service"
    logger.info("Scheduling restart of platform unit %s", unit)

    async def _restart():
        await asyncio.sleep(1)
        try:
            subprocess.Popen(["sudo", "systemctl", "restart", unit])
        except Exception as exc:
            logger.error("Failed to restart platform unit %s: %s", unit, exc)

    asyncio.create_task(_restart())
    return {"success": True, "message": f"Restart initiated for {unit}"}


# Streamlit App Management Endpoints

@app.post("/api/apps/{app_id}/streamlit/launch")
async def launch_streamlit_app(app_id: str):
    """Launch a Streamlit app (or return existing instance)"""
    try:
        app = app_manager.registry.get_app(app_id)
        if not app:
            raise HTTPException(status_code=404, detail=f"App {app_id} not found")
        
        if app.type != "streamlit":
            raise HTTPException(status_code=400, detail="App is not a Streamlit app")
        
        result = streamlit_manager.launch_streamlit_app(app_id)
        
        if result is None:
            raise HTTPException(status_code=500, detail="Failed to launch Streamlit app")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to launch Streamlit app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/apps/{app_id}/streamlit/stop")
async def stop_streamlit_app(app_id: str):
    """Stop a running Streamlit app"""
    try:
        success = streamlit_manager.stop_streamlit_app(app_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Streamlit app is not running")
        
        return {"message": f"Streamlit app {app_id} stopped successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stop Streamlit app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/apps/{app_id}/streamlit/touch")
async def touch_streamlit_app(app_id: str):
    """Update last accessed time for a Streamlit app (extends TTL)"""
    try:
        streamlit_manager.touch_app(app_id)
        return {"message": "TTL extended"}
    except Exception as e:
        logger.error(f"Failed to touch Streamlit app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/streamlit/running")
async def get_running_streamlit_apps():
    """Get list of currently running Streamlit apps"""
    try:
        return streamlit_manager.get_running_apps()
    except Exception as e:
        logger.error(f"Failed to get running Streamlit apps: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# MCP Gateway API Endpoints

@app.get("/api/mcp/status")
async def get_mcp_status():
    """Get MCP gateway status"""
    if not mcp_gateway:
        return {"success": True, "data": {"enabled": False}}
    try:
        return {"success": True, "data": mcp_gateway.get_status()}
    except Exception as e:
        logger.error(f"Failed to get MCP status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/mcp/tools")
async def get_mcp_tools():
    """Get all registered MCP tools across apps"""
    if not mcp_gateway:
        return {"success": True, "data": {"tools": {}, "count": 0}}
    try:
        tools = mcp_gateway.get_tool_index()
        return {"success": True, "data": {"tools": tools, "count": len(tools)}}
    except Exception as e:
        logger.error(f"Failed to get MCP tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    
    config = config_manager.config
    uvicorn.run(
        "latarnia.main:app",
        host=config.system.host,
        port=config.system.main_port,
        reload=True,
        log_level=config.logging.level.lower()
    )
