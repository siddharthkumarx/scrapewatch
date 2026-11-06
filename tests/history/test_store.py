import json
import multiprocessing
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scrapewatch.config import Settings
from scrapewatch.errors import HistoryError
from scrapewatch.history import migrations
from scrapewatch.history.baselines import Baseline, compute_baseline
from scrapewatch.history.stats import FieldStats, field_stats_of, quantile, value_hash
from scrapewatch.history.store import HistoryStore, open_store, path_pattern
from scrapewatch.models import (
    Finding,
    FindingScope,
    GuardLabel,
    GuardResult,
    Record,
    ResourceType,
    ResponseEvent,
    Run,
    RunStatus,
    Verdict,
    VerdictStatus,
)
from scrapewatch.runtime import Watch

HIST = Path(__file__).parents[1] / "fixtures" / "history"


def _event(run_id: str, i: int, **kw: Any) -> ResponseEvent:
    url = kw.pop("url", f"https://books.toscrape.com/catalogue/page-{i}.html")
    return ResponseEvent(
        run_id=run_id,
        url=url,
        domain=kw.pop("domain", url.split("/")[2]),
        status=kw.pop("status", 200),
        wire_bytes=kw.pop("wire_bytes", 1000 + i),
        body_bytes=kw.pop("body_bytes", 5000 + i),
        **kw,
    )


def _run(name: str, verdict: VerdictStatus, started: datetime, records: int = 100) -> Run:
    run = Run(name=name, started_at=started, finished_at=started + timedelta(minutes=1))
    run.status = RunStatus.FINISHED
    run.verdict = Verdict(status=verdict)
    run.counts.records = records
    return run


def test_create_open_migrate_round_trip(tmp_path: Path) -> None:
    store = open_store(tmp_path / "h")
    assert store.db_path.is_file()
    assert migrations.current_version(store.conn) == migrations.LATEST
    run = Run(name="books")
    store.start_run(run)
    store.close()
    store.close()  # idempotent
    with HistoryStore(tmp_path / "h") as again:
        again.open()  # idempotent
        got = again.get_run(run.run_id)
        assert got == run
        assert again.get_run("missing") is None
        mode = again.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        assert migrations.migrate(again.conn) == []  # nothing pending


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    open_store(tmp_path).close()
    conn = sqlite3.connect(tmp_path / "history.sqlite")
    with conn:
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    conn.close()
    with pytest.raises(HistoryError, match="schema version 99"):
        open_store(tmp_path)


def test_unopenable_path_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(HistoryError):
        open_store(blocker / "sub")
    with pytest.raises(HistoryError, match="not open"):
        HistoryStore(tmp_path).events("r")


def test_corrupt_file_raises(tmp_path: Path) -> None:
    (tmp_path / "history.sqlite").write_bytes(b"not a database" * 100)
    with pytest.raises(HistoryError):
        open_store(tmp_path)


def test_write_1000_events_in_batches_and_read_back(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    run = Run(name="books")
    store.start_run(run)
    events = [_event(run.run_id, i, proxy="datacenter", cost=i * 1e-7) for i in range(1000)]
    events[5].guard = GuardResult(label=GuardLabel.BLOCK, vendor="x", rule_id="x.block")
    events[6].attempt = 2
    events[6].retry_of = events[5].event_id
    for start in range(0, 1000, 200):
        assert store.write_events(events[start : start + 200]) == 200
    assert store.write_events([]) == 0
    back = store.events(run.run_id)
    assert back == events
    assert store.event_count(run.run_id) == 1000
    counts = store.response_counts(run.run_id)
    assert counts["responses"] == 1000
    assert counts["document_responses"] == 1000
    assert counts["retries"] == 1
    assert counts["by_label"] == {"block": 1, "ok": 999}
    assert counts["by_status"] == {"200": 1000}
    assert counts["wire_bytes"] == sum(e.wire_bytes for e in events)


def test_field_stats_golden(tmp_path: Path) -> None:
    lines = (HIST / "records_1000.jsonl").read_text("utf-8").splitlines()
    data = [json.loads(line) for line in lines]
    expected = json.loads((HIST / "field_stats.json").read_text("utf-8"))
    got = {k: v.model_dump() for k, v in field_stats_of(data).items()}
    assert set(got) == set(expected)
    for name, exp in expected.items():
        g = got[name]
        for key in ("count", "null_count", "distinct_count", "numeric_count", "top_values"):
            assert g[key] == exp[key], (name, key)
        for key in ("p05", "p50", "p95", "mean"):
            if exp[key] is None:
                assert g[key] is None
            else:
                assert g[key] == pytest.approx(exp[key], abs=1e-9), (name, key)
        if exp["hist"] is None:
            assert g["hist"] is None
        else:
            assert g["hist"]["counts"] == exp["hist"]["counts"]
            assert g["hist"]["edges"] == pytest.approx(exp["hist"]["edges"], abs=1e-9)
    # stored and read back through the store
    store = open_store(tmp_path)
    records = [Record(run_id="r1", data=d, dedupe_key=d["url"]) for d in data]
    store.write_record_stats("r1", records)
    stored = store.field_stats(["r1"])
    assert stored["price"]["r1"].p50 == pytest.approx(expected["price"]["p50"], abs=1e-9)
    assert stored["rating"]["r1"].top_values == expected["rating"]["top_values"]
    assert len(store.value_hashes("r1")) == 1000
    assert store.field_stats([]) == {}


def test_quantile_and_stats_edge_cases() -> None:
    assert quantile([3.0], 0.5) == 3.0
    assert quantile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    stats = field_stats_of(
        [{"a": True, "b": {"x": 1}, "c": [1, 2]}, {"a": False, "b": {"x": 1}, "c": float("nan")}]
    )
    assert stats["a"].numeric_count == 0  # bools are not numbers
    assert stats["b"].distinct_count == 1
    assert stats["c"].numeric_count == 0
    assert FieldStats().coverage == 0.0
    assert value_hash({"a": 1, "b": 2}) == value_hash({"b": 2, "a": 1})


def test_findings_round_trip(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    f = Finding(run_id="r", scope=FindingScope.BATCH, check="batch.row_count", message="m")
    store.write_findings([f])
    assert store.findings("r") == [f]


def _store_with_runs(
    tmp_path: Path, verdicts: list[VerdictStatus]
) -> tuple[HistoryStore, list[Run]]:
    store = open_store(tmp_path)
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    runs = []
    for i, v in enumerate(verdicts):
        run = _run("books", v, t0 + timedelta(hours=i), records=100 + 10 * i)
        store.start_run(run)
        store.write_record_stats(
            run.run_id,
            [
                Record(
                    run_id=run.run_id, data={"price": float(p + i), "c": "GBP"}, dedupe_key=str(p)
                )
                for p in range(10)
            ],
        )
        store.finish_run(run)
        runs.append(run)
    return store, runs


def test_baseline_uses_only_passing_runs(tmp_path: Path) -> None:
    p, h = VerdictStatus.PASS, VerdictStatus.HOLD
    store, runs = _store_with_runs(tmp_path, [p, h, p, h, p])
    crashed = _run("books", p, datetime(2026, 11, 2, tzinfo=timezone.utc))
    crashed.status = RunStatus.CRASHED
    store.finish_run(crashed)
    store.finish_run(_run("other", p, datetime(2026, 11, 3, tzinfo=timezone.utc)))
    base = compute_baseline(store, "books")
    passing = [runs[4].run_id, runs[2].run_id, runs[0].run_id]
    assert base.run_ids == passing  # most recent first
    assert base.available
    assert base.row_count_median == 120.0
    assert base.fields["price"]["p50"] == pytest.approx(6.5)  # medians of 4.5, 6.5, 8.5
    assert base.fields["price"]["coverage"] == 1.0
    assert base.fields["c"]["top_values"] == ["GBP"]
    assert sum(base.fields["price"]["hist"]["shares"]) == pytest.approx(1.0)
    assert base.previous_value_hashes == store.value_hashes(runs[4].run_id)
    assert len(store.baseline_runs("books", n=10, passing_only=False)) == 5
    assert len(store.list_runs(limit=100)) == 7


def test_empty_baseline_below_min_runs(tmp_path: Path) -> None:
    p, h = VerdictStatus.PASS, VerdictStatus.HOLD
    store, _ = _store_with_runs(tmp_path, [p, h, p])
    base = compute_baseline(store, "books", min_runs=3)
    assert base == Baseline(candidate_runs=2, min_runs=3)
    assert not base.available
    assert compute_baseline(store, "books", min_runs=2).available


def test_list_runs_order(tmp_path: Path) -> None:
    store, runs = _store_with_runs(tmp_path, [VerdictStatus.PASS] * 3)
    assert [r.run_id for r in store.list_runs("books", limit=2)] == [
        runs[2].run_id,
        runs[1].run_id,
    ]
    assert store.run_count() == 3


def _worker(history: str, run_id: str, offset: int) -> None:
    w = Watch.attach(run_id, Settings(history={"path": Path(history)}), integration="worker")
    for i in range(500):
        w.record_response(
            f"https://books.toscrape.com/catalogue/page-{offset + i}.html", status=200
        )
    w.finish()


@pytest.mark.skipif(sys.platform == "win32", reason="fork start method")
def test_two_processes_write_one_run(tmp_path: Path) -> None:
    settings = Settings(name="mp", history={"path": tmp_path})
    parent = Watch(settings)
    parent.start()
    ctx = multiprocessing.get_context("fork")
    procs = [
        ctx.Process(target=_worker, args=(str(tmp_path), parent.run.run_id, k * 1000))
        for k in range(2)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
        assert proc.exitcode == 0
    run = parent.finish()
    assert run.counts.responses == 1000
    assert run.counts.by_label == {"ok": 1000}
    with HistoryStore(tmp_path) as store:
        assert store.event_count(run.run_id) == 1000
        stored = store.get_run(run.run_id)
        assert stored is not None
        assert stored.counts.responses == 1000


def test_attached_worker_never_updates_run_row(tmp_path: Path) -> None:
    settings = Settings(name="w", history={"path": tmp_path})
    parent = Watch(settings)
    parent.start()
    worker = Watch.attach(parent.run.run_id, settings)
    worker.record_response("https://example.com/", status=200)
    worker.finish()
    with HistoryStore(tmp_path) as store:
        row = store.get_run(parent.run.run_id)
        assert row is not None
        assert row.status == RunStatus.RUNNING
        assert store.event_count(parent.run.run_id) == 1


def test_prune_deletes_by_window_and_keeps_run_rows(tmp_path: Path) -> None:
    store = open_store(tmp_path)
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    runs = []
    for i in range(4):
        run = _run("books", VerdictStatus.PASS, now - timedelta(days=40 - i))
        store.finish_run(run)
        store.write_record_stats(
            run.run_id, [Record(run_id=run.run_id, data={"a": 1}, dedupe_key="k")]
        )
        runs.append(run)
    ancient = _run("books", VerdictStatus.PASS, now - timedelta(days=400))
    store.finish_run(ancient)
    store.write_findings(
        [Finding(run_id=ancient.run_id, scope=FindingScope.RUN, check="run.x", message="m")]
    )
    old = [_event(runs[0].run_id, i, ts=now - timedelta(days=31)) for i in range(3)]
    fresh = [_event(runs[3].run_id, i, ts=now - timedelta(days=1)) for i in range(2)]
    store.write_events(old + fresh)
    deleted = store.prune(keep_runs=2, keep_event_days=30, now=now)
    assert deleted["events"] == 3
    assert deleted["runs"] == 1
    assert deleted["findings"] == 1
    assert deleted["field_stats"] == 2  # the two oldest of four (the ancient run had none)
    assert deleted["record_hashes"] == 2  # the ancient run had none
    assert store.get_run(runs[0].run_id) is not None  # run rows are kept
    assert store.get_run(ancient.run_id) is None
    assert store.event_count(runs[3].run_id) == 2
    assert set(store.field_stats([r.run_id for r in runs])["a"]) == {
        runs[2].run_id,
        runs[3].run_id,
    }


@pytest.mark.parametrize(
    ("url", "pattern"),
    [
        ("https://books.toscrape.com/catalogue/page-2.html", "/catalogue/page-{n}.html"),
        ("https://books.toscrape.com/", "/"),
        ("https://books.toscrape.com", "/"),
        ("https://quotes.toscrape.com/page/12/", "/page/{n}/"),
        ("https://a.example/x/y/z/w/v", "/x/y/z"),
        ("https://a.example/x/y/z/", "/x/y/z/"),
        ("https://a.example/2026/11/05/story-77", "/{n}/{n}/{n}"),
        ("http://[::1/broken", "/"),
    ],
)
def test_path_pattern(url: str, pattern: str) -> None:
    assert path_pattern(url) == pattern


def test_size_baseline_grouping_golden(tmp_path: Path) -> None:
    golden = json.loads((HIST / "size_events.json").read_text("utf-8"))
    store = open_store(tmp_path)
    events = []
    for i, spec in enumerate(golden["events"]):
        ev = _event(
            "r1",
            i,
            url=spec["url"],
            body_bytes=spec["body_bytes"],
            resource_type=ResourceType(spec.get("resource_type", "document")),
        )
        ev.guard = GuardResult(label=GuardLabel(spec.get("label", "ok")))
        events.append(ev)
    store.write_events(events)
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    assert store.update_size_baselines("r1", now=now) == len(golden["expected"])
    for exp in golden["expected"]:
        got = store.size_baseline(exp["domain"], exp["path_pattern"])
        assert got == (exp["p50_bytes"], exp["n"]), exp
    assert store.size_baseline("books.toscrape.com", "/static/logo.png") is None
    # a second run blends with the stored p50, weighting history by min(n, 50)
    store.write_events(
        [_event("r2", 0, url="https://quotes.toscrape.com/page/9/", body_bytes=14200)]
    )
    store.update_size_baselines("r2", now=now)
    assert store.size_baseline("quotes.toscrape.com", "/page/{n}/") == (12200, 3)
