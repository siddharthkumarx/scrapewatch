"""Batch checks: the whole run against the baseline (docs/architecture.md section 8.2).

:class:`BatchStats` takes records one at a time and keeps only what the checks need (counts,
field statistics, value hashes, currency mismatches), so a run never has to hold its records.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Iterable
from typing import Any

from scrapewatch.checks.rules import DataRules
from scrapewatch.history.baselines import Baseline
from scrapewatch.history.stats import TOP_VALUES, StatsAccumulator, value_hash
from scrapewatch.models import (
    Finding,
    FindingSample,
    FindingScope,
    FixSample,
    Record,
    RecordSamples,
    Severity,
)
from scrapewatch.redact import redact_url

EXAMPLE_TEXT_MAX = 200  # characters kept of one example string
PSI_FLOOR = 0.005  # 0.5% floor on both shares, so empty bins do not give infinite PSI


def psi(actual_shares: list[float], baseline_shares: list[float]) -> float:
    """Population stability index: sum of (a - b) * ln(a / b), with 0.5% floors on a and b."""
    total = 0.0
    for a, b in zip(actual_shares, baseline_shares, strict=True):
        a = max(a, PSI_FLOOR)
        b = max(b, PSI_FLOOR)
        total += (a - b) * math.log(a / b)
    return total


def example_value(value: Any) -> Any:
    """A value as the report may show it: URLs redacted, long text cut, containers summarised."""
    if isinstance(value, str):
        if value.startswith(("http://", "https://", "//")):
            value = redact_url(value)
        return value if len(value) <= EXAMPLE_TEXT_MAX else value[: EXAMPLE_TEXT_MAX - 1] + "…"
    if isinstance(value, (dict, list)):
        text = repr(value)
        return text if len(text) <= EXAMPLE_TEXT_MAX else text[: EXAMPLE_TEXT_MAX - 1] + "…"
    return value


class SampleKeeper:
    """Counts record findings and fixes by kind and keeps the first few examples of each."""

    def __init__(self, examples: int = 3) -> None:
        """Keep at most ``examples`` values per kind (0 keeps counts only)."""
        self.examples = examples
        self.findings: dict[tuple[str, str | None, Severity], FindingSample] = {}
        self.fixes: dict[tuple[str, str], FixSample] = {}

    def add(self, record: Record) -> None:
        """Account the findings and fixes of one record."""
        for f in record.findings:
            key = (f.check, f.field, f.severity)
            sample = self.findings.get(key)
            if sample is None:
                sample = FindingSample(check=f.check, field=f.field, severity=f.severity)
                self.findings[key] = sample
            sample.count += 1
            if len(sample.examples) < self.examples:
                sample.examples.append(example_value(f.observed))
        for fix in record.fixes:
            fkey = (fix.normalizer, fix.field)
            fs = self.fixes.get(fkey)
            if fs is None:
                fs = self.fixes[fkey] = FixSample(normalizer=fix.normalizer, field=fix.field)
            fs.count += 1
            if len(fs.examples) < self.examples:
                fs.examples.append(
                    {"before": example_value(fix.before), "after": example_value(fix.after)}
                )

    def result(self) -> RecordSamples:
        """Samples ordered by severity (hold first), then count."""
        rank = {Severity.HOLD: 0, Severity.WARN: 1, Severity.INFO: 2}
        findings = sorted(
            self.findings.values(), key=lambda s: (rank[s.severity], -s.count, s.check)
        )
        fixes = sorted(self.fixes.values(), key=lambda s: (-s.count, s.normalizer, s.field))
        return RecordSamples(findings=findings, fixes=fixes)


def bin_shares(values: list[float], edges: list[float]) -> list[float]:
    """Share of values in each baseline bin (values outside go to the end bins)."""
    bins = len(edges) - 1
    counts = [0] * bins
    for v in values:
        idx = bisect.bisect_right(edges, v) - 1
        counts[min(max(idx, 0), bins - 1)] += 1
    n = len(values)
    return [c / n for c in counts] if n else [0.0] * bins


class BatchStats:
    """What batch checks need from a run's records, accumulated one record at a time."""

    def __init__(self, rules: DataRules | None = None) -> None:
        """Start empty; ``rules`` tells which currency fields to watch."""
        self.rules = rules
        self.records = 0
        self.usable = 0
        self.duplicates = 0
        self.schema_invalid = 0
        self.flagged_records = 0
        self.stats = StatsAccumulator()
        self.value_hashes: dict[str, str] = {}
        self.currency_checked = 0
        self.currency_mismatches = 0
        self.currency_examples: list[Any] = []
        self.samples = SampleKeeper(rules.examples if rules is not None else 3)

    def add(self, record: Record) -> None:
        """Account one checked record."""
        self.records += 1
        self.samples.add(record)
        if record.usable:
            self.usable += 1
        checks = {f.check for f in record.findings}
        if "dup.repeat" in checks:
            self.duplicates += 1
        if "schema.invalid" in checks:
            self.schema_invalid += 1
        if any(c.startswith("clean.") for c in checks):
            self.flagged_records += 1
        self.stats.add(record.data)
        if record.dedupe_key and record.usable:
            self.value_hashes.setdefault(record.dedupe_key, value_hash(record.data))
        rule = self.rules.batch.currency if self.rules is not None else None
        if rule is not None:
            value = record.data.get(rule.field)
            if value is not None and value != "":
                self.currency_checked += 1
                conflict = any(
                    f.check.endswith(".extra_conflict") and f.field == rule.field
                    for f in record.findings
                )
                outside = rule.expected is not None and value not in rule.expected
                if conflict or outside:
                    self.currency_mismatches += 1
                    if len(self.currency_examples) < 3:
                        self.currency_examples.append(value)

    @classmethod
    def from_records(cls, records: Iterable[Record], rules: DataRules | None) -> BatchStats:
        """Accumulate a list of records."""
        out = cls(rules)
        for rec in records:
            out.add(rec)
        return out


def _pct(x: float) -> str:
    return f"{x:.1f}%"


def _num(x: float) -> str:
    return f"{x:,.4g}" if abs(x) < 1e6 else f"{x:,.0f}"


def batch_findings(
    batch: BatchStats, rules: DataRules, baseline: Baseline, *, run_id: str
) -> list[Finding]:
    """Every batch check of section 8.2 (info only, apart from absolute rules, without baseline)."""
    out: list[Finding] = []

    def add(check: str, sev: Severity, msg: str, **kw: Any) -> None:
        out.append(
            Finding(
                run_id=run_id,
                scope=FindingScope.BATCH,
                check=check,
                severity=sev,
                message=msg,
                **kw,
            )
        )

    b = rules.batch
    n = batch.records
    results = batch.stats.result()

    # Checks that need no history.
    if b.row_count.min_rows is not None and n < b.row_count.min_rows:
        add(
            "batch.row_count",
            Severity.HOLD,
            f"Row count {n} is below the minimum of {b.row_count.min_rows}.",
            observed=n,
            expected={"min_rows": b.row_count.min_rows},
        )
    for name, frule in rules.fields.items():
        if frule.coverage_hold_below_pct is None or n == 0:
            continue
        cov = results[name].coverage * 100 if name in results else 0.0
        if cov < frule.coverage_hold_below_pct:
            add(
                "batch.coverage",
                Severity.HOLD,
                f"Only {_pct(cov)} of records have {name}; at least "
                f"{frule.coverage_hold_below_pct:g}% are required.",
                field=name,
                observed=round(cov, 4),
                expected={"coverage_hold_below_pct": frule.coverage_hold_below_pct},
            )
    if n:
        dup_pct = batch.duplicates / n * 100
        sev = None
        if b.duplicates_hold_pct is not None and dup_pct > b.duplicates_hold_pct:
            sev = Severity.HOLD
        elif dup_pct > b.duplicates_warn_pct:
            sev = Severity.WARN
        if sev is not None:
            add(
                "batch.duplicates",
                sev,
                f"{batch.duplicates} records ({_pct(dup_pct)}) were duplicates.",
                observed=round(dup_pct, 4),
                expected={"warn_pct": b.duplicates_warn_pct, "hold_pct": b.duplicates_hold_pct},
            )
    if b.currency is not None and batch.currency_checked:
        share = batch.currency_mismatches / batch.currency_checked * 100
        if share > b.currency.hold_mismatch_pct:
            add(
                "batch.currency",
                Severity.HOLD,
                f"{batch.currency_mismatches} records ({_pct(share)}) have a currency that "
                f"disagrees with the price or is not one of {b.currency.expected}; "
                f"the limit is {b.currency.hold_mismatch_pct:g}%.",
                field=b.currency.field,
                observed=batch.currency_examples,
                expected=b.currency.expected,
            )

    if not baseline.available:
        add(
            "batch.baseline",
            Severity.INFO,
            f"No baseline yet: {baseline.candidate_runs} of {baseline.min_runs} passing runs. "
            "History comparisons start once there are enough.",
            observed=baseline.candidate_runs,
            expected=baseline.min_runs,
        )
        return out

    # Row count against the baseline median.
    median = baseline.row_count_median or 0.0
    if median > 0:
        change = (n - median) / median * 100
        sev = None
        if change < -b.row_count.hold_pct:
            sev = Severity.HOLD
        elif abs(change) > b.row_count.warn_pct:
            sev = Severity.WARN
        if sev is not None:
            direction = "below" if change < 0 else "above"
            add(
                "batch.row_count",
                sev,
                f"Row count {n} is {abs(change):.0f}% {direction} baseline {median:g}.",
                observed=n,
                expected={
                    "baseline": median,
                    "warn_pct": b.row_count.warn_pct,
                    "hold_pct": b.row_count.hold_pct,
                },
            )

    fields = list(rules.fields) or sorted(baseline.fields)
    for name in fields:
        base = baseline.fields.get(name)
        if base is None:
            continue
        cur = results.get(name)
        cov_now = cur.coverage if cur is not None else 0.0
        if base.get("coverage") is not None and n:
            drop = (base["coverage"] - cov_now) * 100
            sev = None
            if drop > b.coverage.hold_drop_points:
                sev = Severity.HOLD
            elif drop > b.coverage.warn_drop_points:
                sev = Severity.WARN
            if sev is not None:
                add(
                    "batch.coverage",
                    sev,
                    f"Coverage of {name} fell from {_pct(base['coverage'] * 100)} to "
                    f"{_pct(cov_now * 100)} ({drop:.1f} points).",
                    field=name,
                    observed=round(cov_now * 100, 4),
                    expected={"baseline": round(base["coverage"] * 100, 4)},
                )
        numbers = batch.stats.numbers(name)
        hist = base.get("hist")
        if hist and numbers:
            value = psi(bin_shares(numbers, hist["edges"]), hist["shares"])
            sev = None
            if b.distribution.psi_hold is not None and value > b.distribution.psi_hold:
                sev = Severity.HOLD
            elif value > b.distribution.psi_warn:
                sev = Severity.WARN
            if sev is not None:
                add(
                    "batch.distribution.numeric",
                    sev,
                    f"The distribution of {name} shifted (PSI {value:.2f}, limit "
                    f"{b.distribution.psi_warn:g}).",
                    field=name,
                    observed=round(value, 6),
                    expected={"psi_warn": b.distribution.psi_warn},
                )
        strings = batch.stats.strings(name)
        known = base.get("top_values") or []
        distinct = base.get("distinct_count")
        if strings and known and distinct is not None and distinct <= TOP_VALUES:
            total = sum(strings.values())
            new = sum(c for v, c in strings.items() if v not in set(known))
            share = new / total * 100
            if share > b.distribution.new_values_warn_pct:
                unseen = sorted(v for v in strings if v not in set(known))[:5]
                add(
                    "batch.distribution.categorical",
                    Severity.WARN,
                    f"{_pct(share)} of {name} values were never seen before "
                    f"(e.g. {', '.join(map(repr, unseen))}).",
                    field=name,
                    observed=unseen,
                    expected={"new_values_warn_pct": b.distribution.new_values_warn_pct},
                )

    # Unit errors: median moved by about x100 or x1000 (either way).
    for name in b.unit_error.fields:
        base = baseline.fields.get(name) or {}
        numbers = batch.stats.numbers(name)
        if not numbers or not base.get("p50"):
            continue
        median_now = statistics.median(numbers)
        ratio = median_now / base["p50"]
        tol = b.unit_error.tolerance_pct / 100
        for r in b.unit_error.ratios:
            for factor, word in ((r, "times"), (1 / r, "1/")):
                if ratio > 0 and abs(ratio / factor - 1) <= tol:
                    shown = f"about {r:g}x" if word == "times" else f"about 1/{r:g} of"
                    add(
                        "batch.unit_error",
                        b.unit_error.severity,
                        f"Median {name} {_num(median_now)} is {shown} the baseline "
                        f"{_num(base['p50'])}: a unit or scale error?",
                        field=name,
                        observed=median_now,
                        expected={"baseline_p50": base["p50"], "ratio": factor},
                    )

    # Changed share: same dedupe key, different values, vs the previous passing run.
    prev = baseline.previous_value_hashes
    common = [k for k in batch.value_hashes if k in prev]
    if common:
        changed = sum(1 for k in common if batch.value_hashes[k] != prev[k])
        share = changed / len(common) * 100
        rule = b.changed_share
        sev = None
        if rule.hold_above_pct is not None and share > rule.hold_above_pct:
            sev = Severity.HOLD
        elif share > rule.warn_above_pct or (
            rule.warn_below_pct is not None and share < rule.warn_below_pct
        ):
            sev = Severity.WARN
        if sev is not None:
            add(
                "batch.changed_share",
                sev,
                f"{_pct(share)} of {len(common)} records seen in the previous run changed.",
                observed=round(share, 4),
                expected=rule.model_dump(),
            )
    return out
