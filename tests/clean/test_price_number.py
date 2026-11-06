"""Goldens and properties for the price and number normalizers (build item 10)."""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

import pytest
import yaml

from scrapewatch.checks.engine import RecordChecker
from scrapewatch.checks.rules import DataRules
from scrapewatch.clean import NORMALIZERS, CleanOutcome
from scrapewatch.clean.number import normalize_number
from scrapewatch.clean.price import normalize_price

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "clean"


def _cases(name: str) -> list[dict[str, Any]]:
    data = yaml.safe_load((FIXTURES / f"{name}.yml").read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = data["cases"]
    return cases


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(expected, float) and math.isnan(expected):
        return isinstance(actual, float) and math.isnan(actual)
    return type(actual) is type(expected) and actual == expected


def _check(outcome: CleanOutcome, case: dict[str, Any]) -> None:
    expect = case["expect"]
    value = expect.get("value", case["input"])
    assert _same(outcome.value, value), (outcome.value, value)
    assert outcome.changed is expect.get("changed", False)
    assert outcome.flag == expect.get("flag")
    assert outcome.extra == expect.get("extra", {})
    if outcome.flag:
        assert outcome.flag_message
        assert not outcome.changed


@pytest.mark.parametrize("case", _cases("price"), ids=lambda c: repr(c["input"]))
def test_price_golden(case: dict[str, Any]) -> None:
    _check(normalize_price(case["input"], case["options"]), case)


@pytest.mark.parametrize("case", _cases("number"), ids=lambda c: repr(c["input"]))
def test_number_golden(case: dict[str, Any]) -> None:
    _check(normalize_number(case["input"], case["options"]), case)


def test_golden_sizes() -> None:
    # every table row plus at least 40 more real-world shapes across the two files
    assert len(_cases("price")) + len(_cases("number")) >= 16 + 9 + 40


@pytest.mark.parametrize(("name", "fn"), [("price", normalize_price), ("number", normalize_number)])
def test_golden_idempotent(name: str, fn: Any) -> None:
    for case in _cases(name):
        first = fn(case["input"], case["options"])
        if first.flag:
            again = fn(case["input"], case["options"])
            assert again.flag == first.flag  # flags are stable too
            continue
        second = fn(first.value, case["options"])
        assert not second.changed, case
        assert second.flag is None, case
        assert _same(second.value, first.value)


_SYMBOLS = [("$", "USD", "pre"), ("£", "GBP", "pre"), ("€", "EUR", "post"), ("CHF", "CHF", "pre")]


def _write(amount: int, cents: int, locale: str, rng: random.Random) -> str:
    whole = f"{amount:,}" if rng.random() < 0.7 else str(amount)
    if locale == "eu":
        whole = whole.replace(",", ".")
        return f"{whole},{cents:02d}"
    return f"{whole}.{cents:02d}"


def test_price_property_round_trip_and_idempotence() -> None:
    rng = random.Random(20261106)
    for _ in range(2000):
        amount, cents = rng.randrange(0, 10_000_000), rng.randrange(0, 100)
        locale = rng.choice(["us", "eu"])
        sym, code, where = rng.choice(_SYMBOLS)
        number = _write(amount, cents, locale, rng)
        space = rng.choice(["", " ", "\u00a0"])
        text = f"{sym}{space}{number}" if where == "pre" else f"{number}{space}{sym}"
        for opts in ({"locale": locale}, {}):  # two decimals: one reading even under auto
            out = normalize_price(text, opts)
            assert out.flag is None, (text, out.flag_message)
            assert out.value == pytest.approx(amount + cents / 100, abs=1e-9)
            assert out.extra == {"currency": code}
            again = normalize_price(out.value, opts)
            assert not again.changed
            assert again.value == out.value


def test_number_property_round_trip() -> None:
    rng = random.Random(7)
    for _ in range(2000):
        value = rng.randrange(-(10**9), 10**9)
        text = rng.choice([f"{value:,}", str(value), f"{value:_}".replace("_", " ")])
        out = normalize_number(text, {"locale": "us", "integer": True})
        assert out.flag is None, text
        assert out.value == value
        assert normalize_number(out.value, {}).changed is False


@pytest.mark.parametrize("fn", [normalize_price, normalize_number])
def test_bad_locale_option_raises(fn: Any) -> None:
    with pytest.raises(ValueError, match="locale"):
        fn("1", {"locale": "fr"})


def test_bad_currency_default_raises() -> None:
    with pytest.raises(ValueError, match="ISO 4217"):
        normalize_price("1", {"currency_default": "pounds"})


def test_registered_names() -> None:
    assert NORMALIZERS["price"] is normalize_price
    assert NORMALIZERS["number"] is normalize_number


def _checker(fields: dict[str, Any]) -> RecordChecker:
    return RecordChecker(DataRules.model_validate({"fields": fields}))


def test_record_price_fills_currency_and_counts_fixes() -> None:
    checker = _checker(
        {
            "price": {"type": "number", "clean": ["price"]},
            "currency": {"type": "string"},
            "stock": {"type": "integer", "clean": ["number"]},
        }
    )
    rec = checker.check({"price": "£51.77", "stock": "1 204", "currency": None}, run_id="r")
    assert rec.data == {"price": 51.77, "currency": "GBP", "stock": 1204}
    assert isinstance(rec.data["stock"], int)
    assert [(f.field, f.normalizer, f.before, f.after) for f in rec.fixes] == [
        ("price", "price", "£51.77", 51.77),
        ("currency", "price", None, "GBP"),
        ("stock", "number", "1 204", 1204),
    ]
    assert rec.findings == []
    assert rec.usable


def test_record_flag_keeps_value_and_reports() -> None:
    checker = _checker({"price": {"type": "number", "clean": ["price"], "severity": "hold"}})
    rec = checker.check({"price": "1,299"}, run_id="r")
    assert rec.data["price"] == "1,299"
    checks = [(f.check, f.severity.value) for f in rec.findings]
    assert ("clean.price.ambiguous_separator", "hold") in checks
    assert not rec.usable


def test_record_bad_option_becomes_error_finding() -> None:
    checker = _checker({"price": {"clean": ["price"], "clean_options": {"locale": "fr"}}})
    rec = checker.check({"price": "12"}, run_id="r")
    assert rec.data["price"] == "12"
    assert rec.findings[0].check == "clean.price.error"
    assert "locale" in rec.findings[0].message


def test_record_currency_conflict_is_info() -> None:
    checker = _checker({"price": {"clean": ["price"]}, "currency": {}})
    rec = checker.check({"price": "€10", "currency": "GBP"}, run_id="r")
    assert rec.data == {"price": 10.0, "currency": "GBP"}
    assert [f.check for f in rec.findings] == ["clean.price.extra_conflict"]
