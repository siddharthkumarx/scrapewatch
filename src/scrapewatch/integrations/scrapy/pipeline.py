"""Item pipeline at priority 1000 (last): cleans and checks items, writes fixes back."""

from __future__ import annotations

import contextlib
from typing import Any

from scrapewatch.integrations.scrapy.extension import current_watch


class ScrapewatchPipeline:
    """Passes items through ``Watch.record_record``; drops only if SCRAPEWATCH_DROP_UNUSABLE."""

    def __init__(self, crawler: Any) -> None:
        """Read SCRAPEWATCH_DROP_UNUSABLE."""
        self.crawler = crawler
        self.drop_unusable = crawler.settings.getbool("SCRAPEWATCH_DROP_UNUSABLE", False)

    @classmethod
    def from_crawler(cls, crawler: Any) -> ScrapewatchPipeline:
        """Scrapy factory."""
        return cls(crawler)

    def process_item(self, item: Any, spider: Any = None) -> Any:
        """Return the (cleaned) item.

        Fixes (a parsed price, trimmed text) are written back to the item; a field the item
        class does not declare (such as a filled-in currency on a ``scrapy.Item``) is left
        out. Findings stay in the run, never in the item.
        """
        w = current_watch(self.crawler)
        if w is None:
            return item
        try:
            from itemadapter import ItemAdapter

            adapter = ItemAdapter(item)
            record = w.record_record(adapter.asdict())
            if record is None:
                return item
            for key, value in record.data.items():
                if adapter.get(key, _MISSING) is not value:
                    with contextlib.suppress(KeyError):  # not a field this item declares
                        adapter[key] = value
        except Exception as exc:
            w.monitor_error("scrapy process_item", exc)
            return item
        if self.drop_unusable and not record.usable:
            from scrapy.exceptions import DropItem

            raise DropItem(f"scrapewatch: unusable record ({_reason(record)})")
        return item


_MISSING = object()


def _reason(record: Any) -> str:
    worst = [f.message for f in record.findings if f.severity.value == "hold"]
    return worst[0] if worst else "duplicate or schema invalid"
