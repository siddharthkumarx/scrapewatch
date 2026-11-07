"""A local static site for the Playwright tests: one page with 3 images and 1 font.

``/waste.html`` is the waste cutter's page: 2 images, 1 font and 1 script on a tracker host
(the route blocker intercepts it, so it never reaches the network), plus ``/img/keep.png``
added after the load event (allowlisted in the tests, so its estimate never uses a size seen
earlier in the run). The data URL icon stops Chromium asking for ``/favicon.ico``.
"""

from __future__ import annotations

import gzip
import http.server
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# A 1x1 PNG, padded so the gzip image is clearly smaller than its decoded size.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
PADDED = PNG + b"\0" * 4000
GZ_IMAGE = gzip.compress(PADDED, mtime=0)
FONT = b"wOF2" + bytes(range(256)) * 8
TEXT = "<p>A Light in the Attic, Tipping the Velvet, Soumission, Sharp Objects.</p>" * 20
PAGE = (
    "<!doctype html><html><head><title>All products | Books</title><style>"
    "@font-face{font-family:T;src:url(/font.woff2) format('woff2')}body{font-family:T}"
    "</style></head><body>"
    '<img src="/img/a.png"><img src="/img/b.png"><img src="/img/gz.png">'
    f"{TEXT}</body></html>"
).encode()
TRACKER_SCRIPT = "https://static.hotjar.com/c/hotjar-1.js"
WASTE = (
    '<!doctype html><html><head><title>Waste</title><link rel="icon" href="data:,"><style>'
    "@font-face{font-family:T;src:url(/font.woff2) format('woff2')}body{font-family:T}"
    f'</style><script async src="{TRACKER_SCRIPT}"></script></head><body>'
    '<img src="/img/a.png"><img src="/img/b.png">'
    "<script>addEventListener('load',()=>{const i=new Image();i.src='/img/keep.png';"
    "document.body.appendChild(i)})</script>"
    f"{TEXT}</body></html>"
).encode()
BROKEN = (
    '<!doctype html><html><head><title>Broken</title></head><body><img src="/img/broken.png">'
    f"{TEXT}</body></html>"
).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, ctype: str, body: bytes, **extra: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra.items():
            self.send_header(k.replace("_", "-"), v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/page.html":
            self._send(200, "text/html; charset=utf-8", PAGE, Set_Cookie="sid=1; Path=/")
        elif path == "/waste.html":
            self._send(200, "text/html; charset=utf-8", WASTE)
        elif path == "/broken.html":
            self._send(200, "text/html; charset=utf-8", BROKEN)
        elif path in ("/img/a.png", "/img/b.png", "/img/keep.png"):
            self._send(200, "image/png", PADDED)
        elif path == "/img/gz.png":
            self._send(200, "image/png", GZ_IMAGE, Content_Encoding="gzip")
        elif path == "/font.woff2":
            self._send(200, "font/woff2", FONT, Access_Control_Allow_Origin="*")
        elif path == "/old":
            self._send(302, "text/html", b"", Location="/page.html")
        else:
            self._send(404, "text/plain", b"not found")


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


def launch_options() -> dict[str, Any]:
    """``PW_CHROMIUM_PATH`` points at a pre-installed browser when Playwright's own is absent."""
    path = os.environ.get("PW_CHROMIUM_PATH")
    return {"executable_path": path} if path else {}
