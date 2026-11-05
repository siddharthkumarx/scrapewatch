"""price normalizer(s). Input/output table: docs/architecture.md "Cleaning"."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scrapewatch.clean.pipeline import CleanOutcome, register


@register("price")
def normalize_price(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """See docs/architecture.md "Cleaning" for the exact behaviour and flags."""
    raise NotImplementedError("build-progress item 10/11")
