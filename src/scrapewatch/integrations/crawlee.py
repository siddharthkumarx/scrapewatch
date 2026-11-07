"""Crawlee for Python (D20). Usage: docs/architecture.md "Integrations" (3.5).

HTTP crawlers (``BeautifulSoupCrawler``, ``ParselCrawler``, ``HttpCrawler``): Crawlee's
response object has no byte counts, and its default client (impit) exposes none, so the
crawler must use the client this module builds: Crawlee's ``HttpxHttpClient`` with the httpx
hooks of :mod:`scrapewatch.integrations.httpx` (measured, compressed bytes)::

    crawler = BeautifulSoupCrawler(http_client=sw_crawlee.http_client(w, proxy="datacenter"))
    sw_crawlee.attach(crawler, w)   # checks the wiring

``PlaywrightCrawler``: ``attach`` adds a pre-navigation hook that attaches the Playwright
listeners of :mod:`scrapewatch.integrations.playwright` to every page (``request.sizes()``,
measured), with browser time from page open to close.

Transport errors that Crawlee retries never reach a response hook; Crawlee counts them in
its own statistics. ``AdaptivePlaywrightCrawler`` is not supported (it runs both kinds).
"""

from __future__ import annotations

import weakref
from typing import Any

from scrapewatch.integrations import httpx as sw_httpx
from scrapewatch.integrations import playwright as sw_pw
from scrapewatch.runtime import Watch

INTEGRATION = "crawlee"
_CLIENTS: weakref.WeakSet[Any] = weakref.WeakSet()  # clients built by http_client()


def _use_integration(watch: Watch) -> None:
    if watch.run.integration == "manual":
        watch.run.integration = INTEGRATION


def http_client(watch: Watch, *, proxy: str | None = None, **client_kwargs: Any) -> Any:
    """Crawlee's ``HttpxHttpClient`` with scrapewatch's httpx hooks (merged with any given).

    ``client_kwargs`` go to ``HttpxHttpClient`` (and on to ``httpx2.AsyncClient``).
    """
    from crawlee.http_clients import HttpxHttpClient

    _use_integration(watch)
    sw = sw_httpx.async_hooks(watch, proxy=proxy)
    mine = client_kwargs.pop("event_hooks", None) or {}
    hooks = {k: [*mine.get(k, []), *sw[k]] for k in sw}
    client = HttpxHttpClient(event_hooks=hooks, **client_kwargs)
    _CLIENTS.add(client)
    return client


def _class_names(obj: Any) -> set[str]:
    return {cls.__name__ for cls in type(obj).__mro__}


def attach(crawler: Any, watch: Watch, *, proxy: str | None = None) -> None:
    """Wire a Crawlee crawler to ``watch``.

    Raises:
        TypeError: the crawler cannot be measured as configured (an HTTP crawler without
            :func:`http_client`, or an unsupported crawler class). Raised at setup, before
            any request, so a wiring mistake is never silent.
    """
    names = _class_names(crawler)
    if "AdaptivePlaywrightCrawler" in names:
        raise TypeError("scrapewatch: AdaptivePlaywrightCrawler is not supported")
    if "PlaywrightCrawler" in names:
        _use_integration(watch)

        async def hook(context: Any) -> None:
            await sw_pw.attach_async(context.page, watch, proxy=proxy)

        crawler.pre_navigation_hook(hook)
        return
    if "AbstractHttpCrawler" in names:
        client = getattr(crawler, "_http_client", None)
        if client is not None and client not in _CLIENTS:
            raise TypeError(
                "scrapewatch: Crawlee's HTTP responses carry no byte counts; create the "
                "crawler with http_client=sw_crawlee.http_client(w)"
            )
        _use_integration(watch)
        return
    raise TypeError(f"scrapewatch: unsupported Crawlee crawler {type(crawler).__name__}")
