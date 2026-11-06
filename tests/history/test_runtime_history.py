from pathlib import Path

import pytest

from scrapewatch import runtime
from scrapewatch.config import Settings
from scrapewatch.history.store import HistoryStore
from scrapewatch.models import RunStatus
from scrapewatch.runtime import Watch, watch


def test_run_is_stored_with_events_and_stats(tmp_path: Path) -> None:
    settings = Settings(name="books", history={"path": tmp_path / "h"})
    with watch(settings=settings) as w:
        ev = w.record_response("https://books.toscrape.com/", status=200, body=b"x" * 10)
        w.record_record({"title": "A", "price": 1.5})
    assert ev is not None
    with HistoryStore(tmp_path / "h") as store:
        run = store.get_run(w.run.run_id)
        assert run is not None
        assert run.status == RunStatus.FINISHED
        assert run.counts.records == 1
        assert store.events(w.run.run_id) == [ev]
        stats = store.field_stats([w.run.run_id])
        assert stats["price"][w.run.run_id].p50 == 1.5


def test_default_history_path_is_relative_to_cwd(tmp_path: Path) -> None:
    with watch("x") as w:
        w.record_response("https://example.com/", status=200)
    assert (tmp_path / ".scrapewatch" / "history.sqlite").is_file()


def test_store_events_off(tmp_path: Path) -> None:
    settings = Settings(store_events=False, history={"path": tmp_path})
    with watch(settings=settings) as w:
        w.record_response("https://example.com/", status=200)
    assert w.flushed_events == 1
    with HistoryStore(tmp_path) as store:
        assert store.event_count(w.run.run_id) == 0
        assert store.get_run(w.run.run_id) is not None


def test_unopenable_history_fails_open(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    settings = Settings(history={"path": blocker / "sub"})
    with watch(settings=settings) as w:
        assert w.record_response("https://example.com/", status=200) is not None
    assert w.store is None
    assert w.history_failed
    assert w.run.counts.monitor_errors == 1
    assert w.run.status == RunStatus.FINISHED


def test_write_failure_marks_history_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = Watch(Settings(history={"path": tmp_path}))
    w.start()
    assert w.store is not None

    def broken(_events: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(w.store, "write_events", broken)
    w.record_response("https://example.com/", status=200)
    assert w.flush() == 0
    assert w.history_failed
    w.finish()
    assert w.run.counts.monitor_errors == 1


def test_prune_runs_every_twentieth_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "PRUNE_EVERY_RUNS", 2)
    calls: list[int] = []
    original = HistoryStore.prune

    def spy(self: HistoryStore, *, keep_runs: int, keep_event_days: int) -> dict[str, int]:
        calls.append(1)
        return original(self, keep_runs=keep_runs, keep_event_days=keep_event_days)

    monkeypatch.setattr(HistoryStore, "prune", spy)
    for _ in range(4):
        with watch(settings=Settings(history={"path": tmp_path})):
            pass
    assert len(calls) == 2


def test_attach_without_store(tmp_path: Path) -> None:
    blocker = tmp_path / "f"
    blocker.write_text("x", encoding="utf-8")
    w = Watch.attach("abc", Settings(history={"path": blocker}))
    assert w.attached
    assert w.run.run_id == "abc"
    assert w.store is None
    w.record_response("https://example.com/", status=200)
    assert w.finish().run_id == "abc"
