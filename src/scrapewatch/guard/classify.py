"""Rule evaluation. Precedence and tie-breaking: docs/architecture.md "Response guard"."""

from __future__ import annotations

from collections.abc import Mapping

from scrapewatch.guard.features import Features
from scrapewatch.guard.rules import GuardRuleSet
from scrapewatch.models import GuardResult


def classify_features(features: Features, rules: GuardRuleSet) -> GuardResult:
    """Evaluate every rule; pick by label precedence, then confidence, then rule order."""
    raise NotImplementedError("build-progress item 4")


def classify(
    *,
    status: int | None,
    headers: Mapping[str, str],
    body: bytes | str | None = None,
    url: str | None = None,
    final_url: str | None = None,
    rules: GuardRuleSet | None = None,
    baseline_p50_bytes: int | None = None,
) -> GuardResult:
    """Convenience: extract features and classify with the bundled rules."""
    raise NotImplementedError("build-progress item 4")
