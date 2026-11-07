"""books.toscrape.com with httpx, monitored by scrapewatch (draft; finalised in build item 23).

Run from this directory: ``python scrape.py [pages]`` (default 5 pages, 20 books each).
The output goes to data/books.jsonl only when the run passes (or warns); otherwise the last
good file stays live and the report says why.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from html import unescape
from urllib.parse import urljoin

import httpx

import scrapewatch
from scrapewatch.integrations import httpx as sw_httpx

START = "https://books.toscrape.com/catalogue/page-{page}.html"
ITEM_RE = re.compile(
    r'<h3><a href="(?P<href>[^"]+)" title="(?P<title>[^"]+)">.*?'
    r'<p class="price_color">(?P<price>[^<]+)</p>',
    re.S,
)


def parse(html: str, page_url: str) -> Iterator[dict[str, str]]:
    """Title, price (as text: the price normalizer cleans it) and URL of each book."""
    for m in ITEM_RE.finditer(html):
        yield {
            "title": unescape(m["title"]),
            "price": m["price"],
            "url": urljoin(page_url, m["href"]),
        }


def main(pages: int) -> int:
    gate = scrapewatch.PublishGate("data/books.jsonl")
    # Exits in reverse order: the client closes, the staging file is complete, then the run
    # finishes with its verdict.
    with (
        scrapewatch.watch("books", rules="rules.yml", config="scrapewatch.yml") as w,
        gate.stage(w.run.run_id) as staging,
        staging.open("w", encoding="utf-8") as out,
        httpx.Client(
            event_hooks=sw_httpx.hooks(w, proxy="direct"),
            headers={"user-agent": "scrapewatch-example/0.1"},
            timeout=30,
        ) as client,
    ):
        for page in range(1, pages + 1):
            url = START.format(page=page)
            try:
                r = client.get(url)
            except httpx.RequestError as exc:
                sw_httpx.record_error(w, exc)
                continue
            for item in parse(r.text, url):
                rec = w.record_record(item)
                out.write(json.dumps(rec.data if rec else item) + "\n")
    promoted = gate.promote(w.run.run_id, w.verdict)
    print(f"verdict: {w.verdict.status.value}; promoted: {promoted}")
    for reason in w.verdict.reasons:
        print(f"  - {reason}")
    print(f"report: {w.report_path()}")
    return {"pass": 0, "warn": 1}.get(w.verdict.status.value, 2)


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
