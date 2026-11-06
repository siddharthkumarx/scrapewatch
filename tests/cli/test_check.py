"""``scrapewatch check`` and ``check()`` (build item 12)."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from scrapewatch import check
from scrapewatch.cli.main import main
from scrapewatch.history.store import open_store
from scrapewatch.models import Run, VerdictStatus

CLI = Path(__file__).resolve().parents[1] / "fixtures" / "cli"
RULES = CLI / "rules.yml"


def _rows() -> list[dict[str, Any]]:
    lines = (CLI / "books50.jsonl").read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _comparable(run: Run) -> dict[str, Any]:
    data = run.model_dump(mode="json")
    for key in ("run_id", "started_at", "finished_at"):
        data.pop(key)
    for f in data["findings"]:
        f.pop("finding_id")
        f.pop("run_id")
    return data


def _run(args: list[str]) -> Any:
    return CliRunner().invoke(main, args, catch_exceptions=False)


@pytest.mark.extras  # the Parquet leg runs where pyarrow is installed (full.yml extras job)
def test_csv_jsonl_parquet_give_identical_runs(tmp_path: Path) -> None:
    rows = _rows()
    paths = [_write_jsonl(tmp_path / "b.jsonl", rows), _write_csv(tmp_path / "b.csv", rows)]
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        pass
    else:
        pq.write_table(pa.Table.from_pylist(rows), tmp_path / "b.parquet")
        paths.append(tmp_path / "b.parquet")
    runs = [check(p, rules=RULES, history=tmp_path / f"h{i}") for i, p in enumerate(paths)]
    first = _comparable(runs[0])
    for other in runs[1:]:
        assert _comparable(other) == first
    assert runs[0].counts.records == 50
    assert runs[0].verdict.status == VerdictStatus.PASS


def test_csv_strings_are_typed_by_normalizers(tmp_path: Path) -> None:
    run = check(_write_csv(tmp_path / "b.csv", _rows()), rules=RULES, history=tmp_path / "h")
    c = run.counts
    assert c.fixes_by_normalizer == {
        "number": 50,
        "price": 75,
        "whitespace": 50,
    }  # 25 currency fills
    assert c.usable_records == 50
    assert c.findings_by_severity == {}
    assert run.name == "books"
    assert run.integration == "cli"
    assert run.status.value == "finished"


def test_run_is_stored_in_history(tmp_path: Path) -> None:
    src = _write_jsonl(tmp_path / "b.jsonl", _rows())
    for _ in range(4):
        run = check(src, rules=RULES, history=tmp_path / "h")
    assert len(run.verdict.baseline_runs) == 3  # the fourth run compares with three passes
    store = open_store(tmp_path / "h")
    try:
        assert store.list_runs("books")[0].run_id == run.run_id
        assert store.field_stats([run.run_id])["price"][run.run_id].count == 50
    finally:
        store.close()


def test_name_falls_back_to_file_stem(tmp_path: Path) -> None:
    rules = tmp_path / "r.yml"
    rules.write_text("fields: {title: {}}\n", encoding="utf-8")
    run = check(_write_jsonl(tmp_path / "shop.jsonl", [{"title": "x"}]), rules=rules)
    assert run.name == "shop"
    assert (tmp_path / ".scrapewatch" / "history.sqlite").is_file()  # default history path
    assert check(tmp_path / "shop.jsonl", rules=rules, name="other").name == "other"


@pytest.mark.parametrize(
    ("change", "code", "status"),
    [
        (None, 0, "PASS"),
        ("duplicate", 1, "WARN"),
        ("no_price", 2, "HOLD"),
    ],
)
def test_exit_codes(tmp_path: Path, change: str | None, code: int, status: str) -> None:
    rows = _rows()
    if change == "duplicate":
        rows.append(dict(rows[0]))
    elif change == "no_price":
        rows[3]["price"] = None
    src = _write_jsonl(tmp_path / "b.jsonl", rows)
    result = _run(["check", str(src), "--rules", str(RULES), "--history", str(tmp_path / "h")])
    assert result.exit_code == code, result.output
    assert result.output.startswith(f"scrapewatch · books · {status}")


def test_exit_3_on_tool_errors(tmp_path: Path) -> None:
    src = _write_jsonl(tmp_path / "b.jsonl", _rows())
    bad_rules = tmp_path / "bad.yml"
    bad_rules.write_text("fields: {price: {clean: [nope]}}\n", encoding="utf-8")
    result = _run(["check", str(src), "--rules", str(bad_rules)])
    assert result.exit_code == 3
    assert "unknown normalizer" in result.output
    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"title": "a"}\n{oops\n', encoding="utf-8")
    result = _run(["check", str(broken), "--rules", str(RULES)])
    assert result.exit_code == 3
    assert "broken.jsonl:2" in result.output
    odd = tmp_path / "data.txt"
    odd.write_text("x", encoding="utf-8")
    assert _run(["check", str(odd), "--rules", str(RULES)]).exit_code == 3
    assert _run(["check", str(src)]).exit_code == 3  # missing --rules: click usage error
    assert _run(["check", str(src), "--rules", str(RULES), "--format", "xml"]).exit_code == 3


def test_unusable_history_holds(tmp_path: Path) -> None:
    src = _write_jsonl(tmp_path / "b.jsonl", _rows())
    blocker = tmp_path / "h"
    blocker.write_text("not a directory", encoding="utf-8")
    run = check(src, rules=RULES, history=blocker)
    assert run.verdict.status == VerdictStatus.HOLD
    assert any("is not usable" in r for r in run.verdict.reasons)


def test_unreadable_baseline_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scrapewatch.checks.engine as engine

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("baseline exploded")

    monkeypatch.setattr(engine, "compute_baseline", broken)
    run = check(_write_jsonl(tmp_path / "b.jsonl", _rows()), rules=RULES, history=tmp_path / "h")
    assert run.verdict.status == VerdictStatus.HOLD
    assert any("baseline exploded" in r for r in run.verdict.reasons)


def test_json_and_report_outputs(tmp_path: Path) -> None:
    src = _write_jsonl(tmp_path / "b.jsonl", _rows())
    out = tmp_path / "out" / "summary.json"
    report = tmp_path / "rep" / "report.html"
    result = _run(
        ["check", str(src), "--rules", str(RULES), "--json", str(out), "--report", str(report)]
    )
    assert result.exit_code == 0, result.output
    run = Run.model_validate_json(out.read_text("utf-8"))
    assert run.counts.records == 50
    assert Run.model_validate_json(report.with_suffix(".json").read_text("utf-8")) == run
    assert f"summary: {out}" in result.output


def test_promote_to_and_hold_keeps_previous_live(tmp_path: Path) -> None:
    live = tmp_path / "live" / "books.jsonl"
    live.parent.mkdir()
    good = _write_jsonl(tmp_path / "good.jsonl", _rows())
    args = ["--rules", str(RULES), "--history", str(tmp_path / "h"), "--promote-to", str(live)]
    first = _run(["check", str(good), *args])
    assert first.exit_code == 0, first.output
    assert "live: promoted" in first.output
    assert live.read_bytes() == good.read_bytes()

    rows = _rows()
    rows[0]["price"] = None  # a hold
    bad = _write_jsonl(tmp_path / "bad.jsonl", rows)
    second = _run(["check", str(bad), *args])
    assert second.exit_code == 2
    assert "live: held; last good run" in second.output
    assert live.read_bytes() == good.read_bytes()
    held = list((live.parent / "books.jsonl.scrapewatch" / "held").iterdir())
    assert len(held) == 1

    warn_rows = [*_rows(), _rows()[0]]  # a duplicate: warn
    warn = _write_jsonl(tmp_path / "warn.jsonl", warn_rows)
    third = _run(["check", str(warn), *args, "--hold-on-warn"])
    assert third.exit_code == 1
    assert live.read_bytes() == good.read_bytes()
    fourth = _run(["check", str(warn), *args])
    assert "live: promoted" in fourth.output
    assert live.read_bytes() == warn.read_bytes()


def test_promote_to_first_hold_and_hand_edit_warning(tmp_path: Path) -> None:
    live = tmp_path / "books.jsonl"
    rows = _rows()
    rows[0]["price"] = None
    bad = _write_jsonl(tmp_path / "bad.jsonl", rows)
    args = ["--rules", str(RULES), "--promote-to", str(live)]
    result = _run(["check", str(bad), *args])
    assert "nothing was promoted" in result.output
    good = _write_jsonl(tmp_path / "good.jsonl", _rows())
    assert _run(["check", str(good), *args]).exit_code == 0
    live.write_text("edited\n", encoding="utf-8")
    again = _run(["check", str(good), *args])
    assert "differs from the last promoted file" in again.output


def test_gate_recovery_becomes_a_finding(tmp_path: Path) -> None:
    live = tmp_path / "books.jsonl"
    state = tmp_path / "books.jsonl.scrapewatch"
    state.mkdir()
    journal = {"run_id": "r0", "step": "copy", "sha256": "0" * 64, "size": 1}
    (state / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
    good = _write_jsonl(tmp_path / "good.jsonl", _rows())
    out = tmp_path / "s.json"
    result = _run(
        ["check", str(good), "--rules", str(RULES), "--promote-to", str(live), "--json", str(out)]
    )
    assert result.exit_code == 0, result.output
    run = Run.model_validate_json(out.read_text("utf-8"))
    assert [f.check for f in run.findings if f.check.startswith("gate.")] == ["gate.recovered"]


def test_version() -> None:
    result = _run(["--version"])
    assert result.exit_code == 0
    assert "scrapewatch, version" in result.output


@pytest.mark.skipif(sys.platform != "linux", reason="ru_maxrss in KiB is Linux behaviour")
def test_100k_rows_stream_under_200_mb(tmp_path: Path) -> None:
    src = tmp_path / "big.jsonl"
    with src.open("w", encoding="utf-8") as fh:
        for i in range(100_000):
            fh.write(
                json.dumps(
                    {
                        "title": f" Book {i} ",
                        "price": f"£{i % 900}.{i % 100:02d}",
                        "stock": str(i % 7),
                        "url": f"https://books.toscrape.com/catalogue/b_{i}/index.html",
                    }
                )
                + "\n"
            )
    code = (
        "import resource, sys\n"
        "from scrapewatch import check\n"
        "run = check(sys.argv[1], rules=sys.argv[2], history=sys.argv[3])\n"
        "print(run.counts.records, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)\n"
    )
    out = subprocess.run(  # a fresh process, so the peak is the check alone
        [sys.executable, "-c", code, str(src), str(RULES), str(tmp_path / "h")],
        check=True,
        capture_output=True,
        text=True,
    )
    records, max_rss_kib = (int(x) for x in out.stdout.split())
    assert records == 100_000
    assert max_rss_kib < 200 * 1024, f"peak RSS {max_rss_kib / 1024:.0f} MiB"


def test_prune_runs_every_n_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scrapewatch.checks.engine as engine

    calls: list[int] = []
    monkeypatch.setattr(engine, "PRUNE_EVERY_RUNS", 1)
    from scrapewatch.history.store import HistoryStore

    real = HistoryStore.prune

    def spy(self: Any, **kw: Any) -> Any:
        calls.append(1)
        return real(self, **kw)

    monkeypatch.setattr(HistoryStore, "prune", spy)
    check(_write_jsonl(tmp_path / "b.jsonl", _rows()), rules=RULES, history=tmp_path / "h")
    assert calls == [1]


def test_summary_lists_many_kinds_briefly(tmp_path: Path) -> None:
    clean = CLI.parent / "clean"
    result = _run(
        ["check", str(clean / "records_messy.jsonl"), "--rules", str(clean / "rules_clean.yml")]
    )
    assert result.exit_code == 1
    assert "more kinds in the summary" in result.output
    assert "fixes: date 6" in result.output


def test_os_errors_exit_3(tmp_path: Path) -> None:
    src = _write_jsonl(tmp_path / "b.jsonl", _rows())
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")
    result = _run(["check", str(src), "--rules", str(RULES), "--json", str(blocked / "s.json")])
    assert result.exit_code == 3
    assert result.output.startswith("error: ")
