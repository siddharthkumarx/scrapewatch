"""Baseline computation from past passing runs."""

from __future__ import annotations

import statistics
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.history.stats import FieldStats
from scrapewatch.history.store import HistoryStore


class Baseline(BaseModel):
    """What batch checks compare against. Empty when fewer than ``min_runs`` runs exist.

    ``fields[name]`` holds medians across the baseline runs: ``coverage`` (0..1), ``p05``,
    ``p50``, ``p95``, ``mean``, ``numeric_share``, ``hist`` (``edges``: element-wise median
    of the runs' 10-bin quantile edges, ``shares``: mean share per bin) and ``top_values``
    (the union of the runs' most common values).
    """

    model_config = ConfigDict(extra="forbid")
    run_ids: list[str] = Field(default_factory=list)
    candidate_runs: int = 0  # passing runs found (also when below min_runs)
    min_runs: int = 3
    row_count_median: float | None = None
    fields: dict[str, dict[str, Any]] = Field(default_factory=dict)
    previous_value_hashes: dict[str, str] = Field(default_factory=dict)

    @property
    def available(self) -> bool:
        """True when at least one baseline run exists."""
        return bool(self.run_ids)


def compute_baseline(store: HistoryStore, name: str, *, n: int = 7, min_runs: int = 3) -> Baseline:
    """Aggregate the last ``n`` passing runs of ``name``; empty Baseline below ``min_runs``."""
    runs = store.baseline_runs(name, n=n, passing_only=True)
    if len(runs) < min_runs:
        return Baseline(candidate_runs=len(runs), min_runs=min_runs)
    run_ids = [r.run_id for r in runs]
    per_field = store.field_stats(run_ids)
    fields = {name: aggregate_field(list(stats.values())) for name, stats in per_field.items()}
    return Baseline(
        run_ids=run_ids,
        candidate_runs=len(runs),
        min_runs=min_runs,
        row_count_median=float(statistics.median(r.counts.records for r in runs)),
        fields=fields,
        previous_value_hashes=store.value_hashes(run_ids[0]),
    )


def _median(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return float(statistics.median(present)) if present else None


def aggregate_field(stats: list[FieldStats]) -> dict[str, Any]:
    """Medians across runs of one field's statistics (see :class:`Baseline`)."""
    out: dict[str, Any] = {
        "runs": len(stats),
        "coverage": _median([s.coverage for s in stats]),
        "numeric_share": _median(
            [
                (s.numeric_count / (s.count - s.null_count)) if s.count > s.null_count else None
                for s in stats
            ]
        ),
        "p05": _median([s.p05 for s in stats]),
        "p50": _median([s.p50 for s in stats]),
        "p95": _median([s.p95 for s in stats]),
        "mean": _median([s.mean for s in stats]),
    }
    hists = [s.hist for s in stats if s.hist]
    if hists:
        width = len(hists[0]["edges"])
        same = [h for h in hists if len(h["edges"]) == width]
        edges = [statistics.median(h["edges"][i] for h in same) for i in range(width)]
        shares = []
        for i in range(width - 1):
            per_run = [h["counts"][i] / max(sum(h["counts"]), 1) for h in same]
            shares.append(statistics.fmean(per_run))
        out["hist"] = {"edges": [float(e) for e in edges], "shares": shares}
    top: dict[Any, int] = {}
    for s in stats:
        for value, count in s.top_values:
            top[value] = top.get(value, 0) + int(count)
    out["top_values"] = sorted(top, key=lambda v: (-top[v], str(v)))
    return out
