"""httpx event hooks (sync and async). Usage: docs/architecture.md "Integrations"."""

from __future__ import annotations

from typing import Any

from scrapewatch.runtime import Watch


def hooks(watch: Watch, *, proxy: str | None = None) -> dict[str, list[Any]]:
    """Return ``{"request": [...], "response": [...]}`` for ``httpx.Client(event_hooks=...)``.

    Bytes: ``response.num_bytes_downloaded`` after the body is read (measured, compressed).
    """
    raise NotImplementedError("build-progress item 16")


def async_hooks(watch: Watch, *, proxy: str | None = None) -> dict[str, list[Any]]:
    """Same for ``httpx.AsyncClient``."""
    raise NotImplementedError("build-progress item 16")
