#!/usr/bin/env python
"""End-to-end check for Cards 4A/4B/4C -- the Pre-Well Delivery detail pages.

Boots the Flask app against a SCRATCH SQLite database (never the real
pipeline_tracker.db), drives a real browser through the workflow the cards
describe, and writes screenshots.

The journey, in order:
  1. create a lead, open its detail page and expand PRE-WELL DELIVERY;
  2. MOVING TOLERANCE (4A): a PARTIAL capture saves and leaves the dot open;
     then the full eight fields flip it green with no Submit/Approve click;
  3. STAKING LETTERS (4B): either rail row opens the SAME page. Tick boxes 1+2
     and save -> Approval to Stake alone turns green. Tick box 3 -> the Staking
     Location reveals; fill Staked X/Y and save -> Well Site Location turns
     green too;
  4. the reveal-persist promise: untick box 3, save, re-tick -- the coordinates
     are still there;
  5. PRE-DRILLING GEOX (4C): the step renders the calculator and NO duplicate
     PIIP grid, and still completes by the manual walk.

Screenshots (--out, default ./screenshots/card-4):
  01-moving-tolerance-light.png     the 4A page, filled, light theme
  02-moving-tolerance-dark.png      the same, dark theme
  03-staking-letters-collapsed.png  4B before the third box is ticked
  04-staking-letters-light.png      4B revealed + filled, light theme
  05-staking-letters-dark.png       the same, dark theme
  06-geox-assessment.png            4C: calculator, no duplicate grid

Run:  .venv/bin/python scripts/e2e_card_4.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PYTHON = BASE_DIR / ".venv" / "bin" / "python"
HOST = "127.0.0.1"
PORT = 8023  # 8020 dev, 8021 the harness, 8022 the card-2B e2e.
ROOT_URL = f"http://{HOST}:{PORT}"
BOOT_TIMEOUT_S = 30

LEAD_NAME = "WWWW-44"

# Card 4A's eight fields. The location pair prefills from the project's lead
# X/Y, so the journey types all eight anyway -- a prefill the user never looked
# at is not a capture.
TOLERANCE_PARTIAL = {
    "staking_well_x": "532100.5",
    "staking_well_y": "2895120.1",
    "staking_opt1_max_distance_m": "150",
    "staking_opt1_azimuth_deg": "45",
}
TOLERANCE_REST = {
    "staking_opt2_max_distance_m": "220",
    "staking_opt2_azimuth_deg": "180",
    "staking_opt3_max_distance_m": "90",
    "staking_opt3_azimuth_deg": "310",
}

STAKED_X = "533000.25"
STAKED_Y = "2894000.75"


def wait_for_health(deadline_s: float) -> bool:
    started = time.time()
    while time.time() - started < deadline_s:
        try:
            with urllib.request.urlopen(f"{ROOT_URL}/api/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    return False


def start_server(db_path: Path):
    env = dict(os.environ,
               SEGMENT_TRACKER_DB_PATH=str(db_path),
               SEGMENT_TRACKER_AUTH_REQUIRED="0")
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "flask", "--app", "main", "run", "--port", str(PORT)],
        cwd=str(BASE_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_for_health(BOOT_TIMEOUT_S):
        proc.terminate()
        raise SystemExit(f"server did not boot on {ROOT_URL}")
    return proc


def seed_lead() -> int:
    request = urllib.request.Request(
        f"{ROOT_URL}/api/projects",
        data=json.dumps({"project_name": LEAD_NAME}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())["project_id"]


def board_tracked_items(project_id):
    with urllib.request.urlopen(f"{ROOT_URL}/api/projects?pipeline_filter=prospect", timeout=10) as response:
        rows = json.loads(response.read())
    row = next((r for r in rows if r["project_id"] == project_id), None)
    if row is None:  # a fully matured lead leaves the prospect board
        return {}
    return {item["label"]: item["status"] for item in row["tracked_items"]}


def portfolio_status(project_id):
    with urllib.request.urlopen(f"{ROOT_URL}/api/portfolio/rows", timeout=10) as response:
        rows = json.loads(response.read())["rows"]
    row = next((r for r in rows if r["project_id"] == project_id), None)
    return row["status"] if row else None


def rail_statuses(page):
    """{step name: status slug} from the rail rows' own status- classes."""
    out = {}
    for row in page.locator(".component-item[data-task-id]").all():
        name = row.locator("b").inner_text().strip()
        classes = row.get_attribute("class") or ""
        slug = next((c[len("status-"):] for c in classes.split() if c.startswith("status-")), "")
        out[name] = slug
    return out


def unstick(page, off):
    """Turn every position:sticky element static (screenshot hygiene only)."""
    page.evaluate(
        "(off) => { document.querySelectorAll('*').forEach(el => {"
        " if (off) { if (getComputedStyle(el).position === 'sticky')"
        "   { el.dataset.wasSticky = '1'; el.style.position = 'static'; } }"
        " else if (el.dataset.wasSticky) { el.style.position = ''; delete el.dataset.wasSticky; } }); }",
        off)


def set_theme(page, theme):
    page.evaluate(
        "(t) => { const r = document.documentElement;"
        " if (t === 'dark') r.dataset.theme = 'dark'; else delete r.dataset.theme;"
        " try { localStorage.setItem('theme', t); } catch (e) {} }", theme)


def shoot_both_themes(page, out_dir, light_name, dark_name):
    unstick(page, True)
    shell = page.locator("#detail-shell")
    shell.screenshot(path=str(out_dir / light_name))
    set_theme(page, "dark")
    page.wait_for_timeout(250)
    shell.screenshot(path=str(out_dir / dark_name))
    set_theme(page, "light")
    page.wait_for_timeout(250)
    unstick(page, False)


def open_step(page, name):
    page.locator(f'.component-item:has-text("{name}")').first.click()
    page.wait_for_timeout(400)


def save(page):
    page.locator("#save-component").click()
    page.wait_for_selector("#app-message", timeout=15000)
    page.wait_for_timeout(1200)


def run(out_dir: Path, headed: bool) -> int:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    failures = []

    def check(condition, message):
        if condition:
            print(f"  ok   {message}")
        else:
            failures.append(message)
            print(f"  FAIL {message}")

    project_id = seed_lead()
    print(f"seeded lead {LEAD_NAME} (project_id={project_id})")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        page = browser.new_page(viewport={"width": 1680, "height": 1000})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        page.goto(ROOT_URL, wait_until="networkidle")
        page.locator(f'.lead-card:has-text("{LEAD_NAME}"), .project-card:has-text("{LEAD_NAME}")').first.click()
        page.wait_for_selector("#detail-shell:not(.hidden)")
        page.locator(".rail-stage-head:has-text('PRE-WELL DELIVERY')").click()
        page.wait_for_timeout(300)

        # ================= Card 4A -- Moving Tolerance =====================
        print("\nCard 4A -- Moving Tolerance")
        open_step(page, "Moving Tolerance")
        page.wait_for_selector('#dynamic-fields [data-field="staking_well_x"]')
        check(page.locator("#component-title").inner_text().strip() == "Moving Tolerance",
              "the page is titled Moving Tolerance")

        labels = [el.inner_text().split("\n")[0].strip()
                  for el in page.locator("#dynamic-fields label").all()]
        for expected in ["Lead X Coordinate", "Lead Y Coordinate",
                         "Option 1 Max Distance (m)", "Option 1 Azimuth (°)",
                         "Option 2 Max Distance (m)", "Option 2 Azimuth (°)",
                         "Option 3 Max Distance (m)", "Option 3 Azimuth (°)"]:
            check(expected in labels, f"label {expected!r} renders")
        check(len(page.locator("#dynamic-fields .field-row.cols-2").all()) == 4,
              "four 2-column rows (the mockup's grid)")

        # --- a PARTIAL capture saves and leaves the dot open ---------------
        for key, value in TOLERANCE_PARTIAL.items():
            page.locator(f'#dynamic-fields [data-field="{key}"]').fill(value)
        save(page)
        check(rail_statuses(page).get("Moving Tolerance") != "approved",
              "a 4-of-8 capture SAVES but leaves the item open")
        check(page.locator('#dynamic-fields [data-field="staking_opt1_azimuth_deg"]').input_value() == "45",
              "and the partial values really persisted")

        # --- the full eight flip it green ----------------------------------
        for key, value in TOLERANCE_REST.items():
            page.locator(f'#dynamic-fields [data-field="{key}"]').fill(value)
        shoot_both_themes(page, out_dir, "01-moving-tolerance-light.png",
                          "02-moving-tolerance-dark.png")
        save(page)
        check(rail_statuses(page).get("Moving Tolerance") == "approved",
              "all eight fields turn the dot green with no Submit/Approve click")
        check(board_tracked_items(project_id).get("Moving Tolerance") == "Completed",
              "and the board tracked item reads Completed")

        # ================= Card 4B -- Staking Letters ======================
        print("\nCard 4B -- Staking Letters")
        # EITHER rail row opens the same page -- start from the SECOND to prove
        # it is not just "the first step's form".
        open_step(page, "Well Site Location")
        page.wait_for_selector(".sl-workspace")
        check(page.locator("#component-title").inner_text().strip() == "Staking Letters",
              "the consolidated page is titled Staking Letters")
        boxes = [el.inner_text().strip() for el in page.locator(".sl-check").all()]
        check(boxes == [
            "Well creation and well folder are completed",
            "The Approval to Stake letter is placed in the shared folder",
            "The Wellsite Location letter is placed in the shared folder",
        ], f"three confirmations in process order ({boxes})")
        check(page.locator('[data-sl-section="location"].hidden').count() == 1,
              "the Staking Location starts hidden")
        page.locator('[data-sl-section="letters"]').screenshot(
            path=str(out_dir / "03-staking-letters-collapsed.png"))

        # ...and the OTHER rail row opens the very same page.
        open_step(page, "Approval to Stake")
        page.wait_for_selector(".sl-workspace")
        check(page.locator(".sl-check").count() == 3, "either rail row opens the same page")

        # --- boxes 1 + 2 -> Approval to Stake alone turns green -------------
        page.locator('[data-sl-field="staking_well_created"]').check()
        page.locator('[data-sl-field="approval_stake_letter_loaded"]').check()
        save(page)
        statuses = rail_statuses(page)
        check(statuses.get("Approval to Stake") == "approved",
              f"boxes 1+2 complete Approval to Stake (got {statuses.get('Approval to Stake')!r})")
        check(statuses.get("Well Site Location") != "approved",
              "and leave Well Site Location alone")
        check(portfolio_status(project_id) in (None, "Staked"),
              "the record reads Staked wherever the Portfolio can see it")

        # --- box 3 reveals the location, coordinates complete the second item
        page.wait_for_selector(".sl-workspace")
        page.locator('[data-sl-field="wellsite_letter_loaded"]').check()
        page.wait_for_timeout(200)
        check(page.locator('[data-sl-section="location"]:not(.hidden)').count() == 1,
              "ticking box 3 reveals the Staking Location")
        check(page.locator('[data-sl-field="staked_x"]').get_attribute("placeholder")
              == "Staked X Coordinate", "with the mockup's placeholder text")
        page.locator('[data-sl-field="staked_x"]').fill(STAKED_X)
        page.locator('[data-sl-field="staked_y"]').fill(STAKED_Y)
        shoot_both_themes(page, out_dir, "04-staking-letters-light.png",
                          "05-staking-letters-dark.png")
        save(page)
        statuses = rail_statuses(page)
        check(statuses.get("Well Site Location") == "approved",
              f"box 3 + coordinates complete Well Site Location (got {statuses.get('Well Site Location')!r})")
        check(statuses.get("Approval to Stake") == "approved",
              "and Approval to Stake stays green")

        # --- reveal-persist: untick, save, re-tick --------------------------
        page.wait_for_selector(".sl-workspace")
        page.locator('[data-sl-field="wellsite_letter_loaded"]').uncheck()
        page.wait_for_timeout(200)
        check(page.locator('[data-sl-section="location"].hidden').count() == 1,
              "unticking box 3 hides the location again")
        save(page)
        check(rail_statuses(page).get("Well Site Location") != "approved",
              "which correctly REOPENS Well Site Location")
        page.wait_for_selector(".sl-workspace")
        page.locator('[data-sl-field="wellsite_letter_loaded"]').check()
        page.wait_for_timeout(200)
        check(page.locator('[data-sl-field="staked_x"]').input_value() == STAKED_X,
              "and the staked coordinates were NEVER cleared")
        save(page)
        check(rail_statuses(page).get("Well Site Location") == "approved",
              "re-ticking alone completes it again -- nothing had to be retyped")

        # ================= Card 4C -- Pre-Drilling GeoX ====================
        print("\nCard 4C -- Pre-Drilling GeoX Assessment")
        open_step(page, "Pre-Drilling GeoX Assessment")
        page.wait_for_selector("#resource-calculator-panel")
        check(page.locator(".sl-workspace").count() == 0 and page.locator(".la-workspace").count() == 0,
              "the step keeps its own page (no consolidated workspace)")
        duplicates = page.locator('#dynamic-fields [data-field^="pre_drill_piip_"]').count()
        check(duplicates == 0,
              f"no duplicate PIIP grid fighting the calculator (found {duplicates})")
        check(page.locator("#dynamic-fields").inner_html().strip() == "",
              "the calculator is the step's entire body")
        page.locator("#detail-shell").screenshot(path=str(out_dir / "06-geox-assessment.png"))

        check(not errors, f"no uncaught page errors ({errors})")
        browser.close()

    print()
    if failures:
        print(f"FAIL — {len(failures)} check(s) failed:")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("PASS — every check passed")
    print(f"screenshots: {out_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(BASE_DIR / "screenshots" / "card-4"))
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="segdash-e2e-4-"))
    server = start_server(scratch / "e2e.db")
    try:
        return run(Path(args.out), args.headed)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
