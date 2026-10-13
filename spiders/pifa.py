import scrapy

import database
from items import SOURCE_MUTUAL, NavpuItem
from pifa_parsing import FACTS_URL, as_of_date_from, parse_pifa_page


class PifaSpider(scrapy.Spider):
    """Scrape the latest NAVPS for all PIFA mutual funds (one page).

    PIFA's robots.txt has no Crawl-Delay; 10s keeps us polite without
    dragging out future multi-page runs. UITF stays at the project-wide 60s.

    Freshness check: the page's "As of MM/DD/YYYY" heading is compared to
    the latest as_of_date stored for source='mutual'. If it has not
    advanced, the run yields nothing - making frequent cron probes cheap.
    The as-of date is always logged (found, compared, and skipped/kept)
    so runs can be debugged from the Zyte logs. Pass -a force=1 to ignore
    the check and always yield the rows (e.g. same-day corrections).
    """

    name = "pifa"
    allowed_domains = ["pifa.com.ph"]
    custom_settings = {
        "DOWNLOAD_DELAY": 10,
        "DOWNLOAD_DELAY_JITTER": 0,
    }

    def __init__(self, force: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.force = force.strip().lower() in {"1", "true", "yes"}

    async def start(self):
        yield scrapy.Request(FACTS_URL, callback=self.parse)

    def parse(self, response):
        as_of_date = as_of_date_from(response)
        latest = database.get_latest_as_of_date(SOURCE_MUTUAL)
        self.logger.info(
            "page as-of date: %s (DB as-of date: %s, force=%s)",
            as_of_date,
            latest,
            self.force,
        )
        if not self.force and as_of_date and latest is not None:
            if as_of_date <= latest:
                self.logger.info(
                    "up to date: source as-of %s <= DB %s - skipping %s",
                    as_of_date,
                    latest,
                    response.url,
                )
                return
        items = list(parse_pifa_page(response))
        funds = sum(1 for item in items if isinstance(item, NavpuItem))
        self.logger.info(
            "parsed %d funds from %s (as of %s)", funds, response.url, as_of_date
        )
        yield from items
