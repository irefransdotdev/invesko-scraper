"""Parsing for PIFA facts-figures page (mutual funds, source='mutual').

The page is fully server-rendered: 16 tables grouped under an
<h2 class="...table-header"> group heading and an <h4> classification,
9 <td> cells per row (headers are <td>, not <th>):

    legend | fund name | NAVPS/NAVPU | 1yr | 3yr | 5yr | 10yr | YTD | history link

The page-level "As of <strong>MM/DD/YYYY</strong>" date applies to every row.
"""

import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from items import MUTUAL_BATCH_KEY, SOURCE_MUTUAL, BatchDoneItem, NavpuItem

FACTS_URL = "https://pifa.com.ph/facts-figures/"
AS_OF_RE = re.compile(
    r"As of[^<]{0,30}<strong>\s*(\d{1,2}/\d{1,2}/\d{4})\s*</strong>"
)

# NAVPS cells may carry a currency prefix, e.g. "$1.2413" (USD funds) or
# "Є221.33" (the site renders the euro sign as U+0404).
CURRENCY_SYMBOLS = (
    ("$", "USD"),
    ("€", "EUR"),
    ("Є", "EUR"),
    ("₱", "PHP"),
)


def split_currency(text: str | None) -> tuple[str, str | None]:
    text = (text or "").strip()
    for symbol, code in CURRENCY_SYMBOLS:
        if text.startswith(symbol):
            return text[len(symbol):].strip(), code
    return text, None


def number(text: str | None) -> float | None:
    text = text.replace(",", "") if text else ""
    text = text.strip()
    if not text or set(text) == {"-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def percent(text: str | None) -> float | None:
    """Parse a return cell like '-6.34%' / 'N/A' into a float (no % sign)."""
    text = (text or "").strip()
    if not text or text.upper().startswith("N/A"):
        return None
    return number(text.rstrip("%").strip())


def to_iso(date_str: str) -> str:
    date_str = date_str.strip()
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return date_str


def as_of_date_from(response) -> str | None:
    value = response.xpath(
        "//*[contains(text(), 'As of')]/strong[1]/text()"
    ).get()
    if not value:
        match = AS_OF_RE.search(response.text)
        value = match.group(1) if match else None
    return to_iso(value) if value else None


def _clean(nodes_text: str) -> str:
    return " ".join(nodes_text.split())


def parse_pifa_page(response):
    """Yield one NavpuItem per fund row, then a BatchDoneItem marker."""
    as_of_date = as_of_date_from(response)
    scraped_at = datetime.now().isoformat(timespec="seconds")
    group: str | None = None
    classification: str | None = None

    blocks = response.xpath(
        '//h2[contains(@class, "table-header")]'
        "|//h4"
        "|//table[contains(@class, 'table-custom')]"
    )

    for node in blocks:
        tag = getattr(node.root, "tag", None)
        if not isinstance(tag, str):
            continue
        if tag == "h2":
            group = _clean(node.xpath("string(.)").get() or "")
            classification = None
            continue
        if tag == "h4":
            if group:
                classification = _clean(node.xpath("string(.)").get() or "")
            continue

        # table node: emit rows only under a recognised group
        if not group:
            continue
        for row in node.xpath(".//tbody/tr"):
            cells = row.xpath("./td")
            if len(cells) < 9:
                continue
            link = cells[8].xpath(".//a/@href").get() or ""
            fund_id = (
                parse_qs(urlparse(link).query).get("fund_id") or [None]
            )[0]
            if not fund_id:
                continue
            fund_name = _clean(cells[1].xpath("string(.)").get() or "")
            if not fund_name:
                continue
            navps_text, currency = split_currency(
                cells[2].xpath("string(.)").get()
            )
            yield NavpuItem(
                source=SOURCE_MUTUAL,
                batch_key=MUTUAL_BATCH_KEY,
                batch_name="PIFA",
                fund_id=fund_id,
                fund_name=fund_name,
                as_of_date=as_of_date,
                category=group,
                classification=classification,
                currency=currency,
                navpu=number(navps_text),
                navpu_as_of=as_of_date,
                roi_1y=percent(cells[3].xpath("string(.)").get()),
                roi_3y=percent(cells[4].xpath("string(.)").get()),
                roi_5y=percent(cells[5].xpath("string(.)").get()),
                roi_10y=percent(cells[6].xpath("string(.)").get()),
                roi_ytd=percent(cells[7].xpath("string(.)").get()),
                details_url=response.urljoin(link),
                scraped_at=scraped_at,
            )

    yield BatchDoneItem(
        source=SOURCE_MUTUAL,
        batch_key=MUTUAL_BATCH_KEY,
        batch_name="PIFA",
        as_of_date=as_of_date,
    )
