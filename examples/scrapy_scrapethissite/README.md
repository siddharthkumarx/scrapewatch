# Scrapy + scrapethissite.com

What it shows: the three Scrapy pieces together. The extension starts the run when the
spider opens, finishes it when the spider closes and puts the verdict, counts, cost and report
path in Scrapy's stats (`scrapewatch/*`); the downloader middleware (priority 900, before
Scrapy decompresses) measures the compressed bytes of every response and runs the response
guard; the item pipeline cleans each item (text numbers become numbers) and checks it.

```bash
pip install "scrapewatch[scrapy]"
./run.sh            # one page, 250 countries
```

The countries go to `output/countries.jsonl`, the report and JSON summary to
`output/countries/`, history to `.scrapewatch/`. `rules.yml` sets `min_rows: 200`, so a
page that suddenly lists far fewer countries is held rather than shipped.

Files: `spider.py` (settings in `custom_settings`), `rules.yml`, `scrapewatch.yml`, `run.sh`.
