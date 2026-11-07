"""A local server for the conditional-cache tests.

- ``/honour/<n>.html``: ETag ``"v<n>"``; answers 304 when ``If-None-Match`` matches.
- ``/since/<n>.html``: Last-Modified only; answers 304 when ``If-Modified-Since`` matches.
- ``/ignore/<n>.html``: sends an ETag but always answers 200 with the same body.
- ``/changing.html``: a new body on every request.
Every request's conditional headers are logged in ``SEEN``.
"""

from __future__ import annotations

import http.server
import itertools
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

LAST_MODIFIED = "Wed, 04 Nov 2026 10:00:00 GMT"
SEEN: list[tuple[str, str | None, str | None]] = []
_COUNTER = itertools.count()


def page(n: str) -> bytes:
    return f"<html><title>Book {n}</title><p>{'A Light in the Attic ' * 400}</p></html>".encode()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, **extra: str) -> None:
        self.send_response(status)
        if status != 304:
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra.items():
            self.send_header(k.replace("_", "-"), v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        inm = self.headers.get("If-None-Match")
        ims = self.headers.get("If-Modified-Since")
        SEEN.append((self.path, inm, ims))
        kind, _, rest = self.path.strip("/").partition("/")
        n = rest.removesuffix(".html")
        if kind == "honour":
            tag = f'"v{n}"'
            if inm == tag:
                self._send(304, b"", ETag=tag)
            else:
                self._send(200, page(n), ETag=tag)
        elif kind == "since":
            if ims == LAST_MODIFIED:
                self._send(304, b"")
            else:
                self._send(200, page(n), Last_Modified=LAST_MODIFIED)
        elif kind == "ignore":
            self._send(200, page(n), ETag=f'"v{n}"')
        elif kind == "changing.html":
            self._send(200, page(str(next(_COUNTER))), ETag=f'"c{next(_COUNTER)}"')
        else:
            self._send(404, b"not found")

    def do_POST(self) -> None:
        self.do_GET()


@contextmanager
def serve() -> Iterator[str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
