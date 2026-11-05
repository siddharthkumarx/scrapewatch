"""History store: runs, events, field statistics and baselines (SQLite by default)."""

from scrapewatch.history.store import HistoryStore, open_store

__all__ = ["HistoryStore", "open_store"]
