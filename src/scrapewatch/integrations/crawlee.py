"""Crawlee for Python integration.

Hooks for HttpCrawler/BeautifulSoupCrawler/ParselCrawler (httpx-based) and PlaywrightCrawler
(page hooks). Only if it fits cleanly; see build-progress item 20.
"""

from __future__ import annotations

from typing import Any

from scrapewatch.runtime import Watch


def attach(crawler: Any, watch: Watch, *, proxy: str | None = None) -> None:
    """Register pre/post navigation hooks on a Crawlee crawler."""
    raise NotImplementedError("build-progress item 20")
