"""Shared parsing for PSE Edge AJAX endpoints (used by the pse_dividends spider).

Both endpoints return HTML fragments (no <html> wrapper), loaded by the site
itself via jQuery $.post:

- company search: /cm/companySearch.ax  (keyword, pNum) -> 10 rows per page
- dividends tab:  /companyPage/dividends_and_rights_list.ax
                  (DividendsOrRights=Dividends, cmpy_id) -> whole history
"""

import re
from datetime import datetime

from items import DividendItem, PseCompanyItem

BASE_URL = "https://edge.pse.com.ph"
COMPANY_SEARCH_URL = f"{BASE_URL}/cm/companySearch.ax"
DIVIDENDS_URL = (
    f"{BASE_URL}/companyPage/dividends_and_rights_list.ax"
    "?DividendsOrRights=Dividends"
)

SET_COMPANY_RE = re.compile(r'setCompany\(\{company_id:"(\d+)"')
PAGER_RE = re.compile(r"\[(\d+)/(\d+)\]\s*\[Total\s+(\d+)\]")
POPUP_RE = re.compile(r"openPopup\('([0-9a-f]+)'\)")


def to_iso(date_str: str | None) -> str | None:
    """'Nov 20, 2026' -> '2026-11-20'; blank/unparseable input passes through."""
    text = (date_str or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%b %d, %Y").date().isoformat()
    except ValueError:
        return text


def parse_company_search(response) -> tuple[list[PseCompanyItem], int]:
    """Companies on one /cm/companySearch.ax page plus the total page count.

    total_pages comes from the '[1/29] [Total 283]' counter and is only
    meaningful on the first page (later pages report the same values).
    """
    total_pages = 1
    counter = response.css("span::text").getall()
    match = PAGER_RE.search(" ".join(counter))
    if match:
        total_pages = int(match.group(2))

    companies = []
    for row in response.css("table.list tbody tr"):
        cells = row.xpath("./td")
        if len(cells) < 3:
            continue
        onclick = cells[0].xpath(".//a/@onclick").get() or ""
        id_match = SET_COMPANY_RE.search(onclick)
        if not id_match:
            continue
        name = " ".join(cells[0].xpath(".//a//text()").getall()).strip()
        companies.append(
            PseCompanyItem(
                cmpy_id=int(id_match.group(1)),
                name=name,
                symbol=_cell_text(cells[1]) or None,
                sector=_cell_text(cells[2]) or None,
            )
        )
    return companies, total_pages


def parse_dividends(response, cmpy_id: int) -> list[DividendItem]:
    """Dividend rows for one company; 'no data.' rows yield an empty list."""
    scraped_at = datetime.now().isoformat(timespec="seconds")
    items = []
    for row in response.css("table.list tbody tr"):
        cells = row.xpath("./td")
        if len(cells) < 7:
            continue  # colspan 'no data.' row
        circular_cell = cells[6]
        popup = POPUP_RE.search(circular_cell.xpath(".//a/@onclick").get() or "")
        items.append(
            DividendItem(
                cmpy_id=cmpy_id,
                security_type=_cell_text(cells[0]) or None,
                dividend_type=_cell_text(cells[1]) or None,
                dividend_rate=_cell_text(cells[2]) or None,
                ex_date=to_iso(_cell_text(cells[3])),
                record_date=to_iso(_cell_text(cells[4])),
                payment_date=to_iso(_cell_text(cells[5])),
                circular_no=_cell_text(circular_cell) or None,
                circular_ref=popup.group(1) if popup else None,
                scraped_at=scraped_at,
            )
        )
    return items


def _cell_text(cell) -> str:
    return " ".join(cell.xpath("string(.)").getall()).replace("\xa0", " ").strip()