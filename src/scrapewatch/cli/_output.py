"""Text the CLI prints. Plain words first, technical detail second (architecture 13.1)."""

from __future__ import annotations

from typing import Any

from scrapewatch.models import Run

_MAX_FINDING_LINES = 8


def _example(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:39] + "…"


def check_summary(run: Run, *, gate_line: str | None = None, files: list[str] | None = None) -> str:
    """One screen: verdict, reasons, counts, fixes, the main findings and where things went."""
    v = run.verdict
    c = run.counts
    lines = [f"scrapewatch · {run.name} · {v.status.value.upper()}"]
    lines += [f"  {reason}" for reason in v.reasons] or ["  All checks passed."]
    lines.append(
        f"records {c.records:,} · usable {c.usable_records:,} · duplicates {c.duplicates:,}"
        f" · schema invalid {c.schema_invalid:,} · flagged values {c.flags:,}"
    )
    if c.fixes_by_normalizer:
        fixes = " · ".join(f"{k} {n:,}" for k, n in sorted(c.fixes_by_normalizer.items()))
        lines.append(f"fixes: {fixes}")
    sev = c.findings_by_severity
    lines.append(
        f"findings: hold {sev.get('hold', 0):,} · warn {sev.get('warn', 0):,}"
        f" · info {sev.get('info', 0):,}"
    )
    shown = [s for s in run.samples.findings if s.severity.value != "info"]
    for s in shown[:_MAX_FINDING_LINES]:
        where = f" {s.field}" if s.field else ""
        eg = f"  e.g. {', '.join(_example(x) for x in s.examples)}" if s.examples else ""
        lines.append(f"  {s.severity.value:<4} {s.check}{where}: {s.count:,}{eg}")
    if len(shown) > _MAX_FINDING_LINES:
        lines.append(f"  … {len(shown) - _MAX_FINDING_LINES} more kinds in the summary")
    batch = [f for f in run.findings if f.severity.value != "info"]
    for f in batch[:_MAX_FINDING_LINES]:
        lines.append(f"  {f.severity.value:<4} {f.check}: {f.message}")
    if gate_line:
        lines.append(gate_line)
    for path in files or []:
        lines.append(path)
    return "\n".join(lines)
