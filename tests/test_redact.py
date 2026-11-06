import json
from pathlib import Path
from typing import Any

import pytest

from scrapewatch.redact import (
    KEPT_HEADERS,
    REDACTED,
    cookie_names,
    is_sensitive_header,
    redact_headers,
    redact_url,
)

REDACT = Path(__file__).parent / "fixtures" / "redact"
HEADER_CASES: list[dict[str, Any]] = json.loads((REDACT / "headers.json").read_text("utf-8"))
URL_CASES: list[dict[str, str]] = json.loads((REDACT / "urls.json").read_text("utf-8"))


def test_golden_sizes() -> None:
    assert len(HEADER_CASES) == 20
    assert len(URL_CASES) == 20


@pytest.mark.parametrize("case", HEADER_CASES, ids=[c["name"] for c in HEADER_CASES])
def test_header_goldens(case: dict[str, Any]) -> None:
    assert redact_headers(case["input"]) == case["expected"]["headers"]
    assert cookie_names(case["input"]) == case["expected"]["cookie_names"]


@pytest.mark.parametrize("case", URL_CASES, ids=[c["input"] for c in URL_CASES])
def test_url_goldens(case: dict[str, str]) -> None:
    assert redact_url(case["input"]) == case["expected"]


def test_no_kept_header_is_sensitive() -> None:
    assert [h for h in KEPT_HEADERS if is_sensitive_header(h)] == []


def test_redacted_marker() -> None:
    assert redact_url("https://a.example/?token=1").endswith(REDACTED)


class _MultiHeaders:
    """Mimics httpx.Headers: items() merges repeats, get_list() keeps them apart."""

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self._pairs = pairs

    def items(self) -> list[tuple[str, str]]:
        merged: dict[str, str] = {}
        for k, v in self._pairs:
            merged[k] = f"{merged[k]}, {v}" if k in merged else v
        return list(merged.items())

    def get_list(self, name: str) -> list[str]:
        return [v for k, v in self._pairs if k == name]


def test_multi_value_header_object() -> None:
    headers = _MultiHeaders(
        [
            ("set-cookie", "a=1; Expires=Wed, 21 Oct 2026 07:28:00 GMT"),
            ("set-cookie", "b=2"),
            ("content-type", "text/html"),
        ]
    )
    assert cookie_names(headers) == ["a", "b"]  # type: ignore[arg-type]
    assert redact_headers(headers) == {"content-type": "text/html"}  # type: ignore[arg-type]


def test_header_object_with_failing_getter() -> None:
    class Odd(dict[str, str]):
        def getlist(self, name: str) -> list[str]:
            raise RuntimeError("odd")

    assert cookie_names(Odd({"Set-Cookie": "x=1"})) == ["x"]
