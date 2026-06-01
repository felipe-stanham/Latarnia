"""
Configuration management for Latarnia
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0


class EventSubscriberConfig(BaseModel):
    max_events: int = 100
    channels: list[str] = ["latarnia:events:*"]


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"


class PortRange(BaseModel):
    start: int = 8100
    end: int = 8199


class MCPPortRange(BaseModel):
    start: int = 9001
    end: int = 9099


class ProcessManagerConfig(BaseModel):
    data_dir: str = "/opt/latarnia/data"
    logs_dir: str = "/opt/latarnia/logs"
    streamlit_port: int = 8501
    streamlit_ttl_seconds: int = 300
    port_range: PortRange = Field(default_factory=PortRange)
    mcp_port_range: MCPPortRange = Field(default_factory=MCPPortRange)


class PostgresConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    superuser: str = ""
    superuser_password: str = ""
    database_prefix: str = "latarnia_"
    role_prefix: str = "latarnia_"


class SystemConfig(BaseModel):
    main_port: int = 8000
    host: str = "0.0.0.0"


class MCPConfig(BaseModel):
    enabled: bool = False
    transport: str = "sse"
    gateway_path: str = "/mcp"
    # Reserved for future periodic tool resync; currently tools sync on lifecycle events only
    tool_sync_interval_seconds: int = 300


class AuthConfig(BaseModel):
    """Authentication / authorization settings (P-0008)."""
    # Session cookie lifetime. Sessions live in the platform DB; this is the
    # TTL stamped on each session row at login.
    session_ttl_hours: int = 8
    # One-time setup-token lifetime for invited users.
    setup_token_ttl_hours: int = 24
    cookie_name: str = "latarnia_session"
    # TOTP issuer label shown in authenticator apps.
    totp_issuer: str = "Latarnia"


class LatarniaConfig(BaseSettings):
    redis: RedisConfig = Field(default_factory=RedisConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    event_subscriber: EventSubscriberConfig = Field(default_factory=EventSubscriberConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    process_manager: ProcessManagerConfig = Field(default_factory=ProcessManagerConfig)
    health_check_interval_seconds: int = 60
    system: SystemConfig = Field(default_factory=SystemConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    class Config:
        env_prefix = "LATARNIA_"
        case_sensitive = False


class ConfigManager:
    """Manages Latarnia configuration from JSON file and environment variables"""
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path("config/config.json")
        self._config: Optional[LatarniaConfig] = None
        self.logger = logging.getLogger("latarnia.config")
    
    def load_config(self) -> LatarniaConfig:
        """Load configuration from file and environment variables"""
        config_data = {}
        
        # Load from JSON file if it exists
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    config_data = json.load(f)
                self.logger.info(f"Loaded configuration from {self.config_path}")
            except Exception as e:
                self.logger.error(f"Failed to load config from {self.config_path}: {e}")
        else:
            self.logger.warning(f"Config file not found at {self.config_path}, using defaults")
        
        # Create config object (will also load from environment variables)
        self._config = LatarniaConfig(**config_data)
        return self._config
    
    @property
    def config(self) -> LatarniaConfig:
        """Get current configuration, loading if necessary"""
        if self._config is None:
            self.load_config()
        return self._config
    
    def save_config(self, config_path: Optional[Path] = None) -> None:
        """Save current configuration to JSON file"""
        if self._config is None:
            raise ValueError("No configuration loaded")
        
        save_path = config_path or self.config_path
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(save_path, 'w') as f:
                json.dump(self._config.model_dump(), f, indent=2)
            self.logger.info(f"Saved configuration to {save_path}")
        except Exception as e:
            self.logger.error(f"Failed to save config to {save_path}: {e}")
            raise
    
    def get_redis_url(self) -> str:
        """Get Redis connection URL"""
        redis_config = self.config.redis
        return f"redis://{redis_config.host}:{redis_config.port}/{redis_config.db}"
    
    def get_postgres_dsn(self, dbname: str = "postgres") -> str:
        """Build a Postgres DSN for superuser connections"""
        pg = self.config.postgres
        if pg.superuser and pg.superuser_password:
            return f"postgresql://{pg.superuser}:{pg.superuser_password}@{pg.host}:{pg.port}/{dbname}"
        elif pg.superuser:
            return f"postgresql://{pg.superuser}@{pg.host}:{pg.port}/{dbname}"
        else:
            return f"postgresql://@{pg.host}:{pg.port}/{dbname}"

    def get_env(self) -> str:
        """Resolve the active environment (dev | tst | prd).

        Read from the `ENV` environment variable; anything outside the
        known set falls back to `dev`. Matches ServiceManager/SecretManager
        env resolution so all platform components agree on the environment.
        """
        env = os.environ.get("ENV", "dev").lower()
        return env if env in ("dev", "tst", "prd") else "dev"

    def get_domain(self) -> str:
        """Resolve the public domain for the active environment.

        Reads `{ENV}_DOMAIN` (e.g. `PRD_DOMAIN`, `TST_DOMAIN`) from the
        environment. When unset, `dev` defaults to `localhost`; other envs
        also fall back to `localhost` so a misconfigured host stays on a
        self-signed cert rather than attempting ACME against an empty name.
        """
        env = self.get_env()
        domain = os.environ.get(f"{env.upper()}_DOMAIN", "").strip()
        return domain or "localhost"

    def get_platform_db_name(self) -> str:
        """Name of the platform auth DB for the active environment."""
        return f"latarnia_platform_{self.get_env()}"

    def get_data_dir(self, app_name: Optional[str] = None) -> Path:
        """Get data directory path, optionally for specific app"""
        base_dir = Path(self.config.process_manager.data_dir)
        if app_name:
            return base_dir / app_name
        return base_dir
    
    def get_logs_dir(self, app_name: Optional[str] = None) -> Path:
        """Get logs directory path, optionally for specific app"""
        base_dir = Path(self.config.process_manager.logs_dir)
        if app_name:
            return base_dir / app_name
        return base_dir


# Global config manager instance
config_manager = ConfigManager()
