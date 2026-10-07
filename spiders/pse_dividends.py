import scrapy

from pse_edge_parsing import (
    COMPANY_SEARCH_URL,
    DIVIDENDS_URL,
    parse_company_search,
    parse_dividends,
)

AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


class PseDividendsSpider(scrapy.Spider):
    """Scrape dividend history for every company on PSE Edge.

    Flow: one pass over the company directory (10 rows/page) to learn every
    cmpy_id, then a POST to each company's dividends tab. The site keeps
    only recent dividend entries per company, so this captures everything
    PSE publishes - it is not a full historical archive.

    Usage:
        scrapy crawl pse_dividends                   # all companies
        scrapy crawl pse_dividends -a company_ids=6,86
    """

    name = "pse_dividends"
    allowed_domains = ["edge.pse.com.ph"]
    # Project-level delay is 60s (uitf.com.ph crawl-delay); PSE Edge has no
    # robots.txt, so 1s is polite and finishes ~283 companies in ~6 min.
    custom_settings = {
        "DOWNLOAD_DELAY": 1.0,
        "DOWNLOAD_DELAY_JITTER": 0.5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    }

    def __init__(self, company_ids: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.company_filter: set[int] | None = None
        self.pages_seen: set[int] = {1}
        if company_ids.strip():
            parsed: set[int] = set()
            for token in company_ids.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    parsed.add(int(token))
                except ValueError:
                    self.logger.warning("ignoring invalid company_id: %r", token)
            self.company_filter = parsed or None

    async def start(self):
        yield scrapy.FormRequest(
            COMPANY_SEARCH_URL,
            formdata={"keyword": "", "pNum": "1"},
            callback=self.parse_company_page,
            errback=self.errback,
            headers=AJAX_HEADERS,
        )

    def parse_company_page(self, response):
        companies, total_pages = parse_company_search(response)
        if not companies:
            self.logger.warning("no companies parsed from %s", response.url)
        yield from self.emit(companies)
        # Only the first response fans out; later pages would re-request
        # pages the dupefilter has to throw away.
        for page in range(2, total_pages + 1):
            if page in self.pages_seen:
                continue
            self.pages_seen.add(page)
            yield scrapy.FormRequest(
                COMPANY_SEARCH_URL,
                formdata={"keyword": "", "pNum": str(page)},
                callback=self.parse_company_page,
                errback=self.errback,
                headers=AJAX_HEADERS,
            )

    def emit(self, companies):
        for company in companies:
            yield company
            if (
                self.company_filter is not None
                and company.cmpy_id not in self.company_filter
            ):
                continue
            yield scrapy.FormRequest(
                DIVIDENDS_URL,
                formdata={"cmpy_id": str(company.cmpy_id)},
                callback=self.parse_dividends,
                errback=self.errback,
                headers=AJAX_HEADERS,
                cb_kwargs={
                    "cmpy_id": company.cmpy_id,
                    "company_name": company.name,
                },
            )

    def parse_dividends(self, response, cmpy_id, company_name):
        rows = parse_dividends(response, cmpy_id)
        if not rows:
            self.logger.debug("no dividends: %s (cmpy_id=%s)", company_name, cmpy_id)
        yield from rows

    def errback(self, failure):
        self.logger.error("request failed: %s", failure.request.url)
