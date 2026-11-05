"""Verdict computation. Table of rules and defaults: docs/architecture.md "Verdict logic"."""

from __future__ import annotations

from collections.abc import Iterable

from scrapewatch.checks.rules import VerdictThresholds
from scrapewatch.models import Finding, Run, Verdict


def compute_verdict(
    run: Run,
    findings: Iterable[Finding],
    thresholds: VerdictThresholds,
    *,
    checks_failed: bool = False,
) -> Verdict:
    """Fold findings and run counters into pass/warn/hold with plain-words reasons.

    ``checks_failed=True`` (the engine crashed) always yields hold: the gate fails closed.
    """
    raise NotImplementedError("build-progress item 8")
