"""Conditional requests through the requests adapter (build item 22)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scrapewatch.config import Settings
from scrapewatch.integrations import requests as sw_requests
from scrapewatch.models import Run
from scrapewatch.runtime import Watch
from scrapewatch.waste import ConditionalCache

from . import cache_site

requests = pytest.importorskip("requests")

pytestmark = pytest.mark.extras


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    with cache_site.serve() as base:
        yield base


def _run(history: Path, urls: list[str], *, stream: bool = False) -> tuple[Run, list[bool]]:
    w = Watch(Settings(name="books", history={"path": history}))
    w.start()
    flags = []
    with ConditionalCache(history, watch=w) as cache, requests.Session() as session:
        sw_requests.mount(session, w, cache=cache)
        for url in urls:
            r = session.get(url, stream=stream)
            if stream:
                for _ in r.iter_content(1024):
                    pass
                r.close()
            flags.append(sw_requests.unchanged(r))
    return w.finish(), flags


def test_server_honouring_if_none_match(site: str, tmp_path: Path) -> None:
    urls = [f"{site}/honour/{n}.html" for n in ["1", "2", "3"]]
    _, flags = _run(tmp_path, urls)
    assert flags == [False] * 3
    second, flags = _run(tmp_path, urls)
    assert flags == [True] * 3
    assert second.counts.by_status == {"304": 3}
    assert second.waste.conditional_hits == 3
    assert second.waste.unchanged_refetch_requests == 0
    assert second.waste.unchanged_refetch_bytes == 0
    assert second.counts.by_label == {"ok": 3}


def test_server_ignoring_conditionals_same_body(site: str, tmp_path: Path) -> None:
    urls = [f"{site}/ignore/{n}.html" for n in ["1", "2"]]
    _run(tmp_path, urls)
    second, flags = _run(tmp_path, urls)
    assert flags == [True, True]
    assert second.waste.conditional_hits == 0
    assert second.waste.unchanged_refetch_requests == 2


def test_streamed_bodies_and_own_headers(site: str, tmp_path: Path) -> None:
    urls = [f"{site}/honour/4.html"]
    _run(tmp_path, urls, stream=True)
    second, flags = _run(tmp_path, urls, stream=True)
    assert flags == [True]  # a 304 needs no body
    assert second.waste.conditional_hits == 1
    w = Watch(Settings(name="books", history={"path": tmp_path}))
    w.start()
    with ConditionalCache(tmp_path) as cache, requests.Session() as session:
        session.mount("http://", sw_requests.adapter(w, cache=cache))
        r = session.get(urls[0], headers={"If-None-Match": '"mine"'})
        assert r.status_code == 200
        assert sw_requests.unchanged(r)  # same body as the first run (hash)
        p = session.post(urls[0])
        assert not sw_requests.unchanged(p)
    assert ("/honour/4.html", '"mine"', None) in cache_site.SEEN
    w.finish()


def test_unchanged_without_a_cache_and_failures(site: str, tmp_path: Path) -> None:
    w = Watch(Settings(name="books", history={"path": tmp_path}))
    w.start()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with requests.Session() as session:
        sw_requests.mount(session, w)
        r = session.get(f"{site}/honour/1.html", headers={"If-None-Match": '"v1"'})
        assert r.status_code == 304
        assert sw_requests.unchanged(r)
    with requests.Session() as session:
        sw_requests.mount(session, w, cache=ConditionalCache(blocker / "sub", watch=w))
        r = session.get(f"{site}/honour/2.html")
        assert r.status_code == 200
        assert not sw_requests.unchanged(r)
    assert not sw_requests.unchanged(object())
    bad: Any = 1
    assert not sw_requests.unchanged(bad)
    run = w.finish()
    assert run.waste.conditional_hits == 1
    assert run.counts.monitor_errors == 2  # the broken cache's lookup and update
