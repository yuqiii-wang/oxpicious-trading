"""
build_index_exts.py — Build stats.index_exts + stats.etf_trading_amt.

stats.index_exts (per-(date, index_code) extension metrics):
  etf_num           = COUNT(DISTINCT etf_liquidity_margin.code) across ALL ETFs
                      whose stats.sec_classification.parent_index_code = this
                      index code on this date.
  total_etf_amt     = Σ etf_liquidity_margin.amount_wan × 1e4 (yuan) across the
                      same ETF universe. NULL when no ETF tracks the index.
                      Consumed by analyze_sec_alloc_perf_attribution.py as the
                      index's ETF-market trading volume.
  total_etf_amt_ma5 = 5-trading-day moving average of total_etf_amt (AVG over
                      the trailing 5 rows per code, ordered by date).
  stock_num         = COUNT(DISTINCT stock_code) from the latest
                      stats.sec_composition snapshot (source_type='index')
                      with snapshot_date <= this date. Carries forward until
                      the next snapshot. NULL when the index has no
                      composition snapshot. Used by analyze_industry_sentiments.py
                      to classify each index into a pool_size bucket:
                      small (stock_num < 51), mid (51-180), large (> 180).

  Only rows where (date, code) exists in stats.index_identity are inserted
  (FK constraint). Indices with no tracking ETF (e.g. 000001 上证指数) simply
  have no row here — their etf_num is NULL when LEFT JOINed in downstream
  queries.

stats.etf_trading_amt (per-(date, industry_id) aggregate ETF turnover):
  Same source (etf_liquidity_margin) but grouped by the linked parent index's
  industry_id (stats.sec_classification.industry_id where type='index' and
  code = ETF's parent_index_code). PK (date, code) where `code` is the
  industry_id (e.g. BANKS, SEMI, BROAD_CSI) — NOT an index code. Consumed by
  the Perf-Attr "Industry Trading Amt contribution" chart.

Both tables are TRUNCATE-then-INSERT on every run (full recompute).

Usage:
  python build_index_exts.py
"""
import os
import sys
import time
import asyncio

from utils.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    bulk_upsert_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
)

setup_utf8_stdout()

TABLE_INDEX = "stats.index_exts"
TABLE_INDUSTRY = "stats.etf_trading_amt"


async def main():
    t0 = time.time()
    print_build_header(
        "BUILD INDEX EXTS + ETF TRADING AMT "
        "(per date × index / industry aggregation)",
        index_table=TABLE_INDEX,
        industry_table=TABLE_INDUSTRY,
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 1: truncate both tables -----------------------------
        print(f"\n[1/6] Truncating {TABLE_INDEX} and {TABLE_INDUSTRY}...",
              flush=True)
        await truncate_table_async(conn, TABLE_INDEX)
        await truncate_table_async(conn, TABLE_INDUSTRY)

        # ---- Step 2: compute per-(date, index) aggregation ------------
        # etf_num/total_etf_amt are aggregated from etf_liquidity_margin
        # JOIN sec_classification (parent_index_code). total_etf_amt_ma5 is
        # a 5-row trailing AVG (PARTITION BY code ORDER BY date) computed
        # over the aggregated series. SUM ignores NULL amount_wan rows, so
        # total_etf_amt is NULL only when no tracking ETF has any amount on
        # that date. The final JOIN to index_identity satisfies the FK
        # constraint — only rows where (date, code) exists in index_identity
        # are inserted.
        print("\n[2/6] Computing etf_num + total_etf_amt + ma5 per "
              "(date, index_code)...", flush=True)
        rows = await conn.fetch("""
            WITH etf_agg AS (
                SELECT
                    sc.parent_index_code AS code,
                    l.date,
                    COUNT(DISTINCT l.code) AS etf_num,
                    SUM(l.amount_wan) * 10000 AS total_etf_amt
                FROM stats.etf_liquidity_margin l
                JOIN stats.sec_classification sc
                    ON sc.code = l.code AND sc.type = 'etf'
                WHERE sc.parent_index_code <> ''
                GROUP BY sc.parent_index_code, l.date
            ),
            etf_with_ma AS (
                SELECT
                    code, date, etf_num, total_etf_amt,
                    AVG(total_etf_amt) OVER (
                        PARTITION BY code ORDER BY date
                        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                    ) AS total_etf_amt_ma5
                FROM etf_agg
            )
            SELECT ewm.code, ewm.date, ewm.etf_num,
                   ewm.total_etf_amt, ewm.total_etf_amt_ma5
            FROM etf_with_ma ewm
            JOIN stats.index_identity ii
                ON ii.date = ewm.date AND ii.code = ewm.code
            ORDER BY ewm.code, ewm.date
        """)
        print(f"    → {len(rows):,} rows across "
              f"{len(set(r['code'] for r in rows))} indices "
              f"(only indices with linked ETFs — joined via sec_classification)",
              flush=True)

        # ---- Step 3: upsert per-(date, index) rows --------------------
        print(f"\n[3/6] Upserting into {TABLE_INDEX}...", flush=True)
        if not rows:
            print("    → no data to insert.", flush=True)
        else:
            data = [
                {
                    "date": r["date"],
                    "code": r["code"],
                    "etf_num": r["etf_num"],
                    "total_etf_amt": r["total_etf_amt"],
                    "total_etf_amt_ma5": r["total_etf_amt_ma5"],
                }
                for r in rows
            ]
            n = await bulk_upsert_async(
                conn, TABLE_INDEX, data,
                key_columns=["date", "code"],
                batch_size=1000,
            )
            print(f"    → upserted {n:,} rows", flush=True)

        # ---- Step 3b: backfill stock_num from sec_composition ---------
        # For every (date, code) row in index_exts, find the latest
        # stats.sec_composition snapshot (source_type='index') with
        # snapshot_date <= date and COUNT(DISTINCT stock_code). Carries
        # forward until the next snapshot. NULL when the index has no
        # composition snapshot at all (e.g. cross-market H-prefixed
        # indices without a CSI closeweight pull). Done as a UPDATE FROM
        # LATERAL join over the just-upserted rows.
        print(f"\n[3b/6] Backfilling stock_num from sec_composition "
              f"(latest snapshot <= date per code)...", flush=True)
        stock_num_rows = await conn.fetch("""
            WITH stock_counts AS (
                SELECT code, snapshot_date,
                       COUNT(DISTINCT stock_code) AS stock_num
                FROM stats.sec_composition
                WHERE source_type = 'index'
                GROUP BY code, snapshot_date
            )
            UPDATE stats.index_exts ie
            SET stock_num = sc.stock_num
            FROM stock_counts sc
            WHERE sc.code = ie.code
              AND sc.snapshot_date = (
                  SELECT MAX(snapshot_date)
                  FROM stock_counts sc2
                  WHERE sc2.code = ie.code
                    AND sc2.snapshot_date <= ie.date
              )
            RETURNING 1
        """)
        print(f"    → updated stock_num on {len(stock_num_rows):,} "
              f"(date, code) rows", flush=True)

        # ---- Step 4: compute per-(date, industry_id) aggregation ------
        # Same etf_liquidity_margin source, but grouped by the linked
        # parent index's industry_id. Joins:
        #   etf_liquidity_margin l
        #   → sec_classification sc_etf (type='etf', parent_index_code)
        #   → sec_classification sc_idx (type='index', code=parent_index_code,
        #                                  industry_id)
        # No FK constraint on etf_trading_amt — every (date, industry_id)
        # pair with at least one tracking ETF is inserted. industry_id is
        # always non-empty (DEFAULT 'OTHER' on sec_classification).
        print("\n[4/6] Computing etf_num + total_etf_amt + ma5 per "
              "(date, industry_id)...", flush=True)
        ind_rows = await conn.fetch("""
            WITH etf_ind_agg AS (
                SELECT
                    sc_idx.industry_id AS code,
                    l.date,
                    COUNT(DISTINCT l.code) AS etf_num,
                    SUM(l.amount_wan) * 10000 AS total_etf_amt
                FROM stats.etf_liquidity_margin l
                JOIN stats.sec_classification sc_etf
                    ON sc_etf.code = l.code AND sc_etf.type = 'etf'
                JOIN stats.sec_classification sc_idx
                    ON sc_idx.code = sc_etf.parent_index_code
                   AND sc_idx.type = 'index'
                WHERE sc_etf.parent_index_code <> ''
                GROUP BY sc_idx.industry_id, l.date
            ),
            etf_ind_with_ma AS (
                SELECT
                    code, date, etf_num, total_etf_amt,
                    AVG(total_etf_amt) OVER (
                        PARTITION BY code ORDER BY date
                        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                    ) AS total_etf_amt_ma5
                FROM etf_ind_agg
            )
            SELECT code, date, etf_num,
                   total_etf_amt, total_etf_amt_ma5
            FROM etf_ind_with_ma
            ORDER BY code, date
        """)
        print(f"    → {len(ind_rows):,} rows across "
              f"{len(set(r['code'] for r in ind_rows))} industries",
              flush=True)

        # ---- Step 5: upsert per-(date, industry_id) rows --------------
        print(f"\n[5/6] Upserting into {TABLE_INDUSTRY}...", flush=True)
        if not ind_rows:
            print("    → no data to insert.", flush=True)
        else:
            ind_data = [
                {
                    "date": r["date"],
                    "code": r["code"],
                    "etf_num": r["etf_num"],
                    "total_etf_amt": r["total_etf_amt"],
                    "total_etf_amt_ma5": r["total_etf_amt_ma5"],
                }
                for r in ind_rows
            ]
            n_ind = await bulk_upsert_async(
                conn, TABLE_INDUSTRY, ind_data,
                key_columns=["date", "code"],
                batch_size=1000,
            )
            print(f"    → upserted {n_ind:,} rows", flush=True)

        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
