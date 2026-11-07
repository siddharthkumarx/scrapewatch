#!/usr/bin/env python3
"""Benchmark run (build item 24). Runs only through the checks workflow (checks/README.md).

Measures, against practice sites and the independent Shopify stores named on the command line:

(a) Playwright targets: measured wire bytes of each page load without and with the waste
    cutter's route blocker (fresh browser context per load, ``--repeats`` loads per mode;
    the median is reported, every load is kept), plus the blocker's own estimate of what it
    saved.
(b) Guard labels of every document response, per site (all expected ``ok``; anything else is
    listed with its rule so it can be investigated).
(c) httpx targets: cost per usable record under a sample cost model (direct $0, datacenter
    $0.8/GB, residential $8/GB), by re-pricing each run's measured events with
    ``scrapewatch.cost.meter.price_event``.

Writes ``<out>/results.json`` and one JSON summary per run under ``<out>/runs/``. The overhead
numbers (d) come from ``tests/perf`` in the same request.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from parsel import Selector

from scrapewatch.checks.rules import DataRules
from scrapewatch.config import CostConfig, Settings
from scrapewatch.cost.meter import price_event
from scrapewatch.integrations import httpx as sw_httpx
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.models import ResponseEvent, Run
from scrapewatch.runtime import Watch
from scrapewatch.waste import BlockPolicy, compression_headers, install_route_blocker

USER_AGENT = "scrapewatch-benchmark/0.1 (+https://github.com/siddharthkumarx/scrapewatch)"
LABELS = ("direct", "datacenter", "residential")
COST = CostConfig(
    currency="USD",
    default_proxy="datacenter",
    proxies={
        "direct": {"per_gb": 0.0},
        "datacenter": {"per_gb": 0.8},
        "residential": {"per_gb": 8.0},
    },
)
PRODUCTS = DataRules.model_validate(
    {
        "name": "products",
        "key": ["url"],
        "fields": {
            "title": {
                "type": "string",
                "required": True,
                "min_length": 1,
                "clean": ["whitespace", "html_entities"],
            },
            "price": {"type": "number", "required": True, "min": 0, "clean": ["price"]},
            "url": {"type": "url", "required": True, "clean": ["url"]},
        },
    }
)
COUNTRIES = DataRules.model_validate(
    {
        "name": "countries",
        "key": ["name"],
        "fields": {
            "name": {"type": "string", "required": True, "min_length": 2, "clean": ["whitespace"]},
            "population": {"type": "integer", "required": True, "min": 0, "clean": ["number"]},
            "area_km2": {"type": "number", "required": True, "min": 0, "clean": ["number"]},
        },
    }
)

Parser = Callable[[str, str], Iterator[dict[str, Any]]]


# --- parsers -------------------------------------------------------------------------------
def parse_books(text: str, url: str) -> Iterator[dict[str, Any]]:
    for pod in Selector(text).css("article.product_pod"):
        href = pod.css("h3 a::attr(href)").get()
        yield {
            "title": pod.css("h3 a::attr(title)").get(),
            "price": pod.css("p.price_color::text").get(),
            "url": str(httpx.URL(url).join(href)) if href else None,
        }


def parse_countries(text: str, url: str) -> Iterator[dict[str, Any]]:
    for c in Selector(text).css(".country"):
        yield {
            "name": " ".join(c.css(".country-name::text").getall()).strip(),
            "population": c.css(".country-population::text").get(),
            "area_km2": c.css(".country-area::text").get(),
        }


def parse_webscraper(text: str, url: str) -> Iterator[dict[str, Any]]:
    for card in Selector(text).css(".thumbnail"):
        href = card.css("a.title::attr(href)").get()
        yield {
            "title": card.css("a.title::attr(title)").get(),
            "price": "".join(card.css(".price ::text").getall()).strip(),
            "url": str(httpx.URL(url).join(href)) if href else None,
        }


def parse_shopify(text: str, url: str) -> Iterator[dict[str, Any]]:
    base = httpx.URL(url)
    for p in json.loads(text).get("products", []):
        variants = p.get("variants") or [{}]
        yield {
            "title": p.get("title"),
            "price": variants[0].get("price"),
            "url": str(base.copy_with(path=f"/products/{p.get('handle')}", query=None)),
        }


# --- runs ----------------------------------------------------------------------------------
def _watch(out: Path, name: str, rules: DataRules | None) -> tuple[Watch, list[ResponseEvent]]:
    settings = Settings(
        name=name,
        history={"path": out / "history"},
        report_dir=out / "runs",
        cost=COST,
    )
    w = Watch(settings, rules=rules)
    w.start()
    events: list[ResponseEvent] = []
    real = w.record_response

    def spy(*args: Any, **kwargs: Any) -> ResponseEvent | None:
        ev = real(*args, **kwargs)
        if ev is not None:
            events.append(ev)
        return ev

    w.record_response = spy  # type: ignore[method-assign]
    return w, events


def _summary_path(out: Path, run: Run) -> str | None:
    if run.report_path is None:
        return None
    return str(Path(run.report_path).with_suffix(".json").relative_to(out.parent.parent))


def _documents(events: list[ResponseEvent]) -> list[dict[str, Any]]:
    return [
        {
            "url": e.final_url or e.url,
            "status": e.status,
            "label": e.guard.label.value,
            "rule_id": e.guard.rule_id,
            "signals": e.guard.signals,
        }
        for e in events
        if e.resource_type.value == "document"
    ]


def run_httpx(
    out: Path, site: str, urls: list[str], parse: Parser, rules: DataRules
) -> dict[str, Any]:
    """One scrape with the httpx hooks, priced under each sample proxy label."""
    w, events = _watch(out, f"httpx-{site}", rules)
    headers = {"user-agent": USER_AGENT, **compression_headers()}
    with httpx.Client(
        event_hooks=sw_httpx.hooks(w, proxy="datacenter"), headers=headers, timeout=30
    ) as client:
        for url in urls:
            try:
                r = client.get(url)
            except httpx.RequestError as exc:
                sw_httpx.record_error(w, exc)
                continue
            if r.status_code == 200:
                for item in parse(r.text, url):
                    w.record_record(item)
            time.sleep(0.5)  # be gentle
    run = w.finish()
    usable = run.counts.usable_records
    cost = {}
    for label in LABELS:
        total = sum(price_event(e.model_copy(update={"proxy": label}), COST) for e in events)
        per = total / usable if usable else None
        cost[label] = {
            "total": total,
            "per_usable_record": per,
            "per_1000_usable_records": per * 1000 if per is not None else None,
        }
    return {
        "site": site,
        "urls": urls,
        "verdict": run.verdict.status.value,
        "reasons": run.verdict.reasons,
        "requests": run.counts.responses,
        "wire_bytes": run.counts.wire_bytes,
        "body_bytes": sum(e.body_bytes for e in events),
        "bytes_source": run.counts.by_bytes_source,
        "records": run.counts.records,
        "usable_records": usable,
        "labels": run.counts.by_label,
        "documents": _documents(events),
        "cost": cost,
        "summary": _summary_path(out, run),
    }


def _load(browser: Any, out: Path, site: str, url: str, *, blocker: bool, i: int) -> dict[str, Any]:
    mode = "after" if blocker else "before"
    w, events = _watch(out, f"pw-{site}-{mode}-{i}", None)
    context = browser.new_context(user_agent=USER_AGENT)
    sw_pw.attach(context, w, proxy="datacenter")
    if blocker:
        install_route_blocker(context, w, BlockPolicy())
    page = context.new_page()
    idle = True
    t0 = time.monotonic()
    try:
        page.goto(url, wait_until="load", timeout=45_000)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            idle = False  # pages with polling beacons never go idle; measure what loaded
            page.wait_for_timeout(3_000)
    finally:
        context.close()
    seconds = time.monotonic() - t0
    run = w.finish()
    waste = run.waste
    return {
        "mode": mode,
        "requests": run.counts.responses,
        "wire_bytes": run.counts.wire_bytes,
        "bytes_by_resource_type": waste.bytes_by_resource_type,
        "failed_requests": run.counts.by_label.get("error", 0),
        "blocked_requests": waste.blocked_requests,
        "bytes_saved_estimate": waste.bytes_saved_estimate,
        "network_idle": idle,
        "seconds": round(seconds, 2),
        "documents": _documents(events),
        "summary": _summary_path(out, run),
    }


def run_playwright(browser: Any, out: Path, site: str, url: str, repeats: int) -> dict[str, Any]:
    """Loads alternate without and with the blocker, so slow drift affects both alike."""
    loads: dict[str, list[dict[str, Any]]] = {"before": [], "after": []}
    for i in range(repeats):
        for blocker in (False, True):
            entry = _load(browser, out, site, url, blocker=blocker, i=i)
            loads[entry["mode"]].append(entry)
            time.sleep(1.0)
    before = statistics.median(x["wire_bytes"] for x in loads["before"])
    after = statistics.median(x["wire_bytes"] for x in loads["after"])
    return {
        "site": site,
        "url": url,
        "median_wire_bytes_before": before,
        "median_wire_bytes_after": after,
        "measured_saving_bytes": before - after,
        "measured_saving_pct": round((before - after) / before * 100, 1) if before else None,
        "median_blocked_requests": statistics.median(x["blocked_requests"] for x in loads["after"]),
        "median_bytes_saved_estimate": statistics.median(
            x["bytes_saved_estimate"] for x in loads["after"]
        ),
        "median_requests_before": statistics.median(x["requests"] for x in loads["before"]),
        "median_requests_after": statistics.median(x["requests"] for x in loads["after"]),
        "loads": loads,
    }


def _commit() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, default=Path("benchmarks/output"))
    ap.add_argument("--shopify", nargs="*", default=[], help="independent Shopify store hosts")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args(argv)
    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    books = [f"https://books.toscrape.com/catalogue/page-{n}.html" for n in range(1, 6)]
    laptops = "https://webscraper.io/test-sites/e-commerce/static/computers/laptops"
    httpx_targets: list[tuple[str, list[str], Parser, DataRules]] = [
        ("books.toscrape.com", books, parse_books, PRODUCTS),
        (
            "scrapethissite.com",
            ["https://www.scrapethissite.com/pages/simple/"],
            parse_countries,
            COUNTRIES,
        ),
        ("webscraper.io", [f"{laptops}?page={n}" for n in range(1, 4)], parse_webscraper, PRODUCTS),
    ]
    httpx_targets += [
        (host, [f"https://{host}/products.json?limit=250"], parse_shopify, PRODUCTS)
        for host in args.shopify
    ]
    pw_targets = [
        ("books.toscrape.com", books[0]),
        ("quotes.toscrape.com", "https://quotes.toscrape.com/js/"),
        ("webscraper.io", f"{laptops}?page=1"),
    ] + [(host, f"https://{host}/") for host in args.shopify]

    results: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "commit": _commit(),
        "python": sys.version.split()[0],
        "user_agent": USER_AGENT,
        "cost_model": COST.model_dump(mode="json"),
        "repeats": args.repeats,
        "httpx": [],
        "playwright": [],
    }
    for site, urls, parse, rules in httpx_targets:
        print(f"httpx {site} ...", flush=True)
        row = run_httpx(out, site, urls, parse, rules)
        results["httpx"].append(row)
        dc = row["cost"]["datacenter"]["per_1000_usable_records"]
        print(
            f"  {row['verdict']} records {row['records']} usable {row['usable_records']} "
            f"wire {row['wire_bytes']} B; datacenter per 1,000 usable {dc}",
            flush=True,
        )

    from playwright.sync_api import sync_playwright

    launch = (
        {"executable_path": os.environ["PW_CHROMIUM_PATH"]}
        if "PW_CHROMIUM_PATH" in os.environ
        else {}
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        try:
            for site, url in pw_targets:
                print(f"playwright {site} ...", flush=True)
                row = run_playwright(browser, out, site, url, args.repeats)
                results["playwright"].append(row)
                before, after = row["median_wire_bytes_before"], row["median_wire_bytes_after"]
                print(
                    f"  before {before} B, after {after} B ({row['measured_saving_pct']}% less); "
                    f"blocked {row['median_blocked_requests']}, "
                    f"estimate {row['median_bytes_saved_estimate']} B",
                    flush=True,
                )
        finally:
            browser.close()

    guard: dict[str, Counter[str]] = {}
    non_ok = []
    for row in results["httpx"] + [
        {
            "site": r["site"],
            "documents": [
                d for x in r["loads"]["before"] + r["loads"]["after"] for d in x["documents"]
            ],
        }
        for r in results["playwright"]
    ]:
        counter = guard.setdefault(row["site"], Counter())
        for d in row["documents"]:
            counter[d["label"]] += 1
            if d["label"] != "ok":
                non_ok.append({"site": row["site"], **d})
    results["guard"] = {site: dict(c) for site, c in guard.items()}
    results["guard_non_ok"] = non_ok
    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out / "results.json").write_text(
        json.dumps(results, indent=2, sort_keys=False), encoding="utf-8"
    )
    print(f"guard labels per site: {json.dumps(results['guard'])}")
    print(f"non-ok documents: {len(non_ok)}")
    print(f"results: {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
