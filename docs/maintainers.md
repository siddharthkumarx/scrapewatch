# Maintainer guide — scrapewatch

You are building `scrapewatch` item by item from docs/build-progress.md. The architecture
contract is docs/architecture.md; the reasons are docs/decisions.md. Do not redesign; if the
contract is wrong, change the doc and the code in the same commit and say why.

## Goal

An open-source Python library and CLI (MIT, PyPI) that is the health and cost monitor for any
scraper: response guard (block, captcha, consent, login, rate-limit, empty shell, bad
redirect, error page with 200), data checks against schema and history with a pass/warn/hold
verdict, a publish gate that keeps the last good output live, fix-or-flag cleaning, a cost
meter (cost per usable record and waste), an opt-in waste cutter, static HTML reports, alerts,
and integrations for httpx, requests, Playwright, Scrapy and Crawlee, plus a framework-free
CLI. Production-grade, adoptable in 5 minutes, every number in docs from a committed run.

## Hard rules

- Detection and measurement only. No anti-bot bypass, no CAPTCHA solving, never switch IPs
  or proxies because of a block (the proxy label is recorded for cost only), no advice on
  evasion anywhere (code, rules, docs, comments, commit messages).
- No personal data. No telemetry or phoning home of any kind.
- This repository is public: every push is visible at once. Never commit private notes
  (`docs/private/` is git-ignored), credentials or tokens.
- Never use large marketplaces or login-protected sites (Amazon, Walmart, eBay, Etsy,
  LinkedIn, Indeed, Glassdoor, Zillow, Google) in examples, fixtures, benchmarks or docs.
  Allowed: books.toscrape.com, quotes.toscrape.com, webscraper.io test sites,
  scrapethissite.com, a few independent Shopify stores, open-data and docs sites.
- Guard fixtures store features only (headers, cookie names, title, markers ≤ 200 chars),
  never full pages. No copied code from surveyed projects; respect licences.
- Monitoring never crashes or slows the user's scraper (hooks fail open and count their own
  errors). The publish gate fails closed.
- No bodies stored; cookies as names only; secret-looking headers and query values redacted.
- Every number in README or docs comes from a committed `checks/results/<id>/` run or the
  perf test log, cited by path.

## Git rules

- Identity (already set in this clone; set again in a fresh clone):
  `git config user.name "Siddharth Kumar"` and
  `git config user.email 273157498+siddharthkumarx@users.noreply.github.com`.
- `git config core.hooksPath .githooks` once per clone. Commit with `git commit -s`.
- Commit messages: a subject (≤ 72 chars), a body, and no trailers except `Signed-off-by`.
  No "Generated with" footers and no other authorship lines.
  `scripts/commit_policy.py` enforces this locally and in CI.
- Branch: `main`. Push with `git push -u origin main`.
  Never open a pull request. Never push to another branch. Never force-push.
- Small, meaningful commits, one item or sub-step each.

## Commands

```bash
# setup (Python 3.12 is the CI version)
uv sync --python 3.12 --group dev --extra httpx
git config core.hooksPath .githooks

# test, lint, typecheck (all must be green before every commit)
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy

# everything an extra needs (Chromium is pre-installed in the sandbox; do not run
# "playwright install" again there)
uv sync --python 3.12 --group dev --all-extras

# full local matrix (optional: full.yml runs the same on every push to main)
uv python install 3.10 3.11 3.12 3.13
for v in 3.10 3.11 3.12 3.13; do
  uv sync --python $v --group dev --all-extras && uv run --python $v pytest -m "not live" || echo "FAILED $v"
done
uv export --no-emit-project --all-extras --group dev --no-hashes -q > /tmp/requirements.txt
uv run pip-audit --strict --disable-pip --no-deps -r /tmp/requirements.txt
gitleaks git --no-banner .   # install: https://github.com/gitleaks/gitleaks/releases

# a live check (the sandbox cannot reach most sites; run it through GitHub Actions)
#   1. edit checks/request.yml: enabled: true, unique id, question, commands, artifacts
#   2. git commit -s -m "checks: request <id>" && git push -u origin main
#   3. wait for the "checks" workflow, then: git pull origin main
#   4. read checks/results/<id>/{log.txt,meta.json,...}; set enabled: false in the next commit
```

Use `uv run` for everything so the locked environment is used. The `UV_NATIVE_TLS`
variable in this sandbox is deprecated; export `UV_SYSTEM_CERTS=1` if uv warns.

## CI (public repository: Actions run free)

- `ci.yml` on every push and pull request: one job, ubuntu-latest, Python 3.12:
  commit-message policy, ruff, mypy, pytest. Concurrency cancels older runs per branch;
  docs-only pushes (`docs/**`, `**/*.md`, `checks/results/**`) skip it; uv cache enabled.
- `full.yml` on every push to main and on pull requests (docs-only pushes skip it), plus
  workflow_dispatch: 3.10–3.13 matrix, extras jobs (requests, playwright, scrapy, crawlee,
  parquet), gitleaks, pip-audit.
- `checks.yml` on pushes to main that change `checks/request.yml`: runs the request and
  commits results under `checks/results/<id>/`. Free, but every run adds a commit: use it
  only when the run answers a question.
- `release.yml` on `v*` tags: build, twine check, draft GitHub release. PyPI publishing stays
  off (`PYPI_PUBLISH` repo variable unset) until the owner says "publish".

## Working method

1. Open docs/build-progress.md, take the first unticked item, read its A§ references.
2. Write the tests and golden fixtures first, then the implementation, then run the three
   commands above.
3. Replace the stub's `raise NotImplementedError`, raise `fail_under` in pyproject by the
   amount the item says, update docs if a signature changed.
4. Tick the item, commit, push, confirm `ci.yml` and `full.yml` are green on GitHub before the
   next item.
5. Before each milestone: confirm the latest `full.yml` run on main is green (or run the full
   local matrix, pip-audit and gitleaks) and log the result.
6. Record anything that went wrong in docs/incidents.md.

If an item cannot be completed as written, do not shrink it silently: finish what is
possible, write what was left and why in the item's notes, and move on only if the next item
does not depend on it.

## Continue here

Next item: **docs/build-progress.md item 16** (httpx integration). M1, M2 (items 1 to 14) and
the post-M2 report fixes (908d3ef) are done and green on GitHub. Item 15 (alerts, ef18d7e) is
done and green locally; first confirm its ci.yml run 42271244740 and full.yml run
42271244600 are green (fix and log in docs/incidents.md if not), then start item 16.

Notes for the next session:

- Local gate: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run
  pytest -m "not live"`. Export `UV_SYSTEM_CERTS=1` and unset `UV_NATIVE_TLS`. The sandbox
  cannot download the gitleaks binary; use the gitleaks job of full.yml as the evidence.
- `tests/conftest.py` runs every test in its own temporary working directory and clears
  `SCRAPEWATCH_*` variables, so default history paths never touch the repository.
- Runtime structure (`runtime.py`): response hooks `guard` then `cost`, record hook `checks`;
  each hook fails open (counted in `monitor_errors`). Records are never kept: `BatchStats`
  (`checks/batch.py`) accumulates counts, field stats, value hashes and currency mismatches.
  `finish()` order: flush, aggregate (workers), cost, status, verdict (fails closed), overhead,
  report (HTML and summary.json), store, alerts, prune, close.
- Normalizers: all done (items 10 and 11). Shared number parsing in `clean/_numeric.py`,
  currency table in `clean/_currency.py`. The pipeline passes the field type as the reserved
  option `_field_type`; a normalizer's `note` becomes an info finding. Record finding and fix
  examples are on `Run.samples` (A§2.4), filled by `checks/batch.SampleKeeper`.
- A normalizer's `extra` (the currency of a price) fills an empty field with a Fix, and a
  conflicting non-empty value gives a `clean.<name>.extra_conflict` info finding that
  `batch.currency` counts.
- Still open for later items: write `tests/perf/test_overhead.py` (architecture 15) before item 24 (only the 64 KiB classify
  budget is tested so far, in `tests/guard/test_classify.py`).
- Tests that need an extra are marked `extras` and carry the extra's name in their id; the
  extras jobs of full.yml run `-m "extras and not live" -k <extra>` without coverage.
- full.yml installs Chromium only once `tests/integrations/` exists; from item 16 on the
  extras jobs take several minutes.
