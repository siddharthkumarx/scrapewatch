"""The run lifecycle: ``watch()`` and the ``Watch`` object every integration talks to.

This module is the only place that wires history, cost, guard, checks, cleaning, report and
alerts together. Integrations call ``Watch.record_response`` / ``Watch.record_record`` and
nothing else. Every public method on ``Watch`` fails open: it catches its own exceptions,
increments ``counts.monitor_errors`` and returns.
"""

from __future__ import annotations

import functools
import logging
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

from scrapewatch._version import __version__
from scrapewatch.config import Settings, config_hash, load_settings
from scrapewatch.models import (
    BytesSource,
    ErrorInfo,
    GuardLabel,
    GuardResult,
    Record,
    ResourceType,
    ResponseEvent,
    Run,
    RunStatus,
    Severity,
    Verdict,
    VerdictStatus,
)
from scrapewatch.redact import cookie_names, redact_headers, redact_url

logger = logging.getLogger("scrapewatch")

FLUSH_EVERY_EVENTS = 200
FLUSH_EVERY_SECONDS = 2.0
_OVERHEAD_SAMPLES = 10_000
_LOG_INTERVAL_SECONDS = 60.0

T = TypeVar("T")


@dataclass
class ResponseContext:
    """In-memory view of one response while hooks run. Never stored."""

    body: bytes | str | None
    raw_url: str
    raw_final_url: str | None
    raw_headers: Mapping[str, str]
    meta: Mapping[str, Any] = field(default_factory=dict)


ResponseHook = Callable[["Watch", ResponseEvent, ResponseContext], None]
RecordHook = Callable[["Watch", Record], None]


class _RateLimitedLog:
    """Log a monitor failure once per (where, exception type) per minute."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def warn(self, where: str, exc: BaseException) -> None:
        key = (where, type(exc).__name__)
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < _LOG_INTERVAL_SECONDS:
                return
            self._last[key] = now
        logger.warning(
            "scrapewatch monitoring error in %s: %s: %s (counted, scraper unaffected)",
            where,
            type(exc).__name__,
            exc,
        )


def _guarded(where: str) -> Callable[[Callable[..., T]], Callable[..., T | None]]:
    """Wrap a ``Watch`` method so it fails open.

    Any ``Exception`` is counted in ``counts.monitor_errors``, logged (rate limited) and
    swallowed; the method returns None.
    """

    def deco(fn: Callable[..., T]) -> Callable[..., T | None]:
        @functools.wraps(fn)
        def wrapper(self: Watch, *args: Any, **kwargs: Any) -> T | None:
            try:
                return fn(self, *args, **kwargs)
            except Exception as exc:
                self._monitor_error(where, exc)
                return None

        return wrapper

    return deco


def _domain(url: str) -> str:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    return (host or "").lower() or "unknown"


class Watch:
    """A live run. Create it through :func:`watch`."""

    def __init__(self, settings: Settings, *, integration: str = "manual") -> None:
        """Initialise a run without starting it (``start()`` opens history and the clock)."""
        self.settings = settings
        self.run = Run(
            name=settings.name,
            integration=integration,
            scrapewatch_version=__version__,
            config_hash=config_hash(settings),
        )
        self._lock = threading.RLock()
        self._log = _RateLimitedLog()
        self._started = False
        self._finished = False
        self._queue: list[ResponseEvent] = []
        self._last_flush = time.monotonic()
        self._overhead_ms: deque[float] = deque(maxlen=_OVERHEAD_SAMPLES)
        self._response_hooks: list[tuple[str, ResponseHook]] = []
        self._record_hooks: list[tuple[str, RecordHook]] = []
        self.flushed_events = 0

    # --- lifecycle ----------------------------------------------------------------------
    def start(self) -> None:
        """Open the history store, load rules and baselines, mark the run running."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self.run.started_at = datetime.now(tz=timezone.utc)
            self.run.status = RunStatus.RUNNING

    # --- responses ----------------------------------------------------------------------
    def record_response(
        self,
        url: str,
        *,
        status: int | None,
        headers: Mapping[str, str] | None = None,
        body: bytes | str | None = None,
        wire_bytes: int | None = None,
        body_bytes: int | None = None,
        bytes_source: str = "unknown",
        final_url: str | None = None,
        method: str = "GET",
        elapsed_ms: float | None = None,
        resource_type: str = "document",
        proxy: str | None = None,
        attempt: int = 1,
        retry_of: str | None = None,
        error: BaseException | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> ResponseEvent | None:
        """Build, classify, cost and store one response event.

        ``body`` is only inspected (first ``guard.body_window_bytes``), never stored.
        Returns the event, or None when monitoring failed (the failure is counted).
        """
        return self._record_response(
            url,
            status=status,
            headers=headers,
            body=body,
            wire_bytes=wire_bytes,
            body_bytes=body_bytes,
            bytes_source=bytes_source,
            final_url=final_url,
            method=method,
            elapsed_ms=elapsed_ms,
            resource_type=resource_type,
            proxy=proxy,
            attempt=attempt,
            retry_of=retry_of,
            error=error,
            meta=meta,
        )

    @_guarded("record_response")
    def _record_response(
        self,
        url: str,
        *,
        status: int | None,
        headers: Mapping[str, str] | None,
        body: bytes | str | None,
        wire_bytes: int | None,
        body_bytes: int | None,
        bytes_source: str,
        final_url: str | None,
        method: str,
        elapsed_ms: float | None,
        resource_type: str,
        proxy: str | None,
        attempt: int,
        retry_of: str | None,
        error: BaseException | None,
        meta: Mapping[str, Any] | None,
    ) -> ResponseEvent:
        t0 = time.perf_counter()
        raw_headers: Mapping[str, str] = headers if headers is not None else {}
        if body_bytes is None:
            body_bytes = _body_len(body)
        stored_final = redact_url(final_url) if final_url and final_url != url else None
        event = ResponseEvent(
            run_id=self.run.run_id,
            url=redact_url(url),
            final_url=stored_final,
            method=(method or "GET").upper(),
            domain=_domain(url),
            status=status,
            elapsed_ms=elapsed_ms,
            wire_bytes=max(0, int(wire_bytes or 0)),
            body_bytes=max(0, int(body_bytes or 0)),
            bytes_source=_bytes_source(bytes_source),
            resource_type=_resource_type(resource_type),
            proxy=proxy if proxy is not None else self.settings.cost.default_proxy,
            attempt=max(1, int(attempt)),
            retry_of=retry_of,
            headers=redact_headers(raw_headers),
            cookie_names=cookie_names(raw_headers),
            error=_error_info(error),
            integration=self.run.integration,
        )
        if error is not None and status is None:
            event.guard = GuardResult(label=GuardLabel.ERROR, signals=["transport error"])
        ctx = ResponseContext(
            body=body,
            raw_url=url,
            raw_final_url=final_url,
            raw_headers=raw_headers,
            meta=meta or {},
        )
        for name, hook in self._response_hooks:
            try:
                hook(self, event, ctx)
            except Exception as exc:
                self._monitor_error(f"response hook {name}", exc)
                event.monitor_error = _describe(name, exc)
        with self._lock:
            self._count_response(event)
            self._queue.append(event)
            due = len(self._queue) >= FLUSH_EVERY_EVENTS or (
                time.monotonic() - self._last_flush >= FLUSH_EVERY_SECONDS
            )
        if due:
            self.flush()
        self._overhead_ms.append((time.perf_counter() - t0) * 1000.0)
        return event

    def _count_response(self, event: ResponseEvent) -> None:
        c = self.run.counts
        c.responses += 1
        if event.resource_type == ResourceType.DOCUMENT:
            c.document_responses += 1
        label = event.guard.label.value
        c.by_label[label] = c.by_label.get(label, 0) + 1
        status = "null" if event.status is None else str(event.status)
        c.by_status[status] = c.by_status.get(status, 0) + 1
        if event.attempt > 1:
            c.retries += 1
        c.wire_bytes += event.wire_bytes

    # --- records ------------------------------------------------------------------------
    def record_record(
        self,
        data: Mapping[str, Any],
        *,
        source_event_id: str | None = None,
    ) -> Record | None:
        """Clean and check one scraped item; returns the Record with fixes and findings."""
        return self._record_record(data, source_event_id=source_event_id)

    @_guarded("record_record")
    def _record_record(self, data: Mapping[str, Any], *, source_event_id: str | None) -> Record:
        t0 = time.perf_counter()
        record = Record(run_id=self.run.run_id, source_event_id=source_event_id, data=dict(data))
        for name, hook in self._record_hooks:
            try:
                hook(self, record)
            except Exception as exc:
                self._monitor_error(f"record hook {name}", exc)
        with self._lock:
            self._count_record(record)
        self._overhead_ms.append((time.perf_counter() - t0) * 1000.0)
        return record

    def _count_record(self, record: Record) -> None:
        c = self.run.counts
        c.records += 1
        if record.usable:
            c.usable_records += 1
        for finding in record.findings:
            sev = finding.severity.value
            c.findings_by_severity[sev] = c.findings_by_severity.get(sev, 0) + 1
        for fix in record.fixes:
            c.fixes_by_normalizer[fix.normalizer] = c.fixes_by_normalizer.get(fix.normalizer, 0) + 1

    # --- flushing -----------------------------------------------------------------------
    def flush(self) -> int:
        """Write queued events in one batch; returns how many were flushed (0 on failure)."""
        with self._lock:
            batch, self._queue = self._queue, []
            self._last_flush = time.monotonic()
        if not batch:
            return 0
        try:
            written = self._write_events(batch)
        except Exception as exc:
            self._monitor_error("flush", exc)
            return 0
        self.flushed_events += written
        return written

    def _write_events(self, batch: list[ResponseEvent]) -> int:
        """Persist a batch. Without a history store the events are only counted."""
        return len(batch)

    # --- finish -------------------------------------------------------------------------
    def finish(self, *, crashed: bool = False) -> Run:
        """Run batch checks, compute the verdict and cost, write report, store, alert."""
        with self._lock:
            if self._finished:
                return self.run
            self._finished = True
        self._step("flush", self.flush)
        self._step("verdict", lambda: self._finish_verdict(crashed=crashed))
        self.run.finished_at = datetime.now(tz=timezone.utc)
        self.run.status = RunStatus.CRASHED if crashed else RunStatus.FINISHED
        self._step("overhead", self._finish_overhead)
        return self.run

    def _finish_verdict(self, *, crashed: bool) -> None:
        if crashed:
            self.run.verdict = Verdict(
                status=VerdictStatus.HOLD,
                reasons=["The scraper crashed before the run finished."],
                counts={Severity.HOLD.value: 1},
            )

    def _finish_overhead(self) -> None:
        if self._overhead_ms:
            self.run.overhead_ms_p50 = round(statistics.median(self._overhead_ms), 4)

    def _step(self, where: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except Exception as exc:
            self._monitor_error(f"finish {where}", exc)

    # --- helpers ------------------------------------------------------------------------
    def _monitor_error(self, where: str, exc: BaseException) -> None:
        with self._lock:
            self.run.counts.monitor_errors += 1
        self._log.warn(where, exc)

    @property
    def verdict(self) -> Verdict:
        """Verdict so far (final after ``finish``)."""
        return self.run.verdict

    def report_path(self) -> Path | None:
        """Path of the HTML report written by ``finish``, if any."""
        return Path(self.run.report_path) if self.run.report_path else None


def _body_len(body: bytes | str | None) -> int:
    if body is None:
        return 0
    if isinstance(body, str):
        return len(body.encode("utf-8", errors="replace"))
    return len(body)


def _bytes_source(value: str | BytesSource) -> BytesSource:
    try:
        return BytesSource(value)
    except ValueError:
        return BytesSource.UNKNOWN


def _resource_type(value: str | ResourceType) -> ResourceType:
    try:
        return ResourceType(value)
    except ValueError:
        return ResourceType.OTHER


def _error_info(error: BaseException | None) -> ErrorInfo | None:
    if error is None:
        return None
    return ErrorInfo(type=type(error).__name__, message=redact_url(str(error))[:300])


def _describe(where: str, exc: BaseException) -> str:
    return f"{where}: {type(exc).__name__}: {exc}"[:300]


@contextmanager
def watch(
    name: str = "default",
    *,
    rules: str | Path | None = None,
    config: str | Path | None = None,
    settings: Settings | None = None,
    integration: str = "manual",
) -> Iterator[Watch]:
    """The 5-minute path: wrap a scraper run.

    Example::

        with scrapewatch.watch("books", rules="rules.yml") as w:
            ...  # scrape; integrations call w.record_response / w.record_record
        print(w.verdict.status)

    On exit the run is finished (``crashed=True`` if an exception propagated) and the
    exception is re-raised. Monitoring errors never raise.
    """
    if settings is None:
        overrides: dict[str, Any] = {}
        if name != "default":
            overrides["name"] = name
        if rules is not None:
            overrides["rules"] = rules
        settings = load_settings(config, overrides=overrides)
    else:
        updates: dict[str, Any] = {}
        if name != "default":
            updates["name"] = name
        if rules is not None:
            updates["rules"] = Path(rules)
        if updates:
            settings = settings.model_copy(update=updates)
    w = Watch(settings, integration=integration)
    w.start()
    try:
        yield w
    except BaseException:
        w.finish(crashed=True)
        raise
    w.finish()
