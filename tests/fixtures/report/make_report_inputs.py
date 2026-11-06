"""Build the report golden inputs (run_warn.json, history.json, bad_events.json).

Hand-built, synthetic numbers for books.toscrape.com; no page content. Run from the repository
root: ``uv run python tests/fixtures/report/make_report_inputs.py``. The golden HTML is written by
tests/report/test_html.py (see its docstring), with a reason in the commit message.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scrapewatch.models import (
    CostBreakdown,
    Finding,
    FindingSample,
    FindingScope,
    FixSample,
    GuardLabel,
    GuardResult,
    RecordSamples,
    ResponseEvent,
    Run,
    RunCounts,
    RunStatus,
    Severity,
    Verdict,
    VerdictStatus,
    WasteBreakdown,
    per_1000,
)

HERE = Path(__file__).parent
T0 = datetime(2026, 11, 5, 14, 12, 0, tzinfo=timezone.utc)
RUN_ID = "9a1b2c3d4e5f60718293a4b5c6d7e8f9"


def history() -> list[Run]:
    runs = []
    for i in range(8):
        records = [1000, 998, 1000, 1001, 999, 1000, 997, 1000][i]
        bad = [3, 2, 4, 3, 2, 3, 2, 3][i]
        started = T0 - timedelta(days=8 - i)
        runs.append(
            Run(
                run_id=f"{i:02d}" + "0" * 30,
                name="books",
                started_at=started,
                finished_at=started + timedelta(minutes=2, seconds=31),
                status=RunStatus.FINISHED,
                integration="httpx",
                scrapewatch_version="0.1.0",
                counts=RunCounts(
                    responses=1052,
                    document_responses=1052,
                    bad_documents=bad,
                    records=records,
                    usable_records=records - 2,
                ),
                cost=CostBreakdown(
                    total=0.0111,
                    usable_records=records - 2,
                    per_usable_record=0.0111 / (records - 2),
                    per_1000_usable_records=per_1000(0.0111 / (records - 2)),
                ),
                verdict=Verdict(status=VerdictStatus.PASS if i != 5 else VerdictStatus.WARN),
            )
        )
    return runs


def run() -> Run:
    rid = RUN_ID
    finding = {"run_id": rid}
    return Run(
        run_id=rid,
        name="books",
        started_at=T0,
        finished_at=T0 + timedelta(minutes=2, seconds=31),
        status=RunStatus.FINISHED,
        integration="httpx",
        scrapewatch_version="0.1.0",
        rules_version="2026.11.0",
        config_hash="7c1e3a",
        counts=RunCounts(
            responses=1052,
            document_responses=1050,
            bad_documents=31,
            by_label={"ok": 1019, "challenge": 24, "empty": 6, "error": 1, "rate_limit": 2},
            by_status={"200": 1025, "403": 24, "429": 2, "null": 1},
            retries=3,
            records=980,
            usable_records=962,
            duplicates=2,
            schema_invalid=0,
            findings_by_severity={"info": 4, "warn": 16, "hold": 0},
            fixes_by_normalizer={"price": 980, "whitespace": 412, "url": 980},
            flags=3,
            monitor_errors=0,
            wire_bytes=13891022,
            by_domain={"books.toscrape.com": 1050, "cdn.example.org": 2},
            by_bytes_source={"measured": 1051, "unknown": 1},
            events_stored=1052,
        ),
        cost=CostBreakdown(
            currency="USD",
            total=0.0111,
            by_domain={"books.toscrape.com": 0.011, "cdn.example.org": 0.0001},
            by_proxy={"datacenter": 0.0111},
            by_resource_type={"document": 0.0111},
            usable_records=962,
            per_usable_record=0.0111 / 962,
            per_record=0.0111 / 980,
            per_1000_usable_records=per_1000(0.0111 / 962),
            per_1000_records=per_1000(0.0111 / 980),
        ),
        waste=WasteBreakdown(
            bytes_by_resource_type={"document": 13891022},
            retry_requests=3,
            retry_bytes=38000,
            retry_cost=0.00003,
            failed_requests=33,
            failed_bytes=242100,
            failed_cost=0.00019,
            total_waste_cost=0.00022,
        ),
        verdict=Verdict(
            status=VerdictStatus.WARN,
            reasons=[
                "2.9% of pages (31 of 1050) were blocked, empty or failed, above 2%.",
                "2 records (0.2%) were duplicates.",
            ],
            counts={"hold": 0, "warn": 2, "info": 1},
            thresholds={"blocked_share_warn_pct": 2.0, "blocked_share_hold_pct": 20.0},
            baseline_runs=["07" + "0" * 30, "06" + "0" * 30, "04" + "0" * 30],
        ),
        findings=[
            Finding(
                **finding,
                scope=FindingScope.BATCH,
                check="batch.duplicates",
                severity=Severity.WARN,
                message="2 records (0.2%) were duplicates.",
                observed=0.2,
                expected={"warn_above_pct": 1.0},
            ),
            Finding(
                **finding,
                scope=FindingScope.BATCH,
                check="batch.row_count",
                severity=Severity.INFO,
                message="980 records vs baseline median 1000.",
                observed=980,
                expected={"baseline": 1000},
            ),
            Finding(
                **finding,
                scope=FindingScope.BATCH,
                check="batch.coverage",
                severity=Severity.WARN,
                field="rating",
                message="rating present in 88.0% of records, 9.5 points below the baseline.",
                observed=88.0,
                expected={"baseline": 97.5},
            ),
            Finding(
                **finding,
                scope=FindingScope.RUN,
                check="run.blocked_share",
                severity=Severity.WARN,
                message="Blocked share 2.9% is above 2%.",
                observed=2.95,
                expected={"warn_pct": 2.0},
            ),
        ],
        samples=RecordSamples(
            findings=[
                FindingSample(
                    check="field.range",
                    field="price",
                    severity=Severity.WARN,
                    count=12,
                    examples=[1250.0, 1999.0, 1430.5],
                ),
                FindingSample(
                    check="clean.price.ambiguous_separator",
                    field="price",
                    severity=Severity.WARN,
                    count=3,
                    examples=["1,299", "2,500", "1,050"],
                ),
                FindingSample(
                    check="dup.repeat", field=None, severity=Severity.INFO, count=2, examples=[]
                ),
                FindingSample(
                    check="clean.price.extra_conflict",
                    field="currency",
                    severity=Severity.INFO,
                    count=1,
                    examples=["GBP"],
                ),
            ],
            fixes=[
                FixSample(
                    normalizer="price",
                    field="price",
                    count=980,
                    examples=[
                        {"before": "£51.77", "after": 51.77},
                        {"before": "£53.74", "after": 53.74},
                        {"before": "£50.10", "after": 50.1},
                    ],
                ),
                FixSample(
                    normalizer="url",
                    field="url",
                    count=980,
                    examples=[
                        {
                            "before": "a-light-in-the-attic_1000/index.html",
                            "after": "https://books.toscrape.com/catalogue/"
                            "a-light-in-the-attic_1000/index.html",
                        }
                    ],
                ),
                FixSample(
                    normalizer="whitespace",
                    field="title",
                    count=412,
                    examples=[
                        {"before": "  A Light in the Attic ", "after": "A Light in the Attic"}
                    ],
                ),
            ],
        ),
        overhead_ms_p50=0.21,
        overhead_ms_p95=0.48,
    )


def bad_events() -> list[ResponseEvent]:
    out = []
    for i, (label, vendor, status, rule, signals) in enumerate(
        [
            (
                "challenge",
                "cloudflare",
                403,
                "cloudflare.challenge",
                ["status in [403]", "header cf-mitigated ~ ^challenge$"],
            ),
            (
                "empty",
                None,
                200,
                "generic.empty.small_vs_baseline",
                ["size below 30% of baseline", "text density below 0.05"],
            ),
            ("rate_limit", None, 429, "generic.rate_limit.429", ["status in [429]"]),
        ]
    ):
        out.append(
            ResponseEvent(
                event_id=f"e{i}" + "0" * 30,
                run_id=RUN_ID,
                ts=T0 + timedelta(seconds=10 * i),
                url=f"https://books.toscrape.com/catalogue/page-{i + 2}.html?session=[redacted]",
                domain="books.toscrape.com",
                status=status,
                guard=GuardResult(
                    label=GuardLabel(label),
                    vendor=vendor,
                    rule_id=rule,
                    confidence=0.9,
                    signals=signals,
                ),
            )
        )
    return out


if __name__ == "__main__":
    (HERE / "run_warn.json").write_text(run().model_dump_json(indent=2) + "\n", "utf-8")
    hist = [json.loads(r.model_dump_json()) for r in history()]
    (HERE / "history.json").write_text(json.dumps(hist, indent=2) + "\n", "utf-8")
    events = [json.loads(e.model_dump_json()) for e in bad_events()]
    (HERE / "bad_events.json").write_text(json.dumps(events, indent=2) + "\n", "utf-8")
