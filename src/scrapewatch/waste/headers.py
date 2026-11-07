"""``Accept-Encoding`` for HTTP clients: ask for compressed bodies the client can decode."""

from __future__ import annotations

import importlib.util


def _brotli_available() -> bool:
    return any(importlib.util.find_spec(m) is not None for m in ("brotli", "brotlicffi"))


def compression_headers() -> dict[str, str]:
    """Headers asking servers for compressed responses (fewer bytes on the wire).

    ``br`` is offered only when ``brotli`` or ``brotlicffi`` is installed, because httpx and
    requests can decode Brotli only then; otherwise ``gzip, deflate``. Use as default headers,
    e.g. ``httpx.Client(headers=compression_headers())``.
    """
    encodings = ["gzip", "deflate"]
    if _brotli_available():
        encodings.append("br")
    return {"Accept-Encoding": ", ".join(encodings)}
