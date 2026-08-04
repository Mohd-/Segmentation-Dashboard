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
          const selectors = [
            '.bpe-filter-strip label', '.bpe-kpi', '.bpe-well-card',
            '.bpe-detail-head', '.bpe-detail-form', '.bpe-summary',
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
          ['body', 'main', '#tab-bp', '#bpe-detail-view', '.bpe-detail-head',
           '.bpe-detail-grid', '.bpe-detail-nav', '.bpe-detail-form', '.bpe-well-summary',
           '.bpe-gate-depth', '.bpe-gate-logging', '.bpe-check'].forEach((selector) => {
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


def open_dashboard(page, url, year):
    page.goto(url, wait_until="networkidle")
    page.locator('.tabs button[data-tab="bp"]').click()
    page.locator("#bp-year-filter").select_option(str(year))
    page.wait_for_selector(".bpe-well-card", state="visible")
    page.wait_for_timeout(250)


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
        browser = playwright.chromium.launch(channel="msedge", headless=True)

        desktop = browser.new_page(viewport={"width": 1440, "height": 1000})
        desktop.on("pageerror", lambda error: browser_messages.append("pageerror: " + str(error)))
        desktop.on("console", lambda message: browser_messages.append(
            "console: " + message.text) if message.type == "error" else None)
        open_dashboard(desktop, args.url, args.year)
        capture(desktop, output_dir, "bpe-dashboard-desktop.png", results)
        desktop.locator('.bpe-tracking-item[data-step="business-plan-gate"]').first.click()
        desktop.wait_for_selector(".bpe-detail-form", state="visible")
        capture(desktop, output_dir, "bpe-gate-desktop.png", results)
        desktop.locator('.bpe-nav-item[data-detail-slug="flowback-results"]').click()
        desktop.wait_for_selector(".bpe-flow-stage", state="visible")
        capture(desktop, output_dir, "bpe-flowback-desktop.png", results)
        desktop.close()

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("pageerror", lambda error: browser_messages.append("pageerror: " + str(error)))
        mobile.on("console", lambda message: browser_messages.append(
            "console: " + message.text) if message.type == "error" else None)
        open_dashboard(mobile, args.url, args.year)
        capture(mobile, output_dir, "bpe-dashboard-mobile.png", results)
        mobile.locator('.bpe-tracking-item[data-step="business-plan-gate"]').first.click()
        mobile.wait_for_selector(".bpe-detail-form", state="visible")
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
