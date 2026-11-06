"""Byte-count helpers shared by the integrations (docs/architecture.md "Cost model" 11.3)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _pairs(headers: Mapping[str, str] | Iterable[tuple[str, str]] | None) -> list[tuple[str, str]]:
    if headers is None:
        return []
    multi = getattr(headers, "multi_items", None)  # httpx keeps repeated headers apart here
    if callable(multi):
        return [(str(k), str(v)) for k, v in multi()]
    raw: Any = headers.items() if hasattr(headers, "items") else headers
    return [(str(k), str(v)) for k, v in raw]


def header_bytes_estimate(headers: Mapping[str, str] | Iterable[tuple[str, str]] | None) -> int:
    """Estimate of the response header block on the wire.

    ``Σ (len(name) + len(value) + 4) + 2``: name, ``": "``, value and CRLF per header plus the
    blank line. Close to HTTP/1.1, an over-estimate under HTTP/2 (HPACK compresses headers).
    """
    pairs = _pairs(headers)
    if not pairs:
        return 0
    return sum(len(k) + len(v) + 4 for k, v in pairs) + 2


def content_length(headers: Mapping[str, str] | None) -> int | None:
    """The Content-Length header as an int, or None when absent or invalid."""
    if not headers:
        return None
    for k, v in _pairs(headers):
        if k.lower() == "content-length":
            try:
                n = int(str(v).split(",")[0].strip())
            except ValueError:
                return None
            return n if n >= 0 else None
    return None
