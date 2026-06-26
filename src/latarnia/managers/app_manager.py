"""
App Manager for Latarnia

Handles application discovery, manifest parsing, and registry management.
Provides the core functionality for managing Latarnia applications.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..core.config import ConfigManager
from .port_manager import PortManager


class AppType(str, Enum):
    """Application types supported by Latarnia"""
    SERVICE = "service"
    STREAMLIT = "streamlit"


class AppStatus(str, Enum):
    """Application status values"""
    DISCOVERED = "discovered"
    INSTALLING = "installing"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class AppConfig(BaseModel):
    """Application configuration options"""
    has_UI: bool = False
    has_web_ui: bool = False
    redis_required: bool = False
    database: bool = False
    mcp_server: bool = False
    logs_dir: bool = False  # deprecated (P-0005 Scope 4): ignored. Apps log
                            # to stdout/stderr → journald (Linux) or to the
                            # subprocess log file (Darwin).
    data_dir: bool = False
    auto_start: bool = False
    restart_policy: str = Field(default="on-failure", pattern=r'^(always|on-failure|never)$')
    redis_streams_publish: List[str] = Field(default_factory=list)
    redis_streams_subscribe: List[str] = Field(default_factory=list)
    # P-0006: names of environment variables the platform must inject from
    # the per-env master secrets file (`/opt/latarnia/{env}/secrets.env`)
    # at app launch. Apps refuse to start if any declared name is missing
    # from the master file. Values never appear in logs or REST responses.
    requires_secrets: List[str] = Field(default_factory=list)
    # T-0004: path prefixes served without forward_auth. Validated at parse
    # time — invalid entries are dropped with a WARNING so a typo never
    # fails the app. Reserved paths (/health, /docs, /openapi.json) are also
    # rejected. The `/b/` prefix is the platform convention for public bundles.
    public_routes: List[str] = Field(default_factory=list)

    @field_validator("requires_secrets")
    @classmethod
    def _no_empty_secret_names(cls, v: List[str]) -> List[str]:
        for name in v:
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    "requires_secrets entries must be non-empty strings"
                )
        return v

    @field_validator("public_routes")
    @classmethod
    def _validate_public_routes(cls, v: List[str]) -> List[str]:
        import logging as _logging
        _log = _logging.getLogger("latarnia.app_manager")
        _RESERVED = {"/health", "/docs", "/openapi.json"}
        result: List[str] = []
        for entry in v:
            if not entry:
                _log.warning("public_routes: skipping empty entry")
                continue
            if not entry.startswith("/"):
                _log.warning("public_routes: skipping %r — must start with /", entry)
                continue
            if entry == "/":
                _log.warning("public_routes: skipping '/' — would make whole app public")
                continue
            if entry in _RESERVED:
                _log.warning("public_routes: skipping %r — reserved path", entry)
                continue
            result.append(entry)
        return result


class AppInstall(BaseModel):
    """Application installation configuration"""
    setup_commands: Optional[List[str]] = None


class ManifestDependency(BaseModel):
    """Dependency declaration in app manifest"""
    app: str = Field(..., min_length=1)
    min_version: str = Field(..., pattern=r'^\d+\.\d+\.\d+$')


class AppManifest(BaseModel):
    """Application manifest schema (latarnia.json)"""
    name: str = Field(..., min_length=1, max_length=50)
    type: AppType
    description: str = Field(..., min_length=1, max_length=200)
    version: str = Field(..., pattern=r'^\d+\.\d+\.\d+$')
    author: str = Field(..., min_length=1, max_length=100)
    main_file: str = Field(..., min_length=1)
    config: Optional[AppConfig] = Field(default_factory=AppConfig)
    install: Optional[AppInstall] = Field(default_factory=AppInstall)
    requires: List[ManifestDependency] = Field(default_factory=list)

    class Config:
        use_enum_values = True


@dataclass
class AppRuntimeInfo:
    """Runtime information for an application"""
    assigned_port: Optional[int] = None
    process_id: Optional[str] = None
    service_name: Optional[str] = None
    started_at: Optional[datetime] = None
    last_health_check: Optional[datetime] = None
    resource_usage: Optional[Dict[str, float]] = None
    error_message: Optional[str] = None
    service_status: Optional[str] = None  # systemd service status
    health_status: Optional[str] = None   # health check status
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        if self.started_at:
            data['started_at'] = self.started_at.isoformat()
        if self.last_health_check:
            data['last_health_check'] = self.last_health_check.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AppRuntimeInfo':
        """Create from dictionary"""
        if 'started_at' in data and data['started_at']:
            data['started_at'] = datetime.fromisoformat(data['started_at'])
        if 'last_health_check' in data and data['last_health_check']:
            data['last_health_check'] = datetime.fromisoformat(data['last_health_check'])
        return cls(**data)


@dataclass
class DatabaseInfo:
    """Database provisioning info for an app"""
    provisioned: bool = False
    database_name: Optional[str] = None
    role_name: Optional[str] = None
    connection_url: Optional[str] = None
    applied_migrations: List[str] = field(default_factory=list)
    last_migration_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.last_migration_at:
            data['last_migration_at'] = self.last_migration_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> 'DatabaseInfo':
        if data.get('last_migration_at'):
            data['last_migration_at'] = datetime.fromisoformat(data['last_migration_at'])
        return cls(**data)


@dataclass
class MCPInfo:
    """MCP server info for an app"""
    enabled: bool = False
    mcp_port: Optional[int] = None
    healthy: bool = False
    registered_tools: List[str] = field(default_factory=list)
    last_tool_sync: Optional[datetime] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.last_tool_sync:
            data['last_tool_sync'] = self.last_tool_sync.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> 'MCPInfo':
        if data.get('last_tool_sync'):
            data['last_tool_sync'] = datetime.fromisoformat(data['last_tool_sync'])
        return cls(**data)


@dataclass
class StreamInfo:
    """Redis Streams info for an app"""
    publish_streams: List[str] = field(default_factory=list)
    subscribe_streams: List[str] = field(default_factory=list)
    consumer_groups: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'StreamInfo':
        return cls(**data)


@dataclass
class DependencyStatus:
    """Resolved dependency status for a registered app"""
    app: str = ""
    min_version: str = ""
    satisfied: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'DependencyStatus':
        return cls(**data)


@dataclass
class AppRegistryEntry:
    """Registry entry for a discovered application"""
    app_id: str
    name: str
    type: AppType
    description: str
    version: str
    status: AppStatus
    path: Path
    manifest: AppManifest
    runtime_info: AppRuntimeInfo = field(default_factory=AppRuntimeInfo)
    database_info: Optional[DatabaseInfo] = None
    mcp_info: Optional[MCPInfo] = None
    stream_info: Optional[StreamInfo] = None
    dependencies: List[DependencyStatus] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        data['path'] = str(self.path)
        data['manifest'] = self.manifest.model_dump()
        data['runtime_info'] = self.runtime_info.to_dict()
        data['database_info'] = self.database_info.to_dict() if self.database_info else None
        data['mcp_info'] = self.mcp_info.to_dict() if self.mcp_info else None
        data['stream_info'] = self.stream_info.to_dict() if self.stream_info else None
        data['dependencies'] = [d.to_dict() for d in self.dependencies]
        data['discovered_at'] = self.discovered_at.isoformat()
        data['last_updated'] = self.last_updated.isoformat()
        data['type'] = self.type if isinstance(self.type, str) else self.type.value
        data['status'] = self.status if isinstance(self.status, str) else self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> 'AppRegistryEntry':
        """Create from dictionary"""
        data['path'] = Path(data['path'])
        data['manifest'] = AppManifest(**data['manifest'])
        data['runtime_info'] = AppRuntimeInfo.from_dict(data['runtime_info'])
        db = data.pop('database_info', None)
        data['database_info'] = DatabaseInfo.from_dict(db) if db else None
        mcp = data.pop('mcp_info', None)
        data['mcp_info'] = MCPInfo.from_dict(mcp) if mcp else None
        si = data.pop('stream_info', None)
        data['stream_info'] = StreamInfo.from_dict(si) if si else None
        deps = data.pop('dependencies', [])
        data['dependencies'] = [DependencyStatus.from_dict(d) for d in deps] if deps else []
        data['discovered_at'] = datetime.fromisoformat(data['discovered_at'])
        data['last_updated'] = datetime.fromisoformat(data['last_updated'])
        data['type'] = AppType(data['type'])
        data['status'] = AppStatus(data['status'])
        return cls(**data)


class AppRegistry:
    """In-memory application registry"""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.logger = logging.getLogger("latarnia.app_registry")
        
        # Registry storage (in-memory only)
        self.apps: Dict[str, AppRegistryEntry] = {}
        
        self.logger.info("Initialized in-memory app registry")
    
    def register_app(self, entry: AppRegistryEntry) -> bool:
        """Register a new application"""
        try:
            self.apps[entry.app_id] = entry
            self.logger.info(f"Registered app {entry.app_id} ({entry.name})")
            return True
        except Exception as e:
            self.logger.error(f"Failed to register app {entry.app_id}: {e}")
            return False
    
    def update_app(self, app_id: str, **kwargs) -> bool:
        """Update an existing application"""
        if app_id not in self.apps:
            return False
        
        try:
            entry = self.apps[app_id]
            for key, value in kwargs.items():
                if hasattr(entry, key):
                    setattr(entry, key, value)
            
            entry.last_updated = datetime.now()
            self.logger.debug(f"Updated app {app_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to update app {app_id}: {e}")
            return False
    
    def unregister_app(self, app_id: str) -> bool:
        """Unregister an application"""
        if app_id in self.apps:
            del self.apps[app_id]
            self.logger.info(f"Unregistered app {app_id}")
            return True
        return False
    
    def get_app(self, app_id: str) -> Optional[AppRegistryEntry]:
        """Get an application by ID"""
        return self.apps.get(app_id)
    
    def get_all_apps(self) -> List[AppRegistryEntry]:
        """Get all registered applications"""
        return list(self.apps.values())
    
    def get_apps_by_type(self, app_type: AppType) -> List[AppRegistryEntry]:
        """Get applications by type"""
        return [app for app in self.apps.values() if app.type == app_type]
    
    def get_apps_by_status(self, status: AppStatus) -> List[AppRegistryEntry]:
        """Get applications by status"""
        return [app for app in self.apps.values() if app.status == status]

    def get_app_by_name(self, name: str) -> Optional[AppRegistryEntry]:
        """Get an application by manifest name"""
        for app in self.apps.values():
            if app.name == name:
                return app
        return None

    def get_app_by_path(self, path: Path) -> Optional[AppRegistryEntry]:
        """Get an application by its filesystem path"""
        for app in self.apps.values():
            if app.path == path:
                return app
        return None


def _parse_semver(version: str) -> tuple:
    """Parse 'X.Y.Z' into (X, Y, Z) integer tuple for comparison."""
    return tuple(int(p) for p in version.split('.'))


class AppManager:
    """Main application manager for discovery and lifecycle management"""

    def __init__(self, config_manager: ConfigManager, port_manager: PortManager,
                 db_provisioner=None, stream_manager=None):
        self.config_manager = config_manager
        self.port_manager = port_manager
        self.db_provisioner = db_provisioner
        self.stream_manager = stream_manager
        self.mcp_gateway = None  # Set from main.py after gateway creation
        self.registry = AppRegistry(config_manager)
        self.logger = logging.getLogger("latarnia.app_manager")

        # Apps directory
        self.apps_dir = Path.cwd() / "apps"
        self.apps_dir.mkdir(exist_ok=True)
    
    def discover_apps(self) -> int:
        """
        Discover applications in the apps directory
        
        Returns:
            Number of apps discovered
        """
        discovered_count = 0
        
        try:
            self.logger.info(f"Scanning for apps in {self.apps_dir}")

            # Sort app directories: apps without dependencies first so
            # that dependency checks succeed regardless of filesystem order.
            app_dirs = sorted(
                (p for p in self.apps_dir.iterdir() if p.is_dir()),
                key=lambda p: self._has_dependencies(p),
            )
            for app_path in app_dirs:
                
                manifest_file = app_path / "latarnia.json"
                if not manifest_file.exists():
                    # Backward compatibility: accept homehelper.json with deprecation warning
                    legacy_manifest = app_path / "homehelper.json"
                    if legacy_manifest.exists():
                        self.logger.warning(
                            f"App '{app_path.name}' uses deprecated 'homehelper.json' manifest. "
                            f"Rename to 'latarnia.json'."
                        )
                        manifest_file = legacy_manifest
                    else:
                        self.logger.debug(f"No manifest found in {app_path.name}")
                        continue
                
                try:
                    # Parse manifest
                    manifest = self._parse_manifest(manifest_file)
                    if not manifest:
                        continue
                    
                    # Check if app is already registered (by path — stable identifier)
                    existing_app = self.registry.get_app_by_path(app_path)
                    if existing_app:
                        if existing_app.version != manifest.version:
                            self._update_existing_app(existing_app, manifest, app_path)
                        continue

                    # Generate app ID for new apps only
                    app_id = self._generate_app_id(manifest.name, app_path.name)

                    # Check dependencies
                    resolved_deps = []
                    deps_satisfied = True
                    for dep in manifest.requires:
                        dep_app = self.registry.get_app_by_name(dep.app)
                        if not dep_app:
                            self.logger.error(
                                f"App '{manifest.name}' requires '{dep.app}' which is not registered"
                            )
                            deps_satisfied = False
                            break
                        if _parse_semver(dep_app.version) < _parse_semver(dep.min_version):
                            self.logger.error(
                                f"App '{manifest.name}' requires '{dep.app}' >= {dep.min_version}, "
                                f"found {dep_app.version}"
                            )
                            deps_satisfied = False
                            break
                        resolved_deps.append(DependencyStatus(
                            app=dep.app, min_version=dep.min_version, satisfied=True
                        ))
                    if not deps_satisfied:
                        continue

                    # Build new registry info from manifest
                    database_info = None
                    if manifest.config and manifest.config.database:
                        if self.db_provisioner:
                            result = self.db_provisioner.provision_database(manifest.name, app_path)
                            if not result.success:
                                self.logger.error(
                                    f"DB provisioning failed for {manifest.name}: {result.error_message}"
                                )
                                continue
                            database_info = DatabaseInfo(
                                provisioned=True,
                                database_name=result.database_name,
                                role_name=result.role_name,
                                connection_url=result.connection_url,
                                applied_migrations=result.applied_migrations,
                                last_migration_at=datetime.now() if result.applied_migrations else None,
                            )

                        else:
                            self.logger.warning(
                                f"App {manifest.name} requires database but no provisioner configured"
                            )
                            database_info = DatabaseInfo()
                    mcp_info = (
                        MCPInfo(enabled=True)
                        if manifest.config and manifest.config.mcp_server else None
                    )
                    stream_info = None
                    if manifest.config and (
                        manifest.config.redis_streams_publish or manifest.config.redis_streams_subscribe
                    ):
                        publish_streams = manifest.config.redis_streams_publish
                        subscribe_streams = manifest.config.redis_streams_subscribe
                        consumer_groups: List[str] = []

                        if self.stream_manager:
                            from .stream_manager import PublisherCollisionError
                            try:
                                result = self.stream_manager.setup_streams(
                                    manifest.name, app_id, publish_streams, subscribe_streams
                                )
                                if not result.success:
                                    self.logger.error(
                                        f"Stream setup failed for {manifest.name}: {result.error_message}"
                                    )
                                    continue
                                consumer_groups = result.consumer_groups
                            except PublisherCollisionError as e:
                                self.logger.error(str(e))
                                continue
                        else:
                            if publish_streams or subscribe_streams:
                                self.logger.warning(
                                    f"App {manifest.name} declares redis streams but "
                                    f"no stream manager configured"
                                )

                        stream_info = StreamInfo(
                            publish_streams=publish_streams,
                            subscribe_streams=subscribe_streams,
                            consumer_groups=consumer_groups,
                        )

                    # Create new registry entry
                    entry = AppRegistryEntry(
                        app_id=app_id,
                        name=manifest.name,
                        type=manifest.type,
                        description=manifest.description,
                        version=manifest.version,
                        status=AppStatus.DISCOVERED,
                        path=app_path,
                        manifest=manifest,
                        database_info=database_info,
                        mcp_info=mcp_info,
                        stream_info=stream_info,
                        dependencies=resolved_deps,
                    )
                    
                    # Register the app
                    if self.registry.register_app(entry):
                        discovered_count += 1
                        self.logger.info(f"Discovered new app: {manifest.name} ({app_id})")
                    
                except Exception as e:
                    self.logger.error(f"Failed to process app in {app_path}: {e}")
                    continue
            
            self.logger.info(f"Discovery complete: {discovered_count} new apps found")
            return discovered_count
            
        except Exception as e:
            self.logger.error(f"App discovery failed: {e}")
            return 0
    
    @staticmethod
    def _has_dependencies(app_path: Path) -> bool:
        """Return True if the app declares dependencies (used for discovery ordering)."""
        manifest_file = app_path / "latarnia.json"
        if not manifest_file.exists():
            manifest_file = app_path / "homehelper.json"
        if not manifest_file.exists():
            return False
        try:
            import json
            data = json.loads(manifest_file.read_text())
            return bool(data.get("requires"))
        except Exception:
            return False

    def _parse_manifest(self, manifest_file: Path) -> Optional[AppManifest]:
        """Parse and validate application manifest"""
        try:
            with open(manifest_file, 'r') as f:
                data = json.load(f)

            # Reject manifests that declare mcp_port (now dynamically allocated)
            if data.get('config', {}).get('mcp_port') is not None:
                self.logger.error(
                    f"Manifest {manifest_file} declares 'mcp_port' which is no longer supported. "
                    f"Remove 'mcp_port' from config — the platform allocates MCP ports dynamically."
                )
                return None

            # Validate with Pydantic
            manifest = AppManifest(**data)

            # Additional validation
            app_path = manifest_file.parent
            main_file = app_path / manifest.main_file
            if not main_file.exists():
                self.logger.error(f"Main file {manifest.main_file} not found in {app_path}")
                return None
            
            # Check for requirements.txt (default if not specified)
            req_file = app_path / "requirements.txt"
            if not req_file.exists():
                self.logger.warning(f"Requirements file requirements.txt not found in {app_path}")
            
            return manifest
            
        except ValidationError as e:
            self.logger.error(f"Invalid manifest in {manifest_file}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to parse manifest {manifest_file}: {e}")
            return None
    
    def _generate_app_id(self, app_name: str, dir_name: str) -> str:
        """Generate unique app ID from name and directory"""
        # Use directory name as base, fallback to app name
        base_id = dir_name.lower().replace(' ', '-').replace('_', '-')
        
        # Ensure uniqueness
        app_id = base_id
        counter = 1
        while self.registry.get_app(app_id):
            app_id = f"{base_id}-{counter}"
            counter += 1
        
        return app_id
    
    def _update_existing_app(self, existing_app: AppRegistryEntry, manifest: AppManifest, app_path: Path) -> None:
        """Update an existing app with new manifest or path"""
        is_version_bump = existing_app.version != manifest.version

        # Handle version bump migrations if app has a provisioned database
        if (
            existing_app.database_info
            and existing_app.database_info.provisioned
            and self.db_provisioner
            and is_version_bump
        ):
            # Stop the app before running migrations
            was_running = existing_app.status == AppStatus.RUNNING
            if was_running:
                self.logger.info(f"Stopping {existing_app.name} for version bump migration")
                self.registry.update_app(existing_app.app_id, status=AppStatus.STOPPING)

            success, new_migs, error = self.db_provisioner.run_version_bump_migrations(
                existing_app.database_info.database_name, app_path
            )
            if not success:
                self.logger.error(
                    f"Migration failed during version bump for {existing_app.name}: {error}"
                )
                self.registry.update_app(existing_app.app_id, status=AppStatus.ERROR)
                return
            if new_migs:
                existing_app.database_info.applied_migrations.extend(new_migs)
                existing_app.database_info.last_migration_at = datetime.now()

        # Preserve old MCP tools for backward compatibility check on restart.
        # The actual check runs in MCPGateway.on_app_started() after the app
        # restarts with the new code and exposes its updated tool list.
        if is_version_bump and existing_app.mcp_info and existing_app.mcp_info.registered_tools:
            self.logger.info(
                "Version bump for MCP app %s — old tools preserved for "
                "backward compatibility check: %s",
                existing_app.name, existing_app.mcp_info.registered_tools,
            )

        self.registry.update_app(
            existing_app.app_id,
            manifest=manifest,
            path=app_path,
            version=manifest.version,
            description=manifest.description,
            status=AppStatus.DISCOVERED
        )
        self.logger.info(f"Updated existing app: {existing_app.app_id}")
    
    def install_app_dependencies(self, app_id: str) -> bool:
        """
        Install Python dependencies for an application
        
        Args:
            app_id: Application identifier
            
        Returns:
            True if installation successful
        """
        app = self.registry.get_app(app_id)
        if not app:
            self.logger.error(f"App {app_id} not found")
            return False
        
        # Always use requirements.txt as default
        requirements_file = app.path / "requirements.txt"
        if not requirements_file.exists():
            self.logger.info(f"No requirements.txt found for app {app_id}, skipping dependency installation")
            return True
        
        try:
            self.registry.update_app(app_id, status=AppStatus.INSTALLING)
            self.logger.info(f"Installing dependencies for app {app_id}")
            
            # Install dependencies using pip
            cmd = [
                sys.executable, "-m", "pip", "install",
                "-r", str(requirements_file),
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=app.path,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode == 0:
                self.registry.update_app(app_id, status=AppStatus.READY)
                self.logger.info(f"Dependencies installed successfully for app {app_id}")
                return True
            else:
                error_msg = f"Dependency installation failed: {result.stderr}"
                self.registry.update_app(app_id, status=AppStatus.ERROR)
                self.registry.apps[app_id].runtime_info.error_message = error_msg
                self.logger.error(f"Failed to install dependencies for app {app_id}: {error_msg}")
                return False
                
        except subprocess.TimeoutExpired:
            error_msg = "Dependency installation timed out"
            self.registry.update_app(app_id, status=AppStatus.ERROR)
            self.registry.apps[app_id].runtime_info.error_message = error_msg
            self.logger.error(f"Dependency installation timed out for app {app_id}")
            return False
        except Exception as e:
            error_msg = f"Dependency installation error: {str(e)}"
            self.registry.update_app(app_id, status=AppStatus.ERROR)
            self.registry.apps[app_id].runtime_info.error_message = error_msg
            self.logger.error(f"Failed to install dependencies for app {app_id}: {e}")
            return False
    
    def run_setup_commands(self, app_id: str) -> bool:
        """
        Run setup commands for an application
        
        Args:
            app_id: Application identifier
            
        Returns:
            True if setup successful
        """
        app = self.registry.get_app(app_id)
        if not app or not app.manifest.install or not app.manifest.install.setup_commands:
            return True
        
        try:
            self.logger.info(f"Running setup commands for app {app_id}")
            venv_bin = str(Path(sys.executable).parent)
            env = os.environ.copy()
            env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")

            for command in app.manifest.install.setup_commands:
                self.logger.debug(f"Running setup command: {command}")

                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=app.path,
                    env=env,
                    timeout=300,
                )
                
                if result.returncode != 0:
                    error_msg = f"Setup command failed: {command}\n{result.stderr}"
                    self.registry.apps[app_id].runtime_info.error_message = error_msg
                    self.logger.error(f"Setup command failed for app {app_id}: {error_msg}")
                    return False
            
            self.logger.info(f"Setup commands completed successfully for app {app_id}")
            return True
            
        except Exception as e:
            error_msg = f"Setup command error: {str(e)}"
            self.registry.apps[app_id].runtime_info.error_message = error_msg
            self.logger.error(f"Failed to run setup commands for app {app_id}: {e}")
            return False
    
    def prepare_app(self, app_id: str) -> bool:
        """
        Prepare an application for running (install dependencies, run setup)
        
        Args:
            app_id: Application identifier
            
        Returns:
            True if preparation successful
        """
        app = self.registry.get_app(app_id)
        if not app:
            return False
        
        if app.status == AppStatus.READY:
            return True
        
        # Install dependencies
        if not self.install_app_dependencies(app_id):
            return False
        
        # Run setup commands
        if not self.run_setup_commands(app_id):
            self.registry.update_app(app_id, status=AppStatus.ERROR)
            return False
        
        # Allocate port for service apps
        if app.type == AppType.SERVICE:
            port = self.port_manager.allocate_port(app_id, app.type)
            if port:
                app.runtime_info.assigned_port = port
                self.registry.update_app(app_id, runtime_info=app.runtime_info)
            else:
                error_msg = "Failed to allocate port"
                app.runtime_info.error_message = error_msg
                self.registry.update_app(app_id, status=AppStatus.ERROR, runtime_info=app.runtime_info)
                return False
        
        self.registry.update_app(app_id, status=AppStatus.READY)
        return True
    
    def unregister_app(self, app_id: str) -> bool:
        """Unregister an app and clean up its stream resources."""
        app = self.registry.get_app(app_id)
        if not app:
            return False

        # Clean up stream resources before removing from registry
        if self.stream_manager and app.stream_info:
            self.stream_manager.cleanup_app(app_id)
            self.logger.info(f"Cleaned up stream resources for app {app_id}")

        return self.registry.unregister_app(app_id)

    def get_orphaned_apps(self) -> List['AppRegistryEntry']:
        """Return registered apps whose app directory no longer exists on disk."""
        return [app for app in self.registry.get_all_apps() if not app.path.exists()]

    def get_app_statistics(self) -> dict:
        """Get application statistics"""
        all_apps = self.registry.get_all_apps()
        
        status_counts = {}
        type_counts = {}
        
        for app in all_apps:
            status_counts[app.status] = status_counts.get(app.status, 0) + 1
            type_counts[app.type] = type_counts.get(app.type, 0) + 1
        
        return {
            'total_apps': len(all_apps),
            'status_breakdown': status_counts,
            'type_breakdown': type_counts,
            'ready_apps': len(self.registry.get_apps_by_status(AppStatus.READY)),
            'running_apps': len(self.registry.get_apps_by_status(AppStatus.RUNNING)),
            'error_apps': len(self.registry.get_apps_by_status(AppStatus.ERROR))
        }
