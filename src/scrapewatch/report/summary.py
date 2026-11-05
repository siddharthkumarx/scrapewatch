"""summary.json: the Run model dumped with ``mode="json"``."""

from __future__ import annotations

from pathlib import Path

from scrapewatch.models import Run


def write_summary(run: Run, path: Path) -> Path:
    """Write the JSON summary atomically."""
    raise NotImplementedError("build-progress item 13")


def read_summary(path: Path) -> Run:
    """Read and validate a summary.json."""
    raise NotImplementedError("build-progress item 13")
