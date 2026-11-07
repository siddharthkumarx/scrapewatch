"""Scrapy extension, downloader middleware and pipeline (build item 19).

A real crawl runs in a subprocess (Twisted's reactor cannot restart within one pytest
process) through the full downloader middleware chain against a local gzip server; it is the
evidence for architecture 11.3 (the middleware at 900 sees the compressed body). The
components are also unit-tested in-process with ``scrapy.utils.test.get_crawler``.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scrapewatch.config import Settings
from scrapewatch.integrations.scrapy import (
    ScrapewatchDownloaderMiddleware,
    ScrapewatchExtension,
    ScrapewatchPipeline,
)
from scrapewatch.integrations.scrapy.extension import ATTR
from scrapewatch.runtime import Watch

pytest.importorskip("scrapy")

import scrapy

pytestmark = pytest.mark.extras

CRAWL = Path(__file__).with_name("scrapy_crawl.py")


def _crawl(out: Path, *args: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(CRAWL), str(out), *args],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    result: dict[str, Any] = json.loads((out / "result.json").read_text("utf-8"))
    return result


@pytest.fixture(scope="module")
def crawl(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _crawl(tmp_path_factory.mktemp("crawl"))


def _events(result: dict[str, Any], path: str) -> list[dict[str, Any]]:
    url = result["base"] + path
    return [e for e in result["events"] if (e["final_url"] or e["url"]) == url]


# --- the full chain ---------------------------------------------------------------------------


def test_middleware_at_900_measures_compressed_bytes(crawl: dict[str, Any]) -> None:
    """Architecture 11.3: at 900 the body is still compressed (HttpCompression is at 590)."""
    url = crawl["base"] + "/page/1"
    body_at_901, headers_at_901 = crawl["seen_at_901"][url]
    assert body_at_901 == crawl["sent"]["/page/1"]  # exactly what the server sent (gzip)
    assert crawl["decoded"][url] > body_at_901  # the spider got the decoded page
    (ev,) = _events(crawl, "/page/1")
    assert ev["wire_bytes"] == body_at_901 + headers_at_901
    assert ev["bytes_source"] == "measured"
    assert ev["body_bytes"] == crawl["decoded"][url]  # the guard's window decode
    assert ev["guard"]["label"] == "ok"
    assert ev["proxy"] == "datacenter"
    assert ev["integration"] == "scrapy"
    assert ev["elapsed_ms"] is not None


def test_retries_are_linked(crawl: dict[str, Any]) -> None:
    first, second = _events(crawl, "/flaky")
    assert (first["status"], first["attempt"], first["retry_of"]) == (503, 1, None)
    assert first["guard"]["label"] == "error_page"
    assert (second["status"], second["attempt"], second["retry_of"]) == (
        200,
        2,
        first["event_id"],
    )
    assert crawl["run"]["counts"]["retries"] == 1


def test_redirect_hops(crawl: dict[str, Any]) -> None:
    hop = [e for e in crawl["events"] if e["url"].endswith("/old") and e["status"] == 301]
    final = _events(crawl, "/page/2")
    assert len(hop) == 1
    assert hop[0]["final_url"] is None
    assert [(e["url"], e["status"]) for e in final] == [(crawl["base"] + "/old", 200)]


def test_brotli_window_decoded(crawl: dict[str, Any]) -> None:
    if not crawl["brotli"]:
        pytest.skip("brotli not installed")
    (ev,) = _events(crawl, "/br")
    assert ev["wire_bytes"] < ev["body_bytes"]
    assert ev["guard"]["label"] == "ok"


def test_items_cleaned_and_stats_exported(crawl: dict[str, Any]) -> None:
    items = crawl["items"]
    assert len(items) == 80
    assert items[0]["title"] == "Book 1-0"  # whitespace fix written back
    assert isinstance(items[0]["price"], float)  # "£10.99" parsed
    assert items[0]["currency"] == "GBP"  # filled by the price normalizer (dict item)
    stats = crawl["stats"]
    run = crawl["run"]
    assert stats["scrapewatch/verdict"] == run["verdict"]["status"]
    assert stats["scrapewatch/responses"] == run["counts"]["responses"] == 6
    assert stats["scrapewatch/usable_records"] == 80
    assert stats["scrapewatch/label/error_page"] == 1
    assert stats["scrapewatch/cost"] > 0
    assert Path(stats["scrapewatch/report"]).is_file()
    assert run["counts"]["monitor_errors"] == 0


def test_middleware_failure_does_not_drop_responses(tmp_path: Path) -> None:
    result = _crawl(tmp_path, "--broken")
    assert len(result["items"]) == 80  # every page still reached the spider
    assert result["run"]["counts"]["responses"] == 0
    assert result["stats"]["scrapewatch/monitor_errors"] == 6


# --- components in-process --------------------------------------------------------------------


class _Spider(scrapy.Spider):  # type: ignore[misc, unused-ignore]
    name = "unit"


def _crawler(**settings: Any) -> Any:
    from scrapy.utils.test import get_crawler

    return get_crawler(_Spider, settings)


def _opened(**settings: Any) -> tuple[Any, ScrapewatchExtension, Any]:
    crawler = _crawler(**settings)
    ext = ScrapewatchExtension.from_crawler(crawler)
    spider = _Spider()
    ext.spider_opened(spider)
    return crawler, ext, spider


def test_extension_lifecycle_and_settings(tmp_path: Path) -> None:
    crawler, ext, spider = _opened(SCRAPEWATCH_NAME="named")
    w = getattr(crawler, ATTR)
    assert isinstance(w, Watch)
    assert w.run.name == "named"
    assert w.run.integration == "scrapy"
    crawler.stats = _Stats()
    ext.spider_closed(spider, "shutdown")
    assert w.run.status.value == "crashed"
    assert crawler.stats.values["scrapewatch/verdict"] == "hold"


def test_extension_disabled_and_bad_config(tmp_path: Path) -> None:
    crawler = _crawler(SCRAPEWATCH_ENABLED=False)
    ext = ScrapewatchExtension.from_crawler(crawler)
    assert ext.enabled is False
    bad = tmp_path / "bad.yml"
    bad.write_text("history: [", "utf-8")
    crawler, ext, spider = _opened(SCRAPEWATCH_CONFIG=str(bad))
    assert getattr(crawler, ATTR, None) is None  # monitoring stays off, the crawl goes on
    ext.spider_closed(spider, "finished")  # nothing to finish


def test_stats_export_failure_is_counted() -> None:
    crawler, ext, spider = _opened()
    crawler.stats = None  # set_value fails
    ext.spider_closed(spider, "finished")
    assert getattr(crawler, ATTR).run.counts.monitor_errors == 1


class _Stats:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def set_value(self, key: str, value: Any) -> None:
        self.values[key] = value


def _response(url: str, body: bytes, **kw: Any) -> Any:
    from scrapy.http import HtmlResponse

    return HtmlResponse(url, body=body, **kw)


def test_middleware_units() -> None:
    from scrapy import Request
    from scrapy.exceptions import IgnoreRequest

    crawler, _, _ = _opened(SCRAPEWATCH_PROXY="direct-pool")
    w = getattr(crawler, ATTR)
    mw = ScrapewatchDownloaderMiddleware.from_crawler(crawler)
    page = b"<html><head><title>x</title></head><body>" + b"<p>text here</p>" * 300 + b"</body>"
    req = Request("https://books.toscrape.com/a")
    mw.process_request(req)
    key = req.meta["scrapewatch_request_key"]
    mw.process_request(req)
    assert req.meta["scrapewatch_request_key"] == key  # stable across retries
    gz = gzip.compress(page)
    resp = _response(req.url, gz, headers={"Content-Encoding": "gzip"}, request=req)
    assert mw.process_response(req, resp) is resp  # unchanged
    cached = _response(req.url, page, flags=["cached"], request=req)
    mw.process_response(req, cached)
    head = Request("https://books.toscrape.com/h", method="HEAD")
    mw.process_response(head, _response(head.url, b"", request=head))
    mw.process_exception(req, IgnoreRequest("robots"))  # not recorded
    mw.process_exception(req, ConnectionRefusedError("refused"))
    events = w.store.events(w.run.run_id) if w.flush() >= 0 else []
    by = [(e.status, e.wire_bytes > 0, e.body_bytes, e.proxy, e.guard.label.value) for e in events]
    assert by[0] == (200, True, len(page), "direct-pool", "ok")
    assert by[1][:3] == (200, False, len(page))  # cached: no bytes crossed the wire
    assert by[2][0] == 200  # HEAD: no body judged
    assert by[3][0] is None
    assert by[3][4] == "error"
    assert len(events) == 4


def test_middleware_without_watch_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from scrapy import Request

    crawler = _crawler()
    mw = ScrapewatchDownloaderMiddleware.from_crawler(crawler)
    req = Request("https://books.toscrape.com/a")
    resp = _response(req.url, b"<html></html>", request=req)
    mw.process_request(req)
    assert mw.process_response(req, resp) is resp
    mw.process_exception(req, OSError("x"))
    crawler, _, _ = _opened()
    w = getattr(crawler, ATTR)
    mw = ScrapewatchDownloaderMiddleware.from_crawler(crawler)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(mw, "_urls", boom)
    monkeypatch.setattr(type(req), "meta", property(boom))
    mw.process_request(req)
    assert mw.process_response(req, resp) is resp
    mw.process_exception(req, OSError("x"))
    assert w.run.counts.monitor_errors == 3


def test_pipeline_items(tmp_path: Path) -> None:
    from scrapy.exceptions import DropItem

    rules = tmp_path / "rules.yml"
    rules.write_text(
        "schema_version: 1\nname: unit\nkey: [title]\nfields:\n"
        "  title: {type: string, required: true, clean: [whitespace]}\n"
        "  price: {type: number, required: true, min: 0, clean: [price], severity: hold}\n",
        "utf-8",
    )

    class Book(scrapy.Item):  # type: ignore[misc, unused-ignore]
        title = scrapy.Field()
        price = scrapy.Field()

    crawler, _, spider = _opened(SCRAPEWATCH_RULES=str(rules), SCRAPEWATCH_DROP_UNUSABLE=True)
    pipe = ScrapewatchPipeline.from_crawler(crawler)
    item = Book(title=" Sharp Objects ", price="£47.82")
    out = pipe.process_item(item, spider)
    assert out is item
    assert dict(item) == {"title": "Sharp Objects", "price": 47.82}  # no undeclared currency
    with pytest.raises(DropItem, match="unusable"):
        pipe.process_item({"title": "x", "price": "-3"}, spider)
    with pytest.raises(DropItem, match="duplicate or schema"):
        pipe.process_item({"title": "Sharp Objects", "price": "£47.82"}, spider)  # a repeat

    keep = ScrapewatchPipeline.from_crawler(_opened(SCRAPEWATCH_RULES=str(rules))[0])
    bad = {"title": "x", "price": "-3"}
    assert keep.process_item(bad) is bad  # monitoring only: never dropped by default


def test_pipeline_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {"title": "x"}
    assert ScrapewatchPipeline.from_crawler(_crawler()).process_item(item) is item  # no watch
    crawler, _, _ = _opened()
    w = getattr(crawler, ATTR)
    pipe = ScrapewatchPipeline.from_crawler(crawler)
    monkeypatch.setattr(w, "record_record", lambda data: None)
    assert pipe.process_item(item) is item
    monkeypatch.setattr(w, "record_record", lambda data: 1 / 0)
    assert pipe.process_item(item) is item
    assert w.run.counts.monitor_errors == 1


def test_settings_are_programmatic_too() -> None:
    w = Watch(Settings(name="x"), integration="scrapy")
    assert w.run.integration == "scrapy"
