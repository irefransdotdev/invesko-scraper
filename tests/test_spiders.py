"""Spiders: uitf (daily, DB-driven work list with as-of probe), uitf_discover
(catalog), pifa (single page with as-of check), and pse_dividends
(PSE Edge company directory + dividends tabs)."""

import asyncio
import logging

import pytest
import scrapy
from scrapy.http import HtmlResponse

import database
from items import SOURCE_MUTUAL, SOURCE_UITF, DividendItem, NavpuItem, PseCompanyItem
from spiders.pifa import PifaSpider
from spiders.pse_dividends import PseDividendsSpider
from spiders.pse_dividends_current_month import PseDividendsCurrentMonthSpider
from spiders.uitf import UitfSpider, manila_today
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


PIFA_PAGE_HTML = """
<html><body>
<div class="elementor-text-editor">As of <strong>10/09/2026</strong></div>
<h2 class="text-center table-header">Stock Funds</h2>
<table class="table table-sm table-custom table-striped table-borderless">
<tbody><tr>
  <td></td>
  <td>Alpha Fund, Inc.</td>
  <td class="text-center">198.25</td>
  <td class="text-center">-6.34%</td>
  <td class="text-center">N/A</td>
  <td class="text-center">1.50%</td>
  <td class="text-center">N/A</td>
  <td class="text-center">-7.40%</td>
  <td class="text-center"><a href="https://pifa.com.ph/facts-figures/nav-history/?fund_id=7">See All</a></td>
</tr></tbody>
</table>
</body></html>
"""


def pifa_response(html=PIFA_PAGE_HTML):
    return HtmlResponse(
        url="https://pifa.com.ph/facts-figures/",
        body=html.encode("utf-8"),
        encoding="utf-8",
    )


def seed_navpu(source, as_of, fund_id=10, **kw):
    batch_key = "pifa" if source == SOURCE_MUTUAL else kw.pop("batch_key", "3")
    batch_name = "PIFA" if source == SOURCE_MUTUAL else kw.pop("batch_name", "BPI")
    item = NavpuItem(
        source=source,
        batch_key=batch_key,
        batch_name=batch_name,
        fund_id=str(fund_id),
        fund_name="Test Fund",
        as_of_date=as_of,
        navpu=100.0,
    )
    database.save_batch(source, batch_key, batch_name, as_of, [item])


UITF_PAGE_HTML = """
<html><body>
<section class="page-content">
  <h2>Unit Investment Trust Funds - Net Asset Values per unit (UITF NAVpus) as of {date}</h2>
  <div class="table-title"><h3>Equity Fund</h3><img src="/img/peso.png"/></div>
  <table><tbody>
    <tr>
      <td><a href="details.php?fund_id=48">Test Equity Fund</a></td>
      <td>123.45</td><td>5.5</td><td>-2.1</td>
    </tr>
  </tbody></table>
</section>
<select name="bank_id"><option value="3">BPI Trust</option></select>
</body></html>
"""

def uitf_response(html=None, bank_id=3):
    return HtmlResponse(
        url=f"https://uitf.com.ph/daily_navpu.php?bank_id={bank_id}",
        body=(html or UITF_PAGE_HTML).encode("utf-8"),
        encoding="utf-8",
    )


def uitf_requests(db_date, force=""):
    """Seed banks + navpu, start the spider, return (spider, requests)."""
    seed_banks((3, "BPI WEALTH"), (7, "China Banking Corporation"))
    seed_navpu(SOURCE_UITF, db_date, batch_name="BPI WEALTH")
    spider = UitfSpider(force=force)
    return spider, run_start(spider)


def test_uitf_skips_when_db_as_of_is_today(db, caplog):
    with caplog.at_level(logging.INFO, logger="uitf"):
        _, requests = uitf_requests(manila_today())
    assert requests == []
    logs = [r.getMessage() for r in caplog.records]
    assert any(
        f"DB as-of date: {manila_today()}, today: {manila_today()}" in m
        for m in logs
    )
    assert any("up to date" in m for m in logs)


def test_uitf_probes_freshest_bank_when_db_behind_today(db):
    seed_banks((3, "BPI WEALTH"), (7, "China Banking Corporation"))
    seed_navpu(SOURCE_UITF, "2026-08-11", batch_name="BPI WEALTH")
    seed_navpu(
        SOURCE_UITF, "2026-08-12", fund_id=20,
        batch_key="7", batch_name="China Banking Corporation",
    )
    spider = UitfSpider()
    requests = run_start(spider)
    # bank 7 published most recently in the DB - it is the probe target
    assert [r.url for r in requests] == [
        "https://uitf.com.ph/daily_navpu.php?bank_id=7"
    ]
    assert requests[0].callback.__name__ == "parse_probe"
    assert requests[0].cb_kwargs["bank_name"] == "China Banking Corporation"
    assert spider.remaining_banks == [(3, "BPI WEALTH")]


def test_uitf_probe_unchanged_date_skips_everything(db, caplog):
    seed_banks((3, "BPI WEALTH"), (7, "China Banking Corporation"))
    seed_navpu(SOURCE_UITF, "2026-08-11", batch_name="BPI WEALTH")
    spider = UitfSpider()
    run_start(spider)  # issues the probe request
    with caplog.at_level(logging.INFO, logger="uitf"):
        output = list(
            spider.parse_probe(
                uitf_response(UITF_PAGE_HTML.format(date="Aug 11, 2026")),
                bank_name="BPI WEALTH",
            )
        )
    assert output == []
    logs = [r.getMessage() for r in caplog.records]
    assert any("no new data" in m for m in logs)


def test_uitf_probe_newer_date_parses_probe_and_continues(db, caplog):
    seed_banks((3, "BPI WEALTH"), (7, "China Banking Corporation"))
    seed_navpu(SOURCE_UITF, "2026-08-11", batch_name="BPI WEALTH")
    spider = UitfSpider()
    run_start(spider)
    with caplog.at_level(logging.INFO, logger="uitf"):
        output = list(
            spider.parse_probe(
                uitf_response(UITF_PAGE_HTML.format(date="Oct 13, 2026")),
                bank_name="BPI WEALTH",
            )
        )
    navpu_items = [o for o in output if isinstance(o, NavpuItem)]
    bank_reqs = [o for o in output if isinstance(o, scrapy.Request)]
    assert [i.fund_id for i in navpu_items] == ["48"]  # probe page parsed too
    assert [r.url for r in bank_reqs] == [
        "https://uitf.com.ph/daily_navpu.php?bank_id=7"
    ]
    logs = [r.getMessage() for r in caplog.records]
    assert any("new data" in m for m in logs)


def test_uitf_probe_missing_date_fails_open(db, caplog):
    seed_banks((3, "BPI WEALTH"), (7, "China Banking Corporation"))
    seed_navpu(SOURCE_UITF, "2026-08-11", batch_name="BPI WEALTH")
    spider = UitfSpider()
    run_start(spider)
    blank = uitf_response("<html><body><p>moved</p></body></html>")
    with caplog.at_level(logging.WARNING, logger="uitf"):
        output = list(spider.parse_probe(blank, bank_name="BPI WEALTH"))
    bank_reqs = [o for o in output if isinstance(o, scrapy.Request)]
    assert [r.url for r in bank_reqs] == [
        "https://uitf.com.ph/daily_navpu.php?bank_id=7"
    ]
    assert any(
        "no as-of date" in r.getMessage() for r in caplog.records
    )


def test_uitf_force_scrapes_even_when_db_has_today(db):
    _, requests = uitf_requests(manila_today(), force="1")
    assert [r.url for r in requests] == [
        "https://uitf.com.ph/daily_navpu.php?bank_id=3",
        "https://uitf.com.ph/daily_navpu.php?bank_id=7",
    ]


def test_uitf_parse_logs_page_as_of_date(db, caplog):
    spider = UitfSpider()
    with caplog.at_level(logging.INFO, logger="uitf"):
        output = list(
            spider.parse(
                uitf_response(UITF_PAGE_HTML.format(date="Oct 13, 2026")),
                bank_name="BPI WEALTH",
            )
        )
    assert any(isinstance(o, NavpuItem) for o in output)
    assert any(
        "BPI WEALTH: page as-of date: 2026-10-13" in r.getMessage()
        for r in caplog.records
    )


def test_pifa_skips_when_page_as_of_not_advanced(db, caplog):
    seed_navpu(SOURCE_MUTUAL, "2026-10-09")  # page fixture says 10/09/2026
    spider = PifaSpider()
    with caplog.at_level(logging.INFO, logger="pifa"):
        output = list(spider.parse(pifa_response()))
    assert output == []
    logs = [r.getMessage() for r in caplog.records]
    assert any("page as-of date: 2026-10-09 (DB as-of date: 2026-10-09" in m for m in logs)
    assert any("up to date" in m for m in logs)


def test_pifa_yields_when_page_as_of_advances(db):
    seed_navpu(SOURCE_MUTUAL, "2026-10-08")
    spider = PifaSpider()
    output = list(spider.parse(pifa_response()))
    navpu_items = [o for o in output if isinstance(o, NavpuItem)]
    assert len(navpu_items) == 1
    assert navpu_items[0].fund_id == "7"
    assert navpu_items[0].as_of_date == "2026-10-09"


def test_pifa_empty_db_yields_items(db):
    spider = PifaSpider()
    output = list(spider.parse(pifa_response()))
    assert any(isinstance(o, NavpuItem) for o in output)


def test_pifa_force_yields_even_when_unchanged(db):
    seed_navpu(SOURCE_MUTUAL, "2026-10-09")
    spider = PifaSpider(force="true")
    output = list(spider.parse(pifa_response()))
    assert any(isinstance(o, NavpuItem) for o in output)


FEED_ROW = """
<tr>
    <td><a href="/companyInformation/form.do?cmpy_id={cmpy_id}">{name}</a></td>
    <td class="alignC">{security}</td>
    <td class="alignC">Cash</td>
    <td class="alignR">Php1.00</td>
    <td class="alignC">{ex}</td>
    <td class="alignC">{record}</td>
    <td class="alignC">{payment}</td>
    <td class="alignC"><a href="#viewer" onclick="openPopup('{ref}');return false;">{circular}</a></td>
</tr>
"""


def feed_page(rows, page=1, total_pages=3):
    body = (
        f'<span class="count">[{page} / {total_pages}] [Total 120]</span>'
        '<table class="list"><tbody>' + "".join(rows) + "</tbody></table>"
    )
    return HtmlResponse(
        url="https://edge.pse.com.ph/disclosureData/dividends_and_rights_info_list.ax",
        body=body.encode("utf-8"),
        encoding="utf-8",
    )


def feed_row(cmpy_id=13, name="A Brown Company, Inc.", security="COMMON",
             ex="Feb 08, 2027", record="Feb 9, 2027", payment="Feb 23, 2027",
             circular="C00595-2026", ref="f6547bd542fbf07f64d70b69f0a3140b"):
    return FEED_ROW.format(
        cmpy_id=cmpy_id, name=name, security=security, ex=ex, record=record,
        payment=payment, circular=circular, ref=ref,
    )


def test_feed_spider_start_posts_first_page():
    spider = PseDividendsCurrentMonthSpider(month="2026-10")
    requests = run_start(spider)
    assert len(requests) == 1
    assert "dividends_and_rights_info_list.ax" in requests[0].url
    fields = form_fields(requests[0])
    assert fields["pageNum"] == ["1"]
    assert fields["sortMode"] == ["date"]
    assert fields["dateSortType"] == ["DESC"]


def test_feed_spider_keeps_current_and_future_rows_and_follows_page():
    spider = PseDividendsCurrentMonthSpider(month="2026-10")
    assert spider.cutoff == "2026-10-01"
    output = list(
        spider.parse_feed(
            feed_page(
                [
                    feed_row(payment="Feb 23, 2027"),  # future - new circular
                    feed_row(circular="C00100-2026", payment="Oct 15, 2026"),
                    feed_row(circular="C00099-2026", payment="Sep 30, 2026"),
                ]
            ),
            page=1,
        )
    )
    dividends = [o for o in output if isinstance(o, DividendItem)]
    companies = [o for o in output if isinstance(o, PseCompanyItem)]
    page_reqs = [
        o for o in output if isinstance(o, scrapy.FormRequest)
    ]
    assert [d.circular_no for d in dividends] == ["C00595-2026", "C00100-2026"]
    assert [c.cmpy_id for c in companies] == [13]
    assert len(page_reqs) == 1
    assert form_fields(page_reqs[0])["pageNum"] == ["2"]


def test_feed_spider_stops_at_first_page_with_only_old_rows():
    spider = PseDividendsCurrentMonthSpider(month="2026-10")
    output = list(
        spider.parse_feed(
            feed_page(
                [
                    feed_row(payment="Sep 30, 2026"),
                    feed_row(circular="C00098-2026", payment="Aug 01, 2026"),
                ]
            ),
            page=1,
        )
    )
    assert [o for o in output if isinstance(o, DividendItem)] == []
    assert not any(isinstance(o, scrapy.FormRequest) for o in output)


def test_feed_spider_month_arg_overrides_cutoff():
    spider = PseDividendsCurrentMonthSpider(month="2026-08")
    assert spider.cutoff == "2026-08-01"


def test_feed_spider_rejects_bad_month():
    with pytest.raises(ValueError, match="expected YYYY-MM"):
        PseDividendsCurrentMonthSpider(month="August 2026")
