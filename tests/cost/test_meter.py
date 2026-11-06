import json
import math
from pathlib import Path

import pytest
import yaml

from scrapewatch.config import CostConfig, Settings
from scrapewatch.cost import CostMeter, content_length, header_bytes_estimate, price_event
from scrapewatch.cost.meter import waste_category
from scrapewatch.models import (
    CacheInfo,
    ErrorInfo,
    GuardLabel,
    GuardResult,
    ResourceType,
    ResponseEvent,
)
from scrapewatch.runtime import Watch, watch

COST = Path(__file__).parents[1] / "fixtures" / "cost"


def _config() -> CostConfig:
    return CostConfig.model_validate(yaml.safe_load((COST / "cost_config.yml").read_text("utf-8")))


def _events() -> list[ResponseEvent]:
    lines = (COST / "events_small.jsonl").read_text("utf-8").splitlines()
    return [ResponseEvent.model_validate_json(line) for line in lines]


def _meter() -> CostMeter:
    meter = CostMeter(_config())
    for ev in _events():
        meter.add(ev)
    meter.add_browser_seconds(120)
    return meter


def _close(got: object, exp: object) -> None:
    if isinstance(exp, dict):
        assert isinstance(got, dict)
        assert set(got) == set(exp)
        for k in exp:
            _close(got[k], exp[k])
    elif isinstance(exp, float):
        assert got == pytest.approx(exp, abs=1e-12)
    else:
        assert got == exp


def test_cost_breakdown_golden() -> None:
    got = _meter().breakdown(usable_records=10, records=12).model_dump(mode="json")
    _close(got, json.loads((COST / "cost_breakdown.json").read_text("utf-8")))


def test_waste_breakdown_golden() -> None:
    got = _meter().waste().model_dump(mode="json")
    _close(got, json.loads((COST / "waste_breakdown.json").read_text("utf-8")))


def test_waste_from_explicit_events_matches() -> None:
    meter = CostMeter(_config())
    assert meter.waste(_events()) == _meter().waste()


def test_totals_equal_sum_of_parts() -> None:
    b = _meter().breakdown(usable_records=10, records=12)
    assert math.isclose(b.total, math.fsum(b.by_domain.values()), abs_tol=1e-12)
    assert math.isclose(b.total, math.fsum(b.by_proxy.values()) + b.browser_cost, abs_tol=1e-12)
    assert math.isclose(
        b.total, math.fsum(b.by_resource_type.values()) + b.browser_cost, abs_tol=1e-12
    )
    w = _meter().waste()
    parts = [w.retry_cost, w.failed_cost, w.unchanged_refetch_cost, w.blockable_cost]
    assert math.isclose(w.total_waste_cost, math.fsum(parts), abs_tol=1e-12)


def test_waste_category_precedence() -> None:
    ev = ResponseEvent(
        run_id="r",
        url="https://a.example/x.png",
        domain="a.example",
        status=500,
        wire_bytes=10,
        resource_type=ResourceType.IMAGE,
        attempt=2,
        guard=GuardResult(label=GuardLabel.BLOCK),
        cache=CacheInfo(unchanged=True),
        error=ErrorInfo(type="X"),
    )
    assert waste_category(ev) == "retry"
    ev.attempt = 1
    assert waste_category(ev) == "failed"
    ev.status, ev.error, ev.guard = 200, None, GuardResult()
    assert waste_category(ev) == "unchanged_refetch"
    ev.cache = CacheInfo()
    assert waste_category(ev) == "blockable"
    ev.resource_type = ResourceType.DOCUMENT
    assert waste_category(ev) is None
    ev.cache = CacheInfo(unchanged=True)
    ev.wire_bytes = 0  # a 304 with no bytes is not a costly refetch
    assert waste_category(ev) is None


def test_per_record_null_when_zero() -> None:
    b = CostMeter(_config()).breakdown(usable_records=0, records=0)
    assert b.per_usable_record is None
    assert b.per_record is None
    assert b.total == 0.0


def test_unknown_label_finding() -> None:
    findings = _meter().findings("r")
    assert [f.check for f in findings] == ["cost.unknown_label"]
    assert findings[0].observed == ["mystery-pool"]
    assert findings[0].severity.value == "info"
    assert CostMeter(_config()).findings("r") == []


def test_price_event_variants() -> None:
    cfg = _config()
    ev = ResponseEvent(run_id="r", url="u", domain="d", wire_bytes=2_000_000_000)
    assert price_event(ev, cfg) == 0.0  # default proxy "direct"
    ev.proxy = "residential"
    assert price_event(ev, cfg) == 16.0
    ev.proxy = "unblocker"
    assert price_event(ev, cfg) == 0.0025
    ev.cache = CacheInfo(deduped=True)
    assert price_event(ev, cfg) == 0.0


def test_browser_time_without_documents_and_by_domain() -> None:
    meter = CostMeter(_config())
    meter.add_browser_seconds(60)
    meter.add_browser_seconds(-5)
    meter.add_browser_seconds(30, domain="quotes.toscrape.com")
    b = meter.breakdown(usable_records=1, records=1)
    assert b.browser_minutes == 1.5
    assert b.by_domain == {"(browser)": pytest.approx(0.01), "quotes.toscrape.com": 0.005}
    assert b.total == pytest.approx(0.015)
    meter.add(ResponseEvent(run_id="r", url="u", domain="x", resource_type=ResourceType.IMAGE))
    b2 = meter.breakdown(usable_records=1, records=1)
    assert b2.by_domain["x"] == pytest.approx(0.01)  # no documents: spread by responses


def test_blocked_accounting_and_merge() -> None:
    a = CostMeter(_config())
    a.add_blocked(3, bytes_saved_estimate=1000)
    a.add_browser_seconds(60)
    a.add_browser_seconds(60, domain="d")
    b = CostMeter(_config())
    b.merge_extras(a)
    w = b.waste()
    assert (w.blocked_requests, w.bytes_saved_estimate) == (3, 1000)
    assert b.browser_minutes == 2.0


def test_header_estimate_and_content_length() -> None:
    assert header_bytes_estimate({"a": "bc", "de": "f"}) == (1 + 2 + 4) + (2 + 1 + 4) + 2
    assert header_bytes_estimate({}) == 0
    assert header_bytes_estimate(None) == 0
    assert header_bytes_estimate([("x", "y"), ("x", "z")]) == 6 + 6 + 2

    class Multi:
        def multi_items(self) -> list[tuple[str, str]]:
            return [("set-cookie", "a=1"), ("set-cookie", "b=2")]

    assert header_bytes_estimate(Multi()) == 2 * (10 + 3 + 4) + 2  # type: ignore[arg-type]
    assert content_length({"Content-Length": "42"}) == 42
    assert content_length({"content-length": "12, 12"}) == 12
    assert content_length({"content-length": "x"}) is None
    assert content_length({"content-length": "-1"}) is None
    assert content_length({"server": "x"}) is None
    assert content_length(None) is None


def test_watch_prices_events_and_fills_run(tmp_path: Path) -> None:
    settings = Settings(cost=_config(), history={"path": tmp_path})
    with watch(settings=settings) as w:
        ev = w.record_response(
            "https://books.toscrape.com/", status=200, wire_bytes=1_000_000, proxy="datacenter"
        )
        w.record_response("https://books.toscrape.com/x", status=200, proxy="nope")
        w.meter.add_browser_seconds(60)
        w.record_record({"a": 1})
    assert ev is not None
    assert ev.cost == pytest.approx(0.0008)
    run = w.run
    assert run.cost.total == pytest.approx(0.0008 + 0.01)
    assert run.cost.per_usable_record == pytest.approx(0.0108)
    assert run.waste.bytes_by_resource_type == {"document": 1_000_000}
    assert [f.check for f in run.findings] == ["cost.unknown_label"]


def test_parallel_workers_are_priced(tmp_path: Path) -> None:
    settings = Settings(cost=_config(), history={"path": tmp_path})
    parent = Watch(settings)
    parent.start()
    worker = Watch.attach(parent.run.run_id, settings)
    worker.record_response("https://a.example/", status=200, wire_bytes=10**9, proxy="datacenter")
    worker.finish()
    parent.record_response("https://a.example/", status=200, wire_bytes=10**9, proxy="datacenter")
    run = parent.finish()
    assert run.counts.responses == 2
    assert run.cost.total == pytest.approx(1.6)


def test_per_1000_only_below_one_cent() -> None:
    from scrapewatch.models import PER_1000_BELOW, CostBreakdown, per_1000

    assert PER_1000_BELOW == 0.01
    assert per_1000(None) is None
    assert per_1000(0.01) is None
    assert per_1000(0.0115) is None
    assert per_1000(0.0000115) == pytest.approx(0.0115)
    assert per_1000(0.0) == 0.0
    small = CostBreakdown(per_usable_record=0.00001, per_1000_usable_records=0.01)
    assert small.headline() == (0.01, "per 1,000 usable records")
    big = CostBreakdown(per_usable_record=0.5, per_record=0.4)
    assert big.headline() == (0.5, "per usable record")
    assert big.headline(usable=False) == (0.4, "per record")
    assert CostBreakdown().headline() is None
