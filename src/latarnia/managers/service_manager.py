"""
Service Manager for Latarnia

Handles systemd service integration, lifecycle management, and health monitoring.
Provides systemd service template generation and process monitoring capabilities.
"""

import getpass
import json
import logging
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum

from ..core.config import ConfigManager

if TYPE_CHECKING:
    from .secret_manager import SecretManager
from .app_manager import AppManager, AppType, AppStatus
from .port_manager import PortManager


class ServiceStatus(str, Enum):
    """Service status values from systemd"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    ACTIVATING = "activating"
    DEACTIVATING = "deactivating"
    UNKNOWN = "unknown"


class ServiceState(str, Enum):
    """Service state values from systemd"""
    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass
class ServiceInfo:
    """Information about a systemd service"""
    service_name: str
    status: ServiceStatus
    state: ServiceState
    pid: Optional[int] = None
    memory_usage: Optional[int] = None  # in bytes
    cpu_percent: Optional[float] = None
    uptime: Optional[timedelta] = None
    restart_count: int = 0
    last_restart: Optional[datetime] = None
    error_message: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        if self.uptime:
            data['uptime'] = str(self.uptime)
        if self.last_restart:
            data['last_restart'] = self.last_restart.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ServiceInfo':
        """Create from dictionary"""
        if 'uptime' in data and data['uptime']:
            # Parse uptime string back to timedelta
            parts = data['uptime'].split(':')
            if len(parts) == 3:
                hours, minutes, seconds = map(float, parts)
                data['uptime'] = timedelta(hours=hours, minutes=minutes, seconds=seconds)
        if 'last_restart' in data and data['last_restart']:
            data['last_restart'] = datetime.fromisoformat(data['last_restart'])
        return cls(**data)


class ServiceManager:
    """Main service manager for systemd integration"""
    
    def __init__(self, config_manager: ConfigManager, app_manager: AppManager,
                 port_manager: Optional[PortManager] = None,
                 secret_manager: Optional["SecretManager"] = None):
        self.config_manager = config_manager
        self.app_manager = app_manager
        # PortManager is optional for backward compat with older callers/tests;
        # when present, start_service auto-allocates REST and MCP ports so the
        # systemd path matches SubprocessLauncher's one-shot contract.
        self.port_manager = port_manager
        # SecretManager is also optional for backward compat. When present,
        # start_service refuses to launch apps with missing required secrets
        # and writes a per-app filtered file the unit references via
        # `EnvironmentFile=-...` (P-0006).
        self.secret_manager = secret_manager
        self.logger = logging.getLogger("latarnia.service_manager")
        
        # Service tracking
        self.services: Dict[str, ServiceInfo] = {}
        
        # systemd paths
        self.systemd_user_dir = Path.home() / ".config" / "systemd" / "user"
        self.systemd_user_dir.mkdir(parents=True, exist_ok=True)

        # Environment-scoped service prefix. TST and PRD run side-by-side on
        # the homeserver under the same user; without the env segment,
        # per-app unit files in ~/.config/systemd/user/ would collide.
        env = os.environ.get("ENV", "dev").lower()
        if env not in ("dev", "tst", "prd"):
            self.logger.warning(
                "Unrecognized ENV=%r; falling back to 'dev' for service naming", env
            )
            env = "dev"
        self.env = env
        self.service_prefix = f"latarnia-{env}-"

        # Absolute path to the platform's venv Python. Generated unit files
        # use this for ExecStart so systemd does not depend on PATH.
        self.python_executable = sys.executable

    def reconcile_running_units(self) -> int:
        """Sync the in-memory app registry with surviving per-app systemd units.

        Per-app units have independent lifetimes from the platform (no
        PartOf coupling). After a platform restart, units may still be
        active. This method:

          1. Lists `latarnia-{env}-*.service` units that are `active` /
             `activating`.
          2. For each, parses the unit file's ExecStart for `--port` and
             `--mcp-port`.
          3. Claims those ports in `PortManager` so future allocations
             don't collide.
          4. Marks the corresponding app as `RUNNING` in the registry and
             populates `runtime_info.assigned_port` (and
             `mcp_info.mcp_port` if applicable).

        Linux only; no-op on non-Linux. Returns the number of apps
        reconciled.
        """
        if platform.system() != "Linux":
            return 0
        if self.port_manager is None:
            self.logger.debug("No PortManager wired; skipping reconciliation")
            return 0

        try:
            result = subprocess.run(
                [
                    "systemctl", "--user", "show",
                    "--property=Id,ActiveState",
                    "--type=service",
                    f"{self.service_prefix}*.service",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.logger.debug("systemctl not available; skipping reconciliation")
            return 0
        if result.returncode != 0:
            self.logger.debug("systemctl show returned %d; skipping", result.returncode)
            return 0

        active_app_ids: List[str] = []
        current_id: Optional[str] = None
        current_active: Optional[str] = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                if current_id and current_active in {"active", "activating"}:
                    if current_id.startswith(self.service_prefix) and current_id.endswith(".service"):
                        app_id = current_id[len(self.service_prefix):-len(".service")]
                        active_app_ids.append(app_id)
                current_id = None
                current_active = None
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key == "Id":
                current_id = value
            elif key == "ActiveState":
                current_active = value
        if current_id and current_active in {"active", "activating"}:
            if current_id.startswith(self.service_prefix) and current_id.endswith(".service"):
                app_id = current_id[len(self.service_prefix):-len(".service")]
                active_app_ids.append(app_id)

        reconciled = 0
        for app_id in active_app_ids:
            unit_path = self.systemd_user_dir / f"{self.service_prefix}{app_id}.service"
            if not unit_path.exists():
                self.logger.debug("Active unit %s has no file on disk; skipping", app_id)
                continue
            ports = self._parse_ports_from_unit(unit_path)
            if not ports.get("port"):
                self.logger.warning(
                    "Active unit for %s has no --port in ExecStart; cannot reconcile",
                    app_id,
                )
                continue

            app = self.app_manager.registry.get_app(app_id)
            if app is None:
                self.logger.warning(
                    "Active unit for %s but app is not registered; leaving unit alone",
                    app_id,
                )
                continue

            self.port_manager.claim_port(app_id, app.type, ports["port"])
            app.runtime_info.assigned_port = ports["port"]
            if ports.get("mcp_port") and app.mcp_info:
                self.port_manager.claim_mcp_port(app_id, ports["mcp_port"])
                app.mcp_info.mcp_port = ports["mcp_port"]
            self.app_manager.registry.update_app(
                app_id,
                status=AppStatus.RUNNING,
                runtime_info=app.runtime_info,
            )
            self.logger.info(
                "Reconciled %s as RUNNING (port=%s, mcp_port=%s)",
                app_id,
                ports["port"],
                ports.get("mcp_port"),
            )
            reconciled += 1

        return reconciled

    @staticmethod
    def _parse_ports_from_unit(unit_path: Path) -> Dict[str, int]:
        """Parse `--port` and `--mcp-port` from a unit file's ExecStart line."""
        ports: Dict[str, int] = {}
        try:
            text = unit_path.read_text()
        except OSError:
            return ports
        for line in text.splitlines():
            if not line.startswith("ExecStart="):
                continue
            tokens = line.split()
            for i, tok in enumerate(tokens):
                if tok == "--port" and i + 1 < len(tokens):
                    try:
                        ports["port"] = int(tokens[i + 1])
                    except ValueError:
                        pass
                elif tok == "--mcp-port" and i + 1 < len(tokens):
                    try:
                        ports["mcp_port"] = int(tokens[i + 1])
                    except ValueError:
                        pass
            break
        return ports

    def linger_enabled(self, user: Optional[str] = None) -> bool:
        """
        Check whether systemd --user linger is enabled for the given user.

        Linger keeps the user systemd instance running outside an active login
        session — required for per-app user units to start at platform boot.
        On non-Linux hosts, returns True (no-op; no systemd to gate on).

        Args:
            user: Username to check. Defaults to the current user.

        Returns:
            True if linger is enabled, False if disabled, True if undetectable
            (treat as enabled to avoid false positives on non-Linux).
        """
        if platform.system() != "Linux":
            return True

        target_user = user or getpass.getuser()
        try:
            result = subprocess.run(
                ["loginctl", "show-user", target_user, "--property=Linger"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.logger.debug("loginctl not available; skipping linger check")
            return True

        if result.returncode != 0:
            self.logger.debug(
                "loginctl returned %d for user %s; assuming linger enabled",
                result.returncode,
                target_user,
            )
            return True

        for line in result.stdout.strip().splitlines():
            if line.startswith("Linger="):
                return line.split("=", 1)[1].strip().lower() == "yes"
        return True

    def generate_service_template(self, app_id: str) -> Optional[str]:
        """
        Generate systemd service template for an application
        
        Args:
            app_id: Application identifier
            
        Returns:
            Service template content or None if failed
        """
        app = self.app_manager.registry.get_app(app_id)
        if not app or app.type != AppType.SERVICE:
            self.logger.error(f"App {app_id} not found or not a service app")
            return None
        
        if not app.runtime_info.assigned_port:
            self.logger.error(f"App {app_id} has no assigned port")
            return None
        
        # Build command arguments. ExecStart uses the absolute path to the
        # platform's venv Python so systemd does not need PATH set.
        cmd_args = [
            self.python_executable, app.manifest.main_file,
            "--port", str(app.runtime_info.assigned_port)
        ]

        # Add MCP port if app has MCP enabled and port is allocated
        if app.mcp_info and app.mcp_info.enabled and app.mcp_info.mcp_port:
            cmd_args.extend(["--mcp-port", str(app.mcp_info.mcp_port)])

        # Pass --redis-url (matches SubprocessLauncher contract) so apps
        # that read the CLI arg work identically under both launchers.
        if app.manifest.config and app.manifest.config.redis_required:
            cmd_args.extend(["--redis-url", self.config_manager.get_redis_url()])

        # Add optional arguments based on config. --logs-dir is no longer
        # passed: service apps log to stdout/stderr; systemd routes that
        # to journald (single canonical sink). The `logs_dir` manifest
        # field is deprecated as of P-0005 Scope 4.
        if app.manifest.config and app.manifest.config.data_dir:
            data_dir = (Path(self.config_manager.get_data_dir()) / app_id).resolve()
            data_dir.mkdir(parents=True, exist_ok=True)
            cmd_args.extend(["--data-dir", str(data_dir)])

        # Add database URL hint (actual URL passed via environment variable)
        db_url_env = None
        if app.database_info and app.database_info.provisioned and app.database_info.connection_url:
            db_url_env = app.database_info.connection_url
            cmd_args.extend(["--db-url", "env:DATABASE_URL"])

        # Environment variables
        env_vars = []
        if app.manifest.config and app.manifest.config.redis_required:
            redis_config = self.config_manager.config.redis
            env_vars.append(f"REDIS_HOST={redis_config.host}")
            env_vars.append(f"REDIS_PORT={redis_config.port}")
            # password is optional in RedisConfig — guard via getattr.
            redis_password = getattr(redis_config, "password", None)
            if redis_password:
                env_vars.append(f"REDIS_PASSWORD={redis_password}")
        
        # Add database URL via environment variable (not command line)
        if db_url_env:
            env_vars.append(f"DATABASE_URL={db_url_env}")

        # Add custom environment variables from manifest
        if hasattr(app.manifest, 'environment') and app.manifest.environment:
            for key, value in app.manifest.environment.items():
                env_vars.append(f"{key}={value}")
        
        # Restart policy: default is on-failure (matches systemd best practice
        # for supervised long-running services). Manifest may override per-app.
        # Map "never" → systemd's "no" since systemd does not accept "never".
        manifest_policy = None
        if app.manifest.config and app.manifest.config.restart_policy:
            manifest_policy = app.manifest.config.restart_policy
        restart_policy = manifest_policy or "on-failure"
        if restart_policy == "never":
            restart_policy = "no"

        # Generate service template. Environment=ENV={env} is set so apps
        # reading ENV (and ServiceManager itself if re-entrant) behave
        # consistently with the main platform unit.
        # No PartOf=latarnia-{env}.service: that referenced a system-scope
        # unit from a user-scope unit, which systemd silently ignores. App
        # lifetimes are independent of the platform — apps survive a
        # platform restart, which is the desired robustness story for
        # P-0005. Reconciliation at startup picks up surviving units.
        # No User= — user-scope units (`systemctl --user`) already run as
        # the invoking user; `User=` would be a setresuid call and fails
        # with status=216/GROUP.
        service_template = f"""[Unit]
Description=Latarnia Service - {app.manifest.name}
After=network.target
Wants=network.target

[Service]
Type=simple
WorkingDirectory={app.path}
ExecStart={' '.join(cmd_args)}
Restart={restart_policy}
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier={self.service_prefix}{app_id}
Environment=ENV={self.env}
"""

        # Add environment variables
        if env_vars:
            for env_var in env_vars:
                service_template += f"Environment={env_var}\n"

        # P-0006: reference the per-app filtered secrets file. Leading `-`
        # makes it ignore-if-missing, so apps with no `requires_secrets` are
        # unaffected (no file is ever written for them).
        if self.secret_manager is not None:
            secrets_file = self.secret_manager.per_app_path(app_id)
            service_template += f"EnvironmentFile=-{secrets_file}\n"

        service_template += "\n[Install]\nWantedBy=default.target\n"

        return service_template
    
    def create_service_file(self, app_id: str) -> bool:
        """
        Create systemd service file for an application
        
        Args:
            app_id: Application identifier
            
        Returns:
            True if service file created successfully
        """
        try:
            template = self.generate_service_template(app_id)
            if not template:
                return False
            
            service_name = f"{self.service_prefix}{app_id}.service"
            service_file = self.systemd_user_dir / service_name
            
            # Write service file
            with open(service_file, 'w') as f:
                f.write(template)
            
            # Reload systemd daemon
            result = subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                self.logger.error(f"Failed to reload systemd daemon: {result.stderr}")
                return False
            
            self.logger.info(f"Created service file for app {app_id}: {service_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to create service file for app {app_id}: {e}")
            return False
    
    def start_service(self, app_id: str) -> bool:
        """
        Start a systemd service for an application end-to-end.

        Allocates ports if needed, writes the unit file (daemon-reload), then
        runs `systemctl --user start`. Mirrors `SubprocessLauncher.start_service`
        so call sites can dispatch through `pick_launcher` uniformly.

        Args:
            app_id: Application identifier

        Returns:
            True if service started successfully
        """
        service_name = f"{self.service_prefix}{app_id}.service"

        try:
            app = self.app_manager.registry.get_app(app_id)
            if not app:
                self.logger.error(f"App {app_id} not found")
                return False
            if app.type != AppType.SERVICE:
                self.logger.error(f"App {app_id} is not a service app")
                return False

            # P-0006: refuse-to-start gate. Run BEFORE port allocation so a
            # missing secret leaves no port allocations behind.
            if self.secret_manager is not None:
                secret_result = self.secret_manager.validate(app)
                if not secret_result.ok:
                    self.logger.error(
                        "Refusing to start %s: %s", app_id, secret_result.detail,
                    )
                    app.runtime_info.error_message = secret_result.detail
                    self.app_manager.registry.update_app(
                        app_id,
                        status=AppStatus.ERROR,
                        runtime_info=app.runtime_info,
                    )
                    return False

            # Allocate ports if PortManager is wired and no port is set yet.
            allocated_port = False
            allocated_mcp_port = False
            if self.port_manager is not None and not app.runtime_info.assigned_port:
                port = self.port_manager.allocate_port(app_id, app.type)
                if not port:
                    self.logger.error(f"Failed to allocate port for app {app_id}")
                    return False
                app.runtime_info.assigned_port = port
                allocated_port = True

                if app.mcp_info and app.mcp_info.enabled and not app.mcp_info.mcp_port:
                    mcp_port = self.port_manager.allocate_mcp_port(app_id)
                    if not mcp_port:
                        self.logger.error(f"Failed to allocate MCP port for app {app_id}")
                        self.port_manager.release_port(app_id)
                        app.runtime_info.assigned_port = None
                        return False
                    app.mcp_info.mcp_port = mcp_port
                    allocated_mcp_port = True

            # Ensure the unit file exists and is up-to-date with the current
            # port assignment. create_service_file calls generate_service_template
            # and runs daemon-reload.
            if not self.create_service_file(app_id):
                if allocated_port and self.port_manager is not None:
                    self.port_manager.release_port(app_id)
                    app.runtime_info.assigned_port = None
                if allocated_mcp_port and self.port_manager is not None:
                    self.port_manager.release_mcp_port(app_id)
                    if app.mcp_info:
                        app.mcp_info.mcp_port = None
                return False

            # P-0006: write the per-app filtered secrets file referenced by
            # the unit's `EnvironmentFile=-...` line. Idempotent — overwrites
            # stale content from a previous launch with current master values.
            # Validation already ran above; this only fails on disk I/O.
            if self.secret_manager is not None:
                try:
                    self.secret_manager.materialize(app)
                except OSError as e:
                    self.logger.error(
                        "Failed to write per-app secrets file for %s: %s",
                        app_id, type(e).__name__,
                    )
                    if allocated_port and self.port_manager is not None:
                        self.port_manager.release_port(app_id)
                        app.runtime_info.assigned_port = None
                    if allocated_mcp_port and self.port_manager is not None:
                        self.port_manager.release_mcp_port(app_id)
                        if app.mcp_info:
                            app.mcp_info.mcp_port = None
                    return False

            # Now actually start the unit.
            result = subprocess.run(
                ["systemctl", "--user", "start", service_name],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                self.logger.info(f"Started service for app {app_id}")
                self.app_manager.registry.update_app(app_id, status=AppStatus.RUNNING)
                return True

            error_msg = f"Failed to start service: {result.stderr}"
            self.logger.error(f"Failed to start service for app {app_id}: {error_msg}")
            app.runtime_info.error_message = error_msg
            self.app_manager.registry.update_app(
                app_id,
                status=AppStatus.ERROR,
                runtime_info=app.runtime_info
            )
            if allocated_port and self.port_manager is not None:
                self.port_manager.release_port(app_id)
                app.runtime_info.assigned_port = None
            if allocated_mcp_port and self.port_manager is not None:
                self.port_manager.release_mcp_port(app_id)
                if app.mcp_info:
                    app.mcp_info.mcp_port = None
            return False

        except Exception as e:
            error_msg = f"Exception starting service: {str(e)}"
            self.logger.error(f"Failed to start service for app {app_id}: {error_msg}")
            app = self.app_manager.registry.get_app(app_id)
            if app:
                app.runtime_info.error_message = error_msg
                self.app_manager.registry.update_app(
                    app_id,
                    status=AppStatus.ERROR,
                    runtime_info=app.runtime_info
                )
            return False
    
    def stop_service(self, app_id: str) -> bool:
        """
        Stop a systemd service for an application
        
        Args:
            app_id: Application identifier
            
        Returns:
            True if service stopped successfully
        """
        try:
            service_name = f"{self.service_prefix}{app_id}.service"
            
            result = subprocess.run(
                ["systemctl", "--user", "stop", service_name],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                self.logger.info(f"Stopped service for app {app_id}")
                # Update app status
                self.app_manager.registry.update_app(app_id, status=AppStatus.STOPPED)
                return True
            else:
                self.logger.error(f"Failed to stop service for app {app_id}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to stop service for app {app_id}: {e}")
            return False
    
    def restart_service(self, app_id: str) -> bool:
        """
        Restart a systemd service for an application
        
        Args:
            app_id: Application identifier
            
        Returns:
            True if service restarted successfully
        """
        try:
            service_name = f"{self.service_prefix}{app_id}.service"
            
            result = subprocess.run(
                ["systemctl", "--user", "restart", service_name],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                self.logger.info(f"Restarted service for app {app_id}")
                # Update restart tracking
                if app_id in self.services:
                    self.services[app_id].restart_count += 1
                    self.services[app_id].last_restart = datetime.now()
                # Update app status
                self.app_manager.registry.update_app(app_id, status=AppStatus.RUNNING)
                return True
            else:
                self.logger.error(f"Failed to restart service for app {app_id}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to restart service for app {app_id}: {e}")
            return False
    
    def get_service_status(self, app_id: str) -> Optional[ServiceInfo]:
        """
        Get detailed status information for a service
        
        Args:
            app_id: Application identifier
            
        Returns:
            ServiceInfo object or None if failed
        """
        try:
            service_name = f"{self.service_prefix}{app_id}.service"
            
            # Get basic status
            result = subprocess.run(
                ["systemctl", "--user", "show", service_name, 
                 "--property=ActiveState,SubState,MainPID"],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                return None
            
            # Parse systemctl output
            properties = {}
            for line in result.stdout.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    properties[key] = value
            
            # Map systemd states to our enums
            status_map = {
                'active': ServiceStatus.ACTIVE,
                'inactive': ServiceStatus.INACTIVE,
                'failed': ServiceStatus.FAILED,
                'activating': ServiceStatus.ACTIVATING,
                'deactivating': ServiceStatus.DEACTIVATING
            }
            
            state_map = {
                'running': ServiceState.RUNNING,
                'exited': ServiceState.EXITED,
                'failed': ServiceState.FAILED,
                'dead': ServiceState.DEAD
            }
            
            status = status_map.get(properties.get('ActiveState', ''), ServiceStatus.UNKNOWN)
            state = state_map.get(properties.get('SubState', ''), ServiceState.UNKNOWN)
            
            # Get PID if running
            pid = None
            if properties.get('MainPID', '0') != '0':
                pid = int(properties['MainPID'])
            
            service_info = ServiceInfo(
                service_name=service_name,
                status=status,
                state=state,
                pid=pid
            )
            
            # Get additional metrics if service is running
            if pid and status == ServiceStatus.ACTIVE:
                service_info = self._get_process_metrics(service_info, pid)
            
            # Update tracking
            self.services[app_id] = service_info
            
            return service_info
            
        except Exception as e:
            self.logger.error(f"Failed to get service status for app {app_id}: {e}")
            return None
    
    def _get_process_metrics(self, service_info: ServiceInfo, pid: int) -> ServiceInfo:
        """
        Get process metrics for a running service
        
        Args:
            service_info: ServiceInfo object to update
            pid: Process ID
            
        Returns:
            Updated ServiceInfo object
        """
        try:
            # Get memory usage from /proc/pid/status
            status_file = Path(f"/proc/{pid}/status")
            if status_file.exists():
                with open(status_file, 'r') as f:
                    for line in f:
                        if line.startswith('VmRSS:'):
                            # Memory in kB, convert to bytes
                            memory_kb = int(line.split()[1])
                            service_info.memory_usage = memory_kb * 1024
                            break
            
            # Get CPU usage (simplified - would need sampling for accurate measurement)
            stat_file = Path(f"/proc/{pid}/stat")
            if stat_file.exists():
                with open(stat_file, 'r') as f:
                    stat_data = f.read().split()
                    # This is a simplified CPU calculation
                    # In production, you'd want to sample over time
                    utime = int(stat_data[13])  # User time
                    stime = int(stat_data[14])  # System time
                    # For now, just store raw values
                    service_info.cpu_percent = 0.0  # Placeholder
            
            # Get uptime from process start time
            stat_file = Path(f"/proc/{pid}/stat")
            if stat_file.exists():
                with open(stat_file, 'r') as f:
                    stat_data = f.read().split()
                    starttime = int(stat_data[21])  # Process start time in clock ticks
                    # Convert to uptime (simplified)
                    # In production, you'd calculate this properly
                    service_info.uptime = timedelta(seconds=0)  # Placeholder
            
        except Exception as e:
            self.logger.debug(f"Failed to get process metrics for PID {pid}: {e}")
        
        return service_info
    
    def get_service_logs(self, app_id: str, lines: int = 50) -> List[str]:
        """
        Get recent log entries for a per-app systemd user unit.

        Queries the system journal (no `--user` flag) by `_SYSTEMD_USER_UNIT`
        rather than `journalctl --user -u`, because user-mode persistent
        journald is not enabled by default on Raspberry Pi OS — without
        persistent storage `journalctl --user` returns "No journal files".
        The system journal always retains user-unit logs and is readable
        by the unit's own user without sudo.
        """
        try:
            service_name = f"{self.service_prefix}{app_id}.service"

            result = subprocess.run(
                [
                    "journalctl",
                    f"_SYSTEMD_USER_UNIT={service_name}",
                    "-n", str(lines),
                    "--no-pager",
                ],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                return result.stdout.strip().split('\n') if result.stdout.strip() else []
            self.logger.error(f"Failed to get logs for app {app_id}: {result.stderr}")
            return []

        except Exception as e:
            self.logger.error(f"Failed to get logs for app {app_id}: {e}")
            return []
    
    def enable_service(self, app_id: str) -> bool:
        """
        Enable a service to start automatically
        
        Args:
            app_id: Application identifier
            
        Returns:
            True if service enabled successfully
        """
        try:
            service_name = f"{self.service_prefix}{app_id}.service"
            
            result = subprocess.run(
                ["systemctl", "--user", "enable", service_name],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                self.logger.info(f"Enabled service for app {app_id}")
                return True
            else:
                self.logger.error(f"Failed to enable service for app {app_id}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to enable service for app {app_id}: {e}")
            return False
    
    def disable_service(self, app_id: str) -> bool:
        """
        Disable a service from starting automatically
        
        Args:
            app_id: Application identifier
            
        Returns:
            True if service disabled successfully
        """
        try:
            service_name = f"{self.service_prefix}{app_id}.service"
            
            result = subprocess.run(
                ["systemctl", "--user", "disable", service_name],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                self.logger.info(f"Disabled service for app {app_id}")
                return True
            else:
                self.logger.error(f"Failed to disable service for app {app_id}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to disable service for app {app_id}: {e}")
            return False
    
    def remove_service(self, app_id: str) -> bool:
        """
        Remove service file and stop service
        
        Args:
            app_id: Application identifier
            
        Returns:
            True if service removed successfully
        """
        try:
            service_name = f"{self.service_prefix}{app_id}.service"
            service_file = self.systemd_user_dir / service_name
            
            # Stop and disable service first
            self.stop_service(app_id)
            self.disable_service(app_id)
            
            # Remove service file
            if service_file.exists():
                service_file.unlink()
                self.logger.info(f"Removed service file for app {app_id}")
            
            # Reload systemd daemon
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True,
                text=True
            )
            
            # Remove from tracking
            if app_id in self.services:
                del self.services[app_id]
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to remove service for app {app_id}: {e}")
            return False
    
    def get_all_service_statuses(self) -> Dict[str, ServiceInfo]:
        """
        Get status for all managed services
        
        Returns:
            Dictionary mapping app_id to ServiceInfo
        """
        statuses = {}
        
        # Get all service apps from registry
        service_apps = self.app_manager.registry.get_apps_by_type(AppType.SERVICE)
        
        for app in service_apps:
            if app.status in [AppStatus.READY, AppStatus.RUNNING, AppStatus.STOPPED]:
                status = self.get_service_status(app.app_id)
                if status:
                    statuses[app.app_id] = status
        
        return statuses
    
    def get_service_statistics(self) -> dict:
        """
        Get service management statistics
        
        Returns:
            Dictionary with service statistics
        """
        statuses = self.get_all_service_statuses()
        
        status_counts = {}
        total_memory = 0
        running_services = 0
        
        for service_info in statuses.values():
            status_counts[service_info.status] = status_counts.get(service_info.status, 0) + 1
            
            if service_info.status == ServiceStatus.ACTIVE:
                running_services += 1
                if service_info.memory_usage:
                    total_memory += service_info.memory_usage
        
        return {
            'total_services': len(statuses),
            'running_services': running_services,
            'status_breakdown': status_counts,
            'total_memory_usage': total_memory,
            'average_memory_per_service': total_memory / running_services if running_services > 0 else 0
        }
