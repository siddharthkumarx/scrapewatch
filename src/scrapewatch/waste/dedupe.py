"""In-run URL dedupe: skip a canonical URL fetched earlier in the same run.

URLs are canonicalised with the ``url`` normalizer's defaults (docs/architecture.md 10.5):
lower-case scheme and host, no default port, dot segments resolved, tracking parameters
dropped, query sorted, fragment removed. So ``https://Books.toscrape.com:443/a/../b?utm_source=x``
and ``https://books.toscrape.com/b`` are the same page. A URL the normalizer cannot read is
compared as written.

Usage::

    seen = RunDedupe(w)
    for url in urls:
        if seen.seen_before(url):
            continue          # counted in waste.deduped_requests; costs nothing
        r = client.get(url)
"""

from __future__ import annotations

import hashlib
import threading

from scrapewatch.clean.url import normalize_url
from scrapewatch.runtime import Watch


def canonical_url(url: str) -> str:
    """One spelling per page (see the module docstring)."""
    out = normalize_url(url, {})
    return out.value if out.flag is None and isinstance(out.value, str) else url.strip()


class RunDedupe:
    """Set of canonical URLs seen in this run (16-byte digests, so memory stays small)."""

    def __init__(self, watch: Watch | None = None) -> None:
        """Start empty; with a ``watch``, each skipped URL is counted in its waste breakdown."""
        self._seen: set[bytes] = set()
        self._lock = threading.Lock()
        self.watch = watch
        self.skipped = 0

    def __len__(self) -> int:
        """Distinct canonical URLs seen."""
        return len(self._seen)

    def seen_before(self, url: str) -> bool:
        """Canonicalise and check; records the URL. True if it was already fetched.

        Never raises: a URL that cannot be processed is treated as new.
        """
        try:
            key = hashlib.blake2b(canonical_url(url).encode("utf-8"), digest_size=16).digest()
        except Exception as exc:
            if self.watch is not None:
                self.watch.monitor_error("dedupe", exc)
            return False
        with self._lock:
            if key not in self._seen:
                self._seen.add(key)
                return False
            self.skipped += 1
        if self.watch is not None:
            self.watch.add_deduped(1)
        return True
