"""Per-field statistics of a run's records, computed incrementally.

These statistics are all the history store keeps of record values (docs/architecture.md
"History store"): counts, quantiles, a 10-bin quantile histogram for numeric fields and the
20 most common values for strings. Batch checks compare them with the baseline.

Quantiles use linear interpolation between closest ranks (the "inclusive" method of
``statistics.quantiles``, numpy's default), so p50 is the ordinary median.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

TOP_VALUES = 20
HIST_BINS = 10


class FieldStats(BaseModel):
    """Statistics of one field over one run."""

    model_config = ConfigDict(extra="forbid")
    count: int = 0  # records seen (the field may be missing in some)
    null_count: int = 0  # missing, None or empty string
    distinct_count: int = 0
    numeric_count: int = 0
    p05: float | None = None
    p50: float | None = None
    p95: float | None = None
    mean: float | None = None
    hist: dict[str, list[Any]] | None = None  # {"edges": [11 floats], "counts": [10 ints]}
    top_values: list[list[Any]] = Field(default_factory=list)  # [[value, count], ...]

    @property
    def coverage(self) -> float:
        """Share of records with a non-null value (0.0 to 1.0)."""
        return (self.count - self.null_count) / self.count if self.count else 0.0


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile of an already sorted, non-empty sequence."""
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_values[lo]) + (float(sorted_values[hi]) - float(sorted_values[lo])) * frac


def histogram(sorted_values: Sequence[float], edges: Sequence[float]) -> list[int]:
    """Count values into bins ``[e0, e1) ... [e9, e10]``; values outside go to the end bins."""
    bins = len(edges) - 1
    counts = [0] * bins
    for v in sorted_values:
        idx = bisect.bisect_right(edges, v) - 1
        counts[min(max(idx, 0), bins - 1)] += 1
    return counts


def is_number(value: Any) -> bool:
    """True for int/float values that are finite and not bools."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _hashable(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return value


class StatsAccumulator:
    """Collects field statistics one record at a time (memory: numeric values and counters)."""

    def __init__(self) -> None:
        """Start empty."""
        self.records = 0
        self._present: Counter[str] = Counter()
        self._nulls: Counter[str] = Counter()
        self._numbers: dict[str, list[float]] = {}
        self._strings: dict[str, Counter[str]] = {}
        self._others: dict[str, set[Any]] = {}

    def add(self, data: Mapping[str, Any]) -> None:
        """Account one record's data."""
        self.records += 1
        for key, value in data.items():
            self._present[key] += 1
            if _is_null(value):
                self._nulls[key] += 1
            elif is_number(value):
                self._numbers.setdefault(key, []).append(float(value))
            elif isinstance(value, str):
                self._strings.setdefault(key, Counter())[value] += 1
            else:
                self._others.setdefault(key, set()).add(_hashable(value))

    def result(self) -> dict[str, FieldStats]:
        """Statistics per field, sorted by field name."""
        out: dict[str, FieldStats] = {}
        for key in sorted(self._present):
            missing = self.records - self._present[key]
            nums = sorted(self._numbers.get(key, []))
            strings = self._strings.get(key, Counter())
            others = self._others.get(key, set())
            stats = FieldStats(
                count=self.records,
                null_count=self._nulls[key] + missing,
                distinct_count=len(set(nums)) + len(strings) + len(others),
                numeric_count=len(nums),
            )
            if nums:
                edges = [quantile(nums, i / HIST_BINS) for i in range(HIST_BINS + 1)]
                stats.p05 = quantile(nums, 0.05)
                stats.p50 = quantile(nums, 0.5)
                stats.p95 = quantile(nums, 0.95)
                stats.mean = math.fsum(nums) / len(nums)
                stats.hist = {"edges": edges, "counts": histogram(nums, edges)}
            if strings:
                ranked = sorted(strings.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_VALUES]
                stats.top_values = [[v, c] for v, c in ranked]
            out[key] = stats
        return out


def field_stats_of(records: Iterable[Mapping[str, Any]]) -> dict[str, FieldStats]:
    """Statistics of an iterable of record data dicts."""
    acc = StatsAccumulator()
    for data in records:
        acc.add(data)
    return acc.result()


def value_hash(data: Mapping[str, Any]) -> str:
    """Stable hash of a record's values (for the changed-share batch check)."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
