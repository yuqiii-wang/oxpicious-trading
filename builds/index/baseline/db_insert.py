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

import numpy as np
import pandas as pd

from _common.build_commons import copy_or_upsert_split_async


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

    # Vectorized row construction for 4 tables
    _src = daily_df.copy()
    _src["code"] = _src["code"].astype(str)
    # --- Helper: vectorized NaN→None ---
    def _to_db_series(s: pd.Series) -> pd.Series:
        return s.where(s.notna(), None)
    # --- identity_rows ---
    _src["name"] = _src["indexName"].where(_src["indexName"].notna(), "").astype(str)
    identity_rows = _src[["date", "code", "name"]].to_dict(orient="records")
    # --- basic_stats_rows ---
    _basic_cols = ["open", "high", "low", "close", "trading_shares", "trading_amount",
                   "change", "changePct"]
    for _c in _basic_cols:
        if _c in _src.columns:
            _src[_c] = _to_db_series(_src[_c])
    if "is_close_estimated" in _src.columns:
        _src["is_close_estimated"] = _src["is_close_estimated"].fillna(False).astype(bool)
    else:
        _src["is_close_estimated"] = False
    basic_cols_out = ["date", "code"] + _basic_cols + ["is_close_estimated"]
    basic_stats_rows = _src[[c for c in basic_cols_out if c in _src.columns]].to_dict(orient="records")
    # --- valuation_rows ---
    _val_cols = ["pe", "consNumber"]
    for _c in _val_cols:
        if _c in _src.columns:
            _src[_c] = _to_db_series(_src[_c])
    val_cols_out = ["date", "code"] + [c for c in _val_cols if c in _src.columns]
    valuation_rows = _src[val_cols_out].to_dict(orient="records")
    # --- tech_stats_rows ---
    _tech_cols = ["ma5", "ma5_ratio", "ma20", "ma60", "ma120", "ma255",
                  "ema6", "ema10", "ema20", "ema60", "ema120", "ema255"]
    tech_cols_out = ["date", "code"] + [c for c in _tech_cols if c in _src.columns]
    tech_stats_rows = _src[tech_cols_out].to_dict(orient="records")

    pk = ["date", "code"]
    for tbl, rows in [
        ("stats.index_identity",    identity_rows),
        ("stats.index_basic_stats", basic_stats_rows),
        ("stats.index_valuation",   valuation_rows),
        ("stats.index_tech_stats",  tech_stats_rows),
    ]:
        if rows:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, tbl, rows, pk
            )
            total = n_copied + n_upserted
            if verbose:
                via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                      f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                      "upsert"
                print(f"    [DB] Inserted {total:,} rows into {tbl} via {via}", flush=True)

    return len(identity_rows)
