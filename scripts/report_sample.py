"""Render the synthetic sample report used for docs/images/report-sample.png.

The inputs are the made-up golden fixtures in tests/fixtures/report/ (no real run), so the
page carries a "Synthetic example" banner. The README must use a report from a real
committed run instead (docs/readme-plan.md).

    uv run python scripts/report_sample.py /tmp/report-sample.html

Then take a full-page screenshot at 1000 px wide, device scale 2, light scheme, with
Playwright, and save it as docs/images/report-sample.png (under 1 MB).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scrapewatch.models import ResponseEvent, Run
from scrapewatch.report import render_html

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "report"
NOTICE = "Synthetic example: made-up numbers from test fixtures, not from a real run."


def main(out: str) -> None:
    run = Run.model_validate_json((FIXTURES / "run_warn.json").read_text("utf-8"))
    history = [Run.model_validate(x) for x in json.loads((FIXTURES / "history.json").read_text())]
    events = [
        ResponseEvent.model_validate(x)
        for x in json.loads((FIXTURES / "bad_events.json").read_text("utf-8"))
    ]
    html = render_html(
        run,
        history=history,
        bad_events=events,
        gate="Promoted to data/books.jsonl.",
        notice=NOTICE,
    )
    Path(out).write_text(html, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main(sys.argv[1])
