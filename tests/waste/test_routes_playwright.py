"""Route blocker (build item 21) in a real browser against the local test site."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from integrations import pw_site
from scrapewatch.config import Settings
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.models import ResponseEvent
from scrapewatch.runtime import Watch
from scrapewatch.waste import BlockPolicy, install_route_blocker
from scrapewatch.waste.routes import ESTIMATE_BYTES

sync_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.extras

KEEP = "*/img/keep.png"


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


def _watch() -> Watch:
    w = Watch(Settings(name="t"))
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


def _paths(events: list[ResponseEvent], base: str) -> set[str]:
    return {(e.final_url or e.url).removeprefix(base) for e in events}


def _load(browser: Any, w: Watch, url: str, policy: BlockPolicy) -> None:
    context = browser.new_context()
    sw_pw.attach(context, w)
    install_route_blocker(context, w, policy)
    page = context.new_page()
    page.goto(url)
    page.wait_for_load_state("networkidle")
    page.wait_for_function("document.images.length === 3 && document.images[2].complete")
    context.close()


def test_blocks_images_font_and_tracker_allowlist_wins(
    browser: Any, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    _load(browser, w, site + "/waste.html", BlockPolicy(allow=[KEEP]))

    # 2 images + 1 font + 1 tracker script aborted; the allowlisted image loaded
    assert _paths(events, site) == {"/waste.html", "/img/keep.png"}
    assert all(e.status == 200 and e.error is None for e in events)  # blocks are not errors
    waste = w.meter.waste()
    assert waste.blocked_requests == 4
    assert waste.bytes_saved_estimate == (
        2 * ESTIMATE_BYTES["image"] + ESTIMATE_BYTES["font"] + ESTIMATE_BYTES["script"]
    )
    assert w.run.counts.monitor_errors == 0
    run = w.finish()
    assert run.waste.blocked_requests == 4
    assert run.waste.bytes_saved_estimate == waste.bytes_saved_estimate
    assert run.counts.responses == 2


def test_no_resource_types_blocks_only_trackers(
    browser: Any, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    _load(browser, w, site + "/waste.html", BlockPolicy(resource_types=[]))

    assert _paths(events, site) == {
        "/waste.html",
        "/img/a.png",
        "/img/b.png",
        "/img/keep.png",
        "/font.woff2",
    }
    waste = w.meter.waste()
    assert waste.blocked_requests == 1
    assert waste.bytes_saved_estimate == ESTIMATE_BYTES["script"]
    # sizes measured in this run now drive the image estimate
    image_sizes = [e.wire_bytes for e in events if e.resource_type.value == "image"]
    assert sw_pw.median_size(w, "image") == sorted(image_sizes)[1]


def test_estimate_uses_sizes_measured_earlier_in_the_run(browser: Any, site: str) -> None:
    w = _watch()
    _load(browser, w, site + "/waste.html", BlockPolicy(resource_types=[]))
    measured = sw_pw.median_size(w, "image")
    assert measured is not None
    font = sw_pw.median_size(w, "font")
    assert font is not None
    before = w.meter.waste().bytes_saved_estimate
    _load(browser, w, site + "/waste.html", BlockPolicy())  # keep.png is blocked too now
    waste = w.meter.waste()
    assert waste.blocked_requests == 1 + 5
    assert waste.bytes_saved_estimate - before == 3 * measured + font + ESTIMATE_BYTES["script"]


def test_users_own_routes_still_run(browser: Any, site: str) -> None:
    w = _watch()
    context = browser.new_context()
    seen: list[str] = []

    def mine(route: Any) -> None:
        seen.append(route.request.url.removeprefix(site))
        route.continue_()

    context.route("**/*", mine)  # registered first, so it runs after the blocker falls back
    install_route_blocker(context, w)
    page = context.new_page()
    page.goto(site + "/waste.html")
    page.wait_for_load_state("networkidle")
    context.close()
    assert "/waste.html" in seen
    assert "/img/a.png" not in seen  # blocked before reaching it
    assert w.meter.waste().blocked_requests == 5
