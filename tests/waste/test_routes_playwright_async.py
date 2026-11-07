"""Route blocker through Playwright's async API (build item 21)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from integrations import pw_site
from scrapewatch.config import Settings
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.models import ResponseEvent
from scrapewatch.runtime import Watch
from scrapewatch.waste import BlockPolicy, install_route_blocker_async

async_api = pytest.importorskip("playwright.async_api")

pytestmark = pytest.mark.extras


@pytest.fixture(scope="module")
def site() -> Any:
    with pw_site.serve() as base:
        yield base


def test_async_blocker(site: str, monkeypatch: pytest.MonkeyPatch) -> None:
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

    async def main() -> None:
        async with async_api.async_playwright() as p:
            try:
                browser = await p.chromium.launch(**pw_site.launch_options())
            except Exception as exc:
                if os.environ.get("CI"):
                    raise
                pytest.skip(f"Chromium not available: {exc}")
            context = await browser.new_context()
            await sw_pw.attach_async(context, w)
            await install_route_blocker_async(context, w, BlockPolicy(allow=["*/img/keep.png"]))
            page = await context.new_page()
            await page.goto(site + "/waste.html")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_function(
                "document.images.length === 3 && document.images[2].complete"
            )
            await context.close()
            await browser.close()

    asyncio.run(main())
    assert {(e.final_url or e.url).removeprefix(site) for e in events} == {
        "/waste.html",
        "/img/keep.png",
    }
    assert w.meter.waste().blocked_requests == 4
    assert w.run.counts.monitor_errors == 0
