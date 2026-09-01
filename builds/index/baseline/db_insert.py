"""Database insertion for the index baseline build.

Inserts the filtered daily DataFrame (already reduced to missing (date, code)
pairs by build_daily) into the four daily index_* tables:

  • stats.index_identity    (date, code, name)
  • stats.index_basic_stats (date, code, OHLCV, trading_shares, trading_amount,
                             change, change_pct, is_close_estimated,
                             is_ohl_estimated)
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
from _common.df_utils import safe_columns
from builds._commons.row_emission import dates_as_date_list, records_from_frame


async def insert_daily_to_db(conn, daily_df, verbose=True):
    """Insert daily data into database tables (async).

    Returns the number of identity rows inserted (== the row count of all
    four tables, since they share (date, code) PKs).
    """
    if daily_df is None or len(daily_df) == 0:
        return 0

    # Vectorized row construction for 4 tables (no to_dict(orient="records")
    # / iterrows — under cudf.pandas each element extraction is one
    # slow-path fallback per row)
    _src = daily_df.copy()
    _src["code"] = _src["code"].astype(str)
    # --- Rename camelCase → snake_case for DB column alignment ---
    _src = _src.rename(columns={
        "changePct": "change_pct",
        "consNumber": "cons_number",
        "indexName": "name",
    })
    src_cols = safe_columns(_src)
    # Ensure name is never NaN/None (DB column is NOT NULL)
    _src["name"] = _src["name"].where(_src["name"].notna(), "").astype(str)
    # --- Helper: vectorized NaN→None ---
    def _to_db_series(s: pd.Series) -> pd.Series:
        return s.where(s.notna(), None)

    # date/code emitted via ONE numpy transfer each (datetime.date objects)
    date_vals = dates_as_date_list(_src["date"])
    code_vals = np.asarray(_src["code"]).tolist()

    def _emit(cols_without_pk: list) -> list:
        recs = records_from_frame(_src, cols_without_pk)
        return [{"date": d, "code": c, **r} for d, c, r in zip(date_vals, code_vals, recs)]

    # --- basic_stats_rows ---
    _basic_cols = ["open", "high", "low", "close", "trading_shares", "trading_amount",
                   "change", "change_pct"]
    for _c in _basic_cols:
        if _c in src_cols:
            _src[_c] = _to_db_series(_src[_c])
    if "is_close_estimated" in src_cols:
        _src["is_close_estimated"] = _src["is_close_estimated"].fillna(False).astype(bool)
    else:
        _src["is_close_estimated"] = False
    if "is_ohl_estimated" in src_cols:
        _src["is_ohl_estimated"] = _src["is_ohl_estimated"].fillna(False).astype(bool)
    else:
        _src["is_ohl_estimated"] = False
    # --- valuation_rows ---
    _val_cols = ["pe", "cons_number"]
    for _c in _val_cols:
        if _c in src_cols:
            _src[_c] = _to_db_series(_src[_c])

    identity_rows = _emit(["name"])
    basic_stats_rows = _emit(
        [c for c in _basic_cols if c in src_cols]
        + ["is_close_estimated", "is_ohl_estimated"])
    valuation_rows = _emit([_c for _c in _val_cols if _c in src_cols])
    tech_stats_rows = _emit(["ma5", "ma5_ratio", "ma20", "ma60", "ma120", "ma255",
                             "ema6", "ema10", "ema20", "ema60", "ema120", "ema255"])

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
