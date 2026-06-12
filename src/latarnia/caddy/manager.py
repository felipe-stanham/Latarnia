"""
Caddy configuration manager for Latarnia (P-0008).

Replaces the old Python reverse proxy (`web_proxy.py`). Latarnia no longer
proxies app traffic itself — Caddy is the single ingress. This manager
generates a per-environment Caddyfile *include* from the current App
Registry and asks Caddy to reload it.

Generated file (operator-owned tree):
    /opt/latarnia/{env}/caddy/latarnia.caddyfile

The system Caddyfile (`/etc/caddy/Caddyfile`, operator-managed) `import`s the
per-env include for each environment running on the host. On reload Latarnia
runs `caddy reload --config <main Caddyfile>` so every imported section is
re-read together — this is multi-env safe (reloading TST never clobbers
PRD). If the `caddy` binary is unavailable, it falls back to POSTing the
generated include to the admin API.

Caddyfile structure (see P-0008/architecture.md):
  - `/auth/*`, `/docs*`, `/openapi.json`   → Latarnia, no auth (public on LAN)
  - `/apps/{name}/docs*`, `/openapi.json`  → app swagger, no auth (public)
  - `/apps/{name}/*`                        → app webUI, forward_auth + proxy
  - `/*`                                    → dashboard / API / MCP, forward_auth
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import List, Optional

import httpx

from ..core.config import ConfigManager

# Headers Caddy copies from /auth/verify onto the proxied request.
_COPY_HEADERS = "X-Latarnia-User X-Latarnia-App-Role X-Latarnia-Is-Super"


class CaddyConfigManager:
    """Generate the per-env Caddyfile include and reload Caddy."""

    def __init__(
        self,
        config_manager: ConfigManager,
        app_manager,
        admin_url: str = "http://localhost:2019",
        main_caddyfile: str = "/etc/caddy/Caddyfile",
    ) -> None:
        self.config_manager = config_manager
        self.app_manager = app_manager
        self.admin_url = admin_url.rstrip("/")
        self.main_caddyfile = main_caddyfile
        self.env = config_manager.get_env()
        self.logger = logging.getLogger("latarnia.caddy")
        # Serialize config writes + reloads: app start/stop and registration
        # can fire concurrently and must not interleave Caddyfile writes.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _caddyfile_path(self) -> Path:
        """Where the generated per-env include lives.

        Anchored at the env root (parent of the data dir), mirroring
        SecretManager. On the Pi this resolves to
        `/opt/latarnia/{env}/caddy/latarnia.caddyfile`; locally it sits
        under the repo's `caddy/` directory.
        """
        env_root = Path(self.config_manager.get_data_dir()).parent
        return env_root / "caddy" / "latarnia.caddyfile"

    def _site_address(self) -> str:
        """Caddy site address for this environment.

        PRD listens on `{domain}:443`, TST on `{domain}:8443`. For `dev`
        (or a `localhost` domain) the bare `localhost` address is used so
        Caddy serves its automatic self-signed cert.
        """
        domain = self.config_manager.get_domain()
        env = self.env
        if domain == "localhost":
            return "localhost"
        if env == "tst":
            return f"{domain}:8443"
        if env == "prd":
            return f"{domain}:443"
        return "localhost"

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def _forward_auth_lines(self, local: str) -> List[str]:
        """Session-cookie `forward_auth` guard block (8-space indent).

        On a 2xx from `/auth/verify` the request proceeds to the route's
        `reverse_proxy` with the role headers copied in. On a **401**
        (no/expired session) Caddy redirects the browser to `/auth/login`,
        preserving the original path as `next` so login returns the user
        there (spec.md:79-80; workflows.md flow-04). Only 401 is matched —
        an app-level 403 (logged in, wrong role) must not redirect-loop to
        login.
        """
        return [
            f"        forward_auth {local} {{",
            "            uri /auth/verify",
            f"            copy_headers {_COPY_HEADERS}",
            "            @auth_denied status 401",
            "            handle_response @auth_denied {",
            "                redir * /auth/login?next={http.request.uri.path} 302",
            "            }",
            "        }",
        ]

    def _web_ui_apps(self) -> List[tuple]:
        """Return (app_name, port, public_routes) for running service apps with a web UI.

        Only Service Apps are proxied under `/apps/{name}/` — Streamlit Apps
        have their own on-demand launch/modal path and are never routed here.
        A route can only be emitted once an app has an assigned port (i.e. it
        is running); apps that declare `has_web_ui` but aren't up yet are
        skipped and picked up on their next start/registration.
        """
        result = []
        for app in self.app_manager.registry.get_all_apps():
            # AppType subclasses str, so == "service" holds for the enum too.
            if getattr(app, "type", None) != "service":
                continue
            if not getattr(app.manifest.config, "has_web_ui", False):
                continue
            port = getattr(app.runtime_info, "assigned_port", None)
            if not port:
                continue
            public_routes = list(getattr(app.manifest.config, "public_routes", None) or [])
            result.append((app.name, port, public_routes))
        result.sort()
        return result

    def generate_config(self) -> str:
        """Build the Caddyfile include text and write it to disk.

        Returns the generated text. Always reflects current registry state:
        per-app public/protected blocks are regenerated on every call.
        """
        main_port = self.config_manager.config.system.main_port
        site = self._site_address()
        local = f"localhost:{main_port}"

        lines: List[str] = []
        lines.append(f"# Generated by Latarnia ({self.env}). Do not edit by hand.")
        lines.append(f"{site} {{")

        # Public: Latarnia auth endpoints
        lines.append("    handle /auth/* {")
        lines.append(f"        reverse_proxy {local}")
        lines.append("    }")
        # Public: Latarnia swagger
        lines.append("    handle /docs* {")
        lines.append(f"        reverse_proxy {local}")
        lines.append("    }")
        lines.append("    handle /openapi.json {")
        lines.append(f"        reverse_proxy {local}")
        lines.append("    }")

        # Machine-facing surfaces: proxied straight to Latarnia WITHOUT
        # forward_auth. Caddy's forward_auth only validates the session cookie;
        # these paths must accept a Bearer JWT too, so auth is enforced inside
        # Latarnia instead — JWTAuthMiddleware for /api/*, the gateway for /mcp.
        # (forward_auth here would 401 every machine token before it arrives.)
        lines.append("    handle /api/* {")
        lines.append(f"        reverse_proxy {local}")
        lines.append("    }")
        if self.config_manager.config.mcp.enabled:
            mcp_path = self.config_manager.config.mcp.gateway_path.rstrip("/")
            lines.append(f"    handle {mcp_path}/* {{")
            lines.append(f"        reverse_proxy {local}")
            lines.append("    }")

        # Per-app blocks. `handle_path` strips the matched prefix so the app
        # receives root-relative paths (e.g. /apps/my_app/page -> /page),
        # matching the behaviour of the deleted web_proxy.
        web_ui_apps = self._web_ui_apps()
        for name, port, pub_routes in web_ui_apps:
            app_local = f"localhost:{port}"
            # Public: app swagger (no forward_auth). Identity headers stripped
            # so apps can't be spoofed via the public path (T-0004 hardening).
            lines.append(f"    handle_path /apps/{name}/docs* {{")
            lines.append(f"        reverse_proxy {app_local} {{")
            lines.append("            header_up -X-Latarnia-User")
            lines.append("            header_up -X-Latarnia-App-Role")
            lines.append("            header_up -X-Latarnia-Is-Super")
            lines.append("        }")
            lines.append("    }")
            lines.append(f"    handle_path /apps/{name}/openapi.json {{")
            lines.append(f"        reverse_proxy {app_local} {{")
            lines.append("            header_up -X-Latarnia-User")
            lines.append("            header_up -X-Latarnia-App-Role")
            lines.append("            header_up -X-Latarnia-Is-Super")
            lines.append("        }")
            lines.append("    }")
            # Public declared routes (no forward_auth). Caddy's handle (not
            # handle_path) preserves the path matcher, then uri strip_prefix
            # removes /apps/{name} so the app receives its own root-relative
            # path (e.g. /apps/x/b/foo → /b/foo). Identity headers stripped.
            for prefix in pub_routes:
                lines.append(f"    handle /apps/{name}{prefix}* {{")
                lines.append(f"        uri strip_prefix /apps/{name}")
                lines.append(f"        reverse_proxy {app_local} {{")
                lines.append("            header_up -X-Latarnia-User")
                lines.append("            header_up -X-Latarnia-App-Role")
                lines.append("            header_up -X-Latarnia-Is-Super")
                lines.append("        }")
                lines.append("    }")
            # Protected: app webUI
            lines.append(f"    handle_path /apps/{name}/* {{")
            lines.extend(self._forward_auth_lines(local))
            lines.append(f"        reverse_proxy {app_local}")
            lines.append("    }")

        # Catch-all: dashboard and any other browser route — behind
        # forward_auth (session). /api and /mcp were carved out above.
        lines.append("    handle /* {")
        lines.extend(self._forward_auth_lines(local))
        lines.append(f"        reverse_proxy {local}")
        lines.append("    }")

        lines.append("}")
        config = "\n".join(lines) + "\n"

        path = self._caddyfile_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config)
        self.logger.info(
            "Generated Caddyfile for %s (%d web-UI app route block(s)) at %s",
            self.env, len(web_ui_apps), path,
        )
        return config

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def reload(self) -> bool:
        """Ask Caddy to apply the current config.

        Prefers `caddy reload --config <main Caddyfile>` which re-reads the
        system Caddyfile and all its imports — multi-env safe. Falls back to
        POSTing the per-env include to the admin API if the `caddy` binary
        isn't on PATH. Returns True on success.
        """
        caddy_bin = shutil.which("caddy")
        if caddy_bin and Path(self.main_caddyfile).exists():
            try:
                proc = subprocess.run(
                    [caddy_bin, "reload", "--config", self.main_caddyfile],
                    capture_output=True, text=True, timeout=30,
                )
                if proc.returncode == 0:
                    self.logger.info("Caddy reloaded via CLI (%s)", self.main_caddyfile)
                    return True
                self.logger.error(
                    "caddy reload failed (rc=%s): %s",
                    proc.returncode, proc.stderr.strip(),
                )
                return False
            except (subprocess.SubprocessError, OSError) as exc:
                self.logger.error("caddy reload errored: %s", exc)
                return False

        # Fallback: POST the include to the admin API. NOTE: on a multi-env
        # host this replaces the whole running config with just this env's
        # section — acceptable only for single-env hosts (e.g. local dev).
        self.logger.warning(
            "caddy binary or main Caddyfile not found; falling back to admin "
            "API /load (single-env only)"
        )
        try:
            config = self._caddyfile_path().read_text()
        except OSError as exc:
            self.logger.error("Cannot read generated Caddyfile: %s", exc)
            return False
        try:
            resp = httpx.post(
                f"{self.admin_url}/load",
                content=config,
                headers={"Content-Type": "text/caddyfile"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                self.logger.info("Caddy reloaded via admin API")
                return True
            self.logger.error(
                "Caddy admin API /load returned %s: %s",
                resp.status_code, resp.text[:200],
            )
            return False
        except httpx.HTTPError as exc:
            self.logger.error("Caddy admin API /load failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def _regenerate_and_reload(self) -> bool:
        """Hold the write lock across generate + reload (flow-05).

        Exception-safe: these hooks run inside request handlers, so a
        filesystem or Caddy error is logged and swallowed rather than
        failing the lifecycle operation that triggered it.
        """
        with self._lock:
            try:
                self.generate_config()
                return self.reload()
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.error("Caddy regenerate/reload failed: %s", exc)
                return False

    def on_app_registered(self, app_id: Optional[str] = None,
                          port: Optional[int] = None) -> bool:
        """Regenerate + reload after an app registers or starts.

        Driven entirely by current registry state, so the (optional)
        app_id/port args are advisory — kept for call-site clarity and
        flow-05 fidelity.
        """
        self.logger.info("Caddy: app registered/started (%s) — regenerating", app_id)
        return self._regenerate_and_reload()

    def on_app_deregistered(self, app_id: Optional[str] = None) -> bool:
        """Regenerate + reload after an app deregisters or stops."""
        self.logger.info("Caddy: app deregistered/stopped (%s) — regenerating", app_id)
        return self._regenerate_and_reload()
