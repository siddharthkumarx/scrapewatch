import json
import math
from pathlib import Path

import pytest

from scrapewatch.checks.batch import BatchStats, batch_findings, bin_shares, psi
from scrapewatch.checks.engine import check_batch, check_records
from scrapewatch.checks.rules import DataRules, VerdictThresholds, load_data_rules
from scrapewatch.checks.verdict import compute_verdict
from scrapewatch.history.baselines import Baseline
from scrapewatch.models import (
    Finding,
    FindingScope,
    Record,
    Run,
    RunStatus,
    Severity,
    VerdictStatus,
)

CHECKS = Path(__file__).parents[1] / "fixtures" / "checks"
BATCHES = sorted(p.stem for p in CHECKS.glob("batch_*.jsonl"))


def _rules() -> DataRules:
    return load_data_rules(CHECKS / "rules_batch.yml")


def _baseline() -> Baseline:
    return Baseline.model_validate_json((CHECKS / "baseline.json").read_text("utf-8"))


def _items(name: str) -> list[dict[str, object]]:
    lines = (CHECKS / f"{name}.jsonl").read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_six_failure_modes() -> None:
    assert BATCHES == [
        "batch_coverage_drop",
        "batch_currency_mismatch",
        "batch_duplicates",
        "batch_psi_shift",
        "batch_row_drop",
        "batch_unit_x100",
    ]


@pytest.mark.parametrize("name", BATCHES)
def test_batch_fixture(name: str) -> None:
    expected = json.loads((CHECKS / f"{name}.expected.json").read_text("utf-8"))
    result = check_records(_items(name), _rules(), baseline=_baseline())
    got = [
        {"check": f.check, "severity": f.severity.value, "field": f.field, "message": f.message}
        for f in result.batch_findings
    ]
    assert got == expected["findings"]
    assert result.run.verdict.status.value == expected["verdict"]
    assert result.run.verdict.baseline_runs == ["base3", "base2", "base1"]
    for f in result.batch_findings:
        if f.severity != Severity.INFO:
            assert f.message in result.run.verdict.reasons


def test_expected_verdicts_per_failure_mode() -> None:
    verdicts = {
        n: json.loads((CHECKS / f"{n}.expected.json").read_text("utf-8"))["verdict"]
        for n in BATCHES
    }
    assert verdicts == {
        "batch_coverage_drop": "warn",
        "batch_currency_mismatch": "hold",
        "batch_duplicates": "warn",
        "batch_psi_shift": "warn",
        "batch_row_drop": "hold",
        "batch_unit_x100": "hold",
    }


def test_reasons_contain_the_numbers() -> None:
    reasons = check_records(_items("batch_row_drop"), _rules(), baseline=_baseline())
    assert "40" in reasons.run.verdict.reasons[0]
    assert "60%" in reasons.run.verdict.reasons[0]
    assert "100" in reasons.run.verdict.reasons[0]


def test_psi_hand_computed() -> None:
    # 4% in each of the five lower bins, 16% in each upper bin, baseline 10% everywhere:
    # 5 * (0.04 - 0.1) * ln(0.4) + 5 * (0.16 - 0.1) * ln(1.6)
    expected = 5 * (-0.06) * math.log(0.4) + 5 * 0.06 * math.log(1.6)
    assert expected == pytest.approx(0.4158883083, abs=1e-9)
    actual = [0.04] * 5 + [0.16] * 5
    assert psi(actual, [0.1] * 10) == pytest.approx(expected, abs=1e-9)
    # floors: an empty bin counts as 0.5%
    floored = (0.005 - 0.1) * math.log(0.005 / 0.1) + (0.2 - 0.1) * math.log(0.2 / 0.1)
    assert psi([0.0, 0.2], [0.1, 0.1]) == pytest.approx(floored, abs=1e-12)
    values = [11.0] * 4 + [29.0] * 6
    assert bin_shares(values, [10.0, 20.0, 30.0]) == [0.4, 0.6]
    assert bin_shares([], [10.0, 20.0, 30.0]) == [0.0, 0.0]
    assert bin_shares([5.0, 35.0], [10.0, 20.0, 30.0]) == [0.5, 0.5]


def test_no_baseline_gives_info_only_plus_absolute_checks() -> None:
    rules = _rules()
    rules.batch.row_count.min_rows = 50
    rules.fields["price"].coverage_hold_below_pct = 99
    items = _items("batch_row_drop")
    items[0].pop("price")
    result = check_records(items, rules, baseline=Baseline(candidate_runs=2))
    checks = [(f.check, f.severity.value) for f in result.batch_findings]
    assert checks == [
        ("batch.row_count", "hold"),  # below min_rows needs no history
        ("batch.coverage", "hold"),  # below coverage_hold_below_pct needs no history
        ("batch.baseline", "info"),
    ]
    assert "2 of 3 passing runs" in result.batch_findings[-1].message


def test_check_batch_on_records() -> None:
    rules = _rules()
    records = check_records(_items("batch_duplicates"), rules).records
    findings = check_batch(records, rules, _baseline(), run_id="r")
    assert [f.check for f in findings] == ["batch.duplicates"]


def test_changed_share_and_categorical_and_hold_thresholds() -> None:
    rules = _rules()
    rules.batch.changed_share.hold_above_pct = 80
    rules.batch.duplicates_hold_pct = 1.5
    rules.batch.distribution.psi_hold = 1.0
    first = check_records(_items("batch_coverage_drop"), rules)
    base = _baseline()
    base.previous_value_hashes = {r.dedupe_key: "different" for r in first.records if r.dedupe_key}
    items = _items("batch_duplicates")
    for item in items[:10]:
        item["category"] = "manga"
    result = check_records(items, rules, baseline=base)
    got = {(f.check, f.severity.value) for f in result.batch_findings}
    assert ("batch.changed_share", "hold") in got
    assert ("batch.duplicates", "hold") in got
    assert ("batch.distribution.categorical", "warn") in got
    x100 = check_records(_items("batch_unit_x100"), rules, baseline=base)
    assert ("batch.distribution.numeric", "hold") in {
        (f.check, f.severity.value) for f in x100.batch_findings
    }
    rules.batch.changed_share = rules.batch.changed_share.model_copy(
        update={"hold_above_pct": None, "warn_above_pct": 100.0, "warn_below_pct": 5.0}
    )
    base.previous_value_hashes = {
        r.dedupe_key: BatchStats.from_records(first.records, rules).value_hashes[r.dedupe_key]
        for r in first.records
        if r.dedupe_key and r.usable
    }
    same = check_records(_items("batch_coverage_drop"), rules, baseline=base)
    assert ("batch.changed_share", "warn") in {
        (f.check, f.severity.value) for f in same.batch_findings
    }


def test_unit_error_divided_and_row_increase() -> None:
    rules = _rules()
    items = [{**i, "price": float(i["price"]) / 1000} for i in _items("batch_coverage_drop")]  # type: ignore[arg-type]
    result = check_records(items * 2, rules, baseline=_baseline())
    msgs = {f.check: f.message for f in result.batch_findings}
    assert "about 1/1000 of" in msgs["batch.unit_error"]
    assert "above baseline" in msgs["batch.row_count"]


def _run(**counts: int) -> Run:
    run = Run(status=RunStatus.FINISHED)
    for k, v in counts.items():
        setattr(run.counts, k, v)
    return run


def _finding(sev: Severity, msg: str = "m") -> Finding:
    return Finding(run_id="r", scope=FindingScope.BATCH, check="batch.x", severity=sev, message=msg)


T = VerdictThresholds()


@pytest.mark.parametrize(
    ("run", "findings", "kwargs", "status"),
    [
        (_run(records=10, usable_records=10), [], {}, "pass"),
        (_run(records=10, usable_records=10), [_finding(Severity.INFO)], {}, "pass"),
        (_run(records=10, usable_records=10), [_finding(Severity.WARN)], {}, "warn"),
        (_run(records=10, usable_records=10), [_finding(Severity.HOLD)], {}, "hold"),
        (
            _run(records=10, usable_records=10),
            [_finding(Severity.WARN), _finding(Severity.HOLD)],
            {},
            "hold",
        ),
        (_run(records=10, usable_records=10), [], {"checks_failed": True}, "hold"),
        (_run(records=0, usable_records=0), [], {}, "hold"),
        (_run(records=0, usable_records=0), [], {"expect_records": False}, "pass"),
        (
            _run(records=10, usable_records=10, document_responses=100, bad_documents=3),
            [],
            {},
            "warn",
        ),
        (
            _run(records=10, usable_records=10, document_responses=100, bad_documents=21),
            [],
            {},
            "hold",
        ),
        (
            _run(records=10, usable_records=10, document_responses=100, bad_documents=2),
            [],
            {},
            "pass",
        ),
        (_run(records=100, usable_records=98, schema_invalid=2), [], {}, "warn"),
        (_run(records=100, usable_records=94, schema_invalid=6), [], {}, "hold"),
        (_run(records=100, usable_records=100, flags=6), [], {}, "warn"),
        (_run(records=10, usable_records=10, monitor_errors=1), [], {}, "warn"),
    ],
)
def test_verdict_truth_table(
    run: Run, findings: list[Finding], kwargs: dict[str, bool], status: str
) -> None:
    assert compute_verdict(run, findings, T, **kwargs).status.value == status


def test_verdict_record_findings_and_reasons() -> None:
    run = _run(records=10, usable_records=9)
    run.counts.findings_by_severity = {"hold": 1, "warn": 2, "info": 5}
    v = compute_verdict(run, [_finding(Severity.WARN, "batch warn")], T)
    assert v.status == VerdictStatus.HOLD
    assert v.reasons == [
        "1 record finding(s) with severity hold.",
        "batch warn",
        "2 record finding(s) with severity warn.",
    ]
    assert v.counts == {"info": 5, "warn": 3, "hold": 1}
    assert v.thresholds["blocked_share_hold_pct"] == 20.0
    blocked = compute_verdict(
        _run(records=1, usable_records=1, document_responses=1000, bad_documents=234), [], T
    )
    assert blocked.reasons == ["Blocked share 23.4% is above 20% (234 of 1000 pages)."]
    crashed = _run(records=1, usable_records=1)
    crashed.status = RunStatus.CRASHED
    assert compute_verdict(crashed, [], T).reasons == [
        "The scraper crashed before the run finished."
    ]


def test_checks_failed_reason() -> None:
    v = compute_verdict(_run(records=1, usable_records=1), [], T, checks_failed=True)
    assert v.status == VerdictStatus.HOLD
    assert "could not run" in v.reasons[0]


def test_check_records_fails_closed() -> None:
    rules = _rules()

    def items() -> object:
        yield {"title": "ok", "price": 1, "url": "https://a.example/1"}
        raise OSError("file vanished")

    result = check_records(items(), rules)  # type: ignore[arg-type]
    assert result.run.verdict.status == VerdictStatus.HOLD
    assert result.batch_findings[-1].check == "run.checks_failed"
    assert len(result.records) == 1


def test_batch_stats_counts() -> None:
    rules = _rules()
    rec = Record(run_id="r", data={"currency": "EUR"})
    rec.findings.append(
        Finding(run_id="r", scope=FindingScope.RECORD, check="schema.invalid", message="m")
    )
    stats = BatchStats.from_records([rec], rules)
    assert (stats.records, stats.schema_invalid, stats.currency_mismatches) == (1, 1, 1)
    out = batch_findings(stats, rules, Baseline(), run_id="r")
    assert [f.check for f in out] == ["batch.currency", "batch.baseline"]
