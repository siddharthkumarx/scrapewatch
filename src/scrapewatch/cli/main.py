"""``scrapewatch`` entry point."""

from __future__ import annotations

import click

from scrapewatch._version import __version__

EXIT_PASS = 0
EXIT_WARN = 1
EXIT_HOLD = 2
EXIT_ERROR = 3


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="scrapewatch")
def main() -> None:
    """Health and cost monitor for any web scraper."""


@main.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--rules", "rules_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
@click.option("--name", default=None, help="Run name used for baselines (default: rules name).")
@click.option("--report", "report_path", default=None, type=click.Path(dir_okay=False))
@click.option("--json", "json_path", default=None, type=click.Path(dir_okay=False))
@click.option(
    "--promote-to", default=None, type=click.Path(dir_okay=False), help="Publish-gate live path."
)
@click.option("--format", "fmt", default=None, type=click.Choice(["csv", "jsonl", "parquet"]))
def check(
    path: str,
    rules_path: str,
    history_path: str | None,
    name: str | None,
    report_path: str | None,
    json_path: str | None,
    promote_to: str | None,
    fmt: str | None,
) -> None:
    """Check a CSV/JSONL/Parquet file. Exit 0 pass, 1 warn, 2 hold, 3 error."""
    raise NotImplementedError("build-progress item 12")


@main.group()
def guard() -> None:
    """Classify saved responses and extract fixture features."""


@guard.command("classify")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--status", type=int, default=200)
@click.option("--url", default=None)
def guard_classify(path: str, status: int, url: str | None) -> None:
    """Classify a saved HTML/HTTP response file."""
    raise NotImplementedError("build-progress item 14")


@guard.command("features")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--status", type=int, default=200)
@click.option("--url", default=None)
def guard_features(path: str, status: int, url: str | None) -> None:
    """Print the fixture-safe features of a saved response (for contributing rules)."""
    raise NotImplementedError("build-progress item 14")


@main.group()
def rules() -> None:
    """Validate and test rule files."""


@rules.command("test")
@click.argument("path", required=False, type=click.Path(exists=True))
def rules_test(path: str | None) -> None:
    """Run every guard fixture against the rules (bundled or PATH)."""
    raise NotImplementedError("build-progress item 14")


@rules.command("validate")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
def rules_validate(path: str) -> None:
    """Validate a guard-rules or data-rules file."""
    raise NotImplementedError("build-progress item 14")


@main.command()
@click.argument("run_id")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
@click.option("--out", default=None, type=click.Path(dir_okay=False))
def report(run_id: str, history_path: str | None, out: str | None) -> None:
    """Re-render the HTML report of a past run."""
    raise NotImplementedError("build-progress item 14")


@main.group()
def history() -> None:
    """Inspect and prune the history store."""


@history.command("list")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
@click.option("--name", default=None)
@click.option("--limit", default=20)
def history_list(history_path: str | None, name: str | None, limit: int) -> None:
    """List recent runs."""
    raise NotImplementedError("build-progress item 14")


@history.command("show")
@click.argument("run_id")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
def history_show(run_id: str, history_path: str | None) -> None:
    """Print one run's summary.json."""
    raise NotImplementedError("build-progress item 14")


@history.command("prune")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
def history_prune(history_path: str | None) -> None:
    """Apply retention."""
    raise NotImplementedError("build-progress item 14")


@main.command()
@click.argument("run_id")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
def cost(run_id: str, history_path: str | None) -> None:
    """Print the cost and waste breakdown of a run."""
    raise NotImplementedError("build-progress item 14")


@main.group()
def alert() -> None:
    """Alert channels."""


@alert.command("test")
@click.option("--channel", default=None)
def alert_test(channel: str | None) -> None:
    """Send a test alert to configured channels."""
    raise NotImplementedError("build-progress item 15")


@main.command()
@click.option("--dir", "target", default=".", type=click.Path(file_okay=False))
def init(target: str) -> None:
    """Write an example rules.yml and scrapewatch.yml."""
    raise NotImplementedError("build-progress item 14")


if __name__ == "__main__":  # pragma: no cover
    main()
