"""Spiders: uitf (daily, DB-driven work list), uitf_discover (catalog),
and pse_dividends (PSE Edge company directory + dividends tabs)."""

import asyncio
import logging

import scrapy
from scrapy.http import HtmlResponse

import database
from items import SOURCE_UITF, NavpuItem, PseCompanyItem
from spiders.pse_dividends import PseDividendsSpider
from spiders.uitf import UitfSpider
from spiders.uitf_discover import UitfDiscoverSpider

PROVIDERS_HTML = """
<html><body>
<div class="bank"><a href="/daily_navpu.php?bank_id=3">BPI WEALTH</a></div>
<div class="bank"><a href="/daily_navpu.php?bank_id=7">China Banking Corporation</a></div>
<div class="bank"><a href="/other_page.php">link without bank id</a></div>
</body></html>
"""


def providers_response(html=PROVIDERS_HTML):
    return HtmlResponse(
        url="https://uitf.com.ph/fund-providers.php",
        body=html.encode("utf-8"),
        encoding="utf-8",
    )


def run_start(spider):
    async def collect():
        return [request async for request in spider.start()]

    return asyncio.run(collect())


def seed_banks(*providers):
    database.save_bank_list(list(providers))


def test_uitf_builds_requests_from_db(db):
    seed_banks((3, "BPI WEALTH"), (7, "China Banking Corporation"))
    requests = run_start(UitfSpider())
    assert [r.url for r in requests] == [
        "https://uitf.com.ph/daily_navpu.php?bank_id=3",
        "https://uitf.com.ph/daily_navpu.php?bank_id=7",
    ]
    assert requests[0].cb_kwargs["bank_name"] == "BPI WEALTH"


def test_uitf_bank_ids_filter(db):
    seed_banks((3, "BPI WEALTH"), (7, "China Banking Corporation"))
    requests = run_start(UitfSpider(bank_ids="7"))
    assert len(requests) == 1
    assert "bank_id=7" in requests[0].url


def test_uitf_empty_db_warns_and_finishes(db, caplog):
    with caplog.at_level(logging.WARNING, logger="uitf"):
        requests = run_start(UitfSpider())
    assert requests == []
    assert any(
        "uitf_discover" in record.getMessage() for record in caplog.records
    )


def test_uitf_unknown_bank_ids_warn(db, caplog):
    seed_banks((3, "BPI WEALTH"))
    with caplog.at_level(logging.WARNING, logger="uitf"):
        requests = run_start(UitfSpider(bank_ids="3,99"))
    assert len(requests) == 1
    assert any("99" in record.getMessage() for record in caplog.records)


def test_discover_upserts_banks(db, conn):
    spider = UitfDiscoverSpider()
    list(spider.parse_providers(providers_response()))
    rows = conn.execute(
        "SELECT bank_id, name, last_scraped_at FROM banks ORDER BY bank_id"
    ).fetchall()
    assert rows == [
        (3, "BPI WEALTH", None),
        (7, "China Banking Corporation", None),
    ]


def test_discover_reports_new_then_zero(db, caplog):
    spider = UitfDiscoverSpider()
    with caplog.at_level(logging.INFO, logger="uitf_discover"):
        list(spider.parse_providers(providers_response()))
        assert any(
            "(2 new)" in record.getMessage() for record in caplog.records
        )
        caplog.clear()
        list(spider.parse_providers(providers_response()))
        assert any(
            "(0 new)" in record.getMessage() for record in caplog.records
        )
    assert len(database.load_banks()) == 2


def test_discover_updates_name_but_not_last_scraped_at(db, conn):
    item = NavpuItem(
        source=SOURCE_UITF,
        batch_key="3",
        batch_name="Old Name",
        fund_id="10",
        fund_name="Test Fund",
        as_of_date="2026-10-02",
    )
    database.save_batch(SOURCE_UITF, "3", "Old Name", "2026-10-02", [item])
    before = conn.execute(
        "SELECT last_scraped_at FROM banks WHERE bank_id = 3"
    ).fetchone()[0]
    assert before is not None

    spider = UitfDiscoverSpider()
    list(spider.parse_providers(providers_response()))
    after, name = conn.execute(
        "SELECT last_scraped_at, name FROM banks WHERE bank_id = 3"
    ).fetchone()
    assert name == "BPI WEALTH"
    assert after == before


def test_discover_empty_page_warns_without_crashing(db, caplog):
    spider = UitfDiscoverSpider()
    with caplog.at_level(logging.WARNING, logger="uitf_discover"):
        list(
            spider.parse_providers(
                providers_response("<html><body><p>moved</p></body></html>")
            )
        )
    assert any(
        "no providers parsed" in record.getMessage()
        for record in caplog.records
    )
    assert database.load_banks() == []


PSE_COMPANY_HTML = """
<span>[1/3] [Total 28]</span>
<table class="list">
<tbody>
<tr>
    <td><a href="#company" onclick='setCompany({company_id:"6",company_nm:"PLDT Inc."});return false;'>PLDT Inc.</a></td>
    <td>TEL</td><td>Services</td>
  </tr>
<tr>
    <td><a href="#company" onclick='setCompany({company_id:"86",company_nm:"Jollibee Foods Corporation"});return false;'>Jollibee Foods Corporation</a></td>
    <td>JFC</td><td>Industrial</td>
  </tr>
</tbody>
</table>
"""

PSE_DIVIDENDS_HTML = """
<table class="list">
<thead>
  <tr>
    <th>Type of Security</th><th>Type of Dividend</th><th>Dividend Rate</th>
    <th>Ex-Dividend Date</th><th>Record Date</th><th>Payment Date</th>
    <th>Circular Number</th>
  </tr>
</thead>
<tbody>
<tr>
    <td class="alignC">COMMON</td>
    <td class="alignC">Cash</td>
    <td class="alignR">Php1.33</td>
    <td class="alignC">May 04, 2026</td>
    <td class="alignC">May 5, 2026</td>
    <td class="alignC">May 21, 2026</td>
    <td class="alignC"><a href="#viewer" onclick="openPopup('515cc3f7b32c0b1f64d70b69f0a3140b');return false;">C02611-2026</a></td>
  </tr>
</tbody>
</table>
"""


def pse_company_response(html=PSE_COMPANY_HTML):
    return HtmlResponse(
        url="https://edge.pse.com.ph/cm/companySearch.ax",
        body=html.encode("utf-8"),
        encoding="utf-8",
    )


def pse_dividends_response(html=PSE_DIVIDENDS_HTML):
    return HtmlResponse(
        url="https://edge.pse.com.ph/companyPage/dividends_and_rights_list.ax",
        body=html.encode("utf-8"),
        encoding="utf-8",
    )


def form_fields(request):
    from urllib.parse import parse_qs

    return parse_qs(request.body.decode("utf-8"))


def test_pse_start_requests_first_company_page():
    requests = run_start(PseDividendsSpider())
    assert len(requests) == 1
    assert "companySearch.ax" in requests[0].url
    assert form_fields(requests[0])["pNum"] == ["1"]


def test_pse_parse_company_page_emits_items_and_dividend_requests():
    spider = PseDividendsSpider()
    output = list(spider.parse_company_page(pse_company_response()))

    companies = [o for o in output if isinstance(o, PseCompanyItem)]
    dividend_reqs = [
        o
        for o in output
        if isinstance(o, scrapy.FormRequest) and "dividends" in o.url
    ]
    page_reqs = [
        o
        for o in output
        if isinstance(o, scrapy.FormRequest) and "companySearch" in o.url
    ]

    assert [c.cmpy_id for c in companies] == [6, 86]
    assert [form_fields(r)["cmpy_id"] for r in dividend_reqs] == [["6"], ["86"]]
    assert dividend_reqs[0].cb_kwargs["company_name"] == "PLDT Inc."
    # pages 2 and 3 follow page 1
    assert [form_fields(r)["pNum"] for r in page_reqs] == [["2"], ["3"]]


def test_pse_company_ids_filter(db):
    spider = PseDividendsSpider(company_ids="86")
    output = list(spider.parse_company_page(pse_company_response()))

    companies = [o for o in output if isinstance(o, PseCompanyItem)]
    dividend_reqs = [
        o
        for o in output
        if isinstance(o, scrapy.FormRequest) and "dividends" in o.url
    ]
    # directory metadata for every company, dividend requests only for JFC
    assert [c.cmpy_id for c in companies] == [6, 86]
    assert [form_fields(r)["cmpy_id"] for r in dividend_reqs] == [["86"]]


def test_pse_parse_dividends_yields_items():
    spider = PseDividendsSpider()
    rows = list(
        spider.parse_dividends(
            pse_dividends_response(), cmpy_id=86, company_name="Jollibee"
        )
    )
    assert len(rows) == 1
    assert rows[0].cmpy_id == 86
    assert rows[0].security_type == "COMMON"
    assert rows[0].ex_date == "2026-05-04"
    assert rows[0].circular_no == "C02611-2026"
