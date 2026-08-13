"""Database insertion for the index baseline build.

Inserts the filtered daily DataFrame (already reduced to missing (date, code)
pairs by build_daily) into the four daily index_* tables:

  • stats.index_identity    (date, code, name)
  • stats.index_basic_stats (date, code, OHLCV, trading_shares, trading_amount,
                             change, change_pct, is_close_estimated)
  • stats.index_valuation   (date, code, pe, cons_number)
  • stats.index_tech_stats  (date, code, MAs)

Caller has already filtered daily_df to missing (date, code) pairs, so no
further existing_keys check is needed here. All four tables share the
(date, code) primary key and use bulk_upsert_async for idempotency.
"""
from __future__ import annotations

import pandas as pd

from _common.build_commons import bulk_upsert_async


async def insert_daily_to_db(conn, daily_df, verbose=True):
    """Insert daily data into database tables (async).

    Returns the number of identity rows inserted (== the row count of all
    four tables, since they share (date, code) PKs).
    """
    if daily_df is None or len(daily_df) == 0:
        return 0

    identity_rows = []
    basic_stats_rows = []
    valuation_rows = []
    tech_stats_rows = []

    for _, row in daily_df.iterrows():
        identity_rows.append({
            "date": row["date"],
            "code": row["code"],
            "name": str(row.get("indexName", "")) if pd.notna(row.get("indexName")) else "",
        })
        basic_stats_rows.append({
            "date": row["date"],
            "code": row["code"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "trading_shares": row["trading_shares"],
            "trading_amount": row["trading_amount"],
            "change": row["change"],
            "change_pct": row["changePct"],
            "is_close_estimated": bool(row.get("is_close_estimated", False)),
        })
        valuation_rows.append({
            "date": row["date"],
            "code": row["code"],
            "pe": row["pe"],
            "cons_number": row["consNumber"],
        })
        tech_stats_rows.append({
            "date": row["date"],
            "code": row["code"],
            "ma5": row["ma5"],
            "ma5_ratio": row["ma5_ratio"],
            "ma20": row["ma20"],
            "ma60": row["ma60"],
            "ma120": row["ma120"],
            "ma255": row["ma255"],
        })

    pk = ["date", "code"]
    for tbl, rows in [
        ("stats.index_identity",    identity_rows),
        ("stats.index_basic_stats", basic_stats_rows),
        ("stats.index_valuation",   valuation_rows),
        ("stats.index_tech_stats",  tech_stats_rows),
    ]:
        if rows:
            inserted = await bulk_upsert_async(conn, tbl, rows, pk)
            if verbose:
                print(f"    [DB] Inserted {inserted:,} rows into {tbl}", flush=True)

    return len(identity_rows)
