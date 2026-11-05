"""Conditional-request cache: stores ETag / Last-Modified / body hash per URL in history.

An unchanged page (304, or same body hash) is recorded as ``cache.unchanged=True`` and is
never emitted as a new record.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


class ConditionalCache:
    """Small SQLite table keyed by canonical URL."""

    def __init__(self, path: Path) -> None:
        """Bind to a history directory."""
        self.path = path

    def request_headers(self, url: str) -> dict[str, str]:
        """``If-None-Match`` / ``If-Modified-Since`` for a URL seen before, else ``{}``."""
        raise NotImplementedError("build-progress item 22")

    def observe(
        self, url: str, status: int, headers: Mapping[str, str], body: bytes | None
    ) -> bool:
        """Update the entry; returns True when the page is unchanged."""
        raise NotImplementedError("build-progress item 22")
