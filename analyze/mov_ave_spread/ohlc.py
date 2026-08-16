"""Internal OHLC step for analyze.mov_ave_spread.

Rolling OHLC summary (today_close + open/high/low over 6 windows:
20/60/120/255/500/750 trading days) for ETF + Index + Stock. One row per
(sec_type, code, date) in analysis.mov_ave_spreads_detail_ohlc.

For each window W:
  - open_Wd: open price on the W-th trading day before `date`
  - high_Wd: max high over the W trading days ending on `date`
  - low_Wd:  min low over the W trading days ending on `date`

today_close is the close price on `date` (COALESCE(adj_close, close) for
ETFs; close for index/stock). NOT NULL.

Source: the same source DataFrame already loaded by the parent
mov_ave_spread.fetch_source_data — reuses the same DataFrame, no second
DB round-trip. The open/high/low columns are already present (fetched
from stats.*_basic_stats + stats.*_adjustment).

This module is an INTERNAL step of analyze.mov_ave_spread — it is invoked
from __main__.py after the detail + peaks_and_floors tables have been
repopulated, reusing the same DB connection + source DataFrame. It is NOT
a standalone runnable.

Incremental mode (``force=False``):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_spreads_detail_ohlc are (re)computed and upserted.
  The missing-date check is PER-sec_type.

  The OHLC columns require up to 750 prior rows per code, so the FULL
  per-code history (already in the parent DataFrame) is used and the
  result is filtered to target_dates before upsert.

Force mode (``force=True``):
  Truncate analysis.mov_ave_spreads_detail_ohlc, then recompute and
  insert all rows for the active universe.

GPU acceleration: the rolling max/min computations use the shared
``grouped_rolling_agg`` helper, which routes to cuDF when the row count
exceeds the rolling_mean breakeven (~100K rows conservative). The shift
computation for open_Wd uses the shared ``grouped_shift`` helper.
"""
from __future__ import annotations

import time
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    find_missing_analysis_dates,
)
from _common.df_utils import grouped_rolling_agg, grouped_shift
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import (
    OHLC_ANALYSIS_NAME,
    OHLC_COLUMNS,
    OHLC_DESCRIPTION,
    OHLC_TABLE,
    OHLC_WINDOWS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def compute_ohlc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add today_close + rolling open/high/low columns for all windows.

    Args:
        df: Source DataFrame sorted by [sec_type, code, date] with columns
            [sec_type, code, date, price, open, high, low]. Must be the
            FULL per-code history.

    Returns:
        The same df with OHLC columns added in place.
    """
    if df.empty:
        for col in OHLC_COLUMNS:
            df[col] = pd.Series(dtype="float64")
        return df

    grp_keys = ["sec_type", "code"]

    # Ensure numeric conversion of price / open / high / low.
    # Decimal('NaN') from asyncpg is converted to np.nan — these NaN
    # values represent missing data (e.g. holidays, data-gaps) and are
    # handled by the rolling min_periods tolerance below.
    for col in ("price", "open", "high", "low"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # today_close = price (close). The price column already holds the
    # correct close: COALESCE(adj_close, close) for ETFs; close for
    # index/stock.
    df["today_close"] = df["price"]

    for w in OHLC_WINDOWS:
        # open_Wd: open price from W trading days ago (shift by W).
        open_col = f"open_{w}d"
        grouped_shift(
            df, grp_keys, "open",
            out_names=open_col, periods=w, sort=False,
        )

        # high_Wd: rolling max of high over W days.
        # min_periods=1 skips NaN values (Decimal('NaN') from holidays /
        # data-gaps) so the rolling max is computed from all available
        # valid days in the window.
        high_col = f"high_{w}d"
        df[high_col] = grouped_rolling_agg(
            df, grp_keys, "high", window=w,
            min_periods=1, agg="max", sort=False,
        )

        # low_Wd: rolling min of low over W days.
        low_col = f"low_{w}d"
        df[low_col] = grouped_rolling_agg(
            df, grp_keys, "low", window=w,
            min_periods=1, agg="min", sort=False,
        )

    return df


def sanitize_ohlc_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_spreads_detail_ohlc columns and sanitize
    for asyncpg bulk upsert (NaN/inf -> None + to_dict).

    Operates on a DataFrame already carrying the OHLC columns (typically
    filtered to target_dates).
    """
    if df.empty:
        return []

    out_cols = ["sec_type", "code", "date"] + list(OHLC_COLUMNS)
    out = df[out_cols].copy()

    non_numeric = ("sec_type", "code", "date")
    numeric_cols = [c for c in out_cols if c not in non_numeric]

    # Overflow guard: today_close and open/high/low can be large for
    # high-priced indices (e.g. SSE Composite ~3000). NUMERIC(18,6)
    # has a much larger range than NUMERIC(10,6) — |value| < 10^12
    # after rounding to 6dp — so overflow is unlikely. Still guard
    # as a safety net.
    nulled = {}
    for c in numeric_cols:
        before = int(out[c].isna().sum())
        out[c] = _null_if_overflow(out[c])
        n = int(out[c].isna().sum()) - before
        if n > 0:
            nulled[c] = n
    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        print(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled)} column(s): {per}", flush=True)

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


def _null_if_overflow(series):
    """Null values whose |abs| >= 10^12 (would overflow NUMERIC(18,6))."""
    s = pd.to_numeric(series, errors="coerce")
    mask = s.isna() | ~np.isfinite(s) | (s.abs().round(6) >= 1e12)
    return s.where(~mask)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_ohlc(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
) -> None:
    """Run the OHLC-detail pipeline against the source data already
    loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the
    ``price``, ``open``, ``high``, ``low`` columns are reused — no
    second DB fetch). The DataFrame must contain the FULL per-code
    history (not filtered to target_dates) so rolling computations have
    enough lookback rows (up to 750).

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_spreads_detail_ohlc against source identity
         tables. In force mode, truncate the table instead.
      2. Compute today_close + rolling OHLC columns over the FULL
         per-code history, then filter to target_dates.
      3. Upsert into analysis.mov_ave_spreads_detail_ohlc (chunked).
      4. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          price, open, high, low]. Must be the FULL per-code history.
      force: when True, truncate analysis.mov_ave_spreads_detail_ohlc
             first and recompute all rows.
      pool: optional connection pool for parallel upsert chunks.
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory). When
                None, infers sec_types from the DataFrame.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_SPREAD_DETAIL_OHLC (internal step of mov_ave_spread)",
          flush=True)
    print("=" * 78, flush=True)

    # Select only the columns OHLC needs — the parent DataFrame carries
    # many extra columns (MAs, slopes, stds, trading_amt_*) that are
    # irrelevant here.
    needed_cols = ["sec_type", "code", "date", "price", "open", "high", "low"]
    available = [c for c in needed_cols if c in df.columns]
    ohlc_df = df[available].copy()

    if ohlc_df.empty:
        print("    -> no source data; skipping OHLC step.", flush=True)
        return

    # Use the sec_type passed by the parent (per-sec_type loop) or infer
    # from the DataFrame for backward compatibility.
    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(ohlc_df["sec_type"].unique()))

    # ---- Step 0: determine target dates (per-sec_type) --------------
    if force:
        print("    mode: FORCE (full recompute)", flush=True)
        print("\n[o0/3] Force mode: truncating mov_ave_spreads_detail_ohlc...",
              flush=True)
        await truncate_table_async(conn, OHLC_TABLE)
        target_dates_union: Optional[Set] = None
        print("    -> truncated; will recompute all rows", flush=True)
    else:
        print("    mode: incremental (missing dates only)", flush=True)
        print("\n[o0/3] Detecting missing dates PER-sec_type "
              "(etf_identity vs detail_ohlc[etf], etc.)...",
              flush=True)
        target_dates_per_st: dict = {}
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, OHLC_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=st,
            )
            target_dates_per_st[st] = td_st
            print(f"    -> {st}: {len(td_st)} missing dates", flush=True)
        # Union across sec_types — a date is "to do" if ANY sec_type
        # is missing it.
        target_dates_union = set()
        for s in target_dates_per_st.values():
            target_dates_union |= s
        print(f"    -> union across sec_types: "
              f"{len(target_dates_union)} dates to (re)compute",
              flush=True)
        if not target_dates_union:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return

    # ---- Step 1: compute OHLC columns over full history, then filter --
    print("\n[o1/3] Computing today_close + rolling OHLC columns per "
          "(sec_type, code, date) over full history...", flush=True)
    ohlc_df = compute_ohlc_columns(ohlc_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(ohlc_df)
        ohlc_df = ohlc_df[ohlc_df["date"].isin(target_dates_union)].reset_index(drop=True)
        print(f"    -> incremental filter: {len(ohlc_df):,} of {n_before:,} "
              f"rows are in target_dates_union", flush=True)

    if ohlc_df.empty:
        print("    -> no rows to upsert; skipping OHLC upsert.", flush=True)
        return

    # ---- Step 2: build + insert (chunked by date) -------------------
    print(f"\n[o2/3] Building + inserting {len(ohlc_df):,} "
          f"mov_ave_spreads_detail_ohlc rows in date-bounded chunks "
          f"({'COPY' if force else 'upsert'} per chunk)...", flush=True)
    n = await build_and_insert_chunked(
        conn, pool, ohlc_df,
        sanitize_ohlc_rows,
        table_name=OHLC_TABLE,
        key_columns=["sec_type", "code", "date"],
        force=force,
        sec_types=sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_spreads_detail_ohlc",
    )
    del ohlc_df
    print(f"    -> inserted {n:,} rows", flush=True)

    # ---- Step 3: register in analysis_identity ----------------------
    print(f"\n[o3/3] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=OHLC_ANALYSIS_NAME,
        detail_name="mov_ave_spreads_detail_ohlc",
        description=OHLC_DESCRIPTION,
    )

    print(f"\n  mov_ave_spreads_detail_ohlc wall time: "
          f"{time.time() - t0:.1f}s", flush=True)