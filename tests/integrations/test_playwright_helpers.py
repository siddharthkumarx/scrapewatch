"""Pure helpers of the Playwright integration; no browser needed."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from scrapewatch.config import Settings
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.runtime import Watch


def _req(url: str, prev: Any = None, **kw: Any) -> Any:
    return SimpleNamespace(url=url, redirected_from=prev, **kw)


def test_helpers() -> None:
    chain = _req("https://a.example/3", _req("https://a.example/2", _req("https://a.example/1")))
    assert sw_pw._origin(chain) == "https://a.example/1"
    assert sw_pw._elapsed_ms(_req("u", timing={"responseEnd": -1})) is None
    assert sw_pw._elapsed_ms(_req("u", timing=None)) is None
    assert sw_pw._elapsed_ms(_req("u", timing={"responseEnd": 12})) == 12.0
    assert sw_pw._wire(None) == 0
    assert sw_pw._wire({"responseBodySize": -1, "responseHeadersSize": 100}) == 100
    assert sw_pw.resource_type_of(_req("u", resource_type="fetch")) == "xhr"
    assert sw_pw.resource_type_of(_req("u", resource_type="websocket")) == "other"


def test_browser_time_counted_once() -> None:
    w = Watch(Settings(name="t"))
    rec = sw_pw._Recorder(w, None)
    rec.started -= 6.0
    rec.closed()
    rec.closed()
    assert w.meter.browser_minutes == pytest.approx(0.1, abs=0.01)


def test_add_browser_seconds_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    w = Watch(Settings(name="t"))
    w.add_browser_seconds(30, domain="a.example")
    assert w.meter.browser_minutes == pytest.approx(0.5)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("meter bug")

    monkeypatch.setattr(w.meter, "add_browser_seconds", boom)
    w.add_browser_seconds(1)
    assert w.run.counts.monitor_errors == 1
