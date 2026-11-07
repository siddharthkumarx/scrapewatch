"""Playwright route blocker: abort images, media, fonts and known trackers; allowlist wins.

``install_route_blocker(context_or_page, w)`` routes every request of a BrowserContext (or one
Page) through a handler:

- the document (any navigation request) is never blocked;
- a URL matching an ``allow`` pattern is never blocked (allow wins over every block);
- otherwise a request is blocked when its resource type is in ``resource_types``, its host is
  in ``trackers.txt`` (or a subdomain of one) with ``block_trackers`` on, or its URL matches a
  ``block_url_globs`` pattern.

Patterns are shell-style (``fnmatch``) and match the whole URL; ``*`` also matches ``/``, so
``*/img/logo.png`` and ``https://cdn.example.com/*`` both work.

A blocked request is aborted with ``blockedbyclient`` and counted through
``Watch.add_blocked`` with an estimate of the bytes it would have cost: the median wire size of
that resource type seen earlier in the run, else the table in docs/architecture.md 11.3. It
is not a response event, and the Playwright integration does not record its
``requestfailed`` as an error. Anything else falls back to the next route handler (your own
routes still run) or the network. The handler fails open: an error inside it lets the
request through and is counted in ``counts.monitor_errors``.

Routing turns off Playwright's HTTP cache for the context, so every allowed request goes to
the network.
"""

from __future__ import annotations

import fnmatch
from functools import lru_cache
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scrapewatch.integrations.playwright import ABORTED, median_size, resource_type_of
from scrapewatch.models import ResourceType
from scrapewatch.runtime import Watch

DEFAULT_BLOCKED_TYPES: tuple[str, ...] = ("image", "media", "font")
ROUTE_GLOB = "**/*"
ABORT_ERROR = "blockedbyclient"
KIB = 1024
# Fallback estimates when the run has not measured that resource type yet (A§11.3).
ESTIMATE_BYTES: dict[str, int] = {
    "image": 45 * KIB,
    "font": 60 * KIB,
    "media": 500 * KIB,
    "script": 80 * KIB,
    "stylesheet": 30 * KIB,
}
_BLOCKABLE = {t.value for t in ResourceType} - {"document"}


class BlockPolicy(BaseModel):
    """What to block. ``allow`` patterns (glob on URL) always win over blocks."""

    model_config = ConfigDict(extra="forbid")
    resource_types: list[str] = Field(default_factory=lambda: list(DEFAULT_BLOCKED_TYPES))
    block_trackers: bool = True
    block_url_globs: list[str] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list)

    @field_validator("resource_types")
    @classmethod
    def _known_types(cls, value: list[str]) -> list[str]:
        bad = sorted(set(value) - _BLOCKABLE)
        if bad:
            raise ValueError(
                f"cannot block resource types {bad}; choose from {sorted(_BLOCKABLE)}"
                " (the document is never blocked)"
            )
        return value


@lru_cache(maxsize=1)
def tracker_hosts() -> frozenset[str]:
    """Hosts in the bundled ``trackers.txt`` (lower case, comments dropped)."""
    text = resources.files("scrapewatch.waste").joinpath("trackers.txt").read_text("utf-8")
    hosts = (line.split("#", 1)[0].strip().lower() for line in text.splitlines())
    return frozenset(h for h in hosts if h)


def is_tracker(host: str) -> bool:
    """True when ``host`` is a listed tracker host or a subdomain of one."""
    host = host.lower().rstrip(".")
    trackers = tracker_hosts()
    parts = host.split(".")
    return any(".".join(parts[i:]) in trackers for i in range(len(parts) - 1))


def _matches(url: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(url, p) for p in patterns)


def should_block(policy: BlockPolicy, url: str, resource_type: str, *, navigation: bool) -> bool:
    """Decide one request (see the module docstring for the order of the rules)."""
    if navigation or resource_type == "document":
        return False
    if _matches(url, policy.allow):
        return False
    if resource_type in policy.resource_types:
        return True
    if policy.block_trackers:
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            host = ""
        if host and is_tracker(host):
            return True
    return _matches(url, policy.block_url_globs)


def estimate_bytes(watch: Watch, resource_type: str) -> int:
    """Bytes a blocked request would have cost: run median for the type, else the table."""
    measured = median_size(watch, resource_type)
    return measured if measured is not None else ESTIMATE_BYTES.get(resource_type, 0)


def _decide(watch: Watch, policy: BlockPolicy, request: Any) -> int | None:
    """The bytes estimate when the request is to be aborted, else None. Never raises.

    The request goes into ``ABORTED`` before the abort, so the ``requestfailed`` the abort
    causes is never recorded as an error.
    """
    try:
        rtype = resource_type_of(request)
        url = str(request.url)
        if not should_block(policy, url, rtype, navigation=bool(request.is_navigation_request())):
            return None
        estimate = estimate_bytes(watch, rtype)
        ABORTED.add(request)
        return estimate
    except Exception as exc:
        watch.monitor_error("route blocker", exc)
        return None


def _abort_failed(watch: Watch, request: Any, exc: Exception) -> None:
    ABORTED.discard(request)
    watch.monitor_error("route blocker abort", exc)


def install_route_blocker(
    context_or_page: Any, watch: Watch, policy: BlockPolicy | None = None
) -> None:
    """``context_or_page.route("**/*", handler)`` (sync API); blocked requests are counted.

    Install it before the first navigation. Never raises: if the route cannot be installed,
    the failure is counted in ``counts.monitor_errors`` and nothing is blocked.
    """
    pol = policy or BlockPolicy()

    def handler(route: Any) -> None:
        request = route.request
        estimate = _decide(watch, pol, request)
        if estimate is not None:
            try:
                route.abort(ABORT_ERROR)
            except Exception as exc:
                _abort_failed(watch, request, exc)
            else:
                watch.add_blocked(1, bytes_saved_estimate=estimate)
                return
        try:
            route.fallback()
        except Exception as exc:
            watch.monitor_error("route blocker fallback", exc)

    try:
        context_or_page.route(ROUTE_GLOB, handler)
    except Exception as exc:
        watch.monitor_error("route blocker install", exc)


async def install_route_blocker_async(
    context_or_page: Any, watch: Watch, policy: BlockPolicy | None = None
) -> None:
    """Async API variant of :func:`install_route_blocker`. Never raises."""
    pol = policy or BlockPolicy()

    async def handler(route: Any) -> None:
        request = route.request
        estimate = _decide(watch, pol, request)
        if estimate is not None:
            try:
                await route.abort(ABORT_ERROR)
            except Exception as exc:
                _abort_failed(watch, request, exc)
            else:
                watch.add_blocked(1, bytes_saved_estimate=estimate)
                return
        try:
            await route.fallback()
        except Exception as exc:
            watch.monitor_error("route blocker fallback", exc)

    try:
        await context_or_page.route(ROUTE_GLOB, handler)
    except Exception as exc:
        watch.monitor_error("route blocker install", exc)
