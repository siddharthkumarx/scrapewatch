"""Static HTML report: one file, inline CSS and SVG, no external scripts or fonts."""

from __future__ import annotations

from pathlib import Path

from scrapewatch.models import Finding, Record, Run


def render_html(
    run: Run,
    *,
    findings: list[Finding],
    records_sample: list[Record],
    history: list[Run],
) -> str:
    """Render the report. Sections: docs/architecture.md "HTML report"."""
    raise NotImplementedError("build-progress item 13")


def write_html(path: Path, html: str) -> Path:
    """Write atomically (temp file + replace)."""
    raise NotImplementedError("build-progress item 13")
