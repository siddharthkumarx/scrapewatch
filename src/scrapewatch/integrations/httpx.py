"""httpx event hooks (sync and async). Usage: docs/architecture.md "Integrations" (3.1, 3.5).

``hooks(w)`` returns the ``event_hooks`` mapping for ``httpx.Client``; ``async_hooks(w)`` the
one for ``httpx.AsyncClient``. The request hook stamps the start time; the response hook runs
before httpx reads the body, so it wraps that response's ``read``/``close`` (``aread``/
``aclose``) and records once the body is read or the response is closed (streaming). That is
when ``response.num_bytes_downloaded`` holds the bytes that came off the wire, before
decompression (``bytes_source="measured"``).

One event per logical request: a followed redirect chain is recorded once, when the final
response is complete, with ``url`` the first request's URL, ``final_url`` the last one, and
the bytes of every hop (``response.history``) included. A 3xx that is not followed is an event
of its own.

Per-request options travel in ``request.extensions["scrapewatch"]``, for example
``client.get(url, extensions={"scrapewatch": {"request_key": url, "proxy": "residential"}})``:
``proxy``, ``attempt``, ``retry_times``, ``request_key``, ``resource_type``.

With ``cache=ConditionalCache(...)`` (``scrapewatch.waste``) GET requests carry
``If-None-Match``/``If-Modified-Since`` for pages seen before (headers you set yourself are
kept), and each response updates the cache; :func:`unchanged` tells the scraper whether a
response was a 304 or the same body as last time. Events carry ``cache.conditional`` and
``cache.unchanged``.

Transport errors (connect, timeout, TLS) never reach a response hook; record them with
:func:`record_error` in the scraper's ``except httpx.RequestError`` block.

Every hook fails open: an error inside scrapewatch is counted in ``counts.monitor_errors`` and
the request, response and body reach the scraper unchanged.
"""

from __future__ import annotations

import threading
import time
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from scrapewatch.cost.bytes import content_length, header_bytes_estimate
from scrapewatch.models import ResponseEvent
from scrapewatch.runtime import Watch
from scrapewatch.waste.cache import ConditionalCache, is_conditional

INTEGRATION = "httpx"
EXTENSION_KEY = "scrapewatch"
_META_KEYS = ("retry_times", "request_key", "baseline_p50_bytes")


@dataclass
class _RequestState:
    t0: float | None  # None: the request hook never saw it (no elapsed time)
    recorded: bool = False


# Request -> start time and whether it was recorded. Weak keys: nothing outlives the request,
# and the scraper's objects are never modified (redirect requests share ``extensions``).
_REQUESTS: weakref.WeakKeyDictionary[Any, _RequestState] = weakref.WeakKeyDictionary()
_LOCK = threading.Lock()
# Response -> unchanged (304 or same body hash), for :func:`unchanged`.
_UNCHANGED: weakref.WeakKeyDictionary[Any, bool] = weakref.WeakKeyDictionary()


def _stamp(request: Any) -> None:
    with _LOCK:
        _REQUESTS[request] = _RequestState(t0=time.perf_counter())


def _claim(request: Any) -> _RequestState | None:
    """Mark the request recorded; returns its state, or None if it was recorded already."""
    with _LOCK:
        try:
            state = _REQUESTS.get(request)
        except TypeError:  # not weak-referenceable (a stand-in object): no dedupe
            return _RequestState(t0=None, recorded=True)
        if state is None:
            state = _REQUESTS[request] = _RequestState(t0=None)
        elif state.recorded:
            return None
        state.recorded = True
        return state


def _options(request: Any) -> Mapping[str, Any]:
    ext = getattr(request, "extensions", None) or {}
    opts = ext.get(EXTENSION_KEY) if isinstance(ext, Mapping) else None
    return opts if isinstance(opts, Mapping) else {}


def _use_integration(watch: Watch) -> None:
    if watch.run.integration == "manual":
        watch.run.integration = INTEGRATION


class _Recorder:
    """Shared logic of the sync and async hooks for one Watch."""

    def __init__(
        self, watch: Watch, proxy: str | None, cache: ConditionalCache | None = None
    ) -> None:
        self.watch = watch
        self.proxy = proxy
        self.cache = cache

    def safely(self, where: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            self.watch.monitor_error(f"httpx {where}", exc)
            return None

    # --- request ------------------------------------------------------------------------
    def on_request(self, request: Any) -> None:
        _stamp(request)
        if self.cache is not None and str(request.method).upper() == "GET":
            for name, value in self.cache.request_headers(str(request.url)).items():
                if name not in request.headers:
                    request.headers[name] = value

    def cache_info(
        self, response: Any, body: bytes | None, error: BaseException | None
    ) -> dict[str, bool]:
        conditional = is_conditional(response.request.headers)
        status = int(response.status_code)
        if self.cache is not None and error is None and str(response.request.method) == "GET":
            unchanged = self.cache.observe(str(response.url), status, response.headers, body)
        else:
            unchanged = conditional and status == 304
        with _LOCK:
            _UNCHANGED[response] = unchanged
        return {"conditional": conditional, "unchanged": unchanged}

    # --- response -----------------------------------------------------------------------
    def finish(self, response: Any, error: BaseException | None = None) -> ResponseEvent | None:
        """Record a complete response (body read, or closed while streaming)."""
        if error is None and response.has_redirect_location and response.next_request is None:
            return None  # a hop httpx is following: counted in the final response's history
        history = list(getattr(response, "history", None) or [])
        first = history[0].request if history else response.request
        state = _claim(first)
        if state is None:
            return None
        if first is not response.request:
            _claim(response.request)
        wire = 0
        source = "measured"
        for hop in [*history, response]:
            n, measured = _hop_bytes(hop)
            wire += n + header_bytes_estimate(hop.headers)
            if not measured:
                source = "content_length"
        body = _content(response)
        opts = _options(first)
        cache = self.safely("cache", self.cache_info, response, body, error) or {}
        return self.watch.record_response(
            str(first.url),
            status=int(response.status_code),
            headers=response.headers,
            body=body,
            wire_bytes=wire,
            body_bytes=len(body) if body is not None else None,
            bytes_source=source,
            final_url=str(response.url) if history else None,
            method=str(first.method),
            elapsed_ms=_elapsed_ms(state),
            resource_type=str(opts.get("resource_type", "document")),
            proxy=opts.get("proxy", self.proxy),
            attempt=_int(opts.get("attempt"), 1),
            error=error,
            meta={
                **{k: opts[k] for k in _META_KEYS if k in opts},
                "body_seen": body is not None,
                "cache": cache,
            },
        )

    def error(self, exc: BaseException, request: Any, proxy: str | None) -> ResponseEvent | None:
        state = _claim(request)
        if state is None:
            return None  # already recorded (an error while reading a body we saw)
        opts = _options(request)
        return self.watch.record_response(
            str(request.url),
            status=None,
            method=str(getattr(request, "method", "GET")),
            elapsed_ms=_elapsed_ms(state),
            bytes_source="measured",
            resource_type=str(opts.get("resource_type", "document")),
            proxy=proxy if proxy is not None else opts.get("proxy", self.proxy),
            attempt=_int(opts.get("attempt"), 1),
            error=exc,
            meta={k: opts[k] for k in _META_KEYS if k in opts},
        )


def _content(response: Any) -> bytes | None:
    try:
        content = response.content
    except Exception:  # streamed and never read: no body for the guard
        return None
    return content if isinstance(content, bytes) else None


def _hop_bytes(response: Any) -> tuple[int, bool]:
    """Body bytes off the wire for one response, and whether they were measured.

    ``num_bytes_downloaded`` counts what was read from the network stream. A response a
    transport built already read (``httpx.Response(content=...)``, as mock and some caching
    transports do) has a body but no stream count: its Content-Length is used instead.
    """
    n = int(response.num_bytes_downloaded)
    if n == 0:
        body = _content(response)
        length = content_length(response.headers) if body else None
        if length is not None:
            return length, False
    return n, True


def _elapsed_ms(state: _RequestState) -> float | None:
    return None if state.t0 is None else (time.perf_counter() - state.t0) * 1000.0


def _int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


class _Pending:
    """Per-response flags while the body is being read."""

    __slots__ = ("done", "reading")

    def __init__(self) -> None:
        self.reading = 0
        self.done = False


def _wrap_sync(rec: _Recorder, response: Any) -> None:
    pending = _Pending()
    orig_read = response.read
    orig_close = response.close

    def complete(error: BaseException | None = None) -> None:
        if pending.done:
            return
        pending.done = True
        rec.safely("record", rec.finish, response, error)

    def read() -> bytes:
        pending.reading += 1
        try:
            content: bytes = orig_read()
        except Exception as exc:
            pending.reading -= 1
            complete(exc)
            raise
        except BaseException:  # interrupted or cancelled: the scraper's own control flow
            pending.reading -= 1
            pending.done = True
            raise
        pending.reading -= 1
        complete()
        return content

    def close() -> None:
        try:
            orig_close()
        finally:
            if not pending.reading:
                complete()

    response.read = read
    response.close = close


def _wrap_async(rec: _Recorder, response: Any) -> None:
    pending = _Pending()
    orig_aread = response.aread
    orig_aclose = response.aclose

    def complete(error: BaseException | None = None) -> None:
        if pending.done:
            return
        pending.done = True
        rec.safely("record", rec.finish, response, error)

    async def aread() -> bytes:
        pending.reading += 1
        try:
            content: bytes = await orig_aread()
        except Exception as exc:
            pending.reading -= 1
            complete(exc)
            raise
        except BaseException:  # interrupted or cancelled: the scraper's own control flow
            pending.reading -= 1
            pending.done = True
            raise
        pending.reading -= 1
        complete()
        return content

    async def aclose() -> None:
        try:
            await orig_aclose()
        finally:
            if not pending.reading:
                complete()

    response.aread = aread
    response.aclose = aclose


def unchanged(response: Any) -> bool:
    """True when the hooks found this response unchanged: a 304, or the cache's body hash.

    Without a cache, only a 304 to a conditional request counts. False for a response the
    hooks have not recorded yet (a streamed body not read or closed).
    """
    with _LOCK:
        try:
            return _UNCHANGED.get(response, False)
        except TypeError:
            return False


def hooks(
    watch: Watch, *, proxy: str | None = None, cache: ConditionalCache | None = None
) -> dict[str, list[Callable[[Any], None]]]:
    """Return ``{"request": [...], "response": [...]}`` for ``httpx.Client(event_hooks=...)``.

    ``proxy`` is the cost label of the client's traffic (A§11.1); a request can override it
    with ``extensions={"scrapewatch": {"proxy": ...}}``. Bytes: ``num_bytes_downloaded`` once
    the body is read (measured, compressed). To keep hooks you already have, merge the lists:
    ``{k: mine[k] + sw[k] for k in sw}``. ``cache`` turns on conditional requests (see the
    module docstring).
    """
    rec = _Recorder(watch, proxy, cache)
    _use_integration(watch)

    def on_request(request: Any) -> None:
        rec.safely("request hook", rec.on_request, request)

    def on_response(response: Any) -> None:
        rec.safely("response hook", _wrap_sync, rec, response)

    return {"request": [on_request], "response": [on_response]}


def async_hooks(
    watch: Watch, *, proxy: str | None = None, cache: ConditionalCache | None = None
) -> dict[str, list[Callable[[Any], Awaitable[None]]]]:
    """Same for ``httpx.AsyncClient(event_hooks=...)`` (the hooks are coroutines).

    The cache is read and written from the hooks (SQLite, batched writes), as in ``hooks``.
    """
    rec = _Recorder(watch, proxy, cache)
    _use_integration(watch)

    async def on_request(request: Any) -> None:
        rec.safely("request hook", rec.on_request, request)

    async def on_response(response: Any) -> None:
        rec.safely("response hook", _wrap_async, rec, response)

    return {"request": [on_request], "response": [on_response]}


def record_error(
    watch: Watch,
    exc: BaseException,
    request: Any = None,
    *,
    proxy: str | None = None,
) -> ResponseEvent | None:
    """Record a transport failure (``httpx.RequestError``) as an ``error`` event.

    Call it from the scraper's ``except`` block::

        try:
            r = client.get(url)
        except httpx.RequestError as exc:
            sw_httpx.record_error(w, exc)

    ``request`` defaults to ``exc.request``. A request that was already recorded (the error
    happened while reading a body the hooks saw) is not recorded twice. Never raises.
    """
    rec = _Recorder(watch, None)
    try:
        if request is None:
            try:
                request = exc.request  # type: ignore[attr-defined]
            except (AttributeError, RuntimeError):
                return None  # nothing tells us which URL failed
        _use_integration(watch)
    except Exception as err:
        watch.monitor_error("httpx record_error", err)
        return None
    result: ResponseEvent | None = rec.safely("record_error", rec.error, exc, request, proxy)
    return result
