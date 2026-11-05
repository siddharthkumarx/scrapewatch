import importlib
import subprocess
import sys

import scrapewatch

CORE_MODULES = [
    "scrapewatch",
    "scrapewatch.models",
    "scrapewatch.config",
    "scrapewatch.redact",
    "scrapewatch.runtime",
    "scrapewatch.errors",
    "scrapewatch.history",
    "scrapewatch.history.baselines",
    "scrapewatch.cost",
    "scrapewatch.guard",
    "scrapewatch.checks",
    "scrapewatch.checks.expr",
    "scrapewatch.gate",
    "scrapewatch.clean",
    "scrapewatch.clean.price",
    "scrapewatch.clean.number",
    "scrapewatch.clean.date",
    "scrapewatch.clean.text",
    "scrapewatch.clean.url",
    "scrapewatch.clean.dedupe",
    "scrapewatch.report",
    "scrapewatch.alerts",
    "scrapewatch.alerts.telegram",
    "scrapewatch.alerts.slack",
    "scrapewatch.alerts.email",
    "scrapewatch.alerts.webhook",
    "scrapewatch.cli.main",
    "scrapewatch.io",
    "scrapewatch.waste",
    "scrapewatch.integrations",
    # Integration modules must import without their framework installed (lazy imports).
    "scrapewatch.integrations.httpx",
    "scrapewatch.integrations.requests",
    "scrapewatch.integrations.playwright",
    "scrapewatch.integrations.scrapy",
    "scrapewatch.integrations.crawlee",
]


def test_version_is_a_string() -> None:
    assert isinstance(scrapewatch.__version__, str)
    assert scrapewatch.__version__


def test_every_core_module_imports() -> None:
    for name in CORE_MODULES:
        importlib.import_module(name)


def test_public_api_names() -> None:
    for name in ("watch", "Watch", "check", "PublishGate", "classify", "Run", "Verdict"):
        assert hasattr(scrapewatch, name), name


def test_cli_entry_point_runs() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "scrapewatch.cli.main", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "scrapewatch" in out.stdout


def test_no_network_or_telemetry_modules_in_core() -> None:
    """Core never imports an HTTP client at import time; alerts use the stdlib at call time."""
    code = (
        "import sys, scrapewatch, scrapewatch.cli.main, scrapewatch.alerts, scrapewatch.report;"
        "bad=[m for m in ('httpx','requests','urllib3','aiohttp') if m in sys.modules];"
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
    assert out.stdout.strip() == ""
