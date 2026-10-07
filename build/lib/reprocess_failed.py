"""Re-save dead-lettered batches from output/failed/*.json.

Each file is retried through the same transactional save as the crawl.
Successful files move to output/failed/done/.

Legacy payloads (pre-v2) that used bank_id/bank_name/roi_yoy keys are
translated to the current source/batch_key/roi_1y shape.
"""

import glob
import json
import shutil
import sys
from pathlib import Path

import database
from items import SOURCE_UITF, DividendItem, PseCompanyItem

FAILED_DIR = Path(__file__).resolve().parent / "output" / "failed"
DONE_DIR = FAILED_DIR / "done"


def main() -> int:
    files = sorted(glob.glob(str(FAILED_DIR / "*.json")))
    if not files:
        print("no dead-letter files found in", FAILED_DIR)
        return 0

    DONE_DIR.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, 0

    for file_path in files:
        data = json.loads(Path(file_path).read_text(encoding="utf-8"))
        source = data.get("source") or SOURCE_UITF
        batch_key = str(data.get("batch_key") or data.get("bank_id"))
        batch_name = data.get("batch_name") or data.get("bank_name")
        as_of_date = data.get("as_of_date")
        items = None
        if source != "pse":
            items = [
                database_item_from_dict(entry)
                for entry in data.get("items", [])
            ]
        label = f"{source}/{batch_key} ({as_of_date})"
        try:
            if source == "pse":
                saved_companies, saved_dividends = database.save_pse_batch(
                    [
                        PseCompanyItem(**entry)
                        for entry in data.get("companies", [])
                    ],
                    [
                        DividendItem(**entry)
                        for entry in data.get("items", [])
                    ],
                )
                saved = f"{saved_companies} companies, {saved_dividends} dividends"
            else:
                saved = database.save_batch(
                    source, batch_key, batch_name, as_of_date, items
                )
            shutil.move(file_path, str(DONE_DIR / Path(file_path).name))
            ok += 1
            print(f"saved {label}: {saved} rows -> moved to done/")
        except database.BatchSaveError as error:
            failed += 1
            print(f"FAILED {label}: {error}")

    print(f"\nreprocessed: {ok} ok, {failed} failed, {len(files)} total")
    return 1 if failed else 0


def database_item_from_dict(entry: dict):
    from items import NavpuItem

    entry = dict(entry)
    if "batch_key" not in entry:
        entry["batch_key"] = str(entry.pop("bank_id", ""))
    if "batch_name" not in entry:
        entry["batch_name"] = entry.pop("bank_name", None)
    if "roi_1y" not in entry and "roi_yoy" in entry:
        entry["roi_1y"] = entry.pop("roi_yoy")
    for extra in ("roi_3y", "roi_5y", "roi_10y", "classification"):
        entry.setdefault(extra, None)
    entry.setdefault("source", SOURCE_UITF)
    return NavpuItem(**entry)


if __name__ == "__main__":
    sys.exit(main())
