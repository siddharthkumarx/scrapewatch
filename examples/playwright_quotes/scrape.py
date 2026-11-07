"""quotes.toscrape.com/js/ (rendered by JavaScript) with Playwright, monitored by scrapewatch.

Run from this directory: ``python scrape.py [pages]`` (default 3 pages, 10 quotes each).
Playwright's listeners record every request the browser makes (documents, scripts,
stylesheets) with measured bytes; the waste cutter's route blocker aborts images, media,
fonts and known trackers and counts what that saved (an estimate). The run's report and JSON
summary go to output/quotes/.

``PW_CHROMIUM_PATH`` points at a browser binary when Playwright's own is not installed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import scrapewatch
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.waste import BlockPolicy, install_route_blocker

START = "https://quotes.toscrape.com/js/page/{page}/"
# Every quote on the page, read after the page's own script has rendered them.
READ_QUOTES = """() => [...document.querySelectorAll('.quote')].map(q => ({
  text: q.querySelector('.text')?.textContent ?? null,
  author: q.querySelector('.author')?.textContent ?? null,
  tags: [...q.querySelectorAll('.tag')].map(t => t.textContent),
}))"""


def main(pages: int) -> int:
    out_path = Path("output/quotes.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    launch = (
        {"executable_path": os.environ["PW_CHROMIUM_PATH"]}
        if "PW_CHROMIUM_PATH" in os.environ
        else {}
    )
    with (
        scrapewatch.watch("quotes", rules="rules.yml", config="scrapewatch.yml") as w,
        out_path.open("w", encoding="utf-8") as out,
        sync_playwright() as p,
    ):
        browser = p.chromium.launch(**launch)
        context = browser.new_context(user_agent="scrapewatch-example/0.1")
        sw_pw.attach(context, w, proxy="direct")
        install_route_blocker(context, w, BlockPolicy())  # images, media, fonts, trackers
        page = context.new_page()
        for n in range(1, pages + 1):
            page.goto(START.format(page=n))
            page.wait_for_selector(".quote")
            for item in page.evaluate(READ_QUOTES):
                rec = w.record_record(item)
                out.write(json.dumps(rec.data if rec else item, ensure_ascii=False) + "\n")
        context.close()  # browser time is counted up to here
        browser.close()
    waste = w.run.waste
    print(f"verdict: {w.verdict.status.value}")
    for reason in w.verdict.reasons:
        print(f"  - {reason}")
    print(f"records: {w.run.counts.records}, responses: {w.run.counts.responses}")
    print(
        f"waste cutter: {waste.blocked_requests} requests blocked, "
        f"about {waste.bytes_saved_estimate} bytes not downloaded (estimate)"
    )
    print(f"report: {w.report_path()}")
    return {"pass": 0, "warn": 1}.get(w.verdict.status.value, 2)


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
