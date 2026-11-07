# Live checks

The development sandbox cannot reach most websites. Anything that needs the network
(guard checks on practice sites, benchmarks, integration smoke runs) runs through
`.github/workflows/checks.yml`:

1. Edit `checks/request.yml`: set `enabled: true`, a unique `id` (date + slug), the question
   the run answers, the commands and the artifacts to keep (paths or globs relative to the
   repository root; each file keeps its relative path under `checks/results/<id>/`).
2. Commit and push on `main`. The workflow runs the commands and commits
   `checks/results/<id>/` (log.txt, meta.json, artifacts) back to `main`.
3. `git pull` to read the results. Cite them by path in docs (every published number must
   come from a committed result).
4. Set `enabled: false` again in the next commit that touches the file.

Rules: no secrets in the request, only sites that are fine to name publicly
(docs/architecture.md "Benchmarks and sites"), and only run when the result answers a
question. Runs are free on this public repository, but each one adds a commit. Forks
cannot commit results: the workflow runs only on `main` of this repository.
