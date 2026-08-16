"""Internal rebounds (double-top detection) step for analyze.mov_ave_spread.

Detects "rebound" (double-top / shoulder) patterns in price data for ETF +
Index + Stock. One row per (sec_type, code, date) in analysis.mov_ave_rebounds.

For each window W ∈ {20,60,120,255} trading days ending at date D:
  1. Find the close-price maximum in [D-W+1, D] → "top max"
  2. If the top max is NOT today (D), find the next close-price maximum
     strictly after the top max date within the same window → "2nd max"
  3. If a 2nd max exists, emit:
       rebound_date_{W}days        = date of the 2nd max
       rebound_close_price_{W}days = close at the 2nd max
       rebound_gap_days_{W}days    = trading days between top max
                                    and 2nd max
       rebound_trading_amt_{W}days = SUM(trading_amount) during the
                                    rebound period (top max → 2nd max,
                                    inclusive)
     All 4 columns are NULL when the top max is today or no 2nd max
     exists after it (single-peak window).

Source: same DataFrame as the mov_ave_spread parent pipeline (price,
trading_amount columns — no second DB round-trip).

This module is an INTERNAL step of analyze.mov_ave_spread — it is invoked
from __main__.py after the detail + peaks_and_floors tables have been
repopulated, reusing the same DB connection + source DataFrame. It is NOT
a standalone runnable.

Incremental mode (force=False):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_rebounds are (re)computed and upserted.

Force mode (force=True):
  Truncate analysis.mov_ave_rebounds, then recompute and insert all rows.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    find_missing_analysis_dates,
)
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import (
    NUMERIC_MAX_ABS,
    NUMERIC_WIDE_MAX_ABS,
    REBOUNDS_ANALYSIS_NAME,
    REBOUNDS_COLUMNS,
    REBOUNDS_DESCRIPTION,
    REBOUNDS_TABLE,
    REBOUNDS_WINDOWS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)
from analyze.mov_ave_spread.helpers import null_if_overflow


# ---------------------------------------------------------------------------
#  Compute helpers (pure numpy — per-group processing, no cuDF needed)
# ---------------------------------------------------------------------------

def _compute_rebounds_group(
    prices: np.ndarray,
    dates: np.ndarray,
    trading_amts: np.ndarray,
    windows: tuple = REBOUNDS_WINDOWS,
) -> dict[int, tuple]:
    """Compute rebound metrics for one (sec_type, code) group.

    For each window W, returns 4 arrays (rebound_date, rebound_close_price,
    rebound_gap_days, rebound_trading_amt) aligned to the input arrays.

    Uses a deque-based sliding-window max for O(n) per-window performance.
    """
    n = len(prices)
    results = {}

    for W in windows:
        reb_date = np.array([None] * n, dtype=object)
        reb_close = np.full(n, np.nan)
        reb_gap = np.full(n, np.nan)
        reb_amt = np.full(n, np.nan)

        if n < 2:
            results[W] = (reb_date, reb_close, reb_gap, reb_amt)
            continue

        # Step 1: find top_max_pos[i] for each i using deque-based
        # sliding window max. O(n) amortized per window.
        top_max_pos = _sliding_max_pos(prices, W)

        # Step 2: for each row, check if rebound exists
        for i in range(W - 1, n):
            tmp = top_max_pos[i]
            if tmp < 0 or tmp >= i:
                continue  # top max is today or invalid

            # Window [top_max_pos+1, i] — find 2nd max
            seg_prices = prices[tmp + 1 : i + 1]
            if len(seg_prices) == 0:
                continue

            reb_rel = int(np.nanargmax(seg_prices))
            reb_abs = tmp + 1 + reb_rel

            if not np.isfinite(prices[reb_abs]):
                continue

            reb_date[i] = dates[reb_abs]
            reb_close[i] = float(prices[reb_abs])
            reb_gap[i] = float(reb_abs - tmp)

            # SUM of trading_amount during rebound period (top max → 2nd max,
            # inclusive). Uses np.nansum so NaN trading_amount values are
            # treated as 0 (no turnover data that day).
            amt_segment = trading_amts[tmp : reb_abs + 1]
            amt_segment = np.where(np.isfinite(amt_segment), amt_segment, 0.0)
            reb_amt[i] = float(np.sum(amt_segment))

        results[W] = (reb_date, reb_close, reb_gap, reb_amt)

    return results


def _sliding_max_pos(prices: np.ndarray, W: int) -> np.ndarray:
    """Deque-based O(n) sliding window max position.

    Returns an array where result[i] is the position (in the original
    array) of the maximum value within the window [max(0,i-W+1), i].
    Returns -1 for rows where the window is not yet fully populated
    (i < W-1) or all values in the window are NaN.

    Uses a deque of (index, value) pairs, maintaining decreasing values:
    new entries pop smaller-or-equal values from the back, and stale
    entries (outside the window) are popped from the front.
    """
    n = len(prices)
    result = np.full(n, -1, dtype=np.int64)
    dq: deque = deque()  # (index, value) pairs, decreasing by value

    for i in range(n):
        val = prices[i]
        start = i - W + 1
        if start < 0:
            start = 0

        # Remove entries outside the window from the front
        while dq and dq[0][0] < start:
            dq.popleft()

        # Remove entries with value <= current from the back (they can't
        # be the max if current is larger and later)
        if np.isfinite(val):
            while dq and (dq[-1][1] <= val if np.isfinite(dq[-1][1]) else True):
                dq.pop()
            dq.append((i, float(val)))
        else:
            # NaN value — skip adding to deque (it can't be the max)
            pass

        if i >= W - 1 and dq:
            result[i] = dq[0][0]
        elif i < W - 1:
            # Window not fully populated yet — no reliable max
            result[i] = -1

    return result


def compute_rebounds(df: pd.DataFrame) -> pd.DataFrame:
    """Compute rebound metrics for each (sec_type, code, date) row.

    Processes each (sec_type, code) group independently, using a deque-
    based sliding window max for O(n) per-window performance per group.

    Returns a DataFrame with columns matching analysis.mov_ave_rebounds.
    """
    if df.empty:
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    groups = df.groupby(["sec_type", "code"], sort=False)

    all_results = []
    for (sec_type, code), group in groups:
        prices = group["price"].values.astype(np.float64)
        dates = group["date"].values
        trading_amts = group["trading_amount"].values.astype(np.float64)
        n = len(group)

        # Initialize columns for this group
        group_data = {
            "sec_type": sec_type,
            "code": code,
            "date": dates,
        }

        for W in REBOUNDS_WINDOWS:
            reb_date_key = f"rebound_date_{W}days"
            reb_close_key = f"rebound_close_price_{W}days"
            reb_gap_key = f"rebound_gap_days_{W}days"
            reb_amt_key = f"rebound_trading_amt_{W}days"

            if n < W:
                group_data[reb_date_key] = np.array([None] * n, dtype=object)
                group_data[reb_close_key] = np.full(n, np.nan)
                group_data[reb_gap_key] = np.full(n, np.nan)
                group_data[reb_amt_key] = np.full(n, np.nan)
                continue

            # Compute top max positions for this window
            top_max_pos = _sliding_max_pos(prices, W)

            reb_date = np.array([None] * n, dtype=object)
            reb_close = np.full(n, np.nan)
            reb_gap = np.full(n, np.nan)
            reb_amt = np.full(n, np.nan)

            for i in range(W - 1, n):
                tmp = top_max_pos[i]
                if tmp < 0 or tmp >= i:
                    continue

                seg_prices = prices[tmp + 1 : i + 1]
                if len(seg_prices) == 0:
                    continue

                reb_rel = int(np.nanargmax(seg_prices))
                reb_abs = tmp + 1 + reb_rel

                if not np.isfinite(prices[reb_abs]):
                    continue

                reb_date[i] = dates[reb_abs]
                reb_close[i] = float(prices[reb_abs])
                reb_gap[i] = float(reb_abs - tmp)

                amt_segment = trading_amts[tmp : reb_abs + 1]
                amt_segment = np.where(
                    np.isfinite(amt_segment), amt_segment, 0.0
                )
                reb_amt[i] = float(np.sum(amt_segment))

            group_data[reb_date_key] = reb_date
            group_data[reb_close_key] = reb_close
            group_data[reb_gap_key] = reb_gap
            group_data[reb_amt_key] = reb_amt

        # Build DataFrame for this group's results
        group_df = pd.DataFrame(group_data)
        all_results.append(group_df)

    result = pd.concat(all_results, ignore_index=True)
    return result


def sanitize_rebounds_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_rebounds columns, apply the NUMERIC overflow
    guard, and sanitize for asyncpg bulk upsert (NaN/inf -> None + to_dict).
    """
    if df.empty:
        return []

    out_cols = list(REBOUNDS_COLUMNS)
    out = df[out_cols].copy()

    non_numeric = ("sec_type", "code", "date") + tuple(
        f"rebound_date_{w}days" for w in REBOUNDS_WINDOWS
    )
    numeric_cols = [c for c in out_cols if c not in non_numeric]

    # Overflow guard using shared null_if_overflow helper
    nulled = {}
    for c in numeric_cols:
        before = int(out[c].isna().sum())
        if c.startswith("rebound_trading_amt"):
            out[c] = null_if_overflow(out[c], max_abs=NUMERIC_WIDE_MAX_ABS, scale=4)
        else:
            out[c] = null_if_overflow(out[c], max_abs=NUMERIC_MAX_ABS, scale=6)
        n = int(out[c].isna().sum()) - before
        if n > 0:
            nulled[c] = n

    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        print(
            f"    -> overflow-guard nulled {total:,} value(s) across "
            f"{len(nulled)} column(s): {per}",
            flush=True,
        )

    # Convert date columns: NaT → None
    for w in REBOUNDS_WINDOWS:
        col = f"rebound_date_{w}days"
        out[col] = out[col].astype(object).where(pd.notna(out[col]), None)

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_rebounds(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
) -> None:
    """Run the rebounds (double-top detection) pipeline against the
    source data already loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the ``price``
    and ``trading_amount`` columns are reused — no second DB fetch). The
    DataFrame must contain the FULL per-code history so the rolling-window
    detection is correct for the first target date of each code.

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_rebounds against source identity tables. In
         force mode, truncate the table instead.
      2. Compute rebound metrics over the FULL per-code history, then
         filter to target_dates.
      3. Upsert into analysis.mov_ave_rebounds (chunked by date).
      4. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          price, trading_amount]. Must be the FULL per-code history.
      force: when True, truncate analysis.mov_ave_rebounds first and
             recompute all rows.
      pool: optional connection pool for parallel upsert chunks.
      max_concurrent: maximum parallel upsert chunks.
      sec_type: when provided, process only this sec_type.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_REBOUNDS (internal step of mov_ave_spread)", flush=True)
    print("=" * 78, flush=True)

    needed_cols = ["sec_type", "code", "date", "price", "trading_amount"]
    available = [c for c in needed_cols if c in df.columns]
    reb_df = df[available].copy()

    if reb_df.empty:
        print("    -> no source data; skipping rebounds step.", flush=True)
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(reb_df["sec_type"].unique()))

    # ---- Step 0: determine target dates (per-sec_type) --------------
    if force:
        print("    mode: FORCE (full recompute)", flush=True)
        print("\n[rb0/3] Force mode: truncating mov_ave_rebounds...", flush=True)
        await truncate_table_async(conn, REBOUNDS_TABLE)
        target_dates_union: Optional[Set] = None
        print("    -> truncated; will recompute all rows", flush=True)
    else:
        print("    mode: incremental (missing dates only)", flush=True)
        print("\n[rb0/3] Detecting missing dates PER-sec_type...", flush=True)
        target_dates_per_st: dict = {}
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, REBOUNDS_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=st,
            )
            target_dates_per_st[st] = td_st
            print(f"    -> {st}: {len(td_st)} missing dates", flush=True)
        target_dates_union = set()
        for s in target_dates_per_st.values():
            target_dates_union |= s
        print(
            f"    -> union across sec_types: "
            f"{len(target_dates_union)} dates to (re)compute",
            flush=True,
        )
        if not target_dates_union:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return

    # ---- Step 1: compute rebounds over full history, then filter --
    print(
        "\n[rb1/3] Computing rebound metrics per (sec_type, code, date) "
        "over full history...",
        flush=True,
    )
    reb_df = compute_rebounds(reb_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(reb_df)
        reb_df = reb_df[reb_df["date"].isin(target_dates_union)].reset_index(
            drop=True
        )
        print(
            f"    -> incremental filter: {len(reb_df):,} of {n_before:,} "
            f"rows are in target_dates_union",
            flush=True,
        )

    if reb_df.empty:
        print("    -> no rows to upsert; skipping rebounds upsert.", flush=True)
        return

    # ---- Step 2: build + insert (chunked by date) -------------------
    print(
        f"\n[rb2/3] Building + inserting {len(reb_df):,} mov_ave_rebounds "
        f"rows in date-bounded chunks...",
        flush=True,
    )
    n = await build_and_insert_chunked(
        conn, pool, reb_df,
        sanitize_rebounds_rows,
        table_name=REBOUNDS_TABLE,
        key_columns=["sec_type", "code", "date"],
        force=force,
        sec_types=sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_rebounds",
    )
    del reb_df
    print(f"    -> inserted {n:,} rows", flush=True)

    # ---- Step 3: register in analysis_identity ----------------------
    print(
        f"\n[rb3/3] Upserting analysis.analysis_identity registry...",
        flush=True,
    )
    await upsert_analysis_identity(
        conn,
        name=REBOUNDS_ANALYSIS_NAME,
        detail_name="mov_ave_rebounds",
        description=REBOUNDS_DESCRIPTION,
    )

    print(
        f"\n  mov_ave_rebounds wall time: {time.time() - t0:.1f}s",
        flush=True,
    )