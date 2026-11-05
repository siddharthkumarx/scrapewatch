"""Streaming readers. Each yields dicts; CSV values stay strings (normalizers type them)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any


def detect_format(path: Path) -> str:
    """'csv' | 'jsonl' | 'parquet' from the extension (".json" lines counts as jsonl)."""
    raise NotImplementedError("build-progress item 12")


def read_records(path: Path, fmt: str | None = None) -> Iterator[dict[str, Any]]:
    """Stream records from a file."""
    raise NotImplementedError("build-progress item 12")
