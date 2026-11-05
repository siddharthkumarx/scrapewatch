"""requests adapter. Bytes: Content-Length when present (compressed), else decoded length."""

from __future__ import annotations

from typing import Any

from scrapewatch.runtime import Watch


def adapter(watch: Watch, *, proxy: str | None = None, **adapter_kwargs: Any) -> Any:
    """Return an ``HTTPAdapter`` subclass instance to ``session.mount("https://", ...)``."""
    raise NotImplementedError("build-progress item 17")


def mount(session: Any, watch: Watch, *, proxy: str | None = None) -> None:
    """Mount the adapter on http:// and https://."""
    raise NotImplementedError("build-progress item 17")
