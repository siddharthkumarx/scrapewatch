"""Conditional cache, in-run URL dedupe and their accounting (build item 22), without HTTP."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from scrapewatch.checks.batch import BatchStats, batch_findings
from scrapewatch.checks.engine import check_records
from scrapewatch.checks.rules import load_data_rules
from scrapewatch.config import CostConfig, Settings
from scrapewatch.cost.meter import CostMeter, waste_category
from scrapewatch.history.baselines import Baseline
from scrapewatch.models import CacheInfo, ResponseEvent
from scrapewatch.runtime import Watch
from scrapewatch.waste import ConditionalCache, RunDedupe
from scrapewatch.waste import cache as cache_mod
from scrapewatch.waste.cache import is_conditional
from scrapewatch.waste.dedupe import canonical_url

CHECKS = Path(__file__).parents[1] / "fixtures" / "checks"
BODY = b"<html><title>A Light in the Attic</title></html>"


def _watch(tmp_path: Path) -> Watch:
    w = Watch(Settings(name="t", history={"path": tmp_path}))
    w.start()
    return w


# --- canonical URLs and RunDedupe -------------------------------------------------------
@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://Books.ToScrape.com:443/a/../b", "https://books.toscrape.com/b"),
        ("https://books.toscrape.com/b?utm_source=x&page=2", "https://books.toscrape.com/b?page=2"),
        ("https://books.toscrape.com/b?y=1&x=2#top", "https://books.toscrape.com/b?x=2&y=1"),
    ],
)
def test_canonical_url_same_page(a: str, b: str) -> None:
    assert canonical_url(a) == canonical_url(b)


def test_canonical_url_keeps_what_it_cannot_read() -> None:
    assert canonical_url(" /relative ") == "/relative"
    assert canonical_url("mailto:x@example.com") == "mailto:x@example.com"
    assert canonical_url("https://a.test/x") != canonical_url("https://a.test/y")


def test_run_dedupe_counts_into_the_run(tmp_path: Path) -> None:
    w = _watch(tmp_path)
    seen = RunDedupe(w)
    urls = [
        "https://books.toscrape.com/catalogue/page-1.html",
        "https://books.toscrape.com/catalogue/page-2.html",
        "https://BOOKS.toscrape.com/catalogue/page-1.html#reviews",
        "https://books.toscrape.com/catalogue/page-2.html?utm_campaign=x",
        "https://books.toscrape.com/catalogue/page-3.html",
    ]
    fetched = [u for u in urls if not seen.seen_before(u)]
    assert fetched == [urls[0], urls[1], urls[4]]
    assert len(seen) == 3
    assert seen.skipped == 2
    run = w.finish()
    assert run.waste.deduped_requests == 2
    assert run.counts.responses == 0  # a skipped request is not a response
    assert run.cost.total == 0.0


def test_run_dedupe_without_watch_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = RunDedupe()
    assert not seen.seen_before("https://a.test/")
    assert seen.seen_before("https://a.test/")
    w = _watch(tmp_path)
    broken = RunDedupe(w)

    def boom(url: str) -> str:
        raise RuntimeError("canonical bug")

    monkeypatch.setattr("scrapewatch.waste.dedupe.canonical_url", boom)
    assert not broken.seen_before("https://a.test/")  # fails open: treated as new
    assert not RunDedupe().seen_before("https://a.test/")
    assert w.run.counts.monitor_errors == 1


def test_add_deduped_fails_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch(tmp_path)

    def boom(*args: Any) -> None:
        raise RuntimeError("meter bug")

    monkeypatch.setattr(w.meter, "add_deduped", boom)
    w.add_deduped()
    assert w.run.counts.monitor_errors == 1


# --- ConditionalCache -------------------------------------------------------------------
def test_cache_round_trip_across_instances(tmp_path: Path) -> None:
    url = "https://books.toscrape.com/catalogue/page-2.html"
    with ConditionalCache(tmp_path) as cache:
        assert cache.request_headers(url) == {}
        assert not cache.observe(url, 200, {"ETag": '"v1"', "Last-Modified": "Mon"}, BODY)
        # pending entries are visible before they are written
        assert cache.request_headers(url) == {"If-None-Match": '"v1"', "If-Modified-Since": "Mon"}
    with ConditionalCache(str(tmp_path)) as cache:
        assert cache.request_headers(url + "#x") == {
            "If-None-Match": '"v1"',
            "If-Modified-Since": "Mon",
        }
        assert cache.observe(url, 304, {}, None)  # unchanged; validators kept
        assert cache.observe(url, 200, {"ETag": '"v2"'}, BODY)  # same body hash
        assert cache.request_headers(url) == {"If-None-Match": '"v2"'}
        assert not cache.observe(url, 200, {}, BODY + b"!")  # changed
        assert not cache.observe(url, 200, {}, None)  # body never read: no hash
        assert not cache.observe(url, 200, {}, BODY)  # nothing to compare with now
        assert cache.observe(url, 200, {}, BODY)
        assert not cache.observe(url, 404, {}, b"gone")
        assert not cache.observe(url, 301, {"Location": "/"}, b"")
        assert cache.observe("https://new.test/", 304, {"ETag": '"x"'}, None)  # no entry
        assert cache.request_headers("https://new.test/") == {}
    assert cache.errors == 0


def test_cache_stores_redacted_canonical_urls_and_no_bodies(tmp_path: Path) -> None:
    with ConditionalCache(tmp_path) as cache:
        cache.observe("https://Api.Test/x?token=s3cret&b=1", 200, {"ETag": '"e"'}, BODY)
        assert cache.request_headers("https://api.test/x?b=1&token=s3cret") == {
            "If-None-Match": '"e"'
        }
    con = sqlite3.connect(tmp_path / "history.sqlite")
    try:
        rows = con.execute("SELECT * FROM cache_entries").fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    url, etag, last_modified, digest, seen_at = rows[0]
    assert "s3cret" not in url
    assert url.startswith("https://api.test/x?")
    assert etag == '"e"'
    assert last_modified is None
    assert len(digest) == 64
    assert "Attic" not in json.dumps(rows)
    assert seen_at


def test_cache_flushes_in_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_mod, "FLUSH_EVERY_ENTRIES", 2)
    cache = ConditionalCache(tmp_path)
    cache.observe("https://a.test/1", 200, {"ETag": "1"}, BODY)
    assert cache.flush() == 1
    assert cache.flush() == 0
    cache.observe("https://a.test/2", 200, {"ETag": "2"}, BODY)
    cache.observe("https://a.test/3", 200, {"ETag": "3"}, BODY)  # second pending: flushed
    assert cache._pending == {}
    monkeypatch.setattr(cache_mod, "FLUSH_EVERY_SECONDS", 0.0)
    cache.observe("https://a.test/4", 200, {"ETag": "4"}, BODY)
    assert cache._pending == {}
    cache.close()
    cache.close()
    with ConditionalCache(tmp_path) as again:
        assert again.request_headers("https://a.test/4") == {"If-None-Match": "4"}


def test_cache_fails_open(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    w = _watch(tmp_path / "h")
    cache = ConditionalCache(blocker / "sub", watch=w)
    assert cache.request_headers("https://a.test/") == {}
    assert not cache.observe("https://a.test/", 200, {}, BODY)
    assert cache.errors == 2
    assert w.run.counts.monitor_errors == 2
    alone = ConditionalCache(blocker / "sub")
    assert alone.request_headers("https://a.test/") == {}
    assert alone.errors == 1


def test_is_conditional() -> None:
    assert is_conditional({"If-None-Match": '"x"'})
    assert is_conditional({"if-modified-since": "Mon"})
    assert not is_conditional({"Accept": "*/*"})


# --- accounting -------------------------------------------------------------------------
def _event(status: int, wire: int, **cache: bool) -> ResponseEvent:
    return ResponseEvent(
        run_id="r",
        url="https://a.test/",
        domain="a.test",
        status=status,
        wire_bytes=wire,
        proxy="datacenter",
        cache=CacheInfo(**cache),
    )


def test_conditional_hit_is_not_waste_and_full_refetch_is() -> None:
    hit = _event(304, 300, conditional=True, unchanged=True)
    refetch = _event(200, 50_000, conditional=True, unchanged=True)
    unconditional_same = _event(200, 50_000, unchanged=True)
    changed = _event(200, 50_000, conditional=True)
    assert waste_category(hit) is None
    assert waste_category(refetch) == "unchanged_refetch"
    assert waste_category(unconditional_same) == "unchanged_refetch"
    assert waste_category(changed) is None
    meter = CostMeter(CostConfig(proxies={"datacenter": {"per_gb": 0.8}}))
    for ev in (hit, refetch, unconditional_same, changed):
        meter.add(ev)
    waste = meter.waste()
    assert waste.conditional_hits == 1
    assert waste.unchanged_refetch_requests == 2
    assert waste.unchanged_refetch_bytes == 100_000


def test_deduped_requests_survive_worker_aggregation() -> None:
    parent = CostMeter(CostConfig())
    parent.add_deduped(3)
    parent.add(_event(200, 10, deduped=True))
    rebuilt = CostMeter(CostConfig())  # what finish() builds from the store's events
    rebuilt.add(_event(200, 10, deduped=True))
    rebuilt.merge_extras(parent)
    assert parent.waste().deduped_requests == 4
    assert rebuilt.waste().deduped_requests == 4  # the deduped event is not counted twice


def test_record_response_takes_cache_meta(tmp_path: Path) -> None:
    w = _watch(tmp_path)
    ev = w.record_response(
        "https://a.test/",
        status=304,
        meta={"cache": {"conditional": True, "unchanged": True}},
    )
    assert ev is not None
    assert ev.cache == CacheInfo(conditional=True, unchanged=True)
    plain = w.record_response("https://a.test/", status=200, meta={"cache": "nonsense"})
    assert plain is not None
    assert plain.cache == CacheInfo()
    run = w.finish()
    assert run.waste.conditional_hits == 1


def test_unchanged_records_count_but_leave_the_changed_share(tmp_path: Path) -> None:
    w = _watch(tmp_path)
    w.record_record({"title": "a"}, unchanged=True)
    rec = w.record_record({"title": "b"})
    assert rec is not None
    assert not rec.unchanged
    assert w.run.counts.unchanged_records == 1
    assert w.run.counts.records == 2

    rules = load_data_rules(CHECKS / "rules_batch.yml")
    rules.batch.changed_share = rules.batch.changed_share.model_copy(
        update={"hold_above_pct": None, "warn_above_pct": 100.0, "warn_below_pct": 5.0}
    )
    lines = (CHECKS / "batch_coverage_drop.jsonl").read_text("utf-8").splitlines()
    items = [json.loads(line) for line in lines]
    records = check_records(items, rules).records
    base = Baseline.model_validate_json((CHECKS / "baseline.json").read_text("utf-8"))
    base.previous_value_hashes = dict(BatchStats.from_records(records, rules).value_hashes)

    def changed_share(recs: list[Any]) -> list[str]:
        batch = BatchStats.from_records(recs, rules)
        found = batch_findings(batch, rules, base, run_id="r")
        return [f.check for f in found if f.check == "batch.changed_share"]

    # identical values: 0% changed is below warn_below_pct (stale data?)
    assert changed_share(records) == ["batch.changed_share"]
    # the same records from pages the cache found unchanged: not stale, nothing to compare
    unchanged = [r.model_copy(update={"unchanged": True}) for r in records]
    assert changed_share(unchanged) == []
    stats = BatchStats.from_records(unchanged, rules)
    assert stats.value_hashes == base.previous_value_hashes  # still stored for the next run
