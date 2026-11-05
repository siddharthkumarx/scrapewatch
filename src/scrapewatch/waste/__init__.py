"""Waste cutter (opt-in): resource blocking, compression headers, conditional cache, URL dedupe."""

from scrapewatch.waste.cache import ConditionalCache
from scrapewatch.waste.dedupe import RunDedupe
from scrapewatch.waste.routes import BlockPolicy, install_route_blocker

__all__ = ["BlockPolicy", "ConditionalCache", "RunDedupe", "install_route_blocker"]
