"""Turso (libSQL) access layer: schema, per-batch transactional saves with retry.

Data model (v2):
- funds: metadata per fund, PRIMARY KEY (source, fund_id); source is
  'uitf' (bank pages on uitf.com.ph) or 'mutual' (PIFA page). bank_id is
  NULL for mutual funds.
- navpu: LATEST-only measurements per fund, PRIMARY KEY (source, fund_id).
  Every scrape overwrites the fund's single row (new funds are inserted).
- pse_companies / pse_dividends: PSE Edge listing metadata and dividend
  history (pse_dividends PRIMARY KEY makes re-runs idempotent).
"""

import time
from datetime import datetime

import libsql

from config import db_config

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS banks (
        bank_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        last_scraped_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS funds (
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
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_funds_bank_name
    ON funds (bank_id, fund_name)
    """,
    """
    CREATE TABLE IF NOT EXISTS navpu (
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
    """,
    """
    CREATE TABLE IF NOT EXISTS pse_companies (
        cmpy_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        symbol TEXT,
        sector TEXT,
        last_scraped_at TEXT
    )
    """,
    # Upsert key keeps re-runs idempotent: PSE occasionally revises a row
    # (same company/security/ex-date/circular) and the update replaces it.
    # Key columns are written as '' not NULL - SQLite treats NULLs in a
    # PRIMARY KEY as distinct, which would duplicate rows on every run.
    """
    CREATE TABLE IF NOT EXISTS pse_dividends (
        cmpy_id INTEGER NOT NULL,
        security_type TEXT NOT NULL DEFAULT '',
        dividend_type TEXT,
        dividend_rate TEXT,
        ex_date TEXT NOT NULL DEFAULT '',
        record_date TEXT,
        payment_date TEXT,
        circular_no TEXT NOT NULL DEFAULT '',
        circular_ref TEXT,
        scraped_at TEXT NOT NULL,
        PRIMARY KEY (cmpy_id, security_type, ex_date, circular_no)
    )
    """,
]

UPSERT_BANK_SQL = """
    INSERT INTO banks (bank_id, name, last_scraped_at)
    VALUES (?, ?, ?)
    ON CONFLICT(bank_id) DO UPDATE SET
        name = CASE
            WHEN excluded.name = '' THEN banks.name
            ELSE excluded.name
        END,
        last_scraped_at = excluded.last_scraped_at
"""

# Discovery only: adds/refreshes names but never touches last_scraped_at
# (that timestamp marks a NAVPU page scrape, used for deleted-fund checks).
UPSERT_BANK_META_SQL = """
    INSERT INTO banks (bank_id, name, last_scraped_at)
    VALUES (?, ?, NULL)
    ON CONFLICT(bank_id) DO UPDATE SET
        name = CASE
            WHEN excluded.name = '' THEN banks.name
            ELSE excluded.name
        END
"""

UPSERT_FUND_SQL = """
    INSERT INTO funds (
        source, fund_id, bank_id, fund_name, category, classification,
        currency, details_url, first_seen_at, last_seen_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source, fund_id) DO UPDATE SET
        bank_id = excluded.bank_id,
        fund_name = excluded.fund_name,
        category = excluded.category,
        classification = excluded.classification,
        currency = excluded.currency,
        details_url = excluded.details_url,
        last_seen_at = excluded.last_seen_at
"""

# Latest-only: overwrite the fund's single row; never accumulate history.
# The as_of_date guard stops a stale/regressed feed from downgrading a
# newer row (an unreadable "" as_of_date never overwrites either).
UPSERT_NAVPU_SQL = """
    INSERT INTO navpu (
        source, fund_id, as_of_date, navpu, navpu_as_of,
        roi_1y, roi_3y, roi_5y, roi_10y, roi_ytd, scraped_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source, fund_id) DO UPDATE SET
        as_of_date = excluded.as_of_date,
        navpu = excluded.navpu,
        navpu_as_of = excluded.navpu_as_of,
        roi_1y = excluded.roi_1y,
        roi_3y = excluded.roi_3y,
        roi_5y = excluded.roi_5y,
        roi_10y = excluded.roi_10y,
        roi_ytd = excluded.roi_ytd,
        scraped_at = excluded.scraped_at
    WHERE excluded.as_of_date >= navpu.as_of_date
"""

UPSERT_PSE_COMPANY_SQL = """
    INSERT INTO pse_companies (cmpy_id, name, symbol, sector, last_scraped_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(cmpy_id) DO UPDATE SET
        name = excluded.name,
        symbol = excluded.symbol,
        sector = excluded.sector,
        last_scraped_at = excluded.last_scraped_at
"""

UPSERT_PSE_DIVIDEND_SQL = """
    INSERT INTO pse_dividends (
        cmpy_id, security_type, dividend_type, dividend_rate,
        ex_date, record_date, payment_date, circular_no, circular_ref,
        scraped_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(cmpy_id, security_type, ex_date, circular_no) DO UPDATE SET
        dividend_type = excluded.dividend_type,
        dividend_rate = excluded.dividend_rate,
        record_date = excluded.record_date,
        payment_date = excluded.payment_date,
        circular_ref = excluded.circular_ref,
        scraped_at = excluded.scraped_at
"""


class BatchSaveError(Exception):
    """Raised when a scrape batch could not be saved after all retries."""


class BankListError(Exception):
    """Raised when the discovered bank list could not be saved."""


# Backwards-compatible alias (older scripts/tests referenced the old name).
BankSaveError = BatchSaveError


def connect() -> libsql.Connection:
    cfg = db_config()
    if cfg["is_remote"]:
        return libsql.connect(cfg["url"], auth_token=cfg["auth_token"])
    from config import LOCAL_DB_PATH

    LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return libsql.connect(str(LOCAL_DB_PATH))


def init_schema(attempts: int = 3, delay: float = 1.0) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        conn = connect()
        try:
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            conn.commit()
            return
        except Exception as error:  # noqa: BLE001 - retry on any DB error
            last_error = error
            _safe_rollback(conn)
            time.sleep(delay * (2**attempt))
        finally:
            _safe_close(conn)
    raise RuntimeError(f"Could not initialize database schema: {last_error}")


def save_batch(
    source: str,
    batch_key: str,
    batch_name: str | None,
    as_of_date: str | None,
    items: list,
    attempts: int = 4,
    base_delay: float = 1.0,
    logger=None,
) -> int:
    """Save every fund row for one scrape batch in a single transaction.

    Retries `attempts` times (initial try + retries) with exponential backoff.
    Each attempt uses a fresh connection; failures roll back and leave no
    partial data. Raises BatchSaveError after the final failed attempt.
    """
    bank_row_id: int | None = None
    if source == "uitf":
        bank_row_id = int(batch_key)
    scraped_at = datetime.now().isoformat(timespec="seconds")
    last_error: Exception | None = None

    for attempt in range(attempts):
        conn = connect()
        try:
            _write_batch(
                conn, source, bank_row_id, batch_name, scraped_at,
                as_of_date, items,
            )
            conn.commit()
            if logger and attempt:
                logger.info(
                    "source=%s batch=%s saved after %d retries",
                    source,
                    batch_key,
                    attempt,
                )
            return len(items)
        except Exception as error:  # noqa: BLE001 - retry on any DB error
            last_error = error
            _safe_rollback(conn)
            if logger:
                logger.warning(
                    "source=%s batch=%s DB save attempt %d/%d failed: %s",
                    source,
                    batch_key,
                    attempt + 1,
                    attempts,
                    error,
                )
            if attempt < attempts - 1:
                time.sleep(base_delay * (2**attempt))
        finally:
            _safe_close(conn)

    raise BatchSaveError(
        f"source={source} batch={batch_key}: save failed after "
        f"{attempts} attempts: {last_error}"
    )


def save_pse_batch(
    companies: list,
    dividends: list,
    attempts: int = 4,
    base_delay: float = 1.0,
    logger=None,
) -> tuple[int, int]:
    """Upsert PSE company metadata and dividend rows in one transaction.

    Same retry policy as save_batch: fresh connection per attempt, rollback
    on failure, exponential backoff, BatchSaveError after the last attempt.
    Returns (companies_saved, dividends_saved). Idempotent - re-running a
    crawl overwrites the same rows instead of duplicating them.
    """
    scraped_at = datetime.now().isoformat(timespec="seconds")
    last_error: Exception | None = None

    for attempt in range(attempts):
        conn = connect()
        try:
            for company in companies:
                conn.execute(
                    UPSERT_PSE_COMPANY_SQL,
                    (
                        int(company.cmpy_id),
                        company.name,
                        company.symbol or "",
                        company.sector or "",
                        scraped_at,
                    ),
                )
            for row in dividends:
                conn.execute(
                    UPSERT_PSE_DIVIDEND_SQL,
                    (
                        int(row.cmpy_id),
                        row.security_type or "",
                        row.dividend_type,
                        row.dividend_rate,
                        row.ex_date or "",
                        row.record_date,
                        row.payment_date,
                        row.circular_no or "",
                        row.circular_ref,
                        row.scraped_at or scraped_at,
                    ),
                )
            conn.commit()
            if logger and attempt:
                logger.info(
                    "pse batch saved after %d retries", attempt
                )
            return len(companies), len(dividends)
        except Exception as error:  # noqa: BLE001 - retry on any DB error
            last_error = error
            _safe_rollback(conn)
            if logger:
                logger.warning(
                    "pse batch DB save attempt %d/%d failed: %s",
                    attempt + 1,
                    attempts,
                    error,
                )
            if attempt < attempts - 1:
                time.sleep(base_delay * (2**attempt))
        finally:
            _safe_close(conn)

    raise BatchSaveError(
        f"pse batch: save failed after {attempts} attempts: {last_error}"
    )


def count_pse_rows() -> tuple[int, int]:
    """(companies, dividends) row counts - used by the spider's end-of-run log."""
    conn = connect()
    try:
        companies = conn.execute("SELECT COUNT(*) FROM pse_companies").fetchone()
        dividends = conn.execute("SELECT COUNT(*) FROM pse_dividends").fetchone()
        return int(companies[0]), int(dividends[0])
    finally:
        _safe_close(conn)


def funds_exist(source: str, bank_id: int | None = None) -> bool:
    """True when the funds table already holds rows for this scrape target.

    Used to warn about empty batches: a page that suddenly yields 0 funds
    while the DB still has funds for it likely means a parser/site failure.
    """
    conn = connect()
    try:
        if bank_id is not None:
            row = conn.execute(
                "SELECT 1 FROM funds WHERE source = ? AND bank_id = ? LIMIT 1",
                (source, bank_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM funds WHERE source = ? LIMIT 1", (source,)
            ).fetchone()
        return row is not None
    finally:
        _safe_close(conn)


def load_banks() -> list[tuple[int, str | None]]:
    """All known fund providers as (bank_id, name), ordered by bank_id.

    The daily uitf spider uses this as its work list, so discovery of new
    providers stays in uitf_discover instead of the daily run.
    """
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT bank_id, name FROM banks ORDER BY bank_id"
        ).fetchall()
        return [(row[0], row[1]) for row in rows]
    finally:
        _safe_close(conn)


def save_bank_list(
    providers: list[tuple[int, str | None]],
    attempts: int = 4,
    base_delay: float = 1.0,
    logger=None,
) -> tuple[int, int]:
    """Upsert discovered providers in one transaction.

    Returns (total, new) where `new` is the number of bank_ids that were
    not present before this call. Leaves last_scraped_at untouched.
    Raises BankListError after all retries.
    """
    existing = set(_existing_bank_ids())
    last_error: Exception | None = None

    for attempt in range(attempts):
        conn = connect()
        try:
            for bank_id, name in providers:
                conn.execute(UPSERT_BANK_META_SQL, (bank_id, name or ""))
            conn.commit()
            if logger and attempt:
                logger.info(
                    "bank list saved after %d retries", attempt
                )
            new = sum(1 for bank_id, _ in providers if bank_id not in existing)
            return len(providers), new
        except Exception as error:  # noqa: BLE001 - retry on any DB error
            last_error = error
            _safe_rollback(conn)
            if logger:
                logger.warning(
                    "bank list save attempt %d/%d failed: %s",
                    attempt + 1,
                    attempts,
                    error,
                )
            if attempt < attempts - 1:
                time.sleep(base_delay * (2**attempt))
        finally:
            _safe_close(conn)

    raise BankListError(
        f"bank list save failed after {attempts} attempts: {last_error}"
    )


def _existing_bank_ids() -> list[int]:
    conn = connect()
    try:
        rows = conn.execute("SELECT bank_id FROM banks").fetchall()
        return [row[0] for row in rows]
    finally:
        _safe_close(conn)


def _write_batch(
    conn, source, bank_id, batch_name, scraped_at, as_of_date, items
) -> None:
    if source == "uitf":
        conn.execute(
            UPSERT_BANK_SQL, (bank_id, batch_name or "", scraped_at)
        )
    for item in items:
        conn.execute(
            UPSERT_FUND_SQL,
            (
                source,
                int(item.fund_id),
                bank_id,
                item.fund_name,
                item.category,
                item.classification,
                item.currency,
                item.details_url,
                scraped_at,
                scraped_at,
            ),
        )
        conn.execute(
            UPSERT_NAVPU_SQL,
            (
                source,
                int(item.fund_id),
                as_of_date or "",
                item.navpu,
                item.navpu_as_of,
                item.roi_1y,
                item.roi_3y,
                item.roi_5y,
                item.roi_10y,
                item.roi_ytd,
                scraped_at,
            ),
        )


def _safe_rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 - connection may be dead
        pass


def _safe_close(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001 - already closed
        pass
