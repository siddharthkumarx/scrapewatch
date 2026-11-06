"""HTML report and JSON summary (build item 13).

To regenerate the golden after a deliberate change (say why in the commit message):
``SCRAPEWATCH_REGEN_GOLDEN=1 uv run pytest tests/report`` (the variable is read before the
autouse fixture clears SCRAPEWATCH_* variables).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from scrapewatch.models import ResponseEvent, Run
from scrapewatch.report import read_summary, render_html, report_paths, write_html, write_summary

REPORT = Path(__file__).resolve().parents[1] / "fixtures" / "report"
REGEN = os.environ.get("SCRAPEWATCH_REGEN_GOLDEN") == "1"
FILES = {
    "report": ".scrapewatch/reports/books/2026-11-05T14-12-00_9a1b2c3d.html",
    "summary": ".scrapewatch/reports/books/2026-11-05T14-12-00_9a1b2c3d.json",
}
SECTIONS = [
    "What this means",
    "Responses",
    "Data checks",
    "Cleaning",
    "Cost",
    "History",
    "Monitor health",
]


def _inputs() -> tuple[Run, list[Run], list[ResponseEvent]]:
    run = Run.model_validate_json((REPORT / "run_warn.json").read_text("utf-8"))
    history = [Run.model_validate(x) for x in json.loads((REPORT / "history.json").read_text())]
    events = json.loads((REPORT / "bad_events.json").read_text("utf-8"))
    return run, history, [ResponseEvent.model_validate(x) for x in events]


def _render() -> str:
    run, history, events = _inputs()
    return render_html(
        run, history=history, bad_events=events, gate="Promoted to data/books.jsonl.", files=FILES
    )


def _normalise(html: str) -> str:
    return re.sub(r"\s+", " ", html).strip()


def test_golden_report() -> None:
    html = _render()
    golden = REPORT / "report_warn.html"
    if REGEN:
        golden.write_text(html, encoding="utf-8")
    assert _normalise(html) == _normalise(golden.read_text("utf-8"))


def test_report_is_static_and_self_contained() -> None:
    html = _render()
    assert "<script" not in html.lower()
    links = re.findall(r'(?:src|href)\s*=\s*"([^"]*)"', html)
    external = [x for x in links if x.startswith(("http://", "https://"))]
    assert external == ["https://github.com/siddharthkumarx/scrapewatch"]
    assert "@import" not in html
    assert "url(" not in html


def test_every_section_heading_present() -> None:
    html = _render()
    for heading in SECTIONS:
        assert f"<h2>{heading}</h2>" in html
    assert '<span class="badge warn">WARN</span>' in html


def test_values_are_escaped() -> None:
    run, _, _ = _inputs()
    run.name = "<b>books</b>"
    run.verdict.reasons = ['<img src="x"> & more']
    html = render_html(run)
    assert "<b>books</b>" not in html
    assert "&lt;b&gt;books&lt;/b&gt;" in html
    assert '<img src="x">' not in html


def test_empty_history_and_zero_records() -> None:
    html = render_html(Run(name="empty"))
    assert "No responses were recorded" in html
    assert "No records were recorded" in html
    assert "No cost was recorded" in html
    assert "The last 1 runs" in html
    for heading in SECTIONS:
        assert f"<h2>{heading}</h2>" in html


def test_summary_round_trip(tmp_path: Path) -> None:
    run, _, _ = _inputs()
    path = write_summary(run, tmp_path / "deep" / "summary.json")
    assert read_summary(path) == run
    assert Run.model_validate(json.loads(path.read_text("utf-8"))) == run
    assert list(path.parent.iterdir()) == [path]  # no temporary file left behind


def test_write_html_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "r.html"
    write_html(target, "old")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_html(target, "new")
    assert target.read_text("utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["r.html"]


def test_report_paths() -> None:
    run, _, _ = _inputs()
    html, summary = report_paths(Path("out"), run)
    assert html == Path("out/books/2026-11-05T14-12-00_9a1b2c3d.html")
    assert summary == html.with_suffix(".json")
    run.name = "a/b c"
    assert report_paths(Path("out"), run)[0].parent == Path("out/a_b_c")


def test_money_and_measurement_notes() -> None:
    run, _, _ = _inputs()
    run.counts.by_bytes_source = {"measured": 10}
    run.cost.currency = "EUR"
    run.cost.total = 12.5
    html = render_html(run)
    assert "All byte counts were measured" in html
    assert "€12.50" in html
    run.counts.by_bytes_source = {}
    assert "is not known for this run" in render_html(run)


def test_helpers_and_optional_sections() -> None:
    from datetime import timedelta

    from scrapewatch.models import Finding, FindingScope, Severity
    from scrapewatch.report._svg import sparkline

    svg = sparkline([1.0, 2.0, None, 3.0, 4.0, None, 5.0], mark=6, label="x")
    assert svg.count("<polyline") == 2
    assert "<title>x</title>" in svg
    assert "no data yet" in sparkline([None, None])
    run, _, _ = _inputs()
    assert run.finished_at is not None
    run.finished_at = run.started_at + timedelta(hours=2, minutes=5)
    run.waste.blocked_requests = 4
    run.waste.bytes_saved_estimate = 180_000
    run.cost.browser_minutes = 3.5
    run.findings.append(
        Finding(
            run_id=run.run_id,
            scope=FindingScope.BATCH,
            check="batch.currency",
            severity=Severity.HOLD,
            message="m",
            observed=["EUR", "x" * 200],
            expected=None,
        )
    )
    html = render_html(run)
    assert "took 2 h 5 min" in html
    assert "<h3>Waste cutter</h3>" in html
    assert "180.0 KB not downloaded" in html
    assert "Browser time: 3.5 minutes" in html
    assert "…" in html  # the long example is cut
