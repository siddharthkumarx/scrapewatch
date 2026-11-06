import json
from pathlib import Path

import pytest

from scrapewatch.guard.features import (
    Features,
    decode_window,
    extract_features,
    markers_for_fixture,
    visible_text,
)

HTML = Path(__file__).parents[1] / "fixtures" / "guard" / "html"
GOLDEN = json.loads((HTML / "features.expected.json").read_text("utf-8"))


@pytest.mark.parametrize("name", sorted(GOLDEN["cases"]))
def test_features_golden(name: str) -> None:
    case = GOLDEN["cases"][name]
    c = case["input"]
    f = extract_features(
        status=c["status"],
        headers=c["headers"],
        body=(HTML / name).read_bytes(),
        request_url=c["url"],
        final_url=c.get("final_url"),
        baseline_p50_bytes=c.get("baseline_p50_bytes"),
        markers=GOLDEN["markers"],
    )
    got = f.model_dump(mode="json", exclude={"body_window"})
    expected = dict(case["expected"])
    assert got.pop("text_density") == pytest.approx(expected.pop("text_density"), abs=1e-6)
    assert got == expected
    assert f.body_window is not None  # kept in memory for body_regex rules


def test_str_body_and_window_cut() -> None:
    body = "<html><title>T</title><body>" + "é" * 100 + "</body></html>"
    f = extract_features(
        status=200, headers={}, body=body, request_url=None, final_url=None, window_bytes=50
    )
    assert f.body_bytes == len(body.encode())
    assert f.body_window is not None
    assert len(f.body_window) == 50


def test_window_cuts_a_multibyte_character() -> None:
    body = ("<p>" + "€" * 30 + "</p>").encode()
    f = extract_features(
        status=200,
        headers={"content-type": "text/html; charset=utf-8"},
        body=body,
        request_url=None,
        final_url=None,
        window_bytes=10,
    )
    assert not f.decode_error
    assert f.body_window == "<p>€€"


def test_binary_body_is_a_decode_error() -> None:
    f = extract_features(
        status=200,
        headers={"content-type": "text/html"},
        body=b"\x89PNG\x00\x00\x81\x8d",
        request_url=None,
        final_url=None,
    )
    assert f.decode_error
    assert f.body_window is None


def test_unknown_charset_falls_back() -> None:
    assert decode_window("café".encode(), "no-such-codec") == "café"
    assert decode_window(b"caf\xe9", "utf-8") == "café"  # cp1252 fallback
    assert decode_window(b"\x81\x8d\x8f", None) is None


def test_non_text_content_skips_text_analysis() -> None:
    f = extract_features(
        status=200,
        headers={"Content-Type": "application/json"},
        body=b'{"title": "<title>x</title>", "g-recaptcha": 1}',
        request_url=None,
        final_url=None,
        markers=["g-recaptcha"],
    )
    assert f.content_type == "application/json"
    assert f.title is None
    assert f.markers == []
    assert not f.has_captcha_widget
    assert f.body_bytes > 0


def test_no_body() -> None:
    f = extract_features(status=204, headers={}, body=None, request_url=None, final_url=None)
    assert f.body_bytes == 0
    assert f.text_density == 0.0
    empty = extract_features(status=200, headers={}, body=b"", request_url=None, final_url=None)
    assert empty.text_density == 0.0


def test_default_markers_come_from_bundled_rules() -> None:
    f = extract_features(status=200, headers={}, body="<p>x</p>", request_url=None, final_url=None)
    assert f.markers == []


def test_visible_text() -> None:
    html = "<!-- c --><style>x{}</style><SCRIPT>a<b</SCRIPT><p>A &amp;\n B</p><title>T</title>"
    assert visible_text(html) == "A & B"


def test_title_is_clipped() -> None:
    body = "<title>" + "x" * 500 + "</title>"
    f = extract_features(status=200, headers={}, body=body, request_url=None, final_url=None)
    assert f.title == "x" * 200


def test_markers_for_fixture() -> None:
    f = Features(
        status=200,
        markers=["m" * 300],
        title="t",
        body_window="secret page",
        request_url="https://a.example/?token=abc",
        final_url="https://u:p@a.example/x",
        text_density=0.123456789,
    )
    safe = markers_for_fixture(f)
    assert safe.body_window is None
    assert safe.markers == ["m" * 200]
    assert safe.request_url == "https://a.example/?token=[redacted]"
    assert safe.final_url == "https://a.example/x"
    assert safe.text_density == 0.1235
    assert markers_for_fixture(Features()).title is None
