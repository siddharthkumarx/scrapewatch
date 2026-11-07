"""Scrapy extension: opens the Watch on spider_opened, finishes it on spider_closed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scrapewatch.config import load_settings
from scrapewatch.models import Run
from scrapewatch.runtime import Watch

ATTR = "scrapewatch"  # the Watch lives on crawler.scrapewatch while the spider runs
CRASH_REASONS = {"shutdown", "cancelled"}


def current_watch(crawler: Any) -> Watch | None:
    """The running Watch of a crawler, or None (extension disabled or not opened yet)."""
    w = getattr(crawler, ATTR, None)
    return w if isinstance(w, Watch) else None


class ScrapewatchExtension:
    """Holds the Watch on ``crawler.scrapewatch``; exposes stats as ``scrapewatch/*``."""

    def __init__(self, crawler: Any) -> None:
        """Read SCRAPEWATCH_* settings."""
        self.crawler = crawler
        self.watch: Watch | None = None
        s = crawler.settings
        self.enabled = s.getbool("SCRAPEWATCH_ENABLED", True)
        self.rules = s.get("SCRAPEWATCH_RULES")
        self.config = s.get("SCRAPEWATCH_CONFIG")
        self.name = s.get("SCRAPEWATCH_NAME")

    @classmethod
    def from_crawler(cls, crawler: Any) -> ScrapewatchExtension:
        """Scrapy factory; connects spider_opened / spider_closed."""
        from scrapy import signals

        ext = cls(crawler)
        if ext.enabled:
            crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
            crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        return ext

    def spider_opened(self, spider: Any) -> None:
        """Start the run. A bad config or rules file is logged and monitoring stays off."""
        overrides: dict[str, Any] = {"name": self.name or getattr(spider, "name", None) or "scrapy"}
        if self.rules:
            overrides["rules"] = Path(self.rules)
        try:
            settings = load_settings(self.config, overrides=overrides)
        except Exception as exc:
            spider.logger.error("scrapewatch disabled: cannot load settings: %s", exc)
            return
        w = Watch(settings, integration="scrapy")
        w.start()
        self.watch = w
        setattr(self.crawler, ATTR, w)

    def spider_closed(self, spider: Any, reason: str) -> None:
        """Finish the run and export ``scrapewatch/*`` stats. Never stops or fails the crawl."""
        w = self.watch
        if w is None:
            return
        run = w.finish(crashed=reason in CRASH_REASONS)
        try:
            self._export(run)
        except Exception as exc:
            w.monitor_error("scrapy stats", exc)
        spider.logger.info(
            "scrapewatch verdict: %s%s",
            run.verdict.status.value,
            f" ({'; '.join(run.verdict.reasons)})" if run.verdict.reasons else "",
        )

    def _export(self, run: Run) -> None:
        stats = self.crawler.stats
        c = run.counts
        values: dict[str, Any] = {
            "scrapewatch/verdict": run.verdict.status.value,
            "scrapewatch/run_id": run.run_id,
            "scrapewatch/responses": c.responses,
            "scrapewatch/bad_documents": c.bad_documents,
            "scrapewatch/retries": c.retries,
            "scrapewatch/records": c.records,
            "scrapewatch/usable_records": c.usable_records,
            "scrapewatch/duplicates": c.duplicates,
            "scrapewatch/monitor_errors": c.monitor_errors,
            "scrapewatch/wire_bytes": c.wire_bytes,
        }
        for label, n in c.by_label.items():
            values[f"scrapewatch/label/{label}"] = n
        if run.cost is not None:
            values["scrapewatch/cost"] = run.cost.total
            values["scrapewatch/cost_per_usable_record"] = run.cost.per_usable_record
        if run.report_path:
            values["scrapewatch/report"] = run.report_path
        for key, value in values.items():
            stats.set_value(key, value)
