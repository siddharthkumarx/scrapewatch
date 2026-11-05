"""Publish gate: write to staging, promote atomically on pass, keep last good on hold.

Algorithm and on-disk layout: docs/architecture.md "Publish gate".
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from scrapewatch.models import Verdict, VerdictStatus


def _todo(item: str) -> None:
    raise NotImplementedError(item)


class LastGood(BaseModel):
    """Contents of the last-good pointer file."""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    path: str
    sha256: str
    size: int
    promoted_at: str
    verdict: VerdictStatus


class PublishGate:
    """Guards one output file (directories: build-progress item 9b)."""

    def __init__(
        self, output: Path | str, *, keep_good: int = 3, promote_on_warn: bool = True
    ) -> None:
        """``output`` is the live path the consumer reads."""
        self.output = Path(output)
        self.keep_good = keep_good
        self.promote_on_warn = promote_on_warn

    @property
    def state_dir(self) -> Path:
        """``<output>.scrapewatch/`` next to the live file."""
        return self.output.with_name(self.output.name + ".scrapewatch")

    @contextmanager
    def stage(self, run_id: str) -> Iterator[Path]:
        """Yield the staging path to write to; on exception the staging file is kept."""
        _todo("build-progress item 9")
        yield Path()  # pragma: no cover

    def promote(self, run_id: str, verdict: Verdict) -> bool:
        """fsync, atomic replace into ``output``, update last-good; True when promoted."""
        raise NotImplementedError("build-progress item 9")

    def hold(self, run_id: str, verdict: Verdict) -> Path:
        """Move the staging file to ``held/<run_id>/`` and leave the live output untouched."""
        raise NotImplementedError("build-progress item 9")

    def last_good(self) -> LastGood | None:
        """Read the pointer file; None when nothing was ever promoted."""
        raise NotImplementedError("build-progress item 9")

    def verify_live(self) -> bool:
        """True when the live file's hash matches the last-good pointer."""
        raise NotImplementedError("build-progress item 9")

    def recover(self) -> list[str]:
        """Finish or roll back an interrupted promotion using the journal; returns actions taken."""
        raise NotImplementedError("build-progress item 9")
