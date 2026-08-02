#!/usr/bin/env python
"""End-to-end check for Card 2B -- the consolidated Lead Assessment page.

Boots the Flask app against a SCRATCH SQLite database (never the real
pipeline_tracker.db), drives a real browser through the workflow the card
describes, and writes screenshots.

The journey, in order:
  1. create a lead and open its detail page;
  2. click a Lead Assessment rail row -- ANY of the four opens the same page;
  3. fill Section 1 (thickness) and Section 2 (area + GRV);
  4. WATCH the PIIP results and plots appear WITHOUT clicking anything -- there
     is no Calculate button on this page any more;
  5. tick "Polygons and surfaces are placed in the shared folder";
  6. press Save Updates ONCE;
  7. assert all four Lead Assessment rail rows read Approved (green) and the
     board card's four Lead Assessment dots are Completed.

Screenshots (--out, default ./screenshots/card-2b):
  01-full-light.png       the finished page, light theme
  02-full-dark.png        the same page, dark theme
  03-validation-error.png an inline validation error (P10 <= P90)
  04-gas-only.png         a dry-gas scenario: Gas block alone
  05-condensate.png       a condensate scenario: Gas + Liquid

Run:  .venv/bin/python scripts/e2e_card_2b.py
"""
from __future__ import annotations

import argparse
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
PORT = 8022  # 8020 dev, 8021 the front-end harness -- this must not collide.
ROOT_URL = f"http://{HOST}:{PORT}"
BOOT_TIMEOUT_S = 30

LEAD_NAME = "WWWW-44"
LEAD_STEPS = ["Area Definition", "Thickness Estimation", "GRV Inputs", "Resource Assessment"]

# Section 1 + Section 2, the card's own worked example.
INPUTS = {
    "twt_reservoir_ms": "1500",
    "twt_formation_ms": "1800",
    "reservoir_thickness_ft": "200",
    "formation_thickness_ft": "500",
    "p90_area_km2": "12.60",
    "p10_area_km2": "17.30",
    "grv_p90_thousand_acre_ft": "12.60",
    "grv_p10_thousand_acre_ft": "17.30",
    "top_formation_tvdss_ft": "-6500",
}


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
    """Create the lead through the real API and return its project_id."""
    request = urllib.request.Request(
        f"{ROOT_URL}/api/projects",
        data=f'{{"project_name": "{LEAD_NAME}"}}'.encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        import json
        return json.loads(response.read())["project_id"]


def field(page, key):
    return page.locator(f'[data-la-field="{key}"]')


def fill_sections(page):
    for key, value in INPUTS.items():
        box = field(page, key)
        box.fill(value)
        box.dispatch_event("input")


def rail_statuses(page):
    """{step name: status slug} from the rail rows' own status- classes."""
    out = {}
    for row in page.locator(".component-item[data-task-id]").all():
        name = row.locator("b").inner_text().strip()
        classes = row.get_attribute("class") or ""
        slug = next((c[len("status-"):] for c in classes.split() if c.startswith("status-")), "")
        out[name] = slug
    return out


def board_tracked_items(project_id):
    import json
    with urllib.request.urlopen(f"{ROOT_URL}/api/projects?pipeline_filter=prospect", timeout=10) as response:
        rows = json.loads(response.read())
    row = next(r for r in rows if r["project_id"] == project_id)
    return {item["label"]: item["status"] for item in row["tracked_items"]}


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
        # Open the lead from the Segment Maturation board.
        page.locator(f'.lead-card:has-text("{LEAD_NAME}"), .project-card:has-text("{LEAD_NAME}")').first.click()
        page.wait_for_selector("#detail-shell:not(.hidden)")

        # ANY of the four rail rows opens the same page -- start from the third
        # to prove it is not just "the first step's form".
        page.locator('.component-item:has-text("GRV Inputs")').first.click()
        page.wait_for_selector(".la-workspace")
        check(page.locator('[data-la-section]').count() == 4, "all four numbered sections render")
        check(page.locator("#ra-calculate").count() == 0, "there is no Calculate button")
        check(page.locator("#ra-apply").count() == 0, "there is no Apply to Lead button")

        # --- a validation error, before the good values -----------------------
        area_p10 = field(page, "p10_area_km2")
        field(page, "p90_area_km2").fill("12.60")
        field(page, "p90_area_km2").dispatch_event("input")
        area_p10.fill("12.60")
        area_p10.dispatch_event("input")
        page.wait_for_selector('.la-field-error[data-error-for="p10_area_km2"].is-shown')
        error_text = page.locator('.la-field-error[data-error-for="p10_area_km2"]').inner_text()
        check(error_text == "Area P10 must be greater than Area P90.",
              f"the inline ordering error reads correctly ({error_text!r})")
        page.locator('[data-la-section="volume"]').screenshot(path=str(out_dir / "03-validation-error.png"))

        # --- fill everything and WAIT for the auto-run ------------------------
        fill_sections(page)
        page.wait_for_selector('.la-result-gas .ra-plot img', timeout=30000)
        gas_mean = page.locator(".la-result-gas .la-result-box").nth(1).inner_text()
        check(gas_mean not in ("", "—"), f"PIIP auto-rendered with no click (Gas mean {gas_mean})")
        check(page.locator(".la-result-gas .ra-plot img").count() == 1, "the gas exceedance plot rendered")

        # Gas-only (dry gas) vs condensate.
        page.locator('[data-la-section="piip"]').screenshot(path=str(out_dir / "04-gas-only.png"))
        check(page.locator("[data-la-result]").count() == 1, "a dry-gas scenario shows Gas alone")
        page.locator('input[name="la-scenario"][value="condensate_field_a"]').check()
        page.wait_for_selector('.la-result-liquid .ra-plot img', timeout=30000)
        check(page.locator("[data-la-result]").count() == 2,
              "a condensate scenario adds the Liquid block")
        page.locator('[data-la-section="piip"]').screenshot(path=str(out_dir / "05-condensate.png"))

        # --- tick the confirmation and save ONCE ------------------------------
        page.locator('[data-la-field="polygons_surfaces_loaded"]').check()
        folder_path = page.locator("#la-folder-path").inner_text()
        check("Polygons__Surfaces" in folder_path, f"the folder row resolves ({folder_path})")

        # The shell ELEMENT rather than full_page: the app header/tab bar are
        # sticky, so a stitched full-page capture paints them over the middle of
        # the page and an element shot lets them cover its first rows. Unstick
        # them for the duration of the two captures instead.
        unstick(page, True)
        shell = page.locator("#detail-shell")
        shell.screenshot(path=str(out_dir / "01-full-light.png"))
        set_theme(page, "dark")
        page.wait_for_timeout(250)
        shell.screenshot(path=str(out_dir / "02-full-dark.png"))
        set_theme(page, "light")
        page.wait_for_timeout(250)
        unstick(page, False)

        page.locator("#save-component").click()
        page.wait_for_selector("#app-message", timeout=15000)
        page.wait_for_timeout(1500)

        statuses = rail_statuses(page)
        for step in LEAD_STEPS:
            check(statuses.get(step) == "approved",
                  f"rail row {step!r} is green (got {statuses.get(step)!r})")
        items = board_tracked_items(project_id)
        for step in LEAD_STEPS:
            check(items.get(step) == "Completed",
                  f"board tracked item {step!r} is Completed (got {items.get(step)!r})")

        # --- the OTHER stages keep the generic per-step form -------------------
        page.locator(".rail-stage-head:has-text('RISK ANALYSIS')").click()
        page.locator('.component-item:has-text("Reservoir CoS")').first.click()
        page.wait_for_selector("#dynamic-fields .repeatable-field")
        check(page.locator(".la-workspace").count() == 0,
              "a Risk Analysis step still opens the generic form")
        check(page.locator('#dynamic-fields [data-field="reservoir_slides_loaded"]').count() == 1,
              "and renders its own schema fields")
        check(page.locator("#component-title").is_visible(),
              "with the per-step title restored")

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
    parser.add_argument("--out", default=str(BASE_DIR / "screenshots" / "card-2b"))
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="segdash-e2e-2b-"))
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
