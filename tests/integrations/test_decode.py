"""Bounded window decoding used by the Scrapy middleware for the guard."""

from __future__ import annotations

import gzip
import zlib

import pytest

from scrapewatch.guard.features import extract_features
from scrapewatch.integrations._decode import decode_window

SMALL = b"<html><body>" + b"<p>book</p>" * 50 + b"</body></html>"
LARGE = b"<html><body>" + b"<p>a book about something</p>" * 5000 + b"</body></html>"


def test_identity_and_unknown() -> None:
    assert decode_window(LARGE, [], 1000) == (LARGE[:1000], len(LARGE))
    assert decode_window(SMALL, ["identity"], 1000) == (SMALL, len(SMALL))
    assert decode_window(SMALL, ["compress"], 1000) == (None, None)
    assert decode_window(SMALL, ["gzip", "br"], 1000) == (None, None)  # stacked codings
    assert decode_window(b"not gzip", ["gzip"], 1000) == (None, None)  # corrupt


def test_gzip_window_and_trailer_size() -> None:
    assert decode_window(gzip.compress(SMALL), ["gzip"], 4096) == (SMALL, len(SMALL))
    prefix, total = decode_window(gzip.compress(LARGE), ["x-gzip"], 4096)
    assert prefix == LARGE[:4096]
    assert total == len(LARGE)  # from the gzip trailer, without decoding the rest


def test_deflate_zlib_and_raw() -> None:
    assert decode_window(zlib.compress(SMALL), ["deflate"], 4096) == (SMALL, len(SMALL))
    raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    body = raw.compress(LARGE) + raw.flush()
    assert decode_window(body, ["deflate"], 4096) == (LARGE[:4096], None)  # size unknown


def test_brotli() -> None:
    brotli = pytest.importorskip("brotli")
    assert decode_window(brotli.compress(SMALL), ["br"], 4096) == (SMALL, len(SMALL))
    prefix, total = decode_window(brotli.compress(LARGE), ["br"], 4096)
    assert (prefix, total) == (LARGE[:4096], None)


def test_brotli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "brotli", None)
    assert decode_window(b"x", ["br"], 10) == (None, None)


def test_guard_uses_the_full_length_for_a_window() -> None:
    f = extract_features(
        status=200,
        headers={"content-type": "text/html"},
        body=LARGE[:4096],
        request_url="https://a.example/",
        final_url=None,
        total_bytes=len(LARGE),
    )
    assert f.body_bytes == len(LARGE)
