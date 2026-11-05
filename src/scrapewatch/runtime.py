"""The run lifecycle: ``watch()`` and the ``Watch`` object every integration talks to.

This module is the only place that wires history, cost, guard, checks, cleaning, report and
alerts together. Integrations call ``Watch.record_response`` / ``Watch.record_record`` and
nothing else. Every public method on ``Watch`` fails open: it catches its own exceptions,
increments ``counts.monitor_errors`` and returns.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scrapewatch.config import Settings
from scrapewatch.models import Record, ResponseEvent, Run, Verdict


def _todo(item: str) -> None:
    raise NotImplementedError(item)


class Watch:
    """A live run. Create it through :func:`watch`."""

    def __init__(self, settings: Settings, *, integration: str = "manual") -> None:
        """Initialise a run without starting it (``start()`` opens history and the clock)."""
        self.settings = settings
        self.run = Run(name=settings.name, integration=integration)

    def start(self) -> None:
        """Open the history store, load rules and baselines, mark the run running."""
        raise NotImplementedError("build-progress item 1")

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
        raise NotImplementedError("build-progress item 1")

    def record_record(
        self,
        data: Mapping[str, Any],
        *,
        source_event_id: str | None = None,
    ) -> Record | None:
        """Clean and check one scraped item; returns the Record with fixes and findings."""
        raise NotImplementedError("build-progress item 1")

    def finish(self, *, crashed: bool = False) -> Run:
        """Run batch checks, compute the verdict and cost, write report, store, alert."""
        raise NotImplementedError("build-progress item 1")

    @property
    def verdict(self) -> Verdict:
        """Verdict so far (final after ``finish``)."""
        return self.run.verdict

    def report_path(self) -> Path | None:
        """Path of the HTML report written by ``finish``, if any."""
        return Path(self.run.report_path) if self.run.report_path else None


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
    _todo("build-progress item 1")
    yield Watch(Settings())  # pragma: no cover
