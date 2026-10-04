"""Pipeline: batch markers, dead-letters, empty-bank warning, flush."""

import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scrapy.exceptions import DropItem
from scrapy.settings import Settings

import database
import pipelines
from items import SOURCE_MUTUAL, SOURCE_UITF, BatchDoneItem, NavpuItem
from reprocess_failed import database_item_from_dict


def navpu_item(**kw):
    base = dict(
        source=SOURCE_UITF,
        batch_key="3",
        batch_name="BPI Trust",
        fund_id="10",
        fund_name="Test Fund",
        as_of_date="2026-10-02",
        category="Equity Fund",
        currency="PHP",
        navpu=100.0,
        navpu_as_of="2026-10-02",
        roi_1y=5.0,
        roi_ytd=2.0,
        details_url="https://uitf.com.ph/details.php",
        scraped_at="2026-10-02T09:00:00",
    )
    base.update(kw)
    return NavpuItem(**base)


@pytest.fixture()
def pipeline(db, monkeypatch):
    failed_dir = db.parent / "failed"
    monkeypatch.setattr(pipelines, "FAILED_DIR", failed_dir)
    crawler = SimpleNamespace(
        spider=SimpleNamespace(logger=logging.getLogger("test-spider")),
        stats=Mock(),
        settings=Settings(),
    )
    pipe = pipelines.NavpuDatabasePipeline.from_crawler(crawler)
    pipe.open_spider()
    yield pipe
    pipe.close_spider()


def test_marker_flushes_one_transaction(pipeline, conn):
    pipeline.process_item(navpu_item(fund_id="10"))
    pipeline.process_item(navpu_item(fund_id="11", fund_name="Other Fund"))
    assert pipeline.buffers  # not yet written

    with pytest.raises(DropItem):
        pipeline.process_item(
            BatchDoneItem(
                source=SOURCE_UITF,
                batch_key="3",
                batch_name="BPI Trust",
                as_of_date="2026-10-02",
            )
        )
    assert not pipeline.buffers
    assert conn.execute("SELECT COUNT(*) FROM navpu").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0] == 1
    pipeline.stats.inc_value.assert_any_call("db/batches_saved")
    pipeline.stats.inc_value.assert_any_call("db/items_saved", 2)


def test_close_spider_flushes_leftover_buffers(pipeline, conn):
    pipeline.process_item(navpu_item(fund_id="20"))
    pipeline.close_spider()
    assert conn.execute("SELECT COUNT(*) FROM navpu").fetchone()[0] == 1


def test_save_failure_writes_dead_letter(pipeline, monkeypatch, db):
    pipeline.process_item(navpu_item(fund_id="10"))

    def boom(*args, **kwargs):
        raise database.BatchSaveError("nope")

    monkeypatch.setattr(database, "save_batch", boom)
    with pytest.raises(DropItem):
        pipeline.process_item(
            BatchDoneItem(
                source=SOURCE_UITF,
                batch_key="3",
                batch_name="BPI Trust",
                as_of_date="2026-10-02",
            )
        )
    files = list(pipelines.FAILED_DIR.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["source"] == "uitf"
    assert payload["batch_key"] == "3"
    assert payload["items"][0]["roi_1y"] == 5.0
    pipeline.stats.inc_value.assert_called_with("db/batches_failed")


def test_empty_batch_warns_only_when_funds_known(pipeline, monkeypatch):
    warnings = []
    pipeline.spider.logger.warning = lambda msg, *args: warnings.append(
        msg % args if args else msg
    )

    monkeypatch.setattr(database, "funds_exist", lambda *a, **k: False)
    with pytest.raises(DropItem):
        pipeline.process_item(
            BatchDoneItem(
                source=SOURCE_UITF, batch_key="14", batch_name="UCPB",
                as_of_date="2026-10-02",
            )
        )
    assert not warnings

    monkeypatch.setattr(database, "funds_exist", lambda *a, **k: True)
    with pytest.raises(DropItem):
        pipeline.process_item(
            BatchDoneItem(
                source=SOURCE_UITF, batch_key="34", batch_name="Robinsons",
                as_of_date="2026-10-02",
            )
        )
    assert len(warnings) == 1
    assert "0 funds scraped" in warnings[0]


def test_mutual_marker_saves_with_source(conn, db, monkeypatch):
    failed_dir = db.parent / "failed"
    monkeypatch.setattr(pipelines, "FAILED_DIR", failed_dir)
    crawler = SimpleNamespace(
        spider=SimpleNamespace(logger=logging.getLogger("test-spider")),
        stats=Mock(),
        settings=Settings(),
    )
    pipe = pipelines.NavpuDatabasePipeline.from_crawler(crawler)
    pipe.open_spider()
    pipe.process_item(
        navpu_item(
            source=SOURCE_MUTUAL, batch_key="pifa", batch_name="PIFA",
            fund_id="7", fund_name="Alpha Fund, Inc.", currency=None,
            roi_3y=1.5,
        )
    )
    with pytest.raises(DropItem):
        pipe.process_item(
            BatchDoneItem(
                source=SOURCE_MUTUAL, batch_key="pifa", batch_name="PIFA",
                as_of_date="2026-10-02",
            )
        )
    source, bank_id = conn.execute(
        "SELECT source, bank_id FROM funds"
    ).fetchone()
    assert (source, bank_id) == ("mutual", None)
    assert conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0] == 0


def test_reprocess_legacy_payload_format(tmp_path, conn):
    from items import NavpuItem

    legacy = {
        "bank_id": "5",
        "bank_name": "Legacy Bank",
        "as_of_date": "2026-10-01",
        "items": [
            {
                "bank_id": "5",
                "bank_name": "Legacy Bank",
                "fund_id": "77",
                "fund_name": "Legacy Fund",
                "as_of_date": "2026-10-01",
                "category": "Equity",
                "currency": "PHP",
                "navpu": 55.5,
                "navpu_as_of": "2026-10-01",
                "roi_yoy": 3.3,
                "roi_ytd": 1.1,
                "details_url": "https://uitf.com.ph/x",
                "scraped_at": "2026-10-01T09:00:00",
            }
        ],
    }
    item = database_item_from_dict(legacy["items"][0])
    assert isinstance(item, NavpuItem)
    assert item.source == "uitf"
    assert item.batch_key == "5"
    assert item.batch_name == "Legacy Bank"
    assert item.roi_1y == 3.3


def test_secret_settings_copied_to_env(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", "")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "")
    settings = Settings(
        {
            "TURSO_DATABASE_URL": "libsql://cloud.example",
            "TURSO_AUTH_TOKEN": "secret-token",
        }
    )
    crawler = SimpleNamespace(settings=settings, stats=Mock())
    pipelines.NavpuDatabasePipeline.from_crawler(crawler)
    assert os.environ["TURSO_DATABASE_URL"] == "libsql://cloud.example"
    assert os.environ["TURSO_AUTH_TOKEN"] == "secret-token"


def test_real_env_never_overridden_by_settings(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://from-local-env")
    settings = Settings({"TURSO_DATABASE_URL": "libsql://from-dashboard"})
    crawler = SimpleNamespace(settings=settings, stats=Mock())
    pipelines.NavpuDatabasePipeline.from_crawler(crawler)
    assert os.environ["TURSO_DATABASE_URL"] == "libsql://from-local-env"


def test_missing_secret_settings_leave_env_untouched(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", "")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "")
    crawler = SimpleNamespace(settings=Settings(), stats=Mock())
    pipelines.NavpuDatabasePipeline.from_crawler(crawler)
    assert os.environ["TURSO_DATABASE_URL"] == ""
    assert os.environ["TURSO_AUTH_TOKEN"] == ""
