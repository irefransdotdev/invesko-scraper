"""Parsers: uitf.com.ph daily_navpu pages and the PIFA facts-figures page."""

from scrapy.http import HtmlResponse

from items import BatchDoneItem, NavpuItem
from navpu_parsing import parse_navpu_page
from pifa_parsing import parse_pifa_page

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
