"""Scrapy: extension (run lifecycle), downloader middleware (responses), item pipeline (records).

settings.py::

    EXTENSIONS = {"scrapewatch.integrations.scrapy.ScrapewatchExtension": 0}
    DOWNLOADER_MIDDLEWARES = {
        "scrapewatch.integrations.scrapy.ScrapewatchDownloaderMiddleware": 900,
    }
    ITEM_PIPELINES = {"scrapewatch.integrations.scrapy.ScrapewatchPipeline": 1000}
    SCRAPEWATCH_RULES = "rules.yml"
"""

from scrapewatch.integrations.scrapy.extension import ScrapewatchExtension
from scrapewatch.integrations.scrapy.middleware import ScrapewatchDownloaderMiddleware
from scrapewatch.integrations.scrapy.pipeline import ScrapewatchPipeline

__all__ = ["ScrapewatchDownloaderMiddleware", "ScrapewatchExtension", "ScrapewatchPipeline"]
