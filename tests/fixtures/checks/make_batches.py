"""Regenerate the batch_*.jsonl fixtures (run from the repo root). Deterministic, synthetic."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
CATS = ["fiction", "poetry", "travel"]


def book(i: int, price: float) -> dict[str, object]:
    rec: dict[str, object] = {
        "title": f"Book {i}",
        "price": round(price, 2),
        "currency": "GBP",
        "url": f"https://books.toscrape.com/catalogue/book_{i}/index.html",
    }
    if i % 50 != 49:  # 98% coverage, like the baseline
        rec["category"] = CATS[i % 3]
    return rec


def uniform(n: int) -> list[dict[str, object]]:
    return [book(i, 10 + 20 * (i + 0.5) / n) for i in range(n)]


def batches() -> dict[str, list[dict[str, object]]]:
    out: dict[str, list[dict[str, object]]] = {}
    out["row_drop"] = uniform(40)  # 60% fewer rows, same price spread
    cov = uniform(100)
    for i in range(100):
        if i % 10 == 9:  # 90% coverage: 8 points below 98%
            cov[i].pop("category", None)
        elif "category" not in cov[i]:
            cov[i]["category"] = CATS[i % 3]
    out["coverage_drop"] = cov
    shifted = []  # 4% of values in each lower bin, 16% in each upper bin
    i = 0
    for b in range(10):
        for _ in range(4 if b < 5 else 16):
            shifted.append(book(i, 11 + 2 * b))
            i += 1
    out["psi_shift"] = shifted
    out["unit_x100"] = [
        {**r, "price": round(float(str(r["price"])) * 100, 2)} for r in uniform(100)
    ]
    cur = uniform(100)
    for i in (5, 50, 95):
        cur[i]["currency"] = "USD"
    out["currency_mismatch"] = cur
    dup = uniform(98)
    dup += [dict(dup[3]), dict(dup[7])]  # 2 duplicates in 100 records
    out["duplicates"] = dup
    return out


if __name__ == "__main__":
    for name, records in batches().items():
        with (HERE / f"batch_{name}.jsonl").open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
