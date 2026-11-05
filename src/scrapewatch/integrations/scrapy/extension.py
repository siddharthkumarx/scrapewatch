"""Scrapy extension: opens the Watch on spider_opened, finishes it on spider_closed."""

from __future__ import annotations

from typing import Any

from scrapewatch.runtime import Watch


class ScrapewatchExtension:
    """Holds the Watch on ``crawler.scrapewatch``; exposes stats as ``scrapewatch/*``."""

    def __init__(self, crawler: Any) -> None:
        """Read SCRAPEWATCH_* settings."""
        self.crawler = crawler
        self.watch: Watch | None = None

    @classmethod
    def from_crawler(cls, crawler: Any) -> ScrapewatchExtension:
        """Scrapy factory; connects spider_opened / spider_closed."""
        raise NotImplementedError("build-progress item 19")
