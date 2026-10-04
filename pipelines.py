"""Item pipeline: buffer per-batch NAVPU rows, save each batch in one transaction.

Retry policy: database.save_batch already retries with exponential
backoff. If every attempt fails the batch is rolled back and written to a
dead-letter JSON file in output/failed/ for later reprocessing
(see reprocess_failed.py).
"""

import json
from datetime import datetime
from pathlib import Path

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem

import database
from config import db_config
from items import BatchDoneItem, NavpuItem

FAILED_DIR = Path(__file__).resolve().parent / "output" / "failed"


class NavpuDatabasePipeline:
    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        pipeline.crawler = crawler
        pipeline.stats = crawler.stats
        return pipeline

    @property
    def spider(self):
        return self.crawler.spider

    def open_spider(self):
        self.buffers: dict[tuple, list] = {}
        cfg = db_config()
        destination = cfg["url"] if cfg["is_remote"] else cfg["local_path"]
        self.spider.logger.info(
            "database target: %s (env keys found: %s)",
            "remote Turso" if cfg["is_remote"] else "local file",
            ", ".join(cfg["found_env_keys"]) or "none",
        )
        database.init_schema()
        self.spider.logger.info(
            "database schema ready (%s)", destination
        )

    def process_item(self, item):
        if isinstance(item, BatchDoneItem):
            key = (item.source, item.batch_key, item.as_of_date)
            items = self.buffers.pop(key, [])
            self._save_batch(
                item.source, item.batch_key, item.batch_name,
                item.as_of_date, items,
            )
            raise DropItem("batch-complete marker")
        if isinstance(item, NavpuItem):
            key = (item.source, item.batch_key, item.as_of_date)
            self.buffers.setdefault(key, []).append(item)
        return item

    def close_spider(self):
        for (source, batch_key, as_of_date), items in list(
            self.buffers.items()
        ):
            batch_name = items[0].batch_name if items else None
            self._save_batch(
                source, batch_key, batch_name, as_of_date, items
            )
        self.buffers.clear()

    def _save_batch(self, source, batch_key, batch_name, as_of_date, items):
        spider = self.spider
        if not items:
            self._warn_empty_batch(source, batch_key, batch_name)
        try:
            saved = database.save_batch(
                source, batch_key, batch_name, as_of_date, items,
                logger=spider.logger,
            )
        except database.BatchSaveError as error:
            self.stats.inc_value("db/batches_failed")
            path = self._write_dead_letter(
                source, batch_key, batch_name, as_of_date, items, error
            )
            spider.logger.error(
                "source=%s batch=%s: all DB attempts failed, batch saved "
                "to %s (%s)",
                source,
                batch_key,
                path,
                error,
            )
            return
        self.stats.inc_value("db/batches_saved")
        self.stats.inc_value("db/items_saved", saved)

    def _warn_empty_batch(self, source, batch_key, batch_name):
        """0 funds scraped for a target the DB already knows about.

        For UITF that is only normal for providers with no listed funds
        (e.g. Union Bank); otherwise it means the page or parser failed.
        """
        bank_id = int(batch_key) if source == "uitf" else None
        try:
            known = database.funds_exist(source, bank_id=bank_id)
        except Exception:  # noqa: BLE001 - warning must never break the crawl
            return
        if known:
            self.spider.logger.warning(
                "source=%s batch=%s (%s): 0 funds scraped but the database "
                "already has funds for this target - page/parser failure?",
                source,
                batch_key,
                batch_name,
            )

    @staticmethod
    def _write_dead_letter(
        source, batch_key, batch_name, as_of_date, items, error
    ) -> Path:
        FAILED_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = (
            FAILED_DIR
            / f"{source}-{batch_key}-{as_of_date or 'unknown'}-{stamp}.json"
        )
        payload = {
            "source": source,
            "batch_key": batch_key,
            "batch_name": batch_name,
            "as_of_date": as_of_date,
            "failed_at": datetime.now().isoformat(timespec="seconds"),
            "error": str(error),
            "items": [ItemAdapter(item).asdict() for item in items],
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path
