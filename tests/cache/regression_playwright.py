"""TESTS.md "Integration Tests — Full Stack (Playwright MCP)" Dashboard + App Web UI entries.

Spec hash (tests/cache/regression_playwright.hash): sha256 of the seven entry lines
  grep -E '^-\\s+\\*\\*(test_dashboard_page_loads|test_dashboard_shows_app_cards|test_dashboard_health_badge|test_dashboard_capability_badges_render|test_app_web_ui_opens_in_modal|test_example_app_web_ui_loads|test_example_app_add_item_via_ui):' TESTS.md | sha256sum

Needs the local dev server on :8000 (ENV=dev) with example_full_app and example_companion
running. Auth: a superuser session cookie in S_SU (from tests/cache/t20_fixture.py).
The example App's web UI port is read from /api/apps (another App may hold 8100 locally).

test_app_web_ui_opens_in_modal: on :8000 the Web UI button must open a new tab at
/apps/example_full_app/. The platform itself does not serve /apps/* (Caddy does), so the
"tab shows the App's web UI" half is checked through a local real-`caddy` ingress built from
the platform-generated caddy/latarnia.caddyfile, with only the two edits the real-chain test
makes (site line `localhost {` -> `http://:<port> {`, global options prepended).
Usage: S_SU=<session> ENV=dev .venv/bin/python tests/cache/regression_playwright.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

assert os.environ.get("ENV") == "dev", "dev only"
ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("BASE", "http://localhost:8000")
S_SU = os.environ["S_SU"]
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_ingress():
    """Real caddy in front of the dev server, using the generated Caddyfile."""
    caddy = shutil.which("caddy")
    if not caddy:
        return None, None, None, "caddy not on PATH"
    gen = (ROOT / "caddy" / "latarnia.caddyfile").read_text()
    if "localhost {" not in gen or "/apps/example_full_app/*" not in gen:
        return None, None, None, "generated Caddyfile has no localhost site or no example_full_app block"
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="reg_ingress_")
    cf = Path(tmp) / "Caddyfile"
    cf.write_text("{\n\tadmin off\n\tauto_https off\n\tdefault_bind 127.0.0.1\n}\n\n"
                  + gen.replace("localhost {", f"http://:{port} {{", 1))
    proc = subprocess.Popen([caddy, "run", "--config", str(cf), "--adapter", "caddyfile"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc, f"http://localhost:{port}", tmp, ""
        except OSError:
            time.sleep(0.1)
    proc.terminate()
    return None, None, tmp, "caddy did not start"


req = urllib.request.Request(f"{BASE}/api/apps", headers={"Cookie": f"latarnia_session={S_SU}"})
apps = {a["name"]: a for a in json.load(urllib.request.urlopen(req))["apps"]}
PORT = apps["example_full_app"]["runtime_info"]["assigned_port"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context()
    # Cookies are not port-scoped: this one also reaches the local Caddy ingress on localhost.
    ctx.add_cookies([{"name": "latarnia_session", "value": S_SU, "url": BASE}])
    page = ctx.new_page()
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.goto(f"{BASE}/dashboard")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2500)

    # test_dashboard_page_loads
    title = page.title()
    has_header = page.locator(".card-header", has_text="System Status").count() > 0
    check("test_dashboard_page_loads",
          "Latarnia" in title and has_header and not console_errors,
          f"title={title!r} 'System Status' card header={has_header} console_errors={console_errors[:3]}")

    # test_dashboard_shows_app_cards
    cards = {}
    for name in ("example_full_app", "example_companion"):
        # App tiles are `.card.h-100.hh-card`; the section wrapper is also `.card.hh-card`.
        cards[name] = page.locator(".card.h-100.hh-card",
                                   has=page.locator(".card-title span", has_text=name))
    shown = {n: c.count() > 0 and "Status:" in c.first.inner_text() for n, c in cards.items()}
    check("test_dashboard_shows_app_cards", all(shown.values()), f"cards with name+status: {shown}")

    # test_dashboard_health_badge: Redis + Postgres badges in System Status, each "Connected"
    sys_card = page.locator(".card", has=page.locator(".card-header", has_text="System Status")).first
    badges = {k: page.locator(s).inner_text().strip()
              for k, s in (("redis", "#hh-redis-status"), ("postgres", "#hh-postgres-status"))}
    in_card = all(sys_card.locator(s).count() == 1 for s in ("#hh-redis-status", "#hh-postgres-status"))
    sys_text = sys_card.inner_text()
    labels = "Redis" in sys_text and "Postgres" in sys_text
    connected = all(b.endswith("Connected") and "Disconnected" not in b for b in badges.values())
    check("test_dashboard_health_badge", in_card and labels and connected,
          f"badges={badges} in System Status card={in_card} labels Redis/Postgres={labels}")

    # test_dashboard_capability_badges_render
    full_card = cards["example_full_app"].first.inner_text() if cards["example_full_app"].count() else ""
    cap = {"MCP": "MCP:" in full_card and "tools" in full_card,
           "DB": "DB (" in full_card and "migrations" in full_card,
           "Streams": "Streams:" in full_card and "pub" in full_card and "sub" in full_card}
    check("test_dashboard_capability_badges_render", all(cap.values()), f"{cap}")

    # test_app_web_ui_opens_in_modal: click Web UI -> new tab at /apps/example_full_app/
    def click_web_ui(pg, base):
        card = pg.locator(".card.h-100.hh-card",
                          has=pg.locator(".card-title span", has_text="example_full_app"))
        btn = card.first.get_by_text("Web UI", exact=True)
        if not btn.count():
            return None, "no Web UI button on example_full_app card"
        with pg.expect_popup(timeout=8000) as pinfo:
            btn.first.click()
        tab = pinfo.value
        tab.wait_for_load_state()
        return tab, ""

    tab, err = click_web_ui(page, BASE)
    path_ok = tab is not None and tab.url.rstrip("/").endswith("/apps/example_full_app") and \
        tab.url.startswith(BASE)
    detail = f":8000 new tab url={tab.url if tab else None} {err}".strip()
    if tab:
        tab.close()
    ing_ok = False
    proc, ingress, tmp, why = start_ingress()
    try:
        if proc:
            ipage = ctx.new_page()
            ipage.goto(f"{ingress}/dashboard")
            ipage.wait_for_load_state("networkidle")
            ipage.wait_for_timeout(2500)
            itab, err = click_web_ui(ipage, ingress)
            if itab:
                itab.wait_for_timeout(1000)
                h = itab.locator("h1, h2, h3").filter(has_text="Example Full App").count()
                ing_ok = itab.url == f"{ingress}/apps/example_full_app/" and h > 0
                detail += f"; via local caddy ingress: tab url={itab.url} heading 'Example Full App'={h > 0}"
            else:
                detail += f"; via local caddy ingress: {err}"
        else:
            detail += f"; local caddy ingress not started: {why}"
    finally:
        if proc:
            proc.terminate()
            proc.wait(timeout=10)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    check("test_app_web_ui_opens_in_modal", path_ok and ing_ok, detail)

    # test_example_app_web_ui_loads
    app = ctx.new_page()
    app.goto(f"http://localhost:{PORT}/")
    body = app.inner_text("body")
    ok = ("Example Full App" in body and "Add Item" in body
          and app.locator("input").count() >= 2 and app.locator("table").count() > 0)
    check("test_example_app_web_ui_loads", ok, f"port {PORT}")

    # test_example_app_add_item_via_ui
    name_in = app.get_by_placeholder("Item name")
    if name_in.count() == 0:
        name_in = app.locator("input[name=name]")
    desc_in = app.locator("input[name=description], textarea[name=description]")
    if desc_in.count() == 0:
        desc_in = app.get_by_placeholder("Description")
    before = app.locator("table tr", has_text="playwright-test-item").count()
    name_in.first.fill("playwright-test-item")
    desc_in.first.fill("added by test")
    app.get_by_role("button", name="Add").first.click()
    app.wait_for_timeout(1500)
    row = app.locator("table tr", has_text="playwright-test-item")
    check("test_example_app_add_item_via_ui", row.count() == before + 1,
          f"rows with item: before={before} after={row.count()}")
    browser.close()

failed = [n for n, ok in results if not ok]
print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
