"""Currency symbols and ISO 4217 codes the price normalizer recognises (architecture 10.1).

The table is deliberately small and explicit: a symbol maps to one currency, or to several
when it is shared (``kr``), and then the price normalizer flags ``ambiguous_currency`` unless
the field's ``currency_default`` is one of the candidates. A bare ``$`` is USD, or the
``currency_default`` when that is a currency written with a bare ``$``.
"""

from __future__ import annotations

import re

# Active ISO 4217 codes (funds, precious metals and test codes left out).
ISO_4217: frozenset[str] = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN
    BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR
    FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD
    JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT
    MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG
    QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB
    TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VED VES VND VUV WST XAF XCD XCG XOF
    XPF YER ZAR ZMW ZWG ZWL
    """.split()  # noqa: SIM905 - a block of codes is easier to review than a list
)

# Symbol (as written) -> candidate currencies. One candidate = unambiguous.
SYMBOLS: dict[str, tuple[str, ...]] = {
    "US$": ("USD",),
    "A$": ("AUD",),
    "AU$": ("AUD",),
    "C$": ("CAD",),
    "CA$": ("CAD",),
    "NZ$": ("NZD",),
    "S$": ("SGD",),
    "HK$": ("HKD",),
    "MX$": ("MXN",),
    "R$": ("BRL",),
    "$": ("USD",),
    "€": ("EUR",),
    "£": ("GBP",),
    "¥": ("JPY",),
    "₹": ("INR",),
    "Rs.": ("INR",),
    "Rs": ("INR",),
    "₩": ("KRW",),
    "₽": ("RUB",),
    "₺": ("TRY",),
    "₫": ("VND",),
    "₪": ("ILS",),
    "zł": ("PLN",),
    "kr.": ("SEK", "NOK", "DKK"),
    "kr": ("SEK", "NOK", "DKK"),
    "Kr.": ("SEK", "NOK", "DKK"),
    "Kr": ("SEK", "NOK", "DKK"),
}

# Currencies without a minor unit (ISO 4217 exponent 0): "¥1,000" can only be a thousand yen.
WHOLE_UNITS: frozenset[str] = frozenset(
    {"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "VND", "VUV"}
    | {"XAF", "XOF", "XPF"}
)

BARE_DOLLAR_SIGN = "$"
# Currencies whose own shops write prices with a bare "$"; ``currency_default`` may pick one.
BARE_DOLLAR: frozenset[str] = frozenset(
    {"USD", "AUD", "CAD", "NZD", "SGD", "HKD", "MXN", "TWD", "ARS", "CLP", "COP"}
)

_SYMBOL_ALT = "|".join(re.escape(s) for s in sorted(SYMBOLS, key=len, reverse=True))
# A symbol or a code at the start of the text, and one at the end. Letters next to a code or a
# letter symbol ("Rs", "kr") mean it is part of a word, not a currency.
LEADING = re.compile(rf"^(?:(?P<sym>{_SYMBOL_ALT})|(?P<code>[A-Z]{{3}}))(?![A-Za-z])\s*")
TRAILING = re.compile(rf"\s*(?<![A-Za-z])(?:(?P<sym>{_SYMBOL_ALT})|(?P<code>[A-Z]{{3}}))$")


def candidates(token: str) -> tuple[str, ...] | None:
    """Candidate currencies for a symbol or code, or None when it is not a currency."""
    if token in SYMBOLS:
        return SYMBOLS[token]
    if token in ISO_4217:
        return (token,)
    return None
