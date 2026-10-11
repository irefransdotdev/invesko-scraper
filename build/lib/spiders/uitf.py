import scrapy

import database
from items import SOURCE_UITF
from navpu_parsing import BASE_URL, as_of_date_from, parse_navpu_page


class UitfSpider(scrapy.Spider):
    """Daily NAVPU refresh for known UITF providers.

    Work list comes from the banks table (populated by uitf_discover), so
    this spider never visits fund-providers.php - it only hits each bank's
    daily_navpu.php page to update navpu/funds. New funds on known banks
    are picked up automatically from those pages.

    Before scraping every bank, the spider probes daily_navpu.php once and
    reads the site-wide "as of" date. If it has not advanced past the date
    already stored in navpu (source='uitf'), the run stops after that one
    request - making frequent cron probes cheap. Pass -a force=1 to skip
    the check and always scrape all banks (e.g. for same-day corrections).

    Usage:
        scrapy crawl uitf                    # all banks in the DB
        scrapy crawl uitf -a bank_ids=3,7    # subset (testing)
        scrapy crawl uitf -a force=1         # ignore the as-of check
    """

    name = "uitf"
    allowed_domains = ["uitf.com.ph"]

    def __init__(self, bank_ids: str = "", force: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.force = force.strip().lower() in {"1", "true", "yes"}
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
        self.selected: list[tuple[int, str | None]] = []

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
        self.selected = selected

        latest = None if self.force else database.get_latest_as_of_date(
            SOURCE_UITF
        )
        if latest is not None:
            self.logger.info(
                "probing %s (DB as-of date: %s)", BASE_URL, latest
            )
            yield scrapy.Request(BASE_URL, callback=self.parse_probe)
            return

        self.logger.info("scraping %d provider(s)", len(selected))
        for request in self._bank_requests():
            yield request

    def parse_probe(self, response):
        """Compare the site-wide as-of date to the DB; skip banks if unchanged."""
        as_of_date = as_of_date_from(response)
        latest = database.get_latest_as_of_date(SOURCE_UITF)
        if as_of_date is None:
            self.logger.warning(
                "probe page has no as-of date - scraping all providers"
            )
        elif latest is not None and as_of_date <= latest:
            self.logger.info(
                "up to date: source as-of %s <= DB %s - skipping %d provider(s)",
                as_of_date,
                latest,
                len(self.selected),
            )
            return
        else:
            self.logger.info(
                "new data: source as-of %s (DB has %s) - scraping %d provider(s)",
                as_of_date,
                latest,
                len(self.selected),
            )
        for request in self._bank_requests():
            yield request

    def parse(self, response, bank_name=None):
        yield from parse_navpu_page(response, bank_name=bank_name)

    def _bank_requests(self):
        for bank_id, name in self.selected:
            yield scrapy.Request(
                f"{BASE_URL}?bank_id={bank_id}",
                callback=self.parse,
                cb_kwargs={"bank_name": name},
            )
