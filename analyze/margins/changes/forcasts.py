"""Forward price performance for margin trend episodes.

After a margin trend [start_date, end_date] ends, this module computes
the PRICE performance over the next 5, 20, and 60 TRADING days:
  - high_price_Xd   : MAX(close) over the X-day forward window
  - low_price_Xd    : MIN(close) over the X-day forward window
  - days_to_high_Xd : trading days from window start to the high
  - days_to_low_Xd  : trading days from window start to the low

Reuses the margin_changes episodes (DataFrame) already in memory from
the trend detection step. Fetches forward price close data from
``stats.{index,etf,stock}_basic_stats``. Truncates-then-recomputes on
every run (aligned with the margin_changes recompute cycle).

All INSERTs are in Python per project rule — no raw INSERT...SELECT
SQL is used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.build_commons import copy_insert_async, truncate_table_async
from analyze._common import sanitize_for_db_insert, upsert_analysis_identity

from analyze.margins.config import TABLE_CHANGES

# ---- Table name -----------------------------------------------------------
TABLE_FORCASTS = "analysis.margin_hype_to_price_forcasts"

# ---- Forward windows (trading days after end_date) -------------------------
FORCAST_WINDOWS = [5, 20, 60]

# ---- Insert columns (matches the table schema) ----------------------------
INSERT_COLUMNS = [
    "code", "sec_type", "start_date", "end_date",
    "high_price_5d", "low_price_5d", "days_to_high_5d", "days_to_low_5d",
    "high_price_20d", "low_price_20d", "days_to_high_20d", "days_to_low_20d",
    "high_price_60d", "low_price_60d", "days_to_high_60d", "days_to_low_60d",
]

# Numeric columns for sanitize_for_db_insert (round to 4 decimals).
NUMERIC_COLS = [
    "high_price_5d", "low_price_5d",
    "high_price_20d", "low_price_20d",
    "high_price_60d", "low_price_60d",
]

# Integer columns that must be kept as int (not rounded to float).
INTEGER_COLS = [
    "days_to_high_5d", "days_to_low_5d",
    "days_to_high_20d", "days_to_low_20d",
    "days_to_high_60d", "days_to_low_60d",
]

# ---- Source tables for price close data ------------------------------------
_PRICE_CLOSE_TABLES = {
    "index": ("stats.index_basic_stats", "code"),
    "etf":   ("stats.etf_basic_stats",   "code"),
    "stock": ("stats.stock_basic_stats", "code"),
}

# ---- Description for analysis_identity ------------------------------------
DESCRIPTION = (
    "Per-(sec_type, code, trend) FORWARD price performance after a margin "
    "trend episode ends. One row per margin_changes trend episode (1:1 via "
    "FK on code, sec_type, start_date, end_date). For each of 3 forward "
    "windows (5, 20, 60 TRADING days after end_date): high_price (MAX "
    "close), low_price (MIN close), days_to_high (trading days from window "
    "start to high), days_to_low (trading days from window start to low). "
    "Validates whether margin UP/DOWN trends have predictive value for "
    "future price moves. sec_type ∈ {etf, stock, index}. Built by "
    "analyze.margins.changes.forcasts (truncate-then-recompute); all "
    "INSERTs in Python per project rule."
)


async def fetch_forward_price_closes(
    conn,
    episodes: pd.DataFrame,
    max_window: int = 60,
) -> pd.DataFrame:
    """Fetch price close data for all codes in the episodes.

    Fetches only the date range needed: from ``MIN(end_date) - 5``
    to ``MAX(end_date) + max_window * 2`` calendar days, minimizing
    data transfer.

    Returns a DataFrame with columns [sec_type, code, date, close].

    Uses a single UNION ALL query across all sec_type tables to avoid
    per-type round-trips and eliminate the frames-list append pattern.
    """
    _empty_cols = ["sec_type", "code", "date", "close"]
    if episodes.empty:
        return pd.DataFrame(columns=_empty_cols)

    # Compute date range to fetch
    min_end = episodes["end_date"].min()
    max_end = episodes["end_date"].max()
    # Start a few days before min_end to ensure searchsorted finds correct position
    from datetime import timedelta
    date_from = min_end - timedelta(days=10)
    # max_window * 2 calendar days buffer (trading days → calendar days)
    date_to = max_end + timedelta(days=max_window * 2)

    # Collect codes per sec_type; build UNION ALL parts
    sec_types_in_ep = list(episodes["sec_type"].unique())
    parts: list[str] = []
    params: list = [date_from, date_to]  # $1 = date_from, $2 = date_to

    for sec_type in sec_types_in_ep:
        table_info = _PRICE_CLOSE_TABLES.get(sec_type)
        if table_info is None:
            continue
        table, code_col = table_info

        codes: list[str] = (
            episodes.loc[episodes["sec_type"] == sec_type, "code"]
            .unique()
            .tolist()
        )
        if not codes:
            continue

        params.append(codes)
        code_placeholder = f"${len(params)}::text[]"
        parts.append(f"""
            SELECT '{sec_type}'::text AS sec_type,
                   {code_col} AS code, date, close
            FROM {table}
            WHERE {code_col} = ANY({code_placeholder})
              AND date >= $1
              AND date <= $2
              AND close IS NOT NULL
        """)

    if not parts:
        return pd.DataFrame(columns=_empty_cols)

    query = "\nUNION ALL\n".join(parts) + "\nORDER BY sec_type, code, date"
    rows = await conn.fetch(query, *params)

    if not rows:
        return pd.DataFrame(columns=_empty_cols)

    df = pd.DataFrame(rows, columns=_empty_cols)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
#  Forward rolling helpers (vectorized via pandas + numpy)
# ---------------------------------------------------------------------------

def _forward_max_min_for_series(
    close: pd.Series, window: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute forward max/min and positions for all dates in a series.

    For date at index i, the forward window is close[i+1 : min(i+1+w, n)].
    Uses pandas rolling (Cython-backed) for max/min and a custom
    numpy vectorized pass for argmax/argmin positions.

    Returns (high, low, dth, dtl) numpy arrays aligned to the input index.
    """
    n = len(close)
    if n == 0:
        empty = np.array([], dtype=np.float64)
        return empty, empty, empty, empty

    close_arr = close.to_numpy(dtype=np.float64)

    # Forward max/min via pandas rolling on shifted series.
    # shift(-1) moves close[i+1] → position i, so rolling(w).max()
    # gives max of [close[i+1], close[i+2], ..., close[i+w]].
    shifted = close.shift(-1)
    fwd_max = shifted.rolling(window, min_periods=1).max().to_numpy()
    fwd_min = shifted.rolling(window, min_periods=1).min().to_numpy()

    # Forward argmax/argmin positions via numpy vectorized pass.
    # For each position i, scan close_arr[i+1 : i+1+window] for the
    # index of max/min (0-indexed within the window).
    dth = np.full(n, np.nan, dtype=np.float64)
    dtl = np.full(n, np.nan, dtype=np.float64)

    for i in range(n):
        start = i + 1
        end = min(start + window, n)
        if start >= end:
            continue
        window_vals = close_arr[start:end]
        valid_mask = ~np.isnan(window_vals)
        if not valid_mask.any():
            continue
        valid_pos = np.where(valid_mask)[0]
        valid_vals = window_vals[valid_mask]
        dth[i] = valid_pos[np.argmax(valid_vals)]
        dtl[i] = valid_pos[np.argmin(valid_vals)]

    return fwd_max, fwd_min, dth, dtl


def _build_forward_lookup(
    code_prices: pd.DataFrame, windows: list[int]
) -> dict:
    """Build a lookup dict: date -> {window: (high, low, dth, dtl)}.

    Pre-computes forward max/min for ALL dates in the price series using
    pandas rolling + numpy vectorized argmax/argmin, then builds a
    per-date lookup for O(1) episode mapping.
    """
    if code_prices.empty:
        return {}

    code_prices = code_prices.sort_values("date").reset_index(drop=True)
    close = code_prices.set_index("date")["close"]

    # Pre-compute all forward values per window
    fwd_by_window: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
    for w in windows:
        fwd_by_window[w] = _forward_max_min_for_series(close, w)

    # Build date -> {window: values} lookup
    date_arr = code_prices["date"].to_numpy()
    n = len(date_arr)
    lookup: dict = {}
    for i in range(n):
        d = date_arr[i]
        entry: dict = {}
        for w in windows:
            high, low, dth, dtl = fwd_by_window[w]
            entry[w] = (high[i], low[i], dth[i], dtl[i])
        lookup[d] = entry

    return lookup


def compute_forward_forcasts(
    episodes: pd.DataFrame,
    price_closes: pd.DataFrame,
) -> pd.DataFrame:
    """Compute forward price performance for each trend episode.

    Pre-computes forward max/min/argmax/argmin for ALL dates per
    (sec_type, code) using vectorized pandas rolling, then maps each
    episode via O(1) date lookups — eliminating per-episode Python
    loops over price arrays.
    """
    if episodes.empty:
        return episodes

    if price_closes.empty:
        for col in INSERT_COLUMNS:
            if col not in ("code", "sec_type", "start_date", "end_date"):
                episodes[col] = np.nan
        return episodes

    results: list[dict] = []

    # Pre-build forward value lookups for each (sec_type, code) pair
    # that has price data.
    price_lookups: dict[tuple[str, str], dict] = {}
    for (st, code), grp in price_closes.groupby(["sec_type", "code"]):
        lookup = _build_forward_lookup(grp, FORCAST_WINDOWS)
        if lookup:
            price_lookups[(st, code)] = lookup

    # Map each episode via pre-built lookup
    for _, ep in episodes.iterrows():
        st = ep["sec_type"]
        code = ep["code"]
        end_date = ep["end_date"]
        start_date = ep["start_date"]
        key = (st, code)

        row: dict = {
            "code": code,
            "sec_type": st,
            "start_date": start_date,
            "end_date": end_date,
        }

        lookup = price_lookups.get(key)
        if lookup is not None:
            fwd_vals = lookup.get(end_date, {})
            for w in FORCAST_WINDOWS:
                high, low, dth, dtl = fwd_vals.get(
                    w, (np.nan, np.nan, np.nan, np.nan)
                )
                row[f"high_price_{w}d"] = high
                row[f"low_price_{w}d"] = low
                row[f"days_to_high_{w}d"] = dth
                row[f"days_to_low_{w}d"] = dtl
        else:
            for w in FORCAST_WINDOWS:
                row[f"high_price_{w}d"] = np.nan
                row[f"low_price_{w}d"] = np.nan
                row[f"days_to_high_{w}d"] = np.nan
                row[f"days_to_low_{w}d"] = np.nan

        results.append(row)

    if not results:
        return pd.DataFrame(columns=INSERT_COLUMNS)

    result_df = pd.DataFrame(results)
    for col in INSERT_COLUMNS:
        if col not in result_df.columns:
            result_df[col] = np.nan

    return result_df[INSERT_COLUMNS]


async def run_margin_forcasts(
    conn,
    episodes: pd.DataFrame,
) -> int:
    """Compute and store forward price forcasts for margin trend episodes.

    Args:
        conn: asyncpg connection.
        episodes: margin_changes episodes DataFrame (must have columns
            code, sec_type, start_date, end_date).

    Returns:
        Number of rows inserted.
    """
    print("\n  Computing forward price forcasts for margin trends...", flush=True)

    if episodes.empty:
        print("    -> no episodes; writing empty table.", flush=True)
        await truncate_table_async(conn, TABLE_FORCASTS)
        await upsert_analysis_identity(
            conn,
            name="margin_hype_to_price_forcasts",
            detail_name="margin_hype_to_price_forcasts",
            description=DESCRIPTION,
        )
        return 0

    sec_types_present = list(episodes["sec_type"].unique())
    print(f"    Fetching forward price closes "
          f"({', '.join(sec_types_present)})...", flush=True)
    price_closes = await fetch_forward_price_closes(conn, episodes)
    print(f"        -> {len(price_closes):,} price close rows", flush=True)

    print("    Computing forward windows (5d, 20d, 60d)...", flush=True)
    forcasts_df = compute_forward_forcasts(episodes, price_closes)
    print(f"        -> {len(forcasts_df):,} forcast rows", flush=True)

    # Truncate and insert.
    print(f"    Truncating {TABLE_FORCASTS} and inserting...", flush=True)
    await truncate_table_async(conn, TABLE_FORCASTS)

    if forcasts_df.empty:
        n = 0
    else:
        # Ensure column order matches the table schema.
        forcasts_df = forcasts_df[INSERT_COLUMNS].copy()

        # Convert integer columns: groupby may produce float for days cols.
        for col in INTEGER_COLS:
            if col in forcasts_df.columns:
                forcasts_df[col] = forcasts_df[col].astype("Int64")

        rows = sanitize_for_db_insert(
            forcasts_df,
            numeric_cols=NUMERIC_COLS,
            round_to=4,
        )
        n = await copy_insert_async(
            conn, TABLE_FORCASTS, rows,
            columns=INSERT_COLUMNS,
        )

    print(f"    -> COPY-inserted {n:,} rows into {TABLE_FORCASTS}", flush=True)

    # Upsert the analysis_identity row.
    await upsert_analysis_identity(
        conn,
        name="margin_hype_to_price_forcasts",
        detail_name="margin_hype_to_price_forcasts",
        description=DESCRIPTION,
    )

    return n