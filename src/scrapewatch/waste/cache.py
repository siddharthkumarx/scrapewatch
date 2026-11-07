"""Conditional-request cache: stores ETag / Last-Modified / body hash per URL in history.

An unchanged page (a 304, or a body with the same SHA-256 as last time) is reported as
``cache.unchanged`` on its event. A 304 to a conditional request is a conditional hit (cheap,
``waste.conditional_hits``); a full 200 with the same body is an unchanged refetch (bytes paid
for nothing, ``waste.unchanged_refetch_*``). Items scraped from an unchanged page are recorded
with ``w.record_record(item, unchanged=True)`` so the changed-share check leaves them out.

With httpx or requests, pass the cache to the integration and ask the response::

    with ConditionalCache(w.settings.history.path) as cache:
        client = httpx.Client(event_hooks=sw_httpx.hooks(w, cache=cache))
        r = client.get(url)               # If-None-Match / If-Modified-Since added
        if sw_httpx.unchanged(r):         # 304, or the same body as last time
            ...                           # reuse what you stored for this page

Entries live in the ``cache_entries`` table of ``history.sqlite`` (its own connection), keyed by
the canonical URL with secret-looking query values redacted (docs/architecture.md 16). Lookups
are primary-key reads; writes are batched (200 entries or 2 seconds) and flushed on
``close()``. Only GET responses with status 200 or 304 update an entry. No body is stored, only
its hash.

The cache fails open: if history cannot be read or written, ``request_headers`` returns ``{}``,
``observe`` returns False, and the error is counted in ``errors`` (and in the Watch's
``counts.monitor_errors`` when the cache was given one).
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

from scrapewatch.history.store import HistoryStore
from scrapewatch.redact import redact_url
from scrapewatch.waste.dedupe import canonical_url

if TYPE_CHECKING:
    from scrapewatch.runtime import Watch

FLUSH_EVERY_ENTRIES = 200
FLUSH_EVERY_SECONDS = 2.0
CONDITIONAL_HEADERS = ("if-none-match", "if-modified-since")

_Entry = tuple[str | None, str | None, str | None]  # etag, last_modified, body_sha256


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return str(value)
    return None


def is_conditional(headers: Mapping[str, str]) -> bool:
    """True when request headers carry ``If-None-Match`` or ``If-Modified-Since``."""
    return any(str(k).lower() in CONDITIONAL_HEADERS for k in headers)


class ConditionalCache:
    """Small SQLite table keyed by canonical URL."""

    def __init__(self, path: Path | str, *, watch: Watch | None = None) -> None:
        """Bind to a history directory (``history.sqlite`` inside); opened on first use.

        ``watch`` receives the cache's own failures as monitor errors.
        """
        self.path = Path(path)
        self.watch = watch
        self.errors = 0
        self._store: HistoryStore | None = None
        self._pending: dict[str, tuple[str | None, str | None, str | None, str]] = {}
        self._last_flush = time.monotonic()
        self._lock = threading.RLock()

    def _failed(self, where: str, exc: Exception) -> None:
        self.errors += 1
        if self.watch is not None:
            self.watch.monitor_error(f"conditional cache {where}", exc)

    # --- lifecycle ----------------------------------------------------------------------
    def _open(self) -> HistoryStore:
        if self._store is None:
            store = HistoryStore(self.path)
            store.open()
            self._store = store
        return self._store

    def flush(self) -> int:
        """Write pending entries; returns how many."""
        with self._lock:
            rows = [(url, *entry) for url, entry in self._pending.items()]
            self._pending.clear()
            self._last_flush = time.monotonic()
            if not rows:
                return 0
            return self._open().write_cache_entries(rows)

    def close(self) -> None:
        """Flush pending entries and close the connection."""
        with self._lock:
            try:
                self.flush()
            finally:
                if self._store is not None:
                    self._store.close()
                    self._store = None

    def __enter__(self) -> ConditionalCache:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- lookups ------------------------------------------------------------------------
    @staticmethod
    def key(url: str) -> str:
        """The stored key: canonical URL with secret-looking query values redacted."""
        return redact_url(canonical_url(url))

    def _entry(self, key: str) -> _Entry | None:
        pending = self._pending.get(key)
        if pending is not None:
            return pending[0], pending[1], pending[2]
        return self._open().cache_entry(key)

    def request_headers(self, url: str) -> dict[str, str]:
        """``If-None-Match`` / ``If-Modified-Since`` for a URL seen before, else ``{}``."""
        try:
            with self._lock:
                entry = self._entry(self.key(url))
        except Exception as exc:
            self._failed("lookup", exc)
            return {}
        if entry is None:
            return {}
        etag, last_modified, _ = entry
        out: dict[str, str] = {}
        if etag:
            out["If-None-Match"] = etag
        if last_modified:
            out["If-Modified-Since"] = last_modified
        return out

    def observe(
        self, url: str, status: int, headers: Mapping[str, str], body: bytes | None
    ) -> bool:
        """Update the entry; returns True when the page is unchanged.

        A 304 is unchanged (validators the server sent again are kept). A 200 is unchanged
        when its body hash equals the stored one; ``body=None`` (never read) is never
        unchanged and stores no hash. Other statuses change nothing and return False.
        """
        try:
            return self._observe(url, status, headers, body)
        except Exception as exc:
            self._failed("update", exc)
            return False

    def _observe(
        self, url: str, status: int, headers: Mapping[str, str], body: bytes | None
    ) -> bool:
        if status not in (200, 304):
            return False
        key = self.key(url)
        now = datetime.now(tz=timezone.utc).isoformat()
        etag = _header(headers, "etag")
        last_modified = _header(headers, "last-modified")
        with self._lock:
            old = self._entry(key)
            if status == 304:
                if old is None:
                    return True  # the server says unchanged; nothing to update it with
                self._pending[key] = (etag or old[0], last_modified or old[1], old[2], now)
                unchanged = True
            else:
                digest = hashlib.sha256(body).hexdigest() if body is not None else None
                unchanged = digest is not None and old is not None and old[2] == digest
                self._pending[key] = (etag, last_modified, digest, now)
            due = len(self._pending) >= FLUSH_EVERY_ENTRIES or (
                time.monotonic() - self._last_flush >= FLUSH_EVERY_SECONDS
            )
            if due:
                self.flush()
        return unchanged
