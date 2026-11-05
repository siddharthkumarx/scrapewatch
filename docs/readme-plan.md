# README and launch plan

The README is written at build-progress item 26, after the owner sends the README rules. This
file holds what is already decided so the README can be written in one pass.

## Audience and promise

Freelancers and small teams who run scrapers for clients and get burned by bad pages saved
as data, silent drift, and proxy bills. Promise in one line: *know when your scraper is lying,
and what every usable record costs.* Adoption in 5 minutes with one context manager or one
CLI command.

## Structure (draft; the owner's rules override)

1. One-paragraph pitch and a verdict screenshot (report header: pass / warn / hold).
2. Install: `pip install scrapewatch` (+ extras table).
3. 60-second example: `watch()` with httpx; then `scrapewatch check output.jsonl --rules rules.yml`.
4. What it catches: a short table mapping problems (block pages saved as data, x100 prices,
   consent walls, row-count drops, unchanged pages fetched again) to the check that catches
   them.
5. Publish gate: three lines on "a failing run never replaces the last good output".
6. Cost meter: what cost per usable record means and the waste breakdown.
7. Integrations: httpx, requests, Playwright, Scrapy, Crawlee, CLI for any language; one
   snippet each, linking to examples/.
8. Reports and alerts: the report screenshot, the alert message, env vars table.
9. Benchmarks: numbers only from docs/benchmarks.md with links to the result paths.
10. What it does not do: no bypass, no CAPTCHA solving, no proxy switching, no telemetry.
11. Contributing a guard rule: 5 steps (architecture §6.4).
12. See also: `scrapeplanner` by the same author plans the cheapest polite method before
    scraping; scrapewatch watches the runs after.
13. Licence (MIT) and author line (Siddharth Kumar, siddharthkumar.com).

## Visuals

- `docs/images/report-sample.png`: the golden report rendered offline (item 13).
- `docs/images/check-demo.gif` or `.svg`: terminal recording of `scrapewatch check` going
  from pass to hold on a fixture with x100 prices, rendered offline (item 26).
- Keep every image under 1 MB and generated from committed fixtures so it can be regenerated.

## Launch notes

- Publish only after docs/publish-checklist.md is complete.
- Announce with one real benchmark number and the report screenshot; link the Shopify-store
  benchmark only by result path, not by naming the stores in the headline.
- Sibling tool: `scrapeplanner` (same author) plans the cheapest polite method before
  scraping; scrapewatch watches the runs after. Cross-link both READMEs under "See also".
- First issues to open for contributors: "add a guard rule for <vendor variant>" with the
  fixture format, and "add a normalizer for <unit>".
