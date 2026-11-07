"""Playwright sync hooks (build item 18) against a local static site."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from scrapewatch.config import BrowserCost, CostConfig, Settings
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.models import BytesSource, GuardLabel, ResponseEvent
from scrapewatch.runtime import Watch

from . import pw_site

sync_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.extras


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(**pw_site.launch_options())
        except Exception as exc:  # no browser here; CI installs one and must not skip
            if os.environ.get("CI"):
                raise
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    with pw_site.serve() as base:
        yield base


def _watch(**settings: Any) -> Watch:
    w = Watch(Settings(name="t", **settings))
    w.start()
    return w


def _spy(w: Watch, monkeypatch: pytest.MonkeyPatch) -> list[ResponseEvent]:
    events: list[ResponseEvent] = []
    real = w.record_response

    def spy(*args: Any, **kwargs: Any) -> ResponseEvent | None:
        ev = real(*args, **kwargs)
        if ev is not None:
            events.append(ev)
        return ev

    monkeypatch.setattr(w, "record_response", spy)
    return events


def _by_path(events: list[ResponseEvent], base: str) -> dict[str, ResponseEvent]:
    out = {}
    for e in events:
        path = (e.final_url or e.url).removeprefix(base)
        if path != "/favicon.ico":  # some Chromium builds ask for it
            out[path] = e
    return out


def test_page_with_images_and_font(
    browser: Any, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch(cost=CostConfig(browser=BrowserCost(per_minute=0.5)))
    events = _spy(w, monkeypatch)
    context = browser.new_context()
    sw_pw.attach(context, w, proxy="browser-pool")
    page = context.new_page()
    page.goto(site + "/page.html")
    page.wait_for_load_state("networkidle")
    context.close()

    got = _by_path(events, site)
    assert set(got) == {"/page.html", "/img/a.png", "/img/b.png", "/img/gz.png", "/font.woff2"}
    types = {p: e.resource_type.value for p, e in got.items()}
    assert types == {
        "/page.html": "document",
        "/img/a.png": "image",
        "/img/b.png": "image",
        "/img/gz.png": "image",
        "/font.woff2": "font",
    }
    for e in got.values():
        assert e.bytes_source == BytesSource.MEASURED
        assert e.status == 200
        assert e.proxy == "browser-pool"
        assert e.integration == "playwright"
        assert e.guard.label == GuardLabel.OK
    doc = got["/page.html"]
    assert len(pw_site.PAGE) < doc.wire_bytes < len(pw_site.PAGE) + 1000  # body + headers
    assert doc.body_bytes == len(pw_site.PAGE)
    assert doc.cookie_names == ["sid"]  # all headers are read for documents
    gz = got["/img/gz.png"]
    assert len(pw_site.GZ_IMAGE) < gz.wire_bytes < len(pw_site.PADDED)  # encoded size
    plain = got["/img/a.png"]
    assert len(pw_site.PADDED) < plain.wire_bytes < len(pw_site.PADDED) + 1000
    assert got["/font.woff2"].wire_bytes > len(pw_site.FONT)
    assert doc.elapsed_ms is not None
    # browser time from attach to close, priced per minute
    assert w.meter.browser_minutes > 0
    assert w.run.counts.monitor_errors == 0


def test_failed_request_is_an_error_event(
    browser: Any, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    page = browser.new_page()
    sw_pw.attach(page, w)
    page.route("**/img/broken.png", lambda route: route.abort())
    page.goto(site + "/broken.html")
    page.wait_for_load_state("networkidle")
    page.close()
    got = _by_path(events, site)
    broken = got["/img/broken.png"]
    assert broken.status is None
    assert broken.guard.label == GuardLabel.ERROR
    assert broken.error is not None
    assert broken.error.type == "RequestFailedError"
    assert broken.resource_type.value == "image"
    assert got["/broken.html"].guard.label == GuardLabel.OK


def test_redirect_chain(browser: Any, site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    page = browser.new_page()
    sw_pw.attach(page, w)
    page.goto(site + "/old")
    page.close()
    docs = [e for e in events if e.resource_type.value == "document"]
    assert [(e.status, e.url, e.final_url) for e in docs] == [
        (302, site + "/old", None),
        (200, site + "/old", site + "/page.html"),
    ]
    assert docs[1].guard.label == GuardLabel.OK  # an in-site redirect is fine


def test_listener_errors_are_counted(
    browser: Any, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(sw_pw, "_finished_sync", boom)
    page = browser.new_page()
    sw_pw.attach(page, w)
    response = page.goto(site + "/page.html")
    page.wait_for_load_state("networkidle")
    assert response is not None
    assert response.ok
    assert "Attic" in page.content()
    page.close()
    assert w.run.counts.monitor_errors >= 5  # one per finished request, all swallowed


def test_attach_never_raises() -> None:
    w = _watch()
    sw_pw.attach(object(), w)  # not a context or page
    assert w.run.counts.monitor_errors == 1
