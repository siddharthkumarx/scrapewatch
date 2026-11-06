# scrapewatch architecture

This document is the contract the builder implements. Where it and the code disagree, the
code is wrong until this document is changed on purpose (with a note in docs/decisions.md).
Decision references (D1, D2, ...) point at docs/decisions.md.

Contents

1. Overview and principles
2. Core data model
3. Public API and the 5-minute path
4. Package layout, import rules, dependencies
5. Configuration
6. Response guard and the guard rules YAML
7. Data rules YAML
8. Checks engine and verdict logic
9. Publish gate
10. Cleaning
11. Cost model
12. History store
13. Report, JSON summary, CLI, alerts
14. Failure policy
15. Overhead budget
16. Privacy and security
17. Compatibility and versioning
18. Benchmarks and sites

---

## 1. Overview and principles

scrapewatch sits beside a scraper and answers four questions after every run:

1. Did the pages we saved actually contain data, or blocks, challenges, consent walls and
   empty shells? (response guard)
2. Is the data right, compared with the schema and with the last good runs? (checks, verdict)
3. What did we change while cleaning it, and what could we not safely fix? (cleaning)
4. What did the run cost, and what did we pay for that bought nothing? (cost meter, waste)

Then it does two things: it writes a report a client can read, and it keeps the last good
output live when a run should not ship (publish gate).

Principles: detection and measurement only (D2); monitoring fails open, publishing fails
closed (D4); nothing changes silently (D8); no telemetry (D13); every number in a report is
measured and labelled with how (D10, D14).

Data flow for one run:

```
integration ──► Watch.record_response(url, status, headers, body, bytes, proxy, ...)
                   │  extract features ─► guard.classify ─► GuardResult
                   │  redact headers/url, cookie names only
                   │  cost.price_event ─► event.cost
                   └─► ResponseEvent ─► history (batched)
integration ──► Watch.record_record(item)
                   │  clean (fix-or-flag) ─► Fixes, Findings
                   │  schema / field / cross checks ─► Findings, usable
                   └─► Record ─► counts, field stats ─► history
Watch.finish() ──► batch checks vs baseline ─► Findings
                   verdict (pass/warn/hold) ─► cost + waste breakdown
                   summary.json, report.html ─► history.finish_run ─► alerts
PublishGate ─────► promote on pass (or warn), hold otherwise
```

---

## 2. Core data model

Implemented in `src/scrapewatch/models.py` (pydantic v2, `extra="forbid"`). `schema_version`
is 1. A breaking change bumps it and adds a migration (section 12). Pinned by
`tests/test_models.py`.

### 2.1 ResponseEvent

One request/response pair as an integration saw it. No body is stored, ever.

```json
{
  "schema_version": 1,
  "event_id": "3f2c9d8e5a0b4c1d9e8f7a6b5c4d3e2f",
  "run_id": "9a1b2c3d4e5f60718293a4b5c6d7e8f9",
  "ts": "2026-11-05T14:12:03.412Z",
  "url": "https://books.toscrape.com/catalogue/page-2.html",
  "final_url": null,
  "method": "GET",
  "domain": "books.toscrape.com",
  "status": 200,
  "elapsed_ms": 184.2,
  "wire_bytes": 12874,
  "body_bytes": 51233,
  "bytes_source": "measured",
  "resource_type": "document",
  "proxy": "datacenter",
  "attempt": 1,
  "retry_of": null,
  "headers": {"content-type": "text/html; charset=utf-8", "server": "nginx", "content-encoding": "gzip"},
  "cookie_names": ["sessionid"],
  "guard": {"label": "ok", "vendor": null, "rule_id": null, "confidence": 0.0, "signals": []},
  "cache": {"conditional": false, "unchanged": false, "deduped": false},
  "error": null,
  "integration": "httpx",
  "cost": 0.0000077,
  "monitor_error": null
}
```

Field notes:

- `wire_bytes` is what crossed the network for the response (compressed body plus an estimate
  of headers); `body_bytes` is the decoded body length. `bytes_source` says how `wire_bytes`
  was obtained (section 11.3).
- `resource_type` is one of document, xhr, script, stylesheet, image, font, media, other.
  HTTP-client integrations always report document unless the caller says otherwise.
- `attempt` starts at 1; a retry carries `attempt: 2` and `retry_of: <first event_id>`.
- `headers` is the allow-listed, redacted subset (section 16). `cookie_names` are names only.
- `guard.label` is one of ok, block, challenge, captcha, consent, login, rate_limit, empty,
  redirect, error_page, error, unknown.
- `error` is set for transport failures (timeout, DNS, TLS); `status` is then null and
  `guard.label` is `error`.
- `monitor_error` is set when a hook failed while building the event; the event is still
  stored with whatever was known.

### 2.2 Record

```json
{
  "schema_version": 1,
  "record_id": "c0ffee...",
  "run_id": "9a1b...",
  "source_event_id": "3f2c...",
  "data": {"title": "A Light in the Attic", "price": 51.77, "currency": "GBP", "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"},
  "findings": [],
  "fixes": [
    {"field": "price", "normalizer": "price", "before": "£51.77", "after": 51.77},
    {"field": "title", "normalizer": "whitespace", "before": "  A Light in the Attic ", "after": "A Light in the Attic"}
  ],
  "usable": true,
  "dedupe_key": "sha256:4b0c..."
}
```

`usable` is false when the record failed schema validation, has a finding of severity hold,
or is a duplicate (the later copy). Only usable records count in cost per usable record.

### 2.3 Finding

```json
{
  "finding_id": "a1...",
  "run_id": "9a1b...",
  "scope": "record",
  "ref": "c0ffee...",
  "check": "field.range",
  "severity": "warn",
  "field": "price",
  "message": "price 125000 is above max 100000",
  "observed": 125000,
  "expected": {"max": 100000},
  "rule_id": null
}
```

`scope` is response, record, batch or run; `ref` is the event_id, record_id or null. `check`
is a dotted id from a fixed vocabulary (section 8.4). `rule_id` is set for guard findings
(the YAML rule) and cross rules.

### 2.4 Run (also the JSON summary)

```json
{
  "schema_version": 1,
  "run_id": "9a1b...",
  "name": "books",
  "started_at": "2026-11-05T14:12:00Z",
  "finished_at": "2026-11-05T14:14:31Z",
  "status": "finished",
  "integration": "httpx",
  "scrapewatch_version": "0.1.0",
  "rules_version": "2026.11.0",
  "config_hash": "7c1e3a",
  "counts": {
    "responses": 1052, "document_responses": 1052,
    "by_label": {"ok": 1049, "empty": 2, "error": 1},
    "by_status": {"200": 1051, "null": 1},
    "retries": 3, "records": 1000, "usable_records": 998, "duplicates": 2,
    "findings_by_severity": {"info": 4, "warn": 3, "hold": 0},
    "fixes_by_normalizer": {"price": 1000, "whitespace": 412},
    "flags": 0, "monitor_errors": 0, "wire_bytes": 13891022
  },
  "cost": {
    "currency": "USD", "total": 0.0111,
    "by_domain": {"books.toscrape.com": 0.0111},
    "by_proxy": {"datacenter": 0.0111},
    "by_resource_type": {"document": 0.0111},
    "browser_minutes": 0.0, "browser_cost": 0.0, "api_requests": 0, "api_cost": 0.0,
    "usable_records": 998, "per_usable_record": 0.0000111, "per_record": 0.0000111
  },
  "waste": {
    "bytes_by_resource_type": {"document": 13891022},
    "retry_requests": 3, "retry_bytes": 38000, "retry_cost": 0.00003,
    "failed_requests": 3, "failed_bytes": 2100, "failed_cost": 0.0000017,
    "unchanged_refetch_requests": 0, "unchanged_refetch_bytes": 0, "unchanged_refetch_cost": 0.0,
    "blockable_requests": 0, "blockable_bytes": 0, "blockable_cost": 0.0,
    "total_waste_cost": 0.0000317,
    "blocked_requests": 0, "bytes_saved_estimate": 0, "deduped_requests": 0, "conditional_hits": 0
  },
  "verdict": {
    "status": "warn",
    "reasons": ["2 records (0.2%) were duplicates", "3 retries cost $0.00003"],
    "counts": {"hold": 0, "warn": 2, "info": 4},
    "thresholds": {"blocked_share_warn_pct": 2.0, "blocked_share_hold_pct": 20.0},
    "baseline_runs": ["8b...", "7a...", "6c..."]
  },
  "findings": [],
  "report_path": ".scrapewatch/reports/books/2026-11-05T14-12-00_9a1b.html",
  "overhead_ms_p50": 0.21
}
```

`findings` on the Run holds batch- and run-scope findings only; record findings stay on
records (and in history as counts plus a sample).

### 2.5 Verdict

`status` pass | warn | hold, `reasons` as plain sentences (these go in alerts and the report
header), `counts` of findings by severity, `thresholds` actually used, `baseline_runs` ids.

---

## 3. Public API and the 5-minute path

Everything below is importable from `scrapewatch` unless a module path is given.

### 3.1 watch()

```python
@contextmanager
def watch(
    name: str = "default",
    *,
    rules: str | Path | None = None,       # data rules file (rules.yml)
    config: str | Path | None = None,      # scrapewatch.yml (cost model, history, alerts)
    settings: Settings | None = None,      # programmatic alternative to config
    integration: str = "manual",
) -> Iterator[Watch]
```

```python
class Watch:
    run: Run
    settings: Settings
    def start(self) -> None
    def record_response(self, url, *, status, headers=None, body=None, wire_bytes=None,
                        body_bytes=None, bytes_source="unknown", final_url=None, method="GET",
                        elapsed_ms=None, resource_type="document", proxy=None, attempt=1,
                        retry_of=None, error=None, meta=None) -> ResponseEvent | None
    def record_record(self, data, *, source_event_id=None) -> Record | None
    def finish(self, *, crashed: bool = False) -> Run
    verdict: Verdict                      # property
    def report_path(self) -> Path | None
```

Five-minute path with httpx:

```python
import httpx, scrapewatch
from scrapewatch.integrations import httpx as sw_httpx

with scrapewatch.watch("books", rules="rules.yml") as w:
    with httpx.Client(event_hooks=sw_httpx.hooks(w, proxy="datacenter")) as client:
        for page in range(1, 51):
            r = client.get(f"https://books.toscrape.com/catalogue/page-{page}.html")
            for item in parse(r.text):
                w.record_record(item)
print(w.verdict.status, w.report_path())
```

On exit `finish()` runs (with `crashed=True` if an exception is propagating; the exception is
re-raised). Monitoring errors never raise; they are counted.

### 3.2 check()

```python
def check(path, *, rules, history=None, name=None, fmt=None) -> Run
```

What the CLI runs: read CSV/JSONL/Parquet, clean and check every row, compare with history,
return the Run with its verdict.

### 3.3 PublishGate

```python
gate = scrapewatch.PublishGate("data/books.jsonl", keep_good=3, promote_on_warn=True)
with scrapewatch.watch("books", rules="rules.yml") as w:
    with gate.stage(w.run.run_id) as staging:
        with open(staging, "w") as fh:
            for item in scrape():
                rec = w.record_record(item)
                fh.write(json.dumps(rec.data if rec else item) + "\n")
if gate.promote(w.run.run_id, w.verdict):
    print("live output updated")
else:
    print("held; last good output still live:", gate.last_good())
```

### 3.4 classify()

```python
result = scrapewatch.classify(status=403, headers=resp.headers, body=resp.content, url=url)
result.label      # GuardLabel.CHALLENGE
result.vendor     # "cloudflare"
```

### 3.5 Integrations (one short example each)

requests:

```python
import requests
from scrapewatch.integrations import requests as sw_requests
session = requests.Session()
sw_requests.mount(session, w, proxy="residential-a")
```

Playwright (sync):

```python
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.waste import install_route_blocker
context = browser.new_context()
sw_pw.attach(context, w, proxy="browser-pool")
install_route_blocker(context, w)          # opt-in waste cutter
```

Scrapy (settings.py):

```python
EXTENSIONS = {"scrapewatch.integrations.scrapy.ScrapewatchExtension": 0}
DOWNLOADER_MIDDLEWARES = {"scrapewatch.integrations.scrapy.ScrapewatchDownloaderMiddleware": 900}
ITEM_PIPELINES = {"scrapewatch.integrations.scrapy.ScrapewatchPipeline": 1000}
SCRAPEWATCH_RULES = "rules.yml"
SCRAPEWATCH_CONFIG = "scrapewatch.yml"      # optional
SCRAPEWATCH_PROXY_META_KEY = "proxy_label"  # request.meta key holding the proxy label
```

Crawlee for Python:

```python
from scrapewatch.integrations import crawlee as sw_crawlee
crawler = BeautifulSoupCrawler()
sw_crawlee.attach(crawler, w)
```

Any language (CLI):

```bash
scrapewatch check output.jsonl --rules rules.yml --report report.html --promote-to data/live.jsonl
echo $?   # 0 pass, 1 warn, 2 hold, 3 error
```

---

## 4. Package layout, import rules, dependencies

```
src/scrapewatch/
  __init__.py        public API re-exports
  _version.py        __version__
  errors.py          exception hierarchy
  models.py          data model (L0)
  config.py          Settings, CostConfig, ... (L0)
  _yaml.py           safe YAML loader with YAML 1.2 booleans (L0)
  redact.py          header/url/cookie redaction (L0)
  history/           store.py (SQLite), baselines.py, migrations.py (L1)
  cost/              meter.py (L1)
  guard/             features.py, rules.py, classify.py, rules/guard-rules.yml (L1)
  clean/             pipeline.py, price.py, number.py, date.py, text.py, url.py, dedupe.py (L1)
  checks/            rules.py, expr.py, engine.py, verdict.py (L1; engine uses history.baselines)
  io/                readers.py (L1)
  gate.py            publish gate (L2)
  report/            html.py, summary.py (L2)
  alerts/            base.py, telegram.py, slack.py, email.py, webhook.py (L2)
  runtime.py         Watch, watch() — wires L0..L2 together (L3)
  integrations/      httpx.py, requests.py, playwright.py, scrapy/, crawlee.py (L4)
  waste/             routes.py, cache.py, dedupe.py (L4)
  cli/               main.py (L4)
```

Import rules (enforced by `tests/test_layering.py`, build item 1):

- A module may import from its own layer or lower, never higher. L0 imports only the standard
  library, pydantic and PyYAML (config loading through `_yaml.py`, which resolves only
  `true`/`false` as booleans so the `alerts.on` key is not read as YAML 1.1 `True`).
- `runtime.py` is the only module that imports history, cost, guard, clean, checks, report
  and alerts together.
- `integrations/*`, `waste/*` and `cli/*` import third-party frameworks lazily (inside
  functions or behind `try/except ImportError`) so `import scrapewatch.integrations.playwright`
  works without Playwright installed and gives a clear `ImportError` only when used.
- Nothing outside `alerts/` performs network I/O; alerts use `urllib.request` and `smtplib`.

Dependencies: core = pydantic, PyYAML, jsonschema, click (D7). Extras: `httpx`, `requests`,
`playwright`, `scrapy`, `crawlee`, `parquet` (pyarrow), `all`.

---

## 5. Configuration

`scrapewatch.yml` (optional; defaults work with no file):

```yaml
name: books                       # run name; baselines are per name
rules: rules.yml                  # data rules file
history:
  path: .scrapewatch              # directory; history.sqlite lives inside
  backend: sqlite                 # sqlite | jsonl (jsonl: build item 2b, after v0.1)
  keep_runs: 50
  keep_event_days: 30
  baseline_runs: 7
cost:
  currency: USD
  default_proxy: direct
  proxies:
    direct: {per_gb: 0}
    datacenter: {per_gb: 0.8}
    residential-a: {per_gb: 8.0}
  apis:
    unblocker-x: {per_1000_requests: 2.5}
  browser:
    per_minute: 0.0
guard:
  enabled: true
  rules_path: null                # null = bundled guard-rules.yml
  extra_rules: [my-rules.yml]     # merged on top; same ids override
  body_window_bytes: 65536
alerts:
  on: [hold]                      # any of pass, warn, hold
  channels: [telegram, slack]     # empty = every channel whose env vars are set
report_dir: .scrapewatch/reports
store_events: true                # false = store run summaries and field stats only
```

Resolution order: explicit arguments > environment (`SCRAPEWATCH_HISTORY__PATH`,
`SCRAPEWATCH_NAME`, double underscore for nesting) > file > defaults. `config_hash` on the Run
is the first 6 hex chars of sha256 of the canonical JSON of the resolved settings minus paths,
so runs with different thresholds are visibly different in history.

---

## 6. Response guard and the guard rules YAML

### 6.1 What the guard sees: Features

`guard/features.py::extract_features` turns a raw response into a `Features` object. This is
the only view of a response the rules get, and it is also the fixture format.

| Feature | Source | Notes |
|---|---|---|
| `status` | response | null for transport errors |
| `headers` | redacted allow-list (section 16), lower-case names | |
| `cookie_names` | Set-Cookie names | values never kept |
| `request_url`, `final_url` | client | redirect detection |
| `content_type` | header, lower-case, parameters stripped | |
| `title` | first `<title>` in the body window, whitespace collapsed, max 200 chars | |
| `body_bytes` | decoded length | |
| `text_length` | visible text chars in the window (tags, scripts, styles removed) | |
| `text_density` | `text_length / min(body_bytes, window)` | 0.0 to 1.0 |
| `markers` | lower-case marker strings from the rules file found in the window | only markers that matched |
| `has_form_password` | `<input type="password"` in window | login walls |
| `has_captcha_widget` | known widget markers (`g-recaptcha`, `h-captcha`, `cf-turnstile`) | |
| `baseline_p50_bytes` | history size baseline for domain + path pattern | empty shell |
| `body_window` | first `body_window_bytes` decoded, in memory only | never stored |

The window is the first 64 KiB by default. Non-HTML content types (JSON, images) skip text
analysis; the guard still applies status and header rules.

### 6.2 Rules file format

`src/scrapewatch/guard/rules/guard-rules.yml`:

```yaml
schema_version: 1          # format of this file
rules_version: "2026.11.0" # calendar version of the rule set; bump on any rule change
rules:
  - id: cloudflare.challenge          # <vendor or generic>.<label>[.<variant>]
    vendor: cloudflare
    label: challenge
    confidence: 0.95
    since: "2026.11.0"
    notes: "Managed challenge interstitial."
    any:                              # rule matches when ANY group matches
      - all:                          # a group matches when ALL its signals match
          - status: [403, 503]
          - header: {name: server, regex: "^cloudflare$"}
          - body_marker: "cf-chl"
      - all:
          - header: {name: cf-mitigated, regex: "^challenge$"}
  - id: generic.rate_limit.429
    label: rate_limit
    confidence: 0.9
    any:
      - all:
          - status: [429]
```

Signals (exactly one key per signal):

| Signal | Matches when |
|---|---|
| `status: [..]` | status in list |
| `header: {name, regex}` | header present and regex (search, case-insensitive) matches value |
| `header: {name, exists: "true"}` | header present |
| `cookie: regex` | any cookie name matches |
| `body_marker: str` | lower-cased substring found in the window |
| `body_regex: str` | regex (search) on the window; compiled once at load, must be bounded |
| `title_regex: str` | regex on the title |
| `final_url_regex: str` | regex on the final URL (and `final_url != request_url`) |
| `content_type: str` | prefix match |
| `size_below: n` | body_bytes < n |
| `size_below_baseline_pct: p` | baseline exists and body_bytes < p% of baseline p50 |
| `text_density_below: f` | text_density < f |
| `text_length_below: n` | text_length < n |
| `has_form_password: bool`, `has_captcha_widget: bool` | feature equals |

Rule ids: `^[a-z0-9_]+(\.[a-z0-9_]+)+$`, unique in a file. Vendors in v0.1: cloudflare, akamai,
datadome, human (PerimeterX), imperva, kasada, aws_waf, hcaptcha, recaptcha, turnstile, plus
`generic.*` rules (empty shell, consent wall, login wall, rate limit, error page with 200,
redirect to login/geo/currency, soft 404). Rules name vendors by their public product
signatures (header names, cookie names, widget class names). They never describe how to pass
a challenge, and the project does not accept rules that do.

### 6.3 Evaluation

1. Extract features once per response.
2. Evaluate every rule; collect matches with (label, confidence, rule id, matched signals).
3. If no rule matched: label `ok` (or `error` for transport errors, `unknown` when the body
   could not be decoded) with confidence 0.
4. Otherwise pick by label precedence captcha > challenge > block > rate_limit > login >
   consent > redirect > error_page > empty > error > unknown > ok, then by higher
   confidence, then by rule order in the file.
5. `GuardResult.signals` lists the matched signal descriptions, e.g.
   `["status in [403]", "header server ~ ^cloudflare$", "marker cf-chl"]`, so the report can
   explain the label.

Redirect classification: a `redirect` label is produced only by rules with
`final_url_regex` (login, geo, currency, consent paths) when the final URL differs from the
request URL. Plain in-site redirects (http to https, trailing slash) stay `ok`.

Empty shell: `generic.empty.small_vs_baseline` fires when body is below 30% of the domain's
size baseline, text density is below 0.05 and no captcha widget was found. Without a
baseline, `generic.empty.tiny` fires for HTML documents under 2,048 bytes with text length
under 200.

### 6.4 Per-rule tests and contributing a rule

Fixtures: `tests/guard/fixtures/<rule id>[.<variant>].yml`:

```yaml
rule: cloudflare.challenge
expect: {label: challenge, vendor: cloudflare}
features:
  status: 503
  headers: {server: cloudflare, cf-mitigated: challenge, content-type: text/html}
  cookie_names: [__cf_bm]
  title: "Just a moment..."
  body_bytes: 7421
  text_length: 220
  text_density: 0.03
  markers: ["cf-chl"]
```

Negative fixtures: `tests/guard/fixtures/ok.<site>.yml` with `expect: {label: ok}` for real
practice-site features (books.toscrape.com, quotes.toscrape.com, scrapethissite.com) so new
rules do not produce false positives.

`tests/guard/test_rules.py` parametrises over every fixture and asserts the expected label,
vendor and (when given) rule id. A second test fails when any rule id in the rules file has no
fixture. A third test checks every regex compiles and is bounded (no nested quantifiers on
`.*`), and that no fixture contains a marker longer than 200 characters or a key named `body`.

To add a rule:

1. `scrapewatch guard features saved-response.html --status 403` prints the fixture-safe
   features of a response you saved; copy what the rule needs.
2. Add the rule to `guard-rules.yml`, bump `rules_version`, add the fixture.
3. `scrapewatch rules test` (or `pytest tests/guard`).
4. In the pull request say which product the signals come from and that no page body is
   included. Rules that describe how to avoid detection are declined (D2).

---

## 7. Data rules YAML

`rules.yml`, loaded by `checks/rules.py::load_data_rules`:

```yaml
schema_version: 1
name: books                       # run name for baselines (CLI --name overrides)
key: [url]                        # fields forming the dedupe key
schema: schema.json               # optional JSON Schema (draft 2020-12), path relative to this file
fields:
  title:
    type: string
    required: true
    min_length: 1
    clean: [whitespace, html_entities, mojibake]
  price:
    type: number
    required: true
    min: 0
    max: 100000
    clean: [price]
    clean_options: {locale: auto}       # auto | us | eu
    severity: hold                      # severity of this field's findings (default warn)
    coverage_hold_below_pct: 95         # batch: hold if fewer than 95% of records have it
  currency:
    type: string
    allowed: [GBP, USD, EUR]
  sale_price:
    type: number
    min: 0
    clean: [price]
  url:
    type: url
    required: true
    clean: [url]
    clean_options: {drop_params: [utm_*, gclid, fbclid, ref]}
  published_at:
    type: datetime
    clean: [date]
    clean_options: {day_first: false, assume_tz: UTC}
cross:
  - id: sale_le_regular
    expr: "sale_price <= price"
    when: "sale_price is not None"
    severity: warn
    message: "sale price above regular price"
batch:
  row_count: {warn_pct: 20, hold_pct: 50, min_rows: 10}
  coverage: {warn_drop_points: 5, hold_drop_points: 20}
  distribution: {psi_warn: 0.25, new_values_warn_pct: 10}
  changed_share: {warn_above_pct: 50}
  unit_error: {fields: [price], ratios: [100, 1000], tolerance_pct: 15, severity: hold}
  currency: {field: currency, value_field: price, expected: [GBP], hold_mismatch_pct: 1}
  duplicates_warn_pct: 1
verdict:
  blocked_share_warn_pct: 2
  blocked_share_hold_pct: 20
  schema_invalid_warn_pct: 1
  schema_invalid_hold_pct: 5
  flagged_warn_pct: 5
  monitor_errors_warn: 1
  min_usable_records: 1
```

Rules: `schema` and `fields` may be combined (schema first, then field rules). Field `type`
values: string, number, integer, boolean, date, datetime, url, object, array. A Pydantic model
can be passed programmatically instead of a schema path (`DataRules.json_schema` accepts a
dict; `watch(rules=...)` accepts a `DataRules` object; a helper `DataRules.from_model(Model)`
builds the JSON Schema from a Pydantic model). Unknown keys are errors (pydantic
`extra="forbid"`), so typos fail at load time. `schema_version` bumps on breaking format
changes with a loader shim.

Cross-rule expressions (D16): comparisons (`< <= == != >= >`), `and`, `or`, `not`,
`+ - * /`, `is None`, `is not None`, field names, string/number/bool constants, and calls to
`len`, `abs`, `min`, `max`, `round`. Anything else is a `RulesError` at load time.

---

## 8. Checks engine and verdict logic

### 8.1 Per record (in `check_record`)

1. Cleaning: for each field with a `clean:` list, run the normalizers in order (section 10).
   A flag stops the chain for that field and adds a Finding `clean.<normalizer>.<flag>`.
2. Schema: validate `data` against the JSON Schema (if any). Each error becomes a Finding
   `schema.invalid` with the JSON pointer in `field`; the record is unusable.
3. Field rules: `field.required`, `field.type`, `field.range`, `field.length`, `field.regex`,
   `field.allowed`, each with the field's severity.
4. Cross rules: `cross.<id>` with the rule's severity.
5. Dedupe key: sha256 of the JSON of the `key` fields (after cleaning). Later duplicates get
   `dup.repeat` (info) and `usable=false`.

### 8.2 Per batch (in `check_batch`, at finish)

Compared with the baseline (section 12.3) when it exists; otherwise info findings only.

| Check id | Compares | Warn | Hold |
|---|---|---|---|
| `batch.row_count` | records vs baseline median | outside ±20% | below −50% (or below `min_rows`) |
| `batch.coverage` | per-field non-null share vs baseline, in points | drop > 5 | drop > 20, or below `coverage_hold_below_pct` |
| `batch.null_rate` | alias view of coverage for the report | | |
| `batch.distribution.numeric` | PSI of a numeric field vs baseline (10 quantile bins) | PSI > 0.25 | PSI > `psi_hold` if set |
| `batch.distribution.categorical` | share of values never seen in baseline | > 10% | |
| `batch.changed_share` | share of same-key records whose value hash changed vs previous run | > 50% (or < `warn_below_pct`) | > `hold_above_pct` if set |
| `batch.unit_error` | median of field vs baseline median; ratio within 15% of 100, 1000, 1/100, 1/1000 | | hold (default severity) |
| `batch.currency` | share of records where `currency` disagrees with the currency parsed from `value_field` or is outside `expected` | | > 1% |
| `batch.duplicates` | duplicates share | > 1% | > `duplicates_hold_pct` if set |

PSI = Σ (actual% − baseline%) × ln(actual% / baseline%) over bins, with 0.5% floors.

### 8.3 Run level (in `compute_verdict`)

| Rule | Warn | Hold |
|---|---|---|
| Blocked share: responses with a bad label / document responses | > 2% | > 20% |
| Schema-invalid records share | > 1% | > 5% |
| Flagged (unfixable) values share | > 5% | |
| Monitor errors | ≥ 1 | |
| Usable records | | < `min_usable_records` |
| Any finding with severity hold | | hold |
| Any finding with severity warn | warn | |
| Checks engine failed (exception, rules not loadable, history unreadable when the gate is in use) | | hold |
| Run crashed (an exception left the `watch()` block) | | hold |

Verdict = hold if any hold condition, else warn if any warn condition, else pass. `reasons`
hold one sentence per triggered condition with the numbers ("blocked share 23.4% is above
20%"). Thresholds come from `verdict:` and `batch:` in rules.yml; the defaults above apply
without a rules file.

### 8.4 Check id vocabulary

`guard.<label>`, `schema.invalid`, `field.required|type|range|length|regex|allowed`,
`cross.<id>`, `dup.repeat`, `clean.<normalizer>.<flag>`, `batch.<name>` as in 8.2,
`run.blocked_share|schema_invalid|flagged|monitor_errors|usable_records|checks_failed`.

---

## 9. Publish gate

Goal: the consumer's file is never partially written and never replaced by a failing run.

On-disk layout for output `data/books.jsonl`:

```
data/books.jsonl                         live file (consumer reads this)
data/books.jsonl.scrapewatch/
  staging/<run_id>/books.jsonl           being written
  held/<run_id>/books.jsonl              held runs, kept for inspection (pruned with keep_good)
  good/<run_id>/books.jsonl              copies of the last keep_good promoted outputs
  last-good.json                         pointer: run_id, path, sha256, size, promoted_at, verdict
  journal.json                           present only during a promotion
  lock                                   exclusive lock (O_CREAT|O_EXCL with pid and time)
```

Algorithm:

1. `stage(run_id)` takes the lock (stale after 6 hours or if the pid is dead), runs
   `recover()`, creates `staging/<run_id>/`, yields the path. On exception the file stays in
   staging and the lock is released.
2. `promote(run_id, verdict)`:
   - if verdict is hold, or warn with `promote_on_warn=False`: `hold()`; return False.
   - fsync the staging file; compute sha256 and size.
   - write `journal.json` {run_id, step: "copy"}; copy staging to `good/<run_id>/` (fsync).
   - journal step "replace"; `os.replace(staging, live)` (atomic on the same filesystem; the
     staging dir is beside the live file so this holds); fsync the directory on POSIX.
   - write `last-good.json.tmp`, fsync, `os.replace` to `last-good.json`; delete journal.
   - prune `good/` and `held/` to `keep_good` entries; release the lock; return True.
3. `recover()`: if `journal.json` exists, the previous promotion was interrupted. If the live
   file's hash equals `good/<run_id>` hash, finish the pointer update; otherwise leave live as
   is (it is either the old good file or complete), delete the journal, and record a `run`
   finding `gate.recovered`.
4. `verify_live()` compares the live file hash with the pointer; the CLI prints a warning when
   they differ (someone edited the live file by hand).

Crash safety: at every step the live path is either the previous complete file or the new
complete file. Directory outputs (many files per run) are a later item: same journal, but the
swap is `rename(live, prev)`, `rename(staging, live)`, `rmtree(prev)`, with recovery for each
step. Cross-filesystem outputs raise `GateError` at `stage()` time.

The gate fails closed: a missing verdict, an unreadable pointer, a lock that cannot be taken,
or a checks engine exception all mean "do not promote".

---

## 10. Cleaning

Every normalizer has the signature `(value, options) -> CleanOutcome` and is registered under
the name used in `clean:` lists. A normalizer returns one of:

- unchanged (`changed=False`): nothing to do;
- fixed (`changed=True`, `value=new`): exactly one defensible reading; a `Fix` is recorded;
- flagged (`flag="<name>"`): more than one reading, or a value that cannot be made safe; the
  value is left as is and a Finding `clean.<normalizer>.<flag>` (severity warn, or the
  field's severity) is recorded. A flag stops the chain for that field.

`None` and empty strings pass through unchanged (required-ness is a field rule, not a
normalizer's job). Numbers come back as `float` (or `int` when integral and the field type is
integer); Decimal is used internally so "0.1" + "0.2" artefacts do not appear.

### 10.1 price

Options: `locale: auto|us|eu` (default auto), `currency_default: null`, `allow_free: false`.

| Input | Output | Fix or flag |
|---|---|---|
| `"$1,299.00"` | `1299.0`, extra `{currency: "USD"}` | fix |
| `"1.299,00 €"` | `1299.0`, `{currency: "EUR"}` | fix |
| `"Rs. 499"` | `499.0`, `{currency: "INR"}` | fix |
| `"£51.77"` | `51.77`, `{currency: "GBP"}` | fix |
| `"CHF 12.50"` | `12.5`, `{currency: "CHF"}` | fix |
| `"12,99"` | `12.99` | fix: a single separator followed by exactly two digits is decimal |
| `"1,299"` (locale auto) | unchanged | flag `ambiguous_separator` (1299 or 1.299) |
| `"1,299"` (locale us) | `1299.0` | fix |
| `"1.299"` (locale eu) | `1299.0` | fix |
| `"$10 – $20"` | unchanged | flag `range` |
| `"Free"` | `0.0` only with `allow_free: true` | otherwise flag `non_numeric` |
| `"-5.00"` | unchanged | flag `negative` |
| `"Price on request"` | unchanged | flag `non_numeric` |
| `1299` (already numeric) | unchanged | no fix |
| `"$ 1 299,00"` | `1299.0`, USD | fix (space thousands, comma decimal) |

Currency symbols and codes recognised: `$ (USD unless prefixed A$/C$/NZ$/S$/HK$/MX$/R$),
€, £, ¥ (JPY), ₹/Rs/Rs. (INR), ₩, ₽, ₺, ₫, ₪, zł, kr (flag `ambiguous_currency`: SEK/NOK/DKK),
CHF, and any ISO 4217 code adjacent to the number`. A symbol with several currencies flags
`ambiguous_currency` unless `currency_default` is set.

### 10.2 number

Options: `locale`, `allow_suffix: false` (k, M), `integer: false`.

| Input | Output | |
|---|---|---|
| `"1 234,5"` | `1234.5` | fix |
| `"1,234.56"` | `1234.56` | fix |
| `"3.5"` | `3.5` | fix (string to number) |
| `"12k"` | `12000` with `allow_suffix` | else flag `suffix` |
| `"≈ 40"` / `"40+"` | unchanged | flag `approximate` |
| `"n/a"` | unchanged | flag `non_numeric` |
| `"4.5 out of 5"` | unchanged | flag `non_numeric` |

### 10.3 date

Options: `day_first: null|true|false`, `assume_tz: null|"UTC"|IANA name`, `reference_time:
null`, `output: datetime|date`.

| Input | Output | |
|---|---|---|
| `"2026-11-05T14:12:03+01:00"` | `"2026-11-05T13:12:03Z"` | fix (converted to UTC) |
| `"2026-11-05T14:12:03Z"` | unchanged | no fix |
| `"2026-11-05 14:12"` with `assume_tz: Europe/Berlin` | `"2026-11-05T13:12:00Z"` | fix |
| `"2026-11-05 14:12"` without `assume_tz` | unchanged | flag `naive` |
| `"2026-11-05"` | `"2026-11-05"` (date, `output: date`) | fix when input had other formatting, else unchanged |
| `"Nov 5, 2026"` | `"2026-11-05"` | fix (unambiguous month name) |
| `"05/11/2026"` with `day_first: true` | `"2026-11-05"` | fix |
| `"05/11/2026"` without `day_first` | unchanged | flag `ambiguous_order` |
| `"13/11/2026"` | `"2026-11-13"` | fix (13 cannot be a month) |
| `"2 days ago"` with `reference_time` | computed, UTC | fix, plus finding `clean.date.relative` (info) |
| `"2 days ago"` without `reference_time` | unchanged | flag `relative` |
| `"Tue, 05 Nov 2026 14:12:03 GMT"` | `"2026-11-05T14:12:03Z"` | fix (RFC 2822) |
| `"14:12 EST"` style zone abbreviations | unchanged | flag `ambiguous_zone` (abbreviations are not unique) |

Supported inputs: ISO 8601 (with offset, Z, or naive), RFC 2822, `YYYY-MM-DD`, `YYYY/MM/DD`,
`DD.MM.YYYY`, `DD/MM/YYYY` and `MM/DD/YYYY` (with `day_first`), month names in English (full
and three-letter), Unix epoch seconds and milliseconds (10 or 13 digits). Output is ISO 8601
in UTC with a `Z` suffix. No dateutil dependency; the parser is a fixed set of regexes.

### 10.4 whitespace, html_entities, mojibake, strip_tags (text.py)

| Normalizer | Input | Output | |
|---|---|---|---|
| whitespace | `"  A  Light\n in the Attic "` | `"A Light in the Attic"` | fix: strip, collapse runs, NBSP and zero-width chars to space/removed |
| html_entities | `"Tom &amp; Jerry &#8211; 2"` | `"Tom & Jerry – 2"` | fix (`html.unescape`) |
| strip_tags | `"<b>Bold</b> text"` | `"Bold text"` | fix |
| mojibake | `"CafÃ©"` | `"Café"` | fix when latin-1/cp1252 → utf-8 round-trip succeeds and reduces the count of mojibake markers (`Ã`, `â€`, `Â`) |
| mojibake | `"Ã"` alone | unchanged | flag `undecidable` |
| mojibake | `"naïve"` (valid) | unchanged | no fix |

### 10.5 url

Options: `base: null`, `drop_params: [utm_*, gclid, fbclid, msclkid, mc_cid, mc_eid, ref,
_ga]`, `keep_fragment: false`, `sort_query: true`, `trailing_slash: keep|add|strip`.

| Input | Output | |
|---|---|---|
| `"HTTPS://Books.ToScrape.com:443/a/../b/?utm_source=x&b=2&a=1"` | `"https://books.toscrape.com/b/?a=1&b=2"` | fix |
| `"/catalogue/x.html"` with `base` | absolute | fix |
| `"/catalogue/x.html"` without `base` | unchanged | flag `relative` |
| `"javascript:void(0)"` / `"mailto:"` | unchanged | flag `not_http` |
| `"https://example.com/ü"` | `"https://example.com/%C3%BC"` | fix (percent-encoding normalised, host IDNA) |

### 10.6 dedupe (dedupe_key)

Not a field normalizer: `clean/dedupe.py::dedupe_key(record, key_fields)` builds
`sha256:` + hex of the canonical JSON of the key fields after cleaning. Missing key fields
produce a finding `dup.key_missing` (warn) and no key.

### 10.7 Safe fix vs flag

A fix is safe when: the parse has exactly one reading under the given options; it loses no
information the field type keeps (a price string's currency goes to `extra`, which the engine
writes into the `currency` field when that field is empty); and it can be explained in one
sentence in the report. Everything else is a flag. Normalizers never: guess locale from the
first value seen, guess the year, invent a currency, or coerce `None`.

---

## 11. Cost model

### 11.1 Config

`cost:` in scrapewatch.yml (section 5): `proxies.<label>.per_gb`, `apis.<label>.per_1000_requests`,
`browser.per_minute`, `default_proxy`. A proxy label is whatever the integration passed as
`proxy=` (or the Scrapy `request.meta[SCRAPEWATCH_PROXY_META_KEY]`); if the label is in
`apis` it is priced per request instead of per byte. Unknown labels are priced at 0 and
produce one `run` finding `cost.unknown_label` (info) listing them.

### 11.2 Formulas

```
cost(event)            = wire_bytes / 1e9 × per_gb[proxy]            (proxy label)
                       = per_1000_requests[label] / 1000               (api label)
browser_cost(run)      = browser_minutes × per_minute
cost(run)              = Σ cost(event) + browser_cost(run)
cost(domain)           = Σ cost(event) for events of that domain + browser_cost × share of
                         document responses of that domain
cost(proxy)            = Σ cost(event) for events with that label
cost_per_record        = cost(run) / records                 (null when records = 0)
cost_per_usable_record = cost(run) / usable_records          (null when usable_records = 0)

waste:
  retry_cost             = Σ cost(event) with attempt > 1
  failed_cost            = Σ cost(event) with label in BAD_LABELS or status ≥ 400 or error
  unchanged_refetch_cost = Σ cost(event) with cache.unchanged and wire_bytes > 0
  blockable_cost         = Σ cost(event) with resource_type in {image, media, font}
  total_waste_cost       = sum of the four (an event is counted once, in the first matching
                           category in this order: retry, failed, unchanged, blockable)
```

Bytes by resource type and the four waste categories are also reported in bytes and request
counts. Costs are stored in the configured currency with full float precision; the report
rounds for display and shows "< $0.0001" rather than 0.

### 11.3 How wire bytes are measured in each integration

| Integration | `wire_bytes` | `bytes_source` | Known limits |
|---|---|---|---|
| httpx | `response.num_bytes_downloaded` after the body is read, plus header estimate | measured | Hook runs on the response event before the body is read for streaming responses; the integration wraps `response.read()`/`aread()` or records on `close` to get the final count. HTTP/2 framing and TLS bytes are not included. |
| requests | `Content-Length` when present (compressed body) plus header estimate; else `len(response.content)` | content_length / decoded | Chunked, compressed responses without Content-Length are over-estimated (decoded size). The report marks such events. A future urllib3 wrapper could count raw bytes. |
| Playwright | `request.sizes()` → `responseBodySize + responseHeadersSize` (encoded) | measured | Responses served from the browser cache or a service worker report 0 or cached sizes; events carry `cache.unchanged` when `from_cache`-like signals are available. Aborted routes record 0 bytes and an estimate in `bytes_saved_estimate` (median size of that resource type seen earlier in the run, else a fixed table: image 45 KiB, font 60 KiB, media 500 KiB, script 80 KiB, stylesheet 30 KiB). |
| Scrapy | middleware at priority 900 sees `len(response.body)` before `HttpCompressionMiddleware` (590) decodes it, plus header estimate | measured | Must be verified in the integration test with a gzip fixture served through the full middleware chain; if Scrapy's ordering changes, fall back to Content-Length and label accordingly. |
| Crawlee | httpx-based crawlers: as httpx; Playwright crawler: as Playwright | as above | Depends on hook access to the response object. |
| CLI (`check`) | no responses; cost is reported only if a `--events events.jsonl` file is supplied | n/a | |

Header estimate = Σ (len(name) + len(value) + 4) + 2 over response headers, which is close
to HTTP/1.1 on the wire and an over-estimate under HTTP/2 (HPACK). The report labels totals
as "measured" only when every event in the run is measured.

---

## 12. History store

### 12.1 Files

`<history.path>/history.sqlite` (WAL mode, `busy_timeout` 5 s), plus
`<history.path>/reports/<name>/<started_at>_<run_id[:8]>.html` and `.json`. `SCRAPEWATCH_HOME`
or `history.path` relocates everything. The JSONL backend (later item) writes
`runs.jsonl` and `events/<run_id>.jsonl` instead, append-only.

### 12.2 Schema (sqlite, `schema_version` row in `meta`)

```sql
meta(key TEXT PRIMARY KEY, value TEXT);                       -- schema_version, created_at
runs(run_id TEXT PRIMARY KEY, name TEXT, started_at TEXT, finished_at TEXT, status TEXT,
     verdict TEXT, records INTEGER, usable_records INTEGER, cost_total REAL,
     scrapewatch_version TEXT, rules_version TEXT, config_hash TEXT, summary_json TEXT);
events(event_id TEXT PRIMARY KEY, run_id TEXT, ts TEXT, domain TEXT, path_pattern TEXT,
       status INTEGER, label TEXT, resource_type TEXT, proxy TEXT, attempt INTEGER,
       wire_bytes INTEGER, body_bytes INTEGER, cost REAL, unchanged INTEGER, event_json TEXT);
field_stats(run_id TEXT, field TEXT, count INTEGER, null_count INTEGER, distinct_count INTEGER,
            numeric_count INTEGER, p05 REAL, p50 REAL, p95 REAL, mean REAL,
            hist_json TEXT, top_values_json TEXT, PRIMARY KEY (run_id, field));
record_hashes(run_id TEXT, dedupe_key TEXT, value_hash TEXT, PRIMARY KEY (run_id, dedupe_key));
findings(finding_id TEXT PRIMARY KEY, run_id TEXT, scope TEXT, check_id TEXT, severity TEXT,
         field TEXT, message TEXT, finding_json TEXT);
size_baseline(domain TEXT, path_pattern TEXT, n INTEGER, p50_bytes INTEGER, updated_at TEXT,
              PRIMARY KEY (domain, path_pattern));
cache_entries(url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, body_sha256 TEXT, seen_at TEXT);
-- indexes: events(run_id), events(domain, path_pattern, label), runs(name, started_at), findings(run_id)
```

`path_pattern` replaces digit runs in the URL path with `{n}` and keeps at most three
segments, so `/catalogue/page-2.html` and `/catalogue/page-3.html` share a size baseline.

`hist_json` holds 10 quantile-bin edges and counts for numeric fields; `top_values_json` the
20 most common values with counts for strings. These are what batch checks read, so the
store never needs the raw records.

### 12.3 Baselines

`compute_baseline(store, name, n=7, min_runs=3)` takes the last `n` runs with status finished
and verdict pass (D6, D15). With fewer than `min_runs` it returns an empty baseline and batch
checks emit info findings ("no baseline yet, 2 of 3 passing runs"). Row count baseline is the
median; per-field coverage and quantiles are medians across runs; categorical top values are
unioned; `previous_value_hashes` come from the most recent passing run only. The numeric
histogram baseline is the element-wise median of the runs' 10 quantile-bin edges with the
mean share per bin. `HistoryStore.field_stats(run_ids)` returns per-run statistics
(`{field: {run_id: FieldStats}}`); `compute_baseline` does the aggregation.

Size baselines (`size_baseline`) are updated by `update_size_baselines(run_id)` from the run's
OK document responses: per domain and path pattern, the run's median body size is blended
with the stored p50, past samples weighing at most 50.

### 12.4 Retention and maintenance

`prune(keep_runs=50, keep_event_days=30)` deletes events older than the window, record hashes
and field stats of runs beyond `keep_runs`, and run rows beyond 365 days. Run summaries are
small and kept longest. `scrapewatch history prune` runs it; `finish()` runs it once every 20
runs. `VACUUM` is never automatic.

### 12.5 Parallel workers

Several processes may write one run (Scrapy with multiple spiders, a multiprocessing scraper):
the parent creates the run row; each worker opens its own connection, writes events in
batches of 200 (or every 2 seconds) inside one transaction, and never updates `runs`. The
parent's `finish()` aggregates from the tables. SQLite WAL plus `busy_timeout` handles the
contention; a write that still fails after the timeout is counted as a monitor error and
dropped, never retried in a loop. The `watch()` context in a worker is created with
`Watch.attach(run_id=...)` (item 2) rather than `start()`. Workers contribute events only; records
are recorded in the parent process, because field statistics and value hashes are stored per
run rather than per writer. The parent's `finish()` takes response counters from the events
table when it holds more events than the parent wrote itself.

---

## 13. Report, JSON summary, CLI, alerts

### 13.1 HTML report sections (one static file, inline CSS and SVG, no scripts)

1. Header: run name, verdict badge (green pass, amber warn, red hold), started/finished,
   duration, scrapewatch and rules versions.
2. "What this means": the verdict reasons as sentences, and one line on what happened to the
   output (promoted / held, last good run date) when the gate was used.
3. Responses: counts by label with a bar; by domain; the top 20 blocked or empty URLs (query
   values redacted) with label, vendor and matched signals.
4. Data checks: schema/field/cross findings grouped by check with counts and three examples
   each; batch comparison table (row count, coverage per field, null rate, distribution
   shift, changed share) with baseline values and the threshold that applied.
5. Cleaning: fixes per normalizer with before/after examples; flagged values with the flag
   reason and count.
6. Cost: total, per domain, per proxy, per usable record; waste table (retries, failed,
   unchanged refetches, blockable resources) in bytes, requests and money; bytes before vs
   after the waste cutter when enabled; a note on measurement quality (measured vs estimated).
7. History: inline SVG sparklines over the last 30 runs for records, blocked share, cost per
   usable record, with this run marked.
8. Monitor health: monitor errors, overhead p50/p95 per response, events stored.
9. Footer: how to reproduce (`scrapewatch report <run_id>`), file paths.

Plain language throughout: "blocked" rather than "HTTP 403 with WAF signature", with the
technical detail in a smaller line underneath.

### 13.2 JSON summary

`summary.json` = `Run.model_dump(mode="json")` (section 2.4). Written beside the HTML report
and returned by `scrapewatch history show`.

### 13.3 CLI

| Command | Purpose | Exit |
|---|---|---|
| `scrapewatch check PATH --rules rules.yml [--history DIR] [--name N] [--report F] [--json F] [--promote-to LIVE] [--format csv|jsonl|parquet]` | check a file; optional publish gate | 0 pass, 1 warn, 2 hold, 3 error |
| `scrapewatch guard classify FILE [--status N] [--url U]` | classify a saved response | 0; prints label, vendor, rule, signals |
| `scrapewatch guard features FILE [--status N] [--url U]` | print fixture-safe features (YAML) | 0 |
| `scrapewatch rules test [PATH]` | run every guard fixture | 0 ok, 1 failures |
| `scrapewatch rules validate FILE` | validate a guard or data rules file | 0 ok, 1 invalid |
| `scrapewatch report RUN_ID [--out F]` | re-render HTML | 0 |
| `scrapewatch history list [--name N] [--limit N]` / `show RUN_ID` / `prune` | inspect history | 0 |
| `scrapewatch cost RUN_ID` | cost and waste breakdown | 0 |
| `scrapewatch alert test [--channel C]` | send a test alert | 0 ok, 1 delivery failed |
| `scrapewatch init [--dir D]` | write example rules.yml and scrapewatch.yml | 0 |
| `scrapewatch --version` | | 0 |

Exit 3 means the tool itself failed (bad arguments, unreadable file, rules error); CI should
treat it like hold. `check` prints a one-screen text summary (verdict, reasons, counts, cost)
and the report path.

### 13.4 Alerts

Credentials come only from environment variables; `scrapewatch.yml` says when and where.

| Channel | Environment variables | Delivery |
|---|---|---|
| telegram | `SCRAPEWATCH_TELEGRAM_BOT_TOKEN`, `SCRAPEWATCH_TELEGRAM_CHAT_ID` | Bot API `sendMessage`, HTML parse mode, 10 s timeout |
| slack | `SCRAPEWATCH_SLACK_WEBHOOK_URL` | incoming webhook, Block Kit text |
| email | `SCRAPEWATCH_SMTP_HOST`, `SCRAPEWATCH_SMTP_PORT` (587), `SCRAPEWATCH_SMTP_USER`, `SCRAPEWATCH_SMTP_PASSWORD`, `SCRAPEWATCH_EMAIL_FROM`, `SCRAPEWATCH_EMAIL_TO` (comma list), `SCRAPEWATCH_SMTP_STARTTLS` (true) | `smtplib`, plain text body, HTML report attached when under 2 MiB |
| webhook | `SCRAPEWATCH_WEBHOOK_URL`, `SCRAPEWATCH_WEBHOOK_SECRET` (optional) | POST JSON payload; header `X-Scrapewatch-Signature: sha256=<hmac of body>` when the secret is set |

Common: `SCRAPEWATCH_ALERT_ON` (comma list, overrides `alerts.on`). Message text (all
channels):

```
scrapewatch · books · HOLD
Blocked share 23.4% is above 20%. Row count 412 is 58% below baseline 980.
Records 412 (usable 390) · cost $0.41 · $0.00105 per usable record
Report: .scrapewatch/reports/books/2026-11-05T14-12-00_9a1b.html
```

Webhook payload is the `AlertPayload` model (section `alerts/base.py`):
`{schema_version, run_id, name, verdict, reasons, records, usable_records, blocked_share_pct,
cost_total, cost_currency, cost_per_usable_record, report_path, started_at, finished_at}`.
Delivery failures are logged, counted in `monitor_errors`, and never raise out of `finish()`.

---

## 14. Failure policy

Monitoring fails open:

- Every integration hook and every `Watch.record_*` method is wrapped by `_guarded()`: it
  catches `Exception`, increments `counts.monitor_errors`, logs once per (module, exception
  type) at WARNING with a rate limit of one line per minute, and returns None. The scraper's
  request, response or item is returned unchanged.
- Hooks do no network I/O and no blocking disk I/O: history writes are batched in memory and
  flushed in one transaction every 200 events or 2 seconds from the calling thread, with the
  flush itself guarded. Alerts and report rendering run only in `finish()`.
- `finish()` is guarded step by step: if the report fails, the summary is still stored; if
  alerts fail, the verdict is still returned. `finish()` always returns a Run.
- The guard never raises on odd bodies: undecodable bytes give label `unknown`.
- Timeouts: the guard's regexes are bounded (loader rejects catastrophic patterns), the body
  window is capped, and `record_response` has a hard overhead ceiling checked by the test in
  section 15.

Publishing fails closed:

- `PublishGate.promote` requires a Verdict; `compute_verdict` returns hold when the checks
  engine raised, when rules could not be loaded, or when history could not be read and
  batch checks were configured.
- The CLI exits 3 on tool errors, which CI must treat as hold.

---

## 15. Overhead budget

Budget per response on the reference runner (ubuntu-latest, Python 3.12), measured by
`tests/perf/test_overhead.py` over 2,000 synthetic responses, p50 of per-call time:

| Path | Budget p50 | Test assertion (3× slack for noisy runners) |
|---|---|---|
| `record_response` with a 64 KiB HTML body, guard on, history batched | 1.5 ms | < 4.5 ms |
| `record_response` with guard disabled | 0.15 ms | < 0.45 ms |
| `record_record` with 10 fields, 3 normalizers, no schema | 0.2 ms | < 0.6 ms |
| `record_record` with JSON Schema (10 fields) | 0.5 ms | < 1.5 ms |
| Memory per stored event (in the flush queue) | 2 KiB | queue of 200 < 1 MiB |

The test is marked `perf`, runs in CI, and writes its numbers to the test log; the benchmark
item copies real numbers into docs/benchmarks.md with the commit they came from. The Run
records `overhead_ms_p50` so a user can see the monitor's cost in their own report.

---

## 16. Privacy and security

- No telemetry, no update checks, no network calls except the alerts the user configured.
- Stored headers: only names in `redact.KEPT_HEADERS`; any name in `SENSITIVE_HEADERS` or
  matching `SENSITIVE_HEADER_RE` is dropped even if a rule asked for it. (`www-authenticate`
  matches the pattern, so it is not kept). URL-valued headers (`location`) are redacted like
  URLs.
- Cookies: names only, from both request and response.
- URLs: userinfo removed; query values whose key matches `SENSITIVE_QUERY_RE` become
  `[redacted]` in stored events, findings, reports and alerts. The guard sees the original
  URL in memory only.
- Bodies: never stored; the guard keeps the body window in memory for the duration of one
  `record_response` call. `store_events: false` keeps only summaries and field stats.
- Records: `data` is stored only as field statistics and value hashes, not as values,
  except for the three examples per finding shown in the report (configurable `examples: 0`).
  Users scraping personal data should set `examples: 0`; the project's own examples and
  fixtures contain none.
- Fixtures: features only, markers ≤ 200 characters, enforced by test.
- Dependencies: pip-audit in the full workflow; no `eval`, no `pickle`, YAML via
  `yaml.safe_load` only; JSON Schema `$ref` resolution is local-only (no remote refs).
- Secrets: gitleaks in the full workflow; alert credentials are read from the environment at
  send time and never logged.

---

## 17. Compatibility and versioning

- Python 3.10, 3.11, 3.12, 3.13 (3.14 added when every extra supports it).
- Framework ranges: httpx ≥ 0.24 < 1; requests ≥ 2.28; playwright ≥ 1.40; scrapy ≥ 2.11;
  crawlee ≥ 1.0 < 2; pyarrow ≥ 15 (Python 3.10 users get the last pyarrow that supports it).
- Operating systems: Linux, macOS, Windows (the gate uses `os.replace` and a lock file that
  works on all three; no `fcntl`).
- Versioning: 0.x may change the API in a minor release with a changelog entry and a
  deprecation warning where feasible; from 1.0, semver. `SCHEMA_VERSION` (data model and
  history) and `rules_version` (guard rules, calendar) are independent. A history file with an
  older schema is migrated in place on open; a newer one raises `HistoryError` with the
  version it needs.

---

## 18. Benchmarks and sites

Live checks and benchmarks run only through `.github/workflows/checks.yml` (see
checks/README.md) and only against sites that are fine to name publicly: books.toscrape.com,
quotes.toscrape.com (including the JS and delayed variants), webscraper.io test sites,
scrapethissite.com, a few independent Shopify stores chosen at benchmark time (small
catalogues, public, no login), open-data portals and documentation sites. Never large
marketplaces or login-protected sites (D21). Every number in README or docs cites the
committed result directory it came from.
