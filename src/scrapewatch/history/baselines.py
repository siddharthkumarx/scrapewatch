"""Baseline computation from past passing runs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.history.store import HistoryStore


class Baseline(BaseModel):
    """What batch checks compare against. Empty when fewer than ``min_runs`` runs exist."""

    model_config = ConfigDict(extra="forbid")
    run_ids: list[str] = Field(default_factory=list)
    row_count_median: float | None = None
    fields: dict[str, dict[str, Any]] = Field(default_factory=dict)
    previous_value_hashes: dict[str, str] = Field(default_factory=dict)

    @property
    def available(self) -> bool:
        """True when at least one baseline run exists."""
        return bool(self.run_ids)


def compute_baseline(store: HistoryStore, name: str, *, n: int = 7, min_runs: int = 3) -> Baseline:
    """Aggregate the last ``n`` passing runs of ``name``; empty Baseline below ``min_runs``."""
    raise NotImplementedError("build-progress item 2")
