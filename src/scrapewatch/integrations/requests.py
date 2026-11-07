"""requests adapter. Usage: docs/architecture.md "Integrations" (3.5) and 11.3.

``mount(session, w)`` mounts a :class:`requests.adapters.HTTPAdapter` subclass on
``http://`` and ``https://``; ``adapter(w)`` returns one to mount yourself (it takes the usual
``HTTPAdapter`` arguments, such as ``max_retries``).

Bytes off the wire: urllib3's ``HTTPResponse.tell()`` counts the body bytes read from the
socket before decompression, once the body has been read. It stays 0 for chunked bodies
(urllib3 reads those through another path), so the adapter also counts the raw chunk payload
urllib3 hands to its decoder. Both are ``bytes_source="measured"`` (chunk-size lines are not
included). Only when neither count is available does it fall back to Content-Length
(``content_length``) or the decoded length (``decoded``, an over-estimate when compressed).

The adapter reads the body inside ``send`` when the request is not streamed (requests reads
it right after anyway). With ``stream=True`` the event is recorded when ``iter_content`` is
exhausted or the response is closed; the guard then sees the body only if it fit in the
guard's body window. Redirects: requests sends each hop through the adapter, so each hop is
an event; the response that follows a redirect carries the first URL as ``url`` and its own
as ``final_url``, so redirect rules (login, geo, consent) apply.

Retries inside urllib3 (``max_retries``) are not visible to the adapter; to count retries
done by your own code, record them with ``Watch.record_response(meta={"request_key": ...})``.
Every hook fails open: errors inside scrapewatch are counted in ``counts.monitor_errors``.
"""

from __future__ import annotations

import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from scrapewatch.cost.bytes import content_length, header_bytes_estimate
from scrapewatch.models import ResponseEvent
from scrapewatch.runtime import Watch

INTEGRATION = "requests"
CHAIN_TTL_SECONDS = 60.0  # a redirect's follow-up must arrive within this time
CHAIN_MAX = 32  # pending redirect targets remembered per thread
_REDIRECT_STATUS = (301, 302, 303, 307, 308)


class _Counter:
    """Raw body bytes urllib3 passed to its decoder (the chunked path)."""

    __slots__ = ("n",)

    def __init__(self) -> None:
        self.n = 0


def _count_raw(raw: Any) -> _Counter | None:
    """Wrap ``raw._decode`` on this one urllib3 response to count the bytes it receives."""
    decode = getattr(raw, "_decode", None)
    if not callable(decode):
        return None
    counter = _Counter()

    def counting(data: bytes, *args: Any, **kwargs: Any) -> Any:
        if data:
            counter.n += len(data)
        return decode(data, *args, **kwargs)

    raw._decode = counting
    return counter


def _strip_fragment(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit(parts._replace(fragment=""))


def _use_integration(watch: Watch) -> None:
    if watch.run.integration == "manual":
        watch.run.integration = INTEGRATION


class _Recorder:
    def __init__(self, watch: Watch, proxy: str | None) -> None:
        self.watch = watch
        self.proxy = proxy
        self.counters: weakref.WeakKeyDictionary[Any, _Counter] = weakref.WeakKeyDictionary()
        self._local = threading.local()

    def safely(self, where: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            self.watch.monitor_error(f"requests {where}", exc)
            return None

    # --- redirect chains (per thread: requests follows redirects on the calling thread) ----
    def _chains(self) -> OrderedDict[str, tuple[str, float]]:
        chains: OrderedDict[str, tuple[str, float]] | None = getattr(self._local, "chains", None)
        if chains is None:
            chains = self._local.chains = OrderedDict()
        return chains

    def _origin(self, url: str) -> str | None:
        """The first URL of the redirect chain this request continues, if any."""
        found = self._chains().pop(_strip_fragment(url), None)
        if found is None or time.monotonic() - found[1] > CHAIN_TTL_SECONDS:
            return None
        return found[0]

    def _remember_redirect(self, response: Any, origin: str) -> None:
        location = response.headers.get("location")
        if response.status_code not in _REDIRECT_STATUS or not location:
            return
        target = _strip_fragment(urljoin(str(response.url), str(location)))
        from requests.utils import requote_uri

        target = requote_uri(target)
        chains = self._chains()
        chains[target] = (origin, time.monotonic())
        chains.move_to_end(target)
        while len(chains) > CHAIN_MAX:
            chains.popitem(last=False)

    # --- recording ----------------------------------------------------------------------
    def prepare(self, response: Any) -> None:
        counter = _count_raw(response.raw)
        if counter is not None:
            self.counters[response] = counter

    def wire(self, response: Any, body: bytes | None, body_len: int) -> tuple[int, str]:
        raw = response.raw
        headers = getattr(raw, "headers", None) or response.headers
        told = int(raw.tell()) if callable(getattr(raw, "tell", None)) else 0
        counter = self.counters.get(response)
        counted = counter.n if counter is not None else 0
        n = told or counted
        if n or not body_len:
            return n + header_bytes_estimate(headers), "measured"
        length = content_length(response.headers)
        if length is not None:
            return length + header_bytes_estimate(headers), "content_length"
        return body_len + header_bytes_estimate(headers), "decoded"

    def record(
        self,
        response: Any,
        started: float,
        *,
        body: bytes | None,
        body_len: int,
        body_seen: bool,
        error: BaseException | None = None,
    ) -> ResponseEvent | None:
        url = str(response.request.url if response.request is not None else response.url)
        origin = self._origin(url)
        wire, source = self.wire(response, body, body_len)
        raw = response.raw
        headers = getattr(raw, "headers", None) or response.headers
        event = self.watch.record_response(
            origin or url,
            status=int(response.status_code),
            headers=headers,
            body=body if body_seen else None,
            wire_bytes=wire,
            body_bytes=body_len,
            bytes_source=source,
            final_url=url if origin else None,
            method=str(getattr(response.request, "method", None) or "GET"),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            proxy=self.proxy,
            error=error,
            meta={"body_seen": body_seen},
        )
        self._remember_redirect(response, origin or url)
        return event

    def record_error(self, request: Any, exc: BaseException, started: float) -> None:
        url = str(request.url)
        origin = self._origin(url)
        self.watch.record_response(
            origin or url,
            status=None,
            final_url=url if origin else None,
            method=str(request.method or "GET"),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            bytes_source="measured",
            proxy=self.proxy,
            error=exc,
        )


def _wrap_stream(rec: _Recorder, response: Any, started: float) -> None:
    """Record a streamed response when its content is exhausted or it is closed."""
    window = rec.watch.settings.guard.body_window_bytes
    state = {"done": False, "len": 0}
    kept: list[bytes] = []
    orig_iter = response.iter_content
    orig_close = response.close

    def complete(error: BaseException | None = None) -> None:
        if state["done"]:
            return
        state["done"] = True
        seen = 0 < state["len"] <= window and error is None
        body = b"".join(kept) if seen else None
        rec.safely(
            "record",
            rec.record,
            response,
            started,
            body=body,
            body_len=state["len"],
            body_seen=seen,
            error=error,
        )

    def iter_content(*args: Any, **kwargs: Any) -> Iterator[Any]:
        try:
            for chunk in orig_iter(*args, **kwargs):
                if not state["done"]:
                    rec.safely("count", _keep, chunk, kept, state, window)
                yield chunk
        except Exception as exc:
            complete(exc)
            raise
        complete()

    def close() -> None:
        try:
            orig_close()
        finally:
            complete()

    response.iter_content = iter_content
    response.close = close


def _keep(chunk: Any, kept: list[bytes], state: dict[str, Any], window: int) -> None:
    data = chunk.encode("utf-8", errors="replace") if isinstance(chunk, str) else bytes(chunk)
    state["len"] += len(data)
    if state["len"] <= window:
        kept.append(data)
    else:
        kept.clear()


_CLASS: Any = None  # the HTTPAdapter subclass, built on first use
_CLASS_LOCK = threading.Lock()


def _adapter_class() -> Any:
    global _CLASS
    with _CLASS_LOCK:
        if _CLASS is not None:
            return _CLASS
        from requests.adapters import HTTPAdapter

        class ScrapewatchAdapter(HTTPAdapter):
            """``HTTPAdapter`` that records every response and transport error."""

            def __init__(self, watch: Watch, proxy: str | None = None, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.scrapewatch = _Recorder(watch, proxy)
                _use_integration(watch)

            def build_response(self, req: Any, resp: Any) -> Any:
                response = super().build_response(req, resp)
                self.scrapewatch.safely("build_response", self.scrapewatch.prepare, response)
                return response

            def send(self, request: Any, stream: bool = False, *args: Any, **kwargs: Any) -> Any:
                rec = self.scrapewatch
                started = time.perf_counter()
                try:
                    response = super().send(request, stream, *args, **kwargs)
                except Exception as exc:
                    rec.safely("send", rec.record_error, request, exc, started)
                    raise
                if stream:
                    rec.safely("stream", _wrap_stream, rec, response, started)
                    return response
                try:
                    body = response.content
                except Exception as exc:
                    rec.safely(
                        "record",
                        rec.record,
                        response,
                        started,
                        body=None,
                        body_len=0,
                        body_seen=False,
                        error=exc,
                    )
                    raise
                body = body if isinstance(body, bytes) else b""
                rec.safely(
                    "record",
                    rec.record,
                    response,
                    started,
                    body=body,
                    body_len=len(body),
                    body_seen=True,
                )
                return response

        _CLASS = ScrapewatchAdapter
        return _CLASS


def adapter(watch: Watch, *, proxy: str | None = None, **adapter_kwargs: Any) -> Any:
    """Return an ``HTTPAdapter`` subclass instance to ``session.mount("https://", ...)``.

    ``proxy`` is the cost label of the traffic through this adapter (A§11.1);
    ``adapter_kwargs`` go to ``HTTPAdapter`` (``max_retries``, ``pool_maxsize``, ...).
    """
    return _adapter_class()(watch, proxy, **adapter_kwargs)


def mount(session: Any, watch: Watch, *, proxy: str | None = None, **adapter_kwargs: Any) -> None:
    """Mount one recording adapter on ``http://`` and ``https://`` of ``session``."""
    a = adapter(watch, proxy=proxy, **adapter_kwargs)
    session.mount("http://", a)
    session.mount("https://", a)
