"""Shared parsing for daily_navpu.php pages (used by the uitf spider)."""

import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from items import SOURCE_UITF, BatchDoneItem, NavpuItem

BASE_URL = "https://uitf.com.ph/daily_navpu.php"
AS_OF_RE = re.compile(r"as of\s+([A-Za-z]{3}\s+\d{1,2},\s+\d{4})")
NAVPU_RE = re.compile(r"^\s*([\d.]+)\s*(?:\*\s*as of\s+(.+?))?\s*$")


def to_iso(date_str: str) -> str:
    date_str = date_str.strip()
    try:
        return datetime.strptime(date_str, "%b %d, %Y").date().isoformat()
    except ValueError:
        return date_str


def number(text: str | None) -> float | None:
    text = (text or "").strip()
    if not text or set(text) == {"-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_navpu_page(response, bank_name: str | None = None):
    """Yield one NavpuItem per fund row, then a BatchDoneItem marker."""
    names = bank_names(response)
    current_bank_id = (
        parse_qs(urlparse(response.url).query).get("bank_id") or [""]
    )[0]
    resolved_name = bank_name or names.get(current_bank_id)
    as_of_date = as_of_date_from(response)
    scraped_at = datetime.now().isoformat(timespec="seconds")

    for title in response.css("div.table-title"):
        category = (title.css("h3::text").get() or "").strip()
        icon = title.css("img::attr(src)").get() or ""
        currency = "USD" if "dollar" in icon else "PHP"

        table = title.xpath("following-sibling::table[1]")
        if not table:
            continue

        for row in table.xpath(".//tbody/tr"):
            link = row.xpath("./td[1]/a")
            if not link:
                continue

            fund_name = " ".join(link.xpath(".//text()").getall()).strip()
            href = link.xpath("./@href").get() or ""
            fund_id = (parse_qs(urlparse(href).query).get("fund_id") or [None])[0]

            cells = row.xpath("./td")
            if len(cells) < 4:
                continue

            navpu, navpu_as_of = parse_navpu(cells[1].xpath("string(.)").get())

            yield NavpuItem(
                source=SOURCE_UITF,
                batch_key=current_bank_id,
                batch_name=resolved_name,
                as_of_date=as_of_date,
                category=category,
                currency=currency,
                fund_id=fund_id,
                fund_name=fund_name,
                navpu=navpu,
                navpu_as_of=navpu_as_of,
                roi_1y=number(cells[2].xpath("string(.)").get()),
                roi_ytd=number(cells[3].xpath("string(.)").get()),
                details_url=response.urljoin(href),
                scraped_at=scraped_at,
            )

    yield BatchDoneItem(
        source=SOURCE_UITF,
        batch_key=current_bank_id,
        batch_name=resolved_name,
        as_of_date=as_of_date,
    )


def parse_navpu(raw: str | None) -> tuple[float | None, str | None]:
    raw = " ".join((raw or "").split())
    match = NAVPU_RE.match(raw)
    if not match:
        return None, None
    value = float(match.group(1))
    stale_as_of = to_iso(match.group(2)) if match.group(2) else None
    return value, stale_as_of


def as_of_date_from(response) -> str | None:
    heading = response.css("section.page-content h2").xpath("string(.)").get()
    match = AS_OF_RE.search(heading or "")
    if match:
        return to_iso(match.group(1))
    fallback = response.css("#datepicker-1::attr(value)").get()
    return fallback.strip() if fallback else None


def bank_names(response) -> dict[str, str]:
    names = {}
    for option in response.css("select[name='bank_id'] option"):
        value = (option.css("::attr(value)").get() or "").strip()
        if not value:
            continue
        text = " ".join(option.xpath(".//text()").getall())
        text = text.replace("\xa0", " ").strip()
        if text:
            names[value] = text
    return names
