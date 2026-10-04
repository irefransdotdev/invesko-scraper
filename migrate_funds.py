"""Check funds/navpu consistency per source (post-v2: navpu is latest-only).

The original one-time backfill (funds from navpu metadata) is obsolete:
navpu no longer stores fund metadata, and both tables are written in the
same transaction, so they can only diverge through manual edits.
"""

import database


def main() -> None:
    database.init_schema()
    conn = database.connect()
    try:
        problems = 0
        sources = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT source FROM funds "
                "UNION SELECT DISTINCT source FROM navpu ORDER BY 1"
            ).fetchall()
        ]
        if not sources:
            print("no data yet")
            return
        for source in sources:
            fund_rows = conn.execute(
                "SELECT COUNT(*) FROM funds WHERE source = ?", (source,)
            ).fetchone()[0]
            navpu_rows = conn.execute(
                "SELECT COUNT(*) FROM navpu WHERE source = ?", (source,)
            ).fetchone()[0]
            missing_navpu = conn.execute(
                "SELECT COUNT(*) FROM funds f WHERE f.source = ? "
                "AND NOT EXISTS (SELECT 1 FROM navpu n "
                "WHERE n.source = f.source AND n.fund_id = f.fund_id)",
                (source,),
            ).fetchone()[0]
            orphan_navpu = conn.execute(
                "SELECT COUNT(*) FROM navpu n WHERE n.source = ? "
                "AND NOT EXISTS (SELECT 1 FROM funds f "
                "WHERE f.source = n.source AND f.fund_id = n.fund_id)",
                (source,),
            ).fetchone()[0]
            print(
                f"{source}: funds={fund_rows} navpu={navpu_rows} "
                f"funds_without_navpu={missing_navpu} "
                f"navpu_without_funds={orphan_navpu}"
            )
            problems += missing_navpu + orphan_navpu
        if problems:
            print(f"WARNING: {problems} inconsistent rows")
        else:
            print("consistent: every fund has exactly one navpu row")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
