"""scrapewatch: health and cost monitor for any web scraper.

Public API (see docs/architecture.md "Public API"):

- :func:`watch` / :class:`Watch` — the run lifecycle every integration uses.
- :func:`check` — check a file of records against a rules file (what the CLI runs).
- :class:`PublishGate` — staging and atomic promotion of outputs.
- :func:`classify` — classify one response with the guard rules.
- :mod:`scrapewatch.clean` — normalizers.
- :mod:`scrapewatch.models` — the data model.
"""

from scrapewatch._version import __version__
from scrapewatch.checks.engine import check
from scrapewatch.gate import PublishGate
from scrapewatch.guard.classify import classify
from scrapewatch.models import (
    Finding,
    GuardLabel,
    GuardResult,
    Record,
    ResponseEvent,
    Run,
    Severity,
    Verdict,
    VerdictStatus,
)
from scrapewatch.runtime import Watch, watch

__all__ = [
    "Finding",
    "GuardLabel",
    "GuardResult",
    "PublishGate",
    "Record",
    "ResponseEvent",
    "Run",
    "Severity",
    "Verdict",
    "VerdictStatus",
    "Watch",
    "__version__",
    "check",
    "classify",
    "watch",
]
