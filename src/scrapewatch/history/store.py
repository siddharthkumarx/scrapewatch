"""SQLite history store. Schema and locking rules: docs/architecture.md "History store"."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

from scrapewatch.models import Finding, Record, ResponseEvent, Run

SCHEMA_SQL_VERSION = 1


class HistoryStore:
    """One SQLite file (WAL mode). Safe for several worker processes writing one run."""

    def __init__(self, path: Path) -> None:
        """Bind to ``path``; nothing is opened until ``open()``."""
        self.path = path

    def open(self) -> None:
        """Create the file and schema if needed; set WAL and busy_timeout."""
        raise NotImplementedError("build-progress item 2")

    def close(self) -> None:
        """Flush pending writes and close the connection."""
        raise NotImplementedError("build-progress item 2")

    def __enter__(self) -> HistoryStore:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- writes -----------------------------------------------------------------------
    def start_run(self, run: Run) -> None:
        """Insert the run row (status running)."""
        raise NotImplementedError("build-progress item 2")

    def write_events(self, events: Iterable[ResponseEvent]) -> int:
        """Batch-insert events; returns the number written."""
        raise NotImplementedError("build-progress item 2")

    def write_record_stats(self, run_id: str, records: Iterable[Record]) -> None:
        """Store per-field statistics and dedupe/value hashes for a batch of records."""
        raise NotImplementedError("build-progress item 2")

    def write_findings(self, findings: Iterable[Finding]) -> None:
        """Store findings."""
        raise NotImplementedError("build-progress item 2")

    def finish_run(self, run: Run) -> None:
        """Update the run row with the final summary (status, verdict, cost, counts)."""
        raise NotImplementedError("build-progress item 2")

    # --- reads ------------------------------------------------------------------------
    def get_run(self, run_id: str) -> Run | None:
        """Return one run summary."""
        raise NotImplementedError("build-progress item 2")

    def list_runs(self, name: str | None = None, *, limit: int = 20) -> list[Run]:
        """Most recent runs first."""
        raise NotImplementedError("build-progress item 2")

    def baseline_runs(self, name: str, *, n: int, passing_only: bool = True) -> list[Run]:
        """The last ``n`` finished runs with verdict pass (used for batch checks)."""
        raise NotImplementedError("build-progress item 2")

    def field_stats(self, run_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Per-field stats over the given runs (count, null_rate, p05/p50/p95, top values)."""
        raise NotImplementedError("build-progress item 2")

    def value_hashes(self, run_id: str) -> dict[str, str]:
        """dedupe_key -> value hash for the records of a run (change-share check)."""
        raise NotImplementedError("build-progress item 2")

    def size_baseline(self, domain: str, path_pattern: str) -> tuple[int, int] | None:
        """(p50 body bytes, sample size) of past OK document responses, or None."""
        raise NotImplementedError("build-progress item 2")

    def events(self, run_id: str) -> list[ResponseEvent]:
        """All events of a run."""
        raise NotImplementedError("build-progress item 2")

    # --- maintenance ------------------------------------------------------------------
    def prune(self, *, keep_runs: int, keep_event_days: int) -> dict[str, int]:
        """Apply retention; returns counts of deleted rows per table."""
        raise NotImplementedError("build-progress item 2")


def open_store(path: Path | str) -> HistoryStore:
    """Open (and migrate) a store at ``path``; ``path`` is the directory, file is history.sqlite."""
    raise NotImplementedError("build-progress item 2")
