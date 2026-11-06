"""Publish gate: write to staging, promote atomically on pass, keep last good on hold.

Algorithm and on-disk layout: docs/architecture.md "Publish gate".

The gate fails closed: a missing or failing verdict, an unreadable pointer, a lock held by a
live process, or any error while promoting means the live file is not replaced. At every step
the live path holds either the previous complete file or the new complete file.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from scrapewatch.errors import GateError
from scrapewatch.models import Verdict, VerdictStatus

STALE_LOCK_SECONDS = 6 * 3600
_CHUNK = 1 << 20


class LastGood(BaseModel):
    """Contents of the last-good pointer file."""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    path: str
    sha256: str
    size: int
    promoted_at: str
    verdict: VerdictStatus


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file, read in 1 MiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as fh:
        fh.flush()
        os.fsync(fh.fileno())


def _fsync_dir(path: Path) -> None:
    """Make a rename in a directory durable with fsync (POSIX; a no-op on Windows)."""
    if os.name == "nt":  # pragma: no cover - Windows has no directory handles for fsync
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - some filesystems refuse directory fsync
        pass
    finally:
        os.close(fd)


def _write_durable(path: Path, data: bytes) -> None:
    """Write and fsync a small file in place (used for the journal)."""
    with path.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _pid_alive(pid: int) -> bool:
    """True when a process with this pid exists on this machine."""
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _same_filesystem(a: Path, b: Path) -> bool:
    return os.stat(a).st_dev == os.stat(b).st_dev


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class PublishGate:
    """Guards one output file (directories: build-progress item 9b)."""

    def __init__(
        self, output: Path | str, *, keep_good: int = 3, promote_on_warn: bool = True
    ) -> None:
        """``output`` is the live path the consumer reads."""
        self.output = Path(output)
        self.keep_good = keep_good
        self.promote_on_warn = promote_on_warn
        self._lock_owned = False
        self.recovered: list[str] = []  # actions of the last recover()

    @property
    def state_dir(self) -> Path:
        """``<output>.scrapewatch/`` next to the live file."""
        return self.output.with_name(self.output.name + ".scrapewatch")

    @property
    def lock_path(self) -> Path:
        """The lock file."""
        return self.state_dir / "lock"

    @property
    def journal_path(self) -> Path:
        """Present only while a promotion is in progress."""
        return self.state_dir / "journal.json"

    @property
    def pointer_path(self) -> Path:
        """The last-good pointer."""
        return self.state_dir / "last-good.json"

    def staging_path(self, run_id: str) -> Path:
        """Where a run writes its output."""
        return self.state_dir / "staging" / run_id / self.output.name

    def _run_path(self, kind: str, run_id: str) -> Path:
        return self.state_dir / kind / run_id / self.output.name

    # --- lock -----------------------------------------------------------------------------
    def acquire(self) -> None:
        """Take the exclusive lock (O_CREAT|O_EXCL); a stale lock is taken over.

        A lock is stale when it is older than 6 hours, or its pid is not running on this
        machine. Raises GateError when another live process holds it.
        """
        if self._lock_owned:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        info = {"pid": os.getpid(), "host": platform.node(), "time": time.time()}
        for _ in range(3):
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._lock_is_stale():
                    with contextlib.suppress(FileNotFoundError):
                        self.lock_path.unlink()
                    continue
                holder = self._read_lock()
                raise GateError(
                    f"publish gate for {self.output} is locked by pid {holder.get('pid')} "
                    f"on {holder.get('host')}; not promoting"
                ) from None
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(info, fh)
            self._lock_owned = True
            return
        raise GateError(f"could not take the lock {self.lock_path}")  # pragma: no cover

    def release(self) -> None:
        """Release the lock if this gate holds it."""
        if self._lock_owned:
            with contextlib.suppress(FileNotFoundError):
                self.lock_path.unlink()
            self._lock_owned = False

    def _read_lock(self) -> dict[str, Any]:
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _lock_is_stale(self) -> bool:
        info = self._read_lock()
        try:
            age = time.time() - float(info.get("time", 0))
        except (TypeError, ValueError):
            age = float("inf")
        if not info:
            try:
                age = time.time() - self.lock_path.stat().st_mtime
            except FileNotFoundError:
                return True
        if age > STALE_LOCK_SECONDS:
            return True
        same_host = info.get("host") == platform.node()
        pid = info.get("pid")
        return bool(same_host and isinstance(pid, int) and not _pid_alive(pid))

    # --- staging --------------------------------------------------------------------------
    @contextmanager
    def stage(self, run_id: str) -> Iterator[Path]:
        """Yield the staging path to write to; on exception the staging file is kept.

        Takes the lock (held until ``promote`` or ``hold``; released here on exception),
        finishes or rolls back an interrupted promotion, and checks that staging is on the
        same filesystem as the live file so the final rename is atomic.
        """
        self.acquire()
        try:
            self.recover()
            path = self.staging_path(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.output.parent.mkdir(parents=True, exist_ok=True)
            if not _same_filesystem(path.parent, self.output.parent):
                raise GateError(
                    f"staging {path.parent} and output {self.output.parent} are on different "
                    "filesystems; an atomic replace is impossible"
                )
        except BaseException:
            self.release()
            raise
        try:
            yield path
        except BaseException:
            self.release()
            raise

    # --- promote / hold -------------------------------------------------------------------
    def promote(self, run_id: str, verdict: Verdict | None) -> bool:
        """fsync, atomic replace into ``output``, update last-good; True when promoted.

        Hold (and return False) for a hold verdict, a warn verdict with
        ``promote_on_warn=False``, or no verdict at all.
        """
        if not isinstance(verdict, Verdict):
            self.hold(run_id, Verdict(status=VerdictStatus.HOLD, reasons=["no verdict"]))
            return False
        if verdict.status == VerdictStatus.HOLD or (
            verdict.status == VerdictStatus.WARN and not self.promote_on_warn
        ):
            self.hold(run_id, verdict)
            return False
        self.acquire()
        try:
            staging = self.staging_path(run_id)
            if not staging.is_file():
                raise GateError(f"nothing staged for run {run_id} at {staging}")
            self.last_good()  # an unreadable pointer stops the promotion (fail closed)
            _fsync_file(staging)
            digest = sha256_file(staging)
            size = staging.stat().st_size
            journal = {
                "run_id": run_id,
                "sha256": digest,
                "size": size,
                "verdict": verdict.status.value,
                "started_at": _now(),
            }
            _write_durable(self.journal_path, json.dumps({**journal, "step": "copy"}).encode())
            good = self._run_path("good", run_id)
            good.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staging, good)
            _fsync_file(good)
            _write_durable(self.journal_path, json.dumps({**journal, "step": "replace"}).encode())
            os.replace(staging, self.output)
            _fsync_dir(self.output.parent)
            self._write_pointer(run_id, digest, size, verdict.status)
            self.journal_path.unlink()
            shutil.rmtree(staging.parent, ignore_errors=True)
            self._prune()
            return True
        finally:
            self.release()

    def _write_pointer(self, run_id: str, digest: str, size: int, status: VerdictStatus) -> None:
        pointer = LastGood(
            run_id=run_id,
            path=str(self._run_path("good", run_id)),
            sha256=digest,
            size=size,
            promoted_at=_now(),
            verdict=status,
        )
        tmp = self.pointer_path.with_name(self.pointer_path.name + ".tmp")
        _write_durable(tmp, pointer.model_dump_json(indent=2).encode())
        os.replace(tmp, self.pointer_path)
        _fsync_dir(self.state_dir)

    def hold(self, run_id: str, verdict: Verdict) -> Path:
        """Move the staging file to ``held/<run_id>/`` and leave the live output untouched."""
        try:
            staging_dir = self.staging_path(run_id).parent
            held_dir = self.state_dir / "held" / run_id
            held_dir.parent.mkdir(parents=True, exist_ok=True)
            if staging_dir.is_dir():
                if held_dir.exists():
                    shutil.rmtree(held_dir)
                os.replace(staging_dir, held_dir)
            else:
                held_dir.mkdir(parents=True, exist_ok=True)
            (held_dir / "verdict.json").write_text(verdict.model_dump_json(indent=2), "utf-8")
            self._prune()
            return held_dir / self.output.name
        finally:
            self.release()

    # --- pointer and verification ---------------------------------------------------------
    def last_good(self) -> LastGood | None:
        """Read the pointer file; None when nothing was ever promoted.

        Raises GateError when the pointer exists but cannot be read (fail closed).
        """
        try:
            text = self.pointer_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise GateError(f"cannot read {self.pointer_path}: {exc}") from exc
        try:
            return LastGood.model_validate_json(text)
        except ValidationError as exc:
            raise GateError(f"the pointer {self.pointer_path} is corrupt") from exc

    def verify_live(self) -> bool:
        """True when the live file's hash matches the last-good pointer."""
        pointer = self.last_good()
        if pointer is None or not self.output.is_file():
            return False
        return sha256_file(self.output) == pointer.sha256

    # --- recovery -------------------------------------------------------------------------
    def recover(self) -> list[str]:
        """Finish or roll back an interrupted promotion using the journal; returns actions taken.

        If the live file already holds the journal's new content, the pointer is completed.
        Otherwise the live file is left as it is (it is the previous complete file), the copy
        in ``good/`` is removed, and the staging file stays for inspection.
        """
        self.recovered = []
        if not self.journal_path.exists():
            return []
        try:
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
            run_id = str(journal["run_id"])
            digest = str(journal["sha256"])
            size = int(journal["size"])
            status = VerdictStatus(journal.get("verdict", VerdictStatus.PASS.value))
        except (OSError, ValueError, KeyError, TypeError):
            self.journal_path.unlink()
            self.recovered = ["removed an unreadable journal; live file left as it was"]
            return self.recovered
        if self.output.is_file() and sha256_file(self.output) == digest:
            self._write_pointer(run_id, digest, size, status)
            self.recovered.append(f"completed the promotion of run {run_id}")
            shutil.rmtree(self.staging_path(run_id).parent, ignore_errors=True)
        else:
            shutil.rmtree(self._run_path("good", run_id).parent, ignore_errors=True)
            self.recovered.append(
                f"rolled back the interrupted promotion of run {run_id}; live file unchanged"
            )
        self.journal_path.unlink()
        return self.recovered

    # --- retention ------------------------------------------------------------------------
    def _prune(self) -> None:
        pointer = None
        with contextlib.suppress(GateError):
            pointer = self.last_good()
        keep_current = pointer.run_id if pointer else None
        for kind in ("good", "held"):
            base = self.state_dir / kind
            if not base.is_dir():
                continue
            runs = sorted(
                (p for p in base.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in runs[self.keep_good :]:
                if old.name != keep_current:
                    shutil.rmtree(old, ignore_errors=True)


def sha256_tree(root: Path) -> tuple[str, int]:
    """Hash of a directory (sorted relative paths and file hashes) and its total size."""
    h = hashlib.sha256()
    total = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8") + b"\0" + sha256_file(path).encode() + b"\n")
        total += path.stat().st_size
    return h.hexdigest(), total


class DirectoryPublishGate(PublishGate):
    """Guards an output directory (many files per run) with a journaled rename swap.

    Promotion: copy staging to ``good/``, then ``rename(live, prev)``,
    ``rename(staging, live)``, update the pointer and ``rmtree(prev)``. Recovery looks at the
    state on disk: if the live directory already holds the new tree the promotion is
    completed; otherwise the previous directory is put back and the promotion rolled back.
    """

    @property
    def prev_path(self) -> Path:
        """Where the previous live directory waits during the swap."""
        return self.state_dir / "prev" / self.output.name

    def staging_path(self, run_id: str) -> Path:
        """The staging directory a run writes its files into."""
        return self.state_dir / "staging" / run_id / self.output.name

    @contextmanager
    def stage(self, run_id: str) -> Iterator[Path]:
        """Yield a staging directory (created); same rules as :meth:`PublishGate.stage`."""
        with super().stage(run_id) as path:
            path.mkdir(parents=True, exist_ok=True)
            yield path

    def promote(self, run_id: str, verdict: Verdict | None) -> bool:
        """Swap the staging directory in; True when promoted."""
        if not isinstance(verdict, Verdict):
            self.hold(run_id, Verdict(status=VerdictStatus.HOLD, reasons=["no verdict"]))
            return False
        if verdict.status == VerdictStatus.HOLD or (
            verdict.status == VerdictStatus.WARN and not self.promote_on_warn
        ):
            self.hold(run_id, verdict)
            return False
        self.acquire()
        try:
            staging = self.staging_path(run_id)
            if not staging.is_dir():
                raise GateError(f"nothing staged for run {run_id} at {staging}")
            self.last_good()
            for f in staging.rglob("*"):
                if f.is_file():
                    _fsync_file(f)
            digest, size = sha256_tree(staging)
            journal = {
                "run_id": run_id,
                "sha256": digest,
                "size": size,
                "verdict": verdict.status.value,
                "started_at": _now(),
            }

            def step(name: str) -> None:
                _write_durable(self.journal_path, json.dumps({**journal, "step": name}).encode())

            step("copy")
            good = self._run_path("good", run_id)
            if good.exists():
                shutil.rmtree(good)
            shutil.copytree(staging, good)
            step("move_live_to_prev")
            if self.prev_path.exists():
                shutil.rmtree(self.prev_path)
            self.prev_path.parent.mkdir(parents=True, exist_ok=True)
            if self.output.exists():
                os.replace(self.output, self.prev_path)
            step("move_staging_to_live")
            os.replace(staging, self.output)
            _fsync_dir(self.output.parent)
            step("pointer")
            self._write_pointer(run_id, digest, size, verdict.status)
            step("remove_prev")
            shutil.rmtree(self.prev_path, ignore_errors=True)
            self.journal_path.unlink()
            shutil.rmtree(staging.parent, ignore_errors=True)
            self._prune()
            return True
        finally:
            self.release()

    def verify_live(self) -> bool:
        """True when the live directory's tree hash matches the pointer."""
        pointer = self.last_good()
        if pointer is None or not self.output.is_dir():
            return False
        return sha256_tree(self.output)[0] == pointer.sha256

    def recover(self) -> list[str]:
        """Complete or roll back an interrupted swap from the state on disk."""
        self.recovered = []
        if not self.journal_path.exists():
            return []
        try:
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
            run_id = str(journal["run_id"])
            digest = str(journal["sha256"])
            size = int(journal["size"])
            status = VerdictStatus(journal.get("verdict", VerdictStatus.PASS.value))
        except (OSError, ValueError, KeyError, TypeError):
            journal, run_id = {}, ""
            digest, size, status = "", 0, VerdictStatus.PASS
        if run_id and self.output.is_dir() and sha256_tree(self.output)[0] == digest:
            self._write_pointer(run_id, digest, size, status)
            shutil.rmtree(self.prev_path, ignore_errors=True)
            shutil.rmtree(self.staging_path(run_id).parent, ignore_errors=True)
            self.recovered.append(f"completed the promotion of run {run_id}")
        else:
            if not self.output.exists() and self.prev_path.exists():
                os.replace(self.prev_path, self.output)
                self.recovered.append("restored the previous live directory")
            if run_id:
                shutil.rmtree(self._run_path("good", run_id).parent, ignore_errors=True)
            self.recovered.append(
                f"rolled back the interrupted promotion of run {run_id or '(unknown)'}; "
                "live directory unchanged"
            )
        self.journal_path.unlink()
        return self.recovered
