"""Streaming readers. Each yields dicts; CSV values stay strings (normalizers type them).

- CSV: ``csv.DictReader`` over UTF-8 (a BOM is skipped). An empty cell is ``None``, because
  CSV cannot tell an empty string from a missing value and coverage should count it missing.
- JSONL: one JSON object per line; blank lines are skipped. A ``.json`` file holding one JSON
  array is read as a list of records (the whole array is loaded; use JSONL for large files).
- Parquet: ``pyarrow`` (the ``parquet`` extra), read in batches.

Files ending in ``.gz`` are decompressed on the fly. Problems raise :class:`ReadError` with the
line number, so the CLI can exit 3.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

from scrapewatch.errors import ReadError

FORMATS = ("csv", "jsonl", "parquet")
_SUFFIXES = {
    ".csv": "csv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".json": "jsonl",
    ".parquet": "parquet",
    ".pq": "parquet",
}
_PARQUET_BATCH = 4096


def detect_format(path: Path) -> str:
    """'csv' | 'jsonl' | 'parquet' from the extension (".json" lines counts as jsonl)."""
    suffixes = [s.lower() for s in Path(path).suffixes]
    if suffixes and suffixes[-1] == ".gz":
        suffixes = suffixes[:-1]
    fmt = _SUFFIXES.get(suffixes[-1]) if suffixes else None
    if fmt is None:
        raise ReadError(f"{path}: cannot tell the format from the extension; pass --format")
    return fmt


def _open_text(path: Path) -> IO[str]:
    if path.suffix.lower() == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8-sig", newline="")
    return path.open(encoding="utf-8-sig", newline="")


def _read_csv(path: Path) -> Iterator[dict[str, Any]]:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with _open_text(path) as fh:
        reader = csv.DictReader(fh)
        try:
            for row in reader:
                if None in row:
                    raise ReadError(f"{path}:{reader.line_num}: more cells than header columns")
                yield {k: (None if v in ("", None) else v) for k, v in row.items()}
        except csv.Error as exc:
            raise ReadError(f"{path}:{reader.line_num}: {exc}") from exc


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with _open_text(path) as fh:
        first = fh.read(1)
        while first and first.isspace():
            first = fh.read(1)
        if first == "[":
            try:
                items = json.loads(first + fh.read())
            except json.JSONDecodeError as exc:
                raise ReadError(f"{path}: not valid JSON: {exc}") from exc
            for i, item in enumerate(items, 1):
                if not isinstance(item, dict):
                    raise ReadError(f"{path}: item {i} is not a JSON object")
                yield item
            return
        for n, line in enumerate(_lines(first, fh), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReadError(f"{path}:{n}: not valid JSON: {exc.msg}") from exc
            if not isinstance(item, dict):
                raise ReadError(f"{path}:{n}: a line must hold one JSON object")
            yield item


def _lines(first: str, fh: IO[str]) -> Iterator[str]:
    """Lines of ``fh`` with the already-read first character put back."""
    head = fh.readline()
    yield first + head
    yield from fh


def _read_parquet(path: Path) -> Iterator[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ReadError(
            "reading Parquet needs pyarrow: pip install 'scrapewatch[parquet]'"
        ) from exc
    try:
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=_PARQUET_BATCH):
            yield from batch.to_pylist()
    except (OSError, ValueError) as exc:  # pyarrow raises ArrowInvalid (a ValueError) and OSError
        raise ReadError(f"{path}: cannot read Parquet: {exc}") from exc


def read_records(path: Path, fmt: str | None = None) -> Iterator[dict[str, Any]]:
    """Stream records from a file.

    The format and the file are checked at once (so a bad path fails before any work); the
    rows are read lazily as the iterator is consumed.
    """
    path = Path(path)
    fmt = fmt or detect_format(path)
    if fmt not in FORMATS:
        raise ReadError(f"unknown format {fmt!r}; use one of {', '.join(FORMATS)}")
    if not path.is_file():
        raise ReadError(f"{path}: no such file")
    return _rows(path, fmt)


def _rows(path: Path, fmt: str) -> Iterator[dict[str, Any]]:
    try:
        if fmt == "csv":
            yield from _read_csv(path)
        elif fmt == "jsonl":
            yield from _read_jsonl(path)
        else:
            yield from _read_parquet(path)
    except UnicodeDecodeError as exc:
        raise ReadError(f"{path}: not UTF-8 text ({exc.reason} at byte {exc.start})") from exc
    except (OSError, EOFError) as exc:
        raise ReadError(f"{path}: {exc}") from exc
