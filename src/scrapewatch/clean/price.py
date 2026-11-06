"""price normalizer (docs/architecture.md 10.1).

Turns a price string into a number and its currency into ``extra``; flags anything with
more than one reading (separator, currency), ranges, estimates, negatives and text.

Options: ``locale`` (auto | us | eu), ``currency_default`` (used when the value names no
currency, to settle a shared symbol such as ``kr``, or as the currency of a bare ``$``), and
``allow_free`` ("Free" becomes 0).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from scrapewatch.clean import _currency as cur
from scrapewatch.clean._numeric import (
    APPROXIMATE,
    RANGE,
    SIGN,
    ParseError,
    finite_number,
    locale_option,
    parse_amount,
    squash_spaces,
    to_output,
)
from scrapewatch.clean.pipeline import CleanOutcome, register

_FREE = re.compile(r"(?:free|gratis)!?", re.IGNORECASE)
_SUFFIX = re.compile(r"[0-9]\s*(?:k|K|M|B|bn)$")


def _flag(value: Any, flag: str, message: str) -> CleanOutcome:
    """A flagged outcome: the value stays as it was."""
    return CleanOutcome(value=value, flag=flag, flag_message=message)


def _currency(tokens: list[str], default: str | None, text: str) -> str | None:
    """The one currency the tokens name, or raise ``ambiguous_currency``."""
    if not tokens:
        return default
    readings = []
    for token in tokens:
        options = set(cur.candidates(token) or ())
        if token == cur.BARE_DOLLAR_SIGN and default in cur.BARE_DOLLAR:
            options = {str(default)}
        readings.append(options)
    names = set.intersection(*readings)
    if len(names) == 1:
        return names.pop()
    if default is not None and default in names:
        return default
    choices = " or ".join(sorted(names)) if names else "different currencies"
    raise ParseError("ambiguous_currency", f"{text!r} could be {choices}; set currency_default")


def _peel(text: str) -> tuple[str, list[str], bool]:
    """Remove a leading and a trailing currency and the sign; return (rest, tokens, negative)."""
    tokens: list[str] = []
    negative = False
    rest = text
    for _ in range(2):  # sign and currency in either order: "-$5", "$-5", "- 5 €"
        m = SIGN.match(rest)
        if m:
            negative = negative or m.group(1) != "+"
            rest = rest[m.end() :]
        m = cur.LEADING.match(rest)
        if m and not tokens and cur.candidates(m.group("sym") or m.group("code")):
            tokens.append(m.group("sym") or m.group("code"))
            rest = rest[m.end() :]
    m = cur.TRAILING.search(rest)
    if m and cur.candidates(m.group("sym") or m.group("code")):
        tokens.append(m.group("sym") or m.group("code"))
        rest = rest[: m.start()]
    return rest.strip(), tokens, negative


@register("price")
def normalize_price(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """Fix a price string to a number (currency in ``extra``) or flag it."""
    locale = locale_option(options)
    default = options.get("currency_default")
    if default is not None and default not in cur.ISO_4217:
        raise ValueError(f"currency_default must be an ISO 4217 code, not {default!r}")
    if finite_number(value):
        return CleanOutcome(value=value)
    if not isinstance(value, str):
        return _flag(value, "non_numeric", f"{value!r} is not a price")
    text = squash_spaces(value)
    if _FREE.fullmatch(text):
        if options.get("allow_free"):
            extra = {"currency": default} if default else {}
            return CleanOutcome(value=to_output(Decimal(0), options), changed=True, extra=extra)
        return _flag(
            value, "non_numeric", f"{value!r} is not a number; set allow_free to read it as 0"
        )
    if APPROXIMATE.search(text):
        return _flag(value, "approximate", f"{value!r} is a bound or an estimate, not one price")
    if RANGE.search(text):
        return _flag(value, "range", f"{value!r} is a price range, not one price")
    rest, tokens, negative = _peel(text)
    if _SUFFIX.search(rest):
        return _flag(
            value, "suffix", f"{value!r} uses a k/M/B multiplier; prices are written in full"
        )
    try:
        currency = _currency(tokens, default, value)
        whole_units = currency in cur.WHOLE_UNITS
        amount = parse_amount(rest, locale, shown=value, whole_units=whole_units)
    except ParseError as exc:
        return _flag(value, exc.flag, exc.message)
    if negative and amount != 0:
        return _flag(value, "negative", f"{value!r} is negative")
    extra = {"currency": currency} if currency else {}
    return CleanOutcome(value=to_output(amount, options), changed=True, extra=extra)
