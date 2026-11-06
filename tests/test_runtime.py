import logging
from pathlib import Path

import pytest

import scrapewatch
from scrapewatch import runtime
from scrapewatch.config import Settings
from scrapewatch.models import (
    BytesSource,
    GuardLabel,
    ResourceType,
    ResponseEvent,
    RunStatus,
    VerdictStatus,
)
from scrapewatch.runtime import ResponseContext, Watch, watch


def _watch() -> Watch:
    w = Watch(Settings(name="t"), integration="test")
    w.start()
    return w


def test_record_response_builds_redacted_event() -> None:
    w = _watch()
    ev = w.record_response(
        "https://user:pw@Books.ToScrape.com/catalogue/page-2.html?token=abc",
        status=200,
        headers={"Content-Type": "text/html", "Set-Cookie": "sid=1", "Authorization": "x"},
        body=b"<html>" + b"x" * 100,
        wire_bytes=50,
        bytes_source="measured",
        proxy="datacenter",
        elapsed_ms=12.5,
    )
    assert isinstance(ev, ResponseEvent)
    assert ev.url == "https://Books.ToScrape.com/catalogue/page-2.html?token=[redacted]"
    assert ev.domain == "books.toscrape.com"
    assert ev.headers == {"content-type": "text/html"}
    assert ev.cookie_names == ["sid"]
    assert ev.body_bytes == 106
    assert ev.wire_bytes == 50
    assert ev.bytes_source == BytesSource.MEASURED
    assert ev.integration == "test"
    assert ev.run_id == w.run.run_id
    assert "body" not in ev.model_dump()
    c = w.run.counts
    assert (c.responses, c.document_responses, c.wire_bytes) == (1, 1, 50)
    assert c.by_label == {"empty": 1}  # a 106-byte HTML page is an empty shell
    assert c.by_status == {"200": 1}


def test_defaults_and_coercions() -> None:
    w = _watch()
    ev = w.record_response(
        "https://example.com/a",
        status=None,
        body="héllo",
        resource_type="weird",
        bytes_source="nonsense",
        method="post",
        attempt=0,
        final_url="https://example.com/a",
    )
    assert ev is not None
    assert ev.body_bytes == len("héllo".encode())
    assert ev.resource_type == ResourceType.OTHER
    assert ev.bytes_source == BytesSource.UNKNOWN
    assert ev.method == "POST"
    assert ev.attempt == 1
    assert ev.final_url is None  # same as url
    assert ev.proxy == "direct"  # settings.cost.default_proxy
    assert w.run.counts.document_responses == 0
    assert w.run.counts.by_status == {"null": 1}


def test_transport_error_event() -> None:
    w = _watch()
    ev = w.record_response(
        "https://example.com/x", status=None, error=TimeoutError("timed out ?token=abc")
    )
    assert ev is not None
    assert ev.guard.label == GuardLabel.ERROR
    assert ev.error is not None
    assert ev.error.type == "TimeoutError"
    assert "abc" not in ev.error.message


def test_retry_counted() -> None:
    w = _watch()
    first = w.record_response("https://example.com/x", status=503)
    assert first is not None
    w.record_response("https://example.com/x", status=200, attempt=2, retry_of=first.event_id)
    assert w.run.counts.retries == 1


def test_hook_that_raises_is_counted_and_event_kept(caplog: pytest.LogCaptureFixture) -> None:
    w = _watch()

    def boom(_w: Watch, _ev: ResponseEvent, _ctx: ResponseContext) -> None:
        raise RuntimeError("hook failed")

    w._response_hooks.append(("boom", boom))
    with caplog.at_level(logging.WARNING, logger="scrapewatch"):
        ev = w.record_response("https://example.com/", status=200, body=b"<html></html>")
        w.record_response("https://example.com/2", status=200)
    assert ev is not None
    assert ev.monitor_error is not None
    assert "hook failed" in ev.monitor_error
    assert w.run.counts.monitor_errors == 2
    assert w.run.counts.responses == 2
    # rate limited: one log line for the same (place, exception type)
    assert len([r for r in caplog.records if "hook failed" in r.getMessage()]) == 1
    w.finish()
    assert w.flushed_events == 2


def test_failure_while_building_event_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()

    def bad(_headers: object) -> dict[str, str]:
        raise ValueError("cannot redact")

    monkeypatch.setattr(runtime, "redact_headers", bad)
    assert w.record_response("https://example.com/", status=200) is None
    assert w.run.counts.monitor_errors == 1
    assert w.run.counts.responses == 0


def test_record_hook_failure_counted() -> None:
    w = _watch()

    def boom(_w: Watch, _rec: object) -> None:
        raise RuntimeError("x")

    w._record_hooks.append(("boom", boom))
    rec = w.record_record({"a": 1})
    assert rec is not None
    assert w.run.counts.monitor_errors == 1
    assert w.run.counts.records == 1


def test_record_record_counts() -> None:
    w = _watch()
    rec = w.record_record({"title": "A", "price": 1.0}, source_event_id="e1")
    assert rec is not None
    assert rec.data == {"title": "A", "price": 1.0}
    assert rec.source_event_id == "e1"
    assert rec.usable
    assert w.run.counts.records == 1
    assert w.run.counts.usable_records == 1
    assert w.record_record(None) is None  # type: ignore[arg-type]
    assert w.run.counts.monitor_errors == 1


def test_flush_queue_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "FLUSH_EVERY_EVENTS", 3)
    monkeypatch.setattr(runtime, "FLUSH_EVERY_SECONDS", 3600.0)
    w = _watch()
    batches: list[int] = []

    def write(batch: list[ResponseEvent]) -> int:
        batches.append(len(batch))
        return len(batch)

    monkeypatch.setattr(w, "_write_events", write)
    for i in range(7):
        w.record_response(f"https://example.com/{i}", status=200)
    assert batches == [3, 3]
    w.finish()
    assert batches == [3, 3, 1]
    assert w.flushed_events == 7


def test_flush_failure_is_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()

    def write(_batch: list[ResponseEvent]) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(w, "_write_events", write)
    w.record_response("https://example.com/", status=200)
    assert w.flush() == 0
    assert w.flush() == 0  # nothing queued
    assert w.run.counts.monitor_errors == 1


def test_finish_is_idempotent_and_sets_status() -> None:
    w = _watch()
    w.start()  # second start is a no-op
    w.record_response("https://example.com/", status=200)
    run = w.finish()
    assert run.status == RunStatus.FINISHED
    assert run.finished_at is not None
    assert run.overhead_ms_p50 is not None
    assert run.scrapewatch_version == scrapewatch.__version__
    assert run.config_hash is not None
    assert w.finish() is run


def test_finish_step_failure_still_returns_run(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    w.record_response("https://example.com/", status=200)

    def broken() -> None:
        raise RuntimeError("overhead broke")

    monkeypatch.setattr(w, "_finish_overhead", broken)
    run = w.finish()
    assert run.status == RunStatus.FINISHED
    assert run.counts.monitor_errors == 1


def test_watch_reraises_and_marks_crashed() -> None:
    holder: list[Watch] = []

    def scraper() -> None:
        with watch(settings=Settings(name="crashy")) as w:
            holder.append(w)
            w.record_response("https://example.com/", status=200)
            raise KeyError("scraper bug")

    with pytest.raises(KeyError, match="scraper bug"):
        scraper()
    run = holder[0].run
    assert run.status == RunStatus.CRASHED
    assert run.verdict.status == VerdictStatus.HOLD
    assert run.finished_at is not None


def test_watch_normal_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrapewatch.yml").write_text("name: fromfile\n", encoding="utf-8")
    with watch(integration="manual") as w:
        w.record_record({"a": 1})
    assert w.run.name == "fromfile"
    assert w.run.status == RunStatus.FINISHED
    assert w.verdict.status == VerdictStatus.PASS
    report = w.report_path()
    assert report is not None
    assert report.parent == Path(".scrapewatch", "reports", "fromfile")
    assert report.is_file()
    assert report.with_suffix(".json").is_file()
    with watch("explicit", rules=tmp_path / "rules.yml") as w2:
        pass
    assert w2.run.name == "explicit"
    assert w2.settings.rules == tmp_path / "rules.yml"


def test_watch_with_settings_object() -> None:
    with watch("renamed", settings=Settings(name="orig"), rules="r.yml") as w:
        pass
    assert w.run.name == "renamed"
    assert w.settings.rules == Path("r.yml")
    with watch(settings=Settings(name="orig")) as w2:
        pass
    assert w2.run.name == "orig"


def test_unparseable_url_domain() -> None:
    w = _watch()
    ev = w.record_response("http://[::1/broken", status=200)
    assert ev is not None
    assert ev.domain == "unknown"
