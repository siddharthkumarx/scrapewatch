"""Playwright page/context hooks (sync and async). Bytes: ``request.sizes()`` (measured)."""

from __future__ import annotations

from typing import Any

from scrapewatch.runtime import Watch


def attach(context_or_page: Any, watch: Watch, *, proxy: str | None = None) -> None:
    """Listen to ``response`` and ``requestfailed`` on a BrowserContext or Page (sync API)."""
    raise NotImplementedError("build-progress item 18")


async def attach_async(context_or_page: Any, watch: Watch, *, proxy: str | None = None) -> None:
    """Async API variant."""
    raise NotImplementedError("build-progress item 18")
