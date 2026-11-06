"""Schema migrations for the SQLite history store.

Each migration is a list of SQL statements applied in one transaction and recorded in the
``migrations`` table; ``meta.schema_version`` holds the latest applied version. A file written
by a newer scrapewatch (higher version than :data:`LATEST`) is refused with ``HistoryError``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from scrapewatch.errors import HistoryError

MIGRATIONS: dict[int, list[str]] = {
    1: [
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
        """CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, name TEXT, started_at TEXT, finished_at TEXT, status TEXT,
            verdict TEXT, records INTEGER, usable_records INTEGER, cost_total REAL,
            scrapewatch_version TEXT, rules_version TEXT, config_hash TEXT, summary_json TEXT)""",
        """CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY, run_id TEXT, ts TEXT, domain TEXT, path_pattern TEXT,
            status INTEGER, label TEXT, resource_type TEXT, proxy TEXT, attempt INTEGER,
            wire_bytes INTEGER, body_bytes INTEGER, cost REAL, unchanged INTEGER,
            event_json TEXT)""",
        """CREATE TABLE IF NOT EXISTS field_stats (
            run_id TEXT, field TEXT, count INTEGER, null_count INTEGER, distinct_count INTEGER,
            numeric_count INTEGER, p05 REAL, p50 REAL, p95 REAL, mean REAL,
            hist_json TEXT, top_values_json TEXT, PRIMARY KEY (run_id, field))""",
        """CREATE TABLE IF NOT EXISTS record_hashes (
            run_id TEXT, dedupe_key TEXT, value_hash TEXT, PRIMARY KEY (run_id, dedupe_key))""",
        """CREATE TABLE IF NOT EXISTS findings (
            finding_id TEXT PRIMARY KEY, run_id TEXT, scope TEXT, check_id TEXT,
            severity TEXT, field TEXT, message TEXT, finding_json TEXT)""",
        """CREATE TABLE IF NOT EXISTS size_baseline (
            domain TEXT, path_pattern TEXT, n INTEGER, p50_bytes INTEGER, updated_at TEXT,
            PRIMARY KEY (domain, path_pattern))""",
        """CREATE TABLE IF NOT EXISTS cache_entries (
            url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, body_sha256 TEXT,
            seen_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_events_domain ON events(domain, path_pattern, label)",
        "CREATE INDEX IF NOT EXISTS idx_runs_name ON runs(name, started_at)",
        "CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id)",
    ],
}
LATEST = max(MIGRATIONS)


def current_version(conn: sqlite3.Connection) -> int:
    """Schema version recorded in the file (0 for a new file)."""
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not has_meta:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations in order; returns the versions applied."""
    version = current_version(conn)
    if version > LATEST:
        raise HistoryError(
            f"history file has schema version {version}; this scrapewatch reads up to {LATEST}. "
            "Upgrade scrapewatch to open it."
        )
    applied: list[int] = []
    for target in sorted(v for v in MIGRATIONS if v > version):
        now = datetime.now(tz=timezone.utc).isoformat()
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT)"
            )
            for stmt in MIGRATIONS[target]:
                conn.execute(stmt)
            conn.execute("INSERT OR REPLACE INTO migrations VALUES (?, ?)", (target, now))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(target),))
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('created_at', ?)", (now,))
        applied.append(target)
    return applied
