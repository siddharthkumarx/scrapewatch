"""scrapethissite.com/pages/simple/ (250 countries) with Scrapy, monitored by scrapewatch.

Run from this directory: ``scrapy runspider spider.py -O output/countries.jsonl``
(``run.sh`` does that). The extension finishes the run when the spider closes and puts the
verdict, counts and cost in Scrapy's stats (``scrapewatch/*``); the middleware measures the
compressed bytes of every response; the pipeline cleans each item and checks it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import scrapy


class CountriesSpider(scrapy.Spider):
    name = "countries"
    start_urls: ClassVar[list[str]] = ["https://www.scrapethissite.com/pages/simple/"]
    custom_settings: ClassVar[dict[str, Any]] = {
        "EXTENSIONS": {"scrapewatch.integrations.scrapy.ScrapewatchExtension": 0},
        "DOWNLOADER_MIDDLEWARES": {
            "scrapewatch.integrations.scrapy.ScrapewatchDownloaderMiddleware": 900
        },
        "ITEM_PIPELINES": {"scrapewatch.integrations.scrapy.ScrapewatchPipeline": 1000},
        "SCRAPEWATCH_RULES": "rules.yml",
        "SCRAPEWATCH_CONFIG": "scrapewatch.yml",
        "SCRAPEWATCH_PROXY": "direct",
        "USER_AGENT": "scrapewatch-example/0.1",
        "ROBOTSTXT_OBEY": True,
        "LOG_LEVEL": "INFO",
    }

    def parse(self, response: Any) -> Iterator[dict[str, Any]]:
        for country in response.css(".country"):
            yield {
                "name": " ".join(country.css(".country-name::text").getall()).strip(),
                "capital": country.css(".country-capital::text").get(),
                "population": country.css(".country-population::text").get(),
                "area_km2": country.css(".country-area::text").get(),
            }
