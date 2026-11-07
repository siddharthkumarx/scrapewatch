"""Conditional requests and in-run dedupe through the httpx hooks (build item 22)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from scrapewatch.config import CostConfig, Settings
from scrapewatch.integrations import httpx as sw_httpx
from scrapewatch.models import ResponseEvent, Run
from scrapewatch.runtime import Watch
from scrapewatch.waste import ConditionalCache, RunDedupe

from . import cache_site

PAGES = ["1", "2", "3"]


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    with cache_site.serve() as base:
        yield base


def _watch(history: Path) -> Watch:
    cost = CostConfig(default_proxy="datacenter", proxies={"datacenter": {"per_gb": 0.8}})
    w = Watch(Settings(name="books", history={"path": history}, cost=cost))
    w.start()
    return w


def _run(history: Path, urls: list[str]) -> tuple[Run, list[ResponseEvent], list[bool]]:
    """One scraper run with the cache: fetch ``urls`` and return the run, events and flags."""
    w = _watch(history)
    events: list[ResponseEvent] = []
    real = w.record_response

    def spy(*args: object, **kwargs: object) -> ResponseEvent | None:
        ev = real(*args, **kwargs)  # type: ignore[arg-type]
        if ev is not None:
            events.append(ev)
        return ev

    w.record_response = spy  # type: ignore[method-assign]
    flags = []
    with (
        ConditionalCache(history, watch=w) as cache,
        httpx.Client(event_hooks=sw_httpx.hooks(w, cache=cache)) as client,
    ):
        for url in urls:
            r = client.get(url)
            flags.append(sw_httpx.unchanged(r))
            w.record_record({"title": f"from {url}"}, unchanged=sw_httpx.unchanged(r))
    return w.finish(), events, flags


def test_server_honouring_if_none_match(site: str, tmp_path: Path) -> None:
    urls = [f"{site}/honour/{n}.html" for n in PAGES]
    first, events, flags = _run(tmp_path, urls)
    assert flags == [False] * 3
    assert all(not e.cache.conditional and not e.cache.unchanged for e in events)
    assert first.waste.conditional_hits == 0

    second, events, flags = _run(tmp_path, urls)
    assert flags == [True] * 3
    assert [e.status for e in events] == [304] * 3
    assert all(e.cache.conditional and e.cache.unchanged for e in events)
    assert second.waste.conditional_hits == 3
    assert second.waste.unchanged_refetch_requests == 0
    assert second.waste.unchanged_refetch_bytes == 0
    assert second.waste.unchanged_refetch_cost == 0.0
    assert second.counts.by_label == {"ok": 3}  # a 304 is not an empty page
    assert second.counts.unchanged_records == 3
    assert second.counts.records == 3
    # a 304 costs only its headers: far fewer bytes than the first run
    assert second.counts.wire_bytes < first.counts.wire_bytes / 10
    assert second.counts.monitor_errors == 0


def test_last_modified_only(site: str, tmp_path: Path) -> None:
    urls = [f"{site}/since/{n}.html" for n in PAGES]
    _run(tmp_path, urls)
    second, _, flags = _run(tmp_path, urls)
    assert flags == [True] * 3
    assert second.waste.conditional_hits == 3
    assert ("/since/1.html", None, cache_site.LAST_MODIFIED) in cache_site.SEEN


def test_server_ignoring_conditionals_same_body(site: str, tmp_path: Path) -> None:
    urls = [f"{site}/ignore/{n}.html" for n in PAGES]
    _run(tmp_path, urls)
    second, events, flags = _run(tmp_path, urls)
    assert flags == [True] * 3  # unchanged by body hash
    assert [e.status for e in events] == [200] * 3
    assert all(e.cache.conditional and e.cache.unchanged for e in events)
    assert second.waste.conditional_hits == 0
    assert second.waste.unchanged_refetch_requests == 3
    assert second.waste.unchanged_refetch_bytes == second.counts.wire_bytes
    assert second.waste.unchanged_refetch_cost > 0


def test_changed_body_is_not_unchanged(site: str, tmp_path: Path) -> None:
    _run(tmp_path, [f"{site}/changing.html"])
    second, events, flags = _run(tmp_path, [f"{site}/changing.html"])
    assert flags == [False]
    assert events[0].cache.conditional
    assert not events[0].cache.unchanged
    assert second.waste.unchanged_refetch_requests == 0


def test_own_conditional_headers_and_non_get_are_left_alone(site: str, tmp_path: Path) -> None:
    url = f"{site}/honour/7.html"
    _run(tmp_path, [url])
    w = _watch(tmp_path)
    with (
        ConditionalCache(tmp_path) as cache,
        httpx.Client(event_hooks=sw_httpx.hooks(w, cache=cache)) as client,
    ):
        r = client.get(url, headers={"If-None-Match": '"mine"'})
        assert r.status_code == 200  # the scraper's own validator was sent, not the cache's
        assert sw_httpx.unchanged(r)  # still the same body as last time (hash)
        p = client.post(url)
        assert not sw_httpx.unchanged(p)
    assert ("/honour/7.html", '"mine"', None) in cache_site.SEEN
    assert ("/honour/7.html", None, None) in cache_site.SEEN  # the POST
    w.finish()


def test_unchanged_without_a_cache(site: str, tmp_path: Path) -> None:
    w = _watch(tmp_path)
    with httpx.Client(event_hooks=sw_httpx.hooks(w)) as client:
        r = client.get(f"{site}/honour/1.html", headers={"If-None-Match": '"v1"'})
        assert r.status_code == 304
        assert sw_httpx.unchanged(r)  # a 304 to a conditional request
        r = client.get(f"{site}/ignore/1.html")
        assert not sw_httpx.unchanged(r)
    assert not sw_httpx.unchanged(object())
    assert not sw_httpx.unchanged(1)  # not weak-referenceable
    run = w.finish()
    assert run.waste.conditional_hits == 1


def test_cache_errors_fail_open(site: str, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    w = _watch(tmp_path / "h")
    cache = ConditionalCache(blocker / "sub", watch=w)
    with httpx.Client(event_hooks=sw_httpx.hooks(w, cache=cache)) as client:
        r = client.get(f"{site}/honour/1.html")
    assert r.status_code == 200
    assert not sw_httpx.unchanged(r)
    assert w.run.counts.monitor_errors == 2  # lookup and update
    assert w.run.counts.responses == 1


def test_async_hooks_with_cache(site: str, tmp_path: Path) -> None:
    urls = [f"{site}/honour/{n}.html" for n in PAGES]

    async def scrape() -> list[bool]:
        w = _watch(tmp_path)
        flags = []
        with ConditionalCache(tmp_path) as cache:
            async with httpx.AsyncClient(event_hooks=sw_httpx.async_hooks(w, cache=cache)) as c:
                for url in urls:
                    flags.append(sw_httpx.unchanged(await c.get(url)))
        w.finish()
        return flags

    assert asyncio.run(scrape()) == [False] * 3
    assert asyncio.run(scrape()) == [True] * 3


def test_duplicate_urls_in_one_run_are_deduped(site: str, tmp_path: Path) -> None:
    w = _watch(tmp_path)
    seen = RunDedupe(w)
    urls = [f"{site}/honour/{n}.html" for n in ["1", "2", "1", "3", "2"]]
    urls.append(f"{site}/honour/3.html#reviews")
    fetched = 0
    with httpx.Client(event_hooks=sw_httpx.hooks(w)) as client:
        for url in urls:
            if seen.seen_before(url):
                continue
            client.get(url)
            fetched += 1
    run = w.finish()
    assert fetched == 3
    assert run.counts.responses == 3
    assert run.waste.deduped_requests == 3
