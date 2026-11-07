"""Crawlee for Python (build item 20, D20): HTTP crawlers through the httpx hooks, the
Playwright crawler through the Playwright listeners."""

from __future__ import annotations

import asyncio
import gzip
import http.server
import os
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from scrapewatch.config import Settings
from scrapewatch.integrations import crawlee as sw_crawlee
from scrapewatch.models import ResponseEvent
from scrapewatch.runtime import Watch

from . import pw_site

pytest.importorskip("crawlee")

pytestmark = pytest.mark.extras

PAGE = (
    b"<html><head><title>Books</title></head><body>"
    + b'<article class="product_pod"><h3>A book</h3><p class="price">\xc2\xa312.50</p></article>'
    * 20
    + b"</body></html>"
)
GZ = gzip.compress(PAGE, mtime=0)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(GZ)))
        self.end_headers()
        self.wfile.write(GZ)


@pytest.fixture(scope="module")
def base() -> Iterator[str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _watch(monkeypatch: pytest.MonkeyPatch) -> tuple[Watch, list[ResponseEvent]]:
    w = Watch(Settings(name="t"))
    w.start()
    events: list[ResponseEvent] = []
    real = w.record_response

    def spy(*args: Any, **kwargs: Any) -> ResponseEvent | None:
        ev = real(*args, **kwargs)
        if ev is not None:
            events.append(ev)
        return ev

    monkeypatch.setattr(w, "record_response", spy)
    return w, events


def test_beautifulsoup_crawler_measured(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from crawlee.crawlers import BeautifulSoupCrawler
    from crawlee.storage_clients import MemoryStorageClient

    w, events = _watch(monkeypatch)
    seen: list[str] = []
    crawler = BeautifulSoupCrawler(
        http_client=sw_crawlee.http_client(w, proxy="datacenter"),
        storage_client=MemoryStorageClient(),
    )
    sw_crawlee.attach(crawler, w)

    @crawler.router.default_handler
    async def handler(context: Any) -> None:
        for pod in context.soup.select("article.product_pod"):
            w.record_record({"title": pod.h3.text, "price": pod.select_one(".price").text})
        seen.append(context.request.url)

    asyncio.run(crawler.run([base + "/a", base + "/b"]))
    assert len(seen) == 2
    assert sorted(e.url for e in events) == [base + "/a", base + "/b"]
    for e in events:
        assert e.bytes_source.value == "measured"
        assert len(GZ) < e.wire_bytes < len(PAGE)  # compressed body plus headers
        assert e.body_bytes == len(PAGE)
        assert e.proxy == "datacenter"
        assert e.integration == "crawlee"
        assert e.guard.label.value == "ok"
    assert w.run.counts.records == 40
    assert w.run.counts.monitor_errors == 0


def test_http_crawler_without_our_client_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from crawlee.crawlers import BeautifulSoupCrawler

    w, _ = _watch(monkeypatch)
    with pytest.raises(TypeError, match="http_client"):
        sw_crawlee.attach(BeautifulSoupCrawler(), w)
    with pytest.raises(TypeError, match="unsupported"):
        sw_crawlee.attach(object(), w)


def test_user_hooks_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    w, _ = _watch(monkeypatch)

    async def mine(request: Any) -> None:
        pass

    client = sw_crawlee.http_client(w, event_hooks={"request": [mine]})
    hooks = client._async_client_kwargs["event_hooks"]
    assert hooks["request"][0] is mine
    assert len(hooks["request"]) == 2
    assert len(hooks["response"]) == 1


def test_playwright_crawler(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        from crawlee.crawlers import PlaywrightCrawler
    except ImportError as exc:
        pytest.skip(f"Crawlee's Playwright crawler is not installed: {exc}")
    from crawlee.storage_clients import MemoryStorageClient

    w, events = _watch(monkeypatch)
    options = pw_site.launch_options()
    if hasattr(os, "geteuid") and os.geteuid() == 0:  # Chromium's sandbox cannot run as root
        options["chromium_sandbox"] = False
    crawler = PlaywrightCrawler(
        browser_launch_options=options or None,
        storage_client=MemoryStorageClient(),
    )
    sw_crawlee.attach(crawler, w, proxy="browser")

    @crawler.router.default_handler
    async def handler(context: Any) -> None:
        await context.page.wait_for_load_state("networkidle")

    with pw_site.serve() as site:
        try:
            asyncio.run(crawler.run([site + "/page.html"]))
        except Exception as exc:  # no browser here; CI installs one and must not skip
            if os.environ.get("CI"):
                raise
            pytest.skip(f"Chromium not available: {exc}")
        paths = {e.url.removeprefix(site): e.resource_type.value for e in events}
    paths.pop("/favicon.ico", None)
    assert paths == {
        "/page.html": "document",
        "/img/a.png": "image",
        "/img/b.png": "image",
        "/img/gz.png": "image",
        "/font.woff2": "font",
    }
    assert all(e.proxy == "browser" and e.integration == "crawlee" for e in events)
    assert w.meter.browser_minutes > 0
