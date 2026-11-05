"""Data rules file (rules.yml). Format: docs/architecture.md "Data rules YAML"."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.models import Severity

FieldType = Literal[
    "string", "number", "integer", "boolean", "date", "datetime", "url", "object", "array"
]


class FieldRule(BaseModel):
    """Rules for one field."""

    model_config = ConfigDict(extra="forbid")
    type: FieldType | None = None
    required: bool = False
    nullable: bool = True
    min: float | None = None
    max: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    regex: str | None = None
    allowed: list[Any] | None = None
    clean: list[str] = Field(default_factory=list)  # normalizer names, applied in order
    clean_options: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = Severity.WARN
    coverage_hold_below_pct: float | None = None  # batch: hold when coverage falls below


class CrossRule(BaseModel):
    """A relation between fields, written in the safe expression subset."""

    model_config = ConfigDict(extra="forbid")
    id: str
    expr: str
    when: str | None = None
    severity: Severity = Severity.WARN
    message: str | None = None


class RowCountRule(BaseModel):
    """Row count against baseline median."""

    model_config = ConfigDict(extra="forbid")
    warn_pct: float = 20.0
    hold_pct: float = 50.0
    min_rows: int | None = None  # absolute floor; hold below


class CoverageRule(BaseModel):
    """Per-field coverage (non-null share) drop vs baseline, in percentage points."""

    model_config = ConfigDict(extra="forbid")
    warn_drop_points: float = 5.0
    hold_drop_points: float = 20.0


class DistributionRule(BaseModel):
    """Numeric PSI and categorical new-value share vs baseline."""

    model_config = ConfigDict(extra="forbid")
    psi_warn: float = 0.25
    psi_hold: float | None = None
    new_values_warn_pct: float = 10.0


class ChangedShareRule(BaseModel):
    """Share of records whose value hash differs from the previous run (same dedupe key)."""

    model_config = ConfigDict(extra="forbid")
    warn_above_pct: float = 50.0
    hold_above_pct: float | None = None
    warn_below_pct: float | None = None  # for sites that must change (e.g. news)


class UnitErrorRule(BaseModel):
    """Median of a numeric field shifted by x100 / x1000 vs baseline."""

    model_config = ConfigDict(extra="forbid")
    fields: list[str] = Field(default_factory=list)
    ratios: list[float] = Field(default_factory=lambda: [100.0, 1000.0])
    tolerance_pct: float = 15.0
    severity: Severity = Severity.HOLD


class CurrencyRule(BaseModel):
    """Currency field must agree with the currency parsed from a price field."""

    model_config = ConfigDict(extra="forbid")
    field: str
    value_field: str
    expected: list[str] | None = None
    hold_mismatch_pct: float = 1.0


class BatchRules(BaseModel):
    """Checks on the whole run against history."""

    model_config = ConfigDict(extra="forbid")
    row_count: RowCountRule = Field(default_factory=RowCountRule)
    coverage: CoverageRule = Field(default_factory=CoverageRule)
    distribution: DistributionRule = Field(default_factory=DistributionRule)
    changed_share: ChangedShareRule = Field(default_factory=ChangedShareRule)
    unit_error: UnitErrorRule = Field(default_factory=UnitErrorRule)
    currency: CurrencyRule | None = None
    duplicates_warn_pct: float = 1.0
    duplicates_hold_pct: float | None = None


class VerdictThresholds(BaseModel):
    """Run-level thresholds. Defaults: docs/architecture.md "Verdict logic"."""

    model_config = ConfigDict(extra="forbid")
    blocked_share_warn_pct: float = 2.0
    blocked_share_hold_pct: float = 20.0
    schema_invalid_warn_pct: float = 1.0
    schema_invalid_hold_pct: float = 5.0
    flagged_warn_pct: float = 5.0
    monitor_errors_warn: int = 1
    min_usable_records: int = 1


class DataRules(BaseModel):
    """The whole rules.yml."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    name: str = "default"
    key: list[str] = Field(default_factory=list)  # dedupe key fields
    json_schema: str | dict[str, Any] | None = Field(default=None, alias="schema")
    fields: dict[str, FieldRule] = Field(default_factory=dict)
    cross: list[CrossRule] = Field(default_factory=list)
    batch: BatchRules = Field(default_factory=BatchRules)
    verdict: VerdictThresholds = Field(default_factory=VerdictThresholds)


def load_data_rules(path: Path | str) -> DataRules:
    """Load and validate a rules.yml; resolves a relative ``schema`` path against the file."""
    raise NotImplementedError("build-progress item 7")
