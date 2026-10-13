from datetime import datetime, timedelta, timezone

import scrapy

import database
from items import SOURCE_UITF
from navpu_parsing import BASE_URL, as_of_date_from, parse_navpu_page

MANILA = timezone(timedelta(hours=8))


def manila_today() -> str:
    """Today's date in Asia/Manila as ISO (no tzdata dependency)."""
    return datetime.now(MANILA).date().isoformat()


class UitfSpider(scrapy.Spider):
    """Daily NAVPU refresh for known UITF providers.

    Work list comes from the banks table (populated by uitf_discover), so
    this spider never visits fund-providers.php - it only hits each bank's
    daily_navpu.php page to update navpu/funds. New funds on known banks
    are picked up automatically from those pages.

    Freshness check (two stages, no decision is ever made per provider):

    1. If the DB already holds an as_of_date of today or newer
       (source='uitf'), skip entirely - no HTTP at all.
    2. Otherwise probe ONE provider page: the bank whose stored data is
       the freshest (the fastest publisher, most likely to have advanced
       again). If even that page's "as of" date has not moved past the
       DB's date, nothing new is published anywhere yet - skip after that
       single request instead of crawling all ~26 banks for nothing. If it
       has advanced, the full run proceeds (the probe page itself is
       parsed in place, never re-requested).

    Individual provider pages can carry lagging "as of" headings (e.g.
    a bank still showing yesterday's date); stale values can never
    overwrite newer rows - the navpu upsert guards on as_of_date. Every
    scraped page logs its as-of date for debugging. Pass -a force=1 to
    skip the checks and always scrape all banks (e.g. same-day corrections).

    Usage:
        scrapy crawl uitf                    # all banks in the DB
        scrapy crawl uitf -a bank_ids=3,7    # subset (testing)
        scrapy crawl uitf -a force=1         # ignore the freshness checks
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
        # Banks still awaiting their own request while a probe is in flight.
        self.remaining_banks: list[tuple[int, str | None]] = []

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

        if not self.force:
            latest = database.get_latest_as_of_date(SOURCE_UITF)
            today = manila_today()
            self.logger.info(
                "freshness check: DB as-of date: %s, today: %s (force=%s)",
                latest,
                today,
                self.force,
            )
            if latest is not None and latest >= today:
                self.logger.info(
                    "up to date: DB as-of %s is >= today %s - "
                    "skipping %d provider(s)",
                    latest,
                    today,
                    len(selected),
                )
                return

            probe = database.get_freshest_bank(SOURCE_UITF)
            if probe is not None:
                probe_id, probe_as_of = probe
                probe_name = dict(selected).get(probe_id)
                if probe_name is None:
                    # Freshest bank excluded by bank_ids - probe the first
                    # selected bank instead.
                    probe_id, probe_name = selected[0]
                self.remaining_banks = [
                    (bank_id, name)
                    for bank_id, name in selected
                    if bank_id != probe_id
                ]
                self.logger.info(
                    "probing %s?bank_id=%s (freshest provider in DB, "
                    "stored as-of %s)",
                    BASE_URL,
                    probe_id,
                    probe_as_of,
                )
                yield scrapy.Request(
                    f"{BASE_URL}?bank_id={probe_id}",
                    callback=self.parse_probe,
                    cb_kwargs={"bank_name": probe_name},
                )
                return
            # No dated bank rows yet - full scrape directly.

        self.logger.info("scraping %d provider(s)", len(selected))
        for bank_id, name in selected:
            yield scrapy.Request(
                f"{BASE_URL}?bank_id={bank_id}",
                callback=self.parse,
                cb_kwargs={"bank_name": name},
            )

    def parse_probe(self, response, bank_name=None):
        """Freshest provider's page decides whether the whole run is needed."""
        as_of_date = as_of_date_from(response)
        latest = database.get_latest_as_of_date(SOURCE_UITF)
        self.logger.info(
            "probe page (%s) as-of date: %s (DB as-of date: %s)",
            bank_name or response.url,
            as_of_date,
            latest,
        )
        if as_of_date is None:
            self.logger.warning(
                "probe page has no as-of date - scraping all providers"
            )
        elif latest is not None and as_of_date <= latest:
            self.logger.info(
                "no new data: freshest provider as-of %s <= DB %s - "
                "skipping %d provider(s)",
                as_of_date,
                latest,
                1 + len(self.remaining_banks),
            )
            return
        else:
            self.logger.info(
                "new data: freshest provider as-of %s (DB has %s) - "
                "scraping %d provider(s)",
                as_of_date,
                latest,
                1 + len(self.remaining_banks),
            )
        # The probe page is already fetched - parse it instead of
        # re-requesting the same URL (the dupefilter would drop that).
        yield from parse_navpu_page(response, bank_name=bank_name)
        for bank_id, name in self.remaining_banks:
            yield scrapy.Request(
                f"{BASE_URL}?bank_id={bank_id}",
                callback=self.parse,
                cb_kwargs={"bank_name": name},
            )

    def parse(self, response, bank_name=None):
        self.logger.info(
            "%s: page as-of date: %s",
            bank_name or response.url,
            as_of_date_from(response),
        )
        yield from parse_navpu_page(response, bank_name=bank_name)
