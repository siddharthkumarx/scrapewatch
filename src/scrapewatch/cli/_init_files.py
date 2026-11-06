"""Example files written by ``scrapewatch init`` (they load through the real loaders)."""

RULES_YML = """\
# Data rules for scrapewatch: what a good record looks like.
# Reference: docs/architecture.md section 7. Unknown keys are errors, so typos fail early.
schema_version: 1
name: books                  # run name; history and baselines are kept per name
key: [url]                   # fields that identify a record (for duplicates)
examples: 3                  # example values kept per finding in the report (0 keeps none)
fields:
  title:
    type: string
    required: true
    min_length: 1
    clean: [strip_tags, html_entities, whitespace]
  price:
    type: number
    required: true
    min: 0
    max: 100000
    clean: [price]           # "£51.77" -> 51.77 and currency GBP
    clean_options: {locale: auto}
    severity: hold           # a bad price stops the run from going live
  currency:
    type: string
    allowed: [GBP, USD, EUR]
  url:
    type: url
    required: true
    clean: [url]             # drops utm_* and other tracking parameters
batch:
  row_count: {warn_pct: 20, hold_pct: 50, min_rows: 10}
  coverage: {warn_drop_points: 5, hold_drop_points: 20}
  duplicates_warn_pct: 1
verdict:
  blocked_share_warn_pct: 2
  blocked_share_hold_pct: 20
"""

CONFIG_YML = """\
# scrapewatch settings. Every key is optional; these are the defaults unless noted.
# Reference: docs/architecture.md section 5. Environment variables override this file,
# e.g. SCRAPEWATCH_HISTORY__PATH=/data/scrapewatch.
name: books
rules: rules.yml               # relative to this file
history:
  path: .scrapewatch           # history.sqlite and reports/ live here
  keep_runs: 50
  keep_event_days: 30
  baseline_runs: 7
cost:
  currency: USD
  default_proxy: direct
  proxies:                     # price per GB downloaded, by the proxy label you pass
    direct: {per_gb: 0}
    datacenter: {per_gb: 0.8}  # example prices; use your provider's
    residential: {per_gb: 8.0}
  browser:
    per_minute: 0.0
guard:
  enabled: true
alerts:
  on: [hold]                   # credentials come from environment variables only
store_events: true
"""
