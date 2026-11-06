import os
from pathlib import Path

import pytest

from scrapewatch.errors import GateError
from scrapewatch.gate import DirectoryPublishGate, sha256_tree
from scrapewatch.models import Verdict, VerdictStatus

PASS = Verdict(status=VerdictStatus.PASS)
HOLD = Verdict(status=VerdictStatus.HOLD)


def _gate(tmp_path: Path, **kw: object) -> DirectoryPublishGate:
    return DirectoryPublishGate(tmp_path / "out" / "site", **kw)  # type: ignore[arg-type]


def _stage(gate: DirectoryPublishGate, run_id: str, files: dict[str, str]) -> None:
    with gate.stage(run_id) as d:
        for name, text in files.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(text, encoding="utf-8")


def _tree(path: Path) -> dict[str, str]:
    return {
        p.relative_to(path).as_posix(): p.read_text("utf-8")
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


OLD = {"a.jsonl": "old\n", "sub/b.jsonl": "old b\n"}
NEW = {"a.jsonl": "new\n", "c.jsonl": "c\n"}


def _with_old(tmp_path: Path) -> DirectoryPublishGate:
    gate = _gate(tmp_path)
    _stage(gate, "old", OLD)
    assert gate.promote("old", PASS)
    _stage(gate, "new", NEW)
    return gate


def test_promote_and_hold(tmp_path: Path) -> None:
    gate = _with_old(tmp_path)
    assert _tree(gate.output) == OLD
    assert gate.verify_live()
    assert gate.promote("new", PASS)
    assert _tree(gate.output) == NEW
    assert not gate.prev_path.exists()
    pointer = gate.last_good()
    assert pointer is not None
    assert pointer.sha256 == sha256_tree(gate.output)[0]
    assert pointer.size == len("new\n") + len("c\n")
    _stage(gate, "bad", {"a.jsonl": "bad\n"})
    assert gate.promote("bad", HOLD) is False
    assert _tree(gate.output) == NEW
    assert _tree(gate.state_dir / "held" / "bad" / "site") == {"a.jsonl": "bad\n"}
    assert gate.promote("x", None) is False
    with pytest.raises(GateError, match="nothing staged"):
        gate.promote("ghost", PASS)
    gate.output.joinpath("a.jsonl").write_text("edited\n", encoding="utf-8")
    assert not gate.verify_live()


def _fail_replace(call: int, monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.replace
    calls: list[int] = []

    def fake(src: object, dst: object) -> None:
        calls.append(1)
        if len(calls) == call:
            raise OSError("simulated crash")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", fake)


@pytest.mark.parametrize(
    ("failing_call", "expected_live", "first_action"),
    [
        (1, OLD, "rolled back"),  # rename(live, prev) fails: live untouched
        (2, OLD, "restored the previous live directory"),  # rename(staging, live) fails
        (3, NEW, "completed the promotion"),  # the pointer replace fails after the swap
    ],
)
def test_crash_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_call: int,
    expected_live: dict[str, str],
    first_action: str,
) -> None:
    gate = _with_old(tmp_path)
    _fail_replace(failing_call, monkeypatch)
    with pytest.raises(OSError, match="simulated"):
        gate.promote("new", PASS)
    monkeypatch.undo()
    assert gate.journal_path.exists()
    actions = gate.recover()
    assert actions[0].startswith(first_action)
    assert _tree(gate.output) == expected_live
    assert gate.verify_live()
    assert not gate.journal_path.exists()
    pointer = gate.last_good()
    assert pointer is not None
    assert pointer.run_id == ("new" if expected_live == NEW else "old")


def test_crash_while_removing_prev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _with_old(tmp_path)
    import shutil

    real = shutil.rmtree

    def fake(path: object, *a: object, **k: object) -> None:
        if Path(str(path)) == gate.prev_path:
            raise OSError("simulated crash")
        real(path, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(shutil, "rmtree", fake)
    with pytest.raises(OSError, match="simulated"):
        gate.promote("new", PASS)
    monkeypatch.undo()
    assert gate.recover() == ["completed the promotion of run new"]
    assert not gate.prev_path.exists()
    assert _tree(gate.output) == NEW


def test_unreadable_journal_restores_prev(tmp_path: Path) -> None:
    gate = _with_old(tmp_path)
    gate.prev_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(gate.output, gate.prev_path)  # as if the crash came right after the first rename
    gate.journal_path.write_text("{torn", encoding="utf-8")
    actions = gate.recover()
    assert actions[0] == "restored the previous live directory"
    assert _tree(gate.output) == OLD
    assert gate.recover() == []


def test_first_promotion_without_live(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    _stage(gate, "r1", {"x.csv": "1\n"})
    assert gate.promote("r1", PASS)
    assert _tree(gate.output) == {"x.csv": "1\n"}
    assert DirectoryPublishGate(tmp_path / "none").verify_live() is False
