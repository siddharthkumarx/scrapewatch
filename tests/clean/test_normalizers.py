"""Goldens and idempotence for the date, url and text normalizers (build item 11)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import yaml

from scrapewatch.checks.rules import KNOWN_NORMALIZERS
from scrapewatch.clean import NORMALIZERS
from scrapewatch.clean.pipeline import run_normalizers

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "clean"
NAMES = ["date", "url", "whitespace", "html_entities", "strip_tags", "mojibake"]


def _cases(name: str) -> list[dict[str, Any]]:
    data = yaml.safe_load((FIXTURES / f"{name}.yml").read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = data["cases"]
    return cases


ALL = [(name, case) for name in NAMES for case in _cases(name)]


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(expected, float) and math.isnan(expected):
        return isinstance(actual, float) and math.isnan(actual)
    return type(actual) is type(expected) and actual == expected


@pytest.mark.parametrize(("name", "case"), ALL, ids=[f"{n}:{c['input']!r}" for n, c in ALL])
def test_golden(name: str, case: dict[str, Any]) -> None:
    out = NORMALIZERS[name](case["input"], case["options"])
    expect = case["expect"]
    assert _same(out.value, expect.get("value", case["input"])), out
    assert out.changed is expect.get("changed", False), out
    assert out.flag == expect.get("flag"), out
    assert out.note == expect.get("note"), out
    if out.flag:
        assert out.flag_message
        assert not out.changed


@pytest.mark.parametrize("name", NAMES)
def test_idempotent(name: str) -> None:
    fn = NORMALIZERS[name]
    for case in _cases(name):
        first = fn(case["input"], case["options"])
        if first.flag or not first.changed:
            continue
        opts = {k: v for k, v in case["options"].items() if k != "reference_time"}
        second = fn(first.value, opts)
        assert second.flag is None, (case, second)
        assert not second.changed, (case, second)


def test_registry_matches_rules_vocabulary() -> None:
    assert set(NORMALIZERS) == set(KNOWN_NORMALIZERS)


@pytest.mark.parametrize(
    ("name", "options", "match"),
    [
        ("date", {"assume_tz": "Mars/Base"}, "Mars"),
        ("date", {"output": "week"}, "output"),
        ("date", {"reference_time": "2026-11-05T12:00:00"}, "reference_time"),
        ("url", {"trailing_slash": "maybe"}, "trailing_slash"),
    ],
)
def test_bad_options_raise(name: str, options: dict[str, Any], match: str) -> None:
    value = "2 days ago" if "reference_time" in options else "2026-11-05T00:00:00Z"
    with pytest.raises(Exception, match=match):
        NORMALIZERS[name](value if name == "date" else "https://a.example/", options)


def test_date_objects() -> None:
    from datetime import date, datetime, timedelta, timezone

    fn = NORMALIZERS["date"]
    aware = datetime(2026, 11, 5, 14, 12, 3, tzinfo=timezone(timedelta(hours=1)))
    assert fn(aware, {}).value == "2026-11-05T13:12:03Z"
    assert fn(date(2026, 11, 5), {}).value == "2026-11-05"
    naive = datetime(2026, 11, 5, 14, 12)
    assert fn(naive, {}).flag == "naive"
    assert fn(naive, {"assume_tz": "Europe/Berlin"}).value == "2026-11-05T13:12:00Z"


def test_relative_note_becomes_info_finding() -> None:
    r = run_normalizers(
        "published",
        "2 days ago",
        ["date"],
        {"reference_time": "2026-11-05T12:00:00Z"},
        run_id="r",
        record_id="c",
    )
    assert r.value == "2026-11-03T12:00:00Z"
    assert [(f.check, f.severity.value) for f in r.findings] == [("clean.date.relative", "info")]
    assert len(r.fixes) == 1
