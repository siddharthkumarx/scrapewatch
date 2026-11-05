# Publish checklist

Run top to bottom when the owner says "publish". Nothing here happens before that.

## 1. Fresh public repository with a clean history

- [ ] All v0.1 items in docs/build-progress.md ticked; milestone log complete; CI green.
- [ ] Clone this private repo to a scratch directory and run `git filter-repo` to produce
  the public history:
  - drop `docs/private/` from every commit (`--path docs/private --invert-paths`);
  - drop `checks/results/` runs that were only exploratory (keep the ones cited in docs);
  - keep author identity as is (Signed-off-by lines stay).
- [ ] In `docs/maintainers.md`, trim the "Continue here"
  section and the branch names; keep the goal, hard rules, commands and CI split.
- [ ] Create the public repo `siddharthkumarx/scrapewatch` (empty, MIT, no README from the
  template), push `main` from the filtered clone, set `main` as default, protect it
  (require CI, no force-push).
- [ ] `git log --format=%B | grep -i -E "generated (with|by)|co-authored-by"` returns nothing.
- [ ] `gitleaks detect` on the public clone returns nothing.

## 2. Workflows

- [ ] `full.yml`: change `on:` to `push` (branches: main) and `pull_request`, keep
  `workflow_dispatch`.
- [ ] `ci.yml`: keep as the fast check on every push and pull request.
- [ ] `checks.yml`: keep, but restrict `branches` to `main` and feature branches of
  maintainers; document in checks/README.md that forks cannot commit results.
- [ ] `release.yml`: unchanged.

## 3. PyPI Trusted Publishing

- [ ] Owner creates the PyPI project `scrapewatch` (or a pending publisher) and adds a
  Trusted Publisher: owner `siddharthkumarx`, repo `scrapewatch`, workflow `release.yml`,
  environment `pypi`.
- [ ] GitHub: create the `pypi` environment with required reviewers = owner.
- [ ] Set the repository variable `PYPI_PUBLISH=true`.
- [ ] Tag `v0.1.0` on `main`, push the tag, approve the `publish` job, verify
  `pip install scrapewatch==0.1.0` in a clean venv and `scrapewatch --version`.
- [ ] Publish the draft GitHub release with the changelog section.

## 4. After publish

- [ ] README badges (PyPI version, CI) added; docs/benchmarks.md links verified.
- [ ] Open the first contributor issues from docs/readme-plan.md.
- [ ] Archive this private repo (`scrapewatch-wip`) read-only.
