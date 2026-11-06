import statistics
import time
from pathlib import Path

import pytest

from scrapewatch.errors import RulesError
from scrapewatch.guard import classify, extract_features, load_rules
from scrapewatch.guard.classify import classify_features
from scrapewatch.guard.features import Features
from scrapewatch.guard.rules import (
    BUNDLED_RULES,
    GuardRuleSet,
    check_regex,
    rule_fixture_paths,
    validate_rules_file,
)
from scrapewatch.models import GuardLabel

RULES_HEAD = 'schema_version: 1\nrules_version: "2026.11.0"\nrules:\n'


def _rules(tmp_path: Path, body: str, name: str = "rules.yml") -> Path:
    path = tmp_path / name
    path.write_text(RULES_HEAD + body, encoding="utf-8")
    return path


TWO_MATCHING = """
  - id: test.block
    vendor: examplecdn
    label: block
    confidence: 0.99
    any:
      - all:
          - status: [403]
  - id: test.captcha
    vendor: examplecdn
    label: captcha
    confidence: 0.5
    any:
      - all:
          - status: [403]
          - has_captcha_widget: true
  - id: test.block.low
    label: block
    confidence: 0.4
    any:
      - all:
          - status: [403]
"""


def test_precedence_beats_confidence(tmp_path: Path) -> None:
    rules = load_rules(_rules(tmp_path, TWO_MATCHING))
    f = Features(status=403, has_captcha_widget=True)
    result = classify_features(f, rules)
    assert result.label == GuardLabel.CAPTCHA
    assert result.rule_id == "test.captcha"
    assert result.signals == ["status in [403]", "captcha widget"]
    # without the widget only the block rules match: higher confidence wins
    result = classify_features(Features(status=403), rules)
    assert result.rule_id == "test.block"
    assert result.confidence == 0.99


def test_rule_order_breaks_ties(tmp_path: Path) -> None:
    body = """
  - id: a.first
    label: block
    confidence: 0.5
    any: [{all: [{status: [403]}]}]
  - id: a.second
    label: block
    confidence: 0.5
    any: [{all: [{status: [403]}]}]
"""
    rules = load_rules(_rules(tmp_path, body))
    assert classify_features(Features(status=403), rules).rule_id == "a.first"


def test_every_signal_kind(tmp_path: Path) -> None:
    body = """
  - id: t.all
    label: challenge
    confidence: 0.8
    any:
      - all:
          - header: {name: server, regex: "^examplecdn$"}
          - header: {name: x-cache, exists: "true"}
          - cookie: "^__ex_"
          - body_marker: "Verify You Are"
          - body_regex: "ray id: [0-9a-f]{8}"
          - title_regex: "^just a"
          - content_type: "text/html"
          - size_below: 9000
          - size_below_baseline_pct: 50
          - text_density_below: 0.1
          - text_length_below: 300
          - has_form_password: false
          - has_captcha_widget: false
  - id: t.redirect
    label: redirect
    confidence: 0.7
    any: [{all: [{final_url_regex: "/login"}]}]
"""
    rules = load_rules(_rules(tmp_path, body))
    assert rules.markers() == ["verify you are"]
    f = Features(
        status=200,
        headers={"server": "ExampleCDN", "x-cache": "MISS"},
        cookie_names=["__ex_bm"],
        markers=["verify you are"],
        body_window="... Ray ID: 8a1b2c3d ...",
        title="Just a second",
        content_type="text/html",
        body_bytes=4000,
        baseline_p50_bytes=50000,
        text_density=0.05,
        text_length=120,
    )
    result = classify_features(f, rules)
    assert result.label == GuardLabel.CHALLENGE
    assert result.signals == [
        "header server ~ ^examplecdn$",
        "header x-cache present",
        "cookie ~ ^__ex_",
        "marker verify you are",
        "body ~ ray id: [0-9a-f]{8}",
        "title ~ ^just a",
        "content-type text/html*",
        "size 4000 < 9000",
        "size 4000 < 50% of baseline 50000",
        "text density 0.05 < 0.1",
        "text length 120 < 300",
        "no password form",
        "no captcha widget",
    ]
    # each signal failing on its own stops the match
    variants: list[dict[str, object]] = [
        {"headers": {"x-cache": "MISS"}},
        {"headers": {"server": "other", "x-cache": "MISS"}},
        {"headers": {"server": "examplecdn"}},
        {"cookie_names": []},
        {"markers": [], "body_window": "Ray ID: 8a1b2c3d"},
        {"body_window": None},
        {"title": None},
        {"content_type": "application/json"},
        {"body_bytes": 9500},
        {"baseline_p50_bytes": None},
        {"text_density": 0.5},
        {"text_length": 400},
        {"has_form_password": True},
        {"has_captcha_widget": True},
    ]
    for change in variants:
        assert classify_features(f.model_copy(update=change), rules).label == GuardLabel.OK, change
    moved = Features(status=200, request_url="https://a/x", final_url="https://a/login?n=1")
    assert classify_features(moved, rules).label == GuardLabel.REDIRECT
    same = Features(status=200, request_url="https://a/login", final_url="https://a/login")
    assert classify_features(same, rules).label == GuardLabel.OK


def test_marker_found_in_window_without_marker_list(tmp_path: Path) -> None:
    body = """
  - id: t.m
    label: consent
    confidence: 0.6
    any: [{all: [{body_marker: "cookie preferences"}]}]
"""
    rules = load_rules(_rules(tmp_path, body))
    f = Features(status=200, body_window="Manage Cookie Preferences")
    assert classify_features(f, rules).label == GuardLabel.CONSENT


def test_no_match_labels(tmp_path: Path) -> None:
    rules = load_rules(_rules(tmp_path, " []\n").parent / "rules.yml")
    assert rules.rules == []
    assert classify_features(Features(status=200), rules).label == GuardLabel.OK
    assert classify_features(Features(status=None), rules).label == GuardLabel.ERROR
    err = Features(status=200, transport_error=True)
    assert classify_features(err, rules).label == GuardLabel.ERROR
    undecodable = Features(status=200, decode_error=True)
    assert classify_features(undecodable, rules).label == GuardLabel.UNKNOWN


def test_merge_extra_rules_overrides_ids(tmp_path: Path) -> None:
    base = _rules(tmp_path, TWO_MATCHING)
    extra = _rules(
        tmp_path,
        """
  - id: test.block
    label: block
    confidence: 0.1
    any: [{all: [{status: [401]}]}]
  - id: test.new
    label: login
    confidence: 0.5
    any: [{all: [{status: [401]}]}]
""",
        name="extra.yml",
    )
    rules = load_rules(base, extra=[extra])
    ids = [r.id for r in rules.rules]
    assert ids == ["test.block", "test.captcha", "test.block.low", "test.new"]
    assert rules.by_id()["test.block"].confidence == 0.1


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (
            "  - {id: a.b, label: block, confidence: 0.5, any: [{all: [{status: [1]}]}]}\n"
            "  - {id: a.b, label: block, confidence: 0.5, any: [{all: [{status: [2]}]}]}\n",
            "duplicate rule id a.b",
        ),
        (
            "  - {id: a.b, label: block, confidence: 0.5, "
            "any: [{all: [{body_regex: '(.*)+x'}]}]}\n",
            "nested or adjacent",
        ),
        (
            "  - {id: a.b, label: block, confidence: 0.5, any: [{all: [{title_regex: '(['}]}]}\n",
            "does not compile",
        ),
        (
            "  - {id: a.b, label: block, confidence: 0.5, "
            "any: [{all: [{status: [1], cookie: x}]}]}\n",
            "exactly one key",
        ),
        ("  - {id: a.b, label: block, confidence: 0.5, any: [{all: [{}]}]}\n", "exactly one key"),
        (
            "  - {id: a.b, label: block, confidence: 0.5, any: [{all: [{header: {name: x}}]}]}\n",
            "header signal",
        ),
        (
            "  - {id: a.b, label: block, confidence: 0.5, "
            "any: [{all: [{header: {name: x, exists: 'no'}}]}]}\n",
            "exists must be",
        ),
        (
            "  - {id: a.b, label: block, confidence: 0.5, "
            "any: [{all: [{header: {name: x, regex: 'a.*.*b'}}]}]}\n",
            "adjacent",
        ),
        (
            "  - {id: a.b, label: block, confidence: 0.5, any: [{all: [{body_marker: ' '}]}]}\n",
            "body_marker",
        ),
        (
            "  - {id: A, label: block, confidence: 0.5, any: [{all: [{status: [1]}]}]}\n",
            "String should match pattern",
        ),
        ("  - {id: a.b, label: nope, confidence: 0.5, any: [{all: [{status: [1]}]}]}\n", "label"),
        (
            "  - {id: a.b, label: block, confidence: 0.5, any: [{all: [{statsu: [1]}]}]}\n",
            "Extra inputs",
        ),
    ],
)
def test_invalid_rules_rejected(tmp_path: Path, body: str, problem: str) -> None:
    path = _rules(tmp_path, body)
    problems = validate_rules_file(path)
    assert any(problem in p for p in problems), problems
    with pytest.raises(RulesError):
        load_rules(path)


def test_invalid_files(tmp_path: Path) -> None:
    assert validate_rules_file(tmp_path / "missing.yml")[0].startswith("cannot read")
    bad = tmp_path / "bad.yml"
    bad.write_text("rules: [", encoding="utf-8")
    assert "not valid YAML" in validate_rules_file(bad)[0]
    bad.write_text("- 1\n", encoding="utf-8")
    assert "mapping" in validate_rules_file(bad)[0]
    bad.write_text("schema_version: 2\nrules_version: x\nrules: []\n", encoding="utf-8")
    assert "schema_version" in validate_rules_file(bad)[0]
    good = _rules(tmp_path, TWO_MATCHING, name="good.yml")
    with pytest.raises(RulesError):
        load_rules(good, extra=[bad])


def test_check_regex_length() -> None:
    assert check_regex("a" * 301) is not None
    assert check_regex("^cloudflare$") is None


def test_rule_fixture_paths_by_name_and_rule_key(tmp_path: Path) -> None:
    rules = load_rules(_rules(tmp_path, TWO_MATCHING))
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "test.block.yml").write_text("expect: {label: block}\n", encoding="utf-8")
    (fx / "test.block.variant.yml").write_text("expect: {label: block}\n", encoding="utf-8")
    (fx / "other.yml").write_text("rule: test.captcha\n", encoding="utf-8")
    (fx / "broken.yml").write_text("rule: [", encoding="utf-8")
    mapping = rule_fixture_paths(rules, fx)
    assert [p.name for p in mapping["test.block"]] == ["test.block.variant.yml", "test.block.yml"]
    assert [p.name for p in mapping["test.captcha"]] == ["other.yml"]
    assert mapping["test.block.low"] == []


def test_classify_convenience_uses_bundled_rules() -> None:
    assert classify(status=429, headers={}).label == GuardLabel.RATE_LIMIT
    ok = classify(
        status=200,
        headers={"content-type": "text/html"},
        body=b"<html><body>" + b"<p>plenty of text here</p>" * 200 + b"</body></html>",
        url="https://books.toscrape.com/",
    )
    assert ok.label == GuardLabel.OK
    assert BUNDLED_RULES.is_file()
    assert isinstance(load_rules(), GuardRuleSet)


def _page_64k() -> bytes:
    row = b"<tr><td class='title'><a href='/catalogue/x_1/index.html'>A book</a></td>"
    row += b"<td class='price'>&pound;51.77</td><td>In stock</td></tr>\n"
    head = b"<html><head><title>Catalogue</title><script>var a=1;</script></head><body><table>"
    body = head + row * (65536 // len(row))
    return body[:65536]


@pytest.mark.perf
def test_classify_64k_under_budget() -> None:
    body = _page_64k()
    assert len(body) == 65536
    headers = {"content-type": "text/html; charset=utf-8", "server": "nginx"}
    for _ in range(5):
        classify(status=200, headers=headers, body=body, url="https://books.toscrape.com/")
    times = []
    for _ in range(50):
        t0 = time.perf_counter()
        classify(status=200, headers=headers, body=body, url="https://books.toscrape.com/")
        times.append((time.perf_counter() - t0) * 1000)
    p50 = statistics.median(times)
    print(f"classify 64 KiB p50 {p50:.3f} ms")
    assert p50 < 4.5  # architecture section 15: 1.5 ms budget with 3x slack


def test_extract_features_export() -> None:
    f = extract_features(status=200, headers={}, body=None, request_url=None, final_url=None)
    assert isinstance(f, Features)
