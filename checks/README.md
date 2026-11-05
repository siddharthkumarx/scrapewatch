# Live checks

The sandbox the builder works in cannot reach most websites. Anything that needs the network
(guard checks on practice sites, benchmarks, integration smoke runs) runs through
`.github/workflows/checks.yml`:

1. Edit `checks/request.yml`: set `enabled: true`, a unique `id` (date + slug), the question
   the run answers, the commands and the artifacts to keep.
2. Commit and push on the working branch. The workflow runs the commands and commits
   `checks/results/<id>/` (log.txt, meta.json, artifacts) back to the same branch.
3. `git pull` to read the results. Cite them by path in docs (every published number must
   come from a committed result).
4. Set `enabled: false` again in the next commit that touches the file.

Rules: no secrets in the request, only sites that are fine to name publicly
(docs/architecture.md "Benchmarks and sites"), and only run when the result answers a
question. Private Actions minutes are shared.
