from pathlib import Path

from scrapewatch.config import Settings
from scrapewatch.history.store import HistoryStore
from scrapewatch.models import GuardLabel
from scrapewatch.runtime import Watch, watch


def test_watch_classifies_and_records_guard_findings(tmp_path: Path) -> None:
    with watch(settings=Settings(history={"path": tmp_path})) as w:
        limited = w.record_response("https://a.example/x", status=429, body=b"slow down")
        ok = w.record_response(
            "https://a.example/y",
            status=200,
            headers={"content-type": "text/html"},
            body=b"<p>" + b"words " * 500 + b"</p>",
        )
        failed = w.record_response("https://a.example/z", status=None, error=OSError("reset"))
    assert limited is not None
    assert ok is not None
    assert failed is not None
    assert limited.guard.label == GuardLabel.RATE_LIMIT
    assert limited.guard.rule_id == "generic.rate_limit.429"
    assert ok.guard.label == GuardLabel.OK
    assert failed.guard.label == GuardLabel.ERROR
    assert w.run.rules_version == "2026.11.0"
    assert w.run.counts.by_label == {"rate_limit": 1, "ok": 1, "error": 1}
    assert w.run.counts.findings_by_severity == {"info": 2}
    with HistoryStore(tmp_path) as store:
        checks = sorted(f.check for f in store.findings(w.run.run_id))
    assert checks == ["guard.error", "guard.rate_limit"]


def test_guard_disabled(tmp_path: Path) -> None:
    w = Watch(Settings(guard={"enabled": False}, history={"path": tmp_path}))
    w.start()
    ev = w.record_response("https://a.example/", status=429)
    assert ev is not None
    assert ev.guard.label == GuardLabel.OK
    assert w.run.rules_version is None
    w.finish()


def test_custom_rules_and_bad_rules(tmp_path: Path) -> None:
    rules = tmp_path / "r.yml"
    rules.write_text(
        'schema_version: 1\nrules_version: "local.1"\nrules:\n'
        "  - {id: my.block, label: block, confidence: 0.9, any: [{all: [{status: [418]}]}]}\n",
        encoding="utf-8",
    )
    w = Watch(Settings(guard={"rules_path": rules}, history={"path": tmp_path}))
    w.start()
    ev = w.record_response("https://a.example/", status=418)
    assert ev is not None
    assert ev.guard.rule_id == "my.block"
    assert w.run.rules_version == "local.1"
    w.finish()
    rules.write_text("rules: [", encoding="utf-8")
    bad = Watch(Settings(guard={"rules_path": rules}, history={"path": tmp_path}))
    bad.start()
    assert bad.guard_rules is None
    assert bad.run.counts.monitor_errors == 1
    ev2 = bad.record_response("https://a.example/", status=418)
    assert ev2 is not None
    assert ev2.guard.label == GuardLabel.OK
    bad.finish()
