"""v2 migration: source discriminator, latest-only navpu, funds.classification.

What it does (one transaction, remote Turso by default):
1. Backs up funds + navpu rows to output/backup/*.json (before any change).
2. funds  -> rebuilt with PRIMARY KEY (source, fund_id), source='uitf'
   for existing rows, new nullable classification column (NULL for UITF).
3. navpu  -> rebuilt LATEST-ONLY with PRIMARY KEY (source, fund_id):
   roi_yoy renamed to roi_1y, roi_3y/roi_5y/roi_10y added (NULL for UITF),
   duplicated metadata columns dropped (they live in funds),
   only the newest row per fund is kept if history existed.

Idempotent: re-running detects the v2 shape and exits without changes.
"""

import json
from datetime import datetime
from pathlib import Path

import database
from config import db_config

BACKUP_DIR = Path(__file__).resolve().parent / "output" / "backup"

CREATE_NAVPU_V2 = """
    CREATE TABLE navpu_v2 (
        source TEXT NOT NULL,
        fund_id INTEGER NOT NULL,
        as_of_date TEXT NOT NULL,
        navpu REAL,
        navpu_as_of TEXT,
        roi_1y REAL,
        roi_3y REAL,
        roi_5y REAL,
        roi_10y REAL,
        roi_ytd REAL,
        scraped_at TEXT NOT NULL,
        PRIMARY KEY (source, fund_id)
    )
"""

COPY_NAVPU_V2 = """
    INSERT INTO navpu_v2 (
        source, fund_id, as_of_date, navpu, navpu_as_of,
        roi_1y, roi_3y, roi_5y, roi_10y, roi_ytd, scraped_at
    )
    SELECT 'uitf', fund_id, as_of_date, navpu, navpu_as_of,
           roi_yoy, NULL, NULL, NULL, roi_ytd, scraped_at
    FROM (
        SELECT n.*,
               ROW_NUMBER() OVER (
                   PARTITION BY fund_id
                   ORDER BY as_of_date DESC, scraped_at DESC
               ) AS rn
        FROM navpu n
    )
    WHERE rn = 1
"""

CREATE_FUNDS_V2 = """
    CREATE TABLE funds_v2 (
        source TEXT NOT NULL,
        fund_id INTEGER NOT NULL,
        bank_id INTEGER,
        fund_name TEXT NOT NULL,
        category TEXT,
        classification TEXT,
        currency TEXT,
        details_url TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        PRIMARY KEY (source, fund_id)
    )
"""

COPY_FUNDS_V2 = """
    INSERT INTO funds_v2 (
        source, fund_id, bank_id, fund_name, category, classification,
        currency, details_url, first_seen_at, last_seen_at
    )
    SELECT 'uitf', fund_id, bank_id, fund_name, category, NULL,
           currency, details_url, first_seen_at, last_seen_at
    FROM funds
"""


def column_names(conn, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def is_migrated(conn) -> bool:
    navpu_cols = column_names(conn, "navpu")
    funds_cols = column_names(conn, "funds")
    return (
        "source" in navpu_cols
        and "roi_1y" in navpu_cols
        and "bank_id" not in navpu_cols
        and "source" in funds_cols
        and "classification" in funds_cols
    )


def backup(conn) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for table in ("funds", "navpu"):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        path = BACKUP_DIR / f"{table}-{stamp}.json"
        path.write_text(
            json.dumps(rows, indent=1, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"backed up {table}: {len(rows)} rows -> {path}")
    return BACKUP_DIR


def main() -> None:
    cfg = db_config()
    print("target:", "remote Turso" if cfg["is_remote"] else "local file")
    print("env keys found:", ", ".join(cfg["found_env_keys"]) or "none")

    conn = database.connect()
    try:
        if is_migrated(conn):
            funds = conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
            navpu = conn.execute("SELECT COUNT(*) FROM navpu").fetchone()[0]
            print(
                f"already migrated: funds={funds} navpu={navpu} "
                "(latest-only, source present) - nothing to do"
            )
            return

        funds_before = conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
        navpu_before = conn.execute("SELECT COUNT(*) FROM navpu").fetchone()[0]
        distinct_funds = conn.execute(
            "SELECT COUNT(DISTINCT fund_id) FROM navpu"
        ).fetchone()[0]
        backup(conn)

        conn.execute(CREATE_NAVPU_V2)
        conn.execute(COPY_NAVPU_V2)
        conn.execute("DROP TABLE navpu")
        conn.execute("ALTER TABLE navpu_v2 RENAME TO navpu")

        conn.execute(CREATE_FUNDS_V2)
        conn.execute(COPY_FUNDS_V2)
        conn.execute("DROP TABLE funds")
        conn.execute("ALTER TABLE funds_v2 RENAME TO funds")

        for statement in database.SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.commit()

        funds_after = conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
        navpu_after = conn.execute("SELECT COUNT(*) FROM navpu").fetchone()[0]
        print(
            f"funds: {funds_before} -> {funds_after} rows, "
            f"navpu: {navpu_before} -> {navpu_after} rows "
            f"(distinct funds in old navpu: {distinct_funds})"
        )
        if navpu_after != distinct_funds:
            print(
                "WARNING: kept navpu rows differ from old distinct fund_ids "
                "- inspect output/backup/"
            )
        if funds_after != funds_before:
            print("WARNING: funds count changed - inspect output/backup/")
        if navpu_after == distinct_funds and funds_after == funds_before:
            print("migration complete")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
