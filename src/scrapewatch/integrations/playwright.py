"""Playwright hooks (sync and async). Usage: docs/architecture.md "Integrations" (3.5) and 11.3.

``attach(context_or_page, w)`` listens on a BrowserContext (every page in it) or one Page:

- ``requestfinished``: one event per network response, including each redirect hop, once the
  body has arrived, so ``request.sizes()`` is final. ``wire_bytes`` is
  ``responseBodySize + responseHeadersSize`` (encoded, as on the wire): ``measured``.
- ``requestfailed``: an ``error`` event (no status) with Playwright's failure text.
- ``close``: browser time from ``attach`` to the close of the context or page is added to
  the run (``Watch.add_browser_seconds``), priced by ``cost.browser.per_minute``. Close the
  context (or page) before the ``watch()`` block ends, or the time is not counted.

Resource types map to the data model: document, stylesheet, image, media, font, script;
``xhr`` and ``fetch`` become ``xhr``; anything else is ``other``. The guard reads the body
and all headers (cookie names included) of documents only; sub-resources are judged by
status and headers. A response that follows a redirect carries the first URL of the chain as
``url`` and its own as ``final_url``.

Requests the waste cutter aborted (``scrapewatch.waste.install_route_blocker``) are in
``ABORTED``: ``requestfailed`` skips them, because a block is counted as
``waste.blocked_requests``, not as a failed request. Recorded sizes per resource type are kept
per Watch (``median_size``), so the blocker can estimate what a blocked request would have
cost.

Every listener fails open: an error inside scrapewatch is counted in ``counts.monitor_errors``
and the page keeps loading.
"""

from __future__ import annotations

import statistics
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from scrapewatch.models import ResponseEvent
from scrapewatch.runtime import Watch

INTEGRATION = "playwright"
RESOURCE_TYPES = {
    "document": "document",
    "stylesheet": "stylesheet",
    "image": "image",
    "media": "media",
    "font": "font",
    "script": "script",
    "xhr": "xhr",
    "fetch": "xhr",
}

SIZE_SAMPLES = 200  # recent sizes kept per resource type, for blocked-request estimates

# Requests aborted by the route blocker; their ``requestfailed`` is not an error event.
ABORTED: weakref.WeakSet[Any] = weakref.WeakSet()
_SIZES: weakref.WeakKeyDictionary[Watch, dict[str, deque[int]]] = weakref.WeakKeyDictionary()
_SIZES_LOCK = threading.Lock()


def note_size(watch: Watch, resource_type: str, wire_bytes: int) -> None:
    """Remember a measured response size of ``resource_type`` for this run."""
    if wire_bytes <= 0:
        return
    with _SIZES_LOCK:
        per_type = _SIZES.setdefault(watch, {})
        per_type.setdefault(resource_type, deque(maxlen=SIZE_SAMPLES)).append(wire_bytes)


def median_size(watch: Watch, resource_type: str) -> int | None:
    """Median wire size of recent ``resource_type`` responses in this run, or None."""
    with _SIZES_LOCK:
        sizes = list(_SIZES.get(watch, {}).get(resource_type, ()))
    return int(statistics.median(sizes)) if sizes else None


def resource_type_of(request: Any) -> str:
    """Playwright's resource type mapped to the data model (``xhr``/``fetch`` → ``xhr``)."""
    return RESOURCE_TYPES.get(str(request.resource_type), "other")


class RequestFailedError(Exception):
    """A browser request that got no response (``requestfailed``); the message is Playwright's."""


def _use_integration(watch: Watch) -> None:
    if watch.run.integration == "manual":
        watch.run.integration = INTEGRATION


def _origin(request: Any) -> str:
    """URL of the first request of a redirect chain."""
    first = request
    for _ in range(50):  # Chromium stops at 20 redirects; never loop forever
        prev = first.redirected_from
        if prev is None:
            break
        first = prev
    return str(first.url)


def _elapsed_ms(request: Any) -> float | None:
    timing = request.timing
    end = timing.get("responseEnd", -1) if isinstance(timing, Mapping) else -1
    return float(end) if isinstance(end, (int, float)) and end >= 0 else None


def _wire(sizes: Any) -> int:
    if not isinstance(sizes, Mapping):
        return 0
    body = sizes.get("responseBodySize", 0) or 0
    head = sizes.get("responseHeadersSize", 0) or 0
    return max(0, int(body)) + max(0, int(head))


def _wants_body(request: Any, status: int) -> bool:
    return resource_type_of(request) == "document" and not 300 <= status < 400


class _Recorder:
    def __init__(self, watch: Watch, proxy: str | None) -> None:
        self.watch = watch
        self.proxy = proxy
        self.started = time.perf_counter()
        self._closed = False
        self._lock = threading.Lock()

    def safely(self, where: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            self.watch.monitor_error(f"playwright {where}", exc)
            return None

    def record(
        self,
        request: Any,
        *,
        status: int,
        headers: Mapping[str, str],
        sizes: Any,
        body: bytes | None,
    ) -> ResponseEvent | None:
        url = str(request.url)
        origin = _origin(request)
        wire = _wire(sizes)
        rtype = resource_type_of(request)
        self.safely("size sample", note_size, self.watch, rtype, wire)
        return self.watch.record_response(
            origin,
            status=status,
            headers=headers,
            body=body,
            wire_bytes=wire,
            body_bytes=len(body) if body is not None else None,
            bytes_source="measured",
            final_url=url if origin != url else None,
            method=str(request.method),
            elapsed_ms=_elapsed_ms(request),
            resource_type=rtype,
            proxy=self.proxy,
            meta={"body_seen": body is not None},
        )

    def failed(self, request: Any) -> ResponseEvent | None:
        if request in ABORTED:  # blocked by the waste cutter: counted there, not an error
            return None
        url = str(request.url)
        origin = _origin(request)
        return self.watch.record_response(
            origin,
            status=None,
            final_url=url if origin != url else None,
            method=str(request.method),
            bytes_source="measured",
            resource_type=resource_type_of(request),
            proxy=self.proxy,
            error=RequestFailedError(str(request.failure or "request failed")),
        )

    def closed(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.watch.add_browser_seconds(time.perf_counter() - self.started)


def _finished_sync(rec: _Recorder, request: Any) -> None:
    response = request.response()
    if response is None:  # pragma: no cover - a finished request always has a response
        return
    status = int(response.status)
    sizes = request.sizes()
    body: bytes | None = None
    headers: Mapping[str, str] = response.headers
    if _wants_body(request, status):
        headers = response.all_headers()
        body = response.body()
    rec.record(request, status=status, headers=headers, sizes=sizes, body=body)


async def _finished_async(rec: _Recorder, request: Any) -> None:
    response = await request.response()
    if response is None:  # pragma: no cover - a finished request always has a response
        return
    status = int(response.status)
    sizes = await request.sizes()
    body: bytes | None = None
    headers: Mapping[str, str] = response.headers
    if _wants_body(request, status):
        headers = await response.all_headers()
        body = await response.body()
    rec.record(request, status=status, headers=headers, sizes=sizes, body=body)


def attach(context_or_page: Any, watch: Watch, *, proxy: str | None = None) -> None:
    """Listen to ``requestfinished``, ``requestfailed`` and ``close`` (sync API).

    ``context_or_page`` is a ``BrowserContext`` (all its pages) or a ``Page``; ``proxy`` is
    the cost label of its traffic (A§11.1). Never raises.
    """
    try:
        rec = _Recorder(watch, proxy)
        _use_integration(watch)

        def on_finished(request: Any) -> None:
            rec.safely("requestfinished", _finished_sync, rec, request)

        def on_failed(request: Any) -> None:
            rec.safely("requestfailed", rec.failed, request)

        def on_close(*_: Any) -> None:
            rec.safely("close", rec.closed)

        context_or_page.on("requestfinished", on_finished)
        context_or_page.on("requestfailed", on_failed)
        context_or_page.on("close", on_close)
    except Exception as exc:
        watch.monitor_error("playwright attach", exc)


async def attach_async(context_or_page: Any, watch: Watch, *, proxy: str | None = None) -> None:
    """Async API variant of :func:`attach` (the listeners are coroutines). Never raises."""
    try:
        rec = _Recorder(watch, proxy)
        _use_integration(watch)

        async def on_finished(request: Any) -> None:
            try:
                await _finished_async(rec, request)
            except Exception as exc:
                watch.monitor_error("playwright requestfinished", exc)

        async def on_failed(request: Any) -> None:
            rec.safely("requestfailed", rec.failed, request)

        async def on_close(*_: Any) -> None:
            rec.safely("close", rec.closed)

        context_or_page.on("requestfinished", on_finished)
        context_or_page.on("requestfailed", on_failed)
        context_or_page.on("close", on_close)
    except Exception as exc:
        watch.monitor_error("playwright attach", exc)
