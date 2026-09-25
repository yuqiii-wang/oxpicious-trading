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
(date, code) primary key; each is written via ``_copy_or_upsert_frame_split``
— the frame-flavored ``copy_or_upsert_split_async`` contract where rows
after the table's MAX(date) take the CSV COPY fast path
(``csv_copy_from_frame_async``: whole-column text rendering, ~10x less
client CPU than asyncpg's per-value binary ``copy_records_to_table`` on
the 885k-row force rebuild) and rows at or before MAX(date) fall back to
``bulk_upsert_async`` for same-day refresh idempotency.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.build_commons import bulk_upsert_async, get_max_table_date_async
from _common.df_utils import safe_columns
from _common.db_commons import csv_copy_frame_chunked_async
from builds._commons.row_emission import dates_as_date_list, records_from_frame

import logging
logger = logging.getLogger(__name__)


async def _copy_or_upsert_frame_split(conn, table_name: str, df: pd.DataFrame,
                                      key_columns: list[str]) -> tuple[int, int]:
    """MAX(date)-split write for a (date, …)-keyed DataFrame.

    Same routing contract as ``copy_or_upsert_split_async``: rows strictly
    AFTER the table's MAX(date) cannot conflict on the PK and take the
    COPY fast path — via ``csv_copy_from_frame_async`` (CSV text COPY)
    instead of ``copy_insert_async`` (per-value binary record assembly).
    Rows at or before MAX(date) may conflict and go through
    ``bulk_upsert_async`` as row dicts (same-day refreshes; usually
    small). Empty table (force mode truncates first) → whole frame via
    CSV COPY.

    Returns (n_copied, n_upserted).
    """
    if df is None or len(df) == 0:
        return (0, 0)

    max_day = await get_max_table_date_async(conn, table_name)
    if max_day is None:
        # Whole-table rebuild (force): chunk by the partition key — a
        # single CSV COPY retains all its WAL behind one commit (the
        # docstring's own 885k-row rebuild is one COPY too many).
        return (await csv_copy_frame_chunked_async(
            conn, table_name, df, label=table_name.split(".")[-1],
        ), 0)

    # np.datetime64 scalar compares cudf-natively; a pd.Timestamp proxy
    # scalar costs a fallback pair (_Unusable transform + slow compare).
    is_new = df["date"] > np.datetime64(max_day)
    n_copied = 0
    if is_new.any():
        # A long source/DB gap (lag repair) can push this tail well past
        # the commit-chunk target — keep it chunked too.
        n_copied = await csv_copy_frame_chunked_async(
            conn, table_name, df.loc[is_new],
            label=table_name.split(".")[-1],
        )

    n_upserted = 0
    stale = ~is_new
    if stale.any():
        up_df = df.loc[stale]
        val_cols = [c for c in up_df.columns if c not in key_columns]
        date_vals = dates_as_date_list(up_df["date"])
        code_vals = np.asarray(up_df["code"]).tolist()
        recs = records_from_frame(up_df, val_cols)
        rows = [{"date": d, "code": c, **r}
                for d, c, r in zip(date_vals, code_vals, recs)]
        n_upserted = await bulk_upsert_async(conn, table_name, rows,
                                             key_columns)

    return (n_copied, n_upserted)


async def insert_daily_to_db(conn, daily_df, verbose=True):
    """Insert daily data into database tables (async).

    Returns the number of identity rows inserted (== the row count of all
    four tables, since they share (date, code) PKs).
    """
    if daily_df is None or len(daily_df) == 0:
        return 0

    # Vectorized frame preparation for 4 tables (no to_dict(orient="records")
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

    # --- basic_stats columns ---
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
    # --- valuation columns ---
    _val_cols = ["pe", "cons_number"]
    for _c in _val_cols:
        if _c in src_cols:
            _src[_c] = _to_db_series(_src[_c])
    # trading_amt_per_pct_change is absent when no source CSV carried
    # trading_amount (build_daily skips the metric then) — conditional emit.
    _tech_cols = ["ma5", "ma5_ratio", "ma20", "ma60", "ma120", "ma255",
                  "ema6", "ema10", "ema20", "ema60", "ema120", "ema255",
                  "trading_amt_per_pct_change"]

    pk = ["date", "code"]
    # Per-table column lists in DB column order (date, code first).
    table_frames = [
        ("stats.index_identity",    ["name"]),
        ("stats.index_basic_stats",
         [c for c in _basic_cols if c in src_cols]
         + ["is_close_estimated", "is_ohl_estimated"]),
        ("stats.index_valuation",   [_c for _c in _val_cols if _c in src_cols]),
        ("stats.index_tech_stats",  [_c for _c in _tech_cols if _c in src_cols]),
    ]
    n_identity = 0
    for tbl, val_cols in table_frames:
        df = _src[pk + val_cols]
        if len(df) == 0:
            continue
        n_copied, n_upserted = await _copy_or_upsert_frame_split(
            conn, tbl, df, pk
        )
        total = n_copied + n_upserted
        if verbose:
            via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                  f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                  "upsert"
            logger.info(f"    [DB] Inserted {total:,} rows into {tbl} via {via}")
        if tbl == "stats.index_identity":
            n_identity = total

    return n_identity
