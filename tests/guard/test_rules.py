"""Guard rules: every fixture classifies as expected, every rule has a fixture, rules are safe."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scrapewatch import _yaml
from scrapewatch.guard.classify import classify_features
from scrapewatch.guard.features import MARKER_SNIPPET_MAX, Features
from scrapewatch.guard.rules import (
    BUNDLED_RULES,
    bundled_rules,
    rule_fixture_paths,
    validate_rules_file,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_FILES = sorted(FIXTURES.glob("*.yml"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = _yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def _keys(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [str(k) for k in node] + [k for v in node.values() for k in _keys(v)]
    if isinstance(node, list):
        return [k for v in node for k in _keys(v)]
    return []


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=[p.name for p in FIXTURE_FILES])
def test_fixture_classifies_as_expected(path: Path) -> None:
    data = _load(path)
    features = Features.model_validate(data["features"])
    result = classify_features(features, bundled_rules())
    expect = data["expect"]
    assert result.label.value == expect["label"], result.signals
    if "vendor" in expect:
        assert result.vendor == expect["vendor"]
    if "rule_id" in expect:
        assert result.rule_id == expect["rule_id"]


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=[p.name for p in FIXTURE_FILES])
def test_fixture_is_safe_to_commit(path: Path) -> None:
    data = _load(path)
    assert set(data) <= {"rule", "expect", "features"}
    assert "body" not in _keys(data), "fixtures never hold a page body"
    features = data["features"]
    for marker in features.get("markers", []):
        assert len(marker) <= MARKER_SNIPPET_MAX
    assert len(features.get("body_window") or "") <= MARKER_SNIPPET_MAX
    assert len(features.get("title") or "") <= MARKER_SNIPPET_MAX


def test_every_rule_has_a_fixture() -> None:
    mapping = rule_fixture_paths(bundled_rules(), FIXTURES)
    missing = [rule_id for rule_id, paths in mapping.items() if not paths]
    assert missing == [], f"rules without a fixture: {missing}"


def test_bundled_rules_file_is_valid() -> None:
    assert validate_rules_file(BUNDLED_RULES) == []
    rules = bundled_rules()
    assert rules.rules_version
    assert len({r.id for r in rules.rules}) == len(rules.rules)
