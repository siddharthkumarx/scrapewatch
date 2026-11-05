"""In-run URL dedupe: skip a canonical URL fetched earlier in the same run."""

from __future__ import annotations


class RunDedupe:
    """Set of canonical URLs seen in this run."""

    def __init__(self) -> None:
        """Start empty."""
        self._seen: set[str] = set()

    def seen_before(self, url: str) -> bool:
        """Canonicalise and check; records the URL. True if it was already fetched."""
        raise NotImplementedError("build-progress item 22")
