"""httpx hooks (build item 16) against httpx.MockTransport: sync, async, redirects, streaming,
transport errors, fail-open, and the five-minute example from architecture 3.1."""

from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import scrapewatch
from scrapewatch.config import CostConfig, ProxyCost, Settings
from scrapewatch.cost.bytes import header_bytes_estimate
from scrapewatch.integrations import httpx as sw_httpx
from scrapewatch.models import BytesSource, GuardLabel, ResponseEvent, VerdictStatus
from scrapewatch.runtime import Watch

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PAGE = (FIXTURES / "guard" / "html" / "product.html").read_bytes()
GZ = gzip.compress(PAGE, mtime=0)


def _watch(**settings: Any) -> Watch:
    w = Watch(Settings(name="t", **settings))
    w.start()
    return w


def _spy(w: Watch, monkeypatch: pytest.MonkeyPatch) -> list[ResponseEvent]:
    """Collect every event the integration records."""
    events: list[ResponseEvent] = []
    real = w.record_response

    def spy(*args: Any, **kwargs: Any) -> ResponseEvent | None:
        ev = real(*args, **kwargs)
        if ev is not None:
            events.append(ev)
        return ev

    monkeypatch.setattr(w, "record_response", spy)
    return events


def _gzip_response(request: Any) -> Any:
    """A gzip body as a network stream (not pre-read), like a real transport."""
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8", "content-encoding": "gzip"},
        stream=httpx.ByteStream(GZ),
        request=request,
    )


def _expected_wire(response: Any) -> int:
    return len(GZ) + header_bytes_estimate(response.headers)


# --- sync ---------------------------------------------------------------------------------


def test_gzip_body_measured_compressed(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with httpx.Client(
        event_hooks=sw_httpx.hooks(w, proxy="datacenter"),
        transport=httpx.MockTransport(_gzip_response),
    ) as client:
        r = client.get("https://books.toscrape.com/catalogue/page-2.html")
    assert r.content == PAGE  # the scraper's body is untouched (decoded)
    assert len(events) == 1
    ev = events[0]
    assert ev.wire_bytes == _expected_wire(r)
    assert ev.wire_bytes < len(PAGE)  # compressed bytes, not the decoded length
    assert ev.body_bytes == len(PAGE)
    assert ev.bytes_source == BytesSource.MEASURED
    assert ev.status == 200
    assert ev.proxy == "datacenter"
    assert ev.integration == "httpx"
    assert ev.guard.label == GuardLabel.OK
    assert ev.elapsed_ms is not None
    assert ev.elapsed_ms >= 0
    assert ev.headers["content-encoding"] == "gzip"
    assert w.run.integration == "httpx"
    assert w.run.counts.monitor_errors == 0


def test_prebuilt_content_is_measured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport that returns content= (already read by httpx) is still counted once."""
    w = _watch()
    events = _spy(w, monkeypatch)

    def handler(request: Any) -> Any:
        return httpx.Response(200, content=GZ, headers={"content-encoding": "gzip"})

    with httpx.Client(
        event_hooks=sw_httpx.hooks(w), transport=httpx.MockTransport(handler)
    ) as client:
        r = client.get("https://books.toscrape.com/")
        r.read()
        r.close()
    # httpx read the body before the client saw it: Content-Length, labelled as such
    assert r.headers["content-length"] == str(len(GZ))
    assert [e.wire_bytes for e in events] == [_expected_wire(r)]
    assert events[0].bytes_source == BytesSource.CONTENT_LENGTH


def test_cost_from_measured_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    cost = CostConfig(proxies={"datacenter": ProxyCost(per_gb=0.8)})
    w = _watch(cost=cost)
    events = _spy(w, monkeypatch)
    with httpx.Client(
        event_hooks=sw_httpx.hooks(w, proxy="datacenter"),
        transport=httpx.MockTransport(_gzip_response),
    ) as client:
        client.get("https://books.toscrape.com/")
    assert events[0].cost == pytest.approx(events[0].wire_bytes / 1e9 * 0.8)


def _redirecting(request: Any) -> Any:
    path = request.url.path
    if path == "/old":
        return httpx.Response(301, headers={"location": "/mid"}, stream=httpx.ByteStream(b"moved"))
    if path == "/mid":
        return httpx.Response(302, headers={"location": "/new"}, stream=httpx.ByteStream(b"m2"))
    return _gzip_response(request)


def test_followed_redirect_chain_is_one_event(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with httpx.Client(
        event_hooks=sw_httpx.hooks(w),
        transport=httpx.MockTransport(_redirecting),
        follow_redirects=True,
    ) as client:
        r = client.get("https://books.toscrape.com/old")
    assert [h.status_code for h in r.history] == [301, 302]
    assert len(events) == 1
    ev = events[0]
    assert ev.url == "https://books.toscrape.com/old"
    assert ev.final_url == "https://books.toscrape.com/new"
    assert ev.status == 200
    bodies = (b"moved", b"m2")
    hops = sum(
        len(b) + header_bytes_estimate(h.headers) for b, h in zip(bodies, r.history, strict=False)
    )
    assert ev.wire_bytes == _expected_wire(r) + hops


def test_redirect_not_followed_is_its_own_event(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with httpx.Client(
        event_hooks=sw_httpx.hooks(w), transport=httpx.MockTransport(_redirecting)
    ) as client:
        r = client.get("https://books.toscrape.com/old")
    assert r.status_code == 301
    assert [(e.status, e.final_url) for e in events] == [(301, None)]
    assert events[0].wire_bytes == len(b"moved") + header_bytes_estimate(r.headers)


def test_streaming_recorded_when_consumed_or_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with httpx.Client(
        event_hooks=sw_httpx.hooks(w), transport=httpx.MockTransport(_gzip_response)
    ) as client:
        with client.stream("GET", "https://books.toscrape.com/") as r:
            assert events == []  # nothing yet: the body has not been read
            chunks = list(r.iter_bytes())
        assert b"".join(chunks) == PAGE
        with client.stream("GET", "https://books.toscrape.com/read") as r2:
            r2.read()
        with client.stream("GET", "https://books.toscrape.com/head-only") as r3:
            pass  # closed unread: recorded with the bytes read so far (none)
    assert len(events) == 3
    streamed, read, unread = events
    assert unread.wire_bytes == header_bytes_estimate(r3.headers)
    assert unread.bytes_source == BytesSource.MEASURED
    # an unread body is not evidence of an empty page
    assert [e.guard.label for e in events] == [GuardLabel.OK] * 3
    assert streamed.wire_bytes == _expected_wire(r)
    assert streamed.body_bytes == 0  # streamed without read(): no body for the guard
    assert read.wire_bytes == _expected_wire(r2)
    assert read.body_bytes == len(PAGE)


def test_transport_error_recorded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)

    def handler(request: Any) -> Any:
        raise httpx.ConnectError("connection refused", request=request)

    with (
        httpx.Client(
            event_hooks=sw_httpx.hooks(w, proxy="dc"), transport=httpx.MockTransport(handler)
        ) as client,
        pytest.raises(httpx.ConnectError) as info,
    ):
        client.get("https://books.toscrape.com/x?token=secret")
    ev = sw_httpx.record_error(w, info.value)
    assert ev is not None
    assert sw_httpx.record_error(w, info.value) is None  # the same request is not counted twice
    assert events == [ev]
    assert ev.status is None
    assert ev.guard.label == GuardLabel.ERROR
    assert ev.error is not None
    assert ev.error.type == "ConnectError"
    assert ev.url == "https://books.toscrape.com/x?token=[redacted]"
    assert ev.wire_bytes == 0
    assert ev.elapsed_ms is not None  # the request hook stamped the start
    assert ev.proxy is None or ev.proxy == w.settings.cost.default_proxy
    assert w.run.counts.by_label == {"error": 1}


def test_record_error_proxy_and_unknown_request(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    request = httpx.Request("GET", "https://books.toscrape.com/")
    ev = sw_httpx.record_error(w, httpx.ReadTimeout("slow"), request, proxy="residential")
    assert ev is not None
    assert ev.proxy == "residential"
    assert ev.elapsed_ms is None  # the hooks never saw this request
    assert sw_httpx.record_error(w, httpx.ReadTimeout("no request")) is None  # no URL known
    assert sw_httpx.record_error(w, ValueError("not httpx")) is None
    assert len(events) == 1
    assert w.run.counts.monitor_errors == 0


def test_error_recorded_while_streaming_is_not_recorded_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)

    class Slow(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield GZ[:10]
            raise httpx.ReadTimeout("slow")

    def handler(request: Any) -> Any:
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Slow())

    with (
        httpx.Client(
            event_hooks=sw_httpx.hooks(w), transport=httpx.MockTransport(handler)
        ) as client,
        client.stream("GET", "https://books.toscrape.com/") as r,
    ):
        try:
            for _ in r.iter_raw():
                pass
        except httpx.ReadTimeout as exc:
            sw_httpx.record_error(w, exc)
    assert len(events) == 1  # the close that followed did not add a second event
    assert events[0].error is not None


def test_stand_in_request_objects() -> None:
    """record_error accepts any object with url and method, even one without weak refs."""
    from types import SimpleNamespace

    w = _watch()
    request = SimpleNamespace(url="https://books.toscrape.com/", method="POST", extensions=None)
    ev = sw_httpx.record_error(w, OSError("down"), request)
    assert ev is not None
    assert ev.method == "POST"
    assert sw_httpx.record_error(w, OSError("down"), request) is not None  # no dedupe possible


def test_error_while_reading_body(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)

    class Broken(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield GZ[:100]
            raise httpx.ReadError("connection reset")

    def handler(request: Any) -> Any:
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Broken())

    with (
        httpx.Client(
            event_hooks=sw_httpx.hooks(w), transport=httpx.MockTransport(handler)
        ) as client,
        pytest.raises(httpx.ReadError) as info,
    ):
        client.get("https://books.toscrape.com/")
    assert sw_httpx.record_error(w, info.value) is None  # already recorded with its status
    assert len(events) == 1
    ev = events[0]
    assert ev.status == 200
    assert ev.error is not None
    assert ev.error.type == "ReadError"
    assert ev.wire_bytes >= 100


def test_request_options_and_retry_linking(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    url = "https://books.toscrape.com/catalogue/page-3.html"
    opts = {"scrapewatch": {"request_key": url, "proxy": "residential", "resource_type": "xhr"}}
    with httpx.Client(
        event_hooks=sw_httpx.hooks(w, proxy="datacenter"),
        transport=httpx.MockTransport(_gzip_response),
    ) as client:
        client.get(url, extensions=opts)
        client.get(url, extensions=opts)
        client.get(url, extensions={"scrapewatch": {"attempt": 3, "retry_times": "x"}})
    first, second, third = events
    assert (first.attempt, first.retry_of) == (1, None)
    assert (second.attempt, second.retry_of) == (2, first.event_id)
    assert (first.proxy, first.resource_type.value) == ("residential", "xhr")
    assert (third.attempt, third.proxy) == (3, "datacenter")
    assert w.run.counts.retries == 2


def test_resent_request_object_is_recorded_again(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with httpx.Client(
        event_hooks=sw_httpx.hooks(w), transport=httpx.MockTransport(_gzip_response)
    ) as client:
        request = client.build_request("GET", "https://books.toscrape.com/")
        client.send(request)
        client.send(request)
    assert len(events) == 2


def test_merged_with_user_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user hook that reads the body first does not hide the response."""
    w = _watch()
    events = _spy(w, monkeypatch)
    seen: list[int] = []

    def mine(response: Any) -> None:
        response.read()
        seen.append(response.status_code)

    sw = sw_httpx.hooks(w)
    hooks = {"request": sw["request"], "response": [mine, *sw["response"]]}
    with httpx.Client(event_hooks=hooks, transport=httpx.MockTransport(_gzip_response)) as client:
        r = client.get("https://books.toscrape.com/")
    assert seen == [200]
    assert [e.wire_bytes for e in events] == [_expected_wire(r)]


@pytest.mark.parametrize("target", ["header_bytes_estimate", "_wrap_sync", "_stamp"])
def test_failing_hook_does_not_break_the_request(
    target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(sw_httpx, target, boom)
    with httpx.Client(
        event_hooks=sw_httpx.hooks(w), transport=httpx.MockTransport(_gzip_response)
    ) as client:
        r = client.get("https://books.toscrape.com/")
    assert r.status_code == 200
    assert r.content == PAGE
    assert w.run.counts.monitor_errors == 1


def test_failing_record_error_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(sw_httpx, "_use_integration", boom)
    request = httpx.Request("GET", "https://books.toscrape.com/")
    assert sw_httpx.record_error(w, httpx.ConnectError("x"), request) is None
    monkeypatch.setattr(sw_httpx, "_claim", boom)
    monkeypatch.setattr(sw_httpx, "_use_integration", lambda w: None)
    assert sw_httpx.record_error(w, httpx.ConnectError("x"), request) is None
    assert w.run.counts.monitor_errors == 2


def test_integration_name_kept_when_set() -> None:
    w = Watch(Settings(name="t"), integration="mine")
    sw_httpx.hooks(w)
    assert w.run.integration == "mine"


# --- async --------------------------------------------------------------------------------


def _arun(fn: Callable[[], Any]) -> Any:
    return asyncio.run(fn())


def test_async_gzip_redirect_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)

    async def main() -> tuple[Any, Any, Any]:
        async with httpx.AsyncClient(
            event_hooks=sw_httpx.async_hooks(w, proxy="datacenter"),
            transport=httpx.MockTransport(_redirecting),
            follow_redirects=True,
        ) as client:
            r1 = await client.get("https://books.toscrape.com/new")
            r2 = await client.get("https://books.toscrape.com/old")
            async with client.stream("GET", "https://books.toscrape.com/s") as r3:
                async for _ in r3.aiter_bytes():
                    pass
            return r1, r2, r3

    r1, r2, r3 = _arun(main)
    assert r1.content == PAGE
    assert len(events) == 3
    plain, chain, streamed = events
    assert plain.wire_bytes == _expected_wire(r1)
    assert plain.bytes_source == BytesSource.MEASURED
    assert plain.body_bytes == len(PAGE)
    assert plain.proxy == "datacenter"
    assert plain.guard.label == GuardLabel.OK
    assert chain.final_url == "https://books.toscrape.com/new"
    assert chain.wire_bytes > _expected_wire(r2)
    assert streamed.wire_bytes == _expected_wire(r3)
    assert streamed.body_bytes == 0


def test_async_errors_and_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            yield GZ[:50]
            raise httpx.ReadError("reset")

    def handler(request: Any) -> Any:
        if request.url.path == "/down":
            raise httpx.ConnectTimeout("timed out", request=request)
        if request.url.path == "/broken":
            return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Broken())
        return _gzip_response(request)

    async def main() -> list[str]:
        errors: list[str] = []
        async with httpx.AsyncClient(
            event_hooks=sw_httpx.async_hooks(w), transport=httpx.MockTransport(handler)
        ) as client:
            for path in ("/down", "/broken"):
                try:
                    await client.get(f"https://books.toscrape.com{path}")
                except httpx.RequestError as exc:
                    errors.append(type(exc).__name__)
                    sw_httpx.record_error(w, exc)
            monkeypatch.setattr(sw_httpx, "_wrap_async", _raise)
            r = await client.get("https://books.toscrape.com/ok")
            assert r.content == PAGE
        return errors

    assert _arun(main) == ["ConnectTimeout", "ReadError"]
    assert [(e.status, e.error.type if e.error else None) for e in events] == [
        (None, "ConnectTimeout"),
        (200, "ReadError"),
    ]
    assert w.run.counts.monitor_errors == 1


def _raise(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("monitor bug")


def test_cancelled_read_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """KeyboardInterrupt and cancellation pass straight through, unrecorded."""
    w = _watch()
    events = _spy(w, monkeypatch)

    class Interrupt(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            raise KeyboardInterrupt

    class AInterrupt(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            chunks: tuple[bytes, ...] = ()
            for chunk in chunks:
                yield chunk
            raise asyncio.CancelledError

    with (
        httpx.Client(
            event_hooks=sw_httpx.hooks(w),
            transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Interrupt())),
        ) as client,
        pytest.raises(KeyboardInterrupt),
    ):
        client.get("https://books.toscrape.com/")

    async def main() -> None:
        async with httpx.AsyncClient(
            event_hooks=sw_httpx.async_hooks(w),
            transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=AInterrupt())),
        ) as client:
            await client.get("https://books.toscrape.com/")

    with pytest.raises(asyncio.CancelledError):
        _arun(main)
    assert events == []


# --- the five-minute path (architecture 3.1) ------------------------------------------------

RULES = """\
schema_version: 1
name: books
fields:
  title: {type: string, required: true, min_length: 1}
  price: {type: number, required: true, min: 0, clean: [price]}
"""


def _books_site(request: Any) -> Any:
    page = int(request.url.path.rsplit("-", 1)[1].split(".")[0])
    items = "".join(
        f'<article class="product_pod"><h3>Book {page}-{i}</h3>'
        f'<p class="price_color">£{10 + i}.{page:02d}</p></article>'
        for i in range(20)
    )
    html = f"<html><head><title>Books page {page}</title></head><body>{items}</body></html>"
    return httpx.Response(
        200,
        headers={"content-type": "text/html", "content-encoding": "gzip"},
        stream=httpx.ByteStream(gzip.compress(html.encode(), mtime=0)),
    )


def parse(text: str) -> Iterator[dict[str, str]]:
    for block in text.split('<article class="product_pod">')[1:]:
        title = block.split("<h3>")[1].split("</h3>")[0]
        price = block.split('"price_color">')[1].split("</p>")[0]
        yield {"title": title, "price": price}


def test_five_minute_example(tmp_path: Path) -> None:
    (tmp_path / "rules.yml").write_text(RULES, "utf-8")
    transport = httpx.MockTransport(_books_site)

    # The example from docs/architecture.md 3.1, with a mock transport added.
    with scrapewatch.watch("books", rules="rules.yml") as w:  # noqa: SIM117 - as documented
        with httpx.Client(
            event_hooks=sw_httpx.hooks(w, proxy="datacenter"), transport=transport
        ) as client:
            for page in range(1, 51):
                r = client.get(f"https://books.toscrape.com/catalogue/page-{page}.html")
                for item in parse(r.text):
                    w.record_record(item)
    print(w.verdict.status, w.report_path())

    run = w.run
    assert w.verdict.status == VerdictStatus.PASS
    assert run.integration == "httpx"
    assert run.counts.responses == 50
    assert run.counts.by_label == {"ok": 50}
    assert run.counts.by_bytes_source == {"measured": 50}
    assert run.counts.records == run.counts.usable_records == 1000
    # the price normalizer parses the price and fills the currency (GBP): two fixes a record
    assert run.counts.fixes_by_normalizer.get("price") == 2000
    assert run.counts.monitor_errors == 0
    report = w.report_path()
    assert report is not None
    assert report.is_file()
    summary = json.loads(report.with_name(report.stem + ".json").read_text("utf-8"))
    assert summary["counts"]["wire_bytes"] == run.counts.wire_bytes > 0


def test_example_draft_runs_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """examples/httpx_books/scrape.py against a mock books site (the live run is item 23)."""
    import importlib.util
    import shutil

    example = Path(__file__).resolve().parents[2] / "examples" / "httpx_books"
    for name in ("rules.yml", "scrapewatch.yml"):
        shutil.copy(example / name, tmp_path / name)
    spec = importlib.util.spec_from_file_location("httpx_books_scrape", example / "scrape.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def books(request: Any) -> Any:
        page = request.url.path.rsplit("-", 1)[1].split(".")[0]
        pods = "".join(
            f'<article class="product_pod"><h3><a href="book-{page}-{i}/index.html" '
            f'title="Book {page} &amp; {i}">Book</a></h3>'
            f'<div class="product_price"><p class="price_color">£{10 + i}.50</p></div></article>'
            for i in range(20)
        )
        html = f"<html><head><title>All products</title></head><body>{pods}</body></html>"
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    real_client = httpx.Client

    def client(**kwargs: Any) -> Any:
        return real_client(transport=httpx.MockTransport(books), **kwargs)

    monkeypatch.setattr(module.httpx, "Client", client)
    assert module.main(2) == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "books.jsonl").read_text("utf-8").splitlines()
    ]
    assert len(rows) == 40
    assert rows[0] == {
        "title": "Book 1 & 0",
        "price": 10.5,
        "currency": "GBP",
        "url": "https://books.toscrape.com/catalogue/book-1-0/index.html",
    }
