import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from scrapewatch.checks.engine import RecordChecker, check_record
from scrapewatch.checks.rules import DataRules, load_data_rules
from scrapewatch.clean.pipeline import NORMALIZERS, CleanOutcome, apply_normalizers
from scrapewatch.errors import RulesError
from scrapewatch.models import Record, Severity

CHECKS = Path(__file__).parents[1] / "fixtures" / "checks"


def stable(record: Record) -> dict[str, Any]:
    """A record as JSON without record_id, run_id and finding ids."""
    data = record.model_dump(mode="json")
    for key in ("record_id", "run_id", "schema_version", "source_event_id"):
        data.pop(key, None)
    for f in data["findings"]:
        for key in ("finding_id", "run_id", "ref", "scope"):
            f.pop(key, None)
    return data


def test_rules_golden_loads_to_expected_json() -> None:
    rules = load_data_rules(CHECKS / "rules_books.yml")
    got = rules.model_dump(mode="json", by_alias=True)
    assert got == json.loads((CHECKS / "rules_books.expected.json").read_text("utf-8"))
    assert isinstance(rules.json_schema, dict)  # the relative schema path was read


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("fields: {price: {tpye: number}}", "Extra inputs"),
        ("unknown_top: 1", "Extra inputs"),
        ("batch: {row_count: {warn: 1}}", "Extra inputs"),
        ("fields: {price: {type: money}}", "type"),
        ("fields: {price: {clean: [prise]}}", "unknown normalizer"),
        ("fields: {sku: {regex: '(['}}", "bad regex"),
        ("cross: [{id: a, expr: 'price.real > 0'}]", "Attribute"),
        ("cross: [{id: a, expr: 'a < b', when: 'x[0]'}]", "Subscript"),
        ("cross: [{id: a, expr: 'a < b'}, {id: a, expr: 'a > b'}]", "duplicate cross rule"),
        ("schema: {type: 12}", "invalid JSON Schema"),
        ("schema: missing.json", "cannot read JSON Schema"),
        ("schema_version: 2", "schema_version"),
        ("- a list", "mapping"),
        ("fields: [", "not valid YAML"),
    ],
)
def test_invalid_rules_rejected(tmp_path: Path, text: str, problem: str) -> None:
    path = tmp_path / "rules.yml"
    path.write_text(text + "\n", encoding="utf-8")
    with pytest.raises(RulesError, match=problem):
        load_data_rules(path)


def test_schema_file_variants(tmp_path: Path) -> None:
    (tmp_path / "s.yml").write_text("type: object\n", encoding="utf-8")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    rules = tmp_path / "rules.yml"
    rules.write_text("schema: s.yml\n", encoding="utf-8")
    assert load_data_rules(rules).json_schema == {"type": "object"}
    for name, problem in [("bad.json", "cannot be parsed"), ("list.json", "must be an object")]:
        rules.write_text(f"schema: {name}\n", encoding="utf-8")
        with pytest.raises(RulesError, match=problem):
            load_data_rules(rules)
    empty = tmp_path / "empty.yml"
    empty.write_text("", encoding="utf-8")
    assert load_data_rules(empty) == DataRules()
    with pytest.raises(RulesError, match="cannot read"):
        load_data_rules(tmp_path / "nope.yml")


def test_record_goldens() -> None:
    checker = RecordChecker(load_data_rules(CHECKS / "rules_records.yml"))
    inputs = (CHECKS / "records_in.jsonl").read_text("utf-8").splitlines()
    expected = (CHECKS / "records_out.jsonl").read_text("utf-8").splitlines()
    assert len(inputs) == len(expected) == 15
    for i, (line, exp) in enumerate(zip(inputs, expected, strict=True), 1):
        got = stable(checker.check(json.loads(line), run_id="r"))
        assert got == json.loads(exp), f"record {i}"


def test_check_record_without_checker_has_no_cross_record_state() -> None:
    rules = load_data_rules(CHECKS / "rules_records.yml")
    data = {"title": "Same", "price": 1, "url": "https://a.example/x"}
    first = check_record(data, rules, run_id="r")
    second = check_record(data, rules, run_id="r")
    assert first.usable
    assert second.usable  # duplicates need a shared RecordChecker
    assert first.findings[0].ref == first.record_id if first.findings else True


def test_from_model() -> None:
    class Book(BaseModel):
        title: str
        price: float

    rules = DataRules.from_model(Book, key=["title"])
    assert isinstance(rules.json_schema, dict)
    rec = check_record({"title": "T", "price": "x"}, rules, run_id="r")
    assert not rec.usable
    assert [f.check for f in rec.findings] == ["schema.invalid"]
    assert rec.findings[0].field == "/price"


def test_cleaning_fixes_extra_and_flags() -> None:
    def fake_price(value: object, options: object) -> CleanOutcome:
        if value == "bad":
            return CleanOutcome(value=value, flag="non_numeric")
        if value == "boom":
            raise ValueError("broken")
        return CleanOutcome(value=1.5, changed=True, extra={"currency": "GBP"})

    NORMALIZERS["test_fake"] = fake_price
    try:
        rules = DataRules.model_validate(
            {"fields": {"price": {"clean": ["passthrough"], "severity": "hold"}}}
        )
        rules.fields["price"].clean = ["test_fake", "passthrough"]
        checker = RecordChecker(rules)
        ok = checker.check({"price": "£1.50"}, run_id="r")
        assert ok.data == {"price": 1.5, "currency": "GBP"}
        assert [(f.field, f.normalizer, f.before, f.after) for f in ok.fixes] == [
            ("price", "test_fake", "£1.50", 1.5),
            ("currency", "test_fake", None, "GBP"),
        ]
        kept = checker.check({"price": "£1.50", "currency": "EUR"}, run_id="r")
        assert kept.data["currency"] == "EUR"  # extra fills only empty fields
        flagged = checker.check({"price": "bad"}, run_id="r")
        assert [f.check for f in flagged.findings] == ["clean.test_fake.non_numeric"]
        assert flagged.findings[0].severity == Severity.HOLD
        assert not flagged.usable
        crashed = checker.check({"price": "boom"}, run_id="r")
        assert crashed.findings[0].check == "clean.test_fake.error"
        assert "broken" in crashed.findings[0].message
        missing = checker.check({}, run_id="r")
        assert "price" not in missing.data  # nothing to clean, nothing invented
    finally:
        del NORMALIZERS["test_fake"]


def test_apply_normalizers_tuple_and_stubs() -> None:
    value, fixes, findings, extra = apply_normalizers(
        "f", " x ", ["passthrough", "not_registered"], {}, run_id="r", record_id="c"
    )
    assert (value, fixes, findings, extra) == (" x ", [], [], {})
