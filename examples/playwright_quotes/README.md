# Playwright + quotes.toscrape.com/js/

What it shows: Playwright listeners (`sw_pw.attach`) recording every request a real browser
makes, with measured bytes per resource type, browser time priced per minute, and the waste
cutter's route blocker (`install_route_blocker`) aborting images, media, fonts and known
trackers while counting the bytes it saved (an estimate, labelled as one). The quotes on this
site are rendered by JavaScript, so a plain HTTP client would see none.

```bash
pip install "scrapewatch[playwright]"
playwright install chromium
./run.sh 3          # 3 pages, 10 quotes each
```

`scrape.py` exits 0 on pass, 1 on warn, 2 on hold. The quotes go to `output/quotes.jsonl`,
the report and JSON summary to `output/quotes/`, history to `.scrapewatch/`.

Files: `scrape.py`, `rules.yml`, `scrapewatch.yml` (with a sample browser-minute price),
`run.sh`.
