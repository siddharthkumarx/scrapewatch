"""Regenerate records_1000.jsonl and field_stats.json (run from the repo root).

The expected statistics are computed here with the standard library's ``statistics`` module
("inclusive" quantiles = linear interpolation), independently of scrapewatch's own code.
"""

from __future__ import annotations

import bisect
import json
import random
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
WORDS = ["light", "attic", "tipping", "velvet", "soumission", "sharp", "objects", "requiem"]
RATINGS = ["One", "Two", "Three", "Four", "Five"]


def make_records() -> list[dict[str, object]]:
    rng = random.Random(20261106)
    out: list[dict[str, object]] = []
    for i in range(1000):
        rec: dict[str, object] = {
            "url": f"https://books.toscrape.com/catalogue/book_{i}/index.html",
            "title": " ".join(rng.choice(WORDS) for _ in range(2)),
            "price": round(rng.uniform(10, 60), 2),
            "rating": rng.choice(RATINGS),
            "stock": rng.randint(0, 22),
        }
        if i % 25 == 0:
            rec["price"] = None
        if i % 40 == 0:
            del rec["stock"]
        if i % 50 == 0:
            rec["title"] = ""
        out.append(rec)
    return out


def expected(records: list[dict[str, object]]) -> dict[str, object]:
    fields = sorted({k for r in records for k in r})
    out: dict[str, object] = {}
    for f in fields:
        values = [r.get(f) for r in records]
        nulls = sum(1 for v in values if v is None or v == "")
        nums = sorted(float(v) for v in values if isinstance(v, (int, float)))
        strs = [v for v in values if isinstance(v, str) and v]
        entry: dict[str, object] = {
            "count": len(records),
            "null_count": nulls,
            "distinct_count": len(set(nums)) + len(set(strs)),
            "numeric_count": len(nums),
            "p05": None,
            "p50": None,
            "p95": None,
            "mean": None,
            "hist": None,
            "top_values": [],
        }
        if nums:
            q20 = statistics.quantiles(nums, n=20, method="inclusive")
            q10 = statistics.quantiles(nums, n=10, method="inclusive")
            edges = [nums[0], *q10, nums[-1]]
            counts = [0] * 10
            for v in nums:
                counts[min(max(bisect.bisect_right(edges, v) - 1, 0), 9)] += 1
            entry.update(
                p05=q20[0],
                p50=statistics.median(nums),
                p95=q20[18],
                mean=statistics.fmean(nums),
                hist={"edges": edges, "counts": counts},
            )
        if strs:
            ranked = sorted(Counter(strs).items(), key=lambda kv: (-kv[1], kv[0]))[:20]
            entry["top_values"] = [[v, c] for v, c in ranked]
        out[f] = entry
    return out


if __name__ == "__main__":
    recs = make_records()
    with (HERE / "records_1000.jsonl").open("w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    (HERE / "field_stats.json").write_text(json.dumps(expected(recs), indent=1) + "\n", "utf-8")
