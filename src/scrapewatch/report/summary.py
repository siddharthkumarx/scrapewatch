"""summary.json: the Run model dumped with ``mode="json"``."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scrapewatch.models import Run


def write_text_atomic(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` through a temporary file and ``os.replace``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def write_summary(run: Run, path: Path) -> Path:
    """Write the JSON summary atomically."""
    text = json.dumps(run.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    return write_text_atomic(path, text)


def read_summary(path: Path) -> Run:
    """Read and validate a summary.json."""
    return Run.model_validate_json(Path(path).read_text(encoding="utf-8"))
