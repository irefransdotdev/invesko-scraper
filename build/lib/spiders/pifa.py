import scrapy

from items import NavpuItem
from pifa_parsing import FACTS_URL, parse_pifa_page


class PifaSpider(scrapy.Spider):
    """Scrape the latest NAVPS for all PIFA mutual funds (one page).

    PIFA's robots.txt has no Crawl-Delay; 10s keeps us polite without
    dragging out future multi-page runs. UITF stays at the project-wide 60s.
    """

    name = "pifa"
    allowed_domains = ["pifa.com.ph"]
    custom_settings = {
        "DOWNLOAD_DELAY": 10,
        "DOWNLOAD_DELAY_JITTER": 0,
    }

    async def start(self):
        yield scrapy.Request(FACTS_URL, callback=self.parse)

    def parse(self, response):
        items = list(parse_pifa_page(response))
        funds = sum(1 for item in items if isinstance(item, NavpuItem))
        self.logger.info("parsed %d funds from %s", funds, response.url)
        yield from items
