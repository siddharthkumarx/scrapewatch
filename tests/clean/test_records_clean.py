"""End-to-end cleaning: messy records through check_record and the run samples (item 11)."""

from __future__ import annotations

import json
from pathlib import Path

from scrapewatch.checks.engine import RecordChecker, check_records
from scrapewatch.checks.rules import DataRules, load_data_rules
from scrapewatch.config import Settings
from scrapewatch.models import Record
from scrapewatch.runtime import watch

CLEAN = Path(__file__).resolve().parents[1] / "fixtures" / "clean"


def _stable(record: Record) -> dict[str, object]:
    data = record.model_dump(mode="json")
    for key in ("record_id", "run_id", "schema_version", "source_event_id", "unchanged"):
        data.pop(key, None)
    for f in data["findings"]:
        for key in ("finding_id", "run_id", "ref", "scope"):
            f.pop(key, None)
    return data


def _items() -> list[dict[str, object]]:
    lines = (CLEAN / "records_messy.jsonl").read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_records_messy_to_clean_golden() -> None:
    checker = RecordChecker(load_data_rules(CLEAN / "rules_clean.yml"))
    expected = (CLEAN / "records_clean.jsonl").read_text("utf-8").splitlines()
    items = _items()
    assert len(items) == len(expected) == 8
    for i, (item, exp) in enumerate(zip(items, expected, strict=True), 1):
        assert _stable(checker.check(item, run_id="r")) == json.loads(exp), f"record {i}"


def test_second_pass_over_clean_data_changes_nothing() -> None:
    checker = RecordChecker(load_data_rules(CLEAN / "rules_clean.yml"))
    again = RecordChecker(load_data_rules(CLEAN / "rules_clean.yml"))
    for item in _items():
        first = checker.check(item, run_id="r")
        second = again.check(dict(first.data), run_id="r")
        assert second.fixes == [], second.fixes


def test_run_counts_and_samples() -> None:
    rules = load_data_rules(CLEAN / "rules_clean.yml")
    run = check_records(_items(), rules).run
    c = run.counts
    assert c.fixes_by_normalizer == {
        "strip_tags": 1,
        "html_entities": 1,
        "mojibake": 1,
        "price": 8,
        "number": 3,
        "url": 5,
        "date": 6,
    }
    assert c.flags == 8  # warn-level clean.* findings (info notes and conflicts excluded)
    by_kind = {(s.check, s.field): s for s in run.samples.findings}
    assert by_kind[("field.type", "price")].count == 2
    assert by_kind[("field.type", "price")].examples == ["1,299", "Price on request"]
    assert by_kind[("clean.date.relative", "updated_at")].severity.value == "info"
    fixes = {(s.normalizer, s.field): s for s in run.samples.fixes}
    assert fixes[("date", "published")].count == 4
    assert len(fixes[("date", "published")].examples) == 3  # three examples by default
    assert fixes[("price", "price")].examples[0] == {"before": "£51.77", "after": 51.77}
    # warn and hold kinds come before info, then by count
    ranks = [s.severity.value for s in run.samples.findings]
    assert ranks.index("info") > max(i for i, r in enumerate(ranks) if r == "warn")


def test_examples_zero_keeps_counts_only() -> None:
    rules = load_data_rules(CLEAN / "rules_clean.yml").model_copy(update={"examples": 0})
    run = check_records(_items(), rules).run
    assert run.samples.findings
    assert all(s.examples == [] for s in run.samples.findings)
    assert all(s.examples == [] for s in run.samples.fixes)


def test_examples_are_redacted_and_cut() -> None:
    rules = DataRules.model_validate(
        {"fields": {"link": {"allowed": ["https://a.example/"]}, "note": {"regex": "^y"}}}
    )
    items = [{"link": "https://a.example/x?token=abc123", "note": "x" * 500}]
    run = check_records(items, rules).run
    by_field = {s.field: s for s in run.samples.findings}
    assert by_field["link"].examples == ["https://a.example/x?token=[redacted]"]
    note = by_field["note"].examples[0]
    assert len(note) == 200
    assert note.endswith("…")


def test_watch_fills_samples(tmp_path: Path) -> None:
    settings = Settings(history={"path": tmp_path})
    with watch("clean", settings=settings, rules=CLEAN / "rules_clean.yml") as w:
        for item in _items():
            w.record_record(item)
    assert w.run.samples.fixes
    assert w.run.counts.fixes_by_normalizer["url"] == 5
