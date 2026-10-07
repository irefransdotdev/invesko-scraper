"""Parsers: uitf.com.ph daily_navpu pages, the PIFA facts-figures page,
and the PSE Edge company-search / dividends AJAX fragments."""

from scrapy.http import HtmlResponse

from items import BatchDoneItem, NavpuItem
from navpu_parsing import parse_navpu_page
from pifa_parsing import parse_pifa_page
from pse_edge_parsing import parse_company_search, parse_dividends

UITF_HTML = """
<html><body>
<section class="page-content">
  <h2>Fund NAVPU as of Oct 2, 2026</h2>
  <div class="table-title"><h3>Equity Fund</h3>
    <img src="/img/peso.png"/></div>
  <table><tbody>
    <tr>
      <td><a href="details.php?fund_id=48">Test Equity Fund</a></td>
      <td>123.45</td><td>5.5</td><td>-2.1</td>
    </tr>
  </tbody></table>
  <div class="table-title"><h3>Foreign Fund</h3>
    <img src="/img/dollar.png"/></div>
  <table><tbody>
    <tr>
      <td><a href="details.php?fund_id=49">Dollar Bond Fund</a></td>
      <td>9.99 * as of Sep 30, 2026</td><td>1.1</td><td>N/A</td>
    </tr>
  </tbody></table>
</section>
<select name="bank_id">
  <option value="3">BPI Trust</option>
</select>
</body></html>
"""

PIFA_HTML = """
<html><body>
<div class="elementor-text-editor">As of <strong>10/02/2026</strong></div>
<h4>Stray heading before first group - must be ignored</h4>
<table class="table table-sm table-custom table-striped table-borderless">
<tbody><tr>
  <td></td>
  <td>No Group Yet Fund - must be skipped</td>
  <td>1.0</td><td>1%</td><td>1%</td><td>1%</td><td>1%</td><td>1%</td>
  <td><a href="https://pifa.com.ph/facts-figures/nav-history/?fund_id=99">See All</a></td>
</tr></tbody>
</table>
<h2 class="text-center table-header">Stock Funds</h2>
<h4>Primarily invested in Peso securities (shares)</h4>
<table class="table table-sm table-custom table-striped table-borderless">
<thead><td>Legend</td><td>Fund Name</td></thead>
<tbody>
<tr>
  <td class="text-center nav-legend-cell"><span>a</span></td>
  <td>Alpha Fund, Inc.</td>
  <td class="text-center">198.25</td>
  <td class="text-center">-6.34%</td>
  <td class="text-center">N/A</td>
  <td class="text-center">1.50%</td>
  <td class="text-center">N/A</td>
  <td class="text-center">-7.40%</td>
  <td class="text-center"><a href="https://pifa.com.ph/facts-figures/nav-history/?fund_id=7">See All</a></td>
</tr>
<tr>
  <td><span>a</span></td>
  <td>Beta Fund, Inc.</td>
  <td>2.2244</td>
  <td>7.05%</td>
  <td>15.20%</td>
  <td>7.08%</td>
  <td>4.66%</td>
  <td>2.91%</td>
  <td><a href="https://pifa.com.ph/facts-figures/nav-history/?fund_id=8">See All</a></td>
</tr>
<tr>
  <td><span>a</span></td>
  <td>Dollar Equity Fund, Inc.</td>
  <td>$2.4517</td>
  <td>7.05%</td>
  <td>15.20%</td>
  <td>7.08%</td>
  <td>4.66%</td>
  <td>2.91%</td>
  <td><a href="https://pifa.com.ph/facts-figures/nav-history/?fund_id=28">See All</a></td>
</tr>
<tr>
  <td><span>a</span></td>
  <td>Euro Bond Fund, Inc.</td>
  <td>Є221.33</td>
  <td>1.00%</td>
  <td>2.00%</td>
  <td>3.00%</td>
  <td>4.00%</td>
  <td>0.50%</td>
  <td><a href="https://pifa.com.ph/facts-figures/nav-history/?fund_id=50">See All</a></td>
</tr>
</tbody>
</table>
<h2 class="text-center table-header">Bond Funds</h2>
<h4>Peso securities (shares)</h4>
<table class="table table-sm table-custom table-striped table-borderless">
<tbody>
<tr>
  <td></td>
  <td>Gamma Bond Fund</td>
  <td>N/A</td>
  <td>2.75%</td>
  <td>N/A</td>
  <td>N/A</td>
  <td>N/A</td>
  <td>2.01%</td>
  <td><a href="https://pifa.com.ph/facts-figures/nav-history/?fund_id=9">See All</a></td>
</tr>
</tbody>
</table>
</body></html>
"""


def make_response(url, html):
    return HtmlResponse(url=url, body=html.encode("utf-8"), encoding="utf-8")


def test_uitf_parser_fields_and_marker():
    response = make_response(
        "https://uitf.com.ph/daily_navpu.php?bank_id=3", UITF_HTML
    )
    results = list(parse_navpu_page(response))
    funds = [r for r in results if isinstance(r, NavpuItem)]
    marker = [r for r in results if isinstance(r, BatchDoneItem)]

    assert len(funds) == 2 and len(marker) == 1

    first = funds[0]
    assert first.source == "uitf"
    assert first.batch_key == "3"
    assert first.batch_name == "BPI Trust"
    assert first.as_of_date == "2026-10-02"
    assert first.category == "Equity Fund"
    assert first.currency == "PHP"
    assert first.fund_id == "48"
    assert first.navpu == 123.45
    assert first.roi_1y == 5.5
    assert first.roi_ytd == -2.1
    assert first.classification is None

    second = funds[1]
    assert second.currency == "USD"
    assert second.navpu == 9.99
    assert second.navpu_as_of == "2026-09-30"  # stale "* as of" footnote
    assert second.roi_ytd is None  # "N/A" -> None

    assert marker[0].source == "uitf"
    assert marker[0].batch_key == "3"
    assert marker[0].as_of_date == "2026-10-02"


def test_pifa_parser_groups_returns_and_marker():
    response = make_response("https://pifa.com.ph/facts-figures/", PIFA_HTML)
    results = list(parse_pifa_page(response))
    funds = [r for r in results if isinstance(r, NavpuItem)]
    marker = [r for r in results if isinstance(r, BatchDoneItem)]

    assert len(funds) == 5 and len(marker) == 1

    alpha = funds[0]
    assert alpha.source == "mutual"
    assert alpha.batch_key == "pifa"
    assert alpha.batch_name == "PIFA"
    assert alpha.fund_id == "7"
    assert alpha.fund_name == "Alpha Fund, Inc."
    assert alpha.as_of_date == "2026-10-02"
    assert alpha.category == "Stock Funds"
    assert alpha.classification == "Primarily invested in Peso securities (shares)"
    assert alpha.currency is None
    assert alpha.navpu == 198.25
    assert alpha.navpu_as_of == "2026-10-02"
    assert alpha.roi_1y == -6.34
    assert alpha.roi_3y is None  # N/A
    assert alpha.roi_5y == 1.5
    assert alpha.roi_10y is None  # N/A
    assert alpha.roi_ytd == -7.4
    assert alpha.details_url.endswith("nav-history/?fund_id=7")

    beta = funds[1]
    assert beta.fund_id == "8"
    assert beta.navpu == 2.2244
    assert beta.roi_3y == 15.2
    assert beta.currency is None  # unprefixed NAVPS

    dollar = funds[2]
    assert dollar.fund_id == "28"
    assert dollar.navpu == 2.4517  # "$" prefix stripped
    assert dollar.currency == "USD"

    euro = funds[3]
    assert euro.fund_id == "50"
    assert euro.navpu == 221.33  # "Є" prefix stripped
    assert euro.currency == "EUR"

    gamma = funds[4]
    assert gamma.category == "Bond Funds"
    assert gamma.classification == "Peso securities (shares)"
    assert gamma.navpu is None  # N/A NAVPS
    assert gamma.roi_1y == 2.75

    assert marker[0].source == "mutual"
    assert marker[0].batch_key == "pifa"
    assert marker[0].as_of_date == "2026-10-02"


PSE_COMPANY_HTML = """
<span>
[1/29] [Total 283]
</span>
<table class="list">
<thead>
<tr><th>Company Name</th><th>Symbol</th><th class="end">Sector</th></tr>
</thead>
<tbody>
<tr>
    <td>
    <a href="#company" onclick='setCompany({company_id:"6",company_nm:"PLDT Inc."});return false;'>
      PLDT Inc.</a>
    </td>
    <td>TEL</td>
    <td>Services</td>
  </tr>
<tr>
    <td>
    <a href="#company" onclick='setCompany({company_id:"86",company_nm:"Jollibee Foods Corporation"});return false;'>
      Jollibee Foods Corporation</a>
    </td>
    <td>JFC</td>
    <td>Industrial</td>
  </tr>
</tbody>
</table>
<div class="paging"><span>1</span>
<span><a href="#" onclick="goPagePop(2);return false;" >2</a></span></div>
"""

PSE_COMPANY_NO_PAGER_HTML = """
<table class="list">
<tbody>
<tr>
    <td><a href="#company" onclick='setCompany({company_id:"13",company_nm:"A Brown Company, Inc."});return false;'>A Brown Company, Inc.</a></td>
    <td>BRN</td><td>Property</td>
  </tr>
</tbody>
</table>
"""

PSE_DIVIDENDS_HTML = """
<div class="compInfo"><p style="">Jollibee Foods Corporation</p></div>
<table class="list">
<caption>Dividend Information</caption>
<thead>
  <tr>
    <th>Type of Security</th><th>Type of Dividend</th><th>Dividend Rate</th>
    <th>Ex-Dividend Date</th><th>Record Date</th><th>Payment Date</th>
    <th>Circular Number</th>
  </tr>
</thead>
<tbody>
<tr>
    <td class="alignC">JFCPB</td>
    <td class="alignC">Cash</td>
    <td class="alignR">Php10.60125 per share for Series B preferred shares (JFCPB)</td>
    <td class="alignC">Sep 24, 2026</td>
    <td class="alignC">Sep 25, 2026</td>
    <td class="alignC">Oct 14, 2026</td>
    <td class="alignC"><a href="#viewer" onclick="openPopup('00f509b27c7afa9864d70b69f0a3140b');return false;">C06761-2026</a></td>
  </tr>
<tr>
    <td class="alignC">COMMON</td>
    <td class="alignC">Cash</td>
    <td class="alignR">Php1.33</td>
    <td class="alignC">Nov 27, 2025</td>
    <td class="alignC">Nov 28, 2025</td>
    <td class="alignC">Dec 16, 2025</td>
    <td class="alignC"><a href="#viewer" onclick="openPopup('d92c467eb2f6045cec6e1601ccee8f59');return false;">C08088-2025</a></td>
  </tr>
</tbody>
</table>
"""

PSE_DIVIDENDS_EMPTY_HTML = """
<table class="list">
<caption>Dividend Information</caption>
<thead>
  <tr>
    <th>Type of Security</th><th>Type of Dividend</th><th>Dividend Rate</th>
    <th>Ex-Dividend Date</th><th>Record Date</th><th>Payment Date</th>
    <th>Circular Number</th>
  </tr>
</thead>
<tbody>
<tr><td colspan="7" class="alignC">no data.</td></tr>
</tbody>
</table>
"""


def test_pse_company_search_parses_rows_and_pager():
    response = make_response(
        "https://edge.pse.com.ph/cm/companySearch.ax", PSE_COMPANY_HTML
    )
    companies, total_pages = parse_company_search(response)

    assert total_pages == 29
    assert [c.cmpy_id for c in companies] == [6, 86]
    first = companies[0]
    assert first.name == "PLDT Inc."
    assert first.symbol == "TEL"
    assert first.sector == "Services"
    assert companies[1].name == "Jollibee Foods Corporation"
    assert companies[1].sector == "Industrial"


def test_pse_company_search_without_pager_defaults_to_one_page():
    response = make_response(
        "https://edge.pse.com.ph/cm/companySearch.ax", PSE_COMPANY_NO_PAGER_HTML
    )
    companies, total_pages = parse_company_search(response)
    assert total_pages == 1
    assert [c.cmpy_id for c in companies] == [13]
    assert companies[0].name == "A Brown Company, Inc."


def test_pse_dividends_parses_rows_with_iso_dates():
    response = make_response(
        "https://edge.pse.com.ph/companyPage/dividends_and_rights_list.ax",
        PSE_DIVIDENDS_HTML,
    )
    rows = parse_dividends(response, cmpy_id=86)

    assert len(rows) == 2
    first = rows[0]
    assert first.cmpy_id == 86
    assert first.security_type == "JFCPB"
    assert first.dividend_type == "Cash"
    assert first.dividend_rate.startswith("Php10.60125 per share")
    assert first.ex_date == "2026-09-24"
    assert first.record_date == "2026-09-25"
    assert first.payment_date == "2026-10-14"
    assert first.circular_no == "C06761-2026"
    assert first.circular_ref == "00f509b27c7afa9864d70b69f0a3140b"
    assert first.scraped_at

    second = rows[1]
    assert second.security_type == "COMMON"
    assert second.ex_date == "2025-11-27"
    assert second.payment_date == "2025-12-16"


def test_pse_dividends_no_data_yields_empty_list():
    response = make_response(
        "https://edge.pse.com.ph/companyPage/dividends_and_rights_list.ax",
        PSE_DIVIDENDS_EMPTY_HTML,
    )
    assert parse_dividends(response, cmpy_id=55) == []
