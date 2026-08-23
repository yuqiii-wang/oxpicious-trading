"""Internal OHLC step for analyze.mov_ave_spread.

Rolling OHLC summary (today_close + open/high/low/second-peak-high/
second-trough-low over 7 windows: 20/60/120/255/500/750/1275 trading days)
for ETF + Index + Stock. One row per (sec_type, code, date) in
analysis.mov_ave_spreads_detail_ohlc.

For each window W ending on `date` (= "today" / the clicked date):
  - open_Wd:      open price on the W-th trading day before `date`
  - high_Wd:      top-high anchor: the highest CLOSE among window dates
                  MORE THAN 20% of W trading days before `date`; the stored
                  value is that anchor date's CLOSE
  - low_Wd:       lowest-low anchor: the lowest CLOSE among window dates
                  MORE THAN 20% of W trading days before `date`; the stored
                  value is that anchor date's CLOSE
  - high_2nd_Wd:  second-high anchor: the best local-maximum CLOSE peak in
                  the same restricted region that lies MORE THAN 20% of W
                  trading days AFTER the top anchor (so the roof line runs
                  forward in time from the top high); the stored value is
                  that anchor date's INTRADAY HIGH (wick)
  - low_2nd_Wd:   second-low anchor: the best local-minimum CLOSE trough in
                  the same restricted region that lies MORE THAN 20% of W
                  trading days AFTER the bottom anchor; the stored value is
                  that anchor date's INTRADAY LOW (wick)
  - high_line_slope_Wd:  slope of the roof line through the two high
                  anchors, in price units per trading day:
                  (high_2nd_Wd - high_Wd) / (trading days between the two
                  anchor dates); negative = descending roof
  - low_line_slope_Wd:   slope of the floor line through the two low
                  anchors, same formula on the low side; positive =
                  ascending floor

Anchor-date "today" constraint: when the unconstrained extreme's date lies
within 20% of the window from `date`, the next-best qualifying date is used
instead ("search other dates"); the columns are NULL when no qualifying
date exists (e.g. early history shorter than 20% of W + 1 rows).

today_close is the close price on `date` (COALESCE(adj_close, close) for
ETFs; close for index/stock). NOT NULL.

Source: the same source DataFrame already loaded by the parent
mov_ave_spread.fetch_source_data — reuses the same DataFrame, no second
DB round-trip. Anchor DATES are selected on the CLOSE price (``price``
column); top-anchor VALUES are the close on those dates while 2nd-anchor
VALUES are the intraday high/low on those dates (``high``/``low`` columns).
open_Wd uses the open column (fetched from stats.*_basic_stats +
stats.*_adjustment).

This module is an INTERNAL step of analyze.mov_ave_spread — it is invoked
from __main__.py after the detail table has been
repopulated, reusing the same DB connection + source DataFrame. It is NOT
a standalone runnable.

Incremental mode (``force=False``):
  Two kinds of target dates are (re)computed and upserted:
    1. Missing dates — present in source identity tables but NOT yet in
       analysis.mov_ave_spreads_detail_ohlc (per-sec_type check).
    2. Repair dates — dates whose existing rows predate the DATE /
       second-extrema columns (detected via ``high_20d IS NOT NULL AND
       high_date_20d IS NULL``). Those rows are deleted up-front so the
       chunked COPY path can re-insert the recomputed rows (COPY cannot
       upsert).

  The OHLC columns require up to 1275 prior rows per code, so the FULL
  per-code history (already in the parent DataFrame) is used and the
  result is filtered to target_dates before upsert.

Force mode (``force=True``):
  Truncate analysis.mov_ave_spreads_detail_ohlc, then recompute and
  insert all rows for the active universe.

GPU acceleration: the open_Wd shift computation uses the shared
``grouped_shift`` helper (GPU-accelerated).  All anchor value+date
computations use the vectorised sparse-table implementation in
``ohlc_vector`` (per-group NumPy: doubling argmax tables + searchsorted
range queries — no ``rolling().apply`` UDFs anywhere).  The former
per-window ``rolling().apply`` implementation is kept below as the
reference for A/B regression (``_compute_group_ohlc_columns``).
"""
from __future__ import annotations

import datetime
import time
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    find_missing_analysis_dates,
)
from _common.df_utils import grouped_shift
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import (
    OHLC_ANALYSIS_NAME,
    OHLC_COOLDOWN_PCT,
    OHLC_COLUMNS,
    OHLC_DATE_COLUMNS,
    OHLC_DESCRIPTION,
    OHLC_TABLE,
    OHLC_WINDOWS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)
from analyze.mov_ave_spread.ohlc_vector import (
    compute_group_anchors_all_windows,
)


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def _find_local_max_peaks(
    arr: np.ndarray,
) -> tuple[list[int], list[float]]:
    """Find local-maximum peaks in a 1-D array (handles NaN internally).

    A local maximum at position *i* is defined as:

    * **Left boundary** (i == 0):  ``arr[0] >= arr[1]``
    * **Interior** (0 < i < n-1):    ``arr[i] > arr[i-1]``  **and**
                                    ``arr[i] >= arr[i+1]``
    * **Right boundary** (i == n-1): ``arr[n-1] > arr[n-2]``

    Strict ``>`` on the left and ``>=`` on the right means that a flat
    plateau (e.g. [10, 15, 15, 12]) is detected as a **single** peak at
    the *first* plateau position — exactly what we want for curvature
    analysis.

    Returns:
        (original_indices, peak_values) — both lists aligned.
    """
    # Build a clean (no-NaN) version while remembering original indices.
    valid_mask = ~np.isnan(arr)
    if not np.any(valid_mask):
        return [], []

    orig_indices = np.flatnonzero(valid_mask)   # positions in *arr*
    clean = arr[valid_mask]                      # values without NaN
    n = len(clean)
    if n < 2:
        # Need at least 2 valid points to define a boundary peak.
        return [], []

    peaks_idx: list[int] = []
    peaks_val: list[float] = []

    for i in range(n):
        is_peak = True

        # Left check
        if i > 0:
            if not (clean[i] > clean[i - 1]):
                is_peak = False

        # Right check (only when still possibly a peak)
        if is_peak and i < n - 1:
            if not (clean[i] >= clean[i + 1]):
                is_peak = False

        if is_peak:
            peaks_idx.append(int(orig_indices[i]))
            peaks_val.append(float(clean[i]))

    return peaks_idx, peaks_val


def _find_local_min_troughs(
    arr: np.ndarray,
) -> tuple[list[int], list[float]]:
    """Find local-minimum troughs in a 1-D array (handles NaN internally).

    Mirror of ``_find_local_max_peaks``:

    * **Left boundary**:   ``arr[0] <= arr[1]``
    * **Interior**:          ``arr[i] < arr[i-1]``  **and**
                            ``arr[i] <= arr[i+1]``
    * **Right boundary**:  ``arr[n-1] < arr[n-2]``
    """
    valid_mask = ~np.isnan(arr)
    if not np.any(valid_mask):
        return [], []

    orig_indices = np.flatnonzero(valid_mask)
    clean = arr[valid_mask]
    n = len(clean)
    if n < 2:
        return [], []

    troughs_idx: list[int] = []
    troughs_val: list[float] = []

    for i in range(n):
        is_trough = True

        if i > 0:
            if not (clean[i] < clean[i - 1]):
                is_trough = False

        if is_trough and i < n - 1:
            if not (clean[i] <= clean[i + 1]):
                is_trough = False

        if is_trough:
            troughs_idx.append(int(orig_indices[i]))
            troughs_val.append(float(clean[i]))

    return troughs_idx, troughs_val


# ---------------------------------------------------------------------------
#  Anchor-position helpers (raw=True — return float positions, not values).
#
#  Every callback receives the rolling window array `arr` of length n whose
#  LAST index (n-1) is "today" — the row the window ends on.  Anchors must
#  sit MORE THAN `cooldown` trading days away from today, so candidates are
#  restricted to the "qualifying region" [0, n-2-cooldown].  When the
#  unconstrained extreme lies closer to today, the next-best qualifying
#  date is returned instead ("search other dates").
# ---------------------------------------------------------------------------
def _region_limit(n: int, cooldown: int) -> int:
    """Last index (inclusive) of the qualifying region: window positions
    MORE THAN `cooldown` trading days before today (index n-1)."""
    return n - 2 - cooldown


def _argmax_pos_today_constrained_raw(
    arr: np.ndarray, cooldown: int
) -> float:
    """Position of the max value within the qualifying region, as float.

    NaN when the region is empty (window shorter than cooldown+2 rows) or
    all-NaN.
    """
    lim: int = _region_limit(len(arr), cooldown)
    if lim < 0:
        return float("nan")
    region: np.ndarray = arr[: lim + 1]
    if not np.any(~np.isnan(region)):
        return float("nan")
    return float(np.nanargmax(region))


def _argmin_pos_today_constrained_raw(
    arr: np.ndarray, cooldown: int
) -> float:
    """Position of the min value within the qualifying region, as float."""
    lim: int = _region_limit(len(arr), cooldown)
    if lim < 0:
        return float("nan")
    region: np.ndarray = arr[: lim + 1]
    if not np.any(~np.isnan(region)):
        return float("nan")
    return float(np.nanargmin(region))


def _second_peak_max_pos_today_constrained_raw(
    arr: np.ndarray, cooldown: int
) -> float:
    """Position of the 2nd-high anchor as float, or NaN when absent.

    The top reference is the qualifying region's argmax (the same anchor
    the top-high date column uses, so the two roof-line anchors are always
    consistent).  Candidates are local-maximum CLOSE peaks inside the
    region, ranked by close value descending; the first candidate that
    lies STRICTLY MORE THAN ``cooldown`` trading days AFTER the top
    reference wins — the 2nd anchor always postdates the top anchor so
    the roof line runs forward in time.
    """
    lim: int = _region_limit(len(arr), cooldown)
    if lim < 0:
        return float("nan")
    region: np.ndarray = arr[: lim + 1]
    if not np.any(~np.isnan(region)):
        return float("nan")
    top_i: int = int(np.nanargmax(region))

    peaks_idx, peaks_val = _find_local_max_peaks(region)
    order = sorted(
        range(len(peaks_val)), key=lambda k: peaks_val[k], reverse=True
    )
    for k in order:
        if peaks_idx[k] - top_i > cooldown:
            return float(peaks_idx[k])

    return float("nan")


def _second_peak_min_pos_today_constrained_raw(
    arr: np.ndarray, cooldown: int
) -> float:
    """Position of the 2nd-low anchor as float, or NaN when absent.

    Mirror of ``_second_peak_max_pos_today_constrained_raw`` — the bottom
    reference is the qualifying region's argmin; candidates are
    local-minimum CLOSE troughs ranked by close value ascending, and the
    winner lies strictly more than ``cooldown`` trading days AFTER the
    bottom reference.
    """
    lim: int = _region_limit(len(arr), cooldown)
    if lim < 0:
        return float("nan")
    region: np.ndarray = arr[: lim + 1]
    if not np.any(~np.isnan(region)):
        return float("nan")
    bottom_i: int = int(np.nanargmin(region))

    troughs_idx, troughs_val = _find_local_min_troughs(region)
    order = sorted(range(len(troughs_val)), key=lambda k: troughs_val[k])
    for k in order:
        if troughs_idx[k] - bottom_i > cooldown:
            return float(troughs_idx[k])

    return float("nan")


def _init_date_columns(df: pd.DataFrame) -> None:
    """Initialize all OHLC date columns as datetime64[ns] dtype with NaT.

    DATE columns are stored as ``datetime64[ns]`` — NaT is the native
    "missing" marker for this dtype.  The final ``sanitize_for_db_insert``
    sweep already converts any remaining NaT → None for SQL NULL
    serialisation (it handles ``is_datetime64_any_dtype`` explicitly).
    """
    n: int = len(df)
    for col in OHLC_DATE_COLUMNS:
        df[col] = np.full(n, np.datetime64("NaT", "ns"))


def _map_positions_to_dates(
    rel_positions: np.ndarray,
    window_starts: np.ndarray,
    dates_arr: np.ndarray,
) -> np.ndarray:
    """Map relative (within-window) positions to absolute dates.

    Args:
        rel_positions: Float array from ``rolling().apply(raw=True)``.
            NaN indicates "no valid value in this window".
        window_starts: Int array giving the global start position of each
            rolling window (``max(0, row_idx - W + 1)``).
        dates_arr: Datetime64[ns] array of dates for all rows in the group.

    Returns:
        Datetime64[ns] array with NaT where no valid position exists.
    """
    # Convert NaN → -1, then cast valid values to int.
    # Mask NaN first to avoid undefined behaviour when casting NaN to int32.
    int_positions = np.full(len(rel_positions), -1, dtype=np.int32)
    valid_mask: np.ndarray = ~np.isnan(rel_positions)
    int_positions[valid_mask] = rel_positions[valid_mask].astype(np.int32)
    # Global position = window_start + relative_position
    global_positions = window_starts + int_positions
    # A position is valid only if the original value was NOT NaN AND
    # the resulting global index is within [0, len(dates)).
    valid_global: np.ndarray = valid_mask & (global_positions >= 0)
    # Map valid positions to dates, invalid to NaT
    result = np.full(len(rel_positions), np.datetime64("NaT", "ns"))
    result[valid_global] = dates_arr[global_positions[valid_global]]
    return result


def _map_positions_to_values(
    rel_positions: np.ndarray,
    window_starts: np.ndarray,
    values_arr: np.ndarray,
) -> np.ndarray:
    """Map relative (within-window) positions to absolute values.

    Mirror of ``_map_positions_to_dates`` but returns the float value at
    each mapped position (e.g. the close / intraday high / intraday low of
    the anchor date) instead of its date.
    """
    int_positions = np.full(len(rel_positions), -1, dtype=np.int32)
    valid_mask: np.ndarray = ~np.isnan(rel_positions)
    int_positions[valid_mask] = rel_positions[valid_mask].astype(np.int32)
    global_positions = window_starts + int_positions
    valid_global: np.ndarray = (
        valid_mask
        & (global_positions >= 0)
        & (global_positions < len(values_arr))
    )
    result = np.full(len(rel_positions), np.nan, dtype=np.float64)
    result[valid_global] = values_arr[global_positions[valid_global]]
    return result


def _anchor_line_slope(
    top_vals: np.ndarray,
    second_vals: np.ndarray,
    rel_top: np.ndarray,
    rel_second: np.ndarray,
) -> np.ndarray:
    """Slope (price per trading day) of the roof/floor line through the
    two anchors: (2nd value - top value) / (2nd pos - top pos).

    ``rel_top`` / ``rel_second`` are window-relative anchor positions
    from ``rolling().apply(raw=True)`` (NaN when the anchor is absent).
    Under the after-top semantics the 2nd anchor is more than
    ``cooldown`` trading days after the top, so the denominator is
    >= cooldown+1 wherever both anchors exist (the != 0 check is a
    defensive no-op). NaN when either anchor or either value is absent.
    """
    rt: np.ndarray = np.asarray(rel_top, dtype=np.float64)
    r2: np.ndarray = np.asarray(rel_second, dtype=np.float64)
    dp: np.ndarray = r2 - rt
    tv: np.ndarray = np.asarray(top_vals, dtype=np.float64)
    sv: np.ndarray = np.asarray(second_vals, dtype=np.float64)
    ok: np.ndarray = (
        ~np.isnan(dp) & (dp != 0) & ~np.isnan(tv) & ~np.isnan(sv)
    )
    out: np.ndarray = np.full(len(tv), np.nan, dtype=np.float64)
    out[ok] = (sv[ok] - tv[ok]) / dp[ok]
    return out


def _compute_group_ohlc_columns(
    g: pd.DataFrame,
    w: int,
    cooldown: int,
) -> pd.DataFrame:
    """Compute the 4 anchor VALUE + 4 anchor DATE columns for one group.

    REFERENCE implementation (per-window ``rolling().apply(raw=True)``)
    — NOT used by the production path (which calls
    ``ohlc_vector.compute_group_anchors_all_windows``); kept for A/B
    regression against the vectorised implementation.

    Anchor selection is CLOSE-based with two constraints, both strictly
    greater than ``cooldown`` trading days: anchor-vs-today (the window's
    last row) and (for the 2nd anchor) 2nd strictly AFTER the top:

      - top high:  argmax of close in the qualifying region; value = that
                   date's CLOSE.
      - 2nd high:  best local-max close peak in the region more than
                   ``cooldown`` days after the top; value = that date's
                   INTRADAY HIGH.
      - top low:   argmin of close in the region; value = that date's CLOSE.
      - 2nd low:   best local-min close trough more than ``cooldown``
                   days after the bottom; value = that date's INTRADAY LOW.

    Uses ``raw=True`` rolling callbacks that return float positions, then
    maps positions → (date, value) via vectorised numpy lookup.  This
    avoids the Timestamp-casting hazard of ``raw=False`` and keeps the hot
    path in fast numeric code.

    Args:
        g: DataFrame with columns [date, price, high, low], sorted by date.
        w: Rolling window size in trading days.
        cooldown: Minimum separation (in trading days) for BOTH the
                  anchor-vs-today and 2nd-vs-top constraints.

    Returns:
        DataFrame indexed by ``g``'s original index with columns:
        high_Wd, high_date_Wd, high_2nd_Wd, high_2nd_date_Wd,
        high_line_slope_Wd, low_Wd, low_date_Wd, low_2nd_Wd,
        low_2nd_date_Wd, low_line_slope_Wd.
    """
    n: int = len(g)
    positions_in_group: np.ndarray = np.arange(n, dtype=np.int32)

    # Window start for each row: max(0, pos - W + 1)
    window_starts: np.ndarray = np.maximum(0, positions_in_group - w + 1)

    # Anchor positions are selected on CLOSE; values are looked up from
    # close (top anchors) or intraday high/low (2nd anchors).
    close_s: pd.Series = g.set_index("date")["price"]
    dates_arr: np.ndarray = g["date"].to_numpy()  # datetime64[ns]
    close_arr: np.ndarray = g["price"].to_numpy(dtype=np.float64)
    high_arr: np.ndarray = g["high"].to_numpy(dtype=np.float64)
    low_arr: np.ndarray = g["low"].to_numpy(dtype=np.float64)

    peak_min_periods: int = max(3, cooldown + 2)

    # -- top high: argmax of close in the qualifying region --
    def _mk_argmax_cb(_cd: int = cooldown):
        def _cb(arr: np.ndarray) -> float:
            return _argmax_pos_today_constrained_raw(arr, _cd)
        return _cb

    rel_max = close_s.rolling(
        window=w, min_periods=1
    ).apply(_mk_argmax_cb(), raw=True).to_numpy()
    high_date_vals = _map_positions_to_dates(rel_max, window_starts, dates_arr)
    high_vals = _map_positions_to_values(rel_max, window_starts, close_arr)

    # -- top low: argmin of close in the qualifying region --
    def _mk_argmin_cb(_cd: int = cooldown):
        def _cb(arr: np.ndarray) -> float:
            return _argmin_pos_today_constrained_raw(arr, _cd)
        return _cb

    rel_min = close_s.rolling(
        window=w, min_periods=1
    ).apply(_mk_argmin_cb(), raw=True).to_numpy()
    low_date_vals = _map_positions_to_dates(rel_min, window_starts, dates_arr)
    low_vals = _map_positions_to_values(rel_min, window_starts, close_arr)

    # -- 2nd high: best local-max close peak separated from the top --
    def _mk_2nd_high_cb(_cd: int = cooldown):
        def _cb(arr: np.ndarray) -> float:
            return _second_peak_max_pos_today_constrained_raw(arr, _cd)
        return _cb

    rel_2nd_max = close_s.rolling(
        window=w, min_periods=peak_min_periods
    ).apply(_mk_2nd_high_cb(), raw=True).to_numpy()
    high_2nd_date_vals = _map_positions_to_dates(
        rel_2nd_max, window_starts, dates_arr
    )
    high_2nd_vals = _map_positions_to_values(
        rel_2nd_max, window_starts, high_arr
    )

    # -- 2nd low: best local-min close trough separated from the bottom --
    def _mk_2nd_low_cb(_cd: int = cooldown):
        def _cb(arr: np.ndarray) -> float:
            return _second_peak_min_pos_today_constrained_raw(arr, _cd)
        return _cb

    rel_2nd_min = close_s.rolling(
        window=w, min_periods=peak_min_periods
    ).apply(_mk_2nd_low_cb(), raw=True).to_numpy()
    low_2nd_date_vals = _map_positions_to_dates(
        rel_2nd_min, window_starts, dates_arr
    )
    low_2nd_vals = _map_positions_to_values(
        rel_2nd_min, window_starts, low_arr
    )

    # -- roof/floor line slopes through the two anchors --
    # Both anchor positions are window-relative, so their difference is
    # the trading-day distance between the anchors regardless of the
    # window start.
    high_slope_vals: np.ndarray = _anchor_line_slope(
        high_vals, high_2nd_vals, rel_max, rel_2nd_max
    )
    low_slope_vals: np.ndarray = _anchor_line_slope(
        low_vals, low_2nd_vals, rel_min, rel_2nd_min
    )

    result: pd.DataFrame = pd.DataFrame(
        {
            f"high_{w}d": high_vals,
            f"high_date_{w}d": high_date_vals,
            f"high_2nd_{w}d": high_2nd_vals,
            f"high_2nd_date_{w}d": high_2nd_date_vals,
            f"high_line_slope_{w}d": high_slope_vals,
            f"low_{w}d": low_vals,
            f"low_date_{w}d": low_date_vals,
            f"low_2nd_{w}d": low_2nd_vals,
            f"low_2nd_date_{w}d": low_2nd_date_vals,
            f"low_line_slope_{w}d": low_slope_vals,
        },
        index=g.index,
    )
    return result


def compute_ohlc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add today_close + rolling open/high/low/2nd-high/2nd-low columns
    *plus* the corresponding DATE columns for all windows.

    Anchor semantics (see module docstring): top anchors are the
    highest/lowest CLOSE among window dates more than 20% of the window
    before `date` (value = that date's close); 2nd anchors are the best
    local-max/min CLOSE peaks in the same region separated from the top
    anchors (value = that date's intraday high/low).  open_Wd uses the
    ``open`` column.

    open_Wd uses the GPU-accelerated ``grouped_shift`` helper.  All anchor
    value+date columns are computed per (sec_type, code) group by the
    vectorised sparse-table implementation
    (``ohlc_vector.compute_group_anchors_all_windows``).

    Args:
        df: Source DataFrame sorted by [sec_type, code, date] with columns
            [sec_type, code, date, price, open, high, low]. Must be the
            FULL per-code history.

    Returns:
        The same df with OHLC columns added in place.
    """
    if df.empty:
        for col in OHLC_COLUMNS:
            if col in OHLC_DATE_COLUMNS:
                df[col] = pd.Series(dtype="datetime64[ns]")
            else:
                df[col] = pd.Series(dtype="float64")
        return df

    grp_keys: list[str] = ["sec_type", "code"]

    # Ensure numeric conversion of price / open / high / low.
    for col in ("price", "open", "high", "low"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # today_close = price (close).
    df["today_close"] = df["price"]

    # Initialize all date columns to NaT (object dtype).
    _init_date_columns(df)

    # ------------------------------------------------------------------
    #  Phase 1 — open_Wd (GPU-accelerated grouped shift)
    # ------------------------------------------------------------------
    for w in OHLC_WINDOWS:
        open_col: str = f"open_{w}d"
        grouped_shift(
            df, grp_keys, "open",
            out_names=open_col, periods=w, sort=False,
        )

    # ------------------------------------------------------------------
    #  Phase 2 — anchor value + date columns (vectorised sparse tables)
    # ------------------------------------------------------------------
    # One call per (sec_type, code) group computes ALL windows: the
    # doubling argmax tables and interior-extrema arrays are shared
    # across windows inside compute_group_anchors_all_windows.
    cooldowns: list[int] = [
        max(1, int(w * OHLC_COOLDOWN_PCT)) for w in OHLC_WINDOWS
    ]
    for (_st, _code), g in df.groupby(grp_keys, sort=False):
        g_idx = g.index
        anchors: pd.DataFrame = compute_group_anchors_all_windows(
            g[["date", "price", "high", "low"]], OHLC_WINDOWS, cooldowns,
        )
        for oc in anchors.columns:
            df.loc[g_idx, oc] = anchors[oc].values

    return df


def sanitize_ohlc_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_spreads_detail_ohlc columns and sanitize
    for asyncpg bulk upsert (NaN/inf -> None + to_dict).

    DATE columns (``*_date_*``) are classified as non-numeric and
    excluded from the overflow-guard (they hold ``pd.Timestamp`` /
    ``pd.NaT``, not numeric values).  The final ``sanitize_for_db_insert``
    sweep converts any remaining NaT → None for proper SQL NULL
    serialisation by asyncpg.

    Operates on a DataFrame already carrying the OHLC columns (typically
    filtered to target_dates).
    """
    if df.empty:
        return []

    out_cols: list[str] = ["sec_type", "code", "date"] + list(OHLC_COLUMNS)
    out: pd.DataFrame = df[out_cols].copy()

    # Non-numeric = PK text columns + all OHLC date columns.
    non_numeric: set[str] = {"sec_type", "code", "date"} | set(OHLC_DATE_COLUMNS)
    numeric_cols: list[str] = [c for c in out_cols if c not in non_numeric]

    # Overflow guard: today_close and open/high/low can be large for
    # high-priced indices (e.g. SSE Composite ~3000). NUMERIC(18,6)
    # has a much larger range than NUMERIC(10,6) — |value| < 10^12
    # after rounding to 6dp — so overflow is unlikely. Still guard
    # as a safety net.
    nulled: dict[str, int] = {}
    for c in numeric_cols:
        before: int = int(out[c].isna().sum())
        out[c] = _null_if_overflow(out[c])
        n: int = int(out[c].isna().sum()) - before
        if n > 0:
            nulled[c] = n
    if nulled:
        total: int = sum(nulled.values())
        per: str = ", ".join(f"{c}={n}" for c, n in nulled.items())
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

async def find_ohlc_repair_dates(
    conn,
    sec_type: str,
) -> Set[datetime.date]:
    """Return the set of dates whose OHLC rows were written by an older
    pipeline version and must be recomputed (backfill repair).

    A row is "incomplete" when any of these hold:
      1. ``high_20d`` is populated but ``high_date_20d`` is NULL — the
         row predates the DATE / second-extrema columns.
      2. For ANY window W, ``high_2nd_Wd`` is populated but
         ``high_line_slope_Wd`` is NULL — the row predates the roof/floor
         line-slope columns. All windows are checked because a row can
         have a NULL 20d 2nd anchor while a larger window (60d..1275d)
         carries a stale pre-change 2nd anchor.
      3. Same as (2) on the low side.
      4. For ANY window W, ``high_2nd_date_Wd < high_date_Wd`` (or the
         low-side equivalent) — the row was written under the old
         2nd-anchor semantics that allowed the 2nd anchor BEFORE the top
         anchor. The current semantics require the roof/floor line to run
         forward in time (2nd anchor strictly after the top anchor plus
         cooldown), so such rows are recomputed. Note: the SQL slope
         backfill (``_backfill_ohlc_slopes_sql.py``) fills slopes for
         these rows too (slope is direction-invariant), which is why the
         slope-NULL predicate alone no longer catches them.

    Under the current semantics a non-NULL 2nd value always yields a
    non-NULL slope and a strictly-later 2nd date, so after repair the
    predicates never match again (idempotent detection). Rows whose
    entire window is NULL legitimately keep all columns NULL and are NOT
    flagged.

    Args:
      conn: asyncpg connection.
      sec_type: scope the detection to one sec_type.

    Returns:
      Set of dates needing (delete + recompute + reinsert).
    """
    slope_preds: list[str] = []
    for w in OHLC_WINDOWS:
        slope_preds.append(
            f"(high_2nd_{w}d IS NOT NULL AND high_line_slope_{w}d IS NULL)"
        )
        slope_preds.append(
            f"(low_2nd_{w}d IS NOT NULL AND low_line_slope_{w}d IS NULL)"
        )
        slope_preds.append(
            f"(high_2nd_date_{w}d IS NOT NULL"
            f" AND high_2nd_date_{w}d < high_date_{w}d)"
        )
        slope_preds.append(
            f"(low_2nd_date_{w}d IS NOT NULL"
            f" AND low_2nd_date_{w}d < low_date_{w}d)"
        )
    rows = await conn.fetch(
        f"SELECT DISTINCT date FROM {OHLC_TABLE} "
        f"WHERE sec_type = $1 AND ("
        f"  (high_20d IS NOT NULL AND high_date_20d IS NULL)"
        f"  OR {' OR '.join(slope_preds)}"
        f")",
        sec_type,
    )
    return {r["date"] for r in rows}


async def run_ohlc(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the OHLC-detail pipeline against the source data already
    loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the
    ``price`` (close), ``open``, ``high``, ``low`` columns are reused —
    no second DB fetch; anchor dates are selected on ``price`` while
    2nd-anchor values use the intraday ``high``/``low``). The DataFrame
    must contain the FULL per-code history (not filtered to
    target_dates) so rolling computations have enough lookback rows
    (up to 1275 trading days ≈ 5 years).

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_spreads_detail_ohlc against source identity
         tables, PLUS repair dates whose rows predate the DATE /
         second-extrema columns (deleted up-front). In force mode,
         truncate the table instead.
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
    # irrelevant here. Anchor dates are selected on ``price`` (close);
    # 2nd-anchor values use the intraday ``high``/``low``; open_Wd uses
    # ``open``.
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
            # Backfill repair: dates whose rows predate the DATE / 2nd-
            # extrema columns. Delete them up-front so the chunked COPY
            # path (which cannot upsert) can re-insert the recomputed
            # rows without PK conflicts.
            repair_st = await find_ohlc_repair_dates(conn, st)
            if repair_st:
                status: str = await conn.execute(
                    f"DELETE FROM {OHLC_TABLE} "
                    f"WHERE sec_type = $1 AND date = ANY($2)",
                    st, sorted(repair_st),
                )
                n_deleted: int = int(status.rsplit(" ", 1)[-1]) if status else 0
                print(f"    -> {st}: backfill repair — deleted "
                      f"{n_deleted:,} incomplete rows across "
                      f"{len(repair_st)} dates", flush=True)
            target_dates_per_st[st] = td_st | repair_st
            print(f"    -> {st}: {len(td_st)} missing + "
                  f"{len(repair_st)} repair dates", flush=True)
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
        sec_types=() if code_filter is not None else sec_types,
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