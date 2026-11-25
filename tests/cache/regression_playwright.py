"""TESTS.md "Integration Tests — Full Stack (Playwright)" Dashboard + App Web UI entries.

Needs the local dev server on :8000 (ENV=dev) with example_full_app and example_companion
running. Auth: a superuser session cookie in S_SU (from tests/cache/t20_fixture.py).
The example App's web UI port is read from /api/apps (the spec's 8100 is the first port of
the range; another App may hold it locally).
Usage: S_SU=<session> ENV=dev .venv/bin/python tests/cache/regression_playwright.py
"""
import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

assert os.environ.get("ENV") == "dev", "dev only"
BASE = os.environ.get("BASE", "http://localhost:8000")
S_SU = os.environ["S_SU"]
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))


req = urllib.request.Request(f"{BASE}/api/apps", headers={"Cookie": f"latarnia_session={S_SU}"})
apps = {a["name"]: a for a in json.load(urllib.request.urlopen(req))["apps"]}
PORT = apps["example_full_app"]["runtime_info"]["assigned_port"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context()
    ctx.add_cookies([{"name": "latarnia_session", "value": S_SU, "url": BASE}])
    page = ctx.new_page()
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.goto(f"{BASE}/dashboard")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)

    # test_dashboard_page_loads
    title = page.title()
    has_sys_health = page.get_by_text("System Health").count() > 0
    check("test_dashboard_page_loads",
          "Latarnia" in title and has_sys_health and not console_errors,
          f"title={title!r} 'System Health' present={has_sys_health} console_errors={console_errors[:3]}")

    # test_dashboard_shows_app_cards
    cards = {}
    for name in ("example_full_app", "example_companion"):
        # App tiles are `.card.h-100.hh-card`; the section wrapper is also `.card.hh-card`.
        card = page.locator(".card.h-100.hh-card", has=page.locator(".card-title span", has_text=name))
        cards[name] = card
    shown = {n: c.count() > 0 and "Status:" in c.first.inner_text() for n, c in cards.items()}
    check("test_dashboard_shows_app_cards", all(shown.values()), f"cards with name+status: {shown}")

    # test_dashboard_health_badge
    badges = [page.locator(s).inner_text().strip() for s in ("#hh-redis-status", "#hh-postgres-status")]
    words = ("good", "warning", "error")
    badge_ok = any(any(w in b.lower() for w in words) for b in badges)
    check("test_dashboard_health_badge", badge_ok, f"status badges={badges}")

    # test_dashboard_capability_badges_render
    full_card = cards["example_full_app"].first.inner_text() if cards["example_full_app"].count() else ""
    cap = {"MCP": "MCP:" in full_card and "tools" in full_card,
           "DB": "DB (" in full_card and "migrations" in full_card,
           "Streams": "Streams:" in full_card and "pub" in full_card and "sub" in full_card}
    check("test_dashboard_capability_badges_render", all(cap.values()), f"{cap}")

    # test_app_web_ui_opens_in_modal
    btn = cards["example_full_app"].first.get_by_text("Web UI", exact=True)
    detail, ok = "", False
    if btn.count():
        target = btn.first.get_attribute("target")
        href = btn.first.get_attribute("href")
        popup = None
        try:
            with page.expect_popup(timeout=5000) as pinfo:
                btn.first.click()
            popup = pinfo.value
            popup.wait_for_load_state()
        except Exception:
            pass
        page.wait_for_timeout(1000)
        modal_iframe = page.locator(".modal.show iframe")
        in_modal = modal_iframe.count() > 0 and "Example Full App" in (
            modal_iframe.first.content_frame.locator("body").inner_text() if modal_iframe.count() else "")
        popup_heading = popup is not None and "Example Full App" in popup.content()
        ok = in_modal
        detail = (f"href={href!r} target={target!r} modal+iframe with heading={in_modal}; "
                  f"opened new tab instead={popup is not None} (tab has heading={popup_heading})")
    else:
        detail = "no Web UI button on example_full_app card"
    check("test_app_web_ui_opens_in_modal", ok, detail)

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
    name_in.first.fill("playwright-test-item")
    desc_in.first.fill("added by test")
    app.get_by_role("button", name="Add").first.click()
    app.wait_for_timeout(1500)
    row = app.locator("table tr", has_text="playwright-test-item")
    check("test_example_app_add_item_via_ui", row.count() > 0, f"rows with item: {row.count()}")
    browser.close()

failed = [n for n, ok in results if not ok]
print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
