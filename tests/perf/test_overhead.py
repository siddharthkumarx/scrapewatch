"""Overhead budget (docs/architecture.md section 15): per-call time of the monitor's hot paths.

Each test times 2,000 synthetic calls and asserts the p50 against the budget with 3x slack
for noisy runners. Numbers are printed (``pytest -s``) and, when ``SCRAPEWATCH_PERF_OUT``
names a file, written there as JSON, so a benchmark run can keep them.
"""

from __future__ import annotations

import gc
import json
import os
import statistics
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scrapewatch import runtime
from scrapewatch.checks.rules import DataRules
from scrapewatch.config import Settings
from scrapewatch.runtime import Watch

pytestmark = pytest.mark.perf

CALLS = 2_000
WARMUP = 50
SLACK = 3.0
RESULTS: dict[str, dict[str, float]] = {}
# Read at import: tests run in their own directory with SCRAPEWATCH_* variables cleared.
_OUT_ENV = os.environ.get("SCRAPEWATCH_PERF_OUT")
PERF_OUT = Path(_OUT_ENV).resolve() if _OUT_ENV else None

TEXT = "<p>A Light in the Attic, Tipping the Velvet, Soumission, Sharp Objects.</p>\n"
BODY_64K = (
    b"<!doctype html><html><head><title>All products | Books</title></head><body>"
    + (TEXT * (65536 // len(TEXT))).encode()
)[:65536] + b"</body></html>"
HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "server": "nginx",
    "content-encoding": "gzip",
    "set-cookie": "sessionid=abc; Path=/",
}
ITEM = {
    "title": "  A Light in the Attic ",
    "price": "£51.77",
    "stock": "22",
    "rating": "3",
    "category": "Poetry",
    "upc": "a897fe39b1053632",
    "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
    "author": "Shel Silverstein",
    "year": "1974",
    "pages": "176",
}
FIELDS: dict[str, Any] = {
    "title": {"type": "string", "clean": ["whitespace"]},
    "price": {"type": "number", "clean": ["price"]},
    "stock": {"type": "integer", "clean": ["number"]},
    **{k: {"type": "string"} for k in ("rating", "category", "upc", "url", "author")},
    "year": {"type": "string"},
    "pages": {"type": "string"},
}
SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in ITEM},
    "required": list(ITEM),
}


def _p50_ms(fn: Callable[[int], object]) -> float:
    for i in range(WARMUP):
        fn(CALLS + i)  # distinct inputs: warm-up items are not duplicates of timed ones
    times = []
    gc.disable()
    try:
        for i in range(CALLS):
            t0 = time.perf_counter()
            fn(i)
            times.append((time.perf_counter() - t0) * 1000.0)
    finally:
        gc.enable()
    return statistics.median(times)


def _write() -> None:
    if PERF_OUT is not None:
        PERF_OUT.parent.mkdir(parents=True, exist_ok=True)
        PERF_OUT.write_text(json.dumps(RESULTS, indent=2, sort_keys=True), encoding="utf-8")


def _report(name: str, p50: float, budget: float) -> None:
    RESULTS[name] = {"p50_ms": round(p50, 4), "budget_ms": budget, "limit_ms": budget * SLACK}
    print(f"perf {name}: p50 {p50:.4f} ms (budget {budget} ms, assert < {budget * SLACK} ms)")
    _write()


def _watch(tmp_path: Path, **settings: Any) -> Watch:
    w = Watch(Settings(name="perf", history={"path": tmp_path}, **settings))
    w.start()
    return w


def _record_response(w: Watch) -> Callable[[int], object]:
    def call(i: int) -> object:
        return w.record_response(
            f"https://books.toscrape.com/catalogue/page-{i % 50}.html",
            status=200,
            headers=HEADERS,
            body=BODY_64K,
            wire_bytes=12_874,
            bytes_source="measured",
            proxy="datacenter",
        )

    return call


def test_record_response_64k_guard_on(tmp_path: Path) -> None:
    w = _watch(tmp_path)  # history batched: a flush every 200 events is inside the timing
    p50 = _p50_ms(_record_response(w))
    w.finish()
    assert w.run.counts.monitor_errors == 0
    assert w.run.counts.by_label == {"ok": CALLS + WARMUP}
    _report("record_response_64k_guard_on", p50, 1.5)
    assert p50 < 1.5 * SLACK


def test_record_response_guard_off(tmp_path: Path) -> None:
    w = _watch(tmp_path, guard={"enabled": False})
    p50 = _p50_ms(_record_response(w))
    w.finish()
    assert w.run.counts.monitor_errors == 0
    _report("record_response_guard_off", p50, 0.15)
    assert p50 < 0.15 * SLACK


def _record_record(w: Watch) -> Callable[[int], object]:
    def call(i: int) -> object:
        return w.record_record({**ITEM, "upc": f"{i:016x}"})

    return call


def test_record_record_10_fields_3_normalizers(tmp_path: Path) -> None:
    rules = DataRules.model_validate({"name": "perf", "key": ["upc"], "fields": FIELDS})
    w = Watch(Settings(name="perf", history={"path": tmp_path}), rules=rules)
    w.start()
    p50 = _p50_ms(_record_record(w))
    w.finish()
    assert w.run.counts.usable_records == CALLS + WARMUP
    assert w.run.counts.fixes_by_normalizer["price"] >= CALLS
    _report("record_record_10_fields_3_normalizers", p50, 0.2)
    assert p50 < 0.2 * SLACK


def test_record_record_json_schema(tmp_path: Path) -> None:
    rules = DataRules.model_validate({"name": "perf", "key": ["upc"], "schema": SCHEMA})
    w = Watch(Settings(name="perf", history={"path": tmp_path}), rules=rules)
    w.start()
    p50 = _p50_ms(_record_record(w))
    w.finish()
    assert w.run.counts.schema_invalid == 0
    assert w.run.counts.usable_records == CALLS + WARMUP
    _report("record_record_json_schema", p50, 0.5)
    assert p50 < 0.5 * SLACK


def test_flush_queue_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "FLUSH_EVERY_EVENTS", 10**9)
    monkeypatch.setattr(runtime, "FLUSH_EVERY_SECONDS", 10.0**9)
    w = _watch(tmp_path)
    call = _record_response(w)
    call(0)  # warm caches (rules, size baseline lookups) outside the measurement
    w.flush()
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for i in range(200):
            call(i)
        queued = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert len(w._queue) == 200
    per_event_kib = queued / 200 / 1024
    w.finish()
    RESULTS["flush_queue_200_events"] = {
        "bytes": float(queued),
        "per_event_kib": round(per_event_kib, 3),
        "budget_kib": 2.0,
        "limit_bytes": float(1 << 20),
    }
    print(f"perf flush queue: 200 events hold {queued} bytes ({per_event_kib:.2f} KiB each)")
    _write()
    assert queued < 1 << 20
