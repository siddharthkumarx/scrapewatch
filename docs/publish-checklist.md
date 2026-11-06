# Publish checklist

Sections 1 and 2 were done on 2026-11-06, when development moved to this public repository.
Sections 3 and 4 run when the owner says "publish".

## 1. Public repository with a clean history

- [x] History rewritten with `git filter-repo` before the repository went public: maintainer
  guide at `docs/maintainers.md`, branch `main`, unsigned commits, author identity kept.
- [x] `main` is the only branch and the default branch.
- [ ] Rename the repository to `scrapewatch` if it still has its working name (owner,
  Settings → General); GitHub redirects the old URLs.
- [ ] Protect `main` once the build is stable (require CI, no force-push).
- [ ] Before the release: `git log --format=%B | grep -i -E "generated (with|by)|co-authored-by"`
  returns nothing, and `gitleaks detect` on a fresh clone returns nothing.

## 2. Workflows

- [x] `full.yml`: runs on push to `main`, on pull requests and on demand.
- [x] `ci.yml`: the fast check on every push and pull request.
- [x] `checks.yml`: runs on `main` only; forks cannot commit results (checks/README.md).
- [ ] `release.yml`: unchanged until the first release.

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
