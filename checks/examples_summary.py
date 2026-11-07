#!/usr/bin/env python3
"""Print one line per example run: name, verdict, records, usable records, responses, bytes.

Reads the newest JSON summary of each example (``examples/*/output/**/*.json`` holding a Run).
Used by the examples live check so the log shows all four results together.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    rows = []
    for example in sorted(p for p in Path("examples").iterdir() if (p / "rules.yml").is_file()):
        newest = None
        for path in sorted((example / "output").glob("**/*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            is_run = isinstance(data, dict) and "verdict" in data and "counts" in data
            if is_run and (newest is None or data["started_at"] >= newest[1]["started_at"]):
                newest = (path, data)
        if newest is None:
            rows.append(f"{example.name}: no summary found")
            continue
        path, run = newest
        c = run["counts"]
        rows.append(
            f"{example.name}: verdict {run['verdict']['status']}, records {c['records']}, "
            f"usable {c['usable_records']}, responses {c['responses']}, "
            f"wire bytes {c['wire_bytes']} ({path})"
        )
    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
