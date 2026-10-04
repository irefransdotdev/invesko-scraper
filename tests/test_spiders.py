"""Spiders: uitf (daily, DB-driven work list) and uitf_discover (catalog)."""

import asyncio
import logging

from scrapy.http import HtmlResponse

import database
from items import SOURCE_UITF, NavpuItem
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
