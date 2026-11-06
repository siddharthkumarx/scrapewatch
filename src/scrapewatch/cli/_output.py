"""Text the CLI prints. Plain words first, technical detail second (architecture 13.1)."""

from __future__ import annotations

from typing import Any

from scrapewatch.models import GuardResult, Run

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


def guard_result(result: GuardResult) -> str:
    """What answered, in the order a contributor reads it."""
    lines = [
        f"label: {result.label.value}",
        f"vendor: {result.vendor or '-'}",
        f"rule: {result.rule_id or '-'}",
        f"confidence: {result.confidence:g}",
    ]
    lines.append("signals:" if result.signals else "signals: none")
    lines += [f"  - {s}" for s in result.signals]
    return "\n".join(lines)


def _money(x: float | None, currency: str) -> str:
    if x is None:
        return "-"
    if 0 < abs(x) < 0.0001:
        return f"< 0.0001 {currency}"
    return f"{x:,.6f}".rstrip("0").rstrip(".") + f" {currency}"


def runs_table(runs: list[Run]) -> str:
    """Recent runs, newest first, one per line."""
    if not runs:
        return "no runs yet"
    head = f"{'started (UTC)':<19}  {'name':<16} {'verdict':<7} {'records':>8} {'usable':>8}  run"
    lines = [head]
    for r in runs:
        verdict = r.verdict.status.value if r.status.value != "running" else "running"
        lines.append(
            f"{r.started_at:%Y-%m-%d %H:%M:%S}  {r.name[:16]:<16} {verdict:<7} "
            f"{r.counts.records:>8,} {r.counts.usable_records:>8,}  {r.run_id}"
        )
    return "\n".join(lines)


def cost_text(run: Run) -> str:
    """Cost and waste breakdown of one run."""
    c, w = run.cost, run.waste
    cur = c.currency
    lines = [
        f"scrapewatch · {run.name} · run {run.run_id}",
        f"total {_money(c.total, cur)} · per usable record {_money(c.per_usable_record, cur)}"
        f" · per record {_money(c.per_record, cur)}",
    ]
    for title, table in (
        ("by domain", c.by_domain),
        ("by proxy", c.by_proxy),
        ("by resource type", c.by_resource_type),
    ):
        if table:
            lines.append(f"{title}:")
            lines += [f"  {k:<30} {_money(v, cur)}" for k, v in sorted(table.items())]
    if c.browser_minutes:
        lines.append(f"browser {c.browser_minutes:,.1f} min · {_money(c.browser_cost, cur)}")
    if c.api_requests:
        lines.append(f"API requests {c.api_requests:,} · {_money(c.api_cost, cur)}")
    lines.append("waste (requests, bytes, cost):")
    for key, words in (
        ("retry", "retries"),
        ("failed", "failed or blocked"),
        ("unchanged_refetch", "unchanged refetches"),
        ("blockable", "images, fonts, media"),
    ):
        lines.append(
            f"  {words:<22} {getattr(w, key + '_requests'):>8,} {getattr(w, key + '_bytes'):>14,}"
            f"  {_money(getattr(w, key + '_cost'), cur)}"
        )
    lines.append(f"  {'total':<22} {'':>8} {'':>14}  {_money(w.total_waste_cost, cur)}")
    if w.blocked_requests or w.deduped_requests or w.conditional_hits:
        lines.append(
            f"waste cutter: {w.blocked_requests:,} blocked (about {w.bytes_saved_estimate:,} "
            f"bytes saved, estimated), {w.deduped_requests:,} deduped, "
            f"{w.conditional_hits:,} unchanged"
        )
    return "\n".join(lines)
