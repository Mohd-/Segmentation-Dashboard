"""Capture and sanity-check Business Plan Execution desktop/mobile views."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def diagnostics(page):
    return page.evaluate("""
        () => {
          const root = document.documentElement;
          // The restyled markup (Cards R2-R4): the band is the maturation
          // band (.lf-trigger / .kpi-tile), the board is the lead board
          // (.lead-card), and the detail page is the maturation detail shell
          // (.component-rail / .component-editor / .ls-card).
          const selectors = [
            '#bpe-filter-row .lf-trigger', '#bpe-kpis .kpi-tile', '#bp-pipeline .lead-card',
            '.component-editor', '.bpe-detail-form', '.ls-card',
            '.bpe-flow-grid', '.bpe-flow-stage'
          ];
          const clipped = selectors.flatMap((selector) =>
            Array.from(document.querySelectorAll(selector)).filter((element) => {
              const style = getComputedStyle(element);
              return style.display !== 'none' && element.scrollWidth > element.clientWidth + 2 &&
                style.overflowX !== 'auto';
            }).map((element) => selector + ':' + element.scrollWidth + '/' + element.clientWidth)
          );
          const outside = Array.from(document.body.querySelectorAll('*')).filter((element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return style.display !== 'none' && rect.width > 0 && rect.right > innerWidth + 2;
          }).slice(0, 30).map((element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return {
              element: element.tagName.toLowerCase() + (element.id ? '#' + element.id : '') +
                (element.className && typeof element.className === 'string' ? '.' + element.className.trim().replace(/\\s+/g, '.') : ''),
              left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width),
              minWidth: style.minWidth, display: style.display,
              gridTemplateColumns: style.gridTemplateColumns
            };
          });
          const layout = {};
          ['body', 'main', '#tab-bp', '#bpe-filter-row', '#bpe-kpis', '#bp-pipeline',
           '#bpe-detail-view', '.detail-shell', '.component-rail', '.bpe-detail-form',
           '.summary-panel .ls-card', '.bpe-gate-depth', '.bpe-gate-logging',
           '.bpe-detail-form .check-label'].forEach((selector) => {
            const element = document.querySelector(selector);
            if (!element) return;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            layout[selector] = {
              left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width),
              clientWidth: element.clientWidth, scrollWidth: element.scrollWidth,
              minWidth: style.minWidth, maxWidth: style.maxWidth, overflowX: style.overflowX,
              gridTemplateColumns: style.gridTemplateColumns
            };
          });
          return {
            viewportWidth: innerWidth,
            documentWidth: root.scrollWidth,
            horizontalOverflow: root.scrollWidth > innerWidth + 2,
            clipped: clipped,
            outside: outside,
            layout: layout
          };
        }
    """)


def select_year(page, year):
    """Pick the Business Plan year through the VISIBLE control.

    #bp-year-filter is the hidden state store now (Card R2): the year is chosen
    from the band's trigger menu, which is also the only path a user has.
    """
    page.locator('.lead-filter[data-bp-filter="year"] .lf-trigger').click()
    page.locator(
        '.lead-filter[data-bp-filter="year"] .lf-menu .lf-option[data-value="%s"]' % year
    ).click()


def open_dashboard(page, url, year):
    page.goto(url, wait_until="networkidle")
    page.locator('.tabs button[data-tab="bp"]').click()
    page.wait_for_selector("#bpe-filter-row .lf-trigger", state="visible")
    select_year(page, year)
    page.wait_for_selector("#bp-pipeline .lead-card", state="visible")
    page.wait_for_timeout(250)


def click_rail_item(page, slug, stage):
    """Open a rail step, expanding its stage first — the rail is a one-open
    accordion, so items in the two folded groups are hidden."""
    item = page.locator('.component-item[data-detail-slug="%s"]' % slug)
    if not item.is_visible():
        page.locator('.rail-stage-head[data-stage="%s"]' % stage).click()
    item.click()


def open_gate(page):
    """Open the Business Plan Gate step of the first well on the board.

    A card is ONE target now (Card R2) and opens the first step the well is
    still waiting on, which is not necessarily the gate -- so the gate is
    reached from the detail rail, where every step is always listed.
    """
    page.locator("#bp-pipeline .lead-card").first.click()
    page.wait_for_selector(".bpe-detail-form", state="visible")
    click_rail_item(page, "business-plan-gate", "pre_drilling")
    page.wait_for_selector(".bpe-detail-form .radio-group", state="visible")


def capture(page, output_dir, name, results):
    path = output_dir / name
    page.screenshot(path=str(path), full_page=True)
    results[name] = diagnostics(page)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8022/")
    parser.add_argument("--year", type=int, default=2027)
    parser.add_argument("--output", default="screenshots/business-plan-execution")
    args = parser.parse_args()

    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    browser_messages = []

    with sync_playwright() as playwright:
        # Edge on Windows (original capture environment), else any installed
        # Playwright browser so captures work cross-platform.
        try:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
        except Exception:
            try:
                browser = playwright.firefox.launch(headless=True)
            except Exception:
                browser = playwright.chromium.launch(headless=True)

        desktop = browser.new_page(viewport={"width": 1440, "height": 1000})
        desktop.on("pageerror", lambda error: browser_messages.append("pageerror: " + str(error)))
        desktop.on("console", lambda message: browser_messages.append(
            "console: " + message.text) if message.type == "error" else None)
        open_dashboard(desktop, args.url, args.year)
        capture(desktop, output_dir, "bpe-dashboard-desktop.png", results)
        open_gate(desktop)
        capture(desktop, output_dir, "bpe-gate-desktop.png", results)
        click_rail_item(desktop, "flowback-results", "post_testing")
        desktop.wait_for_selector(".bpe-flow-stage", state="visible")
        capture(desktop, output_dir, "bpe-flowback-desktop.png", results)
        desktop.close()

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("pageerror", lambda error: browser_messages.append("pageerror: " + str(error)))
        mobile.on("console", lambda message: browser_messages.append(
            "console: " + message.text) if message.type == "error" else None)
        open_dashboard(mobile, args.url, args.year)
        capture(mobile, output_dir, "bpe-dashboard-mobile.png", results)
        open_gate(mobile)
        capture(mobile, output_dir, "bpe-gate-mobile.png", results)
        mobile.close()
        browser.close()

    print(json.dumps({
        "screenshots": [str(output_dir / name) for name in results],
        "diagnostics": results,
        "browser_messages": browser_messages,
    }, indent=2))
    return 1 if browser_messages or any(item["horizontalOverflow"] for item in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
