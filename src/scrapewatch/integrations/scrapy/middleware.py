"""Downloader middleware at priority 900: sees the response before HttpCompression decodes it."""

from __future__ import annotations

from typing import Any


class ScrapewatchDownloaderMiddleware:
    """Records every response and download error; never alters or drops them."""

    @classmethod
    def from_crawler(cls, crawler: Any) -> ScrapewatchDownloaderMiddleware:
        """Scrapy factory."""
        raise NotImplementedError("build-progress item 19")

    def process_response(self, request: Any, response: Any, spider: Any) -> Any:
        """Record and return the response unchanged."""
        raise NotImplementedError("build-progress item 19")

    def process_exception(self, request: Any, exception: BaseException, spider: Any) -> None:
        """Record a transport error; return None so Scrapy's retry logic runs as usual."""
        raise NotImplementedError("build-progress item 19")
