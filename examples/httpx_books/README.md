# httpx + books.toscrape.com (draft)

Draft written at build item 16; finalised, run live and linked to its results at item 23.

What it shows: `scrapewatch.watch()` around an httpx scraper, the httpx event hooks
(`sw_httpx.hooks`) measuring compressed bytes per response, `record_error` for transport
failures, data rules with the price normalizer, and the publish gate keeping the last good
`data/books.jsonl` live when a run should not ship.

```bash
pip install "scrapewatch[httpx]"
./run.sh 5
```

Files: `scrape.py` (the scraper), `rules.yml` (data rules), `scrapewatch.yml` (history and a
sample cost model), `run.sh`.
