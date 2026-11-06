"""Text normalizers (docs/architecture.md 10.4): whitespace, html_entities, strip_tags, mojibake.

All four leave non-strings alone. Each is idempotent: running it on its own output changes
nothing, so a second pass over cleaned data records no fixes.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any

from scrapewatch.clean.pipeline import CleanOutcome, register

# --- whitespace ------------------------------------------------------------------------------

# Invisible characters removed outright: zero-width space, word joiner, BOM, soft hyphen.
# Zero-width (non-)joiners stay: some scripts and emoji sequences need them.
_INVISIBLE = re.compile("[\u200b\u2060\ufeff\u00ad]")
_WS = re.compile(r"\s+")  # str patterns match Unicode whitespace: NBSP, thin spaces, U+3000


@register("whitespace")
def normalize_whitespace(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """Strip, collapse whitespace runs (NBSP included) to one space, drop zero-width chars."""
    if not isinstance(value, str):
        return CleanOutcome(value=value)
    new = _WS.sub(" ", _INVISIBLE.sub("", value)).strip()
    return CleanOutcome(value=new, changed=new != value)


# --- html_entities ---------------------------------------------------------------------------

_MAX_UNESCAPE = 4


@register("html_entities")
def normalize_html_entities(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """Decode HTML entities (``&amp;``, ``&#8211;``), also when they were escaped twice."""
    if not isinstance(value, str) or "&" not in value:
        return CleanOutcome(value=value)
    new = value
    for _ in range(_MAX_UNESCAPE):
        step = html.unescape(new)
        if step == new:
            return CleanOutcome(value=new, changed=new != value)
        new = step
    return CleanOutcome(
        value=value,
        flag="undecidable",
        flag_message=f"{value!r} is escaped more than {_MAX_UNESCAPE} times",
    )


# --- strip_tags ------------------------------------------------------------------------------

_DROP_BLOCKS = re.compile(
    r"<(script|style|template)\b[^>]*>.*?</\1\s*>|<!--.*?-->", re.IGNORECASE | re.DOTALL
)
_BLOCK_TAGS = (
    "address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|"
    "hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul"
)
_BLOCK = re.compile(rf"</?(?:{_BLOCK_TAGS})\b[^<>]*>", re.IGNORECASE)
# A real tag: "<" then a name (or "/name", "!doctype"), never "a < b".
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9:-]*(?:\s[^<>]*)?/?>|<![A-Za-z][^<>]*>")
_SPACES = re.compile(r" {2,}")


@register("strip_tags")
def normalize_strip_tags(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """Remove HTML tags, comments and script/style content; block tags become a space."""
    if not isinstance(value, str) or "<" not in value:
        return CleanOutcome(value=value)
    new = value
    while True:  # removing a tag can join two halves into a new one: "<<b>i>"
        step = _TAG.sub("", _BLOCK.sub(" ", _DROP_BLOCKS.sub("", new)))
        if step == new:
            break
        new = step
    if new != value:
        new = _SPACES.sub(" ", new).strip()
    return CleanOutcome(value=new, changed=new != value)


# --- mojibake --------------------------------------------------------------------------------

# Characters that show up when UTF-8 bytes were decoded as cp1252 or Latin-1.
_MARKERS = ("Ã", "â€", "Â")
_CONTINUATION = range(0x80, 0xC0)


def _byte(ch: str) -> int | None:
    """The single cp1252 (or Latin-1) byte that decodes to ``ch``, or None."""
    try:
        raw = ch.encode("cp1252")
    except UnicodeEncodeError:
        return ord(ch) if ord(ch) < 0x100 else None
    return raw[0] if len(raw) == 1 else None


def _repair(text: str) -> str:
    """Re-decode every run of characters that spells a UTF-8 sequence in cp1252."""
    out: list[str] = []
    i = 0
    while i < len(text):
        lead = _byte(text[i])
        if lead is not None and 0xC2 <= lead <= 0xF4:
            size = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
            seq = [_byte(c) for c in text[i : i + size]]
            if len(seq) == size and all(b is not None and b in _CONTINUATION for b in seq[1:]):
                try:
                    out.append(bytes(b for b in seq if b is not None).decode("utf-8"))
                except UnicodeDecodeError:
                    pass
                else:
                    i += size
                    continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _marker_count(text: str) -> int:
    return sum(text.count(m) for m in _MARKERS)


def _suspicious(text: str) -> bool:
    """A marker followed by a continuation-byte character or by the end of the text."""
    for i, ch in enumerate(text):
        if ch in "ÃÂâ":
            nxt = text[i + 1] if i + 1 < len(text) else None
            if ch == "â" and nxt != "€":
                continue
            if nxt is None or (_byte(nxt) or 0) in _CONTINUATION:
                return True
    return False


@register("mojibake")
def normalize_mojibake(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """Undo UTF-8 read as cp1252/Latin-1 ("CafÃ©" -> "Café") when that removes markers."""
    if not isinstance(value, str) or not _suspicious(value):
        return CleanOutcome(value=value)
    new = value
    for _ in range(3):  # text that went through the wrong decoding twice
        step = _repair(new)
        if step == new or _marker_count(step) >= _marker_count(new):
            break
        new = step
    if new == value or _suspicious(new):
        return CleanOutcome(
            value=value,
            flag="undecidable",
            flag_message=f"{value!r} looks like mojibake but has no single repair",
        )
    return CleanOutcome(value=new, changed=True)
