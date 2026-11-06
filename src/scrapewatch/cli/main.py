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


def _write_report(
    run: Run, path: Path, history_path: str | None, gate_line: str | None
) -> list[str]:
    """Write the HTML report and summary.json beside it; returns lines for the summary."""
    from scrapewatch.config import load_settings
    from scrapewatch.history.store import open_store
    from scrapewatch.report import render_html, write_html, write_summary

    history: list[Run] = []
    store_path = Path(history_path) if history_path else load_settings().history.path
    try:
        store = open_store(store_path)
        try:
            history = list(reversed(store.list_runs(run.name, limit=30)))
        finally:
            store.close()
    except ScrapewatchError:
        pass  # the report still renders, without the history section's earlier runs
    summary = path.with_suffix(".json")
    run.report_path = str(path)
    gate = gate_line.removeprefix("live: ").split("\n")[0] if gate_line else None
    html = render_html(
        run, history=history, gate=gate, files={"report": str(path), "summary": str(summary)}
    )
    write_html(path, html)
    write_summary(run, summary)
    return [f"report: {path}", f"summary: {summary}"]


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
            files += _write_report(run, Path(report_path), history_path, gate_line)
    except ScrapewatchError as exc:
        _fail(str(exc))
    except OSError as exc:
        _fail(f"{type(exc).__name__}: {exc}")
    from scrapewatch.cli._output import check_summary

    click.echo(check_summary(run, gate_line=gate_line, files=files))
    return EXIT_BY_VERDICT[run.verdict.status]


def _saved_response(path: str, status: int | None) -> tuple[int, dict[str, str], bytes]:
    """A saved body, or a raw HTTP response (``curl -i`` output): (status, headers, body)."""
    raw = Path(path).read_bytes()
    headers: dict[str, str] = {}
    code = 200
    if raw.startswith(b"HTTP/"):
        head, sep, body = raw.partition(b"\r\n\r\n")
        if not sep:
            head, _, body = raw.partition(b"\n\n")
        lines = head.decode("latin-1").splitlines()
        parts = lines[0].split()
        if len(parts) > 1 and parts[1].isdigit():
            code = int(parts[1])
        for line in lines[1:]:
            name, colon, value = line.partition(":")
            if colon:
                headers[name.strip().lower()] = value.strip()
        raw = body
    elif Path(path).suffix.lower() in (".html", ".htm"):
        headers["content-type"] = "text/html"
    return (status if status is not None else code), headers, raw


def _store(history_path: str | None) -> Any:
    from scrapewatch.config import load_settings
    from scrapewatch.history.store import open_store

    path = Path(history_path) if history_path else load_settings().history.path
    if not (Path(path) / "history.sqlite").is_file():
        _fail(f"no history at {path} (pass --history DIR)")
    return open_store(path)


def _find(store: Any, run_id: str) -> Run:
    run: Run | None = store.find_run(run_id)
    if run is None:
        _fail(f"no run {run_id!r} in the history (use a full id or a unique prefix)")
    return run


@main.group()
def guard() -> None:
    """Classify saved responses and extract fixture features."""


@guard.command("classify")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--status", type=int, default=None, help="Status code (default: from the file or 200)."
)
@click.option("--url", default=None)
@click.option("--final-url", default=None, help="URL after redirects, when it differs.")
def guard_classify(path: str, status: int | None, url: str | None, final_url: str | None) -> None:
    """Classify a saved HTML/HTTP response file."""
    from scrapewatch.cli._output import guard_result
    from scrapewatch.guard.classify import classify

    code, headers, body = _saved_response(path, status)
    result = classify(status=code, headers=headers, body=body, url=url, final_url=final_url)
    click.echo(guard_result(result))


@guard.command("features")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--status", type=int, default=None, help="Status code (default: from the file or 200)."
)
@click.option("--url", default=None)
@click.option("--final-url", default=None, help="URL after redirects, when it differs.")
def guard_features(path: str, status: int | None, url: str | None, final_url: str | None) -> None:
    """Print the fixture-safe features of a saved response (for contributing rules)."""
    import yaml

    from scrapewatch.guard.classify import classify_features
    from scrapewatch.guard.features import extract_features, markers_for_fixture
    from scrapewatch.guard.rules import bundled_rules

    code, headers, body = _saved_response(path, status)
    rules = bundled_rules()
    features = extract_features(
        status=code,
        headers=headers,
        body=body,
        request_url=url,
        final_url=final_url,
        markers=rules.markers(),
    )
    result = classify_features(features, rules)
    safe = markers_for_fixture(features).model_dump(mode="json", exclude_none=True)
    expect: dict[str, Any] = {"label": result.label.value}
    if result.vendor:
        expect["vendor"] = result.vendor
    fixture = {"rule": result.rule_id or "<rule id>", "expect": expect, "features": safe}
    click.echo("# Fixture-safe features: no page body, markers and title cut to 200 characters.")
    click.echo(yaml.safe_dump(fixture, sort_keys=False, allow_unicode=True).rstrip())


@main.group()
def rules() -> None:
    """Validate and test rule files."""


@rules.command("test")
@click.argument("fixtures", required=False, type=click.Path(exists=True, file_okay=False))
@click.option(
    "--rules",
    "rules_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Guard rules file (default: the bundled rules).",
)
def rules_test(fixtures: str | None, rules_path: str | None) -> int:
    """Run every guard fixture against the rules (bundled or --rules).

    FIXTURES is the fixture directory (default: tests/guard/fixtures). Exit 1 when a fixture
    classifies differently from its ``expect`` or a rule has no fixture.
    """
    from scrapewatch import _yaml
    from scrapewatch.guard.classify import classify_features
    from scrapewatch.guard.features import Features
    from scrapewatch.guard.rules import bundled_rules, load_rules, rule_fixture_paths

    folder = Path(fixtures or "tests/guard/fixtures")
    if not folder.is_dir():
        _fail(f"no fixture directory {folder} (pass FIXTURES)")
    try:
        ruleset = load_rules(Path(rules_path)) if rules_path else bundled_rules()
    except ScrapewatchError as exc:
        _fail(str(exc))
    failures: list[str] = []
    files = sorted(folder.glob("*.yml"))
    for fixture in files:
        try:
            data = _yaml.safe_load(fixture.read_text(encoding="utf-8"))
            features = Features.model_validate(data["features"])
            expect = data["expect"]
        except Exception as exc:  # a broken fixture is a failure, not a crash
            failures.append(f"{fixture.name}: cannot be read ({type(exc).__name__}: {exc})")
            continue
        got = classify_features(features, ruleset)
        wrong = [
            f"{key} {getattr(got, attr)!r} != {expect[key]!r}"
            for key, attr in (("label", "label"), ("vendor", "vendor"), ("rule_id", "rule_id"))
            if key in expect
            and (got.label.value if key == "label" else getattr(got, attr)) != expect[key]
        ]
        if wrong:
            failures.append(f"{fixture.name}: {'; '.join(wrong)} (signals: {got.signals})")
    missing = [rid for rid, paths in rule_fixture_paths(ruleset, folder).items() if not paths]
    failures += [f"rule {rid} has no fixture" for rid in missing]
    for line in failures:
        click.echo(f"FAIL {line}")
    click.echo(
        f"{len(files)} fixtures, {len(ruleset.rules)} rules (rules_version "
        f"{ruleset.rules_version}): {'all passed' if not failures else f'{len(failures)} failed'}"
    )
    return 1 if failures else 0


@rules.command("validate")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
def rules_validate(path: str) -> int:
    """Validate a guard-rules or data-rules file."""
    from scrapewatch import _yaml
    from scrapewatch.checks.rules import load_data_rules
    from scrapewatch.guard.rules import validate_rules_file

    try:
        raw = _yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        click.echo(f"invalid: {path} cannot be read as YAML: {exc}")
        return 1
    if isinstance(raw, dict) and isinstance(raw.get("rules"), list):
        problems = validate_rules_file(Path(path))
        for problem in problems:
            click.echo(f"invalid: {problem}")
        if not problems:
            click.echo(f"ok: guard rules, {len(raw['rules'])} rules")
        return 1 if problems else 0
    try:
        data_rules = load_data_rules(path)
    except ScrapewatchError as exc:
        click.echo(f"invalid: {exc}")
        return 1
    click.echo(
        f"ok: data rules {data_rules.name!r}, {len(data_rules.fields)} fields, "
        f"{len(data_rules.cross)} cross rules"
    )
    return 0


@main.command()
@click.argument("run_id")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
@click.option("--out", default=None, type=click.Path(dir_okay=False))
def report(run_id: str, history_path: str | None, out: str | None) -> None:
    """Re-render the HTML report of a past run."""
    from scrapewatch.models import BAD_LABELS
    from scrapewatch.report import render_html, report_paths, write_html

    store = _store(history_path)
    try:
        run = _find(store, run_id)
        bad = [e for e in store.events(run.run_id) if e.guard.label in BAD_LABELS][:20]
        history = list(reversed(store.list_runs(run.name, limit=30)))
        history = [r for r in history if r.started_at <= run.started_at]
        if out is None:
            from scrapewatch.config import load_settings

            report_dir = load_settings().report_dir or store.path / "reports"
        target = Path(out) if out else report_paths(report_dir, run)[0]
        html = render_html(run, history=history, bad_events=bad, files={"report": str(target)})
        write_html(target, html)
    finally:
        store.close()
    click.echo(f"report: {target}")


@main.group()
def history() -> None:
    """Inspect and prune the history store."""


@history.command("list")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
@click.option("--name", default=None)
@click.option("--limit", default=20, type=click.IntRange(1, 10000))
def history_list(history_path: str | None, name: str | None, limit: int) -> None:
    """List recent runs."""
    from scrapewatch.cli._output import runs_table

    store = _store(history_path)
    try:
        runs = store.list_runs(name, limit=limit)
    finally:
        store.close()
    click.echo(runs_table(runs))


@history.command("show")
@click.argument("run_id")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
def history_show(run_id: str, history_path: str | None) -> None:
    """Print one run's summary.json."""
    store = _store(history_path)
    try:
        run = _find(store, run_id)
    finally:
        store.close()
    click.echo(json.dumps(run.model_dump(mode="json"), indent=2, ensure_ascii=False))


@history.command("prune")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
def history_prune(history_path: str | None) -> None:
    """Apply retention."""
    from scrapewatch.config import load_settings

    h = load_settings().history
    store = _store(history_path)
    try:
        deleted = store.prune(keep_runs=h.keep_runs, keep_event_days=h.keep_event_days)
    finally:
        store.close()
    parts = ", ".join(f"{table} {n}" for table, n in deleted.items()) or "nothing"
    click.echo(
        f"pruned (keep {h.keep_runs} runs, events for {h.keep_event_days} days): deleted {parts}"
    )


@main.command()
@click.argument("run_id")
@click.option("--history", "history_path", default=None, type=click.Path(file_okay=False))
def cost(run_id: str, history_path: str | None) -> None:
    """Print the cost and waste breakdown of a run."""
    from scrapewatch.cli._output import cost_text

    store = _store(history_path)
    try:
        run = _find(store, run_id)
    finally:
        store.close()
    click.echo(cost_text(run))


@main.group()
def alert() -> None:
    """Alert channels."""


@alert.command("test")
@click.option("--channel", default=None)
def alert_test(channel: str | None) -> int:
    """Send a test alert to configured channels (or only --channel). Exit 1 if any failed."""
    import os
    from datetime import datetime, timezone

    from scrapewatch.alerts.base import CHANNEL_ORDER, AlertPayload, deliver

    payload = AlertPayload(
        run_id="test",
        name="scrapewatch-test",
        verdict="warn",
        reasons=["This is a test alert from `scrapewatch alert test`; nothing is wrong."],
        started_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    results = deliver(payload, channels_wanted=[channel] if channel else None, env=os.environ)
    if not results:
        click.echo(
            "no alert channel is configured; set the environment variables of one of: "
            + ", ".join(CHANNEL_ORDER)
            + " (docs/architecture.md 13.4)"
        )
        return 1
    for name, outcome in results.items():
        click.echo(f"{name}: {outcome}")
    return 0 if all(v == "ok" for v in results.values()) else 1


@main.command()
@click.option("--dir", "target", default=".", type=click.Path(file_okay=False))
@click.option("--force", is_flag=True, help="Overwrite existing files.")
def init(target: str, force: bool) -> None:
    """Write an example rules.yml and scrapewatch.yml."""
    from scrapewatch.cli._init_files import CONFIG_YML, RULES_YML

    folder = Path(target)
    files = {folder / "rules.yml": RULES_YML, folder / "scrapewatch.yml": CONFIG_YML}
    existing = [str(p) for p in files if p.exists()]
    if existing and not force:
        _fail(f"{', '.join(existing)} already exist; pass --force to overwrite")
    folder.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
        click.echo(f"wrote {path}")
    click.echo("next: scrapewatch rules validate rules.yml, then wrap your scraper in watch()")


if __name__ == "__main__":  # pragma: no cover
    main()
