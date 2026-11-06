"""date normalizer (docs/architecture.md 10.3).

Reads a fixed set of formats with regexes (no dateutil) and writes ISO 8601: datetimes in UTC
with a ``Z`` suffix, dates as ``YYYY-MM-DD``. Anything with more than one reading is flagged:
day/month order without ``day_first``, a time without a zone and no ``assume_tz``, zone
abbreviations, relative dates without ``reference_time``, a missing or two-digit year, and
local times that do not exist or happen twice around a DST change.

Options: ``day_first`` (null | true | false), ``assume_tz`` (null | "UTC" | IANA name),
``reference_time`` (ISO 8601 with a zone, for "2 days ago"), ``output`` (null | datetime |
date; null keeps dates as dates and datetimes as datetimes).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

from scrapewatch.clean.pipeline import CleanOutcome, register

_MONTHS = {
    name: i + 1
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ]
    )
    for name in names
}
_MONTH = r"(?P<month>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY_NAME = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
_TIME = (
    r"(?:[T\s]+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?:\.(?P<frac>\d{1,6}))?)?"
    r"\s*(?P<ampm>[ap]\.?m\.?)?)?"
)
_ZONE = r"\s*(?P<zone>Z|[+-]\d{2}:?\d{2}|UTC|GMT|UT|[A-Z]{2,5})?"

_ISO = re.compile(
    r"(?P<year>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
    r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?:[.,](?P<frac>\d{1,9}))?)?"
    r"\s*(?P<zone>Z|[+-]\d{2}(?::?\d{2})?|UTC|GMT|[A-Z]{2,5})?)?",
    re.IGNORECASE,
)
_YMD = re.compile(r"(?P<year>\d{4})/(?P<m>\d{1,2})/(?P<d>\d{1,2})" + _TIME + _ZONE, re.IGNORECASE)
_DOTTED = re.compile(r"(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<year>\d{4})" + _TIME + _ZONE)
_SLASHED = re.compile(r"(?P<a>\d{1,2})[/-](?P<b>\d{1,2})[/-](?P<year>\d{4})" + _TIME + _ZONE)
_SHORT_YEAR = re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2}(?:\D.*)?")
_MONTH_FIRST = re.compile(
    _DAY_NAME + _MONTH + r"\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(?P<year>\d{4}))?"
    r"(?:,?\s*(?:at\s+)?" + _TIME.removeprefix(r"(?:[T\s]+") + _ZONE,
    re.IGNORECASE,
)
_DAY_FIRST = re.compile(
    _DAY_NAME
    + r"(?P<d>\d{1,2})(?:st|nd|rd|th)?\.?\s+(?:of\s+)?"
    + _MONTH
    + r",?(?:\s+(?P<year>\d{4}))?"
    + _TIME
    + _ZONE,
    re.IGNORECASE,
)
_RFC2822 = re.compile(
    r"(?:[A-Za-z]{3},\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}(?::\d{2})?\s+"
    r"(?P<zone>[+-]\d{4}|GMT|UT|UTC|[A-Z]{1,5})"
)
_EPOCH = re.compile(r"\d{10}|\d{13}")
_RELATIVE = re.compile(
    r"(?P<n>\d+|an?|one)\s+(?P<unit>second|sec|minute|min|hour|hr|day|week|month|year)s?\s+ago",
    re.IGNORECASE,
)
_TRAILING_ZONE = re.compile(r"\d\s*([A-Z]{2,5})$")
_TODAY = re.compile(r"(?P<word>today|yesterday)", re.IGNORECASE)
_SECONDS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600}
_SECONDS |= {"day": 86400, "week": 604800}
_UTC_NAMES = {"Z", "UTC", "GMT", "UT"}
_OUTPUTS = (None, "datetime", "date")


class _FlagError(Exception):
    def __init__(self, flag: str, message: str) -> None:
        super().__init__(message)
        self.flag = flag
        self.message = message


@dataclass
class _Parsed:
    """What a format gave: a date, maybe a time, maybe a zone."""

    day: date
    clock: time | None = None
    zone: tzinfo | None = None  # None: no zone written
    note: str | None = None  # info finding suffix (relative dates)


def _zone(text: str | None, value: str) -> tzinfo | None:
    if text is None:
        return None
    upper = text.upper()
    if upper in _UTC_NAMES:
        return timezone.utc
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})?", text)
    if m:
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return timezone(-delta if m.group(1) == "-" else delta)
    raise _FlagError("ambiguous_zone", f"{value!r}: zone abbreviation {text!r} is not unique")


def _date(year: int, month: int, day: int, value: str) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise _FlagError("invalid", f"{value!r} is not a calendar date ({exc})") from None


def _clock(m: re.Match[str], value: str) -> time | None:
    hour = m.groupdict().get("hour")
    if hour is None:
        return None
    h, minute = int(hour), int(m.group("minute"))
    sec = int(m.group("second") or 0)
    frac = (m.group("frac") or "").ljust(6, "0")[:6]
    ampm = (m.groupdict().get("ampm") or "").replace(".", "").lower()
    if ampm:
        if not 1 <= h <= 12:
            raise _FlagError("invalid", f"{value!r}: hour {h} with {ampm}")
        h = h % 12 + (12 if ampm == "pm" else 0)
    try:
        return time(h, minute, sec, int(frac))
    except ValueError as exc:
        raise _FlagError("invalid", f"{value!r} is not a time of day ({exc})") from None


def _year(m: re.Match[str], value: str) -> int:
    year = m.group("year")
    if year is None:
        raise _FlagError("no_year", f"{value!r} has no year (the year is never guessed)")
    return int(year)


def _order(a: int, b: int, day_first: bool | None, value: str) -> tuple[int, int]:
    """(month, day) of ``a/b``."""
    if a > 12 and b <= 12:
        return b, a
    if b > 12 and a <= 12:
        return a, b
    if a == b or day_first is not None:
        return (b, a) if day_first else (a, b)
    raise _FlagError(
        "ambiguous_order",
        f"{value!r} could be day/month or month/day; set clean_options day_first",
    )


def _reference(options: Mapping[str, Any]) -> datetime | None:
    ref = options.get("reference_time")
    if ref is None:
        return None
    if isinstance(ref, str):
        ref = datetime.fromisoformat(ref.replace("Z", "+00:00"))
    if not isinstance(ref, datetime) or ref.tzinfo is None:
        raise ValueError("reference_time must be an ISO 8601 datetime with a zone")
    return ref.astimezone(timezone.utc)


def _parse_text(text: str, options: Mapping[str, Any]) -> _Parsed:
    value = text
    if _EPOCH.fullmatch(text):
        return _epoch(int(text))
    m = _ISO.fullmatch(text)
    if m:
        day = _date(int(m.group("year")), int(m.group("m")), int(m.group("d")), value)
        return _Parsed(day, _clock(m, value), _zone(m.group("zone"), value))
    if _RFC2822.fullmatch(text):
        zone = _RFC2822.fullmatch(text)
        _zone(zone.group("zone") if zone else None, value)  # flags EST and friends
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError) as exc:
            raise _FlagError("invalid", f"{value!r} is not a valid date ({exc})") from None
        return _Parsed(dt.date(), dt.timetz().replace(tzinfo=None), dt.tzinfo)
    for pattern in (_YMD, _DOTTED):
        m = pattern.fullmatch(text)
        if m:
            day = _date(int(m.group("year")), int(m.group("m")), int(m.group("d")), value)
            return _Parsed(day, _clock(m, value), _zone(m.group("zone"), value))
    m = _SLASHED.fullmatch(text)
    if m:
        month, d = _order(int(m.group("a")), int(m.group("b")), options.get("day_first"), value)
        day = _date(int(m.group("year")), month, d, value)
        return _Parsed(day, _clock(m, value), _zone(m.group("zone"), value))
    for pattern in (_MONTH_FIRST, _DAY_FIRST):
        m = pattern.fullmatch(text)
        if m:
            month = _MONTHS[m.group("month").lower()]
            day = _date(_year(m, value), month, int(m.group("d")), value)
            return _Parsed(day, _clock(m, value), _zone(m.group("zone"), value))
    if _SHORT_YEAR.fullmatch(text):
        raise _FlagError("two_digit_year", f"{value!r} has a two-digit year (never guessed)")
    rel = _RELATIVE.fullmatch(text) or _TODAY.fullmatch(text)
    if rel:
        return _relative(rel, options, value)
    zone = _TRAILING_ZONE.search(text)
    if zone and zone.group(1).upper() not in _UTC_NAMES | {"AM", "PM"}:
        message = f"{value!r}: zone abbreviation {zone.group(1)!r} is not unique"
        raise _FlagError("ambiguous_zone", message)
    raise _FlagError("unparseable", f"{value!r} is not in a supported date format")


def _epoch(n: int) -> _Parsed:
    dt = datetime.fromtimestamp(n / 1000 if n >= 10**12 else n, tz=timezone.utc)
    return _Parsed(dt.date(), dt.time(), timezone.utc)


def _relative(m: re.Match[str], options: Mapping[str, Any], value: str) -> _Parsed:
    ref = _reference(options)
    if ref is None:
        raise _FlagError("relative", f"{value!r} is relative; set clean_options reference_time")
    groups = m.groupdict()
    if "word" in groups:
        back = 0 if groups["word"].lower() == "today" else 1
        return _Parsed(ref.date() - timedelta(days=back), note="relative")
    unit = groups["unit"].lower()
    if unit not in _SECONDS:
        raise _FlagError("relative", f"{value!r}: {unit}s have no fixed length")
    n = 1 if groups["n"].lower() in ("a", "an", "one") else int(groups["n"])
    dt = ref - timedelta(seconds=n * _SECONDS[unit])
    return _Parsed(dt.date(), dt.time(), timezone.utc, note="relative")


def _assume(options: Mapping[str, Any]) -> tzinfo | None:
    name = options.get("assume_tz")
    if name is None:
        return None
    if str(name).upper() in _UTC_NAMES:
        return timezone.utc
    return ZoneInfo(str(name))


def _localize(day: date, clock: time, zone: tzinfo, value: str) -> datetime:
    """Attach an IANA zone, flagging times skipped or repeated by a DST change."""
    local = datetime.combine(day, clock).replace(tzinfo=zone)
    if isinstance(zone, timezone):
        return local
    early, late = local.replace(fold=0), local.replace(fold=1)
    if early.utcoffset() != late.utcoffset():
        round_trip = early.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if round_trip != local.replace(tzinfo=None):
            raise _FlagError("nonexistent_time", f"{value!r} does not exist in {zone} (DST change)")
        raise _FlagError("ambiguous_time", f"{value!r} happens twice in {zone} (DST change)")
    return local


def _iso(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    spec = "seconds" if not dt.microsecond else "milliseconds"
    if dt.microsecond % 1000:
        spec = "microseconds"
    return dt.replace(tzinfo=None).isoformat(timespec=spec) + "Z"


def _render(parsed: _Parsed, options: Mapping[str, Any], value: str) -> str:
    output = options.get("output")
    if output not in _OUTPUTS:
        raise ValueError(f"output must be datetime or date, not {output!r}")
    if parsed.clock is None:
        if output != "datetime":
            return parsed.day.isoformat()
        parsed.clock = time(0, 0)
    zone = parsed.zone or _assume(options)
    if zone is None:
        raise _FlagError("naive", f"{value!r} has no time zone; set clean_options assume_tz")
    dt = _localize(parsed.day, parsed.clock, zone, value)
    if output == "date":
        return dt.astimezone(timezone.utc).date().isoformat()
    return _iso(dt)


def _from_object(value: Any) -> _Parsed | None:
    if isinstance(value, datetime):
        return _Parsed(value.date(), value.timetz().replace(tzinfo=None), value.tzinfo)
    if isinstance(value, date):
        return _Parsed(value)
    if isinstance(value, int) and not isinstance(value, bool) and _EPOCH.fullmatch(str(value)):
        return _epoch(value)
    return None


@register("date")
def normalize_date(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """Fix a date or datetime to ISO 8601 (UTC, ``Z``) or flag it."""
    _assume(options)  # bad zone names fail on every value, not only naive ones
    try:
        parsed = _from_object(value)
        if parsed is None:
            if not isinstance(value, str):
                raise _FlagError("unparseable", f"{value!r} is not a date")
            parsed = _parse_text(" ".join(value.split()), options)
        new = _render(parsed, options, repr(value) if not isinstance(value, str) else value)
    except _FlagError as exc:
        return CleanOutcome(value=value, flag=exc.flag, flag_message=exc.message)
    note = parsed.note
    return CleanOutcome(
        value=new,
        changed=new != value,
        note=note,
        note_message=f"{value!r} was read relative to reference_time as {new}" if note else None,
    )
