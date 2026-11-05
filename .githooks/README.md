# Git hooks

Enable once per clone:

```bash
git config core.hooksPath .githooks
```

`commit-msg` runs `scripts/commit_policy.py`: subject up to 72 characters, only `Signed-off-by`
trailers (use `git commit -s`), no "Generated with/by" footers. CI runs the same script on every
pushed commit, so a bypassed hook still fails the build.
