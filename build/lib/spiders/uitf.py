import scrapy

import database
from navpu_parsing import BASE_URL, parse_navpu_page


class UitfSpider(scrapy.Spider):
    """Daily NAVPU refresh for known UITF providers.

    Work list comes from the banks table (populated by uitf_discover), so
    this spider never visits fund-providers.php - it only hits each bank's
    daily_navpu.php page to update navpu/funds. New funds on known banks
    are picked up automatically from those pages.

    Usage:
        scrapy crawl uitf                    # all banks in the DB
        scrapy crawl uitf -a bank_ids=3,7    # subset (testing)
    """

    name = "uitf"
    allowed_domains = ["uitf.com.ph"]

    def __init__(self, bank_ids: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bank_filter: set[int] | None = None
        if bank_ids.strip():
            parsed: set[int] = set()
            for token in bank_ids.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    parsed.add(int(token))
                except ValueError:
                    self.logger.warning("ignoring invalid bank_id: %r", token)
            self.bank_filter = parsed or None

    async def start(self):
        banks = database.load_banks()
        if not banks:
            self.logger.warning(
                "banks table is empty - run 'scrapy crawl uitf_discover' first"
            )
            return
        selected = [
            (bank_id, name)
            for bank_id, name in banks
            if self.bank_filter is None or bank_id in self.bank_filter
        ]
        if self.bank_filter is not None:
            known = {bank_id for bank_id, _ in banks}
            unknown = self.bank_filter - known
            if unknown:
                self.logger.warning(
                    "unknown bank_ids skipped: %s",
                    ", ".join(str(i) for i in sorted(unknown)),
                )
        if not selected:
            self.logger.warning("no banks selected")
            return
        self.logger.info("scraping %d provider(s)", len(selected))
        for bank_id, name in selected:
            yield scrapy.Request(
                f"{BASE_URL}?bank_id={bank_id}",
                callback=self.parse,
                cb_kwargs={"bank_name": name},
            )

    def parse(self, response, bank_name=None):
        yield from parse_navpu_page(response, bank_name=bank_name)
