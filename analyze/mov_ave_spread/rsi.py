"""Internal RSI step for analyze.mov_ave_spread.

Wilder RSI (6/10/14/20/60/120/255/500 days) + short-term price gaps
(2/3 day returns) + last-extreme gap/days columns for ETF + Index + Stock.
One row per (sec_type, code, date) in analysis.mov_ave_rsi.

RSI uses Wilder's smoothing (EWM alpha=1/N, adjust=False, min_periods=N).
gap_Ndays = (price[t] - price[t-N]) / price[t-N] (N-day price return).

gap_since_last_extreme = (price[t] - extreme_price) / extreme_price, where
extreme_price is the price at the most recent local turning point (high/
low) detected by price_slope sign change. Sign indicates the type of the
last extreme: positive = last extreme was a MIN, negative = last extreme
was a MAX.

days_since_last_extreme = trading days since the most recent local turning
point. NULL when no preceding turning point exists.

Source prices: ETF = COALESCE(etf_adjustment.adj_close,
etf_basic_stats.close); index = index_basic_stats.close; stock =
stock_basic_stats.close (same price convention as the parent
mov_ave_spread analysis — the price + price_slope columns are reused from
the parent's already-fetched source DataFrame, avoiding a second DB
round-trip).

This module is an INTERNAL step of analyze.mov_ave_spread — it is invoked
from __main__.py after the detail table has been
repopulated, reusing the same DB connection + source DataFrame. It is NOT
a standalone runnable.

Incremental mode (``force=False``):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_rsi are (re)computed and upserted. The missing-date
  check is PER-sec_type (PK is (sec_type, code, date) — a date populated
  for ETF must not mask the same date being missing for index/stock).

  RSI is recursive, so the FULL per-code history (already in the parent's
  DataFrame) is used and the RSI computed over it, then filtered to
  target_dates before upsert.

Force mode (``force=True``):
  Truncate analysis.mov_ave_rsi, then recompute and insert all rows for
  the active universe.
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
from _common.df_utils import grouped_diff, grouped_shift
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import SEC_TYPES, SEC_TYPE_IDENTITY_TABLE


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

RSI_TABLE = "analysis.mov_ave_rsi"
RSI_ANALYSIS_NAME = "mov_ave_rsi"

RSI_DESCRIPTION = (
    "Wilder Relative Strength Index (RSI) + short-term price-gap analysis "
    "+ last-extreme gap/days (ETF + Index + Stock). For each security and "
    "business date, computes Wilder RSI for 8 windows (rsi_6days / "
    "rsi_10days / rsi_14days / rsi_20days / rsi_60days / rsi_120days / "
    "rsi_255days / rsi_500days) using Wilder's exponential smoothing "
    "(EWM alpha=1/N, adjust=False, min_periods=N; RSI = "
    "100 - 100/(1+RS) where RS = avg_gain/avg_loss over the per-code "
    "N-day gain/loss series; RSI=100 on pure uptrend, 0 on pure "
    "downtrend, NULL when flat), plus 2 short-term price-gap columns "
    "(gap_2days / gap_3days) defined as the N-day price return "
    "(price[t]-price[t-N])/price[t-N], plus 2 last-extreme columns "
    "(gap_since_last_extreme / days_since_last_extreme) computed from the "
    "most recent local turning point (high/low) detected by price_slope "
    "sign change. gap_since_last_extreme = "
    "(price[t]-extreme_price)/extreme_price; sign indicates the type of "
    "the last extreme (positive = MIN, negative = MAX). "
    "days_since_last_extreme = trading days since the last turning point. "
    "The sec_type column discriminates the source universe ('etf' | "
    "'index' | 'stock'); ETF price uses COALESCE(etf_adjustment.adj_close, "
    "etf_basic_stats.close), index uses index_basic_stats.close, stock "
    "uses stock_basic_stats.close."
)

# RSI windows (Wilder smoothing). 14 is the classic Wilder default; 6/10/20
# are common shorter/longer variants; 60 (~3 trading months), 120 (~half
# trading year), 255 (~1 trading year, matches MA255), and 500 (~2 trading
# years) are progressively longer-term momentum windows that smooth out
# short-term noise — useful for trend-confirmation alongside the shorter
# windows. Note: 500-day RSI will be NULL for recent IPOs / ETFs with
# < 500 rows of history.
RSI_WINDOWS = (6, 10, 14, 20, 60, 120, 255, 500)

# N-day price-return windows for the gap columns.
GAP_WINDOWS = (2, 3)

# NUMERIC(10,6) overflow guard (|value| must be < 10^4 after rounding to 6dp).
# RSI is bounded 0..100; gap columns are ratios nullified when the
# denominator is near-zero, so overflow is unlikely — the guard is a safety
# net mirroring the parent mov_ave_spread.
NUMERIC_MAX_ABS = 10000.0


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def compute_rsi_and_gaps(
    df: pd.DataFrame,
    rsi_windows: tuple = RSI_WINDOWS,
    gap_windows: tuple = GAP_WINDOWS,
) -> pd.DataFrame:
    """Add rsi_{W}days (Wilder) + gap_{W}days columns to df, per
    (sec_type, code) ordered by date.

    Wilder RSI: EWM alpha=1/W, adjust=False, min_periods=W. Range 0..100.
    gap_Wdays: (price[t] - price[t-W]) / price[t-W].

    Rows with NULL price produce NULL RSI and gap values (gain/loss are
    NaN, and ``ignore_na=True`` in the EWM skips them without propagating
    NaN forward — RSI carries the last non-null value through gaps).

    Must be called on the FULL per-code history (Wilder smoothing is
    recursive); the caller filters to target_dates afterwards.

    Returns df (sorted by sec_type, code, date) with the new columns added.

    GPU acceleration: the diff (delta = price[t]-price[t-1]) and the
    shifts (price[t-W] for gap_Ndays) are routed to cuDF via the shared
    ``grouped_diff`` / ``grouped_shift`` helpers when the row count
    exceeds the ``groupby_diff`` / ``groupby_shift`` breakeven
    (~320K rows conservative). Each helper transfers only the minimal
    column subset (group_keys + price) to VRAM. Wilder EWM stays on
    pandas: cuDF lacks grouped-ewm support and the per-group apply
    fallback is no faster than pandas' vectorized ``groupby.ewm``, so
    the shared helpers' auto-routing is the right granularity here.
    """
    if df.empty:
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp = ["sec_type", "code"]

    # delta = price[t] - price[t-1] via shared grouped_diff (auto-routes
    # to cuDF when the row count exceeds the groupby_diff breakeven).
    grouped_diff(df, grp, "price", out_names="_delta", periods=1, sort=False)
    delta = df["_delta"]
    # gain/loss: positive/negative parts of delta. For null-price rows
    # (delta is NaN), gain and loss must stay NaN — NOT 0 — so the EWM
    # smoothing below (ignore_na=True) skips them instead of treating
    # them as flat days, which would skew RSI for subsequent rows.
    gain = delta.where(delta > 0, 0.0).where(delta.notna())
    loss = (-delta).where(delta < 0, 0.0).where(delta.notna())
    df = df.drop(columns=["_delta"])

    # Wilder RSI: EWM alpha=1/W, adjust=False, min_periods=W.
    # Stays on pandas — cuDF lacks grouped-ewm support (the per-group
    # apply fallback is no faster than pandas' vectorized groupby.ewm),
    # so the shared helpers' auto-routing is the right granularity here.
    # ignore_na=True: null-price rows (NaN gain/loss) are skipped by the
    # EWM, so they neither increment the smoothing nor propagate NaN
    # forward — RSI carries the last non-null value through gaps.
    for w in rsi_windows:
        avg_gain = _grouped_ewm_pandas(gain, df, grp, 1.0 / w, w, ignore_na=True)
        avg_loss = _grouped_ewm_pandas(loss, df, grp, 1.0 / w, w, ignore_na=True)
        df[f"rsi_{w}days"] = _rsi_from_avgs(avg_gain, avg_loss)

    # gap_Wdays = (price[t] - price[t-W]) / price[t-W] via shared
    # grouped_shift (auto-routes to cuDF when beneficial).
    for w in gap_windows:
        prev_col = f"_price_prev{w}"
        grouped_shift(
            df, grp, "price", out_names=prev_col,
            periods=w, sort=False,
        )
        prev = df[prev_col]
        out = (df["price"] - prev) / prev
        mask = prev.isna() | (prev.abs() < 1e-12) | ~np.isfinite(out)
        df[f"gap_{w}days"] = out.where(~mask)
        df = df.drop(columns=[prev_col])

    return df


def sanitize_rsi_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_rsi columns, apply the NUMERIC(10,6) overflow
    guard, and sanitize for asyncpg bulk upsert (NaN/inf -> None + to_dict).

    Operates on a DataFrame already carrying the rsi_*/gap_* columns
    (typically filtered to target_dates).
    """
    if df.empty:
        return []

    rsi_cols = [f"rsi_{w}days" for w in RSI_WINDOWS]
    gap_cols = [f"gap_{w}days" for w in GAP_WINDOWS]
    extreme_cols = [
        "gap_since_last_extreme",
        "days_since_last_extreme",
        "date_of_last_extreme",
    ]
    out_cols = ["sec_type", "code", "date"] + rsi_cols + gap_cols + extreme_cols
    out = df[out_cols].copy()

    # date_of_last_extreme is a DATE column (non-numeric) — exclude from
    # the NUMERIC(10,6) overflow guard + numeric sanitization.
    non_numeric = ("sec_type", "code", "date", "date_of_last_extreme")
    numeric_cols = [c for c in out_cols if c not in non_numeric]

    # Overflow guard (safety net; RSI is 0..100, gaps near-zero-nulled).
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
        print(f"    -> NUMERIC(10,6) overflow-guard nulled {total:,} value(s) "
              f"across {len(nulled)} column(s): {per}", flush=True)

    # date_of_last_extreme is a DATE column — convert NaT -> None so asyncpg
    # serializes NULL (asyncpg cannot serialize pd.NaT). Cast to object dtype
    # so None stays None (not converted back to NaT).
    out["date_of_last_extreme"] = (
        out["date_of_last_extreme"]
        .astype(object)
        .where(pd.notna(out["date_of_last_extreme"]), None)
    )

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


# ---------------------------------------------------------------------------
#  Shared helpers (work on both pandas and cuDF Series — both support
#  arithmetic, .where, and boolean masks)
# ---------------------------------------------------------------------------

def _rsi_from_avgs(avg_gain, avg_loss):
    """RSI = 100 - 100/(1+RS), with edge-case handling.

    RSI = 100  when avg_loss == 0 and avg_gain > 0 (pure uptrend).
    RSI = 0    when avg_gain == 0 and avg_loss > 0 (pure downtrend).
    RSI = NaN  when both == 0 (flat / undefined).

    Works on pandas and cuDF Series (both support /, where, ==, >, &).
    """
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)

    up_only = (avg_loss == 0) & (avg_gain > 0)
    rsi = rsi.where(~up_only, 100.0)

    down_only = (avg_gain == 0) & (avg_loss > 0)
    rsi = rsi.where(~down_only, 0.0)

    flat = (avg_gain == 0) & (avg_loss == 0)
    rsi = rsi.where(~flat, np.nan)
    return rsi


def _null_if_overflow(series):
    """Null values whose |abs| >= NUMERIC_MAX_ABS (would overflow
    NUMERIC(10,6)). Mirrors analyze.mov_ave_spread.helpers.null_if_overflow.
    """
    s = pd.to_numeric(series, errors="coerce")
    mask = s.isna() | ~np.isfinite(s) | (s.abs().round(6) >= NUMERIC_MAX_ABS)
    return s.where(~mask)


# ---------------------------------------------------------------------------
#  Wilder EWM helper (pandas groupby.ewm — vectorized, no per-group callbacks)
#  Stays on pandas: cuDF lacks grouped-ewm support and the per-group apply
#  fallback is no faster than pandas' Cython groupby.ewm. The diff (delta)
#  and shifts (gap_Ndays) ARE cuDF-accelerated via the shared helpers.
# ---------------------------------------------------------------------------

def _grouped_ewm_pandas(s, df, grp, alpha, min_periods, ignore_na=False):
    """Grouped EWM mean (Wilder smoothing) aligned to df.index.

    ``groupby(keys).ewm(alpha, adjust=False, min_periods).mean()`` returns a
    MultiIndex Series (group keys + original index). Strip the group-key
    levels and reindex to df.index to realign.

    When ``ignore_na=True``, NaN values in the input are skipped by the EWM
    (neither incrementing the smoothing nor propagating NaN forward) — used
    so null-price rows don't corrupt RSI for subsequent non-null rows.
    """
    keys = [df[k] for k in grp]
    res = (
        s.groupby(keys, sort=False)
        .ewm(alpha=alpha, adjust=False, min_periods=min_periods, ignore_na=ignore_na)
        .mean()
    )
    res = res.reset_index(level=list(range(len(grp))), drop=True)
    return res.reindex(df.index)


# ---------------------------------------------------------------------------
#  Last-extreme (turning point) computation
# ---------------------------------------------------------------------------

def _compute_since_last_extreme(df: pd.DataFrame) -> pd.DataFrame:
    """Add gap_since_last_extreme + days_since_last_extreme +
    date_of_last_extreme columns.

    A turning point (extreme) is where ``price_slope`` changes sign,
    identifying a local high (max) or low (min) in the price curve:
      - MAX: slope[t] > 0 and slope[t+1] < 0  (price was rising, now falling)
      - MIN: slope[t] < 0 and slope[t+1] > 0  (price was falling, now rising)

    For each row, finds the most recent preceding turning point per
    (sec_type, code) and computes:
      - gap_since_last_extreme = (price[t] - extreme_price) / extreme_price.
        Sign indicates the type of the last extreme: positive = last
        extreme was a local MIN (price rebounded upward), negative = last
        extreme was a local MAX (price fell). NULL when no preceding
        extreme exists.
      - days_since_last_extreme = trading days since the last extreme
        (0 on the extreme row itself). NULL when no preceding extreme.
      - date_of_last_extreme = the biz date of the most recent turning
        point (the date on which extreme_price was observed). Carried
        forward from each turning point until the next one. NULL when no
        preceding extreme exists.

    Requires ``price_slope`` column (1st derivative of price, available
    from the parent mov_ave_spread source DataFrame). Called after
    ``compute_rsi_and_gaps`` on the full per-code history.
    """
    if df.empty:
        return df

    grp = ["sec_type", "code"]
    df = df.sort_values(grp + ["date"]).reset_index(drop=True)

    # Next-day slope (shift -1) — a turning point at row t is confirmed
    # by the slope at t+1 flipping sign. Uses the shared grouped_shift
    # helper (auto-routes to cuDF when beneficial).
    grouped_shift(
        df, grp, "price_slope", out_names="_next_slope",
        periods=-1, sort=False,
    )
    slope = df["price_slope"]
    next_slope = df["_next_slope"]

    # Detect turning points: the high/low is at row t where slope[t] and
    # slope[t+1] have opposite (non-zero) signs.
    is_max = (slope > 0) & (next_slope < 0)
    is_min = (slope < 0) & (next_slope > 0)
    is_extreme = is_max | is_min

    # Per-group VALID trading-day position (0-based within each code).
    # Only increments on rows with non-null price — null/no-data rows get the
    # same position as the preceding non-null row (day increment = 0), but
    # they do NOT interrupt the extreme forward-fill below. This ensures
    # days_since_last_extreme stays constant across null/no-data rows while
    # the extreme (and its date) carries forward uninterrupted.
    grp_keys = [df[k] for k in grp]
    has_price = df["price"].notna()
    valid_pos = (
        has_price.astype("int64")
        .groupby(grp_keys, sort=False).cumsum() - 1
    )

    # Forward-fill the extreme price, position, and date within each group.
    # Rows before the first extreme get NaN → NULL in DB.
    extreme_price = (
        df["price"].where(is_extreme)
        .groupby(grp_keys, sort=False).ffill()
    )
    extreme_pos = (
        valid_pos.where(is_extreme)
        .groupby(grp_keys, sort=False).ffill()
    )
    extreme_date = (
        df["date"].where(is_extreme)
        .groupby(grp_keys, sort=False).ffill()
    )

    # gap = (price[t] - extreme_price) / extreme_price
    gap = (df["price"] - extreme_price) / extreme_price
    gap_mask = (
        extreme_price.isna()
        | (extreme_price.abs() < 1e-12)
        | ~np.isfinite(gap)
    )
    df["gap_since_last_extreme"] = gap.where(~gap_mask)

    # days = current valid position - extreme position.
    # Null/no-data rows share the previous non-null row's valid_pos, so the
    # day count does NOT increment on them (increment = 0), but the extreme
    # forward-fill is not interrupted.
    days = (valid_pos - extreme_pos).astype(float)
    days_mask = extreme_pos.isna()
    df["days_since_last_extreme"] = days.where(~days_mask)

    # date_of_last_extreme — forward-filled biz date of the last extreme.
    # NULL for rows before the first turning point in each code.
    df["date_of_last_extreme"] = extreme_date.where(~days_mask)

    # Drop the temporary helper column (not part of mov_ave_rsi schema).
    df = df.drop(columns=["_next_slope"])
    return df


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_rsi(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the Wilder RSI + gaps pipeline against the source price data
    already loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the ``price``
    column is reused — no second DB fetch). The DataFrame must contain the
    FULL per-code history (not filtered to target_dates) so Wilder
    smoothing is correct for the first target date of each code.

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_rsi against source identity tables. In force
         mode, truncate the table instead.
      2. Compute Wilder RSI (8 windows) + gaps (2 windows) + last-extreme
         columns (gap_since_last_extreme, days_since_last_extreme) over
         the FULL per-code history, then filter to target_dates.
      3. Upsert into analysis.mov_ave_rsi (chunked by date).
      4. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          price, price_slope]. Must be the FULL per-code history.
      force: when True, truncate analysis.mov_ave_rsi first and recompute
             all rows.
      pool: optional connection pool for parallel upsert chunks.
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory). When
                None, infers sec_types from the DataFrame.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_RSI (internal step of mov_ave_spread)", flush=True)
    print("=" * 78, flush=True)

    # Select only the columns RSI needs — the parent DataFrame carries
    # many extra columns (OHLC, MAs, slopes, stds) that are irrelevant
    # here. price_slope is needed for turning-point (last extreme)
    # detection. Rows with NULL price are KEPT (not dropped): RSI / gap
    # columns will be NULL for them, but days_since_last_extreme and
    # date_of_last_extreme carry forward uninterrupted (day increment = 0
    # on null-price rows). This ensures the API LEFT JOIN always finds a
    # mov_ave_rsi row for every detail date, so the UI never misses a
    # date_of_last_extreme after a no-data gap.
    rsi_df = df[["sec_type", "code", "date", "price", "price_slope"]].copy()
    n_null_price = int(rsi_df["price"].isna().sum())
    if n_null_price > 0:
        print(f"    note: {n_null_price:,} rows with NULL price will get "
              f"NULL RSI/gap but carried-forward last-extreme columns",
              flush=True)

    if rsi_df.empty:
        print("    -> no source data; skipping RSI step.", flush=True)
        return

    # Use the sec_type passed by the parent (per-sec_type loop) or infer
    # from the DataFrame for backward compatibility.
    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(rsi_df["sec_type"].unique()))

    # ---- Step 0: determine target dates (per-sec_type) --------------
    if code_filter is not None:
        # Single-code mode (--code): the caller already DELETEd this
        # code's rows from the table, so compute ALL dates for this code
        # and bypass the per-sec_type skip-filter (sec_types=() at the
        # insert below keeps every row — dates covered by OTHER codes
        # would otherwise mask this code's gaps).
        print("    mode: SINGLE-CODE (full recompute for this code)",
              flush=True)
        target_dates_union: Optional[Set] = None
    elif force:
        print("    mode: FORCE (full recompute)", flush=True)
        print("\n[r0/3] Force mode: truncating mov_ave_rsi...", flush=True)
        await truncate_table_async(conn, RSI_TABLE)
        target_dates_union: Optional[Set] = None
        print("    -> truncated; will recompute all rows", flush=True)
    else:
        print("    mode: incremental (missing dates only)", flush=True)
        print("\n[r0/3] Detecting missing dates PER-sec_type "
              "(etf_identity vs mov_ave_rsi[etf], etc.)...",
              flush=True)
        target_dates_per_st: dict = {}
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, RSI_TABLE,
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

    # ---- Step 1: compute RSI + gaps + last-extreme over full history --
    print("\n[r1/3] Computing Wilder RSI + gaps + last-extreme "
          "per (sec_type, code, date) over full history...", flush=True)
    rsi_df = compute_rsi_and_gaps(rsi_df)
    rsi_df = _compute_since_last_extreme(rsi_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(rsi_df)
        rsi_df = rsi_df[rsi_df["date"].isin(target_dates_union)].reset_index(drop=True)
        print(f"    -> incremental filter: {len(rsi_df):,} of {n_before:,} "
              f"rows are in target_dates_union", flush=True)

    if rsi_df.empty:
        print("    -> no rows to upsert; skipping RSI upsert.", flush=True)
        return

    # ---- Step 2: build + insert (chunked by date) -------------------
    # sanitize_rsi_rows materializes one Python dict per row; for the full
    # stock universe (6.7M rows) that is multi-GB and OOMs. Build + insert
    # per date-chunk so peak memory is bounded to one chunk's dicts
    # (~100K rows). Mirrors the parent mov_ave_spread detail step.
    print(f"\n[r2/3] Building + inserting {len(rsi_df):,} mov_ave_rsi rows "
          f"in date-bounded chunks ({'COPY' if force else 'upsert'} per "
          f"chunk)...", flush=True)
    n = await build_and_insert_chunked(
        conn, pool, rsi_df,
        sanitize_rsi_rows,
        table_name=RSI_TABLE,
        key_columns=["sec_type", "code", "date"],
        force=force,
        sec_types=() if code_filter is not None else sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_rsi",
    )
    del rsi_df
    print(f"    -> inserted {n:,} rows", flush=True)

    # ---- Step 3: register in analysis_identity ----------------------
    print(f"\n[r3/3] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=RSI_ANALYSIS_NAME,
        detail_name="mov_ave_rsi",
        description=RSI_DESCRIPTION,
    )

    print(f"\n  mov_ave_rsi wall time: {time.time() - t0:.1f}s", flush=True)
