"""Downloader middleware at priority 900: sees the response before HttpCompression decodes it.

``process_response`` runs from the downloader towards the engine, highest priority first, so
at 900 the body is still as it came off the wire (``HttpCompressionMiddleware`` is at 590):
``wire_bytes = len(response.body) + header estimate`` is ``measured`` (verified with a gzip
server through the full chain in ``tests/integrations/test_scrapy.py``). For the guard the
middleware decodes at most the guard's body window itself (``_decode.decode_window``).

Responses flagged ``cached`` (HttpCacheMiddleware) cost 0 bytes. Retries carry
``request.meta["retry_times"]`` and are linked to their first attempt; a redirected response
carries the first URL (``meta["redirect_urls"][0]``) as ``url``.
"""

from __future__ import annotations

import uuid
from typing import Any

from scrapewatch.cost.bytes import header_bytes_estimate
from scrapewatch.integrations._decode import decode_window
from scrapewatch.integrations.scrapy.extension import current_watch
from scrapewatch.models import ResponseEvent
from scrapewatch.runtime import Watch

KEY_META = "scrapewatch_request_key"  # (url, id): stable across retries, new per redirect hop


def _headers(scrapy_headers: Any) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Scrapy's bytes headers as (name, value) pairs and as a joined dict for the guard."""
    pairs: list[tuple[str, str]] = []
    joined: dict[str, str] = {}
    for name, values in scrapy_headers.items():
        key = name.decode("latin-1") if isinstance(name, bytes) else str(name)
        texts = [v.decode("latin-1") if isinstance(v, bytes) else str(v) for v in values]
        pairs.extend((key, t) for t in texts)
        joined[key] = ", ".join(texts)
    return pairs, joined


class ScrapewatchDownloaderMiddleware:
    """Records every response and download error; never alters or drops them."""

    def __init__(self, crawler: Any) -> None:
        """Read the proxy settings."""
        self.crawler = crawler
        s = crawler.settings
        self.proxy_key = s.get("SCRAPEWATCH_PROXY_META_KEY", "proxy_label")
        self.default_proxy = s.get("SCRAPEWATCH_PROXY")

    @classmethod
    def from_crawler(cls, crawler: Any) -> ScrapewatchDownloaderMiddleware:
        """Scrapy factory."""
        return cls(crawler)

    def _watch(self) -> Watch | None:
        return current_watch(self.crawler)

    def process_request(self, request: Any, spider: Any = None) -> None:
        """Give the request a key that its retries share (redirect hops get a new one)."""
        w = self._watch()
        if w is None:
            return
        try:
            key = request.meta.get(KEY_META)
            if not (isinstance(key, tuple) and key and key[0] == request.url):
                request.meta[KEY_META] = (request.url, uuid.uuid4().hex)
        except Exception as exc:
            w.monitor_error("scrapy process_request", exc)

    def process_response(self, request: Any, response: Any, spider: Any = None) -> Any:
        """Record and return the response unchanged."""
        w = self._watch()
        if w is not None:
            try:
                self._record(w, request, response)
            except Exception as exc:
                w.monitor_error("scrapy process_response", exc)
        return response

    def process_exception(self, request: Any, exception: BaseException, spider: Any = None) -> None:
        """Record a transport error; return None so Scrapy's retry logic runs as usual."""
        w = self._watch()
        if w is None:
            return
        try:
            from scrapy.exceptions import IgnoreRequest

            if isinstance(exception, IgnoreRequest):
                return  # filtered before download (robots.txt, offsite): not a failure
            url, final_url = self._urls(request)
            w.record_response(
                url,
                status=None,
                final_url=final_url,
                method=str(request.method),
                elapsed_ms=self._elapsed(request),
                bytes_source="measured",
                proxy=self._proxy(request),
                error=exception,
                meta=self._meta(request),
            )
        except Exception as exc:
            w.monitor_error("scrapy process_exception", exc)

    # --- helpers ------------------------------------------------------------------------
    def _record(self, w: Watch, request: Any, response: Any) -> ResponseEvent | None:
        pairs, joined = _headers(response.headers)
        cached = "cached" in (response.flags or [])
        body = bytes(response.body or b"")
        wire = 0 if cached else len(body) + header_bytes_estimate(pairs)
        window, total = (None, None)
        if request.method != "HEAD":
            codings = [
                c.decode("latin-1") if isinstance(c, bytes) else str(c)
                for c in response.headers.getlist("Content-Encoding")
            ]
            window, total = decode_window(body, codings, w.settings.guard.body_window_bytes)
        seen = window is not None and total is not None
        url, final_url = self._urls(request)
        return w.record_response(
            url,
            status=int(response.status),
            headers=joined,
            body=window if seen else None,
            wire_bytes=wire,
            body_bytes=total,
            bytes_source="measured",
            final_url=final_url or (response.url if response.url != url else None),
            method=str(request.method),
            elapsed_ms=self._elapsed(request),
            proxy=self._proxy(request),
            meta={**self._meta(request), "body_seen": seen},
        )

    @staticmethod
    def _urls(request: Any) -> tuple[str, str | None]:
        chain = request.meta.get("redirect_urls") or []
        if chain:
            return str(chain[0]), str(request.url)
        return str(request.url), None

    @staticmethod
    def _elapsed(request: Any) -> float | None:
        latency = request.meta.get("download_latency")
        return float(latency) * 1000.0 if isinstance(latency, (int, float)) else None

    def _proxy(self, request: Any) -> str | None:
        label = request.meta.get(self.proxy_key, self.default_proxy)
        return str(label) if label is not None else None

    @staticmethod
    def _meta(request: Any) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        retry_times = request.meta.get("retry_times")
        if isinstance(retry_times, int):
            meta["retry_times"] = retry_times
        key = request.meta.get(KEY_META)
        if isinstance(key, tuple) and len(key) == 2:
            meta["request_key"] = key[1]
        return meta
