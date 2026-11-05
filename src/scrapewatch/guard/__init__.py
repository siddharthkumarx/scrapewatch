"""Response guard.

Classifies responses as ok, block, challenge, captcha, consent, login, rate_limit, empty,
redirect, error_page or error. Detection only; never bypass.
"""

from scrapewatch.guard.classify import classify
from scrapewatch.guard.features import Features, extract_features
from scrapewatch.guard.rules import GuardRule, GuardRuleSet, load_rules

__all__ = ["Features", "GuardRule", "GuardRuleSet", "classify", "extract_features", "load_rules"]
