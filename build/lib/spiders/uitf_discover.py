from urllib.parse import parse_qs, urlparse

import scrapy

import database

PROVIDERS_URL = "https://uitf.com.ph/fund-providers.php"


class UitfDiscoverSpider(scrapy.Spider):
    """Refresh the banks table from fund-providers.php (run on demand).

    The daily uitf spider reads its work list from that table, so run this
    whenever new providers may have appeared. Adds/renames banks only -
    never touches funds, navpu, or banks.last_scraped_at (that timestamp
    marks a NAVPU page scrape).
    """

    name = "uitf_discover"
    allowed_domains = ["uitf.com.ph"]

    async def start(self):
        yield scrapy.Request(PROVIDERS_URL, callback=self.parse_providers)

    def parse_providers(self, response) -> list:
        """Update the banks table from the providers page (returns no items)."""
        providers: list[tuple[int, str | None]] = []
        for link in response.css("div.bank a"):
            href = link.xpath("./@href").get() or ""
            bank_id = (
                parse_qs(urlparse(href).query).get("bank_id") or [None]
            )[0]
            if not bank_id:
                continue
            name = " ".join(link.xpath(".//text()").getall()).strip() or None
            try:
                providers.append((int(bank_id), name))
            except ValueError:
                self.logger.warning(
                    "skipping non-numeric bank_id %r", bank_id
                )
        if not providers:
            self.logger.warning(
                "no providers parsed from %s - page layout changed?",
                response.url,
            )
            return []

        known = {bank_id for bank_id, _ in database.load_banks()}
        try:
            total, new = database.save_bank_list(
                providers, logger=self.logger
            )
        except database.BankListError as error:
            self.logger.error("could not save providers: %s", error)
            return []

        listed = {bank_id for bank_id, _ in providers}
        gone = sorted(known - listed)
        self.logger.info(
            "providers listed: %d (%d new), banks in DB after run: %d",
            total,
            new,
            len(known | listed),
        )
        if gone:
            self.logger.info(
                "in DB but no longer listed: %s",
                ", ".join(str(i) for i in gone),
            )
        return []
