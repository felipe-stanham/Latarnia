"""
Subprocess launcher for Latarnia service apps.

Spawns service apps as background subprocess children of the platform via
`Popen`. This is the macOS-only fallback launcher used when systemd is not
available; on Linux the platform routes through `ServiceManager` instead.

Compared to systemd, this launcher provides no crash recovery and no journal
integration. It exists so that local dev on macOS keeps working.
"""
import logging
import os
import subprocess
import sys
import psutil
from pathlib import Path
from typing import Optional, Dict, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from .secret_manager import SecretManager


class SubprocessLauncher:
    """Launches service apps as platform-process children (macOS fallback)."""

    def __init__(self, config_manager, app_manager, port_manager,
                 secret_manager: Optional["SecretManager"] = None):
        self.config_manager = config_manager
        self.app_manager = app_manager
        self.port_manager = port_manager
        # P-0006: optional. When set, refuses to start apps with missing
        # required secrets and merges declared secrets into Popen env=.
        self.secret_manager = secret_manager
        self.logger = logging.getLogger("latarnia.subprocess_launcher")

        # Track running processes: app_id -> process info
        self.processes: Dict[str, dict] = {}

    def start_service(self, app_id: str) -> bool:
        """Start a service app as a background process.

        Returns True if the app is running (or already was), False on failure.
        Verb harmonized with `ServiceManager.start_service` so call sites can
        use `pick_launcher(app).start_service(app_id)` regardless of OS.
        """
        try:
            app = self.app_manager.registry.get_app(app_id)
            if not app:
                self.logger.error(f"App {app_id} not found")
                return False

            if app.type != "service":
                self.logger.error(f"App {app_id} is not a service app")
                return False

            # P-0006: refuse-to-start gate. Run BEFORE port allocation so a
            # missing secret leaves no port allocations behind. Holds the
            # filtered env dict for later merge into Popen `env=`.
            secret_env: Dict[str, str] = {}
            if self.secret_manager is not None:
                secret_result, secret_env = self.secret_manager.get_filtered_env(app)
                if not secret_result.ok:
                    self.logger.error(
                        "Refusing to start %s: %s", app_id, secret_result.detail,
                    )
                    app.runtime_info.error_message = secret_result.detail
                    self.app_manager.registry.update_app(
                        app_id, status="error",
                        runtime_info=app.runtime_info,
                    )
                    return False

            # Check if already running
            if app_id in self.processes:
                pid = self.processes[app_id].get('pid')
                if pid and psutil.pid_exists(pid):
                    self.logger.info(f"App {app_id} is already running (PID: {pid})")
                    return True

            # Allocate port
            port = self.port_manager.allocate_port(app_id, app.type)
            if not port:
                self.logger.error(f"Failed to allocate port for app {app_id}")
                return False

            # Build command
            app_path = Path(app.path)
            main_file = app_path / app.manifest.main_file

            if not main_file.exists():
                self.logger.error(f"Main file not found: {main_file}")
                self.port_manager.release_port(app_id)
                return False

            # Allocate MCP port if app has MCP enabled
            mcp_port = None
            if app.mcp_info and app.mcp_info.enabled:
                mcp_port = self.port_manager.allocate_mcp_port(app_id)
                if not mcp_port:
                    self.logger.error(f"Failed to allocate MCP port for app {app_id}")
                    self.port_manager.release_port(app_id)
                    return False

            # Build arguments
            cmd = [sys.executable, str(main_file), "--port", str(port)]

            # Add MCP port if allocated
            if mcp_port:
                cmd.extend(["--mcp-port", str(mcp_port)])

            # Add Redis URL if required
            if app.manifest.config.redis_required:
                redis_url = self.config_manager.get_redis_url()
                cmd.extend(["--redis-url", redis_url])

            # Add data dir if required
            if app.manifest.config.data_dir:
                data_dir = self.config_manager.get_data_dir(app_id)
                data_dir.mkdir(parents=True, exist_ok=True)
                cmd.extend(["--data-dir", str(data_dir)])

            # No --logs-dir: apps log to stdout/stderr. On macOS we still
            # capture stdout/stderr to a file via Popen below so the
            # dashboard's /api/apps/{id}/logs endpoint can read it.
            # The `logs_dir` manifest field is deprecated as of P-0005
            # Scope 4; logs canonical sink is journald (Linux) /
            # subprocess log file (Darwin).

            # Build environment for subprocess
            proc_env = dict(os.environ)

            # Pass database URL via environment variable if provisioned
            if app.database_info and app.database_info.provisioned and app.database_info.connection_url:
                proc_env["DATABASE_URL"] = app.database_info.connection_url
                cmd.extend(["--db-url", "env:DATABASE_URL"])

            # P-0006: merge declared secrets into the subprocess env. Done
            # AFTER os.environ copy so secrets win over any host env shadowing.
            # Logged only by count + key list, never by value.
            if secret_env:
                proc_env.update(secret_env)
                self.logger.info(
                    "Injecting %d secret(s) into %s: %s",
                    len(secret_env), app_id, sorted(secret_env.keys()),
                )

            # Start process
            self.logger.info(f"Starting app {app_id} with command: {' '.join(cmd)}")

            # Redirect stdout/stderr to log file
            log_file = self.config_manager.get_logs_dir() / f"{app_id}.log"
            with open(log_file, 'a') as log:
                process = subprocess.Popen(
                    cmd,
                    cwd=str(app_path),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,  # Detach from parent
                    env=proc_env,
                )

            # Track process
            self.processes[app_id] = {
                'pid': process.pid,
                'port': port,
                'mcp_port': mcp_port,
                'started_at': datetime.now(),
                'command': ' '.join(cmd)
            }

            # Update app registry
            self.app_manager.registry.update_app(
                app_id,
                status='running',
                runtime_info=app.runtime_info
            )
            app.runtime_info.assigned_port = port
            app.runtime_info.process_id = str(process.pid)
            app.runtime_info.started_at = datetime.now()

            # Store allocated MCP port in MCPInfo at launch time
            if mcp_port and app.mcp_info:
                app.mcp_info.mcp_port = mcp_port
                self.logger.info(
                    f"Started app {app_id} (PID: {process.pid}, REST port: {port}, "
                    f"MCP port: {mcp_port})"
                )
            else:
                self.logger.info(f"Started app {app_id} (PID: {process.pid}, Port: {port})")
            return True

        except Exception as e:
            self.logger.error(f"Failed to start app {app_id}: {e}", exc_info=True)
            if app_id in self.processes:
                del self.processes[app_id]
            self.port_manager.release_port(app_id)
            self.port_manager.release_mcp_port(app_id)
            return False

    def stop_service(self, app_id: str) -> bool:
        """Stop a running service app subprocess. Verb harmonized with ServiceManager."""
        try:
            if app_id not in self.processes:
                self.logger.warning(f"App {app_id} is not tracked as running")
                return False

            pid = self.processes[app_id].get('pid')
            if not pid:
                self.logger.warning(f"No PID found for app {app_id}")
                return False

            # Try to terminate gracefully
            try:
                process = psutil.Process(pid)
                process.terminate()
                process.wait(timeout=5)
                self.logger.info(f"Stopped app {app_id} (PID: {pid})")
            except psutil.TimeoutExpired:
                # Force kill if graceful termination fails
                process.kill()
                self.logger.warning(f"Force killed app {app_id} (PID: {pid})")
            except psutil.NoSuchProcess:
                self.logger.warning(f"Process {pid} for app {app_id} not found")

            # Release ports
            self.port_manager.release_port(app_id)
            self.port_manager.release_mcp_port(app_id)

            # Update registry
            app = self.app_manager.registry.get_app(app_id)
            if app:
                self.app_manager.registry.update_app(app_id, status='stopped')
                app.runtime_info.process_id = None
                app.runtime_info.assigned_port = None
                if app.mcp_info:
                    app.mcp_info.mcp_port = None

            # Remove from tracking
            del self.processes[app_id]

            return True

        except Exception as e:
            self.logger.error(f"Failed to stop app {app_id}: {e}", exc_info=True)
            return False

    def restart_service(self, app_id: str) -> bool:
        """Restart a service app subprocess. Verb harmonized with ServiceManager."""
        self.logger.info(f"Restarting app {app_id}")
        self.stop_service(app_id)
        return self.start_service(app_id)

    def get_process_info(self, app_id: str) -> Optional[dict]:
        """Get detailed process information including start time"""
        if app_id not in self.processes:
            return None

        process_info = self.processes[app_id].copy()

        # Calculate uptime if process is running
        if 'started_at' in process_info:
            uptime_seconds = (datetime.now() - process_info['started_at']).total_seconds()
            process_info['uptime_seconds'] = int(uptime_seconds)

            # Format uptime as human-readable string
            hours, remainder = divmod(int(uptime_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)

            if hours > 0:
                process_info['uptime'] = f"{hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                process_info['uptime'] = f"{minutes}m {seconds}s"
            else:
                process_info['uptime'] = f"{seconds}s"

            # Convert datetime to ISO string for JSON serialization
            process_info['started_at'] = process_info['started_at'].isoformat()

        return process_info

    def stop_all(self):
        """Stop all managed processes"""
        self.logger.info("Stopping all managed processes")
        for app_id in list(self.processes.keys()):
            self.stop_service(app_id)
