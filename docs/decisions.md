# Decisions

What exists already, what scrapewatch does differently, and every design decision with its
reason. Numbered so other docs can cite them (D1, D2, ...). Survey facts checked on 2026-11-05
against PyPI metadata and the projects' own docs; no code was copied from any of them.

## 1. Name check

`scrapewatch` is free on PyPI (no project, 404 on the JSON API) and so are `scrape-watch` and
`scrapeplanner`. GitHub has a handful of zero-star repositories named scrapewatch (personal
dashboards and a Bright Data demo); none is a published package or a notable project. Verdict:
use the name.

## 2. Survey: prior art

| Project | What it is | Licence | What scrapewatch does differently |
|---|---|---|---|
| Spidermon (Zyte) | Monitors for Scrapy spiders: stats-based monitors, item validation with JSON Schema or schematics, notifications (Slack, Telegram, email), periodic monitors. | BSD-3-Clause | Spidermon is Scrapy-only and monitors *stats*. scrapewatch is framework-free (httpx, requests, Playwright, Scrapy, Crawlee, or any language through the CLI), classifies *every response* (block, captcha, consent, empty shell), compares batches with *history*, meters *cost* and *gates publishing*. Spidermon remains the better choice for a pure-Scrapy shop that wants custom monitor classes; scrapewatch's Scrapy extension can coexist with it. |
| Great Expectations | Large data-quality framework: expectation suites, data docs, checkpoints; aimed at warehouses and pipelines. | Apache-2.0 | GX is heavy (many dependencies, a project layout, a context object). scrapewatch keeps a four-dependency core and one YAML file, and its checks are scraping-specific: blocked-page share, empty shells, unit errors (x100), currency mismatch, cost per usable record. |
| pandera | Dataframe schema validation (pandas, polars, pyspark), typed and lightweight. | MIT | Excellent for dataframes, silent on HTTP responses, history or cost. scrapewatch validates *records* (dicts) with Pydantic or JSON Schema so it works on streams, not only frames; a pandera adapter is a possible later extra. |
| Soda Core | Metric checks on warehouses with SodaCL; scheduled scans. | Changed to a proprietary licence on PyPI as of the 4.x series (earlier releases were Apache-2.0) | Warehouse-oriented and now not open source. scrapewatch stays MIT and local-first (SQLite file, no server). |
| Scrapy contracts | `@url` / `@returns` / `@scrapes` docstring contracts run by `scrapy check`. | BSD-3-Clause (part of Scrapy) | Contracts are unit tests of callbacks run offline. scrapewatch watches *production runs* and their history. They complement each other. |
| wafw00f | Fingerprints WAF vendors by sending probe requests and matching response signatures. | BSD-3-Clause | wafw00f is an active *scanner* that sends its own (sometimes malicious-looking) probes. scrapewatch is *passive*: it only looks at responses the scraper already received, never sends probes, and classifies the *page type* (challenge, captcha, consent, login, empty) as well as the vendor. Its rules are a versioned YAML file with a fixture per rule rather than Python classes. |
| ScrapeOps SDK | Hosted monitoring for Scrapy with dashboards and proxy aggregation. | Proprietary service with an open SDK | Needs an API key and sends stats to a SaaS. scrapewatch has no server and no telemetry. |
| Paid bandwidth savers (Scrapfly Proxy Saver, Bright Data's cost-reduction guidance, Zyte API, Apify's resource blocking) | Block images/CSS/fonts, stub resources, cache responses at the proxy, often with vendor-claimed savings of 50% or more. | Proprietary | These save bandwidth *inside a vendor's product*. scrapewatch's waste cutter does the client-side part (route blocking with an allowlist, compression headers, conditional requests, URL dedupe) in the open, and *measures* bytes before and after so the number in the report is real. It never switches proxies or IPs. |

Takeaway: nothing combines response classification, data checks against history, cleaning with
counted fixes, a cost meter and a publish gate in one framework-free library. That is the gap.

## 3. Design decisions

**D1. Framework-free core, integrations as thin adapters.** The core speaks only the data
model (`ResponseEvent`, `Record`, `Run`). Integrations translate framework objects into
`Watch.record_response` / `Watch.record_record` calls and nothing else, so adding a framework
is one module and the CLI can check files produced by scrapers in any language.

**D2. Detection and measurement only.** No bypass, no CAPTCHA solving, no IP or proxy
switching, no advice on evasion in code, docs or rules. The guard says *what* answered; the
scraper's author decides what to do. This keeps the project safely on the right side of every
site's terms and makes rules easy to accept from contributors.

**D3. Guard rules as versioned YAML with one fixture per rule.** Rules in data, not code,
so contributors add a rule without Python. The file carries a calendar `rules_version`
(`2026.11.0`) separate from the package version, so a rules update can ship without a code
release. A test fails when a rule has no fixture. Fixtures hold features only (status, header
names and values, cookie names, title, markers of at most 200 characters), never a page body,
so the repository never contains a vendor's copyrighted page and never contains anything that
could serve as an evasion template.

**D4. Fail open for monitoring, fail closed for publishing.** A monitoring library that
crashes a scraper gets removed. Every hook catches its own exceptions, counts them in
`counts.monitor_errors` and returns. The publish gate is the opposite: if checks cannot run,
the verdict is hold and the last good output stays live.

**D5. Verdicts are pass / warn / hold, not a score.** Three states map to CI exit codes
(0, 1, 2) and to one human decision: ship, look, stop. Thresholds have conservative defaults
and live in the rules file, so a team tunes them per dataset.

**D6. History in one SQLite file, baselines from passing runs only.** SQLite is in the
standard library, handles parallel workers with WAL mode and a busy timeout, and a single file
is easy to back up or delete. Baselines use only runs that passed, so a bad run never poisons
the next comparison. JSONL is a later backend for read-only or object-storage setups.

**D7. Small core dependency set.** Core needs pydantic (models and validation), PyYAML
(rules), jsonschema (JSON Schema rules) and click (CLI). HTTP clients, browsers, Scrapy,
Crawlee and pyarrow are extras. Alerts use the standard library (`urllib.request`,
`smtplib`) so the core never needs an HTTP client. Reports are rendered with the standard
library (`html.escape` and format strings), not a template engine, and contain no external
scripts, fonts or images.

**D8. Fix-or-flag cleaning, nothing silent.** A normalizer either makes a change with exactly
one defensible interpretation (and records a `Fix`) or flags the value (a `Finding`). It never
guesses locale, year or currency. Fix counts per normalizer appear in every report so a client
can see what was changed.

**D9. Cost per usable record is the headline number.** Cost per request hides waste. Dividing
the run's cost by records that *passed checks* makes blocks, retries, empty pages and
unchanged refetches show up as a higher number. The report also breaks the waste down so the
author knows which lever to pull.

**D10. Wire bytes, honestly labelled.** Each event says how its byte count was obtained
(`measured`, `content_length`, `decoded`, `unknown`). Where a client cannot expose compressed
bytes, the report says the figure is an estimate rather than pretending to precision. (Build
item 17 found that requests can be measured: urllib3's `tell()` covers bodies with a length
and the decoder input covers chunked ones, so the estimates are now only fallbacks.)

**D11. One static HTML report per run.** Clients open it from an email attachment or a shared
drive, so it must be one file with inline CSS and SVG and no network access. The JSON summary
is the Run model itself, so tooling never parses HTML.

**D12. Publish gate with staging and an atomic replace.** The consumer's file is only ever
replaced by `os.replace` of a fully written, fsynced file on the same filesystem. A pointer
file records the last good run and hash. A held run keeps its staging file for inspection.
Directory outputs come later with a journal-based swap.

**D13. Privacy by construction.** No telemetry, no phone-home, no update checks. Stored
events keep an allow-list of headers, cookie *names* only, URLs with secret-looking query
values redacted, and no bodies. This is enforced by the model (`extra="forbid"`, no body field)
rather than by convention.

**D14. Overhead budget tested, not promised.** A test measures the per-response cost of
recording and classifying on synthetic responses and fails above budget. Numbers quoted in docs
come from committed runs (`checks/results/`) or from the benchmark item.

**D15. Verdict thresholds compare with the baseline median, not the last run.** One odd run
should not move the goalposts; the median of the last seven passing runs is robust to it.
Fewer than three passing runs means "no baseline": batch checks report info findings only.

**D16. Safe expression subset for cross-field rules.** Rules like `sale_price <= price`
are parsed with `ast` and evaluated by a small interpreter that allows comparisons, boolean
operators, arithmetic, field names, constants and five builtin calls. No `eval`, so a rules
file from a colleague cannot run code.

**D17. Python 3.10 to 3.13, semver after 1.0.** 3.10 is still common on servers; 3.13 is the
current release. Until 1.0, a minor version may change the API with a changelog entry; after
1.0, semver. The data model has its own `schema_version` and the rules file its own
`rules_version`, each with a migration path.

**D18. Lean CI while private.** One job on one Python for every push; matrix, extras, secret
scanning and dependency audit on dispatch only; docs-only pushes skip CI. Actions minutes are
shared with other private repositories. Superseded on 2026-11-06: development moved to this
public repository, where Actions run free, so `full.yml` also runs on every push to main and
on pull requests.

**D19. Commit hygiene enforced twice.** A local `commit-msg` hook and the same script in CI
allow only `Signed-off-by` trailers and reject generated-with footers, so the public history
is uniform regardless of which tool wrote a commit.

**D20. Crawlee support is conditional.** Crawlee for Python wraps httpx and Playwright
and exposes pre/post navigation hooks. If those hooks give access to the response object and
byte counts, the integration is a thin adapter over the httpx and Playwright ones. If not, the
item is dropped rather than shipped half-working; the build plan says how to decide.
Outcome (item 20, Crawlee 1.11): the navigation hooks give Crawlee's own response object,
which has no byte counts, and the default HTTP client (impit) exposes none either. Crawlee's
`HttpxHttpClient` passes `event_hooks` to its httpx-compatible client, and scrapewatch's httpx
hooks measure compressed bytes there unchanged. So HTTP crawlers are supported through
`sw_crawlee.http_client(w)`, and `attach()` refuses an HTTP crawler built without it rather
than guess; `PlaywrightCrawler` gets the Playwright listeners on every page through a
pre-navigation hook. `AdaptivePlaywrightCrawler` is not supported.

**D21. Practice sites only in examples, fixtures and benchmarks.** books.toscrape.com,
quotes.toscrape.com, webscraper.io test sites, scrapethissite.com, a few independent Shopify
stores, open-data and docs sites. No large marketplaces, no login-protected sites, so the
examples stay runnable and the project stays uncontroversial.
