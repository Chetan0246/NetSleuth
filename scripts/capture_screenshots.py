#!/usr/bin/env python3
"""Capture the application screenshots used in the report.

Requires a running backend (``uvicorn app.main:app --port 8000``) and frontend
(``npm run dev``), plus Playwright's Chromium::

    pip install playwright && playwright install chromium
    python3 scripts/capture_screenshots.py

Writes five PNGs to ``docs/screenshots/``. The sessions it creates are deleted
again on exit, so a capture run leaves the database as it found it.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request as u
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
APP = os.environ.get("NETSLEUTH_APP_URL", "http://127.0.0.1:5173")
API = os.environ.get("NETSLEUTH_API_URL", "http://127.0.0.1:8000/api/v1")

#: Route black hole on the campus edge toward the web server: a confident,
#: link-localized diagnosis, which makes the timeline and report meaningful.
FAULT_VALUE = 'ROUTE_BLACKHOLE|l-campus-edge|{"destination_node_id":"web-1"}'


def session_ids() -> set[str]:
    with u.urlopen(API + "/lab/sessions?limit=200") as response:
        return {item["id"] for item in json.loads(response.read())["sessions"]}


def delete_session(session_id: str) -> int:
    request = u.Request(f"{API}/lab/sessions/{session_id}", method="DELETE")
    with u.urlopen(request) as response:
        return response.status


def shot(page, name: str, *, full: bool = False, element=None) -> None:
    path = OUT / name
    if element is not None:
        element.screenshot(path=str(path))
    else:
        page.screenshot(path=str(path), full_page=full)
    print(f"saved {name} ({path.stat().st_size} bytes)")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    before = session_ids()
    print(f"sessions before: {before or '{}'}")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 950}, device_scale_factor=2
            )
            page = context.new_page()
            page.set_default_timeout(40_000)

            # --- Network Lab: create a session, capture the topology ---
            page.goto(APP + "/lab", wait_until="networkidle")
            page.get_by_role(
                "button", name="Create session from Small Campus Network"
            ).click()
            page.get_by_text("Active session").wait_for()
            topology_svg = page.get_by_role("img", name=re.compile("Network topology"))
            topology_svg.wait_for()
            page.wait_for_timeout(700)
            shot(
                page,
                "01-network-topology.png",
                element=topology_svg.locator("xpath=ancestor::section[1]"),
            )

            # --- Inject the fault, capture the injection panel ---
            select = page.get_by_label("Fault scenario")
            offered = [
                select.locator("option").nth(i).get_attribute("value")
                for i in range(select.locator("option").count())
            ]
            assert FAULT_VALUE in offered, f"fault not offered: {FAULT_VALUE}"
            select.select_option(value=FAULT_VALUE)
            page.get_by_role("button", name="Inject fault").click()
            page.get_by_role(
                "heading", name=re.compile(r"Active faults\s*\(1\)")
            ).wait_for()
            page.wait_for_timeout(700)
            shot(
                page,
                "02-fault-injection.png",
                element=page.locator("section").filter(has_text="Fault injection"),
            )

            # --- Workbench: run the adaptive diagnosis, capture the timeline ---
            page.get_by_role("link", name="Diagnostic Workbench").click()
            page.get_by_role(
                "button", name=re.compile("Start & run to conclusion")
            ).click()
            page.get_by_text("Probe timeline").wait_for()
            page.wait_for_function(
                "document.body.innerText.includes('confident')", timeout=60_000
            )
            page.wait_for_timeout(900)
            shot(page, "03-adaptive-diagnostic-timeline.png", full=True)

            # --- Report: capture the final explanation ---
            page.get_by_role("link", name="Evidence & Report").click()
            page.get_by_role(
                "heading", name="Evidence & Diagnosis Report"
            ).wait_for()
            page.get_by_text("Evidence supporting this hypothesis").wait_for()
            page.wait_for_timeout(700)
            shot(page, "04-final-explanation.png", full=True)

            # --- Experiment Studio: run a small suite, capture the comparison ---
            page.get_by_role("link", name="Experiment Studio").click()
            page.get_by_role("button", name="Run experiment suite").click()
            page.get_by_text("Top-1 accuracy").first.wait_for(timeout=180_000)
            page.wait_for_timeout(1_200)
            shot(page, "05-experiment-comparison.png", full=True)

            browser.close()
    finally:
        for session_id in session_ids() - before:
            print(f"cleanup session {session_id} -> {delete_session(session_id)}")
        print(f"sessions after: {session_ids() or '{}'}")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
