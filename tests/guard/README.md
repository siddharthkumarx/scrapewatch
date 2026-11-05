# Guard fixtures

One YAML file per case: `tests/guard/fixtures/<rule id>[.<variant>].yml`. Format and the
contributor workflow are in docs/architecture.md "Guard rules YAML" (section "Per-rule tests").
`tests/guard/test_rules.py` (build-progress item 4) parametrises over every file and fails
when a rule in the rules file has no fixture.

Fixtures hold features only (status, headers, cookie names, title, markers of at most 200
characters, sizes), never a page body.
