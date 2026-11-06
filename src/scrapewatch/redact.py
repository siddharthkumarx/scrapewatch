"""Redaction of secrets before anything is stored or reported.

Rules (docs/architecture.md "Privacy and security"):
- header names in ``SENSITIVE_HEADERS`` or matching ``SENSITIVE_HEADER_RE`` are dropped;
- cookies are reduced to their names;
- query parameters whose key matches ``SENSITIVE_QUERY_RE`` keep the key, value becomes
  ``[redacted]``;
- userinfo in URLs (``user:pass@host``) is removed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

REDACTED = "[redacted]"

SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
        "x-xsrf-token",
    }
)
SENSITIVE_HEADER_RE = re.compile(r"(?i)(token|secret|key|auth|session|password|credential)")
SENSITIVE_QUERY_RE = re.compile(
    r"(?i)^(api[_-]?key|apikey|key|token|access[_-]?token|secret|password|passwd|pwd|auth|"
    r"session(id)?|sid|sig|signature|hash|code|otp)$"
)

# Headers worth keeping for the guard and the cost meter (lower-case names).
KEPT_HEADERS: frozenset[str] = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "server",
        "via",
        "location",
        "retry-after",
        "cache-control",
        "etag",
        "last-modified",
        "vary",
        "cf-ray",
        "cf-mitigated",
        "cf-cache-status",
        "x-cache",
        "x-amz-cf-id",
        "x-amzn-waf-action",
        "x-datadome",
        "x-datadome-cid",
        "x-iinfo",
        "x-cdn",
        "x-akamai-transformed",
        "x-kasada-ac",
        "x-frame-options",
    }
)


def is_sensitive_header(name: str) -> bool:
    """True for header names that may carry credentials; these are never stored."""
    lower = name.lower()
    return lower in SENSITIVE_HEADERS or bool(SENSITIVE_HEADER_RE.search(lower))


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return the allow-listed, redacted header subset safe to store.

    Names are lower-cased; only ``KEPT_HEADERS`` survive, and a sensitive name is dropped even
    when allow-listed. URL-valued headers (``location``) go through :func:`redact_url`.
    Repeated names are joined with ``", "``.
    """
    out: dict[str, str] = {}
    for name, value in _header_items(headers):
        lower = str(name).strip().lower()
        if lower not in KEPT_HEADERS or is_sensitive_header(lower):
            continue
        text = str(value)
        if lower == "location":
            text = redact_url(text)
        out[lower] = f"{out[lower]}, {text}" if lower in out else text
    return out


def cookie_names(headers: Mapping[str, str]) -> list[str]:
    """Extract cookie names from Set-Cookie / Cookie headers; values are never returned.

    Works with plain dicts and with multi-value header objects (``get_list`` in httpx,
    ``getlist`` in urllib3/requests); several Set-Cookie values merged into one string with
    commas are split on the comma that starts a new ``name=`` pair.
    """
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        name = name.strip()
        if name and name not in seen and _COOKIE_NAME_RE.fullmatch(name):
            seen.add(name)
            names.append(name)

    for name, value in _header_items(headers, multi=("set-cookie", "cookie")):
        lower = str(name).lower()
        if lower == "set-cookie":
            for one in _SET_COOKIE_SPLIT_RE.split(str(value)):
                first = one.split(";", 1)[0]
                if "=" in first:
                    add(first.split("=", 1)[0])
        elif lower == "cookie":
            for pair in str(value).split(";"):
                if "=" in pair:
                    add(pair.split("=", 1)[0])
    return names


def redact_url(url: str) -> str:
    """Strip userinfo and replace secret-looking query values.

    The query string is rewritten pair by pair without re-encoding, so a URL without secrets
    comes back byte for byte. Key=value pairs in the fragment are treated the same way. A URL
    that cannot be parsed loses its query and fragment rather than leaking them.
    """
    try:
        parts = urlsplit(url)
        netloc = parts.netloc
        if "@" in netloc:
            netloc = netloc.rsplit("@", 1)[1]
        rebuilt = SplitResult(
            parts.scheme,
            netloc,
            parts.path,
            _redact_pairs(parts.query),
            _redact_pairs(parts.fragment) if "=" in parts.fragment else parts.fragment,
        )
        return urlunsplit(rebuilt) if rebuilt != parts else url
    except ValueError:
        return url.split("?", 1)[0].split("#", 1)[0]


_COOKIE_NAME_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_SET_COOKIE_SPLIT_RE = re.compile(r"[\r\n]+|,\s*(?=[!#$%&'*+\-.^_`|~0-9A-Za-z]+=)")


def _redact_pairs(query: str) -> str:
    if not query:
        return query
    out = []
    for pair in query.split("&"):
        key, sep, _value = pair.partition("=")
        if sep and SENSITIVE_QUERY_RE.match(unquote(key.replace("+", " ")).strip()):
            out.append(f"{key}={REDACTED}")
        else:
            out.append(pair)
    return "&".join(out)


def _header_items(headers: Any, multi: Iterable[str] = ()) -> list[tuple[str, str]]:
    """Header pairs, expanding multi-valued names when the object supports it."""
    if headers is None:
        return []
    wanted = {m.lower() for m in multi}
    getter = getattr(headers, "get_list", None) or getattr(headers, "getlist", None)
    items: list[tuple[str, str]] = []
    expanded: set[str] = set()
    if callable(getter) and wanted:
        for name in wanted:
            try:
                values = list(getter(name))
            except Exception:  # header objects vary; fall back to items()
                values = []
            if values:
                expanded.add(name)
                items.extend((name, str(v)) for v in values)
    raw = headers.items() if hasattr(headers, "items") else headers
    for name, value in raw:
        if str(name).lower() in expanded:
            continue
        items.append((str(name), str(value)))
    return items
