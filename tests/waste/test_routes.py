"""Route blocker rules, estimates and failure handling (build item 21), without a browser."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from scrapewatch.config import Settings
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.runtime import Watch
from scrapewatch.waste import headers as waste_headers
from scrapewatch.waste import routes
from scrapewatch.waste.routes import (
    ESTIMATE_BYTES,
    BlockPolicy,
    estimate_bytes,
    install_route_blocker,
    install_route_blocker_async,
    is_tracker,
    should_block,
    tracker_hosts,
)


class FakeRequest:
    def __init__(self, url: str, resource_type: str, *, navigation: bool = False) -> None:
        self.url = url
        self.resource_type = resource_type
        self.navigation = navigation
        self.method = "GET"
        self.redirected_from = None
        self.failure = "net::ERR_BLOCKED_BY_CLIENT"

    def is_navigation_request(self) -> bool:
        return self.navigation


class FakeRoute:
    def __init__(self, request: FakeRequest, *, fail_abort: bool = False) -> None:
        self.request = request
        self.fail_abort = fail_abort
        self.aborted: str | None = None
        self.fell_back = False

    def abort(self, error_code: str) -> None:
        if self.fail_abort:
            raise RuntimeError("route already handled")
        self.aborted = error_code

    def fallback(self) -> None:
        self.fell_back = True


class FakeAsyncRoute:
    def __init__(self, request: FakeRequest, *, fail_abort: bool = False) -> None:
        self.sync = FakeRoute(request, fail_abort=fail_abort)
        self.request = request

    @property
    def aborted(self) -> str | None:
        return self.sync.aborted

    @property
    def fell_back(self) -> bool:
        return self.sync.fell_back

    async def abort(self, error_code: str) -> None:
        self.sync.abort(error_code)

    async def fallback(self) -> None:
        self.sync.fallback()


class FakeContext:
    def __init__(self) -> None:
        self.handler: Any = None

    def route(self, glob: str, handler: Any) -> None:
        assert glob == "**/*"
        self.handler = handler


class FakeAsyncContext(FakeContext):
    async def route(self, glob: str, handler: Any) -> None:  # type: ignore[override]
        FakeContext.route(self, glob, handler)


def _watch() -> Watch:
    w = Watch(Settings(name="t"))
    w.start()
    return w


def test_policy_defaults_and_validation() -> None:
    p = BlockPolicy()
    assert p.resource_types == ["image", "media", "font"]
    assert p.block_trackers
    assert BlockPolicy(resource_types=["script", "stylesheet", "xhr", "other"]).resource_types
    with pytest.raises(ValidationError, match="document is never blocked"):
        BlockPolicy(resource_types=["document"])
    with pytest.raises(ValidationError, match="'video'"):
        BlockPolicy(resource_types=["video"])
    with pytest.raises(ValidationError):
        BlockPolicy(block_images=True)  # type: ignore[call-arg]


def test_tracker_list() -> None:
    hosts = tracker_hosts()
    assert 25 <= len(hosts) <= 40
    assert all(h == h.lower() and " " not in h and not h.startswith("#") for h in hosts)
    assert is_tracker("static.hotjar.com")
    assert is_tracker("STATIC.HOTJAR.COM.")
    assert is_tracker("www.google-analytics.com")  # subdomain of a listed host
    assert is_tracker("stats.g.doubleclick.net")
    assert not is_tracker("hotjar.com")  # only static.hotjar.com is listed
    assert not is_tracker("books.toscrape.com")
    assert not is_tracker("com")
    assert not is_tracker("notdoubleclick.net")


@pytest.mark.parametrize(
    ("url", "rtype", "navigation", "policy", "blocked"),
    [
        ("https://books.toscrape.com/", "document", True, BlockPolicy(), False),
        # a navigation is never blocked, even to a tracker or with a matching glob
        ("https://static.hotjar.com/x", "document", True, BlockPolicy(), False),
        ("https://s.test/a.png", "image", False, BlockPolicy(), True),
        ("https://s.test/a.woff2", "font", False, BlockPolicy(), True),
        ("https://s.test/a.mp4", "media", False, BlockPolicy(), True),
        ("https://s.test/app.js", "script", False, BlockPolicy(), False),
        ("https://static.hotjar.com/c/x.js", "script", False, BlockPolicy(), True),
        (
            "https://static.hotjar.com/c/x.js",
            "script",
            False,
            BlockPolicy(block_trackers=False),
            False,
        ),
        ("https://s.test/a.png", "image", False, BlockPolicy(resource_types=[]), False),
        ("https://s.test/a.png", "image", False, BlockPolicy(allow=["*/a.png"]), False),
        # allow wins over a tracker host and over block globs
        (
            "https://static.hotjar.com/c/x.js",
            "script",
            False,
            BlockPolicy(allow=["https://static.hotjar.com/*"]),
            False,
        ),
        (
            "https://s.test/ads/x.js",
            "script",
            False,
            BlockPolicy(block_url_globs=["*/ads/*"]),
            True,
        ),
        (
            "https://s.test/ads/x.js",
            "script",
            False,
            BlockPolicy(block_url_globs=["*/ads/*"], allow=["*/ads/x.js"]),
            False,
        ),
        ("not a url", "xhr", False, BlockPolicy(), False),
        ("http://[::1/x", "xhr", False, BlockPolicy(), False),  # unparsable host
    ],
)
def test_should_block(
    url: str, rtype: str, navigation: bool, policy: BlockPolicy, blocked: bool
) -> None:
    assert should_block(policy, url, rtype, navigation=navigation) is blocked


def test_estimates_table_then_run_median() -> None:
    w = _watch()
    assert estimate_bytes(w, "image") == 45 * 1024
    assert estimate_bytes(w, "font") == 60 * 1024
    assert estimate_bytes(w, "media") == 500 * 1024
    assert estimate_bytes(w, "script") == 80 * 1024
    assert estimate_bytes(w, "stylesheet") == 30 * 1024
    assert estimate_bytes(w, "xhr") == 0  # no table value
    for size in (100, 300, 200, 0):  # 0 (cached, unknown) is not a sample
        sw_pw.note_size(w, "image", size)
    assert estimate_bytes(w, "image") == 200
    assert estimate_bytes(_watch(), "image") == ESTIMATE_BYTES["image"]  # per run
    for size in range(sw_pw.SIZE_SAMPLES + 50):
        sw_pw.note_size(w, "font", 1000 + size)
    assert estimate_bytes(w, "font") == 1000 + 50 + (sw_pw.SIZE_SAMPLES - 1) // 2


def test_sync_handler_blocks_and_counts() -> None:
    w = _watch()
    ctx = FakeContext()
    install_route_blocker(ctx, w)
    img = FakeRoute(FakeRequest("https://s.test/a.png", "image"))
    doc = FakeRoute(FakeRequest("https://s.test/", "document", navigation=True))
    ctx.handler(img)
    ctx.handler(doc)
    assert img.aborted == "blockedbyclient"
    assert not img.fell_back
    assert img.request in sw_pw.ABORTED
    assert doc.aborted is None
    assert doc.fell_back
    waste = w.meter.waste()
    assert (waste.blocked_requests, waste.bytes_saved_estimate) == (1, ESTIMATE_BYTES["image"])
    # the integration does not record the abort's requestfailed as an error
    rec = sw_pw._Recorder(w, None)
    assert rec.failed(img.request) is None
    assert w.run.counts.responses == 0
    run = w.finish()
    assert run.waste.blocked_requests == 1
    assert run.waste.bytes_saved_estimate == ESTIMATE_BYTES["image"]


def test_async_handler_blocks_and_counts() -> None:
    w = _watch()
    ctx = FakeAsyncContext()
    asyncio.run(install_route_blocker_async(ctx, w, BlockPolicy(resource_types=["font"])))
    font = FakeAsyncRoute(FakeRequest("https://s.test/a.woff2", "font"))
    img = FakeAsyncRoute(FakeRequest("https://s.test/a.png", "image"))
    asyncio.run(ctx.handler(font))
    asyncio.run(ctx.handler(img))
    assert font.aborted == "blockedbyclient"
    assert img.fell_back
    assert w.meter.waste().blocked_requests == 1


@pytest.mark.parametrize("is_async", [False, True])
def test_failed_abort_lets_the_request_through(is_async: bool) -> None:
    w = _watch()
    request = FakeRequest("https://s.test/a.png", "image")
    if is_async:
        actx = FakeAsyncContext()
        asyncio.run(install_route_blocker_async(actx, w))
        aroute = FakeAsyncRoute(request, fail_abort=True)
        asyncio.run(actx.handler(aroute))
        fell_back = aroute.fell_back
    else:
        ctx = FakeContext()
        install_route_blocker(ctx, w)
        route = FakeRoute(request, fail_abort=True)
        ctx.handler(route)
        fell_back = route.fell_back
    assert fell_back
    assert request not in sw_pw.ABORTED
    assert w.meter.waste().blocked_requests == 0
    assert w.run.counts.monitor_errors == 1


@pytest.mark.parametrize("is_async", [False, True])
def test_handler_errors_fail_open(is_async: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(routes, "should_block", boom)
    request = FakeRequest("https://s.test/a.png", "image")
    if is_async:
        actx = FakeAsyncContext()
        asyncio.run(install_route_blocker_async(actx, w))
        aroute = FakeAsyncRoute(request)
        asyncio.run(actx.handler(aroute))
        assert aroute.fell_back
    else:
        ctx = FakeContext()
        install_route_blocker(ctx, w)
        route = FakeRoute(request)
        ctx.handler(route)
        assert route.fell_back
    assert w.run.counts.monitor_errors == 1


class BrokenFallback(FakeRoute):
    def fallback(self) -> None:
        raise RuntimeError("page closed")


class BrokenAsyncFallback(FakeAsyncRoute):
    async def fallback(self) -> None:
        raise RuntimeError("page closed")


def test_fallback_errors_are_counted() -> None:
    w = _watch()
    ctx = FakeContext()
    install_route_blocker(ctx, w)
    ctx.handler(BrokenFallback(FakeRequest("https://s.test/app.js", "script")))
    actx = FakeAsyncContext()
    asyncio.run(install_route_blocker_async(actx, w))
    asyncio.run(actx.handler(BrokenAsyncFallback(FakeRequest("https://s.test/app.js", "script"))))
    assert w.run.counts.monitor_errors == 2


def test_install_never_raises() -> None:
    w = _watch()
    install_route_blocker(object(), w)
    asyncio.run(install_route_blocker_async(object(), w))
    assert w.run.counts.monitor_errors == 2


def test_add_blocked_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    w.add_blocked(2, bytes_saved_estimate=10)
    assert w.meter.waste().blocked_requests == 2

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("meter bug")

    monkeypatch.setattr(w.meter, "add_blocked", boom)
    w.add_blocked()
    assert w.run.counts.monitor_errors == 1


def test_compression_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(waste_headers, "_brotli_available", lambda: True)
    assert waste_headers.compression_headers() == {"Accept-Encoding": "gzip, deflate, br"}
    monkeypatch.setattr(waste_headers, "_brotli_available", lambda: False)
    assert waste_headers.compression_headers() == {"Accept-Encoding": "gzip, deflate"}


def test_brotli_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    found: set[str] = set()
    monkeypatch.setattr(
        "importlib.util.find_spec", lambda name: object() if name in found else None
    )
    assert not waste_headers._brotli_available()
    found.add("brotlicffi")
    assert waste_headers._brotli_available()


def test_compression_headers_decode_with_httpx() -> None:
    httpx = pytest.importorskip("httpx")
    import gzip

    body = b"<html>" + b"x" * 5000 + b"</html>"

    def handler(request: Any) -> Any:
        assert "gzip" in request.headers["accept-encoding"]
        return httpx.Response(
            200, content=gzip.compress(body), headers={"content-encoding": "gzip"}
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler), headers=waste_headers.compression_headers()
    ) as client:
        assert client.get("https://books.toscrape.com/").content == body
