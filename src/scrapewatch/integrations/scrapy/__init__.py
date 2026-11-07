"""Scrapy: extension (run lifecycle), downloader middleware (responses), item pipeline (records).

settings.py::

    EXTENSIONS = {"scrapewatch.integrations.scrapy.ScrapewatchExtension": 0}
    DOWNLOADER_MIDDLEWARES = {
        "scrapewatch.integrations.scrapy.ScrapewatchDownloaderMiddleware": 900,
    }
    ITEM_PIPELINES = {"scrapewatch.integrations.scrapy.ScrapewatchPipeline": 1000}
    SCRAPEWATCH_RULES = "rules.yml"
    SCRAPEWATCH_CONFIG = "scrapewatch.yml"      # optional
    SCRAPEWATCH_PROXY_META_KEY = "proxy_label"  # request.meta key holding the proxy label

Other settings: ``SCRAPEWATCH_ENABLED`` (default True), ``SCRAPEWATCH_NAME`` (default: the
spider's name), ``SCRAPEWATCH_PROXY`` (label when the meta key is absent),
``SCRAPEWATCH_DROP_UNUSABLE`` (default False: monitoring never removes items).

The extension never closes the spider: a hold verdict is reported, not enforced.
"""

from scrapewatch.integrations.scrapy.extension import ScrapewatchExtension
from scrapewatch.integrations.scrapy.middleware import ScrapewatchDownloaderMiddleware
from scrapewatch.integrations.scrapy.pipeline import ScrapewatchPipeline

__all__ = ["ScrapewatchDownloaderMiddleware", "ScrapewatchExtension", "ScrapewatchPipeline"]
