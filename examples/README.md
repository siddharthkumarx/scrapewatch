# Examples

Four runnable scrapers on practice sites, each monitored by scrapewatch. Every directory has a
README, `rules.yml` (data rules), `scrapewatch.yml` (history, report directory and a sample
cost model, not a quote) and `run.sh`.

| Example | Site | Shows |
|---|---|---|
| [`httpx_books/`](httpx_books/) | books.toscrape.com | `watch()` + httpx hooks, compression headers, data rules, publish gate |
| [`playwright_quotes/`](playwright_quotes/) | quotes.toscrape.com/js/ | Playwright listeners, browser time, the route blocker |
| [`scrapy_scrapethissite/`](scrapy_scrapethissite/) | scrapethissite.com/pages/simple/ | Scrapy extension, downloader middleware, item pipeline |
| [`any_language/`](any_language/) | books.toscrape.com | a curl + awk scraper, then `scrapewatch check` |

## Live run

All four ran against the live sites through the checks workflow on 2026-11-07 (request
`2026-11-07-examples`, commit 1eb8261). Results, with each run's report and JSON summary:
[`checks/results/2026-11-07-examples/`](../checks/results/2026-11-07-examples/) (`log.txt` has the output, `meta.json` the exit codes).

| Example | Verdict | Records (usable) | Responses | Wire bytes | Summary |
|---|---|---|---|---|---|
| httpx_books (5 pages) | pass | 100 (100) | 5 | 28,602 | [json](../checks/results/2026-11-07-examples/examples/httpx_books/output/books/2026-11-07T15-38-12_45d1cbb5.json) |
| playwright_quotes (3 pages) | pass | 30 (30) | 15 | 175,212 | [json](../checks/results/2026-11-07-examples/examples/playwright_quotes/output/quotes/2026-11-07T15-38-13_373c4825.json) |
| scrapy_scrapethissite | pass | 250 (250) | 2 | 14,053 | [json](../checks/results/2026-11-07-examples/examples/scrapy_scrapethissite/output/countries/2026-11-07T15-38-17_0c724f6b.json) |
| any_language (3 pages) | pass | 60 (60) | 0 (file check) | 0 | [json](../checks/results/2026-11-07-examples/examples/any_language/output/summary.json) |

The figures are copied from those summaries (`counts.records`, `counts.usable_records`,
`counts.responses`, `counts.wire_bytes`); `checks/results/2026-11-07-examples/log.txt` prints the same line per example.
