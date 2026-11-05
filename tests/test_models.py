"""Pins the JSON shapes documented in docs/architecture.md "Core data model"."""

import json

import pytest
from pydantic import ValidationError

from scrapewatch.models import (
    BAD_LABELS,
    GUARD_LABEL_PRECEDENCE,
    SCHEMA_VERSION,
    Finding,
    FindingScope,
    GuardLabel,
    Record,
    ResponseEvent,
    Run,
    Severity,
    VerdictStatus,
)


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1


def test_response_event_json_shape() -> None:
    ev = ResponseEvent(
        run_id="r1", url="https://books.toscrape.com/", domain="books.toscrape.com", status=200
    )
    data = json.loads(ev.model_dump_json())
    expected_keys = {
        "schema_version",
        "event_id",
        "run_id",
        "ts",
        "url",
        "final_url",
        "method",
        "domain",
        "status",
        "elapsed_ms",
        "wire_bytes",
        "body_bytes",
        "bytes_source",
        "resource_type",
        "proxy",
        "attempt",
        "retry_of",
        "headers",
        "cookie_names",
        "guard",
        "cache",
        "error",
        "integration",
        "cost",
        "monitor_error",
    }
    assert set(data) == expected_keys
    assert data["guard"] == {
        "label": "ok",
        "vendor": None,
        "rule_id": None,
        "confidence": 0.0,
        "signals": [],
    }
    assert data["cache"] == {"conditional": False, "unchanged": False, "deduped": False}
    assert data["bytes_source"] == "unknown"
    assert data["ts"].endswith("Z") or "+00:00" in data["ts"]


def test_run_json_shape() -> None:
    run = Run(name="books")
    data = json.loads(run.model_dump_json())
    assert set(data) == {
        "schema_version",
        "run_id",
        "name",
        "started_at",
        "finished_at",
        "status",
        "integration",
        "scrapewatch_version",
        "rules_version",
        "config_hash",
        "counts",
        "cost",
        "waste",
        "verdict",
        "findings",
        "report_path",
        "overhead_ms_p50",
    }
    assert data["status"] == "running"
    assert data["verdict"]["status"] == "pass"
    assert data["cost"]["per_usable_record"] is None


def test_record_and_finding_round_trip() -> None:
    f = Finding(
        run_id="r1",
        scope=FindingScope.RECORD,
        ref="rec1",
        check="field.required",
        severity=Severity.HOLD,
        field="price",
        message="price is missing",
    )
    rec = Record(run_id="r1", data={"price": None}, findings=[f], usable=False)
    again = Record.model_validate_json(rec.model_dump_json())
    assert again == rec
    assert again.findings[0].severity is Severity.HOLD


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ResponseEvent(run_id="r1", url="u", domain="d", body=b"never stored")  # type: ignore[call-arg]


def test_label_precedence_covers_every_label_once() -> None:
    assert sorted(GUARD_LABEL_PRECEDENCE, key=str) == sorted(GuardLabel, key=str)
    assert GUARD_LABEL_PRECEDENCE[0] is GuardLabel.CAPTCHA
    assert GUARD_LABEL_PRECEDENCE[-1] is GuardLabel.OK
    assert GuardLabel.OK not in BAD_LABELS
    assert GuardLabel.UNKNOWN not in BAD_LABELS


def test_verdict_values_match_exit_codes() -> None:
    assert [v.value for v in VerdictStatus] == ["pass", "warn", "hold"]
