"""Bounded decoding of a compressed body window for the guard (integrations only).

Integrations that see the body before the client decodes it (Scrapy's downloader middleware
at 900) decode only the first ``window`` bytes here, never the whole body, so a hostile
compression ratio cannot cost memory. The guard reads at most that window anyway.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterable

GZIP_TRAILER = 8  # CRC32 + ISIZE


def decode_window(
    body: bytes, encodings: Iterable[str], window: int
) -> tuple[bytes | None, int | None]:
    """Return ``(decoded prefix, full decoded length)`` for a body with one content coding.

    The prefix holds at most ``window`` bytes (the whole body when it is shorter). The full
    length is known when the whole body was decoded, or from the gzip trailer; otherwise it
    is None. ``(None, None)`` means the coding is unknown, stacked, or the data is corrupt.
    """
    codings = [c.strip().lower() for c in encodings if c and c.strip().lower() != "identity"]
    if not codings:
        return body[:window], len(body)
    if len(codings) != 1:
        return None, None
    coding = codings[0]
    try:
        if coding in ("gzip", "x-gzip"):
            return _gzip(body, window)
        if coding == "deflate":
            return _deflate(body, window)
        if coding == "br":
            return _brotli(body, window)
    except Exception:  # corrupt data or a decoder failure: the guard goes without a body
        return None, None
    return None, None


def _zlib(body: bytes, wbits: int, window: int) -> tuple[bytes, bool]:
    d = zlib.decompressobj(wbits)
    out = d.decompress(body, window + 1)
    complete = d.eof and len(out) <= window
    return out[:window], complete


def _gzip(body: bytes, window: int) -> tuple[bytes | None, int | None]:
    out, complete = _zlib(body, 16 + zlib.MAX_WBITS, window)
    if complete:
        return out, len(out)
    total = int.from_bytes(body[-4:], "little") if len(body) >= 18 else None
    # ISIZE is the length mod 2**32 of the last member only: trust it when it is plausible.
    return out, total if total is not None and total > len(out) else None


def _deflate(body: bytes, window: int) -> tuple[bytes | None, int | None]:
    try:
        out, complete = _zlib(body, zlib.MAX_WBITS, window)
    except zlib.error:  # servers often send raw deflate without the zlib header
        out, complete = _zlib(body, -zlib.MAX_WBITS, window)
    return out, len(out) if complete else None


def _brotli(body: bytes, window: int) -> tuple[bytes | None, int | None]:
    try:
        import brotli
    except ImportError:
        return None, None
    d = brotli.Decompressor()
    out = d.process(body, output_buffer_limit=window + 1)
    complete = d.is_finished() and len(out) <= window
    return out[:window], len(out) if complete else None
