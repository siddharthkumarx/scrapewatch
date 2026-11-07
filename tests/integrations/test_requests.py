"""requests adapter (build item 17) against a local http.server serving gzip bodies.

Also the evidence for architecture 11.3: urllib3's ``tell()`` gives the compressed byte count
for Content-Length and close-delimited bodies; chunked bodies are counted at the decoder.
"""

from __future__ import annotations

import gzip
import http.server
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scrapewatch.config import Settings
from scrapewatch.cost.bytes import header_bytes_estimate
from scrapewatch.integrations import requests as sw_requests
from scrapewatch.models import BytesSource, GuardLabel, ResponseEvent
from scrapewatch.runtime import Watch

requests = pytest.importorskip("requests")

pytestmark = pytest.mark.extras

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PAGE = (FIXTURES / "guard" / "html" / "product.html").read_bytes()
GZ = gzip.compress(PAGE, mtime=0)
BIG = b"<html><head><title>Big</title></head><body>" + b"<p>a book</p>" * 8000 + b"</body></html>"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def _head(self, status: int = 200, **extra: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for k, v in extra.items():
            self.send_header(k.replace("_", "-"), v)

    def do_HEAD(self) -> None:
        self._head(Content_Length=str(len(PAGE)))
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/cl":
            self._head(Content_Encoding="gzip", Content_Length=str(len(GZ)), Set_Cookie="sid=1")
            self.end_headers()
            self.wfile.write(GZ)
        elif path in ("/chunked", "/big"):
            body = GZ if path == "/chunked" else gzip.compress(BIG, mtime=0)
            self._head(Content_Encoding="gzip", Transfer_Encoding="chunked")
            self.end_headers()
            for i in range(0, len(body), 100):
                part = body[i : i + 100]
                self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
            self.wfile.write(b"0\r\n\r\n")
        elif path == "/close":
            self._head(Content_Encoding="gzip", Connection="close")
            self.end_headers()
            self.wfile.write(GZ)
            self.close_connection = True
        elif path == "/old":
            self._head(301, Location="/mid", Content_Length="0")
            self.end_headers()
        elif path == "/mid":
            self._head(302, Location="/login#top", Content_Length="0")
            self.end_headers()
        elif path == "/login":
            body = (
                b"<html><head><title>Example Shop</title></head><body>"
                + b"<p>Welcome to the shop, browse our catalogue.</p>" * 60
                + b"</body></html>"
            )
            self._head(Content_Length=str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/truncated":  # a chunk shorter than announced, then the connection ends
            self._head(Content_Encoding="gzip", Transfer_Encoding="chunked")
            self.end_headers()
            self.wfile.write(b"%x\r\n%s" % (len(GZ) + 50, GZ))
            self.close_connection = True
        else:
            self._head(404, Content_Length="9")
            self.end_headers()
            self.wfile.write(b"not found")


@pytest.fixture(scope="module")
def base() -> Iterator[str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _watch() -> Watch:
    w = Watch(Settings(name="t"))
    w.start()
    return w


def _spy(w: Watch, monkeypatch: pytest.MonkeyPatch) -> list[ResponseEvent]:
    events: list[ResponseEvent] = []
    real = w.record_response

    def spy(*args: Any, **kwargs: Any) -> ResponseEvent | None:
        ev = real(*args, **kwargs)
        if ev is not None:
            events.append(ev)
        return ev

    monkeypatch.setattr(w, "record_response", spy)
    return events


def _session(w: Watch, **kwargs: Any) -> Any:
    s = requests.Session()
    sw_requests.mount(s, w, **kwargs)
    return s


def _wire(r: Any, body: int) -> int:
    return body + header_bytes_estimate(r.raw.headers)


# --- risk 1: what urllib3 reports ----------------------------------------------------------


@pytest.mark.parametrize("path", ["/cl", "/close", "/chunked"])
def test_urllib3_tell_evidence(base: str, path: str) -> None:
    """tell() is the compressed count except for chunked bodies, where it stays 0."""
    with requests.Session() as s:
        r = s.get(base + path)
    assert r.content == PAGE
    assert r.raw.tell() == (0 if path == "/chunked" else len(GZ))


# --- bytes ---------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/cl", "/close", "/chunked"])
@pytest.mark.parametrize("stream", [False, True])
def test_gzip_measured_compressed(
    base: str, path: str, stream: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with _session(w, proxy="datacenter") as s:
        r = s.get(base + path, stream=stream)
        assert r.content == PAGE  # the scraper's body is untouched (decoded)
        r.close()
    assert len(events) == 1
    ev = events[0]
    assert ev.wire_bytes == _wire(r, len(GZ))
    assert ev.bytes_source == BytesSource.MEASURED
    assert ev.body_bytes == len(PAGE)
    assert ev.status == 200
    assert ev.proxy == "datacenter"
    assert ev.integration == "requests"
    assert ev.guard.label == GuardLabel.OK
    assert ev.elapsed_ms is not None
    assert w.run.counts.monitor_errors == 0


def test_fallback_labels_without_a_count(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """No tell() and no decoder count: Content-Length when present, else decoded length."""
    monkeypatch.setattr(sw_requests, "_count_raw", lambda raw: None)
    w = _watch()
    events = _spy(w, monkeypatch)
    with _session(w) as s:
        r1 = s.get(base + "/chunked")
        monkeypatch.setattr(type(r1.raw), "tell", lambda self: 0)
        r2 = s.get(base + "/cl")
    chunked, with_length = events
    assert (chunked.bytes_source, chunked.wire_bytes) == (
        BytesSource.DECODED,
        _wire(r1, len(PAGE)),
    )
    assert (with_length.bytes_source, with_length.wire_bytes) == (
        BytesSource.CONTENT_LENGTH,
        _wire(r2, len(GZ)),
    )


def test_head_and_404(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with _session(w) as s:
        head = s.head(base + "/cl")
        missing = s.get(base + "/nope")
    h, m = events
    assert (h.method, h.status, h.wire_bytes, h.bytes_source) == (
        "HEAD",
        200,
        _wire(head, 0),
        BytesSource.MEASURED,
    )
    assert (m.status, m.wire_bytes) == (404, _wire(missing, 9))


# --- streaming -------------------------------------------------------------------------------


def test_streaming_large_body_is_not_judged_by_a_partial_window(
    base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with _session(w) as s:
        r = s.get(base + "/big", stream=True)
        total = sum(len(c) for c in r.iter_content(4096))
        r.close()
        r2 = s.get(base + "/chunked", stream=True)
        r2.close()  # closed unread
        r3 = s.get(base + "/cl", stream=True)
        text = "".join(r3.iter_content(1000, decode_unicode=True))
    assert total == len(BIG)
    big, unread, decoded = events
    assert big.body_bytes == len(BIG)
    assert big.bytes_source == BytesSource.MEASURED
    assert big.guard.label == GuardLabel.OK  # bigger than the window: body not judged
    assert (unread.body_bytes, unread.guard.label) == (0, GuardLabel.OK)
    assert len(text) > 0
    assert decoded.guard.label == GuardLabel.OK
    assert decoded.wire_bytes == _wire(r3, len(GZ))


# --- redirects ---------------------------------------------------------------------------------


def test_redirect_chain_links_final_url(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with _session(w) as s:
        r = s.get(base + "/old")
        s.get(base + "/old", allow_redirects=False)
    assert [h.status_code for h in r.history] == [301, 302]
    hop1, hop2, final, alone = events
    assert (hop1.status, hop1.url, hop1.final_url) == (301, base + "/old", None)
    assert (hop2.status, hop2.url, hop2.final_url) == (302, base + "/old", base + "/mid")
    assert (final.status, final.url, final.final_url) == (200, base + "/old", base + "/login#top")
    assert final.guard.label == GuardLabel.REDIRECT
    assert final.guard.rule_id == "generic.redirect.login"
    assert (alone.status, alone.final_url) == (301, None)


def test_chain_targets_expire(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with _session(w) as s:
        s.get(base + "/mid", allow_redirects=False)
        monkeypatch.setattr(sw_requests, "CHAIN_TTL_SECONDS", -1.0)
        s.get(base + "/login")
        monkeypatch.setattr(sw_requests, "CHAIN_MAX", 0)
        s.get(base + "/mid", allow_redirects=False)  # remembered, then evicted at once
        s.get(base + "/login")
    assert [e.final_url for e in events] == [None, None, None, None]


# --- errors and fail-open ---------------------------------------------------------------------


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    sock.close()
    return port


def test_connection_error_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    url = f"http://127.0.0.1:{_closed_port()}/x?api_key=secret"
    with _session(w, proxy="dc") as s, pytest.raises(requests.ConnectionError):
        s.get(url)
    assert len(events) == 1
    ev = events[0]
    assert ev.status is None
    assert ev.guard.label == GuardLabel.ERROR
    assert ev.error is not None
    assert ev.error.type == "ConnectionError"
    assert "secret" not in ev.url
    assert ev.proxy == "dc"


def test_body_read_error_recorded(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()
    events = _spy(w, monkeypatch)
    with _session(w) as s:
        with pytest.raises(requests.exceptions.ChunkedEncodingError):
            s.get(base + "/truncated")
        r = s.get(base + "/truncated", stream=True)
        with pytest.raises(requests.exceptions.ChunkedEncodingError):
            r.content  # noqa: B018
    assert [(e.status, e.error.type if e.error else None) for e in events] == [
        (200, "ChunkedEncodingError"),
        (200, "ChunkedEncodingError"),
    ]


@pytest.mark.parametrize(
    "target", ["header_bytes_estimate", "_count_raw", "_wrap_stream", "_strip_fragment"]
)
def test_failing_monitor_does_not_break_the_request(
    base: str, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _watch()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(sw_requests, target, boom)
    with _session(w) as s:
        r = s.get(base + "/cl", stream=target == "_wrap_stream")
        assert r.content == PAGE
        assert r.status_code == 200
    assert w.run.counts.monitor_errors == 1


def test_failing_error_recording_keeps_the_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    w = _watch()

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("monitor bug")

    monkeypatch.setattr(sw_requests, "_strip_fragment", boom)
    with _session(w) as s, pytest.raises(requests.ConnectionError):
        s.get(f"http://127.0.0.1:{_closed_port()}/")
    assert w.run.counts.monitor_errors == 1


def test_adapter_keeps_httpadapter_arguments(base: str) -> None:
    w = Watch(Settings(name="t"), integration="mine")
    a = sw_requests.adapter(w, proxy="p", max_retries=3, pool_maxsize=2)
    assert isinstance(a, requests.adapters.HTTPAdapter)
    assert a.max_retries.total == 3
    assert w.run.integration == "mine"  # a name given to watch() is kept
    assert sw_requests.adapter(w).__class__ is type(a)  # the class is built once


def test_helpers_degrade_quietly() -> None:
    assert sw_requests._count_raw(object()) is None  # no urllib3 decoder to count at
    assert sw_requests._strip_fragment("http://[::1/x#f") == "http://[::1/x#f"  # unparsable
