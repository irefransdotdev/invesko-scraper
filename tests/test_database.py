"""Latest-only upsert semantics, transactions, and retry behaviour."""

import json
from types import SimpleNamespace

import pytest

import database
from items import SOURCE_MUTUAL, SOURCE_UITF, DividendItem, NavpuItem, PseCompanyItem


def fund_item(fund_id, name="Test Fund", **kw):
    base = dict(
        source=SOURCE_UITF,
        batch_key="3",
        batch_name="BPI Trust",
        fund_id=str(fund_id),
        fund_name=name,
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


def test_insert_then_update_keeps_single_row(conn):
    database.save_batch(SOURCE_UITF, "3", "BPI", "2026-10-01", [fund_item(10)])
    database.save_batch(
        SOURCE_UITF, "3", "BPI", "2026-10-02",
        [fund_item(10, as_of_date="2026-10-02", navpu=101.5)],
    )
    rows = conn.execute("SELECT * FROM navpu").fetchall()
    assert len(rows) == 1
    as_of, navpu = conn.execute(
        "SELECT as_of_date, navpu FROM navpu"
    ).fetchone()
    assert (as_of, navpu) == ("2026-10-02", 101.5)
    first_seen, last_seen = conn.execute(
        "SELECT first_seen_at, last_seen_at FROM funds"
    ).fetchone()
    assert first_seen and last_seen
    assert conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0] == 1


def test_stale_as_of_never_downgrades(conn):
    database.save_batch(SOURCE_UITF, "3", "BPI", "2026-10-02", [fund_item(10)])
    database.save_batch(
        SOURCE_UITF, "3", "BPI", "2026-09-30",
        [fund_item(10, as_of_date="2026-09-30", navpu=99.0)],
    )
    navpu = conn.execute("SELECT navpu FROM navpu").fetchone()[0]
    assert navpu == 100.0


def test_same_date_correction_overwrites(conn):
    database.save_batch(SOURCE_UITF, "3", "BPI", "2026-10-02", [fund_item(10)])
    database.save_batch(
        SOURCE_UITF, "3", "BPI", "2026-10-02",
        [fund_item(10, navpu=100.75)],
    )
    navpu = conn.execute("SELECT navpu FROM navpu").fetchone()[0]
    assert navpu == 100.75


def test_mutual_batch_leaves_banks_untouched(conn):
    item = fund_item(
        7,
        name="Alpha Fund, Inc.",
        source=SOURCE_MUTUAL,
        batch_key="pifa",
        batch_name="PIFA",
        category="Stock Funds",
        classification="Primarily invested in Peso securities (shares)",
        currency=None,
        roi_3y=1.5,
    )
    database.save_batch(SOURCE_MUTUAL, "pifa", "PIFA", "2026-10-02", [item])
    assert conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0] == 0
    bank_id, source = conn.execute(
        "SELECT bank_id, source FROM funds"
    ).fetchone()
    assert bank_id is None and source == "mutual"
    roi_3y, roi_5y = conn.execute(
        "SELECT roi_3y, roi_5y FROM navpu"
    ).fetchone()
    assert (roi_3y, roi_5y) == (1.5, None)


def test_failed_batch_leaves_no_partial_rows(db, monkeypatch):
    original = database._write_batch

    def partial_then_fail(conn, *args, **kwargs):
        original(conn, *args, **kwargs)
        raise RuntimeError("simulated crash after writing rows")

    monkeypatch.setattr(database, "_write_batch", partial_then_fail)
    with pytest.raises(database.BatchSaveError):
        database.save_batch(
            SOURCE_UITF, "3", "BPI", "2026-10-02",
            [fund_item(10), fund_item(11)],
            attempts=1,
        )
    conn = database.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM navpu").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0] == 0
    finally:
        conn.close()


def test_retry_then_succeed(db, monkeypatch):
    calls = {"n": 0}
    original = database._write_batch

    def flaky(conn, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        original(conn, *args, **kwargs)

    monkeypatch.setattr(database, "_write_batch", flaky)
    saved = database.save_batch(
        SOURCE_UITF, "3", "BPI", "2026-10-02", [fund_item(10)],
        attempts=3, base_delay=0,
    )
    assert saved == 1
    assert calls["n"] == 2
    conn = database.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM navpu").fetchone()[0] == 1
    finally:
        conn.close()


def test_funds_exist_flags(conn):
    assert database.funds_exist(SOURCE_UITF, bank_id=3) is False
    database.save_batch(SOURCE_UITF, "3", "BPI", "2026-10-02", [fund_item(10)])
    assert database.funds_exist(SOURCE_UITF, bank_id=3) is True
    assert database.funds_exist(SOURCE_UITF, bank_id=14) is False
    assert database.funds_exist(SOURCE_MUTUAL) is False


def dividend_item(**kw):
    base = dict(
        cmpy_id=86,
        security_type="COMMON",
        dividend_type="Cash",
        dividend_rate="Php1.33",
        ex_date="2026-05-04",
        record_date="2026-05-05",
        payment_date="2026-05-21",
        circular_no="C02611-2026",
        circular_ref="515cc3f7b32c0b1f64d70b69f0a3140b",
        scraped_at="2026-10-07T00:00:00",
    )
    base.update(kw)
    return DividendItem(**base)


def company_item(**kw):
    base = dict(cmpy_id=86, name="Jollibee Foods Corporation", symbol="JFC")
    base.update(kw)
    return PseCompanyItem(**base)


def test_save_pse_batch_writes_both_tables(conn):
    saved = database.save_pse_batch([company_item()], [dividend_item()])
    assert saved == (1, 1)
    company = conn.execute(
        "SELECT name, symbol, sector, last_scraped_at FROM pse_companies"
    ).fetchone()
    assert company[:3] == ("Jollibee Foods Corporation", "JFC", "")
    assert company[3]
    row = conn.execute(
        "SELECT security_type, ex_date, circular_no FROM pse_dividends"
    ).fetchone()
    assert row == ("COMMON", "2026-05-04", "C02611-2026")


def test_save_pse_batch_is_idempotent(conn):
    database.save_pse_batch([company_item()], [dividend_item()])
    database.save_pse_batch(
        [company_item(sector="Industrial")],
        [dividend_item(dividend_rate="Php1.40")],
    )
    assert conn.execute("SELECT COUNT(*) FROM pse_companies").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM pse_dividends").fetchone()[0] == 1
    sector = conn.execute("SELECT sector FROM pse_companies").fetchone()[0]
    rate = conn.execute("SELECT dividend_rate FROM pse_dividends").fetchone()[0]
    assert sector == "Industrial"
    assert rate == "Php1.40"


def test_save_pse_batch_null_keys_normalize_to_empty(conn):
    database.save_pse_batch(
        [],
        [
            dividend_item(
                security_type=None,
                ex_date=None,
                circular_no=None,
                record_date=None,
            )
        ],
    )
    database.save_pse_batch([], [dividend_item(security_type=None, ex_date=None, circular_no=None)])
    assert conn.execute("SELECT COUNT(*) FROM pse_dividends").fetchone()[0] == 1
    row = conn.execute(
        "SELECT security_type, ex_date, circular_no FROM pse_dividends"
    ).fetchone()
    assert row == ("", "", "")


def test_count_pse_rows(conn):
    assert database.count_pse_rows() == (0, 0)
    database.save_pse_batch(
        [company_item(), company_item(cmpy_id=6, name="PLDT Inc.", symbol="TEL")],
        [dividend_item(), dividend_item(cmpy_id=6, ex_date="2026-03-25")],
    )
    assert database.count_pse_rows() == (2, 2)
