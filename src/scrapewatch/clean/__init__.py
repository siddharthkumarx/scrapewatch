"""Fix-or-flag normalizers. Every fix is recorded as a Fix; anything ambiguous is a Finding.

Registry: ``NORMALIZERS`` maps the names used in rules.yml ``clean:`` lists to callables.
"""

from scrapewatch.clean.pipeline import NORMALIZERS, CleanOutcome, apply_normalizers

__all__ = ["NORMALIZERS", "CleanOutcome", "apply_normalizers"]
