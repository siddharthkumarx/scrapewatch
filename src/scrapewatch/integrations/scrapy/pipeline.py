"""Item pipeline at priority 1000 (last): cleans and checks items, attaches findings."""

from __future__ import annotations

from typing import Any


class ScrapewatchPipeline:
    """Passes items through ``Watch.record_record``; drops only if SCRAPEWATCH_DROP_UNUSABLE."""

    @classmethod
    def from_crawler(cls, crawler: Any) -> ScrapewatchPipeline:
        """Scrapy factory."""
        raise NotImplementedError("build-progress item 19")

    def process_item(self, item: Any, spider: Any) -> Any:
        """Return the (cleaned) item."""
        raise NotImplementedError("build-progress item 19")
