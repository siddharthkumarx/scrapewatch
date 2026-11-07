"""The examples (build item 23) load offline: rules, settings and the files each one needs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scrapewatch.checks.engine import check_records
from scrapewatch.checks.rules import load_data_rules
from scrapewatch.config import load_settings

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
NAMES = ["any_language", "httpx_books", "playwright_quotes", "scrapy_scrapethissite"]


def test_the_four_examples() -> None:
    assert sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir() and p.name[0] != "_") == NAMES


@pytest.mark.parametrize("name", NAMES)
def test_example_layout_rules_and_settings(name: str) -> None:
    example = EXAMPLES / name
    for f in ("README.md", "rules.yml", "scrapewatch.yml", "run.sh"):
        assert (example / f).is_file(), f
    assert os.access(example / "run.sh", os.X_OK)
    rules = load_data_rules(example / "rules.yml")
    settings = load_settings(example / "scrapewatch.yml")
    assert rules.name == settings.name
    assert settings.cost.proxies  # a sample cost model
    assert str(settings.history.path) == ".scrapewatch"  # git-ignored


def test_shell_scraper_parses_a_catalogue_snippet(tmp_path: Path) -> None:
    awk = shutil.which("awk")
    if awk is None:  # pragma: no cover - every CI runner has awk
        pytest.skip("no awk")
    script = (EXAMPLES / "any_language" / "scrape.sh").read_text("utf-8")
    program = script.split('awk -v base="$base" \'', 1)[1].rsplit("'", 1)[0]
    html = (
        '<h3><a href="a-light-in-the-attic_1000/index.html" title="A &quot;Light&quot; '
        'it&#39;s">A Light ...</a></h3>\n<p class="price_color">£51.77</p>\n'
    )
    out = subprocess.run(
        [awk, "-v", "base=https://books.toscrape.com/catalogue/", program],
        input=html,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    (tmp_path / "books.jsonl").write_text(out, encoding="utf-8")
    rules = load_data_rules(EXAMPLES / "any_language" / "rules.yml")
    items = [json.loads(line) for line in out.splitlines()]
    result = check_records(items, rules)
    assert result.records[0].data == {
        "title": 'A "Light" it\'s',
        "price": 51.77,
        "currency": "GBP",
        "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
    }
    assert result.records[0].usable
