# Benchmarks

One run, through the checks workflow on GitHub Actions (ubuntu-latest, Python 3.12.16), on
2026-11-07 from 16:04 to 16:09 UTC, at commit 21fd5a6 (the request commit; the code is that
of 73c3a0f). Every number below is copied from the committed results:

- `checks/results/2026-11-07-benchmark/benchmarks/output/results.json` (sections a to c),
- `checks/results/2026-11-07-benchmark/benchmarks/output/perf.json` (section d),
- `checks/results/2026-11-07-benchmark/benchmarks/output/runs/` (one JSON summary per run),
- `checks/results/2026-11-07-benchmark/log.txt` and `meta.json` (output and exit codes).

The script is `benchmarks/run_benchmark.py`; the request is in the history of
`checks/request.yml`. Sites: books.toscrape.com, quotes.toscrape.com/js/,
webscraper.io/test-sites/e-commerce/static, scrapethissite.com/pages/simple/, and three
independent Shopify stores (www.beardbrand.com, www.kettleandfire.com, www.partakefoods.com,
robots.txt checked for every path fetched). User agent
`scrapewatch-benchmark/0.1 (+https://github.com/siddharthkumarx/scrapewatch)`. These are
measurements of one run on one day; pages change, so a rerun will differ somewhat.

## (a) Bytes before and after the waste cutter (Playwright)

Each page was loaded 6 times in a fresh browser context: 3 times without the route blocker and
3 times with it (default `BlockPolicy`: images, media, fonts and listed tracker hosts),
alternating. Bytes are the sum of `wire_bytes` over every request Playwright reported for the
load (measured: `responseBodySize + responseHeadersSize`). The table shows medians; every load
is in `results.json` under `playwright[].loads`.

| Site (page) | Before (bytes) | After (bytes) | Measured saving | Requests before → after | Blocked | Blocker's estimate (bytes) |
|---|---|---|---|---|---|---|
| books.toscrape.com (catalogue page 1) | 336,944 | 111,932 | 225,012 (66.8%) | 31 → 10 | 22 | 1,044,480 |
| quotes.toscrape.com/js/ | 118,083 | 58,284 | 59,799 (50.6%) | 7 → 5 | 2 | 122,880 |
| webscraper.io (laptops, page 1) | 2,208,506 | 466,519 | 1,741,987 (78.9%) | 34 → 17 | 13 | 846,269 |
| www.beardbrand.com (home) | 3,801,066 | 2,466,866 | 1,334,200 (35.1%) | 245 → 218 | 27 | 1,266,877 |
| www.kettleandfire.com (home) | 5,419,195 | 2,824,134 | 2,595,061 (47.9%) | 297 → 254 | 59 | 2,872,320 |
| www.partakefoods.com (home) | 7,562,852 | 2,972,557 | 4,590,295 (60.7%) | 443 → 355 | 97 | 3,099,262 |

How to read it:

- The measured saving is the difference between the two medians, so it is real bytes. The
  blocker's estimate is what scrapewatch reports in a single run, where nothing unblocked is
  there to compare with (`waste.bytes_saved_estimate`: the run's median size per resource
  type, else the fixed table in docs/architecture.md 11.3). Against the measured saving, the
  estimate was 4.6 times too high on books.toscrape.com (small thumbnails against the 45 KiB
  image default) and 2.1 times on quotes.toscrape.com, within 11% on beardbrand.com and
  kettleandfire.com, and too low on webscraper.io (0.49 of the measured saving) and
  partakefoods.com (0.68), where blocked fonts and images also stop what they would have
  loaded. Treat it as an order of magnitude; the report labels it an estimate.
- The three loads of each mode were close (for example 336,924 to 336,968 bytes before on
  books.toscrape.com), so the medians are stable for this run.
- Requests that failed (cancelled or refused third-party requests) happened with and without
  the blocker; the blocker did not add any (partakefoods.com: 16 to 18 per load before, 9 or
  10 after). On beardbrand.com and partakefoods.com some loads never reached network idle
  (polling beacons); those loads were measured 3 seconds after the load event
  (`network_idle: false` in `results.json`).
- The blocker blocks only what its policy says. Shops still load their scripts and
  stylesheets; a stricter policy (`resource_types` with `script`) would save more and break
  more.

## (b) Guard results per site

The label of every document response in the run: the httpx scrapes and the 6 Playwright
loads per site (`results.json`, `guard` and `guard_non_ok`).

| Site | Documents | Labels |
|---|---|---|
| books.toscrape.com | 11 | ok 11 |
| quotes.toscrape.com | 6 | ok 6 |
| webscraper.io | 9 | ok 9 |
| scrapethissite.com | 1 | ok 1 |
| www.beardbrand.com | 7 | ok 7 |
| www.kettleandfire.com | 7 | ok 7 |
| www.partakefoods.com | 13 | ok 13 |

All expected `ok`, all `ok` (`guard_non_ok` is empty). A trial run before this one was not
clean: iframes (ad, consent and payment frames) were judged as pages, and one shop page was
labelled `captcha` because its 64 KiB guard window held only inline script. Both were fixed
before this run (commit 4e576b3; docs/incidents.md, 2026-11-07). Sub-resources are judged too
but are not documents: partakefoods.com had two 403 responses from third-party hosts
labelled `block` (for example `runs/pw-www.partakefoods.com-after-0/2026-11-07T16-07-55_ce5db550.json`, `counts.by_label`);
they are not the page, and the verdict's blocked share counts documents only.

## (c) Cost per usable record (httpx)

One scrape per site with the httpx hooks and `compression_headers()`; records cleaned and
checked against data rules (title, price, url; for scrapethissite.com name, population,
area). The run's measured events were then priced under each label of the sample cost model:
direct $0/GB, datacenter $0.8/GB, residential $8/GB (an example, not a quote; `cost_model` in
`results.json`). Costs in USD per 1,000 usable records, rounded to 3 significant figures
(full precision in `httpx[].cost`).

| Site | Requests | Wire bytes (measured) | Decoded bytes | Records (usable) | Verdict | Direct | Datacenter | Residential |
|---|---|---|---|---|---|---|---|---|
| books.toscrape.com (5 pages) | 5 | 28,607 | 257,073 | 100 (100) | pass | $0 | $0.000229 | $0.00229 |
| scrapethissite.com (1 page) | 1 | 11,905 | 203,361 | 250 (250) | pass | $0 | $0.0000381 | $0.000381 |
| webscraper.io (3 pages) | 3 | 27,772 | 113,812 | 18 (18) | pass | $0 | $0.00123 | $0.0123 |
| www.beardbrand.com (products.json) | 1 | 23,744 | 236,109 | 68 (68) | pass | $0 | $0.000279 | $0.00279 |
| www.kettleandfire.com (products.json) | 1 | 60,137 | 522,742 | 99 (99) | pass | $0 | $0.000486 | $0.00486 |
| www.partakefoods.com (products.json) | 1 | 8,561 | 43,267 | 15 (15) | pass | $0 | $0.000457 | $0.00457 |

Wire bytes are compressed bytes off the network plus a header estimate (docs/architecture.md
11.3), which is why they are 4 to 17 times smaller than the decoded bodies here: pricing by
decoded size would overstate these costs by that factor. A plain HTTP scrape of these sites
costs fractions of a cent per 1,000 records. The same catalogue page in a browser (section a,
without the blocker) moved 59 times the bytes of its httpx fetch on books.toscrape.com
(336,944 against 28,607 / 5 per page) and 239 times on webscraper.io (2,208,506 against
27,772 / 3), which is where proxy money goes.

## (d) Overhead on the runner

`tests/perf/test_overhead.py` (docs/architecture.md 15) in the same request, without coverage:
2,000 synthetic calls per path, p50 per call (`perf.json`).

| Path | p50 on the runner | Budget | Test limit (3× slack) |
|---|---|---|---|
| `record_response`, 64 KiB HTML body, guard on, history batched | 1.536 ms | 1.5 ms | 4.5 ms |
| `record_response`, guard off | 0.066 ms | 0.15 ms | 0.45 ms |
| `record_record`, 10 fields, 3 normalizers | 0.128 ms | 0.2 ms | 0.6 ms |
| `record_record`, JSON Schema (10 fields) | 0.111 ms | 0.5 ms | 1.5 ms |
| Flush queue, 200 events | 284,828 bytes (1.39 KiB per event) | 2 KiB per event | 1 MiB |

The 64 KiB guard path is 2% over its budget on this runner (within the test's limit). The
overhead work in commit aef0f0f (docs/architecture.md 15) brought it near the budget; most of
the remaining time is searching the 64 KiB window for the rule markers and stripping tags. Scraping pages
of 64 KiB or more, the guard adds about 1.5 ms per response; with the guard off, under 0.1 ms.
