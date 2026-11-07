"""A real Scrapy crawl through the full downloader middleware chain (run in a subprocess).

Usage: python scrapy_crawl.py OUTDIR [--broken]. Writes OUTDIR/result.json. A local server
serves gzip pages, a 503-then-200 page, a redirect and (with brotli) a br page.
"""

from __future__ import annotations

import gzip
import http.server
import json
import sys
import threading
from pathlib import Path
from typing import Any

import scrapy
from scrapy import signals
from scrapy.crawler import CrawlerProcess

from scrapewatch.cost.bytes import header_bytes_estimate
from scrapewatch.history.store import open_store
from scrapewatch.integrations.scrapy import ScrapewatchDownloaderMiddleware
from scrapewatch.integrations.scrapy.extension import current_watch
from scrapewatch.integrations.scrapy.middleware import _headers

OUT = Path(sys.argv[1])
BROKEN = "--broken" in sys.argv
SENT: dict[str, int] = {}
SEEN_AT_901: dict[str, list[int]] = {}
DECODED: dict[str, int] = {}
ITEMS: list[dict[str, Any]] = []
BASE = ""
HITS: dict[str, int] = {}

try:
    import brotli
except ImportError:  # pragma: no cover
    brotli = None


def page(n: int) -> bytes:
    books = "".join(
        f'<article class="product_pod"><h3>Book {n}-{i}</h3><p class="price">£{10 + i}.99</p>'
        f"<p>{'A fine book about many things. ' * 4}</p></article>"
        for i in range(20)
    )
    return f"<html><head><title>Books {n}</title></head><body>{books}</body></html>".encode()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, encoding: str | None, **extra: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra.items():
            self.send_header(k.replace("_", "-"), v)
        self.end_headers()
        self.wfile.write(body)
        SENT[self.path] = len(body)

    def do_GET(self) -> None:
        HITS[self.path] = HITS.get(self.path, 0) + 1
        if self.path.startswith("/page/"):
            self._send(200, gzip.compress(page(int(self.path.rsplit("/", 1)[1]))), "gzip")
        elif self.path == "/flaky":
            if HITS[self.path] == 1:
                self._send(503, gzip.compress(b"<html><body>busy</body></html>"), "gzip")
            else:
                self._send(200, gzip.compress(page(9)), "gzip")
        elif self.path == "/old":
            self._send(301, b"", None, Location="/page/2")
        elif self.path == "/br" and brotli is not None:
            self._send(200, brotli.compress(page(7)), "br")
        else:
            self._send(404, b"missing", None)


class Probe:
    """At 901 (before scrapewatch at 900): what crossed the wire, for the assertions."""

    def process_response(self, request: Any, response: Any, spider: Any = None) -> Any:
        pairs, _ = _headers(response.headers)
        SEEN_AT_901.setdefault(request.url, []).append(len(response.body))
        SEEN_AT_901[request.url].append(header_bytes_estimate(pairs))
        return response


class Books(scrapy.Spider):  # type: ignore[misc, unused-ignore]
    name = "books"

    def start_requests(self) -> Any:  # Scrapy before 2.13
        for path in ("/page/1", "/flaky", "/old") + (("/br",) if brotli else ()):
            yield scrapy.Request(BASE + path, meta={"proxy_label": "datacenter"})

    async def start(self) -> Any:  # Scrapy 2.13 and later
        for request in self.start_requests():
            yield request

    def parse(self, response: Any) -> Any:
        DECODED[response.url] = len(response.body)
        for block in response.text.split('<article class="product_pod">')[1:]:
            title = block.split("<h3>")[1].split("</h3>")[0]
            price = block.split('<p class="price">')[1].split("</p>")[0]
            yield {"title": f"  {title} ", "price": price, "url": response.url}


def keep_item(item: Any, **kwargs: Any) -> None:
    """Signal receivers are weak references in Scrapy: a module function stays alive."""
    ITEMS.append(dict(item))


def main() -> None:
    global BASE
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{srv.server_address[1]}"

    (OUT / "rules.yml").write_text(
        "schema_version: 1\nname: books\nfields:\n"
        "  title: {type: string, required: true, clean: [whitespace]}\n"
        "  price: {type: number, required: true, min: 0, clean: [price]}\n"
        "  url: {type: url, required: true}\n",
        "utf-8",
    )
    (OUT / "scrapewatch.yml").write_text(
        f"history:\n  path: {OUT / '.scrapewatch'}\n"
        "cost:\n  proxies:\n    datacenter: {per_gb: 0.8}\n",
        "utf-8",
    )
    if BROKEN:

        def boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("monitor bug")

        ScrapewatchDownloaderMiddleware._record = boom  # type: ignore[method-assign]

    process = CrawlerProcess(
        {
            "EXTENSIONS": {"scrapewatch.integrations.scrapy.ScrapewatchExtension": 0},
            "DOWNLOADER_MIDDLEWARES": {
                "scrapewatch.integrations.scrapy.ScrapewatchDownloaderMiddleware": 900,
                Probe: 901,
            },
            "ITEM_PIPELINES": {"scrapewatch.integrations.scrapy.ScrapewatchPipeline": 1000},
            "SCRAPEWATCH_RULES": str(OUT / "rules.yml"),
            "SCRAPEWATCH_CONFIG": str(OUT / "scrapewatch.yml"),
            "SCRAPEWATCH_PROXY_META_KEY": "proxy_label",
            "ROBOTSTXT_OBEY": False,
            "TELNETCONSOLE_ENABLED": False,
            "LOG_LEVEL": "WARNING",
            "RETRY_TIMES": 2,
        }
    )
    crawler = process.create_crawler(Books)
    crawler.signals.connect(keep_item, signal=signals.item_scraped)
    process.crawl(crawler)
    process.start()
    srv.shutdown()

    w = current_watch(crawler)
    assert w is not None
    store = open_store(OUT / ".scrapewatch")
    result = {
        "base": BASE,
        "sent": SENT,
        "seen_at_901": SEEN_AT_901,
        "decoded": DECODED,
        "items": ITEMS,
        "stats": {
            k: v for k, v in crawler.stats.get_stats().items() if k.startswith("scrapewatch/")
        },
        "run": json.loads(w.run.model_dump_json()),
        "events": [json.loads(e.model_dump_json()) for e in store.events(w.run.run_id)],
        "brotli": brotli is not None,
    }
    (OUT / "result.json").write_text(json.dumps(result, indent=1, default=str), "utf-8")


if __name__ == "__main__":
    main()
