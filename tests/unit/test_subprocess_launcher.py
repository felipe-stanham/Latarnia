"""
Unit tests for SubprocessLauncher (P-0005 Scope 2).

SubprocessLauncher is the macOS fallback launcher for service apps. It is the
renamed and verb-harmonized version of the former MacOSProcessManager.
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from latarnia.managers.app_manager import (
    AppManager,
    AppManifest,
    AppRegistry,
    AppRegistryEntry,
    AppRuntimeInfo,
    AppStatus,
    AppType,
)
from latarnia.managers.subprocess_launcher import SubprocessLauncher


@pytest.fixture
def temp_dirs():
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        (base / "logs").mkdir()
        yield base


@pytest.fixture
def mock_config_manager(temp_dirs):
    cfg = Mock()
    cfg.get_redis_url.return_value = "redis://localhost:6379/0"
    cfg.get_logs_dir.return_value = temp_dirs / "logs"
    cfg.get_data_dir = Mock(side_effect=lambda app_id=None: temp_dirs / (app_id or "data"))
    return cfg


@pytest.fixture
def mock_app_manager():
    am = Mock(spec=AppManager)
    am.registry = Mock(spec=AppRegistry)
    return am


@pytest.fixture
def mock_port_manager():
    pm = Mock()
    pm.allocate_port.return_value = 8100
    pm.allocate_mcp_port.return_value = None
    pm.release_port = Mock()
    pm.release_mcp_port = Mock()
    return pm


@pytest.fixture
def launcher(mock_config_manager, mock_app_manager, mock_port_manager):
    return SubprocessLauncher(mock_config_manager, mock_app_manager, mock_port_manager)


@pytest.fixture
def sample_service_app(temp_dirs):
    app_path = temp_dirs / "my-service"
    app_path.mkdir()
    main_file = app_path / "app.py"
    main_file.write_text("# placeholder")

    manifest = AppManifest(
        name="my-service",
        type=AppType.SERVICE,
        description="Sample",
        version="1.0.0",
        author="Test",
        main_file="app.py",
        config={
            "has_UI": False,
            "redis_required": False,
            "data_dir": False,
            "logs_dir": False,
            "auto_start": False,
            "restart_policy": "on-failure",
        },
    )

    return AppRegistryEntry(
        app_id="my-service",
        name="my-service",
        type=AppType.SERVICE,
        description="Sample",
        version="1.0.0",
        status=AppStatus.READY,
        path=app_path,
        manifest=manifest,
        runtime_info=AppRuntimeInfo(),
    )


class TestSubprocessLauncher:
    def test_launcher_logger_name(self, launcher):
        assert launcher.logger.name == "latarnia.subprocess_launcher"

    @patch("latarnia.managers.subprocess_launcher.subprocess.Popen")
    def test_start_service_success(
        self, mock_popen, launcher, mock_app_manager, sample_service_app,
    ):
        """start_service spawns Popen and tracks PID + port."""
        mock_app_manager.registry.get_app.return_value = sample_service_app
        mock_proc = MagicMock()
        mock_proc.pid = 4242
        mock_popen.return_value = mock_proc

        ok = launcher.start_service("my-service")

        assert ok is True
        assert "my-service" in launcher.processes
        assert launcher.processes["my-service"]["pid"] == 4242
        assert launcher.processes["my-service"]["port"] == 8100

        # Popen called with venv python + main_file + --port 8100
        call_args, _call_kwargs = mock_popen.call_args
        cmd = call_args[0]
        assert cmd[0] == sys.executable
        assert cmd[1].endswith("app.py")
        assert "--port" in cmd
        assert "8100" in cmd

    @patch("latarnia.managers.subprocess_launcher.subprocess.Popen")
    def test_start_service_rejects_non_service_type(
        self, mock_popen, launcher, mock_app_manager, sample_service_app,
    ):
        """Streamlit apps are not handled by this launcher."""
        sample_service_app.type = AppType.STREAMLIT
        mock_app_manager.registry.get_app.return_value = sample_service_app

        ok = launcher.start_service("my-service")

        assert ok is False
        mock_popen.assert_not_called()

    @patch("latarnia.managers.subprocess_launcher.psutil.pid_exists", return_value=True)
    @patch("latarnia.managers.subprocess_launcher.psutil.Process")
    def test_stop_service_success(
        self, mock_process_cls, mock_pid_exists,
        launcher, mock_app_manager, sample_service_app,
    ):
        """stop_service terminates the tracked PID and releases ports."""
        launcher.processes["my-service"] = {
            "pid": 4242,
            "port": 8100,
            "mcp_port": None,
            "started_at": datetime.now(),
            "command": "python3 app.py --port 8100",
        }
        mock_proc = MagicMock()
        mock_process_cls.return_value = mock_proc
        mock_app_manager.registry.get_app.return_value = sample_service_app

        ok = launcher.stop_service("my-service")

        assert ok is True
        mock_proc.terminate.assert_called_once()
        assert "my-service" not in launcher.processes
        launcher.port_manager.release_port.assert_called_with("my-service")

    def test_restart_service_calls_stop_then_start(
        self, launcher, mock_app_manager, sample_service_app,
    ):
        """restart_service is stop+start. Verifies the wrapper, not the inner work."""
        mock_app_manager.registry.get_app.return_value = sample_service_app
        with patch.object(launcher, "stop_service", return_value=True) as mock_stop, \
             patch.object(launcher, "start_service", return_value=True) as mock_start:
            ok = launcher.restart_service("my-service")
            assert ok is True
            mock_stop.assert_called_once_with("my-service")
            mock_start.assert_called_once_with("my-service")

    @patch("latarnia.managers.subprocess_launcher.subprocess.Popen")
    def test_start_service_merges_secrets_into_popen_env(
        self, mock_popen, launcher, mock_app_manager, sample_service_app,
        temp_dirs, mock_config_manager,
    ):
        """cap-004: declared secrets land in Popen env=, undeclared do not."""
        from latarnia.managers.secret_manager import SecretManager
        import os as _os

        mock_config_manager.get_data_dir = Mock(side_effect=lambda app_id=None: temp_dirs / "data" / (app_id or "")) if False else mock_config_manager.get_data_dir
        # Override to a real dir so SecretManager finds the env root.
        env_root = temp_dirs
        mock_config_manager.get_data_dir = Mock(return_value=env_root / "data")
        (env_root / "data").mkdir(exist_ok=True)
        master = env_root / "secrets.env"
        master.write_text("A=1\nB=2\nC=3\n")
        _os.chmod(master, 0o600)

        sm = SecretManager(mock_config_manager, mock_app_manager, env="dev")
        launcher.secret_manager = sm

        sample_service_app.manifest.config.requires_secrets = ["A", "B"]
        mock_app_manager.registry.get_app.return_value = sample_service_app

        mock_proc = MagicMock()
        mock_proc.pid = 4242
        mock_popen.return_value = mock_proc

        ok = launcher.start_service("my-service")

        assert ok is True
        # Popen received env= containing A and B but not C.
        _, kwargs = mock_popen.call_args
        env_passed = kwargs["env"]
        assert env_passed["A"] == "1"
        assert env_passed["B"] == "2"
        assert "C" not in env_passed

    @patch("latarnia.managers.subprocess_launcher.subprocess.Popen")
    def test_start_service_refuses_when_secret_missing(
        self, mock_popen, launcher, mock_app_manager, sample_service_app,
        temp_dirs, mock_config_manager,
    ):
        """cap-005 on Darwin: missing secret → no Popen, no port allocation."""
        from latarnia.managers.secret_manager import SecretManager
        import os as _os

        env_root = temp_dirs
        mock_config_manager.get_data_dir = Mock(return_value=env_root / "data")
        (env_root / "data").mkdir(exist_ok=True)
        master = env_root / "secrets.env"
        master.write_text("A=1\n")
        _os.chmod(master, 0o600)

        sm = SecretManager(mock_config_manager, mock_app_manager, env="dev")
        launcher.secret_manager = sm

        sample_service_app.manifest.config.requires_secrets = ["A", "B"]
        mock_app_manager.registry.get_app.return_value = sample_service_app

        ok = launcher.start_service("my-service")

        assert ok is False
        mock_popen.assert_not_called()
        # No port allocation attempted.
        launcher.port_manager.allocate_port.assert_not_called()
        # error_message names the missing key (B), never a value.
        assert "missing required secret" in (sample_service_app.runtime_info.error_message or "")
        assert "B" in (sample_service_app.runtime_info.error_message or "")

    def test_get_process_info_includes_uptime(self, launcher):
        launcher.processes["my-service"] = {
            "pid": 4242,
            "port": 8100,
            "mcp_port": None,
            "started_at": datetime.now(),
            "command": "python3 app.py",
        }
        info = launcher.get_process_info("my-service")
        assert info is not None
        assert info["pid"] == 4242
        assert "uptime" in info
        assert "uptime_seconds" in info
        # started_at serialized as ISO string
        assert isinstance(info["started_at"], str)
