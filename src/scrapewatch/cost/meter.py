"""Cost formulas. Reference: docs/architecture.md "Cost model"."""

from __future__ import annotations

import math
from collections.abc import Iterable

from scrapewatch.config import CostConfig
from scrapewatch.models import (
    BAD_LABELS,
    CostBreakdown,
    Finding,
    FindingScope,
    ResourceType,
    ResponseEvent,
    Severity,
    WasteBreakdown,
)

BLOCKABLE_TYPES: frozenset[ResourceType] = frozenset(
    {ResourceType.IMAGE, ResourceType.MEDIA, ResourceType.FONT}
)
WASTE_CATEGORIES = ("retry", "failed", "unchanged_refetch", "blockable")


def _label(event: ResponseEvent, cost: CostConfig) -> str:
    return event.proxy if event.proxy is not None else cost.default_proxy


def price_event(event: ResponseEvent, cost: CostConfig) -> float:
    """Cost of one event: wire_bytes/1e9 * per_gb[proxy] + per_1000/1000 when proxy is an API.

    A label that is in neither ``proxies`` nor ``apis`` costs 0 (the meter reports it). A
    request skipped by the in-run dedupe costs nothing.
    """
    if event.cache.deduped:
        return 0.0
    label = _label(event, cost)
    if label in cost.apis:
        return cost.apis[label].per_1000_requests / 1000.0
    if label in cost.proxies:
        return event.wire_bytes / 1e9 * cost.proxies[label].per_gb
    return 0.0


def waste_category(event: ResponseEvent) -> str | None:
    """The first matching waste category (retry, failed, unchanged_refetch, blockable) or None."""
    if event.attempt > 1:
        return "retry"
    if (
        event.guard.label in BAD_LABELS
        or (event.status is not None and event.status >= 400)
        or event.error is not None
    ):
        return "failed"
    if event.cache.unchanged and event.wire_bytes > 0:
        return "unchanged_refetch"
    if event.resource_type in BLOCKABLE_TYPES:
        return "blockable"
    return None


class CostMeter:
    """Accumulates priced events and produces the breakdowns."""

    def __init__(self, cost: CostConfig) -> None:
        """Bind to a cost model."""
        self.cost = cost
        self._event_costs: list[float] = []
        self._by_domain: dict[str, list[float]] = {}
        self._by_proxy: dict[str, list[float]] = {}
        self._by_type: dict[str, list[float]] = {}
        self._docs_by_domain: dict[str, int] = {}
        self._responses_by_domain: dict[str, int] = {}
        self._browser_seconds = 0.0
        self._browser_seconds_by_domain: dict[str, float] = {}
        self._api_requests = 0
        self._api_costs: list[float] = []
        self.unknown_labels: set[str] = set()
        self._waste = WasteBreakdown()
        self._waste_costs: dict[str, list[float]] = {c: [] for c in WASTE_CATEGORIES}

    def add(self, event: ResponseEvent) -> float:
        """Price and accumulate one event; returns its cost."""
        value = price_event(event, self.cost)
        label = _label(event, self.cost)
        self._event_costs.append(value)
        self._by_domain.setdefault(event.domain, []).append(value)
        self._by_proxy.setdefault(label, []).append(value)
        self._by_type.setdefault(event.resource_type.value, []).append(value)
        self._responses_by_domain[event.domain] = self._responses_by_domain.get(event.domain, 0) + 1
        if event.resource_type == ResourceType.DOCUMENT:
            self._docs_by_domain[event.domain] = self._docs_by_domain.get(event.domain, 0) + 1
        if label in self.cost.apis and not event.cache.deduped:
            self._api_requests += 1
            self._api_costs.append(value)
        elif label not in self.cost.proxies and label not in self.cost.apis:
            self.unknown_labels.add(label)
        self._add_waste(event, value)
        return value

    def _add_waste(self, event: ResponseEvent, value: float) -> None:
        w = self._waste
        rtype = event.resource_type.value
        w.bytes_by_resource_type[rtype] = w.bytes_by_resource_type.get(rtype, 0) + event.wire_bytes
        if event.cache.deduped:
            w.deduped_requests += 1
        if event.cache.conditional and event.cache.unchanged:
            w.conditional_hits += 1
        category = waste_category(event)
        if category is None:
            return
        setattr(w, f"{category}_requests", getattr(w, f"{category}_requests") + 1)
        setattr(w, f"{category}_bytes", getattr(w, f"{category}_bytes") + event.wire_bytes)
        self._waste_costs[category].append(value)

    def add_blocked(self, count: int = 1, *, bytes_saved_estimate: int = 0) -> None:
        """Account requests the waste cutter aborted (they cost nothing; savings are estimated)."""
        self._waste.blocked_requests += count
        self._waste.bytes_saved_estimate += bytes_saved_estimate

    def merge_extras(self, other: CostMeter) -> None:
        """Copy browser time and blocked-request counts (not events) from another meter."""
        self._browser_seconds += other._browser_seconds
        for d, secs in other._browser_seconds_by_domain.items():
            self._browser_seconds_by_domain[d] = self._browser_seconds_by_domain.get(d, 0) + secs
        self._waste.blocked_requests += other._waste.blocked_requests
        self._waste.bytes_saved_estimate += other._waste.bytes_saved_estimate

    def add_browser_seconds(self, seconds: float, *, domain: str | None = None) -> None:
        """Account browser time (Playwright/Crawlee browser crawlers).

        Time given with a ``domain`` is charged to it; other time is spread over domains by
        their share of document responses.
        """
        if seconds <= 0:
            return
        if domain is None:
            self._browser_seconds += seconds
        else:
            self._browser_seconds_by_domain[domain] = (
                self._browser_seconds_by_domain.get(domain, 0.0) + seconds
            )

    @property
    def browser_minutes(self) -> float:
        """All browser time so far, in minutes."""
        return (self._browser_seconds + math.fsum(self._browser_seconds_by_domain.values())) / 60

    def breakdown(self, *, usable_records: int, records: int) -> CostBreakdown:
        """Totals per domain, proxy and resource type; cost per (usable) record."""
        per_minute = self.cost.browser.per_minute
        browser_cost = self.browser_minutes * per_minute
        by_domain: dict[str, float] = {d: math.fsum(v) for d, v in self._by_domain.items()}
        spread = self._browser_seconds / 60 * per_minute
        if spread:
            weights = self._docs_by_domain or self._responses_by_domain
            total_w = sum(weights.values())
            if total_w:
                for d, n in weights.items():
                    by_domain[d] = by_domain.get(d, 0.0) + spread * n / total_w
            else:
                by_domain["(browser)"] = by_domain.get("(browser)", 0.0) + spread
        for d, secs in self._browser_seconds_by_domain.items():
            by_domain[d] = by_domain.get(d, 0.0) + secs / 60 * per_minute
        total = math.fsum([*self._event_costs, browser_cost])
        return CostBreakdown(
            currency=self.cost.currency,
            total=total,
            by_domain=by_domain,
            by_proxy={p: math.fsum(v) for p, v in self._by_proxy.items()},
            by_resource_type={t: math.fsum(v) for t, v in self._by_type.items()},
            browser_minutes=self.browser_minutes,
            browser_cost=browser_cost,
            api_requests=self._api_requests,
            api_cost=math.fsum(self._api_costs),
            usable_records=usable_records,
            per_usable_record=total / usable_records if usable_records > 0 else None,
            per_record=total / records if records > 0 else None,
        )

    def waste(self, events: Iterable[ResponseEvent] | None = None) -> WasteBreakdown:
        """Retries, failures, unchanged refetches and blockable resources with their cost.

        With ``events`` the breakdown is computed for those events alone (a fresh meter);
        without, for everything added so far.
        """
        if events is not None:
            meter = CostMeter(self.cost)
            for ev in events:
                meter.add(ev)
            return meter.waste()
        w = self._waste.model_copy(deep=True)
        for category in WASTE_CATEGORIES:
            setattr(w, f"{category}_cost", math.fsum(self._waste_costs[category]))
        w.total_waste_cost = math.fsum(
            [v for category in WASTE_CATEGORIES for v in self._waste_costs[category]]
        )
        return w

    def findings(self, run_id: str) -> list[Finding]:
        """Run-scope findings: ``cost.unknown_label`` (info) when labels had no price."""
        if not self.unknown_labels:
            return []
        labels = sorted(self.unknown_labels)
        return [
            Finding(
                run_id=run_id,
                scope=FindingScope.RUN,
                check="cost.unknown_label",
                severity=Severity.INFO,
                message=(
                    f"No price for proxy label(s) {', '.join(labels)}; priced at 0. "
                    "Add them under cost.proxies or cost.apis."
                ),
                observed=labels,
            )
        ]
