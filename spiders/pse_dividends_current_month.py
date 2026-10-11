from datetime import datetime, timedelta, timezone

import scrapy

from pse_edge_parsing import (
    DIVIDENDS_FEED_URL,
    parse_dividends_feed,
)

AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest"}
MANILA = timezone(timedelta(hours=8))


class PseDividendsCurrentMonthSpider(scrapy.Spider):
    """Catch new PSE dividend circulars from the market-wide announcements feed.

    Unlike pse_dividends (which crawls all ~283 company pages), this reads
    the single Dividends and Rights feed - every company's circulars in one
    list sorted by payment date DESC (~50 rows/page). Only rows whose
    payment date falls on/after the 1st of the current month (Asia/Manila)
    are kept, and pagination stops at the first page with no kept rows.

    Saves are INSERT-ONLY: circulars already in the pse_dividends table are
    skipped, never updated - the full pse_dividends crawl owns revisions.
    Company names are upserted (names/last_scraped_at only).

    Usage:
        scrapy crawl pse_dividends_current_month              # current month
        scrapy crawl pse_dividends_current_month -a month=2026-09
    """

    name = "pse_dividends_current_month"
    allowed_domains = ["edge.pse.com.ph"]
    custom_settings = {
        "DOWNLOAD_DELAY": 1.0,
        "DOWNLOAD_DELAY_JITTER": 0.5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    }

    def __init__(self, month: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cutoff = self._resolve_cutoff(month)

    @staticmethod
    def _resolve_cutoff(month: str) -> str:
        month = month.strip()
        if month:
            try:
                parsed = datetime.strptime(month, "%Y-%m")
            except ValueError:
                raise ValueError(
                    f"invalid month {month!r} - expected YYYY-MM"
                ) from None
            return parsed.date().replace(day=1).isoformat()
        today = datetime.now(MANILA).date()
        return today.replace(day=1).isoformat()

    async def start(self):
        yield scrapy.FormRequest(
            DIVIDENDS_FEED_URL,
            formdata={
                "pageNum": "1",
                "sortMode": "date",
                "dateSortType": "DESC",
                "cmpySortType": "ASC",
            },
            callback=self.parse_feed,
            errback=self.errback,
            headers=AJAX_HEADERS,
            cb_kwargs={"page": 1},
        )

    def parse_feed(self, response, page):
        companies, dividends, total_pages = parse_dividends_feed(response)
        kept = [
            row for row in dividends if row.payment_date and row.payment_date >= self.cutoff
        ]
        kept_ids = {row.cmpy_id for row in kept}
        kept_companies = [c for c in companies if c.cmpy_id in kept_ids]
        self.logger.info(
            "page %d/%d: kept %d of %d rows (payment date >= %s)",
            page,
            total_pages,
            len(kept),
            len(dividends),
            self.cutoff,
        )
        yield from kept_companies
        yield from kept
        # Feed is sorted payment-date DESC, so the first page whose rows are
        # all older than the cutoff means every later page is older too.
        if kept and page < total_pages:
            yield scrapy.FormRequest(
                DIVIDENDS_FEED_URL,
                formdata={
                    "pageNum": str(page + 1),
                    "sortMode": "date",
                    "dateSortType": "DESC",
                    "cmpySortType": "ASC",
                },
                callback=self.parse_feed,
                errback=self.errback,
                headers=AJAX_HEADERS,
                cb_kwargs={"page": page + 1},
            )

    def errback(self, failure):
        self.logger.error("request failed: %s", failure.request.url)
