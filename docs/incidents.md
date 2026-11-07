# Incidents

Anything that went wrong while building or running scrapewatch: a flaky test, a wrong
assumption in the architecture, a CI outage, a benchmark that had to be rerun, a site that
started answering differently. One entry per incident, newest first. Keep it factual; this
file becomes public.

## Template

```
## YYYY-MM-DD — short title

- Item: build-progress item N (or "runtime", "CI", "benchmark")
- Symptom: what was observed, with the command or the result path
- Cause: what was actually wrong
- Fix: commit(s) and what changed; doc updated? (architecture §, decisions D)
- Prevention: test added, check added, or "none possible because ..."
- Minutes spent (Actions): N, if a workflow ran
```

## Entries

## 2026-11-07 — Crawlee's Playwright crawler could not start Chromium on CI

- Item: build-progress item 20 (CI)
- Symptom: the crawlee extras job of full.yml run 42364705224 failed
  `test_playwright_crawler` with no events; the log shows Chromium's "No usable sandbox!".
- Cause: Crawlee launches Chromium with its sandbox on (plain Playwright leaves it off), and
  GitHub's Ubuntu runners block the unprivileged user namespaces it needs. The test turned the
  sandbox off only when running as root, which covered the local sandbox but not CI.
- Fix: the test always passes `chromium_sandbox=False`.
- Prevention: none possible locally (the container runs as root); the extras job is the check.
- Minutes spent (Actions): one full.yml run.

## 2026-11-07 — mypy red on CI after the Crawlee adapter

- Item: build-progress item 20 (CI)
- Symptom: ci.yml run 42364557356 and the four matrix jobs of full.yml run 42364557470
  failed in `mypy`: `Untyped decorator makes function "handler" untyped` in
  `tests/integrations/test_crawlee.py`.
- Cause: CI type-checks without the crawlee extra, where Crawlee's router decorator is `Any`;
  locally all extras were installed, so the decorator was typed and mypy passed. The same
  trap was avoided for Scrapy in item 19 but mypy was not re-run without extras here.
- Fix: `# type: ignore[untyped-decorator, unused-ignore]` on the two handlers (green with and
  without extras).
- Prevention: before pushing a change to an integration test, run mypy in both environments
  (`uv sync --group dev --extra httpx` and `--all-extras`); noted in docs/maintainers.md.
- Minutes spent (Actions): one ci.yml and one full.yml run.

## 2026-11-07 — streamed responses closed unread were labelled empty

- Item: build-progress item 16 (found while starting item 17)
- Symptom: an httpx response opened with `client.stream(...)` and closed without reading the
  body was recorded with no body; the guard rule `generic.empty.tiny` matched (`size 0 <
  2048`), so a normal page counted as a bad document. The item 16 test checked bytes, not
  the label.
- Cause: the runtime treated "body not seen" the same as "body empty".
- Fix: a `body_seen` feature (not serialized; default true). Integrations pass
  `meta={"body_seen": False}` when they never read the body, and body-derived signals then
  never match; status, header, cookie, URL and content-type rules still apply. Architecture
  2.1 and 6.1 updated. Size baselines already ignore events with no body bytes.
- Prevention: the httpx streaming test asserts the label; a guard test covers `body_seen`.
- Minutes spent (Actions): 0 (found locally).

## 2026-11-06 — extras tests outside tests/integrations never ran in CI

- Item: build-progress item 12 (CI)
- Symptom: the job list of full.yml run 42264389001 showed every extras job skipping its test
  step; the Parquet reader tests (marked `extras`, in `tests/io/` and `tests/cli/`) are
  skipped in the main matrix, which has no pyarrow, so nothing ran them.
- Cause: the extras jobs only ran `tests/integrations/`, which does not exist before item 16.
- Fix: the extras jobs now run `pytest -m "extras and not live" -k <extra> --no-cov` over the
  whole test tree; exit 5 (nothing selected) still passes.
- Prevention: a test that needs an extra must be marked `extras` and have the extra's name in
  its id (file or function name); noted in docs/maintainers.md.
- Minutes spent (Actions): 0 extra (found by reading the job list).
