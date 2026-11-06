"""``scrapewatch`` entry point."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, NoReturn

import click

from scrapewatch._version import __version__
from scrapewatch.errors import ScrapewatchError
from scrapewatch.models import Finding, FindingScope, Run, Severity, VerdictStatus

EXIT_PASS = 0
EXIT_WARN = 1
EXIT_HOLD = 2
EXIT_ERROR = 3
EXIT_BY_VERDICT = {
    VerdictStatus.PASS: EXIT_PASS,
    VerdictStatus.WARN: EXIT_WARN,
    VerdictStatus.HOLD: EXIT_HOLD,
}


class _Group(click.Group):
    """A group whose usage errors exit 3, not click's 2 (2 means hold here)."""

    def main(self, *args: Any, **kwargs: Any) -> Any:
        """Run the command; map click's own errors to exit 3."""
        kwargs["standalone_mode"] = False
        try:
            code = super().main(*args, **kwargs)
        except click.ClickException as exc:
            exc.show()
            sys.exit(EXIT_ERROR)
        except click.Abort:
            click.echo("Aborted.", err=True)
            sys.exit(EXIT_ERROR)
        sys.exit(code if isinstance(code, int) else EXIT_PASS)


def _fail(message: str) -> NoReturn:
    click.echo(f"error: {message}", err=True)
    sys.exit(EXIT_ERROR)


def _write_json(path: str | Path, run: Run) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(run.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def _gate(run: Run, source: Path, live: Path, *, promote_on_warn: bool) -> str:
    """Stage a copy of the checked file and promote it on the verdict; returns a status line."""
    from scrapewatch.gate import PublishGate

    gate = PublishGate(live, promote_on_warn=promote_on_warn)
    warning = ""
    if gate.last_good() is not None and not gate.verify_live():
        warning = f"\nwarning: {live} differs from the last promoted file (edited by hand?)"
    with gate.stage(run.run_id) as staging:
        shutil.copyfile(source, staging)
    for action in gate.recovered:
        run.findings.append(
            Finding(
                run_id=run.run_id,
                scope=FindingScope.RUN,
                check="gate.recovered",
                severity=Severity.INFO,
                message=f"The publish gate recovered an interrupted promotion: {action}",
            )
        )
    if gate.promote(run.run_id, run.verdict):
        return f"live: promoted to {live}{warning}"
    good = gate.last_good()
    if good is None:
        return f"live: held; nothing was promoted to {live} yet{warning}"
    return (
        f"live: held; last good run {good.run_id[:8]} from {good.promoted_at} "
        f"is still live at {live}{warning}"
    )


@click.group(cls=_Group, context_settings={"help_option_names": ["-h", "--help"]})
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
@click.option(
    "--hold-on-warn", is_flag=True, help="With --promote-to: promote only on pass, not on warn."
)
def check(
    path: str,
    rules_path: str,
    history_path: str | None,
    name: str | None,
    report_path: str | None,
    json_path: str | None,
    promote_to: str | None,
    fmt: str | None,
    hold_on_warn: bool,
) -> int:
    """Check a CSV/JSONL/Parquet file. Exit 0 pass, 1 warn, 2 hold, 3 error."""
    from scrapewatch.checks.engine import check as run_check

    try:
        run = run_check(path, rules=rules_path, history=history_path, name=name, fmt=fmt)
        gate_line = None
        if promote_to is not None:
            gate_line = _gate(run, Path(path), Path(promote_to), promote_on_warn=not hold_on_warn)
        files = []
        if json_path is not None:
            _write_json(json_path, run)
            files.append(f"summary: {json_path}")
        if report_path is not None:
            # The HTML report comes with build item 13; until then the summary is written.
            summary = Path(report_path).with_suffix(".json")
            _write_json(summary, run)
            files.append(f"summary: {summary} (the HTML report is not built yet)")
    except ScrapewatchError as exc:
        _fail(str(exc))
    except OSError as exc:
        _fail(f"{type(exc).__name__}: {exc}")
    from scrapewatch.cli._output import check_summary

    click.echo(check_summary(run, gate_line=gate_line, files=files))
    return EXIT_BY_VERDICT[run.verdict.status]


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
