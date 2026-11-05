"""Checks engine: schema, field, cross-field, duplicate and batch rules; verdict."""

from scrapewatch.checks.engine import CheckResult, check, check_records
from scrapewatch.checks.rules import DataRules, load_data_rules
from scrapewatch.checks.verdict import compute_verdict

__all__ = [
    "CheckResult",
    "DataRules",
    "check",
    "check_records",
    "compute_verdict",
    "load_data_rules",
]
