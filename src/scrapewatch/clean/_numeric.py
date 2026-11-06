"""Shared number parsing for the price and number normalizers (architecture 10.1, 10.2).

``parse_amount`` reads the digits-and-separators part of a value and decides which separator
is the decimal point. It returns a reading only when there is exactly one; otherwise a flag:

- two kinds of separator: the last one is the decimal point (``1.299,00``, ``1 234,5``);
- one separator kind used more than once: thousands (``1,299,000``; Indian ``1,29,999``);
- one ``,`` or ``.`` used once: decimal unless it is followed by exactly three digits after a
  one-to-three digit group (``1,299``), which is ``ambiguous_separator`` unless ``locale`` is
  ``us`` (``,`` groups thousands) or ``eu`` (``.`` groups thousands);
- spaces and apostrophes only ever group thousands (``1 299``, ``1'299.50``);
- a trailing ``,-`` or ``.-`` means no fraction (``12,-``).

Decimal is used throughout so values convert to float once, at the end.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

LOCALES = ("auto", "us", "eu")

_SPACES = re.compile(r"[\s\u00a0\u2007\u2009\u202f]+")
_SHAPE = re.compile(r"[0-9]+(?:[.,' ][0-9]+)*|[.,][0-9]+")
_NO_FRACTION = re.compile(r"[.,][-\u2013\u2014]$")
_EXPONENT = re.compile(r"[0-9]+(?:\.[0-9]+)?[eE][-+]?[0-9]+")

# Leading words and signs that make a value a bound or an estimate, and a trailing "+".
APPROXIMATE = re.compile(
    r"^(?:~|≈|≃|<=?|>=?|≤|≥|ca\.?\s|circa\s|approx(?:\.|imately)?\s?|about\s|around\s|"
    r"up\s+to\s|from\s|starting\s+(?:at|from)\s|over\s|under\s|at\s+least\s|at\s+most\s)"
    r"|\+$",
    re.IGNORECASE,
)
# Two numbers joined by a dash or "to": a range, never one value.
RANGE = re.compile(r"[0-9]\s*(?:[-\u2013\u2014]|\bto\b|\.\.)\s*\D{0,4}[0-9]")
SIGN = re.compile(r"^([-+\u2212])\s*")


class ParseError(Exception):
    """A value with no reading or more than one; ``flag`` names the finding suffix."""

    def __init__(self, flag: str, message: str) -> None:
        super().__init__(message)
        self.flag = flag
        self.message = message


def squash_spaces(text: str) -> str:
    """Strip, and turn every run of whitespace (NBSP and thin spaces included) into one space."""
    return _SPACES.sub(" ", text).strip()


def locale_option(options: Mapping[str, Any]) -> str:
    """The ``locale`` option, validated."""
    locale = options.get("locale") or "auto"
    if locale not in LOCALES:
        raise ValueError(f"locale must be one of {', '.join(LOCALES)}, not {locale!r}")
    return str(locale)


def _grouped(groups: list[str], *, indian: bool) -> bool:
    """True when the integer groups are a valid thousands grouping."""
    head, rest = groups[0], groups[1:]
    if not rest or not 1 <= len(head) <= 3 or head.startswith("0"):
        return False
    if all(len(g) == 3 for g in rest):
        return True
    # Indian grouping: 1,29,999 and 12,34,56,789 (pairs, then a final group of three).
    return (
        indian
        and len(head) <= 2
        and len(rest) >= 2
        and all(len(g) == 2 for g in rest[:-1])
        and len(rest[-1]) == 3
    )


def _integer(part: str, thousands: str | None, text: str) -> str:
    """Digits of an integer part written with ``thousands`` separators (or none)."""
    if thousands is None:
        return part
    groups = part.split(thousands)
    if not _grouped(groups, indian=thousands == ","):
        raise ParseError("non_numeric", f"{text!r}: digit groups do not match a separator")
    return "".join(groups)


def parse_amount(
    text: str, locale: str, *, shown: str | None = None, whole_units: bool = False
) -> Decimal:
    """Parse digits with thousands and decimal separators; no sign, currency or suffix.

    ``shown`` is the value as the user wrote it (for messages). ``whole_units`` says the
    currency has no minor unit (JPY, KRW, ...), so ``1,000`` and ``1.000`` group thousands.
    """
    original = text if shown is None else shown
    text = text.replace("\u2019", "'")
    forced_decimal: str | None = None
    if _NO_FRACTION.search(text):
        forced_decimal, text = text[-2], text[:-2]
    if not _SHAPE.fullmatch(text):
        raise ParseError("non_numeric", f"{original!r} is not a number")
    seps = [c for c in text if c in ".,' "]
    if forced_decimal is not None:
        if forced_decimal in seps:
            raise ParseError("non_numeric", f"{original!r}: decimal separator used twice")
        if len(set(seps)) > 1:
            raise ParseError("non_numeric", f"{original!r}: mixed thousands separators")
        return Decimal(_integer(text, seps[0] if seps else None, original))
    if not seps:
        return Decimal(text)
    if text[0] in ".,":  # ".5"
        return Decimal("0." + text[1:])
    kinds = list(dict.fromkeys(seps))
    if len(kinds) >= 2:
        decimal = seps[-1]
        thousands = [k for k in kinds if k != decimal]
        if decimal in " '" or seps.count(decimal) != 1 or len(thousands) != 1:
            raise ParseError("non_numeric", f"{original!r}: separators do not form a number")
        whole, frac = text.rsplit(decimal, 1)
        return Decimal(_integer(whole, thousands[0], original) + "." + frac)
    sep = kinds[0]
    if sep in " '" or seps.count(sep) > 1:
        return Decimal(_integer(text, sep, original))
    whole, frac = text.split(sep)
    could_group = len(frac) == 3 and 1 <= len(whole) <= 3 and not whole.startswith("0")
    if not could_group:
        return Decimal(whole + "." + frac)
    if whole_units:
        return Decimal(whole + frac)
    if locale == "us":
        return Decimal(whole + frac) if sep == "," else Decimal(whole + "." + frac)
    if locale == "eu":
        return Decimal(whole + frac) if sep == "." else Decimal(whole + "." + frac)
    raise ParseError(
        "ambiguous_separator",
        f"{original!r} could be {whole}{frac} or {whole}.{frac}; "
        "set clean_options locale to us or eu",
    )


def parse_exponent(text: str) -> Decimal | None:
    """``1.5e3`` style values, or None."""
    if not _EXPONENT.fullmatch(text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:  # pragma: no cover - the regex admits only valid literals
        return None


def wants_integer(options: Mapping[str, Any]) -> bool:
    """True when the field is an integer field or the ``integer`` option is set."""
    return bool(options.get("integer")) or options.get("_field_type") == "integer"


def to_output(value: Decimal, options: Mapping[str, Any]) -> int | float:
    """Float, or int when integral and the field wants integers."""
    if value == 0:
        value = Decimal(0)  # no negative zero
    if wants_integer(options) and value == value.to_integral_value():
        return int(value)
    return float(value)


def finite_number(value: Any) -> bool:
    """True for int and finite float values (bool is not a number here)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)
