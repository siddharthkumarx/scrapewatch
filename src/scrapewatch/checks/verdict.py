"""Verdict computation. Table of rules and defaults: docs/architecture.md "Verdict logic"."""

from __future__ import annotations

from collections.abc import Iterable

from scrapewatch.checks.rules import VerdictThresholds
from scrapewatch.models import Finding, Run, RunStatus, Severity, Verdict, VerdictStatus


def _pct(x: float) -> str:
    return f"{x:.1f}%"


def compute_verdict(
    run: Run,
    findings: Iterable[Finding],
    thresholds: VerdictThresholds,
    *,
    checks_failed: bool = False,
    expect_records: bool = True,
) -> Verdict:
    """Fold findings and run counters into pass/warn/hold with plain-words reasons.

    ``findings`` are the batch- and run-scope findings; record and response findings enter
    through ``run.counts.findings_by_severity``. ``checks_failed=True`` (the engine crashed,
    rules could not load, history could not be read for batch checks) always yields hold:
    the gate fails closed. ``expect_records=False`` (no data rules and no records: a
    responses-only run) skips the minimum-usable-records rule.
    """
    t = thresholds
    c = run.counts
    holds: list[str] = []
    warns: list[str] = []

    if checks_failed:
        holds.append(
            "The checks could not run (rules could not be loaded, a check failed, or history "
            "could not be read), so the output is held to be safe."
        )
    if run.status == RunStatus.CRASHED:
        holds.append("The scraper crashed before the run finished.")

    if c.document_responses:
        share = c.bad_documents / c.document_responses * 100
        if share > t.blocked_share_hold_pct:
            holds.append(
                f"Blocked share {_pct(share)} is above {t.blocked_share_hold_pct:g}% "
                f"({c.bad_documents} of {c.document_responses} pages)."
            )
        elif share > t.blocked_share_warn_pct:
            warns.append(
                f"Blocked share {_pct(share)} is above {t.blocked_share_warn_pct:g}% "
                f"({c.bad_documents} of {c.document_responses} pages)."
            )
    if c.records:
        share = c.schema_invalid / c.records * 100
        if share > t.schema_invalid_hold_pct:
            holds.append(
                f"{_pct(share)} of records failed the schema, above {t.schema_invalid_hold_pct:g}%."
            )
        elif share > t.schema_invalid_warn_pct:
            warns.append(
                f"{_pct(share)} of records failed the schema, above {t.schema_invalid_warn_pct:g}%."
            )
        flagged = c.flags / c.records * 100
        if flagged > t.flagged_warn_pct:
            warns.append(
                f"{c.flags} values could not be cleaned safely ({_pct(flagged)} of records), "
                f"above {t.flagged_warn_pct:g}%."
            )
    if c.monitor_errors >= t.monitor_errors_warn > 0:
        warns.append(f"Monitoring hit {c.monitor_errors} internal error(s); see the log.")
    if expect_records and c.usable_records < t.min_usable_records:
        holds.append(
            f"Only {c.usable_records} usable record(s); at least {t.min_usable_records} "
            "are required."
        )

    run_findings = list(findings)
    counts = {s.value: 0 for s in Severity}
    for sev, n in c.findings_by_severity.items():
        counts[sev] = counts.get(sev, 0) + n
    for f in run_findings:
        counts[f.severity.value] += 1
        if f.severity == Severity.HOLD:
            holds.append(f.message)
        elif f.severity == Severity.WARN:
            warns.append(f.message)
    record_holds = c.findings_by_severity.get(Severity.HOLD.value, 0)
    record_warns = c.findings_by_severity.get(Severity.WARN.value, 0)
    if record_holds:
        holds.append(f"{record_holds} record finding(s) with severity hold.")
    if record_warns:
        warns.append(f"{record_warns} record finding(s) with severity warn.")

    status = VerdictStatus.PASS
    reasons: list[str] = []
    if holds:
        status = VerdictStatus.HOLD
        reasons = holds + warns
    elif warns:
        status = VerdictStatus.WARN
        reasons = warns
    return Verdict(
        status=status,
        reasons=reasons,
        counts=counts,
        thresholds=t.model_dump(),
    )
