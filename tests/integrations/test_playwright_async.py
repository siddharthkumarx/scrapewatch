"""Playwright async hooks (build item 18): the same site through the async API."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from scrapewatch.config import Settings
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.models import BytesSource, GuardLabel, ResponseEvent
from scrapewatch.runtime import Watch

from . import pw_site

async_api = pytest.importorskip("playwright.async_api")

pytestmark = pytest.mark.extras


def _run(w: Watch, base: str, path: str, *, route_abort: str | None = None) -> None:
    async def main() -> None:
        async with async_api.async_playwright() as p:
            try:
                browser = await p.chromium.launch(**pw_site.launch_options())
            except Exception as exc:
                if os.environ.get("CI"):
                    raise
                pytest.skip(f"Chromium not available: {exc}")
            context = await browser.new_context()
            await sw_pw.attach_async(context, w, proxy="pool")
            page = await context.new_page()
            if route_abort:
                await page.route(route_abort, lambda route: route.abort())
            await page.goto(base + path)
            await page.wait_for_load_state("networkidle")
            await context.close()
            await browser.close()

    asyncio.run(main())


def test_async_page_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
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
    with pw_site.serve() as base:
        _run(w, base, "/page.html")
        _run(w, base, "/broken.html", route_abort="**/img/broken.png")
    got = {e.url.removeprefix(base): e for e in events if not e.url.endswith("favicon.ico")}
    assert {p: e.resource_type.value for p, e in got.items()} == {
        "/page.html": "document",
        "/img/a.png": "image",
        "/img/b.png": "image",
        "/img/gz.png": "image",
        "/font.woff2": "font",
        "/broken.html": "document",
        "/img/broken.png": "image",
    }
    assert got["/page.html"].bytes_source == BytesSource.MEASURED
    assert got["/page.html"].body_bytes == len(pw_site.PAGE)
    assert got["/page.html"].cookie_names == ["sid"]
    assert got["/img/gz.png"].wire_bytes < len(pw_site.PADDED)
    assert got["/img/broken.png"].guard.label == GuardLabel.ERROR
    assert all(e.proxy == "pool" for e in events)
    assert w.meter.browser_minutes > 0
    assert w.run.counts.monitor_errors == 0


def test_async_listener_errors_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    w = Watch(Settings(name="t"))
    w.start()

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(sw_pw, "_finished_async", boom)
    with pw_site.serve() as base:
        _run(w, base, "/page.html")
    assert w.run.counts.monitor_errors >= 5


def test_async_attach_never_raises() -> None:
    w = Watch(Settings(name="t"))
    asyncio.run(sw_pw.attach_async(object(), w))
    assert w.run.counts.monitor_errors == 1
