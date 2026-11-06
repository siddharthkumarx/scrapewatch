# Build progress

The build plan, in build order. Each item is sized for one sitting and is self-contained:
scope, files, public API it adds, acceptance tests (golden fixtures) and "done when". The
builder works top to bottom, ticks the box, records the local full-matrix result before each
milestone, and never starts an item before the previous one is done. The architecture
contract is docs/architecture.md (cited as A§n); decisions are docs/decisions.md (Dn).

Conventions for every item:

- Branch: the working branch given in docs/maintainers.md. Small commits, `git commit -s`, no other
  trailers (the hook enforces this).
- Tests live under `tests/<area>/` and use golden fixtures under `tests/fixtures/<area>/`.
  A golden fixture is an input file plus an expected output file; the test loads both and
  compares. Update a golden only with a reason in the commit message.
- Lint, types and tests green before the commit: `uv run ruff check . && uv run ruff format
  --check . && uv run mypy && uv run pytest`.
- Every stub replaced in an item loses its `raise NotImplementedError`; the coverage floor in
  pyproject rises by the amount noted in "done when" (never lowered).
- Every public function keeps the signature in the stub unless A§ says otherwise; a signature
  change updates architecture.md in the same commit.
- Numbers in docs come from committed runs only (checks/results/ or the perf test log).

Milestones: **M1** core engine (items 1-9), **M2** cleaning, CLI, report (10-14), **M3**
alerts and integrations (15-19), **M4** waste cutter, examples, benchmarks (20-24),
**v0.1 release** (25-28). Before each milestone the builder runs the full local matrix (see
docs/maintainers.md "Full local matrix") and records the result in the "Milestone log" at the bottom.

Legend: `[v0.1]` = part of the smallest release worth launching. Items without the tag ship in
v0.2 or later.

---

## M1 — core engine

### 1. Event model, redaction, config, run lifecycle `[v0.1]`

- [x] Scope: implement `config.load_settings`, `config.config_hash`, `redact.*`, and
  `runtime.Watch` in-memory (start, record_response without guard/cost/history,
  record_record without checks, finish without report/alerts), plus `_guarded()` and the
  flush queue skeleton. Add `tests/test_layering.py` enforcing A§4 import rules.
- Files: `config.py`, `redact.py`, `runtime.py`, `tests/test_config.py`, `tests/test_redact.py`,
  `tests/test_runtime.py`, `tests/test_layering.py`.
- Public API: `watch()`, `Watch.start/record_response/record_record/finish`, `load_settings`.
- Acceptance: golden `tests/fixtures/config/full.yml` → expected Settings JSON; env override
  test (`SCRAPEWATCH_HISTORY__PATH`); redaction goldens: 20 header sets and 20 URLs with
  expected outputs (`tests/fixtures/redact/*.json`); `record_response` with a hook that
  raises inside → `monitor_errors == 1`, no exception, event stored with `monitor_error`;
  `watch()` re-raises the user's exception and marks the run crashed; layering test parses
  imports with `ast` and fails on upward imports.
- Done when: all above pass, coverage floor set to 40.

### 2. History store (SQLite) and baselines `[v0.1]`

- [x] Scope: A§12 schema, migrations table, batched event writes, field stats computation
  (quantile bins, top values), record hashes, size baseline, retention, `Watch.attach(run_id)`
  for workers, `compute_baseline`.
- Files: `history/store.py`, `history/baselines.py`, `history/migrations.py`,
  `history/stats.py`, `tests/history/`.
- Public API: `open_store`, `HistoryStore.*`, `compute_baseline`, `Watch.attach`.
- Acceptance: create/open/migrate round trip; write 1,000 events in batches and read back
  equal models; field stats golden: `tests/fixtures/history/records_1000.jsonl` → expected
  `field_stats.json` (p05/p50/p95 within 1e-9, top values exact); baseline uses only passing
  runs (fixture with 3 pass + 2 hold runs → 3 run ids); empty baseline below `min_runs`;
  two processes (multiprocessing) writing 500 events each to one run, parent reads 1,000;
  prune deletes by window and keeps run rows; `size_baseline` path-pattern grouping golden.
- Done when: above pass on Linux and Windows path semantics (use `tmp_path`), coverage ≥ 50.

### 3. Cost meter `[v0.1]`

- [x] Scope: A§11 formulas, `price_event`, `CostMeter.add/add_browser_seconds/breakdown/waste`,
  unknown-label finding, header-size estimate helper, wiring into `Watch` so each event gets
  `cost` and `finish()` fills `run.cost` and `run.waste`.
- Files: `cost/meter.py`, `cost/bytes.py`, `tests/cost/`.
- Public API: `price_event`, `CostMeter`.
- Acceptance: golden `tests/fixtures/cost/events_small.jsonl` + `cost_config.yml` → expected
  `cost_breakdown.json` and `waste_breakdown.json` computed by hand in the fixture's README
  (per domain, per proxy, API-priced label, browser minutes spread by share, per usable
  record null when 0); waste category precedence (retry before failed before unchanged
  before blockable) with one event that qualifies for all four; float totals equal the sum
  of parts within 1e-12.
- Done when: pass, coverage ≥ 55.

### 4. Guard: features, rules loader, classifier, fixture runner `[v0.1]`

- [ ] Scope: `extract_features` (title, text density, markers, password form, captcha
  widgets, content-type handling, decoding with fallback), `load_rules` (merge, duplicate
  ids, regex compile and boundedness check), `classify_features` (precedence A§6.3,
  signals text), `classify`, `rule_fixture_paths`, `tests/guard/test_rules.py` (parametrised
  over fixtures; fails on a rule without fixture; fails on fixtures with `body` or long
  markers), and wiring into `Watch.record_response` with guard findings `guard.<label>`.
- Files: `guard/features.py`, `guard/rules.py`, `guard/classify.py`, `tests/guard/`.
- Public API: `classify`, `extract_features`, `load_rules`, `GuardRuleSet`.
- Acceptance: features golden: 6 small synthetic HTML files under `tests/fixtures/guard/html/`
  (written by the builder, not copied from any site) → expected features JSON; precedence
  test with two matching rules; unbounded regex rejected; `ok.*` fixtures for the three
  practice sites built from synthetic feature values; `classify()` on a 64 KiB body under
  the A§15 budget.
- Done when: pass with the rules file still empty except two generic rules used by tests,
  coverage ≥ 60.

### 5. Guard rules file: vendors and generic rules `[v0.1]`

- [ ] Scope: write `guard-rules.yml` with rules for cloudflare (challenge, block, turnstile
  widget), akamai (block, bot manager challenge), datadome (captcha/challenge, block),
  human/PerimeterX (block, captcha), imperva/Incapsula (block, challenge), kasada
  (challenge), aws_waf (challenge, captcha, block), hcaptcha, recaptcha, turnstile widgets,
  and generic rules: rate_limit (429 and text), login wall (password form + login path),
  consent wall (markers + short text), empty shell (baseline and tiny variants), error page
  with 200 (title regex + short text), soft 404, redirect to login/geo/currency. Each with
  signals drawn from public product signatures (header names, cookie names, widget class
  names, interstitial titles) and one positive fixture; several negative fixtures. Add
  `rules_version` "2026.11.0".
- Files: `guard/rules/guard-rules.yml`, `tests/guard/fixtures/*.yml`, `docs/guard-rules.md`
  (table of rules with the signal kinds, for contributors).
- Public API: none new.
- Acceptance: every rule has a fixture (test from item 4); negative fixtures stay `ok`; a
  synthetic page containing both a consent marker and a captcha widget classifies as captcha.
- Done when: `scrapewatch rules test` (item 14 adds the command; until then `pytest
  tests/guard`) passes, docs/guard-rules.md lists every rule.

### 6. Size baselines, empty-shell and redirect detection wired to history `[v0.1]`

- [ ] Scope: `Watch.record_response` looks up `size_baseline(domain, path_pattern)` through
  a small LRU, passes `baseline_p50_bytes` to features; `finish()` updates size baselines
  from this run's OK document responses (only when the verdict is pass); redirect label
  requires `final_url != url`; retry linkage (`retry_of`) and attempt counting from
  integrations' meta.
- Files: `runtime.py`, `guard/features.py`, `history/store.py`, `tests/test_runtime_guard.py`.
- Acceptance: run 1 stores baselines; run 2 with bodies at 20% of size → `empty` label; a
  redirect to `/login` → `redirect` with rule id; a retry chain of three attempts → three
  events, `counts.retries == 2`.
- Done when: pass, coverage ≥ 62.

### 7. Checks engine: data rules, expressions, schema, field and cross rules `[v0.1]`

- [ ] Scope: `load_data_rules` (relative schema path, `DataRules.from_model`), `expr.py`
  (ast whitelist interpreter), `check_record` (clean → schema → field → cross → dedupe key),
  check-id vocabulary A§8.4, `Record.usable` rule. Cleaning calls go through
  `clean.apply_normalizers`, which at this point is implemented with a `passthrough`
  normalizer only (item 10 adds the real ones).
- Files: `checks/rules.py`, `checks/expr.py`, `checks/engine.py`, `clean/pipeline.py`,
  `tests/checks/`.
- Public API: `load_data_rules`, `DataRules`, `check_record`, `compile_expr`, `evaluate`.
- Acceptance: rules golden `tests/fixtures/checks/rules_books.yml` loads to expected JSON;
  invalid keys rejected; expression tests: 30 allowed expressions evaluate as Python would,
  12 disallowed (`__import__`, attribute access, lambda, subscripts, comprehensions) raise
  `RulesError`; record goldens: `records_in.jsonl` → `records_out.jsonl` with findings and
  usable flags for required, type, range, length, regex, allowed, cross, duplicates, schema
  errors (JSON pointer in `field`).
- Done when: pass, coverage ≥ 66.

### 8. Batch checks and verdict `[v0.1]`

- [ ] Scope: `check_batch` (A§8.2 table: row count, coverage, distribution PSI and new
  categories, changed share, unit error, currency, duplicates share), `compute_verdict`
  (A§8.3 with reasons text), `check_records`, wiring into `Watch.finish`, `checks_failed`
  path.
- Files: `checks/engine.py`, `checks/verdict.py`, `checks/batch.py`, `tests/checks/`.
- Public API: `check_batch`, `compute_verdict`, `check_records`.
- Acceptance: baseline fixture (`tests/fixtures/checks/baseline.json`) plus six batch
  fixtures, one per failure mode (row drop 60% → hold; coverage drop 8 points → warn; PSI
  0.4 → warn; x100 prices → hold; currency mismatch 3% → hold; 2% duplicates → warn) with
  expected findings JSON; verdict truth table test (hold beats warn; `checks_failed` → hold
  with reason); reasons contain the numbers; PSI hand-computed example matches within 1e-9.
- Done when: pass, coverage ≥ 70. **M1 ends: run the full local matrix and log it.**

### 9. Publish gate `[v0.1]`

- [ ] Scope: A§9 for single-file outputs: lock, stage, promote (fsync, journal, replace,
  pointer), hold, last_good, verify_live, recover, keep_good pruning; `--promote-to` in the
  CLI comes in item 12.
- Files: `gate.py`, `tests/test_gate.py`.
- Public API: `PublishGate`, `LastGood`.
- Acceptance: promote on pass replaces live atomically and writes the pointer; hold leaves
  live untouched and keeps `held/<run>`; warn honours `promote_on_warn`; crash simulation:
  monkeypatch `os.replace` to raise after the journal step "copy" → `recover()` leaves live
  equal to the old file and clears the journal; crash after replace → `recover()` completes
  the pointer; stale lock (dead pid) is taken over; cross-filesystem staging raises
  `GateError`; Windows-safe (no fcntl).
- Done when: pass, coverage ≥ 72.

### 9b. Publish gate for directory outputs

- [ ] Scope: rename-swap with journal and recovery for a directory of files.
- Acceptance: same crash matrix as item 9 for the three steps.
- Done when: pass.

## M2 — cleaning, CLI, report

### 10. Cleaning: price and number `[v0.1]`

- [ ] Scope: `clean/price.py`, `clean/number.py` with every row of A§10.1 and A§10.2,
  currency table, `extra` currency fill rule, fix accounting in `apply_normalizers`.
- Files: `clean/price.py`, `clean/number.py`, `clean/pipeline.py`, `clean/_currency.py`,
  `tests/clean/`.
- Public API: normalizers `price`, `number` (registry names).
- Acceptance: golden `tests/fixtures/clean/price.yml` and `number.yml`: list of
  {input, options, expect: {value, changed, flag, extra}} covering every table row plus 40
  more real-world shapes written by the builder; property test that a fixed value never
  changes on a second pass (idempotence).
- Done when: pass, coverage ≥ 74.

### 11. Cleaning: date, text, url, dedupe key; pipeline wiring `[v0.1]`

- [ ] Scope: A§10.3 to 10.6; `clean_options` plumbing from rules; fix counts into
  `RunCounts.fixes_by_normalizer` and `flags`; three examples per finding kept for the
  report (A§16, `examples` option).
- Files: `clean/date.py`, `clean/text.py`, `clean/url.py`, `clean/dedupe.py`, `tests/clean/`.
- Public API: normalizers `date`, `whitespace`, `html_entities`, `strip_tags`, `mojibake`,
  `url`; `dedupe_key()`.
- Acceptance: goldens per normalizer as in item 10 (every table row, plus timezone
  conversion across DST, epoch seconds vs milliseconds, IDNA host, tracking-param globs);
  idempotence; `tests/fixtures/clean/records_messy.jsonl` → `records_clean.jsonl` end to end
  through `check_record` with the expected `fixes` lists.
- Done when: pass, coverage ≥ 76.

### 12. File readers and `scrapewatch check` with exit codes and `--promote-to` `[v0.1]`

- [ ] Scope: `io/readers.py` (CSV with `csv.DictReader`, JSONL, Parquet via pyarrow extra with
  a clear error when missing), `checks.engine.check`, the `check` CLI command printing the
  text summary and exiting 0/1/2/3, `--promote-to` through `PublishGate` (stage = copy of the
  input, promote on verdict), `--json`, `--report` (report placeholder until item 13 writes
  the JSON only).
- Files: `io/readers.py`, `checks/engine.py`, `cli/main.py`, `tests/cli/test_check.py`,
  `tests/io/`.
- Public API: `check()`, CLI `check`.
- Acceptance: three input formats with the same 50 records give identical Runs (modulo ids);
  exit code tests for pass/warn/hold/error (bad rules file → 3); `--promote-to` end to end
  with a hold keeping the previous live file; CSV strings typed by normalizers; a 100k-row
  JSONL streams under 200 MB RSS (measured with `resource` on Linux, skipped elsewhere).
- Done when: pass, coverage ≥ 78.

### 13. JSON summary and HTML report `[v0.1]`

- [ ] Scope: A§13.1 sections, A§13.2 summary, inline SVG sparklines, atomic writes, report
  path on the Run, `Watch.finish` writes both; plain-language strings.
- Files: `report/html.py`, `report/summary.py`, `report/_svg.py`, `report/templates/base.html`
  (a Python format-string template with `html.escape`), `tests/report/`.
- Public API: `render_html`, `write_summary`, `read_summary`.
- Acceptance: golden `tests/fixtures/report/run_warn.json` (+ findings, sample records,
  history) → `report_warn.html` compared after whitespace normalisation; the HTML contains no
  `<script`, no `http://` or `https://` in `src=`/`href=` except the report's own footer
  links to the project; every section heading present; `summary.json` round-trips through
  `Run.model_validate`; report renders with an empty history and with zero records.
- Done when: pass; a screenshot of the golden report rendered by Playwright (local, offline)
  is committed to `docs/images/report-sample.png`; coverage ≥ 80.

### 14. Remaining CLI commands `[v0.1]`

- [ ] Scope: `guard classify`, `guard features`, `rules test`, `rules validate`, `report`,
  `history list|show|prune`, `cost`, `init` (writes example `rules.yml` and
  `scrapewatch.yml` with comments), `--version`.
- Files: `cli/main.py`, `cli/_output.py`, `tests/cli/`.
- Acceptance: each command has a `CliRunner` test; `guard features` output loads as a valid
  fixture and contains no key named `body`; `init` files load through their loaders;
  `rules test` exits 1 on a deliberately broken fixture.
- Done when: pass, coverage ≥ 82. **M2 ends: full local matrix, log it.**

## M3 — alerts and integrations

### 15. Alerts `[v0.1]`

- [ ] Scope: A§13.4: payload, four channels on the standard library, `send_alerts`, env
  override, HMAC signature, `alert test` command, wiring into `finish()` (guarded).
- Files: `alerts/*.py`, `tests/alerts/`.
- Acceptance: a local `http.server` thread receives the webhook JSON and the signature
  verifies; Slack and Telegram request bodies match goldens (`tests/fixtures/alerts/*.json`)
  using a fake `urlopen`; email uses a fake SMTP class and attaches the report when small;
  delivery failure → counted, not raised; `on` filtering.
- Done when: pass, coverage ≥ 83.

### 16. httpx integration and `watch()` end to end `[v0.1]`

- [ ] Scope: `integrations/httpx.py` sync and async hooks: request hook stamps start time and
  attempt from `request.extensions`, response hook records after body read (wrap `read`
  / `aread` for streaming), `num_bytes_downloaded`, redirects via `response.history`,
  transport errors through a transport wrapper or documented `try/except` helper
  `sw_httpx.record_error(w, exc, request)`.
- Files: `integrations/httpx.py`, `tests/integrations/test_httpx.py`.
- Public API: `hooks`, `async_hooks`, `record_error`.
- Acceptance: with `httpx.MockTransport` serving gzip bodies, `wire_bytes` equals the
  compressed length plus header estimate and `bytes_source == "measured"`; a hook that is
  monkeypatched to raise does not break the request; async variant same assertions;
  five-minute example from A§3.1 runs against MockTransport.
- Done when: pass; `examples/httpx_books/` draft exists (finalised in item 23).

### 17. requests adapter `[v0.1]`

- [ ] Scope: `HTTPAdapter` subclass recording in `build_response` and `send` (errors),
  Content-Length vs decoded labelling, `mount()`.
- Files: `integrations/requests.py`, `tests/integrations/test_requests.py`.
- Acceptance: with a local `http.server` serving gzip with and without Content-Length,
  `bytes_source` is `content_length` and `decoded` respectively; connection error recorded
  as `error` label; adapter failure does not break the request.
- Done when: pass.

### 18. Playwright hooks `[v0.1]`

- [ ] Scope: `attach` / `attach_async` on context or page: `response` and `requestfailed`
  listeners, `request.sizes()`, resource type mapping, document vs sub-resource, redirect
  chains, browser time accounting (`add_browser_seconds` from context open to close).
- Files: `integrations/playwright.py`, `tests/integrations/test_playwright.py` (marked
  `extras`, skipped without Playwright).
- Acceptance: against a local static server (Playwright `page.route` fulfilments and a
  `http.server` thread), 1 document + 3 images + 1 font produce 5 events with correct
  resource types and measured bytes; a failed request becomes an `error` event; listener
  exceptions are swallowed and counted.
- Done when: pass locally with Chromium (pre-installed in the sandbox) and in the `extras`
  job.

### 19. Scrapy extension, downloader middleware, item pipeline `[v0.1]`

- [ ] Scope: A§3.5 Scrapy settings; extension opens/closes the Watch per spider and exports
  `scrapewatch/*` stats (verdict, counts, cost); middleware at 900 measures compressed bytes,
  records retries via `request.meta["retry_times"]`, proxy label from
  `SCRAPEWATCH_PROXY_META_KEY`; pipeline cleans and checks items, optional drop of unusable
  items; close reason `scrapewatch_hold` is never used to stop the spider (monitoring only).
- Files: `integrations/scrapy/*.py`, `tests/integrations/test_scrapy.py` (marked `extras`).
- Acceptance: a spider run with `scrapy.utils.test.get_crawler` against a local gzip server
  through the full middleware chain: `wire_bytes` equals compressed size (this verifies the
  A§11.3 ordering claim; if it fails, switch to Content-Length and update A§11.3); retries
  linked; items get fixes; stats exported; middleware exception does not drop the response.
- Done when: pass. **M3 ends: full local matrix, log it.**

### 20. Crawlee for Python

- [ ] Scope: decide first (D20): read Crawlee's hook API for the installed version; if
  `pre_navigation_hook`/`post_navigation` give the response and byte counts for the httpx
  crawlers and the page for Playwright, implement `attach()` as an adapter over items 16/18;
  otherwise write the finding in docs/decisions.md and leave the stub with a clear
  `NotImplementedError` message. Not in v0.1.
- Acceptance: BeautifulSoupCrawler against a local server records events; Playwright
  crawler reuses item 18 tests.
- Done when: either path done and documented.

## M4 — waste cutter, examples, benchmarks

### 21. Waste cutter: Playwright route blocker and compression headers `[v0.1]`

- [ ] Scope: `install_route_blocker` (sync/async), `BlockPolicy`, tracker list (a short,
  documented list of ad/analytics hosts stored in `waste/trackers.txt`), allowlist wins,
  blocked requests counted with size estimates (A§11.3 table), `waste.compression_headers()`
  helper returning `Accept-Encoding: gzip, deflate, br` for HTTP clients, before/after
  numbers in the Run (`bytes_saved_estimate`, `blocked_requests`).
- Files: `waste/routes.py`, `waste/trackers.txt`, `waste/headers.py`, `tests/waste/`.
- Acceptance: local page with 3 images, 1 font, 1 tracker script, 1 allowlisted image → 4
  aborted, 1 allowed image loaded, counts and estimate match; policy with
  `resource_types: []` blocks only trackers.
- Done when: pass.

### 22. Waste cutter: conditional-request cache and in-run URL dedupe

- [ ] Scope: `ConditionalCache` over `cache_entries` (ETag, Last-Modified, body hash),
  helpers for httpx/requests to add conditional headers and observe responses, 304 and
  same-hash → `cache.unchanged`, records from unchanged pages marked so the engine does not
  count them as new (`Record` gets `unchanged: bool` in schema_version 1 as an optional
  field with default False); `RunDedupe` with canonical URLs; both report into
  `WasteBreakdown`.
- Acceptance: local server honouring If-None-Match: second run shows `conditional_hits`,
  `unchanged_refetch_*` zero; server ignoring conditionals but same body: unchanged by hash;
  duplicate URLs in one run deduped and counted.
- Done when: pass.

### 23. Three runnable examples on practice sites `[v0.1]`

- [ ] Scope: `examples/httpx_books/` (books.toscrape.com, watch + httpx + rules + gate),
  `examples/playwright_quotes/` (quotes.toscrape.com/js/, Playwright hooks + route blocker),
  `examples/scrapy_scrapethissite/` (scrapethissite.com/pages/simple/, extension +
  middleware + pipeline), `examples/any_language/` (a shell+curl scraper writing JSONL and
  `scrapewatch check`). Each has a README, a `rules.yml`, a `scrapewatch.yml` with a sample
  cost model, and a `run.sh`. Each is also a live check: one `checks/request.yml` run that
  executes all four and keeps their `summary.json` and reports as artifacts.
- Acceptance: the live-check results committed under `checks/results/<id>/` show verdict
  pass for all four and at least 1 record each; the examples' rules files load in a unit
  test (offline).
- Done when: results committed and linked from examples/README.md.

### 24. Benchmark run and docs/benchmarks.md `[v0.1]`

- [ ] Scope: `benchmarks/run_benchmark.py` executed through the checks workflow against
  books.toscrape.com, quotes.toscrape.com/js/, webscraper.io/test-sites/e-commerce/static,
  scrapethissite.com and two or three independent Shopify stores named in the request:
  (a) bytes before vs after the waste cutter for the Playwright targets; (b) guard results
  per site (all expected `ok`; any non-ok is investigated, not hidden); (c) cost per usable
  record under a sample cost model (direct $0, datacenter $0.8/GB, residential $8/GB) for the
  httpx targets; (d) the overhead perf numbers from `tests/perf` on the runner. Write
  docs/benchmarks.md with the tables, the exact `checks/results/<id>` paths, the date, the
  commit, and the cost model used; every number cites its result file.
- Acceptance: docs/benchmarks.md contains no number without a result path; the request file
  is back to `enabled: false`.
- Done when: committed. **M4 ends: full local matrix, log it.**

## v0.1 release

### 25. Packaging and release workflow (publishing off) `[v0.1]`

- [ ] Scope: `uv build` produces wheel and sdist with `py.typed`, the rules YAML and
  `trackers.txt` included; `twine check`; version `0.1.0` in `_version.py`; CHANGELOG;
  release.yml verified by a dry run on a `v0.1.0-rc1` tag with `PYPI_PUBLISH` unset (draft
  release with artifacts, no publish); `scrapewatch --version` from an installed wheel in a
  clean venv.
- Acceptance: a test unpacks the wheel and checks the data files are present; the rc tag
  workflow run is green and the draft release exists.
- Done when: done, and docs/publish-checklist.md items that can be ticked now are ticked.

### 26. README and animated visuals `[v0.1]`

- [ ] Scope: waits for the README rules from the owner (docs/readme-plan.md has the plan).
  Visuals: a terminal recording of `scrapewatch check` (pass → hold) rendered to GIF/SVG
  offline (e.g. with a scripted asciinema cast rendered by a local tool, committed under
  docs/images/), and the report screenshot from item 13. Numbers only from
  docs/benchmarks.md.
- Done when: the owner approves the README.

### 27. Private explainer (outside this repository)

- [x] Handled outside this repository: the owner keeps a plain-words explainer privately.
  Never commit `docs/private/` (it is git-ignored).

### 28. Release candidate checks `[v0.1]`

- [ ] Scope: full local matrix, gitleaks, pip-audit, `full.yml` dispatched once on GitHub,
  docs/incidents.md has at least the template, CHANGELOG `0.1.0` section, tag `v0.1.0`
  prepared but **not pushed** until the owner says "publish".
- Done when: logged below and reported to the owner.

---

## Milestone log

Record each full local matrix here (date, commit, Python versions, pass/fail per version,
gitleaks and pip-audit result). Example:

| Date | Milestone | Commit | 3.10 | 3.11 | 3.12 | 3.13 | gitleaks | pip-audit | Notes |
|---|---|---|---|---|---|---|---|---|---|
| (none yet) | | | | | | | | | |
