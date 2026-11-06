"""Static HTML report: one file, inline CSS and SVG, no external scripts or fonts.

Sections follow docs/architecture.md 13.1. Every value from the run is escaped with
``html.escape``; URLs from the data appear as text, never as links, so the only links in a
report are the project's own in the footer. The output is deterministic for a given input
(no clock reads), which is what the golden test relies on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from html import escape
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any

from scrapewatch.models import BAD_LABELS, GuardLabel, ResponseEvent, Run, Severity
from scrapewatch.report._svg import bar, sparkline
from scrapewatch.report.summary import write_text_atomic

PROJECT_URL = "https://github.com/siddharthkumarx/scrapewatch"
HISTORY_RUNS = 30
EXAMPLE_CHARS = 80

LABEL_WORDS: dict[str, str] = {
    "ok": "pages with content",
    "block": "blocked",
    "challenge": "challenge page",
    "captcha": "CAPTCHA",
    "consent": "consent wall",
    "login": "login wall",
    "rate_limit": "rate limited",
    "empty": "empty page",
    "redirect": "redirected away",
    "error_page": "error page",
    "error": "request failed",
    "unknown": "could not be read",
}
CATEGORY_WORDS: dict[str, str] = {
    "retry": "Retries",
    "failed": "Failed or blocked responses",
    "unchanged_refetch": "Pages fetched again without changes",
    "blockable": "Images, fonts and media",
}
_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "INR": "₹"}
_SEVERITY_RANK = {Severity.HOLD: 0, Severity.WARN: 1, Severity.INFO: 2}


# --- formatting --------------------------------------------------------------------------


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _n(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _pct(part: float, whole: float) -> str:
    return "\u2013" if not whole else f"{100.0 * part / whole:.1f}%"


def _bytes(n: float) -> str:
    for unit, size in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if abs(n) >= size:
            return f"{n / size:,.1f} {unit}"
    return f"{n:,.0f} B"


def _money(x: float | None, currency: str) -> str:
    if x is None:
        return "\u2013"
    sym = _SYMBOLS.get(currency, currency + " ")
    if x == 0:
        return f"{sym}0"
    if 0 < abs(x) < 0.0001:
        return f"&lt; {sym}0.0001"
    if abs(x) >= 1:
        return f"{sym}{x:,.2f}"
    return f"{sym}{x:.4f}" if abs(x) >= 0.01 else f"{sym}{x:.6f}".rstrip("0")


def _when(dt: datetime | None) -> str:
    return "\u2013" if dt is None else dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _duration(run: Run) -> str:
    if run.finished_at is None:
        return "still running"
    seconds = max(0, round((run.finished_at - run.started_at).total_seconds()))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min {sec} s" if minutes else f"{sec} s"


def _example(value: Any) -> str:
    text = f'"{value}"' if isinstance(value, str) else repr(value)
    if len(text) > EXAMPLE_CHARS:
        text = text[: EXAMPLE_CHARS - 1] + "…"
    return f"<code>{_e(text)}</code>"


def _examples(values: Sequence[Any]) -> str:
    return ", ".join(_example(v) for v in values) if values else '<span class="muted">\u2013</span>'


def _tile(value: str, key: str) -> str:
    return f'<div class="tile"><div class="v">{value}</div><div class="k">{_e(key)}</div></div>'


def _table(head: Sequence[str], rows: Sequence[Sequence[str]], num: Sequence[int] = ()) -> str:
    """Rows are already-escaped HTML; columns listed in ``num`` are right-aligned."""
    th = "".join(
        f'<th class="num">{_e(h)}</th>' if i in num else f"<th>{_e(h)}</th>"
        for i, h in enumerate(head)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="num">{c}</td>' if i in num else f"<td>{c}</td>" for i, c in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _sev(severity: Severity | str) -> str:
    value = severity.value if isinstance(severity, Severity) else str(severity)
    return f'<span class="sev {_e(value)}">{_e(value)}</span>'


def _blocked_share(run: Run) -> float | None:
    c = run.counts
    return None if not c.document_responses else 100.0 * c.bad_documents / c.document_responses


# --- sections ----------------------------------------------------------------------------


def _header(run: Run) -> str:
    status = run.verdict.status.value
    meta = [
        f"started {_when(run.started_at)}",
        f"finished {_when(run.finished_at)}",
        f"took {_duration(run)}",
        f"scrapewatch {_e(run.scrapewatch_version or '?')}",
    ]
    if run.rules_version:
        meta.append(f"guard rules {_e(run.rules_version)}")
    meta.append(f"run <code>{_e(run.run_id)}</code>")
    if run.status.value != "finished":
        meta.append(f"<strong>status {_e(run.status.value)}</strong>")
    return (
        f"<header><h1>{_e(run.name)}</h1>"
        f'<span class="badge {status}">{status.upper()}</span>'
        f'<div class="meta">{" · ".join(meta)}</div></header>'
    )


def _meaning(run: Run, gate: str | None) -> str:
    out = ['<section id="meaning"><h2>What this means</h2>']
    if run.verdict.reasons:
        items = "".join(f"<li>{_e(r)}</li>" for r in run.verdict.reasons)
        out.append(f'<ul class="reasons">{items}</ul>')
    else:
        out.append('<p class="reasons">Everything checked out: no problems were found.</p>')
    advice = {
        "pass": "The output can be used.",
        "warn": "The output can be used, but someone should look at the points above.",
        "hold": "Do not use this output; the last good output should stay live.",
    }[run.verdict.status.value]
    out.append(f"<p>{advice}</p>")
    if gate:
        out.append(f"<p><strong>Output:</strong> {_e(gate)}</p>")
    notes = [f for f in run.findings if f.scope.value == "run"]
    if notes:
        rows = [[_sev(f.severity), f"<code>{_e(f.check)}</code>", _e(f.message)] for f in notes]
        out.append(_table(["severity", "check", "detail"], rows))
    if run.verdict.baseline_runs:
        out.append(
            f'<p class="small">Compared with {len(run.verdict.baseline_runs)} earlier passing '
            "runs of the same name.</p>"
        )
    out.append("</section>")
    return "".join(out)


def _responses(run: Run, bad_events: Sequence[ResponseEvent]) -> str:
    c = run.counts
    out = ['<section id="responses"><h2>Responses</h2>']
    if not c.responses:
        out.append("<p>No responses were recorded in this run (records only).</p></section>")
        return "".join(out)
    share = _blocked_share(run)
    out.append(
        '<div class="tiles">'
        + _tile(_n(c.responses), "responses")
        + _tile(_pct(c.bad_documents, c.document_responses), "pages without data")
        + _tile(_n(c.retries), "retries")
        + _tile(_bytes(c.wire_bytes), "downloaded")
        + "</div>"
    )
    if share is not None:
        out.append(
            f"<p>{_n(c.bad_documents)} of {_n(c.document_responses)} pages "
            f"({share:.1f}%) were blocked, empty or otherwise without data.</p>"
        )
    top = max(c.by_label.values(), default=0)
    rows = []
    for label, n in sorted(c.by_label.items(), key=lambda kv: (-kv[1], kv[0])):
        kind = "bad" if label in {lbl.value for lbl in BAD_LABELS} else ""
        rows.append(
            [
                _e(LABEL_WORDS.get(label, label)),
                f"<code>{_e(label)}</code>",
                _n(n),
                _pct(n, c.responses),
                bar(n, top, kind=kind),
            ]
        )
    out.append("<h3>What answered</h3>")
    out.append(_table(["meaning", "label", "responses", "share", ""], rows, num=(2, 3)))
    if c.by_domain:
        top_d = max(c.by_domain.values())
        rows = [
            [_e(d), _n(n), _money(run.cost.by_domain.get(d), run.cost.currency), bar(n, top_d)]
            for d, n in sorted(c.by_domain.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        out.append("<h3>By domain</h3>")
        out.append(_table(["domain", "responses", "cost", ""], rows, num=(1, 2)))
    if bad_events:
        rows = []
        for ev in bad_events[:20]:
            g = ev.guard
            signals = "; ".join(g.signals) if g.signals else "\u2013"
            rows.append(
                [
                    f"<code>{_e(ev.final_url or ev.url)}</code>",
                    _e(LABEL_WORDS.get(g.label.value, g.label.value)),
                    _e(g.vendor or "\u2013"),
                    _e("\u2013" if ev.status is None else ev.status),
                    f'<span class="small">{_e(g.rule_id or "")} {_e(signals)}</span>',
                ]
            )
        out.append("<h3>Pages without data (first 20)</h3>")
        out.append(_table(["URL (secrets removed)", "what", "vendor", "status", "why"], rows))
    out.append("</section>")
    return "".join(out)


def _batch_value(value: Any) -> str:
    if value is None:
        return "\u2013"
    if isinstance(value, float):
        return _e(f"{value:,.4g}")
    if isinstance(value, dict):
        return _e(", ".join(f"{k} {v}" for k, v in value.items()))
    if isinstance(value, list):
        return _examples(value)
    return _e(value)


def _data(run: Run) -> str:
    c = run.counts
    out = ['<section id="data"><h2>Data checks</h2>']
    out.append(
        '<div class="tiles">'
        + _tile(_n(c.records), "records")
        + _tile(_n(c.usable_records), "usable records")
        + _tile(_n(c.duplicates), "duplicates")
        + _tile(_n(c.schema_invalid), "failed the schema")
        + "</div>"
    )
    if not c.records:
        out.append("<p>No records were recorded in this run.</p>")
    kinds = [s for s in run.samples.findings if not s.check.startswith("clean.")]
    if kinds:
        rows = [
            [
                _sev(s.severity),
                f"<code>{_e(s.check)}</code>",
                _e(s.field or "\u2013"),
                _n(s.count),
                _examples(s.examples),
            ]
            for s in kinds
        ]
        out.append("<h3>Record findings</h3>")
        out.append(_table(["severity", "check", "field", "records", "examples"], rows, num=(3,)))
    elif c.records:
        out.append("<p>Every record passed the schema, field and cross-field rules.</p>")
    batch = [f for f in run.findings if f.scope.value == "batch"]
    if batch:
        batch.sort(key=lambda f: (_SEVERITY_RANK[f.severity], f.check, f.field or ""))
        rows = [
            [
                _sev(f.severity),
                f"<code>{_e(f.check)}</code>",
                _e(f.field or "\u2013"),
                _e(f.message),
                _batch_value(f.observed),
                _batch_value(f.expected),
            ]
            for f in batch
        ]
        out.append("<h3>This run compared with earlier runs</h3>")
        out.append(_table(["severity", "check", "field", "what", "this run", "expected"], rows))
    if run.verdict.thresholds:
        items = ", ".join(f"{_e(k)} {_e(v)}" for k, v in sorted(run.verdict.thresholds.items()))
        out.append(f'<p class="small">Thresholds used: {items}.</p>')
    out.append("</section>")
    return "".join(out)


def _cleaning(run: Run) -> str:
    out = ['<section id="cleaning"><h2>Cleaning</h2>']
    fixes = run.samples.fixes
    if fixes:
        total = sum(f.count for f in fixes)
        out.append(f"<p>{_n(total)} values were changed; every change is listed by kind.</p>")
        rows = []
        for f in fixes:
            pairs = ", ".join(
                f"{_example(x['before'])} → {_example(x['after'])}" for x in f.examples
            )
            rows.append(
                [
                    f"<code>{_e(f.normalizer)}</code>",
                    _e(f.field),
                    _n(f.count),
                    pairs or '<span class="muted">\u2013</span>',
                ]
            )
        out.append(_table(["cleaner", "field", "values changed", "examples"], rows, num=(2,)))
    else:
        out.append("<p>No values were changed.</p>")
    flags = [s for s in run.samples.findings if s.check.startswith("clean.")]
    if flags:
        rows = [
            [
                _sev(s.severity),
                f"<code>{_e(s.check.split('.', 2)[1])}</code>",
                _e(s.check.split(".", 2)[2].replace("_", " ")),
                _e(s.field or "\u2013"),
                _n(s.count),
                _examples(s.examples),
            ]
            for s in flags
        ]
        out.append("<h3>Values left as they were</h3>")
        out.append(
            "<p>These values have more than one reading or cannot be made safe, so they were "
            "not changed.</p>"
        )
        out.append(
            _table(["severity", "cleaner", "reason", "field", "values", "examples"], rows, num=(4,))
        )
    out.append("</section>")
    return "".join(out)


def _cost(run: Run) -> str:
    cost, waste, c = run.cost, run.waste, run.counts
    cur = cost.currency
    out = ['<section id="cost"><h2>Cost</h2>']
    if not c.responses and not cost.total:
        out.append("<p>No cost was recorded: this run has no responses.</p></section>")
        return "".join(out)
    out.append(
        '<div class="tiles">'
        + _tile(_money(cost.total, cur), "total")
        + _tile(_money(cost.per_usable_record, cur), "per usable record")
        + _tile(_money(cost.per_record, cur), "per record")
        + _tile(_money(waste.total_waste_cost, cur), "bought nothing usable")
        + "</div>"
    )
    if cost.by_proxy:
        rows = [[_e(k), _money(v, cur)] for k, v in sorted(cost.by_proxy.items())]
        out.append("<h3>By proxy or API</h3>")
        out.append(_table(["label", "cost"], rows, num=(1,)))
    if cost.browser_minutes:
        out.append(
            f"<p>Browser time: {cost.browser_minutes:,.1f} minutes, "
            f"{_money(cost.browser_cost, cur)}.</p>"
        )
    rows = []
    for key, words in CATEGORY_WORDS.items():
        rows.append(
            [
                _e(words),
                _n(getattr(waste, f"{key}_requests")),
                _bytes(getattr(waste, f"{key}_bytes")),
                _money(getattr(waste, f"{key}_cost"), cur),
            ]
        )
    rows.append(
        [
            "<strong>Total</strong>",
            "",
            "",
            f"<strong>{_money(waste.total_waste_cost, cur)}</strong>",
        ]
    )
    out.append("<h3>Waste</h3>")
    out.append(_table(["what", "requests", "downloaded", "cost"], rows, num=(1, 2, 3)))
    if (
        waste.blocked_requests
        or waste.bytes_saved_estimate
        or waste.deduped_requests
        or (waste.conditional_hits)
    ):
        out.append(
            "<h3>Waste cutter</h3><p>"
            f"{_n(waste.blocked_requests)} requests blocked (about "
            f"{_bytes(waste.bytes_saved_estimate)} not downloaded, an estimate), "
            f"{_n(waste.deduped_requests)} repeated URLs skipped, "
            f"{_n(waste.conditional_hits)} pages unchanged since the last run.</p>"
        )
    sources = c.by_bytes_source
    estimated = sum(n for k, n in sources.items() if k != "measured")
    if not sources:
        note = "How the byte counts were measured is not known for this run."
    elif not estimated:
        note = "All byte counts were measured on the wire."
    else:
        parts = ", ".join(f"{_e(k)} {_n(n)}" for k, n in sorted(sources.items()) if k != "measured")
        note = (
            f"{_n(estimated)} of {_n(sum(sources.values()))} responses have estimated byte counts "
            f"({parts}); totals are estimates."
        )
    out.append(f'<p class="small">{note}</p></section>')
    return "".join(out)


def _history(run: Run, history: Sequence[Run]) -> str:
    earlier = [r for r in history if r.run_id != run.run_id][-(HISTORY_RUNS - 1) :]
    runs = [*earlier, run]
    mark = len(runs) - 1
    series: list[tuple[str, list[float | None], str]] = [
        ("Records", [float(r.counts.records) for r in runs], _n(run.counts.records)),
        (
            "Pages without data (%)",
            [_blocked_share(r) for r in runs],
            "\u2013" if _blocked_share(run) is None else f"{_blocked_share(run):.1f}%",
        ),
        (
            "Cost per usable record",
            [r.cost.per_usable_record for r in runs],
            _money(run.cost.per_usable_record, run.cost.currency),
        ),
    ]
    rows = [[_e(name), sparkline(vals, mark=mark, label=name), now] for name, vals, now in series]
    statuses = " ".join(
        f'<span class="sev {r.verdict.status.value}">{r.verdict.status.value[0].upper()}</span>'
        for r in runs
    )
    return (
        f'<section id="history"><h2>History</h2><p>The last {len(runs)} runs of '
        f"<strong>{_e(run.name)}</strong>, oldest first; this run is the red dot.</p>"
        + _table(["", "trend", "this run"], rows, num=(2,))
        + f'<p class="small">Verdicts: {statuses}</p></section>'
    )


def _health(run: Run) -> str:
    c = run.counts

    def ms(x: float | None) -> str:
        return "\u2013" if x is None else f"{x:.3f} ms"

    errors = (
        "No monitoring errors."
        if not c.monitor_errors
        else f"{_n(c.monitor_errors)} monitoring errors; the scraper kept running (they are "
        "counted, never raised)."
    )
    return (
        '<section id="health"><h2>Monitor health</h2>'
        f"<p>{errors}</p>"
        + _table(
            ["measure", "value"],
            [
                ["Monitoring time per call, median", ms(run.overhead_ms_p50)],
                ["Monitoring time per call, 95th percentile", ms(run.overhead_ms_p95)],
                ["Responses stored in history", _n(c.events_stored)],
            ],
            num=(1,),
        )
        + "</section>"
    )


def _footer(run: Run, files: Mapping[str, str] | None) -> str:
    lines = [f"Reproduce this report: <code>scrapewatch report {_e(run.run_id)}</code>"]
    for name, path in (files or {}).items():
        lines.append(f"{_e(name)}: <code>{_e(path)}</code>")
    lines.append(
        f'Made by <a href="{PROJECT_URL}">scrapewatch</a> {_e(run.scrapewatch_version)}; '
        "this file needs no network and holds no scripts."
    )
    return "<footer>" + "<br>".join(lines) + "</footer>"


def render_html(
    run: Run,
    *,
    history: Sequence[Run] = (),
    bad_events: Sequence[ResponseEvent] = (),
    gate: str | None = None,
    files: Mapping[str, str] | None = None,
) -> str:
    """Render the report. Sections: docs/architecture.md "HTML report".

    ``history`` are earlier runs of the same name (oldest first; the run itself is added),
    ``bad_events`` the responses without data to list, ``gate`` one sentence on what happened
    to the output, ``files`` paths to show in the footer.
    """
    bad = [ev for ev in bad_events if ev.guard.label != GuardLabel.OK]
    body = "\n".join(
        [
            _header(run),
            _meaning(run, gate),
            _responses(run, bad),
            _data(run),
            _cleaning(run),
            _cost(run),
            _history(run, history),
            _health(run),
            _footer(run, files),
        ]
    )
    template = Template(
        resources.files("scrapewatch.report").joinpath("templates/base.html").read_text("utf-8")
    )
    return template.substitute(
        title=_e(f"{run.name} · {run.verdict.status.value.upper()} · scrapewatch"),
        version=_e(run.scrapewatch_version),
        body=body,
    )


def report_paths(report_dir: Path, run: Run) -> tuple[Path, Path]:
    """``<report_dir>/<name>/<started_at>_<run_id[:8]>.html`` and the ``.json`` beside it."""
    stamp = run.started_at.strftime("%Y-%m-%dT%H-%M-%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in run.name) or "run"
    base = Path(report_dir) / safe / f"{stamp}_{run.run_id[:8]}"
    return base.with_suffix(".html"), base.with_suffix(".json")


def write_html(path: Path, html: str) -> Path:
    """Write atomically (temp file + replace)."""
    return write_text_atomic(Path(path), html)


__all__ = ["render_html", "report_paths", "write_html"]
