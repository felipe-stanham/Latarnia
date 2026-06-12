# Regression Tests

Critical-path tests for Latarnia. Each test is declarative — Claude Code generates verification scripts on the fly.

## Core Infrastructure

- **test_config_load_from_json:** Create a temporary JSON config file with `{"redis": {"host": "testhost", "port": 6380}, "system": {"main_port": 9000}}`. Instantiate `ConfigManager` with that path and call `load_config()`. -> Config object has `redis.host == "testhost"`, `redis.port == 6380`, `system.main_port == 9000`.

- **test_config_defaults_on_missing_file:** Instantiate `ConfigManager` with a non-existent path `/tmp/nonexistent_hh_config.json` and call `load_config()`. -> Config uses defaults: `redis.host == "localhost"`, `redis.port == 6379`, `system.main_port == 8000`, `health_check_interval_seconds == 60`.

- **test_config_port_range_values:** Create `LatarniaConfig()` with defaults. -> `process_manager.port_range.start == 8100` and `process_manager.port_range.end == 8199`.

- **test_config_redis_url_generation:** Instantiate `ConfigManager()`, call `load_config()`, then `get_redis_url()`. -> Returns `"redis://localhost:6379/0"`.

- **test_redis_client_connect_success:** Create `RedisMessageBusClient("test", "redis://localhost:6379/0")`. Mock `redis.from_url` to return a mock that responds to `ping()` with `True`. Call `client.connect()`. -> Returns `True` and `client._connected is True`.

- **test_redis_client_connect_failure:** Create `RedisMessageBusClient("test", "redis://localhost:6379/0")`. Mock `redis.from_url` to raise `redis.ConnectionError`. Call `client.connect()`. -> Returns `False` and `client._connected is False`.

- **test_redis_health_monitor_connected:** Create `RedisHealthMonitor("redis://localhost:6379/0")`. Mock `redis.from_url` to return a mock with `ping() -> True` and `info()` returning `{"used_memory": 52428800, "used_memory_peak": 104857600, "used_memory_rss": 62914560, "total_commands_processed": 5000, "connected_clients": 10, "uptime_in_seconds": 7200, "keyspace_hits": 1000, "keyspace_misses": 100}`, and `pubsub_channels()` returning two channels. Call `get_redis_metrics()`. -> Returns dict with `status == "connected"`, `memory.used_mb == 50`, `stats.connected_clients == 10`.

- **test_redis_health_monitor_disconnected:** Create `RedisHealthMonitor("redis://localhost:6379/0")`. Mock `redis.from_url` to raise `redis.ConnectionError("Connection failed")`. Call `get_redis_metrics()`. -> Returns dict with `status == "error"` and `error == "Connection failed"`.

- **test_system_monitor_cpu_metrics:** Create `SystemMonitor()`. Mock `psutil.cpu_percent` to return `25.5`, `os.getloadavg` to return `(0.8, 0.6, 0.9)`, `psutil.cpu_count` to return `4`. Call `_get_cpu_metrics()`. -> Returns dict with `usage_percent == 25.5`, `core_count == 4`, `load_avg_1m == 0.8`.

- **test_system_monitor_memory_metrics:** Create `SystemMonitor()`. Mock `psutil.virtual_memory` to return `total=8GB, used=6GB, available=2GB, percent=75.0, free=1GB`. Call `_get_memory_metrics()`. -> Returns dict with `total_mb == 8192`, `used_mb == 6144`, `percent == 75.0`.

- **test_system_monitor_status_good:** Create `SystemMonitor()`. Call `_determine_system_status` with hardware metrics: `cpu.usage_percent=50, memory.percent=60, disk.percent=70, temperature.cpu_celsius=45` and empty processes list. -> Returns `"good"`.

- **test_system_monitor_status_warning:** Create `SystemMonitor()`. Call `_determine_system_status` with hardware metrics: `cpu.usage_percent=85, memory.percent=60, disk.percent=70, temperature.cpu_celsius=45` and empty processes list. -> Returns `"warning"`.

## App Management

- **test_app_discovery_valid_manifest:** Create a temp directory structure with `apps/test-service/latarnia.json` containing `{"name": "test-service", "type": "service", "description": "Test", "version": "1.0.0", "author": "Test", "main_file": "app.py"}` and an `apps/test-service/app.py` file. Create `AppManager` with mocked config pointing to that temp dir. Call `discover_apps()`. -> Returns `1`. `registry.get_all_apps()` returns one entry with `name == "test-service"` and `type == AppType.SERVICE`.

- **test_app_discovery_invalid_manifest:** Create a temp directory with `apps/bad-app/latarnia.json` containing `{"name": "bad-app"}` (missing required fields). Call `discover_apps()`. -> Returns `0`. Registry is empty.

- **test_app_discovery_missing_main_file:** Create a temp directory with valid manifest referencing `nonexistent.py` as `main_file`, but do not create that file. Call `discover_apps()`. -> Returns `0`. Registry is empty.

- **test_app_registry_register_and_get:** Create an `AppRegistry` with mocked config. Register an `AppRegistryEntry` with `app_id="app-1"`. Call `get_app("app-1")`. -> Returns the same entry. Call `get_app("nonexistent")`. -> Returns `None`.

- **test_app_registry_get_all_apps:** Register two apps (one service, one streamlit) into `AppRegistry`. Call `get_all_apps()`. -> Returns list of length 2.

- **test_app_registry_filter_by_type:** Register one service app and one streamlit app. Call `get_apps_by_type(AppType.SERVICE)`. -> Returns list of length 1 with the service app. Call `get_apps_by_type(AppType.STREAMLIT)`. -> Returns list of length 1 with the streamlit app.

- **test_app_registry_unregister:** Register an app with `app_id="app-1"`. Call `unregister_app("app-1")`. -> Returns `True`. `get_app("app-1")` returns `None`.

- **test_manifest_version_validation:** Attempt to create `AppManifest` with `version="1.0"` (invalid semver). -> Raises validation error (pydantic `ValidationError`).

## Service Management

- **test_service_template_generation:** Create `ServiceManager` with mocked dependencies. Mock `registry.get_app` to return a service app entry at path `/tmp/test-service` with `assigned_port=8100`, `main_file="app.py"`, `restart_policy="always"`, `redis_required=True`, `data_dir=True`. Call `generate_service_template("test-service")`. -> Returns a string containing `"Description=Latarnia Service - test-service"`, `"ExecStart={sys.executable} app.py --port 8100"` (absolute venv Python — no bare `python`), `"Restart=always"`, `"Environment=ENV={env}"`, `"Environment=REDIS_HOST=localhost"`. Must NOT contain `"PartOf="` (P-0005 Scope 4: lifecycle decoupling) and must NOT contain `"--logs-dir"` (P-0005 Scope 4: journald is the canonical sink).

- **test_service_template_restart_policy_defaults_to_on_failure:** Create `ServiceManager` and a service app entry where `manifest.config.restart_policy` is cleared (simulating a manifest that doesn't set it). Call `generate_service_template`. -> Returns a template containing `"Restart=on-failure"` and `"RestartSec=5"`.

- **test_service_template_never_maps_to_no:** Create a service app entry with `restart_policy="never"`. Call `generate_service_template`. -> Template contains `"Restart=no"` (systemd's spelling — `"never"` is invalid).

- **test_linger_warning_on_startup:** Patch `platform.system()` → `"Linux"` and `ServiceManager.linger_enabled` → `False`. Run the relevant section of the `lifespan` startup. -> A `WARNING` log is emitted that mentions the user and includes the remediation command `loginctl enable-linger {user}`.

- **test_service_template_no_app:** Create `ServiceManager`. Mock `registry.get_app` to return `None`. Call `generate_service_template("nonexistent")`. -> Returns `None`.

- **test_service_start_success:** Create `ServiceManager`. Mock `subprocess.run` to return `returncode=0`. Mock `registry.get_app` to return a valid service entry. Call `start_service("test-service")`. -> Returns `True`. Verify `subprocess.run` was called with `["systemctl", "--user", "start", "latarnia-test-service.service"]`. Verify `registry.update_app` was called with `status=AppStatus.RUNNING`.

- **test_service_start_failure:** Create `ServiceManager`. Mock `subprocess.run` to return `returncode=1, stderr="Service failed"`. Call `start_service("test-service")`. -> Returns `False`. Verify `registry.update_app` was called with `status=AppStatus.ERROR`.

- **test_service_stop_success:** Create `ServiceManager`. Mock `subprocess.run` to return `returncode=0`. Call `stop_service("test-service")`. -> Returns `True`. Verify `subprocess.run` was called with `["systemctl", "--user", "stop", "latarnia-test-service.service"]`.

- **test_service_restart_success:** Create `ServiceManager`. Mock `subprocess.run` to return `returncode=0`. Call `restart_service("test-service")`. -> Returns `True`. Verify `subprocess.run` was called with `["systemctl", "--user", "restart", "latarnia-test-service.service"]`.

## Combined Health (P-0005 cap-005)

- **test_combined_health_stopped_shows_red:** Create `HealthMonitor` with mocked `ServiceManager` (`env="tst"`). Patch `HealthMonitor.get_systemd_states` to return `{"example_full_app": "inactive"}` and set `health_results["example_full_app"]` to a `HealthCheckResult(status=HealthStatus.GOOD)` (stale). Call `get_overall_status("example_full_app")`. -> Returns `{overall_status: "grey", detail: "stopped"}`. Repeat with the systemd state set to `"failed"`. -> Returns `{overall_status: "red", ...}`.

- **test_combined_health_active_but_health_error:** Set `get_systemd_states` → `{"app_a": "active"}` and `health_results["app_a"]` to `HealthCheckResult(status=HealthStatus.ERROR, message="redis down")`. Call `get_overall_status("app_a")`. -> Returns `overall_status == "red"` with the message propagated into `detail`.

- **test_combined_health_active_good_to_green:** Set `get_systemd_states` → `{"app_a": "active"}` and `health_results["app_a"]` to `HealthCheckResult(status=HealthStatus.GOOD)`. Call `get_overall_status("app_a")`. -> Returns `overall_status == "green"`.

- **test_api_apps_returns_overall_status:** Call `GET /api/apps` against a TestClient instance. Mock `health_monitor.get_overall_status("app_x")` to return `{"overall_status": "yellow", "detail": "starting"}`. -> The `apps[0]` payload contains `overall_status == "yellow"` and `overall_status_detail == "starting"`.

## Web Dashboard

- **test_health_endpoint:** Use `httpx.AsyncClient` with FastAPI `TestClient` or ASGI transport against the `app` from `latarnia.main`. Mock `system_monitor.get_hardware_metrics()` to return `{"cpu": {"usage_percent": 30}, "memory": {"percent": 50}, "disk": {"percent": 40}}`. Mock `redis_monitor.get_redis_metrics()` to return `{"status": "connected"}`. Send GET to `/health`. -> Response status 200. JSON body has `health == "good"`, `message == "System operational"`, and `extra_info.config_loaded == True`.

- **test_health_endpoint_redis_down:** Mock `redis_monitor.get_redis_metrics()` to return `{"status": "error"}`. Mock `system_monitor.get_hardware_metrics()` to return valid metrics. Send GET to `/health`. -> Response status 200. JSON body has `health == "error"` and `message` contains `"Redis connection failed"`.

- **test_root_endpoint:** Send GET to `/` (no follow-redirects). -> Response status 302 with `Location: /dashboard`.

- **test_get_all_apps_endpoint:** Mock `app_manager.registry.get_all_apps()` to return a list with one app entry (mocked `to_dict()` returning `{"app_id": "test-1", "name": "test"}`). Send GET to `/api/apps`. -> Response status 200. JSON body has `total_count == 1` and `apps` is a list of length 1.

- **test_get_app_not_found:** Mock `app_manager.registry.get_app("nonexistent")` to return `None`. Send GET to `/api/apps/nonexistent`. -> Response status 404.

- **test_system_metrics_endpoint:** Mock `system_monitor.get_system_summary()` to return `{"status": "good", "cpu": 30}`. Send GET to `/api/system/metrics`. -> Response status 200. JSON body has `status == "good"`.

## UI Integration

- **test_streamlit_launch_non_streamlit_app:** Mock `app_manager.registry.get_app("svc-1")` to return an entry with `type == "service"`. Send POST to `/api/apps/svc-1/streamlit/launch`. -> Response status 400 with detail `"App is not a Streamlit app"`.

- **test_streamlit_launch_app_not_found:** Mock `app_manager.registry.get_app("nonexistent")` to return `None`. Send POST to `/api/apps/nonexistent/streamlit/launch`. -> Response status 404.

- **test_streamlit_touch_extends_ttl:** In `StreamlitManager`, add a process entry for `app_id="st-1"` with `last_accessed` set to 5 minutes ago. Call `touch_app("st-1")`. -> The `processes["st-1"]["last_accessed"]` is updated to approximately `datetime.now()` (within 2 seconds).

## Port Management

- **test_port_allocation_within_range:** Create `PortManager` with config `port_range.start=8100, port_range.end=8105`. Mock `socket.socket.bind` to succeed. Call `allocate_port("app-1", "service")`. -> Returns a port between 8100 and 8105 inclusive. `app_ports["app-1"]` equals the returned port.

- **test_port_allocation_exhausted:** Create `PortManager` with config `port_range.start=8100, port_range.end=8105`. Mock `socket.socket.bind` to raise `OSError` for all ports. Call `allocate_port("app-1", "service")`. -> Returns `None`.

- **test_port_release:** Allocate a port for `"app-1"`. Call `release_port("app-1")`. -> Returns `True`. `get_app_port("app-1")` returns `None`. The port is no longer in `allocations`.

- **test_port_reuse_for_same_app:** Allocate a port for `"app-1"`. Call `allocate_port("app-1", "service")` again. -> Returns the same port as the first allocation.

- **test_port_statistics:** Create `PortManager` with range 8100-8105 (6 ports). Allocate ports for `"app1"` (service) and `"app2"` (streamlit). Call `get_port_statistics()`. -> Returns `total_ports == 6`, `allocated_ports == 2`, `utilization_percent == 33.3`, `app_type_breakdown.service == 1`, `app_type_breakdown.streamlit == 1`.

- **test_stale_port_cleanup:** Allocate a port for `"app-1"`. Set `allocation.allocated_at` to 2 hours ago. Mock socket bind to succeed (port is actually free). Call `cleanup_stale_allocations()`. -> Returns `1`. `"app-1"` is no longer in `app_ports`.

## MCP Port Management

- **test_mcp_port_allocation_within_range:** Create `PortManager` with config `mcp_port_range.start=9001, mcp_port_range.end=9005`. Mock `socket.socket.bind` to succeed. Call `allocate_mcp_port("app-1")`. -> Returns a port between 9001 and 9005 inclusive. `app_mcp_ports["app-1"]` equals the returned port.

- **test_mcp_port_allocation_exhausted:** Create `PortManager` with config `mcp_port_range.start=9001, mcp_port_range.end=9005`. Mock `socket.socket.bind` to raise `OSError` for all ports. Call `allocate_mcp_port("app-1")`. -> Returns `None`.

- **test_mcp_port_release:** Allocate an MCP port for `"app-1"`. Call `release_mcp_port("app-1")`. -> Returns `True`. `get_app_mcp_port("app-1")` returns `None`. The port is no longer in `mcp_allocations`.

- **test_mcp_port_reuse_for_same_app:** Allocate an MCP port for `"app-1"`. Call `allocate_mcp_port("app-1")` again. -> Returns the same port as the first allocation.

- **test_mcp_ports_independent_from_rest:** Allocate a REST port and an MCP port for `"app-1"`. Release REST port. -> MCP port is still allocated. `get_app_mcp_port("app-1")` returns the MCP port.

- **test_port_statistics_includes_mcp:** Create `PortManager` with MCP range 9001-9005 (5 ports). Allocate MCP ports for `"app1"` and `"app2"`. Call `get_port_statistics()`. -> Returns `mcp_total_ports == 5`, `mcp_allocated_ports == 2`, `mcp_utilization_percent == 40.0`.

## MCP Launch Integration

- **test_manifest_rejects_mcp_port:** Create a manifest with `config.mcp_port: 9001`. Call `_parse_manifest()`. -> Returns `None` (manifest rejected).

- **test_discovery_mcp_info_no_port:** Create a manifest with `config.mcp_server: true` (no `mcp_port`). Call `discover_apps()`. -> App is registered. `mcp_info.enabled == True`. `mcp_info.mcp_port` is `None` (port assigned at launch).

- **test_service_template_includes_mcp_port:** Create a service app entry with `mcp_info.enabled=True, mcp_info.mcp_port=9001`. Call `generate_service_template()`. -> Template contains `--mcp-port 9001`.

## MCP Gateway

- **test_mcp_config_defaults:** Create `MCPConfig()` with no arguments. -> `enabled == False`, `transport == "sse"`, `gateway_path == "/mcp"`, `tool_sync_interval_seconds == 300`.

- **test_mcp_gateway_tool_index_build:** Create `MCPGateway` with a mock `app_manager` whose registry returns one healthy MCP-enabled app (`mcp_info.enabled=True`, `mcp_info.healthy=True`, `mcp_info.mcp_port=9001`). Mock `mcp.client.sse.sse_client` and `ClientSession` to return two tools (`get_time`, `echo`). Call `await gateway._build_tool_index()`. -> `gateway._tool_index` has 2 entries with keys `"app_name.get_time"` and `"app_name.echo"`.

- **test_mcp_gateway_tool_index_skips_unhealthy:** Create `MCPGateway` with a mock `app_manager` whose registry returns one MCP-enabled app with `mcp_info.healthy=False`. Call `await gateway._build_tool_index()`. -> `gateway._tool_index` is empty.

- **test_mcp_gateway_list_tools:** Populate `gateway._tool_index` with 3 entries from 2 apps. Call `gateway._handle_list_tools()`. -> Returns a list of 3 `mcp.types.Tool` objects with namespaced names.

- **test_mcp_gateway_call_tool_success:** Populate `gateway._tool_index` with `"crm.add_contact"` pointing to app `crm` on port 9001. Mock registry to return healthy app. Mock `sse_client` + `ClientSession.call_tool` to return `CallToolResult(content=[TextContent(type="text", text="id=42")])`. Call `await gateway._handle_call_tool("crm.add_contact", {"name": "Alice"})`. -> Returns content list with text `"id=42"`.

- **test_mcp_gateway_call_tool_unknown:** Call `await gateway._handle_call_tool("unknown.tool", {})` with empty index. -> Returns list with one `TextContent` containing `"Error: Unknown tool"`.

- **test_mcp_gateway_call_tool_unhealthy:** Populate index with `"crm.add_contact"`. Mock registry to return app with `mcp_info.healthy=False`. Call `await gateway._handle_call_tool("crm.add_contact", {})`. -> Returns list with one `TextContent` containing `"Error: App 'crm' is currently unavailable"`.

- **test_mcp_gateway_on_app_started:** Create gateway with mock app in registry. Mock `_fetch_tools_from_app` to return 2 entries. Call `await gateway.on_app_started("crm")`. -> `gateway._tool_index` has 2 entries. Registry `mcp_info.registered_tools` updated with the 2 tool names.

- **test_mcp_gateway_on_app_stopped:** Populate index with 2 tools for app `crm`. Call `await gateway.on_app_stopped("crm")`. -> `gateway._tool_index` is empty.

- **test_mcp_backward_compat_pass:** Call `MCPGateway.check_backward_compatibility(["search", "add", "delete"], ["search", "add", "delete", "export"])`. -> Returns `(True, [])`.

- **test_mcp_backward_compat_fail:** Call `MCPGateway.check_backward_compatibility(["search", "add", "delete"], ["search", "add"])`. -> Returns `(False, ["delete"])`.

## Web UI Reverse Proxy

- **test_proxy_app_not_found:** Set `web_proxy.app_manager` to a mock with `registry.get_app_by_name("nonexistent")` returning `None`. Call `_lookup_app("nonexistent")`. -> Returns `(None, HTMLResponse)` with status 404 and body containing "App Not Found".

- **test_proxy_app_no_web_ui:** Mock `registry.get_app_by_name("crm")` to return an app with `manifest.config.has_web_ui=False`. Call `_lookup_app("crm")`. -> Returns `(None, HTMLResponse)` with status 404 and body containing "No Web UI".

- **test_proxy_app_not_running:** Mock `registry.get_app_by_name("crm")` to return an app with `has_web_ui=True` but `status="stopped"`. Call `_lookup_app("crm")`. -> Returns `(None, HTMLResponse)` with status 503 and body containing "Unavailable".

- **test_proxy_app_lookup_success:** Mock `registry.get_app_by_name("crm")` to return an app with `has_web_ui=True`, `status="running"`, `runtime_info.assigned_port=8101`. Call `_lookup_app("crm")`. -> Returns `(app_entry, None)`.

- **test_proxy_forwarded_headers:** Create a mock `Request` with `client.host="192.168.1.10"`, `url.scheme="https"`, `headers={"host": "latarnia:8000", "accept": "text/html"}`. Call `_build_forwarded_headers(request)`. -> Returns dict with `x-forwarded-for="192.168.1.10"`, `x-forwarded-proto="https"`, `x-forwarded-host="latarnia:8000"`. "host" header is removed. "accept" header is preserved.

- **test_proxy_http_success:** Use `httpx.AsyncClient` with FastAPI `TestClient` (ASGI transport). Mock a running app `crm` with `has_web_ui=True` on port 8101. Mock `httpx.AsyncClient.request` to return a 200 response with body `"<h1>CRM</h1>"`. Send GET to `/apps/crm/dashboard`. -> Response status 200, body contains `"<h1>CRM</h1>"`.

- **test_proxy_http_connect_error:** Mock a running app `crm`. Mock `httpx.AsyncClient.request` to raise `httpx.ConnectError`. Send GET to `/apps/crm/`. -> Response status 503 with body containing "Cannot connect".

- **test_proxy_bare_app_redirect:** Mock a running app `crm` with `has_web_ui=True`. Send GET to `/apps/crm` (no trailing slash). -> Response status 307 with `Location: /apps/crm/`.

## MCP SSE Transport (Integration)

- **test_mcp_sse_endpoint_connects:** Build the MCP Starlette app from `examples/mcp_server_example.py` via `_build_mcp_app()`. Start it with `uvicorn.Server` on a random available port in a background thread. Send GET to `http://127.0.0.1:{port}/sse` with a 3-second timeout. -> Response status 200. First line of body starts with `event: endpoint`. Stop the server.

- **test_mcp_sse_session_no_errors:** Build the MCP Starlette app from `examples/mcp_server_example.py`. Start it on a random port. Use `mcp.client.sse.sse_client` to connect to `http://127.0.0.1:{port}/sse`, create a `ClientSession`, call `initialize()`, then `list_tools()`. -> Returns a list containing tools named `get_time` and `echo`. Disconnect the client. Check that uvicorn logged no `TypeError` or `500 Internal Server Error` during the session. Stop the server.

- **test_mcp_gateway_sse_endpoint_connects:** Import `MCPGateway` and create an instance with a mocked `app_manager` (no apps). Call `await gateway.initialize()`. Start the gateway's `_asgi_app` with `uvicorn.Server` on a random port in a background thread. Send GET to `http://127.0.0.1:{port}/sse` with a 3-second timeout. -> Response status 200. First line of body starts with `event: endpoint`. Stop the server.

## Dashboard Updates

- **test_dashboard_capability_badges_mcp:** Call `buildCapabilityBadges` (JS function) with app data containing `mcp_info: {enabled: true, healthy: true, registered_tools: ["search", "add"]}`. -> Returns HTML string containing `"MCP: 2 tools"` and `"bg-info"`.

- **test_dashboard_capability_badges_db:** Call `buildCapabilityBadges` with app data containing `database_info: {provisioned: true, applied_migrations: ["001", "002", "003"]}`. -> Returns HTML string containing `"DB (3 migrations)"` and `"bg-success"`.

- **test_dashboard_capability_badges_streams:** Call `buildCapabilityBadges` with app data containing `stream_info: {publish_streams: ["events"], subscribe_streams: ["commands", "data"]}`. -> Returns HTML string containing `"Streams: 1 pub / 2 sub"`.

- **test_dashboard_capability_badges_legacy:** Call `buildCapabilityBadges` with app data containing no `mcp_info`, no `database_info`, no `stream_info`. -> Returns empty string.

- **test_dashboard_web_ui_button:** Render a service app card with `manifest.config.has_web_ui=true` and `name="crm"`. -> Card HTML contains `<a` tag with `href="/apps/crm/"` and text `"Web UI"`.

- **test_dashboard_error_unmet_deps:** Call `buildErrorAlerts` with app data containing `dependencies: [{app: "kb", satisfied: false}]`. -> Returns HTML containing `"Unmet deps: kb"`.

## Integration Tests — Full Stack (Playwright MCP)

These tests require the Playwright MCP server and a running local dev instance of Latarnia with the example apps installed in `apps/`. The `example_full_app` and `example_companion` apps serve as the integration test fixtures for the platform — they exercise every platform feature (DB, MCP, Redis Streams, web UI proxy, dependencies).

**Prerequisite:** Copy `examples/example_full_app` and `examples/example_companion` to `apps/` before running. Start Latarnia dev server on localhost:8000. Start the example_full_app via the API (`POST /api/apps/example-full-app/process/start`).

### Dashboard

- **test_dashboard_page_loads:** Use Playwright to navigate to `http://localhost:8000/dashboard`. -> Page title contains "Latarnia". Page contains an element with text "System Health". No JavaScript console errors.

- **test_dashboard_shows_app_cards:** Navigate to `http://localhost:8000/dashboard`. -> Page contains app cards for both `example_full_app` and `example_companion`. Each card displays the app name and status.

- **test_dashboard_health_badge:** Navigate to `http://localhost:8000/dashboard`. -> Health status badge is visible and shows either "good", "warning", or "error".

- **test_dashboard_capability_badges_render:** Navigate to `http://localhost:8000/dashboard`. -> The `example_full_app` card shows capability badges for MCP (with tool count), DB (with migration count), and Streams (with pub/sub counts).

- **test_app_web_ui_opens_in_modal:** Navigate to `http://localhost:8000/dashboard`. Click the "Web UI" button on the `example_full_app` card. -> A modal/iframe opens displaying the app's web UI with the "Example Full App" heading.

### App Web UI

- **test_example_app_web_ui_loads:** Use Playwright to navigate to `http://localhost:8100/`. -> Page contains heading "Example Full App". Page contains "Add Item" form with name and description inputs. Items table is visible.

- **test_example_app_add_item_via_ui:** Navigate to `http://localhost:8100/`. Fill in the "Item name" input with "playwright-test-item" and description with "added by test". Click the "Add" button. -> Items table updates to include a row with "playwright-test-item".

### MCP via Platform Gateway

- **test_mcp_dynamic_port_allocation:** After example_full_app is started, send GET to `http://localhost:8000/api/apps`. -> The `example_full_app` entry has `mcp_info.enabled == true` and `mcp_info.mcp_port` is an integer in range 9001-9099 (dynamically allocated, not hardcoded).

- **test_mcp_gateway_lists_example_tools:** After example_full_app is started, send GET to `http://localhost:8000/api/apps`. -> The `example_full_app` entry has `mcp_info.healthy == true` and `mcp_info.registered_tools` contains `["list_items", "add_item", "get_status"]`.

- **test_mcp_gateway_proxies_tool_call:** Connect an MCP client to `http://localhost:8000/mcp/sse`. Call `list_tools()`. -> Result includes tools prefixed with `example_full_app.` (e.g., `example_full_app.list_items`, `example_full_app.add_item`, `example_full_app.get_status`). Call `example_full_app.get_status` with no arguments. -> Returns JSON with `health == "good"` and `db_connected == true`.

### Redis Streams

- **test_item_creation_publishes_event:** Add an item via `POST http://localhost:8100/api/items?name=stream-test`. -> Response 200 with item data. Check Redis stream `latarnia:streams:example.events.created` (via `redis-cli XRANGE latarnia:streams:example.events.created - + COUNT 1`). -> Last entry contains `source == "example_full_app"` and data contains `"item_created"`.

### Database

- **test_example_app_db_provisioned:** Query the API `GET http://localhost:8000/api/apps`. -> The `example_full_app` entry has `database_info.provisioned == true` and `database_info.applied_migrations` contains `["001_initial.sql", "002_add_tags.sql", "003_add_status.sql"]`.

## Authentication, Roles & Tokens (P-0008)

> After P-0008, `/api/*` and the MCP gateway require authentication. Browser
> flows use a session cookie; machine/MCP clients use a Bearer JWT. The
> platform auth DB is `latarnia_platform_{env}`. Caddy/ufw runtime criteria
> (cap-001/003/006) are validated on the Pi/tst, not locally.

### Platform DB & Setup

- **test_platform_auth_db_created:** Start Latarnia (dev). Connect to Postgres `latarnia_platform_dev`. -> DB exists; tables `users`, `user_credentials`, `sessions`, `app_roles`, `machine_tokens`, `schema_versions` are present.
- **test_first_run_totp_setup:** With no active users, `GET /auth/setup`. -> Renders a QR-code page (account `admin`). Submitting a valid TOTP code -> 303 redirect to `/dashboard` with a `latarnia_session` cookie; the user becomes an active superuser.

### Login & Session

- **test_totp_login:** `POST /auth/login` with username `admin` + a valid 6-digit code. -> 302/303 with `latarnia_session` cookie. Replaying the same code within its 30s window -> rejected.
- **test_verify_headers:** `GET /auth/verify` with a valid session cookie and `X-Forwarded-Uri: /apps/example_full_app/`. -> 200 with `X-Latarnia-User`, `X-Latarnia-App-Role`, `X-Latarnia-Is-Super`. Without a cookie -> 401.

### Roles

- **test_dashboard_tile_role_filtering:** As a non-superuser with role `none` for `example_full_app`, load `/dashboard` (GET `/api/apps`). -> The `example_full_app` tile is absent. Grant `webUI-low` -> tile appears on next load with a role badge.
- **test_assign_full_requires_superuser:** `POST /api/auth/roles/example_full_app` `{"role":"full","user_id":...}` as a non-superuser -> 403; as a superuser -> 200.

### Machine Tokens

- **test_token_issue_and_use:** `POST /api/auth/tokens` `{"label":"agent","app_scope":{"example_full_app":"webUI-med"}}` (session-authed) -> returns a raw JWT once. `GET /api/apps` with `Authorization: Bearer <jwt>` -> 200. No token -> 401.
- **test_token_revocation:** `DELETE /api/auth/tokens/{id}` -> token marked revoked; a subsequent `GET /api/apps` with that token -> 401.
- **test_token_scope_enforced:** A non-superuser token scoped to app A -> `GET /api/apps/{B}` returns 403.

### Role-Aware Example App

- **test_example_webui_role_header:** `GET http://localhost:8100/` with header `X-Latarnia-App-Role: webUI-low` -> no "Add Item" form. With `full` -> "Add Item" form present and an "Admin" section. No header -> defaults to full (backward compatible).
- **test_mcp_requires_bearer:** Connect an MCP client to `/mcp/sse` without a Bearer token -> 401. With a valid in-scope token -> tool list scoped to the token's apps; the per-app MCP server receives `X-Latarnia-App-Role`.
