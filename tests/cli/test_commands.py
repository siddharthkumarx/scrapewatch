"""The remaining CLI commands (build item 14)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from scrapewatch import _yaml, check
from scrapewatch.checks.rules import load_data_rules
from scrapewatch.cli.main import main
from scrapewatch.config import Settings, load_settings
from scrapewatch.guard.features import Features
from scrapewatch.runtime import watch

ROOT = Path(__file__).resolve().parents[2]
GUARD_FIXTURES = ROOT / "tests" / "guard" / "fixtures"
CLI = ROOT / "tests" / "fixtures" / "cli"
CHALLENGE = (
    "HTTP/1.1 403 Forbidden\r\nServer: cloudflare\r\ncf-mitigated: challenge\r\n"
    "Content-Type: text/html; charset=UTF-8\r\nSet-Cookie: __cf_bm=abc; path=/\r\n\r\n"
    "<html><head><title>Just a moment...</title></head><body>"
    '<div id="cf-chl-widget">checking</div></body></html>'
)


def _run(args: list[str]) -> Any:
    return CliRunner().invoke(main, args, catch_exceptions=False)


def _history(tmp_path: Path) -> tuple[Path, str]:
    """A history with two file checks and one watch() run with a challenge page."""
    hist = tmp_path / "h"
    src = tmp_path / "b.jsonl"
    shutil.copyfile(CLI / "books50.jsonl", src)
    check(src, rules=CLI / "rules.yml", history=hist)
    check(src, rules=CLI / "rules.yml", history=hist)
    settings = Settings(history={"path": hist})
    with watch("books", settings=settings) as w:
        w.record_response(
            "https://books.toscrape.com/catalogue/page-2.html?token=tok3nvalue",
            status=403,
            headers={"server": "cloudflare", "cf-mitigated": "challenge"},
            body=b"<title>Just a moment...</title>",
            wire_bytes=7000,
            proxy="datacenter",
        )
        w.record_response("https://books.toscrape.com/", status=200, body=b"<p>" + b"x" * 5000)
    return hist, w.run.run_id


def test_guard_classify_raw_http_and_html(tmp_path: Path) -> None:
    raw = tmp_path / "resp.txt"
    raw.write_text(CHALLENGE, encoding="utf-8")
    result = _run(["guard", "classify", str(raw)])
    assert result.exit_code == 0
    assert result.output.splitlines()[:3] == [
        "label: challenge",
        "vendor: cloudflare",
        "rule: cloudflare.challenge",
    ]
    assert "signals:" in result.output
    page = tmp_path / "page.html"
    page.write_text("<html><title>Books</title><p>" + "word " * 400, encoding="utf-8")
    ok = _run(["guard", "classify", str(page), "--url", "https://books.toscrape.com/"])
    assert ok.output.startswith("label: ok")
    assert "signals: none" in ok.output
    rate = _run(["guard", "classify", str(page), "--status", "429"])
    assert rate.output.startswith("label: rate_limit")


def test_guard_features_is_a_valid_fixture(tmp_path: Path) -> None:
    raw = tmp_path / "resp.txt"
    raw.write_text(CHALLENGE.replace("\r\n", "\n"), encoding="utf-8")  # bare newlines too
    result = _run(["guard", "features", str(raw), "--url", "https://a.example/x?token=s3cret"])
    assert result.exit_code == 0
    data = _yaml.safe_load(result.output)
    assert set(data) == {"rule", "expect", "features"}
    assert data["rule"] == "cloudflare.challenge"
    assert data["expect"] == {"label": "challenge", "vendor": "cloudflare"}
    features = Features.model_validate(data["features"])
    assert features.status == 403
    assert "body" not in json.dumps(data["features"]).replace("body_bytes", "")
    assert "s3cret" not in result.output
    assert "cf-chl" in features.markers


def test_rules_test_passes_on_bundled_fixtures_and_fails_on_broken_one(tmp_path: Path) -> None:
    ok = _run(["rules", "test", str(GUARD_FIXTURES)])
    assert ok.exit_code == 0, ok.output
    assert "all passed" in ok.output
    broken = tmp_path / "fx"
    shutil.copytree(GUARD_FIXTURES, broken)
    target = broken / "cloudflare.challenge.yml"
    target.write_text(target.read_text("utf-8").replace("label: challenge", "label: ok"), "utf-8")
    (broken / "junk.yml").write_text("features: [not, a, mapping]\n", encoding="utf-8")
    bad = _run(["rules", "test", str(broken)])
    assert bad.exit_code == 1
    assert "FAIL cloudflare.challenge.yml: label" in bad.output
    assert "FAIL junk.yml: cannot be read" in bad.output


def test_rules_test_reports_rules_without_fixtures(tmp_path: Path, monkeypatch: Any) -> None:
    rules = tmp_path / "rules.yml"
    rules.write_text(
        "schema_version: 1\nrules_version: '2026.11.0'\nrules:\n"
        "  - id: mine.block\n    label: block\n    confidence: 0.9\n"
        "    any: [{all: [{status: [418]}]}]\n",
        encoding="utf-8",
    )
    (tmp_path / "fx").mkdir()
    result = _run(["rules", "test", str(tmp_path / "fx"), "--rules", str(rules)])
    assert result.exit_code == 1
    assert "rule mine.block has no fixture" in result.output
    monkeypatch.chdir(tmp_path)
    assert _run(["rules", "test"]).exit_code == 3  # no tests/guard/fixtures here
    bad = tmp_path / "bad.yml"
    bad.write_text("rules: 3\n", encoding="utf-8")
    assert _run(["rules", "test", str(tmp_path / "fx"), "--rules", str(bad)]).exit_code == 3


def test_rules_validate(tmp_path: Path) -> None:
    bundled = ROOT / "src" / "scrapewatch" / "guard" / "rules" / "guard-rules.yml"
    ok = _run(["rules", "validate", str(bundled)])
    assert ok.exit_code == 0
    assert ok.output.startswith("ok: guard rules")
    data = _run(["rules", "validate", str(CLI / "rules.yml")])
    assert data.exit_code == 0
    assert data.output.startswith("ok: data rules 'books'")
    bad_guard = tmp_path / "g.yml"
    bad_guard.write_text(
        "schema_version: 1\nrules_version: x\nrules:\n  - id: a.b\n    label: block\n"
        "    confidence: 1\n    any: [{all: [{body_regex: '(a+)+'}]}]\n",
        encoding="utf-8",
    )
    assert _run(["rules", "validate", str(bad_guard)]).exit_code == 1
    bad_data = tmp_path / "d.yml"
    bad_data.write_text("fields: {price: {clean: [nope]}}\n", encoding="utf-8")
    result = _run(["rules", "validate", str(bad_data)])
    assert result.exit_code == 1
    assert "unknown normalizer" in result.output
    broken = tmp_path / "broken.yml"
    broken.write_text("a: [\n", encoding="utf-8")
    assert _run(["rules", "validate", str(broken)]).exit_code == 1


def test_history_list_show_prune_and_cost(tmp_path: Path) -> None:
    hist, run_id = _history(tmp_path)
    listed = _run(["history", "list", "--history", str(hist)])
    assert listed.exit_code == 0
    lines = listed.output.splitlines()
    assert lines[0].startswith("started (UTC)")
    assert len(lines) == 4
    assert lines[1].endswith(run_id)  # newest first
    assert _run(["history", "list", "--history", str(hist), "--name", "nope"]).output == (
        "no runs yet\n"
    )
    shown = _run(["history", "show", run_id[:8], "--history", str(hist)])
    assert json.loads(shown.output)["run_id"] == run_id
    cost = _run(["cost", run_id, "--history", str(hist)])
    assert cost.exit_code == 0
    assert "by proxy:" in cost.output
    assert "failed or blocked" in cost.output
    pruned = _run(["history", "prune", "--history", str(hist)])
    assert pruned.exit_code == 0
    assert pruned.output.startswith("pruned (keep 50 runs")


def test_unknown_run_and_missing_history_exit_3(tmp_path: Path) -> None:
    hist, _ = _history(tmp_path)
    assert _run(["history", "show", "zzzz", "--history", str(hist)]).exit_code == 3
    assert _run(["cost", "abc", "--history", str(hist)]).exit_code == 3
    missing = _run(["history", "list", "--history", str(tmp_path / "none")])
    assert missing.exit_code == 3
    assert "no history at" in missing.output


def test_report_rerenders_a_past_run(tmp_path: Path) -> None:
    hist, run_id = _history(tmp_path)
    out = tmp_path / "again.html"
    result = _run(["report", run_id[:10], "--history", str(hist), "--out", str(out)])
    assert result.exit_code == 0, result.output
    html = out.read_text("utf-8")
    assert "Pages without data (first 20)" in html
    assert "token=[redacted]" in html
    assert "tok3nvalue" not in html
    default = _run(["report", run_id, "--history", str(hist)])
    assert default.exit_code == 0
    path = Path(default.output.split("report: ", 1)[1].strip())
    assert path.parent == hist / "reports" / "books"
    assert path.is_file()


def test_init_writes_loadable_examples(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    result = _run(["init", "--dir", str(target)])
    assert result.exit_code == 0
    rules = load_data_rules(target / "rules.yml")
    assert rules.name == "books"
    assert "price" in rules.fields
    settings = load_settings(target / "scrapewatch.yml")
    assert settings.cost.proxies["datacenter"].per_gb == 0.8
    assert settings.rules == target / "rules.yml"
    again = _run(["init", "--dir", str(target)])
    assert again.exit_code == 3
    assert "already exist" in again.output
    assert _run(["init", "--dir", str(target), "--force"]).exit_code == 0


@pytest.mark.parametrize("args", [["--help"], ["guard", "--help"], ["history", "--help"]])
def test_help(args: list[str]) -> None:
    assert _run(args).exit_code == 0


def test_cost_text_optional_lines() -> None:
    from scrapewatch.cli._output import cost_text
    from scrapewatch.models import Run

    run = Run(name="x")
    run.cost.browser_minutes = 2.0
    run.cost.api_requests = 10
    run.cost.total = 0.00001
    run.waste.blocked_requests = 3
    text = cost_text(run)
    assert "browser 2.0 min" in text
    assert "API requests 10" in text
    assert "waste cutter: 3 blocked" in text
    assert "per usable record -" in text
    assert "total < 0.0001 USD" in text
