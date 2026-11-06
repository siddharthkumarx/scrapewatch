"""SQLite history store. Schema and locking rules: docs/architecture.md "History store"."""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import threading
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

from scrapewatch.errors import HistoryError
from scrapewatch.history.migrations import LATEST, migrate
from scrapewatch.history.stats import FieldStats, StatsAccumulator, value_hash
from scrapewatch.models import (
    Finding,
    GuardLabel,
    Record,
    ResourceType,
    ResponseEvent,
    Run,
    RunStatus,
    VerdictStatus,
)

SCHEMA_SQL_VERSION = LATEST
DB_FILE = "history.sqlite"
BUSY_TIMEOUT_MS = 5000
RUN_ROWS_KEEP_DAYS = 365
SIZE_BASELINE_WEIGHT_CAP = 50  # past samples weigh at most this much against a new run

_DIGITS_RE = re.compile(r"\d+")


def path_pattern(url: str) -> str:
    """Path with digit runs replaced by ``{n}``, at most three segments.

    ``/catalogue/page-2.html`` and ``/catalogue/page-3.html`` share ``/catalogue/page-{n}.html``.
    """
    try:
        path = urlsplit(url).path
    except ValueError:
        path = ""
    segments = [s for s in path.split("/") if s][:3]
    pattern = "/" + "/".join(_DIGITS_RE.sub("{n}", s) for s in segments)
    if path.endswith("/") and segments and len([s for s in path.split("/") if s]) <= 3:
        pattern += "/"
    return pattern


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


class HistoryStore:
    """One SQLite file (WAL mode). Safe for several worker processes writing one run."""

    def __init__(self, path: Path) -> None:
        """Bind to ``path``; nothing is opened until ``open()``."""
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def db_path(self) -> Path:
        """The SQLite file inside the history directory."""
        return self.path / DB_FILE

    def open(self) -> None:
        """Create the file and schema if needed; set WAL and busy_timeout."""
        if self._conn is not None:
            return
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=BUSY_TIMEOUT_MS / 1000,
                check_same_thread=False,
                isolation_level=None,
            )
        except (OSError, sqlite3.Error) as exc:
            raise HistoryError(f"cannot open history at {self.db_path}: {exc}") from exc
        try:
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.isolation_level = "DEFERRED"
            migrate(conn)
        except HistoryError:
            conn.close()
            raise
        except sqlite3.Error as exc:
            conn.close()
            raise HistoryError(f"cannot prepare history at {self.db_path}: {exc}") from exc
        self._conn = conn

    def close(self) -> None:
        """Flush pending writes and close the connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

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

    @property
    def conn(self) -> sqlite3.Connection:
        """The open connection; raises HistoryError when closed."""
        if self._conn is None:
            raise HistoryError("history store is not open")
        return self._conn

    def _write(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        with self._lock:
            conn = self.conn
            try:
                with conn:
                    cur = conn.executemany(sql, rows)
                    return max(cur.rowcount, 0)
            except sqlite3.Error as exc:
                raise HistoryError(f"history write failed: {exc}") from exc

    def _read(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            conn = self.conn
            conn.row_factory = sqlite3.Row
            try:
                return list(conn.execute(sql, params))
            except sqlite3.Error as exc:
                raise HistoryError(f"history read failed: {exc}") from exc

    # --- writes -----------------------------------------------------------------------
    def start_run(self, run: Run) -> None:
        """Insert the run row (status running)."""
        self._write(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [self._run_row(run)],
        )

    def write_events(self, events: Iterable[ResponseEvent]) -> int:
        """Batch-insert events in one transaction; returns the number written."""
        rows = [
            (
                ev.event_id,
                ev.run_id,
                _iso(ev.ts),
                ev.domain,
                path_pattern(ev.url),
                ev.status,
                ev.guard.label.value,
                ev.resource_type.value,
                ev.proxy,
                ev.attempt,
                ev.wire_bytes,
                ev.body_bytes,
                ev.cost,
                int(ev.cache.unchanged),
                ev.model_dump_json(),
            )
            for ev in events
        ]
        if not rows:
            return 0
        self._write("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def write_record_stats(self, run_id: str, records: Iterable[Record]) -> None:
        """Store per-field statistics and dedupe/value hashes for a batch of records."""
        acc = StatsAccumulator()
        hashes: dict[str, str] = {}
        for rec in records:
            acc.add(rec.data)
            if rec.dedupe_key and rec.usable:
                hashes.setdefault(rec.dedupe_key, value_hash(rec.data))
        self.write_field_stats(run_id, acc.result())
        self.write_value_hashes(run_id, hashes)

    def write_field_stats(self, run_id: str, stats: dict[str, FieldStats]) -> None:
        """Replace the field statistics of a run."""
        rows = [
            (
                run_id,
                name,
                s.count,
                s.null_count,
                s.distinct_count,
                s.numeric_count,
                s.p05,
                s.p50,
                s.p95,
                s.mean,
                json.dumps(s.hist) if s.hist is not None else None,
                json.dumps(s.top_values),
            )
            for name, s in stats.items()
        ]
        with self._lock:
            self._write("DELETE FROM field_stats WHERE run_id = ?", [(run_id,)])
            self._write("INSERT INTO field_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def write_value_hashes(self, run_id: str, hashes: dict[str, str]) -> None:
        """Store dedupe_key -> value hash for a run."""
        self._write(
            "INSERT OR REPLACE INTO record_hashes VALUES (?,?,?)",
            [(run_id, k, v) for k, v in hashes.items()],
        )

    def write_findings(self, findings: Iterable[Finding]) -> None:
        """Store findings."""
        self._write(
            "INSERT OR REPLACE INTO findings VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    f.finding_id,
                    f.run_id,
                    f.scope.value,
                    f.check,
                    f.severity.value,
                    f.field,
                    f.message,
                    f.model_dump_json(),
                )
                for f in findings
            ],
        )

    def finish_run(self, run: Run) -> None:
        """Update the run row with the final summary (status, verdict, cost, counts)."""
        self._write(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [self._run_row(run)],
        )

    @staticmethod
    def _run_row(run: Run) -> tuple[Any, ...]:
        return (
            run.run_id,
            run.name,
            _iso(run.started_at),
            _iso(run.finished_at),
            run.status.value,
            run.verdict.status.value if run.status != RunStatus.RUNNING else None,
            run.counts.records,
            run.counts.usable_records,
            run.cost.total,
            run.scrapewatch_version,
            run.rules_version,
            run.config_hash,
            run.model_dump_json(),
        )

    def update_size_baselines(self, run_id: str, *, now: datetime | None = None) -> int:
        """Fold this run's OK document responses into ``size_baseline``; returns rows updated.

        Per (domain, path pattern) the run's median body size is blended with the stored p50,
        weighting past samples by ``min(n, 50)`` so the baseline follows slow site changes.
        """
        rows = self._read(
            "SELECT domain, path_pattern, body_bytes FROM events WHERE run_id = ? AND label = ? "
            "AND resource_type = ? AND body_bytes > 0",
            (run_id, GuardLabel.OK.value, ResourceType.DOCUMENT.value),
        )
        groups: dict[tuple[str, str], list[int]] = {}
        for r in rows:
            groups.setdefault((r["domain"], r["path_pattern"]), []).append(int(r["body_bytes"]))
        stamp = _iso(now or datetime.now(tz=timezone.utc))
        out = []
        for (domain, pattern), sizes in sorted(groups.items()):
            run_p50 = float(statistics.median(sizes))
            k = len(sizes)
            old = self.size_baseline(domain, pattern)
            if old is None:
                p50, n = run_p50, k
            else:
                w = min(old[1], SIZE_BASELINE_WEIGHT_CAP)
                p50, n = (old[0] * w + run_p50 * k) / (w + k), old[1] + k
            out.append((domain, pattern, n, round(p50), stamp))
        self._write("INSERT OR REPLACE INTO size_baseline VALUES (?,?,?,?,?)", out)
        return len(out)

    # --- reads ------------------------------------------------------------------------
    def get_run(self, run_id: str) -> Run | None:
        """Return one run summary."""
        rows = self._read("SELECT summary_json FROM runs WHERE run_id = ?", (run_id,))
        return Run.model_validate_json(rows[0]["summary_json"]) if rows else None

    def list_runs(self, name: str | None = None, *, limit: int = 20) -> list[Run]:
        """Most recent runs first."""
        if name is None:
            rows = self._read(
                "SELECT summary_json FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            )
        else:
            rows = self._read(
                "SELECT summary_json FROM runs WHERE name = ? ORDER BY started_at DESC LIMIT ?",
                (name, limit),
            )
        return [Run.model_validate_json(r["summary_json"]) for r in rows]

    def baseline_runs(self, name: str, *, n: int, passing_only: bool = True) -> list[Run]:
        """The last ``n`` finished runs with verdict pass (used for batch checks)."""
        sql = "SELECT summary_json FROM runs WHERE name = ? AND status = ?"
        params: list[Any] = [name, RunStatus.FINISHED.value]
        if passing_only:
            sql += " AND verdict = ?"
            params.append(VerdictStatus.PASS.value)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(n)
        return [Run.model_validate_json(r["summary_json"]) for r in self._read(sql, params)]

    def field_stats(self, run_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Per-field stats of the given runs: ``{field: {run_id: FieldStats}}``.

        Aggregation across runs (medians, unions) happens in ``compute_baseline``.
        """
        if not run_ids:
            return {}
        marks = ",".join("?" * len(run_ids))
        rows = self._read(
            f"SELECT * FROM field_stats WHERE run_id IN ({marks}) ORDER BY field",  # noqa: S608
            list(run_ids),
        )
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            out.setdefault(r["field"], {})[r["run_id"]] = FieldStats(
                count=r["count"],
                null_count=r["null_count"],
                distinct_count=r["distinct_count"],
                numeric_count=r["numeric_count"],
                p05=r["p05"],
                p50=r["p50"],
                p95=r["p95"],
                mean=r["mean"],
                hist=json.loads(r["hist_json"]) if r["hist_json"] else None,
                top_values=json.loads(r["top_values_json"] or "[]"),
            )
        return out

    def value_hashes(self, run_id: str) -> dict[str, str]:
        """dedupe_key -> value hash for the records of a run (change-share check)."""
        rows = self._read(
            "SELECT dedupe_key, value_hash FROM record_hashes WHERE run_id = ?", (run_id,)
        )
        return {r["dedupe_key"]: r["value_hash"] for r in rows}

    def size_baseline(self, domain: str, path_pattern: str) -> tuple[int, int] | None:
        """(p50 body bytes, sample size) of past OK document responses, or None."""
        rows = self._read(
            "SELECT p50_bytes, n FROM size_baseline WHERE domain = ? AND path_pattern = ?",
            (domain, path_pattern),
        )
        return (int(rows[0]["p50_bytes"]), int(rows[0]["n"])) if rows else None

    def events(self, run_id: str) -> list[ResponseEvent]:
        """All events of a run."""
        rows = self._read(
            "SELECT event_json FROM events WHERE run_id = ? ORDER BY ts, rowid", (run_id,)
        )
        return [ResponseEvent.model_validate_json(r["event_json"]) for r in rows]

    def findings(self, run_id: str) -> list[Finding]:
        """All stored findings of a run."""
        rows = self._read(
            "SELECT finding_json FROM findings WHERE run_id = ? ORDER BY rowid", (run_id,)
        )
        return [Finding.model_validate_json(r["finding_json"]) for r in rows]

    def run_count(self) -> int:
        """Number of run rows in the store."""
        return int(self._read("SELECT COUNT(*) AS n FROM runs")[0][0])

    def event_count(self, run_id: str) -> int:
        """Number of stored events of a run (from every process that wrote it)."""
        return int(self._read("SELECT COUNT(*) AS n FROM events WHERE run_id = ?", (run_id,))[0][0])

    def response_counts(self, run_id: str) -> dict[str, Any]:
        """Response counters of a run aggregated from the events table (all writers)."""
        total = self._read(
            "SELECT COUNT(*) AS n, COALESCE(SUM(resource_type = 'document'), 0) AS docs, "
            "COALESCE(SUM(attempt > 1), 0) AS retries, COALESCE(SUM(wire_bytes), 0) AS wire "
            "FROM events WHERE run_id = ?",
            (run_id,),
        )[0]
        labels = self._read(
            "SELECT label, COUNT(*) AS n FROM events WHERE run_id = ? GROUP BY label "
            "ORDER BY label",
            (run_id,),
        )
        statuses = self._read(
            "SELECT status, COUNT(*) AS n FROM events WHERE run_id = ? GROUP BY status "
            "ORDER BY status",
            (run_id,),
        )
        return {
            "responses": int(total["n"]),
            "document_responses": int(total["docs"]),
            "retries": int(total["retries"]),
            "wire_bytes": int(total["wire"]),
            "by_label": {r["label"]: int(r["n"]) for r in labels},
            "by_status": {
                ("null" if r["status"] is None else str(r["status"])): int(r["n"]) for r in statuses
            },
        }

    # --- maintenance ------------------------------------------------------------------
    def prune(
        self, *, keep_runs: int, keep_event_days: int, now: datetime | None = None
    ) -> dict[str, int]:
        """Apply retention; returns counts of deleted rows per table.

        Events older than ``keep_event_days`` go; field stats and record hashes are kept for the
        ``keep_runs`` most recent runs of each name; run rows (and their findings) older than
        365 days go. Run rows are otherwise kept.
        """
        now = now or datetime.now(tz=timezone.utc)
        event_cutoff = _iso(now - timedelta(days=keep_event_days))
        run_cutoff = _iso(now - timedelta(days=RUN_ROWS_KEEP_DAYS))
        old_runs = [
            r["run_id"]
            for r in self._read(
                "SELECT run_id FROM (SELECT run_id, ROW_NUMBER() OVER "
                "(PARTITION BY name ORDER BY started_at DESC) AS rn FROM runs) WHERE rn > ?",
                (keep_runs,),
            )
        ]
        expired = [
            r["run_id"]
            for r in self._read("SELECT run_id FROM runs WHERE started_at < ?", (run_cutoff,))
        ]
        counts = {"events": 0, "field_stats": 0, "record_hashes": 0, "findings": 0, "runs": 0}
        with self._lock:
            counts["events"] = self._write("DELETE FROM events WHERE ts < ?", [(event_cutoff,)])
            for run_id in sorted(set(old_runs) | set(expired)):
                counts["field_stats"] += self._write(
                    "DELETE FROM field_stats WHERE run_id = ?", [(run_id,)]
                )
                counts["record_hashes"] += self._write(
                    "DELETE FROM record_hashes WHERE run_id = ?", [(run_id,)]
                )
            for run_id in expired:
                counts["findings"] += self._write(
                    "DELETE FROM findings WHERE run_id = ?", [(run_id,)]
                )
                counts["events"] += self._write("DELETE FROM events WHERE run_id = ?", [(run_id,)])
                counts["runs"] += self._write("DELETE FROM runs WHERE run_id = ?", [(run_id,)])
        return counts


def open_store(path: Path | str) -> HistoryStore:
    """Open (and migrate) a store at ``path``; ``path`` is the directory, file is history.sqlite."""
    store = HistoryStore(Path(path))
    store.open()
    return store
