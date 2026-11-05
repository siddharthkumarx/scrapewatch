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
from collections.abc import Mapping

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
        "www-authenticate",
    }
)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return the allow-listed, redacted header subset safe to store."""
    raise NotImplementedError("build-progress item 1")


def cookie_names(headers: Mapping[str, str]) -> list[str]:
    """Extract cookie names from Set-Cookie / Cookie headers; values are never returned."""
    raise NotImplementedError("build-progress item 1")


def redact_url(url: str) -> str:
    """Strip userinfo and replace secret-looking query values."""
    raise NotImplementedError("build-progress item 1")
