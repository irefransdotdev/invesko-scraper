"""Print database contents summary (counts only - never credentials)."""

import database
from config import db_config


def main() -> None:
    cfg = db_config()
    print("target:", "remote Turso" if cfg["is_remote"] else "local file")
    print("env keys found:", ", ".join(cfg["found_env_keys"]) or "none")

    conn = database.connect()
    try:
        total_navpu = conn.execute("SELECT COUNT(*) FROM navpu").fetchone()[0]
        total_funds = conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
        print(f"funds: {total_funds}  navpu rows: {total_navpu}")

        print()
        print("--- uitf (per bank) ---")
        banks = conn.execute(
            "SELECT bank_id, name, last_scraped_at FROM banks ORDER BY bank_id"
        ).fetchall()
        print(f"{'bank_id':>7}  {'funds':>5}  {'navpu':>5}  {'latest as_of':<12}  name")
        for bank_id, name, _scraped in banks:
            fund_count = conn.execute(
                "SELECT COUNT(*) FROM funds WHERE source = 'uitf' "
                "AND bank_id = ?",
                (bank_id,),
            ).fetchone()[0]
            navpu_count, latest = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(n.as_of_date), '-') "
                "FROM navpu n JOIN funds f "
                "ON n.source = f.source AND n.fund_id = f.fund_id "
                "WHERE f.source = 'uitf' AND f.bank_id = ?",
                (bank_id,),
            ).fetchone()
            print(
                f"{bank_id:>7}  {fund_count:>5}  {navpu_count:>5}  "
                f"{latest:<12}  {name}"
            )

        print()
        print("--- mutual (PIFA) ---")
        mutual = conn.execute(
            "SELECT COUNT(*) FROM funds WHERE source = 'mutual'"
        ).fetchone()[0]
        navpu_count, latest = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(as_of_date), '-') "
            "FROM navpu WHERE source = 'mutual'"
        ).fetchone()
        print(f"funds: {mutual}  navpu: {navpu_count}  latest as_of: {latest}")

        print()
        print("--- per source (navpu rows vs funds) ---")
        for source, navpu_rows, fund_rows in conn.execute(
            "SELECT f.source, "
            "(SELECT COUNT(*) FROM navpu n WHERE n.source = f.source), "
            "COUNT(*) FROM funds f GROUP BY f.source"
        ).fetchall():
            flag = "" if navpu_rows == fund_rows else "  <- MISMATCH"
            print(f"{source}: navpu={navpu_rows} funds={fund_rows}{flag}")

        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_%' ORDER BY name"
        ).fetchall()
        print()
        print("indexes:", ", ".join(row[0] for row in indexes) or "none")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
