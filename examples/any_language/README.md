# Any language: a shell scraper + `scrapewatch check`

What it shows: scrapewatch without any Python in the scraper. `scrape.sh` (curl and awk)
writes books.toscrape.com catalogue pages as JSON lines; `scrapewatch check` cleans and checks
the file against `rules.yml` and earlier runs, writes a report and a JSON summary, and exits
0 pass, 1 warn, 2 hold, 3 error, so a cron job or CI step can stop on a bad run. Add
`--promote-to data/books.jsonl` to publish the file only when it passes.

```bash
pip install scrapewatch
./run.sh 3          # 3 catalogue pages, 20 books each
```

Prices arrive as text (`£51.77`) and titles with HTML entities (`&#39;`); the price and
html_entities normalizers fix them, and the report lists every fix. A file check measures data,
not traffic, so this run has no cost figures.

Files: `scrape.sh` (the scraper), `run.sh`, `rules.yml`, `scrapewatch.yml` (history path).
