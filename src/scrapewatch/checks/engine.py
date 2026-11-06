"""Per-record and per-batch checks."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.checks.batch import BatchStats, batch_findings
from scrapewatch.checks.expr import compile_expr, evaluate
from scrapewatch.checks.rules import DataRules, FieldRule
from scrapewatch.checks.verdict import compute_verdict
from scrapewatch.clean import dedupe_key
from scrapewatch.clean.pipeline import run_normalizers
from scrapewatch.history.baselines import Baseline
from scrapewatch.models import Finding, FindingScope, Fix, Record, Run, Severity


class CheckResult(BaseModel):
    """Everything ``check_records`` produces."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    records: list[Record] = Field(default_factory=list)
    batch_findings: list[Finding] = Field(default_factory=list)
    run: Run


class RecordChecker:
    """Compiled form of a DataRules object for fast per-record checks.

    Holds the JSON Schema validator, compiled regexes and expressions, and the dedupe keys
    seen so far in the run (so later duplicates are marked).
    """

    def __init__(self, rules: DataRules) -> None:
        """Compile everything once."""
        self.rules = rules
        schema = rules.json_schema if isinstance(rules.json_schema, dict) else None
        self.validator = Draft202012Validator(schema) if schema is not None else None
        self.regexes = {
            name: re.compile(f.regex) for name, f in rules.fields.items() if f.regex is not None
        }
        self.cross = [
            (r, compile_expr(r.expr), compile_expr(r.when) if r.when else None) for r in rules.cross
        ]
        self.seen_keys: set[str] = set()

    def check(
        self, data: Mapping[str, Any], *, run_id: str, record_id: str | None = None
    ) -> Record:
        """Clean, then schema, field and cross rules, then the dedupe key."""
        record = Record(run_id=run_id, data=dict(data))
        if record_id is not None:
            record.record_id = record_id
        values = record.data
        findings: list[Finding] = []
        fixes: list[Fix] = []

        def finding(check: str, severity: Severity, message: str, **kw: Any) -> None:
            findings.append(
                Finding(
                    run_id=run_id,
                    scope=FindingScope.RECORD,
                    ref=record.record_id,
                    check=check,
                    severity=severity,
                    message=message,
                    **kw,
                )
            )

        # 1. cleaning
        for name, rule in self.rules.fields.items():
            if not rule.clean:
                continue
            present = name in values
            result = run_normalizers(
                name,
                values.get(name),
                rule.clean,
                rule.clean_options,
                run_id=run_id,
                record_id=record.record_id,
                severity=rule.severity,
                field_type=rule.type,
            )
            if present or result.fixes:
                values[name] = result.value
            fixes.extend(result.fixes)
            findings.extend(result.findings)
            for key, val in result.extra.items():
                current = values.get(key)
                if not _missing(current) and current != val:
                    finding(
                        f"clean.{result.extra_source[key]}.extra_conflict",
                        Severity.INFO,
                        f"{key} is {current!r} but {name} {result.fixes[0].before!r} says {val!r}"
                        if result.fixes
                        else f"{key} is {current!r} but {name} says {val!r}",
                        field=key,
                        observed=current,
                        expected=val,
                    )
                if _missing(current):
                    fixes.append(
                        Fix(
                            field=key,
                            normalizer=result.extra_source[key],
                            before=values.get(key),
                            after=val,
                        )
                    )
                    values[key] = val
        # 2. JSON Schema
        schema_failed = False
        if self.validator is not None:
            for err in sorted(self.validator.iter_errors(values), key=lambda e: list(e.path)):
                schema_failed = True
                pointer = "/" + "/".join(str(p) for p in err.absolute_path)
                finding(
                    "schema.invalid",
                    Severity.INFO,
                    f"{pointer}: {err.message}",
                    field=pointer,
                    observed=None if isinstance(err.instance, (dict, list)) else err.instance,
                    expected={str(err.validator): err.validator_value}
                    if not isinstance(err.validator_value, (dict, list))
                    else str(err.validator),
                )
        # 3. field rules
        for name, rule in self.rules.fields.items():
            for check, message, observed, expected in self._field_problems(name, rule, values):
                finding(
                    check,
                    rule.severity,
                    message,
                    field=name,
                    observed=observed,
                    expected=expected,
                )
        # 4. cross rules
        for crule, expr, when in self.cross:
            try:
                if when is not None and not evaluate(when, values):
                    continue
                ok = evaluate(expr, values)
            except Exception as exc:
                finding(
                    f"cross.{crule.id}",
                    Severity.INFO,
                    f"{crule.expr} could not be evaluated: {type(exc).__name__}: {exc}",
                    rule_id=crule.id,
                )
                continue
            if not ok:
                finding(
                    f"cross.{crule.id}",
                    crule.severity,
                    crule.message or f"{crule.expr} is false",
                    rule_id=crule.id,
                    expected=crule.expr,
                )
        # 5. dedupe key
        duplicate = False
        if self.rules.key:
            dkey = dedupe_key(values, self.rules.key)
            if dkey is None:
                missing = [k for k in self.rules.key if _missing(values.get(k))]
                finding(
                    "dup.key_missing",
                    Severity.WARN,
                    f"dedupe key field(s) missing: {', '.join(missing)}",
                    expected=self.rules.key,
                )
            else:
                record.dedupe_key = dkey
                if dkey in self.seen_keys:
                    duplicate = True
                    finding("dup.repeat", Severity.INFO, "duplicate of an earlier record")
                else:
                    self.seen_keys.add(dkey)
        record.findings = findings
        record.fixes = fixes
        record.usable = not (
            schema_failed or duplicate or any(f.severity == Severity.HOLD for f in findings)
        )
        return record

    def _field_problems(
        self, name: str, rule: FieldRule, values: Mapping[str, Any]
    ) -> list[tuple[str, str, Any, Any]]:
        value = values.get(name)
        if _missing(value):
            if rule.required:
                return [("field.required", f"{name} is missing", value, "a value")]
            if value is None and name in values and not rule.nullable:
                return [("field.type", f"{name} is null", None, "not null")]
            return []
        if rule.type is not None and not _type_ok(rule.type, value):
            shown = _short(value)
            return [("field.type", f"{name} {shown} is not of type {rule.type}", value, rule.type)]
        out: list[tuple[str, str, Any, Any]] = []
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if rule.min is not None and number < rule.min:
                out.append(
                    (
                        "field.range",
                        f"{name} {value} is below min {rule.min:g}",
                        value,
                        {"min": rule.min},
                    )
                )
            if rule.max is not None and number > rule.max:
                out.append(
                    (
                        "field.range",
                        f"{name} {value} is above max {rule.max:g}",
                        value,
                        {"max": rule.max},
                    )
                )
        if isinstance(value, (str, list)):
            n = len(value)
            if rule.min_length is not None and n < rule.min_length:
                out.append(
                    (
                        "field.length",
                        f"{name} has length {n}, below {rule.min_length}",
                        n,
                        {"min_length": rule.min_length},
                    )
                )
            if rule.max_length is not None and n > rule.max_length:
                out.append(
                    (
                        "field.length",
                        f"{name} has length {n}, above {rule.max_length}",
                        n,
                        {"max_length": rule.max_length},
                    )
                )
        if name in self.regexes and isinstance(value, str) and not self.regexes[name].search(value):
            out.append(
                (
                    "field.regex",
                    f"{name} {_short(value)} does not match {rule.regex}",
                    value,
                    rule.regex,
                )
            )
        if rule.allowed is not None and value not in rule.allowed:
            out.append(
                (
                    "field.allowed",
                    f"{name} {_short(value)} is not one of {rule.allowed}",
                    value,
                    rule.allowed,
                )
            )
        return out


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _short(value: Any, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _type_ok(kind: str, value: Any) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return _is_number(value)
    if kind == "integer":
        return _is_number(value) and float(value).is_integer()
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "url":
        if not isinstance(value, str):
            return False
        try:
            parts = urlsplit(value)
        except ValueError:
            return False
        return parts.scheme in ("http", "https") and bool(parts.netloc)
    if kind == "date":
        if isinstance(value, date) and not isinstance(value, datetime):
            return True
        if not isinstance(value, str) or not _DATE_RE.match(value):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if kind == "datetime":
        if isinstance(value, datetime):
            return True
        if not isinstance(value, str) or ("T" not in value and " " not in value):
            return False
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True
    return True  # pragma: no cover - FieldType is a closed Literal


def check_record(
    data: Mapping[str, Any],
    rules: DataRules | RecordChecker,
    *,
    run_id: str,
) -> Record:
    """Apply cleaning, then schema, field and cross rules to one item.

    Pass a :class:`RecordChecker` (built once per run) to get duplicate detection across
    records and to avoid recompiling the rules for every item.
    """
    checker = rules if isinstance(rules, RecordChecker) else RecordChecker(rules)
    return checker.check(data, run_id=run_id)


def check_batch(
    records: list[Record], rules: DataRules, baseline: Baseline, *, run_id: str
) -> list[Finding]:
    """Duplicates and batch-vs-history rules."""
    return batch_findings(BatchStats.from_records(records, rules), rules, baseline, run_id=run_id)


def check_records(
    items: Iterable[Mapping[str, Any]],
    rules: DataRules,
    *,
    baseline: Baseline | None = None,
    run: Run | None = None,
) -> CheckResult:
    """Check an iterable of items end to end (records, batch findings, verdict).

    The verdict fails closed: if anything raises while checking, the run is held with the
    reason, and the records checked so far are returned.
    """
    run = run or Run(name=rules.name)
    baseline = baseline or Baseline()
    checker = RecordChecker(rules)
    batch = BatchStats(rules)
    records: list[Record] = []
    findings: list[Finding] = []
    failed = False
    try:
        for item in items:
            rec = checker.check(item, run_id=run.run_id)
            records.append(rec)
            batch.add(rec)
        findings = batch_findings(batch, rules, baseline, run_id=run.run_id)
    except Exception as exc:
        failed = True
        findings.append(
            Finding(
                run_id=run.run_id,
                scope=FindingScope.RUN,
                check="run.checks_failed",
                severity=Severity.HOLD,
                message=f"The checks failed: {type(exc).__name__}: {exc}",
            )
        )
    fill_record_counts(run, batch, records)
    run.findings = [*run.findings, *findings]
    run.verdict = compute_verdict(run, findings, rules.verdict, checks_failed=failed)
    run.verdict.baseline_runs = list(baseline.run_ids)
    return CheckResult(records=records, batch_findings=findings, run=run)


def fill_record_counts(run: Run, batch: BatchStats, records: Iterable[Record]) -> None:
    """Record counters on the Run from a batch (findings and fixes from the records)."""
    c = run.counts
    run.samples = batch.samples.result()
    c.records = batch.records
    c.usable_records = batch.usable
    c.duplicates = batch.duplicates
    c.schema_invalid = batch.schema_invalid
    for rec in records:
        for f in rec.findings:
            c.findings_by_severity[f.severity.value] = (
                c.findings_by_severity.get(f.severity.value, 0) + 1
            )
            if f.check.startswith("clean.") and f.severity != Severity.INFO:
                c.flags += 1
        for fix in rec.fixes:
            c.fixes_by_normalizer[fix.normalizer] = c.fixes_by_normalizer.get(fix.normalizer, 0) + 1


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
