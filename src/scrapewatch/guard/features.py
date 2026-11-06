"""Feature extraction: the only thing the guard (and fixtures) ever see of a response."""

from __future__ import annotations

import codecs
import html
import re
from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from scrapewatch.guard.rules import bundled_markers
from scrapewatch.redact import cookie_names, redact_headers, redact_url

MARKER_SNIPPET_MAX = 200  # characters; fixtures may never hold more of a page than this
TITLE_MAX = 200

# Widget class names of the public CAPTCHA products (detection only).
CAPTCHA_WIDGET_MARKERS: tuple[str, ...] = ("g-recaptcha", "h-captcha", "cf-turnstile")
TEXT_TYPES: frozenset[str] = frozenset({"text/html", "application/xhtml+xml", "text/plain"})

_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_INVISIBLE_RE = re.compile(
    r"<!--.*?-->|<(script|style|template|title)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"\s+")
_PASSWORD_RE = re.compile(r"<input\b[^>]*\btype\s*=\s*[\"']?password\b", re.IGNORECASE)
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)


class Features(BaseModel):
    """Features of one response. This is also the fixture format (tests/guard/fixtures)."""

    model_config = ConfigDict(extra="forbid")
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)  # lower-case names, redacted
    cookie_names: list[str] = Field(default_factory=list)
    final_url: str | None = None
    request_url: str | None = None
    content_type: str | None = None
    title: str | None = None
    body_bytes: int = 0
    text_length: int = 0
    text_density: float = 0.0  # visible text chars / body bytes, over the body window
    markers: list[str] = Field(default_factory=list)  # short lower-case snippets found in body
    has_form_password: bool = False
    has_captcha_widget: bool = False
    baseline_p50_bytes: int | None = None  # from history, for empty-shell detection
    decode_error: bool = False  # the body window could not be decoded as text
    transport_error: bool = False  # no response at all (timeout, DNS, TLS, reset)
    # In-memory only; never written to history. Fixtures may hold at most
    # MARKER_SNIPPET_MAX synthetic characters here, for body_regex rules.
    body_window: str | None = None


def _content_type(headers: Mapping[str, str]) -> str | None:
    for k, v in headers.items():
        if k.lower() == "content-type":
            return str(v).split(";", 1)[0].strip().lower() or None
    return None


def _charset(headers: Mapping[str, str]) -> str | None:
    for k, v in headers.items():
        if k.lower() == "content-type":
            m = _CHARSET_RE.search(str(v))
            return m.group(1).lower() if m else None
    return None


def decode_window(raw: bytes, charset: str | None, *, truncated: bool = False) -> str | None:
    """Decode the first bytes of a body; None when no candidate encoding fits.

    Tries the declared charset, then UTF-8, then cp1252, each strictly. When the window was
    cut from a longer body (``truncated``), an incomplete multi-byte sequence at its end is
    not an error. Bytes with NUL characters are treated as binary.
    """
    if b"\x00" in raw:
        return None
    candidates = [c for c in (charset, "utf-8", "cp1252") if c]
    for enc in dict.fromkeys(candidates):
        try:
            decoder = codecs.getincrementaldecoder(enc)(errors="strict")
            return decoder.decode(raw, final=not truncated)
        except (LookupError, UnicodeDecodeError):
            continue
    return None


def visible_text(window: str) -> str:
    """Text a reader would see: comments, scripts, styles and tags removed, spaces collapsed."""
    text = _INVISIBLE_RE.sub(" ", window)
    text = _TAG_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", html.unescape(text)).strip()


def extract_features(
    *,
    status: int | None,
    headers: Mapping[str, str],
    body: bytes | str | None,
    request_url: str | None,
    final_url: str | None,
    window_bytes: int = 65536,
    baseline_p50_bytes: int | None = None,
    markers: Iterable[str] | None = None,
    transport_error: bool = False,
) -> Features:
    """Compute features from a raw response. Only the first ``window_bytes`` of body are read.

    ``markers`` are the lower-case ``body_marker`` strings of the rule set (default: the
    bundled rules); only those found in the window are listed. Non-text content types (JSON,
    images) skip text analysis, so only status and header rules apply to them.
    """
    headers = headers or {}
    content_type = _content_type(headers)
    feats = Features(
        status=status,
        headers=redact_headers(headers),
        cookie_names=cookie_names(headers),
        request_url=request_url,
        final_url=final_url,
        content_type=content_type,
        baseline_p50_bytes=baseline_p50_bytes,
        transport_error=transport_error,
    )
    if body is None:
        return feats
    if isinstance(body, str):
        window = body[:window_bytes]
        feats.body_bytes = len(body.encode("utf-8", errors="replace"))
        raw_len = min(feats.body_bytes, window_bytes)
    else:
        feats.body_bytes = len(body)
        raw = bytes(body[:window_bytes])
        raw_len = len(raw)
        decoded = decode_window(raw, _charset(headers), truncated=len(body) > window_bytes)
        if decoded is None:
            feats.decode_error = True
            return feats
        window = decoded
    if content_type is not None and content_type not in TEXT_TYPES:
        return feats
    feats.body_window = window
    lower = window.lower()
    m = _TITLE_RE.search(window)
    if m:
        title = _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", m.group(1)))).strip()
        feats.title = title[:TITLE_MAX]
    feats.text_length = len(visible_text(window))
    feats.text_density = min(1.0, feats.text_length / raw_len) if raw_len else 0.0
    if markers is None:
        markers = bundled_markers()
    feats.markers = sorted({mk for mk in markers if mk and mk in lower})
    feats.has_form_password = bool(_PASSWORD_RE.search(window))
    feats.has_captcha_widget = any(w in lower for w in CAPTCHA_WIDGET_MARKERS)
    return feats


def markers_for_fixture(features: Features, *, max_len: int = MARKER_SNIPPET_MAX) -> Features:
    """Drop ``body_window`` and clip markers so a Features object is safe to commit as a fixture.

    URLs are redacted and the text density is rounded to 4 places.
    """
    return features.model_copy(
        update={
            "body_window": None,
            "markers": [m[:max_len] for m in features.markers],
            "title": features.title[:max_len] if features.title else features.title,
            "request_url": redact_url(features.request_url) if features.request_url else None,
            "final_url": redact_url(features.final_url) if features.final_url else None,
            "text_density": round(features.text_density, 4),
        }
    )
