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
