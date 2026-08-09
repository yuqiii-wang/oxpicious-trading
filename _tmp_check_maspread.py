"""Temporary diagnostic: report mov_ave_spreads_detail row counts per sec_type
and confirm source stats tables are JOINable for etf/index/stock.

Run via: python -m _tmp_check_maspread
"""
import asyncio
import os

import asyncpg

from _common.db_commons import _get_conn_params

params = _get_conn_params()


async def main() -> None:
    conn = await asyncpg.connect(**params)
    try:
        print("=== analysis.mov_ave_spreads_detail by sec_type ===")
        rows = await conn.fetch(
            """
            SELECT sec_type, COUNT(*) AS n, COUNT(DISTINCT code) AS codes,
                   MIN(date) AS min_d, MAX(date) AS max_d
            FROM analysis.mov_ave_spreads_detail
            GROUP BY sec_type ORDER BY sec_type
            """
        )
        if not rows:
            print("  (table is EMPTY)")
        for r in rows:
            print(
                f"  {r['sec_type']:8s} rows={r['n']:>10,}  "
                f"codes={r['codes']:>6,}  date=[{r['min_d']} .. {r['max_d']}]"
            )

        print("\n=== peaks_and_floors by sec_type ===")
        rows = await conn.fetch(
            """
            SELECT sec_type, COUNT(*) AS n, COUNT(DISTINCT code) AS codes
            FROM analysis.mov_ave_peaks_and_floors
            GROUP BY sec_type ORDER BY sec_type
            """
        )
        if not rows:
            print("  (table is EMPTY)")
        for r in rows:
            print(
                f"  {r['sec_type']:8s} rows={r['n']:>10,}  "
                f"codes={r['codes']:>6,}"
            )

        print("\n=== mov_ave_rsi by sec_type ===")
        rows = await conn.fetch(
            """
            SELECT sec_type, COUNT(*) AS n, COUNT(DISTINCT code) AS codes
            FROM analysis.mov_ave_rsi
            GROUP BY sec_type ORDER BY sec_type
            """
        )
        if not rows:
            print("  (table is EMPTY)")
        for r in rows:
            print(
                f"  {r['sec_type']:8s} rows={r['n']:>10,}  "
                f"codes={r['codes']:>6,}"
            )

        print("\n=== source stats tables (active-universe basis) ===")
        for tbl in (
            "stats.etf_identity",
            "stats.index_identity",
            "stats.stock_identity",
        ):
            r = await conn.fetchrow(
                f"SELECT COUNT(*) AS n, COUNT(DISTINCT code) AS codes, "
                f"MAX(date) AS max_d FROM {tbl}"
            )
            print(
                f"  {tbl:30s} rows={r['n']:>10,}  "
                f"codes={r['codes']:>6,}  max_date={r['max_d']}"
            )

        print("\n=== JOINable source rows per sec_type (last 90 days) ===")
        queries = {
            "etf": """
                SELECT COUNT(*) AS n, COUNT(DISTINCT i.code) AS codes
                FROM stats.etf_identity i
                JOIN stats.etf_basic_stats b ON b.date=i.date AND b.code=i.code
                JOIN stats.etf_tech_stats t   ON t.date=i.date AND t.code=i.code
                JOIN stats.etf_liquidity_margin m ON m.date=i.date AND m.code=i.code
                WHERE i.date >= CURRENT_DATE - 90
            """,
            "index": """
                SELECT COUNT(*) AS n, COUNT(DISTINCT i.code) AS codes
                FROM stats.index_identity i
                JOIN stats.index_basic_stats b ON b.date=i.date AND b.code=i.code
                JOIN stats.index_tech_stats  t ON t.date=i.date AND t.code=i.code
                WHERE i.date >= CURRENT_DATE - 90
            """,
            "stock": """
                SELECT COUNT(*) AS n, COUNT(DISTINCT i.code) AS codes
                FROM stats.stock_identity i
                JOIN stats.stock_basic_stats b ON b.date=i.date AND b.code=i.code
                JOIN stats.stock_tech_stats  t ON t.date=i.date AND t.code=i.code
                JOIN stats.stock_liquidity_margin m ON m.date=i.date AND m.code=i.code
                WHERE i.date >= CURRENT_DATE - 90 AND b.close IS NOT NULL
            """,
        }
        for sec, q in queries.items():
            r = await conn.fetchrow(q)
            print(
                f"  {sec:8s} joinable_rows(90d)={r['n']:>10,}  "
                f"codes={r['codes']:>6,}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
