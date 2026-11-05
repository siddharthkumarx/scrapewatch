"""Cost formulas. Reference: docs/architecture.md "Cost model"."""

from __future__ import annotations

from collections.abc import Iterable

from scrapewatch.config import CostConfig
from scrapewatch.models import CostBreakdown, ResponseEvent, WasteBreakdown


def price_event(event: ResponseEvent, cost: CostConfig) -> float:
    """Cost of one event: wire_bytes/1e9 * per_gb[proxy] + per_1000/1000 when proxy is an API."""
    raise NotImplementedError("build-progress item 3")


class CostMeter:
    """Accumulates priced events and produces the breakdowns."""

    def __init__(self, cost: CostConfig) -> None:
        """Bind to a cost model."""
        self.cost = cost

    def add(self, event: ResponseEvent) -> float:
        """Price and accumulate one event; returns its cost."""
        raise NotImplementedError("build-progress item 3")

    def add_browser_seconds(self, seconds: float, *, domain: str | None = None) -> None:
        """Account browser time (Playwright/Crawlee browser crawlers)."""
        raise NotImplementedError("build-progress item 3")

    def breakdown(self, *, usable_records: int, records: int) -> CostBreakdown:
        """Totals per domain, proxy and resource type; cost per (usable) record."""
        raise NotImplementedError("build-progress item 3")

    def waste(self, events: Iterable[ResponseEvent] | None = None) -> WasteBreakdown:
        """Retries, failures, unchanged refetches and blockable resources with their cost."""
        raise NotImplementedError("build-progress item 3")
