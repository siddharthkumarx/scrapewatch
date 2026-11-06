import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import pytest

import scrapewatch.gate as gate_mod
from scrapewatch.errors import GateError
from scrapewatch.gate import LastGood, PublishGate, sha256_file
from scrapewatch.models import Verdict, VerdictStatus

PASS = Verdict(status=VerdictStatus.PASS)
WARN = Verdict(status=VerdictStatus.WARN, reasons=["w"])
HOLD = Verdict(status=VerdictStatus.HOLD, reasons=["h"])


def _stage(gate: PublishGate, run_id: str, text: str) -> None:
    with gate.stage(run_id) as path:
        path.write_text(text, encoding="utf-8")


def _gate(tmp_path: Path, **kw: object) -> PublishGate:
    return PublishGate(tmp_path / "data" / "books.jsonl", **kw)  # type: ignore[arg-type]


def test_promote_on_pass_replaces_live_and_writes_pointer(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    _stage(gate, "run1", "a\n")
    assert gate.promote("run1", PASS) is True
    assert gate.output.read_text("utf-8") == "a\n"
    pointer = gate.last_good()
    assert isinstance(pointer, LastGood)
    assert pointer.run_id == "run1"
    assert pointer.sha256 == sha256_file(gate.output)
    assert pointer.size == 2
    assert pointer.verdict == VerdictStatus.PASS
    assert Path(pointer.path).read_text("utf-8") == "a\n"  # copy in good/
    assert gate.verify_live()
    assert not gate.journal_path.exists()
    assert not gate.lock_path.exists()
    assert not (gate.state_dir / "staging" / "run1").exists()


def test_hold_leaves_live_untouched(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    _stage(gate, "run1", "good\n")
    gate.promote("run1", PASS)
    _stage(gate, "run2", "bad\n")
    assert gate.promote("run2", HOLD) is False
    assert gate.output.read_text("utf-8") == "good\n"
    held = gate.state_dir / "held" / "run2"
    assert (held / "books.jsonl").read_text("utf-8") == "bad\n"
    assert json.loads((held / "verdict.json").read_text("utf-8"))["status"] == "hold"
    last = gate.last_good()
    assert last is not None
    assert last.run_id == "run1"
    assert not gate.lock_path.exists()


def test_warn_honours_promote_on_warn(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    _stage(gate, "r1", "x\n")
    assert gate.promote("r1", WARN) is True
    strict = _gate(tmp_path, promote_on_warn=False)
    _stage(strict, "r2", "y\n")
    assert strict.promote("r2", WARN) is False
    assert strict.output.read_text("utf-8") == "x\n"


def test_missing_verdict_holds(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    _stage(gate, "r1", "x\n")
    assert gate.promote("r1", None) is False
    assert not gate.output.exists()
    assert (gate.state_dir / "held" / "r1" / "books.jsonl").exists()


def test_nothing_staged_and_hold_without_staging(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    with pytest.raises(GateError, match="nothing staged"):
        gate.promote("ghost", PASS)
    assert not gate.lock_path.exists()
    held = gate.hold("ghost", HOLD)
    assert held.parent.is_dir()


def test_exception_while_staging_keeps_file_and_releases_lock(tmp_path: Path) -> None:
    gate = _gate(tmp_path)

    def scraper() -> None:
        with gate.stage("r1") as path:
            path.write_text("partial", encoding="utf-8")
            raise RuntimeError("scraper died")

    with pytest.raises(RuntimeError, match="scraper died"):
        scraper()
    assert gate.staging_path("r1").read_text("utf-8") == "partial"
    assert not gate.lock_path.exists()
    assert not gate.output.exists()


def _replace_failing_on(call: int, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    real = os.replace
    calls: list[int] = []

    def fake(src: object, dst: object) -> None:
        calls.append(1)
        if len(calls) == call:
            raise OSError("simulated crash")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", fake)
    return calls


def test_crash_during_replace_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = _gate(tmp_path)
    _stage(gate, "old", "old\n")
    gate.promote("old", PASS)
    _stage(gate, "new", "new\n")
    _replace_failing_on(1, monkeypatch)  # the live replace fails after the journal "copy" step
    with pytest.raises(OSError, match="simulated"):
        gate.promote("new", PASS)
    monkeypatch.undo()
    assert gate.journal_path.exists()
    journal = json.loads(gate.journal_path.read_text("utf-8"))
    assert journal["step"] == "replace"
    actions = gate.recover()
    assert actions == ["rolled back the interrupted promotion of run new; live file unchanged"]
    assert gate.output.read_text("utf-8") == "old\n"
    assert not gate.journal_path.exists()
    assert not (gate.state_dir / "good" / "new").exists()
    assert gate.staging_path("new").exists()  # kept for inspection
    last = gate.last_good()
    assert last is not None
    assert last.run_id == "old"
    assert gate.verify_live()


def test_crash_after_replace_completes_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = _gate(tmp_path)
    _stage(gate, "old", "old\n")
    gate.promote("old", PASS)
    _stage(gate, "new", "new\n")
    _replace_failing_on(2, monkeypatch)  # the live replace works, the pointer replace fails
    with pytest.raises(OSError, match="simulated"):
        gate.promote("new", WARN)
    monkeypatch.undo()
    assert gate.output.read_text("utf-8") == "new\n"
    stale = gate.last_good()
    assert stale is not None
    assert stale.run_id == "old"
    assert not gate.verify_live()  # live and pointer disagree until recovery
    actions = gate.recover()
    assert actions == ["completed the promotion of run new"]
    fixed = gate.last_good()
    assert fixed is not None
    assert fixed.run_id == "new"
    assert fixed.verdict == VerdictStatus.WARN
    assert gate.verify_live()
    assert gate.recovered == actions


def test_recover_runs_when_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _gate(tmp_path)
    _stage(gate, "a", "a\n")
    _replace_failing_on(1, monkeypatch)
    with pytest.raises(OSError, match="simulated"):
        gate.promote("a", PASS)
    monkeypatch.undo()
    _stage(gate, "b", "b\n")  # stage() recovers first
    assert gate.recovered[0].startswith("rolled back")
    assert gate.promote("b", PASS)
    assert gate.output.read_text("utf-8") == "b\n"


def test_unreadable_journal(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    gate.state_dir.mkdir(parents=True)
    gate.journal_path.write_text("{torn", encoding="utf-8")
    assert gate.recover() == ["removed an unreadable journal; live file left as it was"]
    assert gate.recover() == []


def test_corrupt_pointer_fails_closed(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    _stage(gate, "r1", "x\n")
    gate.promote("r1", PASS)
    gate.pointer_path.write_text("not json", encoding="utf-8")
    with pytest.raises(GateError, match="corrupt"):
        gate.last_good()
    _stage(gate, "r2", "y\n")
    with pytest.raises(GateError):
        gate.promote("r2", PASS)
    assert gate.output.read_text("utf-8") == "x\n"
    assert not gate.lock_path.exists()


def test_verify_live_detects_hand_edits(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    assert gate.verify_live() is False  # nothing promoted
    _stage(gate, "r1", "x\n")
    gate.promote("r1", PASS)
    gate.output.write_text("edited\n", encoding="utf-8")
    assert gate.verify_live() is False


def test_lock_held_by_a_live_process(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    gate.state_dir.mkdir(parents=True)
    gate.lock_path.write_text(
        json.dumps({"pid": os.getppid(), "host": platform.node(), "time": time.time()}),
        encoding="utf-8",
    )
    with pytest.raises(GateError, match="locked by pid"), gate.stage("r1"):
        pass
    other = _gate(tmp_path)
    with pytest.raises(GateError):
        other.acquire()


def test_stale_lock_with_dead_pid_is_taken_over(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    dead_pid = int(proc.stdout)
    gate = _gate(tmp_path)
    gate.state_dir.mkdir(parents=True)
    gate.lock_path.write_text(
        json.dumps({"pid": dead_pid, "host": platform.node(), "time": time.time()}),
        encoding="utf-8",
    )
    _stage(gate, "r1", "x\n")
    assert gate.promote("r1", PASS)


def test_old_and_unreadable_locks_are_stale(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    gate.state_dir.mkdir(parents=True)
    gate.lock_path.write_text(
        json.dumps({"pid": 1, "host": "elsewhere", "time": time.time() - 7 * 3600}),
        encoding="utf-8",
    )
    gate.acquire()
    gate.acquire()  # re-entrant for the owner
    gate.release()
    gate.lock_path.write_text("garbage", encoding="utf-8")
    old = time.time() - 7 * 3600
    os.utime(gate.lock_path, (old, old))
    gate.acquire()
    gate.release()
    gate.lock_path.write_text(json.dumps({"pid": 1, "host": "elsewhere", "time": "x"}), "utf-8")
    gate.acquire()  # unparsable time counts as stale
    gate.release()


def test_cross_filesystem_staging_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate_mod, "_same_filesystem", lambda a, b: False)
    gate = _gate(tmp_path)
    with pytest.raises(GateError, match="different filesystems"), gate.stage("r1"):
        pass
    assert not gate.lock_path.exists()


def test_keep_good_pruning(tmp_path: Path) -> None:
    gate = _gate(tmp_path, keep_good=2)
    for i in range(4):
        _stage(gate, f"r{i}", f"{i}\n")
        gate.promote(f"r{i}", PASS)
        time.sleep(0.01)
        _stage(gate, f"h{i}", "bad\n")
        gate.promote(f"h{i}", HOLD)
        time.sleep(0.01)
    good = sorted(p.name for p in (gate.state_dir / "good").iterdir())
    held = sorted(p.name for p in (gate.state_dir / "held").iterdir())
    assert good == ["r2", "r3"]
    assert held == ["h2", "h3"]
    assert gate.output.read_text("utf-8") == "3\n"


def test_no_fcntl_import() -> None:
    assert "fcntl" not in Path(gate_mod.__file__).read_text("utf-8")


def test_pid_alive() -> None:
    assert gate_mod._pid_alive(os.getpid())
    assert not gate_mod._pid_alive(0)
