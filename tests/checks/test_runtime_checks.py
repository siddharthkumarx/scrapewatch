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
