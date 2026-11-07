# httpx + books.toscrape.com

What it shows: `scrapewatch.watch()` around an httpx scraper, the httpx event hooks
(`sw_httpx.hooks`) measuring compressed bytes per response, `record_error` for transport
failures, `compression_headers()` from the waste cutter, data rules with the price normalizer,
and the publish gate keeping the last good `data/books.jsonl` live when a run should not ship.

```bash
pip install "scrapewatch[httpx]"
./run.sh 5          # 5 catalogue pages, 20 books each
```

`scrape.py` exits 0 on pass, 1 on warn, 2 on hold. Each run writes a report and a JSON summary
to `output/books/` and keeps history in `.scrapewatch/`, so later runs are compared with the
earlier ones (row count, coverage, distributions).

Files: `scrape.py` (the scraper), `rules.yml` (data rules), `scrapewatch.yml` (history,
report directory and a sample cost model, not a quote), `run.sh`.
