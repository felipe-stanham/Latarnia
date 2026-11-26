"""
Unit tests for the Manifest `name` pattern (T-0024)

A Manifest `name` is an identifier matching APP_NAME_PATTERN: the platform interpolates
it into the Caddyfile, Postgres names, log lines and docs pages.
"""

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from latarnia.core.config import ConfigManager
from latarnia.managers.app_manager import (
    APP_NAME_PATTERN, AppManager, AppManifest,
)
from latarnia.managers.port_manager import PortManager

REPO_ROOT = Path(__file__).parent.parent.parent


def _manifest_data(name):
    return {
        "name": name,
        "type": "service",
        "description": "Test service application",
        "version": "1.0.0",
        "author": "Test Author",
        "main_file": "app.py",
    }


class TestAppNamePattern:
    """AppManifest.name accepts only APP_NAME_PATTERN identifiers"""

    def test_pattern_constant(self):
        assert APP_NAME_PATTERN == r"^[a-z][a-z0-9_-]{0,49}$"

    @pytest.mark.parametrize("name", [
        "latarnik",
        "example_full_app",
        "example_companion",
        "crm-app",
        "x",
        "a1",
        "a" * 50,
    ])
    def test_valid_names(self, name):
        """t24-001: identifier names are accepted"""
        assert AppManifest(**_manifest_data(name)).name == name

    @pytest.mark.parametrize("name", [
        "Camera Detection",
        "App_A",
        "latarnik ",
        "a{http.request.uri}",
        "a}b",
        "a/b",
        "../x",
        "a.b",
        "1app",
        "-app",
        "_app",
        "app\n",
        "app\r",
        "ápp",
        "",
        "a" * 51,
    ])
    def test_invalid_names(self, name):
        """t24-002: anything else is rejected with an error on field `name`"""
        with pytest.raises(ValidationError) as exc_info:
            AppManifest(**_manifest_data(name))
        assert [e["loc"] for e in exc_info.value.errors()] == [("name",)]


class TestAppNameDiscovery:
    """AppManager.discover_apps() skips Apps whose Manifest name is invalid"""

    @pytest.fixture
    def temp_dirs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_dir = temp_path / "config"
            apps_dir = temp_path / "apps"
            config_dir.mkdir()
            apps_dir.mkdir()
            yield {'base': temp_path, 'config': config_dir, 'apps': apps_dir}

    @pytest.fixture
    def app_manager(self, temp_dirs):
        mock_config = Mock()
        mock_config.process_manager.port_range.start = 8100
        mock_config.process_manager.port_range.end = 8105
        mock_config_manager = Mock(spec=ConfigManager)
        mock_config_manager.config = mock_config
        mock_config_manager.get_data_dir.return_value = temp_dirs['config']
        mock_port_manager = Mock(spec=PortManager)
        mock_port_manager.allocate_port.return_value = 8100
        with patch('pathlib.Path.cwd', return_value=temp_dirs['base']):
            return AppManager(mock_config_manager, mock_port_manager)

    @staticmethod
    def _write_app(apps_dir, dir_name, manifest_name):
        app_dir = apps_dir / dir_name
        app_dir.mkdir()
        (app_dir / "latarnia.json").write_text(json.dumps(_manifest_data(manifest_name)))
        (app_dir / "app.py").write_text("# Test main file")
        return app_dir / "latarnia.json"

    def test_discover_skips_app_with_invalid_name(self, app_manager, temp_dirs, caplog):
        """t24-003: valid Apps register, the bad-name App does not, ERROR names the manifest"""
        self._write_app(temp_dirs['apps'], "good-one", "good-one")
        self._write_app(temp_dirs['apps'], "good-two", "good_two")
        bad_manifest = self._write_app(temp_dirs['apps'], "bad-app", "bad{x}")

        with caplog.at_level(logging.DEBUG):
            count = app_manager.discover_apps()

        assert count == 2
        assert sorted(a.name for a in app_manager.registry.get_all_apps()) == ["good-one", "good_two"]
        assert app_manager.registry.get_app_by_name("bad{x}") is None

        errors = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.ERROR
            and "Invalid manifest" in r.getMessage()
            and str(bad_manifest) in r.getMessage()
        ]
        assert len(errors) == 1
        # The message names the field and the pattern so the operator can fix it
        assert "name" in errors[0]
        assert "String should match pattern" in errors[0]
        assert APP_NAME_PATTERN in errors[0]


class TestShippedManifests:
    """Every Manifest the repo ships parses with the name pattern"""

    def test_apps_and_examples_manifests_parse(self):
        """t24-004: latarnia.json under apps/ and examples/ -> parses with AppManifest"""
        manifests = sorted(
            list((REPO_ROOT / "apps").glob("*/latarnia.json"))
            + list((REPO_ROOT / "examples").glob("*/latarnia.json"))
        )
        # examples/ is committed, so at least its two Apps are always found;
        # apps/ is gitignored and may be absent in a fresh worktree.
        assert len(manifests) >= 2
        for manifest_file in manifests:
            AppManifest(**json.loads(manifest_file.read_text()))
