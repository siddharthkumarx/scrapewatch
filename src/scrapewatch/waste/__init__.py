"""Waste cutter (opt-in): resource blocking, compression headers, conditional cache, URL dedupe."""

from scrapewatch.waste.cache import ConditionalCache
from scrapewatch.waste.dedupe import RunDedupe
from scrapewatch.waste.headers import compression_headers
from scrapewatch.waste.routes import BlockPolicy, install_route_blocker, install_route_blocker_async

__all__ = [
    "BlockPolicy",
    "ConditionalCache",
    "RunDedupe",
    "compression_headers",
    "install_route_blocker",
    "install_route_blocker_async",
]
