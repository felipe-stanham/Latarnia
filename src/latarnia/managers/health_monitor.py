"""
Health Monitor for Latarnia

Handles periodic health checks for service applications and tracks health status.
Provides configurable health check intervals and failure tracking.
Includes MCP server liveness probes for apps that declare mcp_server: true.
"""

import asyncio
import logging
import platform
import subprocess
import aiohttp
import psutil
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum

from ..core.config import ConfigManager
from .app_manager import AppManager, AppType, AppStatus, MCPInfo
from .service_manager import ServiceManager, ServiceStatus


class OverallStatus(str, Enum):
    """Combined dashboard status per P-0005 flow-03 rules table."""
    GREEN = "green"     # process alive AND /health good
    YELLOW = "yellow"   # degraded or transient (warning, activating, /health timeout)
    RED = "red"         # systemd failed, or /health error, or process dead
    GREY = "grey"       # stopped on purpose (systemd inactive, no data)


class HealthStatus(str, Enum):
    """Health check status values"""
    GOOD = "good"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check"""
    app_id: str
    status: HealthStatus
    message: str
    response_time: Optional[float] = None  # in seconds
    extra_info: Optional[dict] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'HealthCheckResult':
        """Create from dictionary"""
        if 'timestamp' in data and data['timestamp']:
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


@dataclass
class HealthCheckConfig:
    """Configuration for health checks"""
    enabled: bool = True
    interval: int = 30  # seconds
    timeout: int = 5  # seconds
    max_failures: int = 3
    failure_threshold: int = 2  # consecutive failures before marking as unhealthy
    startup_grace_period: int = 60  # seconds after start_at before failures count


class HealthMonitor:
    """Health monitoring system for service applications"""
    
    def __init__(self, config_manager: ConfigManager, app_manager: AppManager, service_manager: ServiceManager):
        self.config_manager = config_manager
        self.app_manager = app_manager
        self.service_manager = service_manager
        self.logger = logging.getLogger("latarnia.health_monitor")
        
        # Health check configuration
        self.config = HealthCheckConfig()
        
        # Health check tracking
        self.health_results: Dict[str, HealthCheckResult] = {}
        self.failure_counts: Dict[str, int] = {}
        self.last_check_times: Dict[str, datetime] = {}
        
        # Monitoring task
        self._monitoring_task: Optional[asyncio.Task] = None
        self._running = False
        
        # HTTP session for health checks
        self._session: Optional[aiohttp.ClientSession] = None

        # Cached systemd ActiveState map (populated on demand).
        # Time-based: re-fetched when the last-refresh age exceeds
        # `config.interval`. One batched systemctl call per interval,
        # shared across all /api/apps lookups within the window.
        self._systemd_states: Dict[str, str] = {}
        self._systemd_states_refreshed_at: Optional[datetime] = None

    async def start_monitoring(self):
        """Start the health monitoring system"""
        if self._running:
            self.logger.warning("Health monitoring is already running")
            return
        
        self._running = True
        self.logger.info("Starting health monitoring system")
        
        # Create HTTP session
        timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        self._session = aiohttp.ClientSession(timeout=timeout)
        
        # Start monitoring task
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())
    
    async def stop_monitoring(self):
        """Stop the health monitoring system"""
        if not self._running:
            return
        
        self._running = False
        self.logger.info("Stopping health monitoring system")
        
        # Cancel monitoring task
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
        
        # Close HTTP session
        if self._session:
            await self._session.close()
            self._session = None
    
    async def _monitoring_loop(self):
        """Main monitoring loop"""
        while self._running:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(self.config.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in health monitoring loop: {e}")
                await asyncio.sleep(self.config.interval)
    
    async def _perform_health_checks(self):
        """Perform health checks for all running service apps."""
        service_apps = self.app_manager.registry.get_apps_by_type(AppType.SERVICE)

        # Include RUNNING apps and ERROR apps whose process is still alive
        # (the latter allows recovery from transient startup failures).
        checkable = [
            app for app in service_apps
            if (
                app.status == AppStatus.RUNNING
                or (app.status == AppStatus.ERROR and self._is_process_alive(app))
            )
            and app.manifest.config
            and app.manifest.config.has_UI
        ]

        if not checkable:
            return

        tasks = [asyncio.create_task(self._check_app_health(app.app_id)) for app in checkable]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for app, result in zip(checkable, results):
            if isinstance(result, Exception):
                self.logger.error(f"Health check failed for app {app.app_id}: {result}")
                await self._handle_health_check_failure(app.app_id, str(result))
    
    def _is_process_alive(self, app) -> bool:
        """Return True if the app's tracked process is still running."""
        pid_str = getattr(app.runtime_info, "process_id", None)
        if not pid_str:
            return False
        try:
            return psutil.pid_exists(int(pid_str))
        except (ValueError, TypeError):
            return False

    async def _check_app_health(self, app_id: str) -> Optional[HealthCheckResult]:
        """
        Perform health check for a specific app
        
        Args:
            app_id: Application identifier
            
        Returns:
            HealthCheckResult or None if failed
        """
        app = self.app_manager.registry.get_app(app_id)
        if not app or not app.runtime_info.assigned_port:
            return None
        
        health_url = f"http://localhost:{app.runtime_info.assigned_port}/health"
        
        try:
            start_time = datetime.now()
            
            async with self._session.get(health_url) as response:
                response_time = (datetime.now() - start_time).total_seconds()
                
                if response.status == 200:
                    data = await response.json()
                    
                    # Parse health response according to spec
                    health_status = data.get('health', 'unknown')
                    message = data.get('message', 'No message provided')
                    extra_info = data.get('extra_info', {})
                    
                    # Map to our enum
                    status_map = {
                        'good': HealthStatus.GOOD,
                        'warning': HealthStatus.WARNING,
                        'error': HealthStatus.ERROR
                    }
                    status = status_map.get(health_status, HealthStatus.UNKNOWN)
                    
                    result = HealthCheckResult(
                        app_id=app_id,
                        status=status,
                        message=message,
                        response_time=response_time,
                        extra_info=extra_info
                    )
                    
                    # Update tracking
                    self.health_results[app_id] = result
                    self.last_check_times[app_id] = datetime.now()

                    # Reset failure count on successful check
                    if status in [HealthStatus.GOOD, HealthStatus.WARNING]:
                        self.failure_counts[app_id] = 0
                        # Recover from error state if the process was stuck there
                        if app.status == AppStatus.ERROR:
                            self.logger.info("App %s recovered from error state", app_id)
                            app.runtime_info.error_message = None
                            self.app_manager.registry.update_app(
                                app_id,
                                status=AppStatus.RUNNING,
                                runtime_info=app.runtime_info,
                            )
                    else:
                        await self._handle_health_check_failure(app_id, message)

                    # Update app registry with health info
                    app.runtime_info.last_health_check = datetime.now()
                    self.app_manager.registry.update_app(app_id, runtime_info=app.runtime_info)

                    # After /health passes, probe MCP server if enabled
                    if status in [HealthStatus.GOOD, HealthStatus.WARNING]:
                        await self._probe_mcp_health(app)

                    return result
                    
                else:
                    error_msg = f"HTTP {response.status}: {await response.text()}"
                    await self._handle_health_check_failure(app_id, error_msg)
                    return None
                    
        except asyncio.TimeoutError:
            error_msg = f"Health check timeout after {self.config.timeout}s"
            await self._handle_health_check_failure(app_id, error_msg)
            return None
        except Exception as e:
            error_msg = f"Health check error: {str(e)}"
            await self._handle_health_check_failure(app_id, error_msg)
            return None
    
    async def _probe_mcp_health(self, app) -> None:
        """
        Probe the MCP server for an app that declares mcp_server: true.

        Performs a basic HTTP GET to the app's declared mcp_port. If the
        server responds with any 2xx status, MCPInfo.healthy is set to True;
        otherwise it is set to False. The probe is skipped when the app has
        no MCP info or MCP is not enabled.

        Args:
            app: AppRegistryEntry for the app to probe
        """
        if not app.mcp_info or not app.mcp_info.enabled:
            return

        mcp_port = app.mcp_info.mcp_port
        if not mcp_port:
            self.logger.debug(
                f"App {app.app_id} has mcp_server enabled but no mcp_port declared, "
                f"skipping MCP probe"
            )
            return

        # Try common MCP endpoints in order of preference
        probe_paths = ["/sse", "/mcp", "/"]
        mcp_healthy = False

        for path in probe_paths:
            probe_url = f"http://localhost:{mcp_port}{path}"
            try:
                # Use a short timeout for the MCP probe (separate from /health timeout)
                mcp_timeout = aiohttp.ClientTimeout(total=3)
                async with self._session.get(probe_url, timeout=mcp_timeout) as response:
                    if 200 <= response.status < 300:
                        mcp_healthy = True
                        self.logger.debug(
                            f"MCP probe succeeded for app {app.app_id} on port {mcp_port}{path}"
                        )
                        break
            except asyncio.TimeoutError:
                self.logger.debug(
                    f"MCP probe timeout for app {app.app_id} on {probe_url}"
                )
                continue
            except Exception as e:
                self.logger.debug(
                    f"MCP probe error for app {app.app_id} on {probe_url}: {e}"
                )
                continue

        # Update MCPInfo in registry
        app.mcp_info.healthy = mcp_healthy
        self.app_manager.registry.update_app(app.app_id, mcp_info=app.mcp_info)

        if not mcp_healthy:
            self.logger.warning(
                f"MCP server probe failed for app {app.app_id} on port {mcp_port}"
            )

    async def _handle_health_check_failure(self, app_id: str, error_message: str):
        """
        Handle health check failure for an app

        Args:
            app_id: Application identifier
            error_message: Error message
        """
        # Skip counting failures during the startup grace period so transient
        # connection errors while an app is still initialising don't mark it
        # as permanently failed.
        app = self.app_manager.registry.get_app(app_id)
        if app and app.runtime_info.started_at:
            elapsed = (datetime.now() - app.runtime_info.started_at).total_seconds()
            if elapsed < self.config.startup_grace_period:
                remaining = self.config.startup_grace_period - elapsed
                self.logger.debug(
                    "App %s within startup grace period (%.0fs remaining), "
                    "ignoring failure: %s",
                    app_id, remaining, error_message,
                )
                return

        # Increment failure count
        self.failure_counts[app_id] = self.failure_counts.get(app_id, 0) + 1
        
        # Create error result
        result = HealthCheckResult(
            app_id=app_id,
            status=HealthStatus.ERROR,
            message=error_message
        )
        
        self.health_results[app_id] = result
        self.last_check_times[app_id] = datetime.now()
        
        # Check if we should take action
        failure_count = self.failure_counts[app_id]
        
        if failure_count >= self.config.failure_threshold:
            self.logger.warning(f"App {app_id} has failed {failure_count} consecutive health checks")
            
            # Update app status to error
            app = self.app_manager.registry.get_app(app_id)
            if app:
                app.runtime_info.error_message = f"Health check failures: {error_message}"
                self.app_manager.registry.update_app(
                    app_id,
                    status=AppStatus.ERROR,
                    runtime_info=app.runtime_info
                )
            
            # Check if we should restart the service
            if failure_count >= self.config.max_failures:
                self.logger.error(f"App {app_id} has exceeded max failures ({self.config.max_failures}), attempting restart")
                await self._attempt_service_restart(app_id)
    
    async def _attempt_service_restart(self, app_id: str):
        """
        Attempt to restart a failed service
        
        Args:
            app_id: Application identifier
        """
        try:
            self.logger.info(f"Attempting to restart service for app {app_id}")
            
            # Use service manager to restart
            success = self.service_manager.restart_service(app_id)
            
            if success:
                self.logger.info(f"Successfully restarted service for app {app_id}")
                # Reset failure count
                self.failure_counts[app_id] = 0
                
                # Wait a bit before next health check
                await asyncio.sleep(10)
            else:
                self.logger.error(f"Failed to restart service for app {app_id}")
                
        except Exception as e:
            self.logger.error(f"Exception during service restart for app {app_id}: {e}")
    
    def get_health_status(self, app_id: str) -> Optional[HealthCheckResult]:
        """
        Get latest health status for an app
        
        Args:
            app_id: Application identifier
            
        Returns:
            Latest HealthCheckResult or None
        """
        return self.health_results.get(app_id)
    
    def get_all_health_statuses(self) -> Dict[str, HealthCheckResult]:
        """
        Get health status for all monitored apps
        
        Returns:
            Dictionary mapping app_id to HealthCheckResult
        """
        return self.health_results.copy()
    
    def get_health_statistics(self) -> dict:
        """
        Get health monitoring statistics
        
        Returns:
            Dictionary with health statistics
        """
        if not self.health_results:
            return {
                'total_apps': 0,
                'healthy_apps': 0,
                'warning_apps': 0,
                'error_apps': 0,
                'unknown_apps': 0,
                'average_response_time': 0.0,
                'total_failures': 0
            }
        
        status_counts = {
            HealthStatus.GOOD: 0,
            HealthStatus.WARNING: 0,
            HealthStatus.ERROR: 0,
            HealthStatus.UNKNOWN: 0
        }
        
        total_response_time = 0.0
        response_time_count = 0
        total_failures = sum(self.failure_counts.values())
        
        for result in self.health_results.values():
            status_counts[result.status] += 1
            
            if result.response_time is not None:
                total_response_time += result.response_time
                response_time_count += 1
        
        avg_response_time = total_response_time / response_time_count if response_time_count > 0 else 0.0
        
        return {
            'total_apps': len(self.health_results),
            'healthy_apps': status_counts[HealthStatus.GOOD],
            'warning_apps': status_counts[HealthStatus.WARNING],
            'error_apps': status_counts[HealthStatus.ERROR],
            'unknown_apps': status_counts[HealthStatus.UNKNOWN],
            'average_response_time': round(avg_response_time, 3),
            'total_failures': total_failures
        }
    
    def update_config(self, **kwargs):
        """
        Update health check configuration
        
        Args:
            **kwargs: Configuration parameters to update
        """
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
                self.logger.info(f"Updated health check config: {key} = {value}")
    
    def is_monitoring(self) -> bool:
        """
        Check if health monitoring is currently running

        Returns:
            True if monitoring is active
        """
        return self._running

    # P-0005 Scope 3: combined systemd + /health status

    def get_systemd_states(self) -> Dict[str, str]:
        """
        Return {app_id: ActiveState} for every `latarnia-{env}-*.service` unit.

        One batched `systemctl --user show` call on Linux; empty dict on
        non-Linux hosts. Result is cached for one health-check interval so
        per-refresh cost is bounded regardless of app count. `env` is read
        from the associated `ServiceManager` (immutable for the process
        lifetime), so the cache is safe to share across callers.
        """
        if platform.system() != "Linux":
            return {}

        now = datetime.now()
        if (
            self._systemd_states_refreshed_at is not None
            and (now - self._systemd_states_refreshed_at).total_seconds() < self.config.interval
        ):
            return self._systemd_states

        env_value = self.service_manager.env
        prefix = f"latarnia-{env_value}-"
        pattern = f"{prefix}*.service"
        try:
            result = subprocess.run(
                [
                    "systemctl", "--user", "show",
                    "--property=Id,ActiveState,SubState",
                    "--type=service",
                    pattern,
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.logger.debug("systemctl not available; skipping systemd state fetch")
            self._systemd_states = {}
            self._systemd_states_refreshed_at = now
            return self._systemd_states

        if result.returncode != 0:
            self.logger.debug(
                "systemctl show returned %d: %s",
                result.returncode,
                result.stderr.strip(),
            )
            self._systemd_states = {}
            self._systemd_states_refreshed_at = now
            return self._systemd_states

        self._systemd_states = self._parse_systemctl_show(result.stdout, prefix)
        self._systemd_states_refreshed_at = now
        return self._systemd_states

    @staticmethod
    def _parse_systemctl_show(output: str, prefix: str) -> Dict[str, str]:
        """
        Parse the output of `systemctl show --property=Id,ActiveState,SubState`.

        `systemctl show` emits property lines for each unit separated by blank
        lines. Extract (Id, ActiveState) per block, strip the env prefix and
        `.service` suffix, and return {app_id: ActiveState}.
        """
        states: Dict[str, str] = {}
        current_id: Optional[str] = None
        current_active: Optional[str] = None

        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                if current_id and current_active:
                    if current_id.startswith(prefix) and current_id.endswith(".service"):
                        app_id = current_id[len(prefix):-len(".service")]
                        states[app_id] = current_active
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

        # Handle trailing block (output without terminating blank line).
        if current_id and current_active:
            if current_id.startswith(prefix) and current_id.endswith(".service"):
                app_id = current_id[len(prefix):-len(".service")]
                states[app_id] = current_active

        return states

    @staticmethod
    def _combine(systemd_state: Optional[str], health_result: Optional[HealthCheckResult]) -> Tuple[OverallStatus, str]:
        """
        Combine a systemd ActiveState with an app's /health result per flow-03.

        Args:
            systemd_state: `active`, `activating`, `inactive`, `failed`, or None
                (None means systemd is unavailable — e.g. macOS — in which
                case health_result alone drives the answer).
            health_result: latest /health response, or None if never probed or
                the app was not reachable.

        Returns:
            (OverallStatus, detail) matching the flow-03 rules table.
        """
        # Systemd-authoritative bad states first.
        if systemd_state == "failed":
            return OverallStatus.RED, "systemd unit failed"
        if systemd_state == "inactive":
            return OverallStatus.GREY, "stopped"
        if systemd_state == "activating":
            return OverallStatus.YELLOW, "starting"

        # From here: systemd_state is "active" OR None (non-Linux).
        if health_result is None:
            # No health info yet — treat as yellow on Linux (process alive but
            # unknown), grey on non-Linux (nothing to report).
            if systemd_state == "active":
                return OverallStatus.YELLOW, "/health unreachable"
            return OverallStatus.GREY, "no status"

        status_map = {
            HealthStatus.GOOD: (OverallStatus.GREEN, health_result.message or "healthy"),
            HealthStatus.WARNING: (OverallStatus.YELLOW, health_result.message or "degraded"),
            HealthStatus.ERROR: (OverallStatus.RED, health_result.message or "app error"),
            HealthStatus.UNKNOWN: (OverallStatus.YELLOW, health_result.message or "unknown"),
        }
        return status_map.get(
            health_result.status, (OverallStatus.YELLOW, health_result.message or "unknown")
        )

    def get_overall_status(self, app_id: str) -> Dict[str, str]:
        """
        Return `{overall_status, detail}` for a single app, combining systemd
        ActiveState with the latest /health result.

        Used by `/api/apps` to populate each app's `overall_status` field.

        Special case (P-0006 cap-005): when the app is in registry status
        ERROR with a `runtime_info.error_message` set, surface that as RED
        directly. This handles refuse-to-start cases (missing required
        secrets, etc.) where no systemd unit was ever written and no
        health probe ever ran — without this short-circuit such apps
        would surface as grey ("no status") and the operator would miss
        the actionable error.
        """
        app = self.app_manager.registry.get_app(app_id)
        if app is not None:
            if app.status == AppStatus.ERROR:
                err_msg = getattr(app.runtime_info, "error_message", None)
                if err_msg:
                    return {"overall_status": OverallStatus.RED.value, "detail": err_msg}
            # On macOS systemd is unavailable, so non-running registry states
            # must short-circuit before stale health_results produce a false green.
            if app.status in (AppStatus.STOPPED, AppStatus.DISCOVERED):
                return {"overall_status": OverallStatus.GREY.value, "detail": "stopped"}

        systemd_states = self.get_systemd_states()
        systemd_state = systemd_states.get(app_id) if systemd_states else None
        health_result = self.health_results.get(app_id)
        status, detail = self._combine(systemd_state, health_result)
        return {"overall_status": status.value, "detail": detail}
