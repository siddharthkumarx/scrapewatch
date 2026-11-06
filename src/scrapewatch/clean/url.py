"""url normalizer (docs/architecture.md 10.5).

Normalises an http(s) URL to one spelling: lower-case scheme and host, IDNA host, no default
port, dot segments resolved, percent-encoding normalised (upper-case escapes, unreserved
characters decoded, non-ASCII encoded), tracking parameters dropped, query sorted by key and
the fragment removed. Relative URLs are resolved against ``base`` or flagged.

Options: ``base``, ``drop_params`` (globs, default below), ``keep_fragment`` (false),
``sort_query`` (true), ``trailing_slash`` (keep | add | strip).
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from scrapewatch.clean.pipeline import CleanOutcome, register

DEFAULT_DROP_PARAMS = ("utm_*", "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "ref", "_ga")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PATH_SAFE = _UNRESERVED | frozenset("/:@!$&'()*+,;=")
_QUERY_SAFE = _PATH_SAFE | frozenset("?")  # '&', '=' and '+' stay as written
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_HEX = frozenset("0123456789abcdefABCDEF")
_TRAILING = ("keep", "add", "strip")


def _flag(value: Any, flag: str, message: str) -> CleanOutcome:
    return CleanOutcome(value=value, flag=flag, flag_message=message)


def _pct(text: str, safe: frozenset[str]) -> str:
    """Normalise percent-encoding: decode unreserved escapes, upper-case the rest."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "%" and i + 2 < len(text) and set(text[i + 1 : i + 3]) <= _HEX:
            code = int(text[i + 1 : i + 3], 16)
            decoded = chr(code)
            out.append(decoded if decoded in _UNRESERVED else f"%{code:02X}")
            i += 3
            continue
        if ch in safe:
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
        i += 1
    return "".join(out)


def _host(host: str) -> str:
    host = host.lower().rstrip(".")
    if host.isascii():
        return host
    return host.encode("idna").decode("ascii")


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 section 5.2.4 for an absolute path (empty segments are kept)."""
    out: list[str] = []
    segments = path.split("/")[1:]
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if seg == "..":
            if out:
                out.pop()
            if last:
                out.append("")
        elif seg == ".":
            if last:
                out.append("")
        else:
            out.append(seg)
    return "/" + "/".join(out)


def _path(path: str, *, trailing: str) -> str:
    norm = _remove_dot_segments(_pct(path or "/", _PATH_SAFE))
    if trailing == "strip" and norm != "/":
        norm = norm.rstrip("/") or "/"
    elif trailing == "add" and not norm.endswith("/") and "." not in norm.rsplit("/", 1)[-1]:
        norm += "/"
    return norm


def _query(query: str, drop: list[str], *, sort: bool) -> str:
    items: list[tuple[str, str]] = []
    for part in query.split("&"):
        if not part:
            continue
        key, sep, val = part.partition("=")
        name = unquote(key.replace("+", " ")).lower()
        if any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in drop):
            continue
        items.append((_pct(key, _QUERY_SAFE), sep + _pct(val, _QUERY_SAFE)))
    if sort:
        items.sort(key=lambda kv: kv[0])  # stable: repeated keys keep their order
    return "&".join(k + v for k, v in items)


@register("url")
def normalize_url(value: Any, options: Mapping[str, Any]) -> CleanOutcome:
    """Normalise an http(s) URL, or flag it as relative, not http, or invalid."""
    trailing = options.get("trailing_slash") or "keep"
    if trailing not in _TRAILING:
        raise ValueError(f"trailing_slash must be one of {', '.join(_TRAILING)}")
    if not isinstance(value, str):
        return _flag(value, "invalid", f"{value!r} is not a URL")
    text = value.strip()
    scheme = _SCHEME.match(text)
    if scheme is None or text.startswith("//"):
        base = options.get("base")
        if not base:
            return _flag(value, "relative", f"{value!r} is relative; set clean_options base")
        text = urljoin(str(base), text)
        scheme = _SCHEME.match(text)
    if scheme is None or scheme.group(1).lower() not in _DEFAULT_PORTS:
        return _flag(value, "not_http", f"{value!r} is not an http(s) URL")
    try:
        parts = urlsplit(text)
        port = parts.port
        host = _host(parts.hostname or "")
    except (ValueError, UnicodeError) as exc:
        return _flag(value, "invalid", f"{value!r} is not a valid URL: {exc}")
    if not host:
        return _flag(value, "invalid", f"{value!r} has no host")
    netloc = host if ":" not in host else f"[{host}]"
    if port is not None and port != _DEFAULT_PORTS[parts.scheme.lower()]:
        netloc += f":{port}"
    userinfo = parts.netloc.rpartition("@")[0] if "@" in parts.netloc else ""
    if userinfo:
        netloc = f"{userinfo}@{netloc}"
    drop = options.get("drop_params")
    drop = list(DEFAULT_DROP_PARAMS if drop is None else drop)
    query = _query(parts.query, drop, sort=options.get("sort_query", True) is not False)
    fragment = _pct(parts.fragment, _QUERY_SAFE) if options.get("keep_fragment") else ""
    new = urlunsplit(
        (parts.scheme.lower(), netloc, _path(parts.path, trailing=trailing), query, fragment)
    )
    return CleanOutcome(value=new, changed=new != value)
