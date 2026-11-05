"""Per-record and per-batch checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.checks.rules import DataRules
from scrapewatch.history.baselines import Baseline
from scrapewatch.models import Finding, Record, Run


class CheckResult(BaseModel):
    """Everything ``check_records`` produces."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    records: list[Record] = Field(default_factory=list)
    batch_findings: list[Finding] = Field(default_factory=list)
    run: Run


def check_record(data: Mapping[str, Any], rules: DataRules, *, run_id: str) -> Record:
    """Apply cleaning, then schema, field and cross rules to one item."""
    raise NotImplementedError("build-progress item 7")


def check_batch(
    records: list[Record], rules: DataRules, baseline: Baseline, *, run_id: str
) -> list[Finding]:
    """Duplicates and batch-vs-history rules."""
    raise NotImplementedError("build-progress item 8")


def check_records(
    items: Iterable[Mapping[str, Any]],
    rules: DataRules,
    *,
    baseline: Baseline | None = None,
    run: Run | None = None,
) -> CheckResult:
    """Check an iterable of items end to end (records, batch findings, verdict)."""
    raise NotImplementedError("build-progress item 8")


def check(
    path: str | Path,
    *,
    rules: str | Path | DataRules,
    history: str | Path | None = None,
    name: str | None = None,
    fmt: str | None = None,
) -> Run:
    """Check a CSV, JSONL or Parquet file. This is what ``scrapewatch check`` calls."""
    raise NotImplementedError("build-progress item 12")
