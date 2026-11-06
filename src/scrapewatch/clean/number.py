"""number normalizer (docs/architecture.md 10.2).

Turns a number written as text into a number; flags estimates, ranges, multipliers (unless
``allow_suffix``), ambiguous separators and anything with words or units around it.

Options: ``locale`` (auto | us | eu), ``allow_suffix`` (k, K = thousand, M = million,
B and bn = billion), ``integer`` (the result must be a whole number and comes back as int).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from scrapewatch.clean._numeric import (
    APPROXIMATE,
    RANGE,
    SIGN,
    ParseError,
    finite_number,
    locale_option,
    parse_amount,
    parse_exponent,
    squash_spaces,
    to_output,
)
from scrapewatch.clean.pipeline import CleanOutcome, register

_SUFFIX = re.compile(r"(?<=[0-9])\s*(k|K|M|B|bn)$")
_MULTIPLIER = {"k": 3, "K": 3, "M": 6, "B": 9, "bn": 9}


def _flag(value: Any, flag: str, message: str) -> CleanOutcome:
    """A flagged outcome: the value stays as it was."""
    return CleanOutcome(value=value, flag=flag, flag_message=message)


@register("number")
def normalize_number(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """Fix a numeric string to a number or flag it."""
    locale = locale_option(options)
    if finite_number(value):
        return CleanOutcome(value=value)
    if not isinstance(value, str):
        return _flag(value, "non_numeric", f"{value!r} is not a number")
    text = squash_spaces(value)
    if APPROXIMATE.search(text):
        return _flag(value, "approximate", f"{value!r} is a bound or an estimate, not one number")
    if RANGE.search(text):
        return _flag(value, "range", f"{value!r} is a range, not one number")
    negative = False
    m = SIGN.match(text)
    if m:
        negative = m.group(1) != "+"
        text = text[m.end() :]
    exponent = 0
    m = _SUFFIX.search(text)
    if m:
        if not options.get("allow_suffix"):
            return _flag(
                value, "suffix", f"{value!r} uses a {m.group(1)} multiplier; set allow_suffix"
            )
        exponent = _MULTIPLIER[m.group(1)]
        text = text[: m.start()]
    try:
        amount = parse_exponent(text)
        if amount is None:
            amount = parse_amount(text, locale, shown=value)
    except ParseError as exc:
        return _flag(value, exc.flag, exc.message)
    amount = amount.scaleb(exponent)
    if negative:
        amount = -amount
    if options.get("integer") and amount != amount.to_integral_value():
        return _flag(value, "not_integer", f"{value!r} is not a whole number")
    return CleanOutcome(value=to_output(amount, options), changed=True)
