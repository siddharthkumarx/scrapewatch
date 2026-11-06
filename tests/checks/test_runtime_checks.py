from pathlib import Path

from scrapewatch.checks.rules import DataRules
from scrapewatch.config import Settings
from scrapewatch.runtime import Watch, watch

CHECKS = Path(__file__).parents[1] / "fixtures" / "checks"


def test_watch_checks_records_with_a_rules_file(tmp_path: Path) -> None:
    settings = Settings(history={"path": tmp_path})
    with watch("r", settings=settings, rules=CHECKS / "rules_records.yml") as w:
        good = w.record_record({"title": "Fine", "price": 2, "url": "https://a.example/1"})
        dup = w.record_record({"title": "Fine", "price": 2, "url": "https://a.example/1"})
        bad = w.record_record({"title": "Pricey", "price": 5000, "url": "https://a.example/2"})
    assert good is not None
    assert dup is not None
    assert bad is not None
    assert good.usable
    assert good.dedupe_key is not None
    assert not dup.usable
    assert not bad.usable
    c = w.run.counts
    assert (c.records, c.usable_records, c.duplicates) == (3, 1, 1)
    assert c.findings_by_severity == {"info": 1, "hold": 1}
    assert not w.checks_failed


def test_watch_accepts_a_rules_object(tmp_path: Path) -> None:
    rules = DataRules.model_validate({"fields": {"a": {"required": True}}})
    with watch(settings=Settings(history={"path": tmp_path}), rules=rules) as w:
        rec = w.record_record({"b": 1})
    assert rec is not None
    assert [f.check for f in rec.findings] == ["field.required"]


def test_unloadable_rules_mark_checks_failed(tmp_path: Path) -> None:
    bad = tmp_path / "rules.yml"
    bad.write_text("fields: [", encoding="utf-8")
    w = Watch(Settings(rules=bad, history={"path": tmp_path}))
    w.start()
    assert w.checks_failed
    assert w.checker is None
    rec = w.record_record({"a": 1})
    assert rec is not None
    w.finish()
    assert w.run.counts.monitor_errors == 1


def test_crashing_check_marks_checks_failed(tmp_path: Path) -> None:
    w = Watch(Settings(history={"path": tmp_path}), rules=DataRules())
    w.start()
    assert w.checker is not None

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("engine bug")

    w.checker.check = boom  # type: ignore[method-assign,assignment]
    rec = w.record_record({"a": 1})
    assert rec is not None
    assert w.checks_failed
    assert w.run.counts.monitor_errors == 1
    w.finish()


def _scrape(w: Watch, n: int, price_factor: float = 1.0) -> None:
    for i in range(n):
        w.record_record(
            {
                "title": f"Book {i}",
                "price": round((10 + 20 * (i + 0.5) / n) * price_factor, 2),
                "currency": "GBP",
                "category": ["fiction", "poetry", "travel"][i % 3],
                "url": f"https://books.toscrape.com/catalogue/book_{i}/index.html",
            }
        )


def test_history_baseline_end_to_end(tmp_path: Path) -> None:
    settings = Settings(name="books", history={"path": tmp_path})
    rules = CHECKS / "rules_batch.yml"
    verdicts = []
    for _ in range(3):
        with watch(settings=settings, rules=rules) as w:
            _scrape(w, 100)
        verdicts.append(w.verdict.status.value)
        assert any(f.check == "batch.baseline" for f in w.run.findings)
    assert verdicts == ["pass", "pass", "pass"]
    with watch(settings=settings, rules=rules) as w:
        _scrape(w, 40)
    assert w.verdict.status.value == "hold"
    assert len(w.verdict.baseline_runs) == 3
    assert w.verdict.reasons[0] == "Row count 40 is 60% below baseline 100."
    with watch(settings=settings, rules=rules) as w:
        _scrape(w, 100, price_factor=100)
    assert w.verdict.status.value == "hold"
    assert any("about 100x" in r for r in w.verdict.reasons)


def test_unreadable_history_holds_when_rules_are_used(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    settings = Settings(history={"path": blocker / "h"})
    with watch(settings=settings, rules=CHECKS / "rules_batch.yml") as w:
        _scrape(w, 10)
    assert w.verdict.status.value == "hold"
    with watch(settings=settings) as w2:
        w2.record_response("https://a.example/", status=200, body=b"<p>" + b"x" * 3000)
    assert w2.verdict.status.value == "warn"  # monitor error only; no batch checks configured


def test_responses_only_run_passes(tmp_path: Path) -> None:
    with watch(settings=Settings(history={"path": tmp_path})) as w:
        w.record_response("https://a.example/", status=200, body=b"<p>" + b"x" * 3000)
    assert w.verdict.status.value == "pass"


def test_baseline_failure_fails_closed(tmp_path: Path) -> None:
    w = Watch(Settings(history={"path": tmp_path}), rules=DataRules())
    w.start()
    assert w.store is not None

    def broken(*_a: object, **_k: object) -> list[object]:
        raise RuntimeError("db locked")

    w.store.baseline_runs = broken  # type: ignore[method-assign,assignment]
    run = w.finish()
    assert run.verdict.status.value == "hold"
    assert "db locked" in run.verdict.reasons[0]
