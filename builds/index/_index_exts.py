"""build_index_exts step — populates stats.index_exts + stats.etf_trading_amt.

Phase 3 step of builds.index (also hosted here: _exchange_trading_amt,
_sec_similars) so no single giant orchestrator file.

stats.index_exts (per-(date, index_code) extension metrics):
  etf_num           = COUNT(DISTINCT etf_liquidity_margin.code) across ALL ETFs
                      whose stats.sec_classification.parent_index_code = this
                      index code on this date.
  total_etf_trading_amount     = Σ etf_liquidity_margin.trading_amount (yuan)
                      across the same ETF universe. NULL when no ETF tracks
                      the index. Consumed by builds.cross_stats
                      as the index's ETF-market trading volume.
  total_etf_trading_amount_ma5 = 5-trading-day moving average of
                      total_etf_trading_amount (AVG over the trailing 5 rows
                      per code, ordered by date).
  stock_num         = COUNT(DISTINCT stock_code) from the latest
                      stats.sec_composition snapshot (source_type='index')
                      with snapshot_date <= this date. Carries forward until
                      the next snapshot. NULL when the index has no
                      composition snapshot.

stats.etf_trading_amt (per-(date, industry_id) aggregate ETF turnover):
  Same source (etf_liquidity_margin) but grouped by the linked parent index's
  industry_id. PK (date, code) where `code` is the industry_id (NOT an index
  code).

Incremental mode (default): only dates present in stats.etf_liquidity_margin
but missing from stats.index_exts are (re)computed and upserted. The MA5
window function is computed over the FULL per-code history (CTE reads all
dates for correctness), but only target-date rows survive to the upsert.
The stock_num backfill UPDATE is also filtered to target dates.

--date mode (forced_date set): the missing-date skip is bypassed — the
forced date is recomputed even when already present (existing rows are
refreshed through the upsert write path; no truncation, no deletes). The
forced date must exist in stats.etf_liquidity_margin, else the run exits(1)
(forced_date_scope).

Force mode (force=True): truncate both tables first, then full recompute.
"""
from typing import Optional, Set

import datetime

import pandas as pd

from _common.build_commons import (
    copy_or_upsert_split_async,
    truncate_table_async,
    find_missing_dates,
    forced_date_scope,
    rec_col,
    rec_cols,
)
from builds._commons.row_emission import records_from_frame

TABLE_INDEX = "stats.index_exts"
TABLE_INDUSTRY = "stats.etf_trading_amt"


async def build_index_exts(conn, force: bool = False,
                           forced_date: Optional[datetime.date] = None) -> None:
    """Populate stats.index_exts + stats.etf_trading_amt.

    Incremental when force=False (missing dates only); full recompute when
    force=True (truncate first). With forced_date (--date mode), only that
    date is recomputed — the missing-date skip is bypassed (existing rows
    are refreshed via the upsert path) and no truncation happens.
    """
    # ---- Step 1: detect missing dates or truncate ----------------
    if force:
        print(f"\n[INDEX_EXTS] Force mode: truncating {TABLE_INDEX} and "
              f"{TABLE_INDUSTRY}...", flush=True)
        await truncate_table_async(conn, TABLE_INDEX)
        await truncate_table_async(conn, TABLE_INDUSTRY)
        target_dates: Optional[Set[datetime.date]] = None
    else:
        print(f"\n[INDEX_EXTS] Detecting missing dates...", flush=True)
        source_rows = await conn.fetch(
            "SELECT DISTINCT date FROM stats.etf_liquidity_margin"
        )
        source_dates = {r["date"] for r in source_rows if r["date"]}
        if forced_date is not None:
            # --date mode: bypass the missing-date skip — the forced date is
            # ALWAYS recomputed (existing rows refreshed via the upsert
            # path); exits(1) when the date has no source data.
            target_dates = forced_date_scope(
                source_dates, forced_date,
                source_label="stats.etf_liquidity_margin dates",
            )
            print(f"    -> [DATE MODE] forcing recompute of {forced_date} "
                  f"(missing-date skip bypassed)", flush=True)
        else:
            target_dates = await find_missing_dates(
                conn, TABLE_INDEX, source_dates
            )
            print(f"    -> {len(target_dates)} dates missing from "
                  f"{TABLE_INDEX} (out of {len(source_dates)} source dates)",
                  flush=True)
            if not target_dates:
                print("    -> DB is up to date; nothing to do.", flush=True)
                return

    # ---- Step 2: compute per-(date, index) aggregation ------------
    # etf_num/total_etf_trading_amount are aggregated from etf_liquidity_margin
    # JOIN sec_classification (parent_index_code). total_etf_trading_amount_ma5
    # is a 5-row trailing AVG (PARTITION BY code ORDER BY date) computed
    # over the aggregated series. SUM ignores NULL trading_amount rows, so
    # total_etf_trading_amount is NULL only when no tracking ETF has any
    # trading_amount on that date. The final JOIN to index_identity
    # satisfies the FK constraint — only rows where (date, code) exists in
    # index_identity are inserted.
    #
    # MA5 correctness: the CTE computes over ALL dates (the window function
    # needs full per-code history). In incremental mode, only target-date
    # rows are returned via a WHERE filter on the final SELECT, so only
    # those are upserted. Existing rows keep their already-correct MA5.
    print("\n[INDEX_EXTS] Computing etf_num + total_etf_trading_amount + ma5 per "
          "(date, index_code)...", flush=True)
    date_filter = (
        "WHERE ewm.date = ANY($1::date[])"
        if target_dates is not None else ""
    )
    sql_index = f"""
        WITH etf_agg AS (
            SELECT
                sc.parent_index_code AS code,
                l.date,
                COUNT(DISTINCT l.code) AS etf_num,
                SUM(l.trading_amount) AS total_etf_trading_amount
            FROM stats.etf_liquidity_margin l
            JOIN stats.sec_classification sc
                ON sc.code = l.code AND sc.type = 'etf'
            WHERE sc.parent_index_code <> ''
              AND sc.is_primary_exchange = TRUE
            GROUP BY sc.parent_index_code, l.date
        ),
        etf_with_ma AS (
            SELECT
                code, date, etf_num, total_etf_trading_amount,
                AVG(total_etf_trading_amount) OVER (
                    PARTITION BY code ORDER BY date
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                ) AS total_etf_trading_amount_ma5
            FROM etf_agg
        )
        SELECT ewm.code, ewm.date, ewm.etf_num,
               ewm.total_etf_trading_amount, ewm.total_etf_trading_amount_ma5
        FROM etf_with_ma ewm
        JOIN stats.index_identity ii
            ON ii.date = ewm.date AND ii.code = ewm.code
        {date_filter}
        ORDER BY ewm.code, ewm.date
    """
    if target_dates is not None:
        rows = await conn.fetch(sql_index, sorted(target_dates))
    else:
        rows = await conn.fetch(sql_index)
    print(f"    -> {len(rows):,} rows across "
          f"{len(set(rec_col(rows, 'code')))} indices "
          f"(only indices with linked ETFs — joined via sec_classification)",
          flush=True)

    # ---- Step 3: upsert per-(date, index) rows --------------------
    print(f"\n[INDEX_EXTS] Upserting into {TABLE_INDEX}...", flush=True)
    if not rows:
        print("    -> no data to insert.", flush=True)
    else:
        # Whole-column extraction + column-major row emission
        df = pd.DataFrame(rec_cols(rows))
        data = records_from_frame(
            df, ["date", "code", "etf_num",
                 "total_etf_trading_amount", "total_etf_trading_amount_ma5"],
        )
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE_INDEX, data, key_columns=["date", "code"],
        )
        total = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        print(f"    -> upserted {total:,} rows via {via}", flush=True)

    # ---- Step 3b: backfill stock_num from sec_composition ---------
    # For every (date, code) row in index_exts, find the latest
    # stats.sec_composition snapshot (source_type='index') with
    # snapshot_date <= date and COUNT(DISTINCT stock_code). Carries
    # forward until the next snapshot. NULL when the index has no
    # composition snapshot at all (e.g. cross-market H-prefixed
    # indices without a CSI closeweight pull). Done as a UPDATE FROM
    # LATERAL join over the just-upserted rows.
    #
    # In incremental mode, filter to target dates so existing rows are
    # not touched (their stock_num is already correct).
    print(f"\n[INDEX_EXTS] Backfilling stock_num from sec_composition "
          f"(latest snapshot <= date per code)...", flush=True)
    date_filter_update = (
        "AND ie.date = ANY($1::date[])"
        if target_dates is not None else ""
    )
    sql_stock_num = f"""
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
          {date_filter_update}
        RETURNING 1
    """
    if target_dates is not None:
        stock_num_rows = await conn.fetch(sql_stock_num, sorted(target_dates))
    else:
        stock_num_rows = await conn.fetch(sql_stock_num)
    print(f"    -> updated stock_num on {len(stock_num_rows):,} "
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
    #
    # Same MA5 pattern: full CTE for window correctness, output filtered
    # to target dates in incremental mode.
    print("\n[INDEX_EXTS] Computing etf_num + total_etf_trading_amount + ma5 per "
          "(date, industry_id)...", flush=True)
    date_filter_ind = (
        "WHERE eim.date = ANY($1::date[])"
        if target_dates is not None else ""
    )
    sql_ind = f"""
        WITH etf_ind_agg AS (
            SELECT
                sc_idx.industry_id AS code,
                l.date,
                COUNT(DISTINCT l.code) AS etf_num,
                SUM(l.trading_amount) AS total_etf_trading_amount
            FROM stats.etf_liquidity_margin l
            JOIN stats.sec_classification sc_etf
                ON sc_etf.code = l.code AND sc_etf.type = 'etf'
            JOIN stats.sec_classification sc_idx
                ON sc_idx.code = sc_etf.parent_index_code
               AND sc_idx.type = 'index'
            WHERE sc_etf.parent_index_code <> ''
              AND sc_etf.is_primary_exchange = TRUE
            GROUP BY sc_idx.industry_id, l.date
        ),
        etf_ind_with_ma AS (
            SELECT
                code, date, etf_num, total_etf_trading_amount,
                AVG(total_etf_trading_amount) OVER (
                    PARTITION BY code ORDER BY date
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                ) AS total_etf_trading_amount_ma5
            FROM etf_ind_agg
        )
        SELECT eim.code, eim.date, eim.etf_num,
               eim.total_etf_trading_amount, eim.total_etf_trading_amount_ma5
        FROM etf_ind_with_ma eim
        {date_filter_ind}
        ORDER BY eim.code, eim.date
    """
    if target_dates is not None:
        ind_rows = await conn.fetch(sql_ind, sorted(target_dates))
    else:
        ind_rows = await conn.fetch(sql_ind)
    print(f"    -> {len(ind_rows):,} rows across "
          f"{len(set(r['code'] for r in ind_rows))} industries",
          flush=True)

    # ---- Step 5: upsert per-(date, industry_id) rows --------------
    print(f"\n[INDEX_EXTS] Upserting into {TABLE_INDUSTRY}...", flush=True)
    if not ind_rows:
        print("    -> no data to insert.", flush=True)
    else:
        ind_data = [
            {
                "date": r["date"],
                "code": r["code"],
                "etf_num": r["etf_num"],
                "total_etf_trading_amount": r["total_etf_trading_amount"],
                "total_etf_trading_amount_ma5": r["total_etf_trading_amount_ma5"],
            }
            for r in ind_rows
        ]
        n_copied2, n_upserted2 = await copy_or_upsert_split_async(
            conn, TABLE_INDUSTRY, ind_data, key_columns=["date", "code"],
        )
        total2 = n_copied2 + n_upserted2
        via2 = "COPY" if n_copied2 > 0 and n_upserted2 == 0 else \
               f"COPY+upsert ({n_copied2}+{n_upserted2})" if n_copied2 > 0 else \
               "upsert"
        print(f"    -> upserted {total2:,} rows via {via2}", flush=True)
