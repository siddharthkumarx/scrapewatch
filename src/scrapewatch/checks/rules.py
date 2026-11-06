"""Data rules file (rules.yml). Format: docs/architecture.md "Data rules YAML"."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scrapewatch import _yaml
from scrapewatch.checks.expr import compile_expr
from scrapewatch.errors import RulesError
from scrapewatch.models import Severity

# Normalizer names a rules file may use (docs/architecture.md section 10).
KNOWN_NORMALIZERS: frozenset[str] = frozenset(
    {
        "passthrough",
        "price",
        "number",
        "date",
        "whitespace",
        "html_entities",
        "strip_tags",
        "mojibake",
        "url",
    }
)

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

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: int = 1
    name: str = "default"
    key: list[str] = Field(default_factory=list)  # dedupe key fields
    json_schema: str | dict[str, Any] | None = Field(default=None, alias="schema")
    fields: dict[str, FieldRule] = Field(default_factory=dict)
    cross: list[CrossRule] = Field(default_factory=list)
    batch: BatchRules = Field(default_factory=BatchRules)
    verdict: VerdictThresholds = Field(default_factory=VerdictThresholds)
    examples: int = Field(default=3, ge=0)  # example values kept per finding for the report

    @classmethod
    def from_model(cls, model: type[BaseModel], **values: Any) -> DataRules:
        """Rules whose JSON Schema is built from a Pydantic model (other keys as keywords)."""
        rules = cls.model_validate({**values, "schema": model.model_json_schema()})
        validate_data_rules(rules)
        return rules


def load_data_rules(path: Path | str) -> DataRules:
    """Load and validate a rules.yml; resolves a relative ``schema`` path against the file.

    The JSON Schema file (JSON or YAML) is read and stored on ``json_schema`` as a dict.
    Unknown keys, unknown normalizers, invalid regexes, invalid JSON Schemas and cross-rule
    expressions outside the safe subset all raise ``RulesError``.
    """
    path = Path(path)
    try:
        raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RulesError(f"cannot read rules file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RulesError(f"rules file {path} is not valid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RulesError(f"rules file {path} must contain a mapping")
    schema = raw.get("schema")
    if isinstance(schema, str):
        schema_path = Path(schema)
        if not schema_path.is_absolute():
            schema_path = path.parent / schema_path
        raw["schema"] = _read_schema(schema_path)
    try:
        rules = DataRules.model_validate(raw)
    except ValidationError as exc:
        errs = [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]]
        raise RulesError(f"invalid rules file {path}: " + "; ".join(errs)) from exc
    validate_data_rules(rules, where=str(path))
    return rules


def _read_schema(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesError(f"cannot read JSON Schema {path}: {exc}") from exc
    try:
        data = json.loads(text) if path.suffix == ".json" else _yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as exc:
        raise RulesError(f"JSON Schema {path} cannot be parsed: {exc}") from exc
    if not isinstance(data, dict):
        raise RulesError(f"JSON Schema {path} must be an object")
    return data


def validate_data_rules(rules: DataRules, *, where: str = "rules") -> None:
    """Check what pydantic cannot: schema, regexes, normalizer names, expressions."""
    if rules.schema_version != 1:
        raise RulesError(f"{where}: unsupported schema_version {rules.schema_version}")
    if isinstance(rules.json_schema, str):
        raise RulesError(f"{where}: schema path {rules.json_schema!r} was not loaded")
    if rules.json_schema is not None:
        try:
            Draft202012Validator.check_schema(rules.json_schema)
        except SchemaError as exc:
            raise RulesError(f"{where}: invalid JSON Schema: {exc.message}") from exc
    for name, field in rules.fields.items():
        unknown = [n for n in field.clean if n not in KNOWN_NORMALIZERS]
        if unknown:
            raise RulesError(f"{where}: field {name}: unknown normalizer(s) {unknown}")
        if field.regex is not None:
            try:
                re.compile(field.regex)
            except re.error as exc:
                raise RulesError(f"{where}: field {name}: bad regex: {exc}") from exc
    seen: set[str] = set()
    for rule in rules.cross:
        if rule.id in seen:
            raise RulesError(f"{where}: duplicate cross rule id {rule.id}")
        seen.add(rule.id)
        compile_expr(rule.expr)
        if rule.when is not None:
            compile_expr(rule.when)
