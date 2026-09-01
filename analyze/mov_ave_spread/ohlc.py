"""Internal OHLC step for analyze.mov_ave_spread.

Rolling OHLC summary (today_close + open/high/low/second-peak-high/
second-trough-low over 7 windows: 20/60/120/255/500/750/1275 trading days)
for ETF + Index + Stock. One row per (sec_type, code, date) in
analysis.mov_ave_spreads_detail_ohlc.

For each window W ending on `date` (= "today" / the clicked date):
  - open_Wd:      open price on the W-th trading day before `date`
  - high_Wd:      top-high anchor: the MAXIMUM valid CLOSE in the 1st
                  HALF of the window — value = that anchor date's CLOSE
  - low_Wd:       top-low anchor: the MINIMUM valid CLOSE in the 1st
                  half — value = that anchor date's CLOSE
  - high_2nd_Wd:  second-high anchor: the MAXIMUM valid CLOSE in the
                  2nd HALF of the window — value = that anchor date's
                  INTRADAY HIGH (wick)
  - low_2nd_Wd:   second-low anchor: the MINIMUM valid CLOSE in the
                  2nd half — value = that anchor date's INTRADAY LOW
                  (wick)
  - high_line_slope_Wd:  slope of the roof line through the two high
                  anchors, in price units per trading day:
                  (high_2nd_Wd - high_Wd) / (trading days between the two
                  anchor dates); negative = descending roof
  - low_line_slope_Wd:   slope of the floor line through the two low
                  anchors, same formula on the low side; positive =
                  ascending floor

HALF-SPLIT ANCHORS: the window [date-W+1, date] is cut in half —
h = L // 2 with L the window length in trading-day positions (for odd L
the 2nd half gets the extra day); the 1st extreme is the max/min valid
CLOSE of the 1st half and the 2nd extreme the max/min valid CLOSE of
the 2nd half.  Ties go to the earliest date; NaN closes are skipped.
The halves are disjoint and ordered, so the 2nd anchor date is ALWAYS
strictly after the 1st anchor date wherever both exist.  No anchors
when the window has fewer than 2 positions; a half with no valid close
NULLs its anchor independently.

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
       second-extrema columns (detected via ``high_over_period IS NOT
       NULL AND high_date_over_period IS NULL``). Those rows are deleted
       up-front so the chunked COPY path can re-insert the recomputed
       rows (COPY cannot upsert).

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
per-window reference sweep is kept below as the reference for A/B
regression (``_compute_group_ohlc_columns``).
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
from _common.df_utils import (
    column_subset,
    grouped_shift,
    host_array,
    safe_columns,
)
from analyze.mov_ave_spread.helpers import null_if_overflow_counted
from analyze._common import (
    build_and_insert_chunked_df,
    upsert_analysis_identity,
)
from analyze.mov_ave_spread.config import (
    OHLC_ANALYSIS_NAME,
    OHLC_COLUMNS,
    OHLC_DATE_COLUMNS,
    OHLC_DESCRIPTION,
    OHLC_TABLE,
    OHLC_WINDOWS,
    OHLC_WIDE_COLUMNS,
    OHLC_WIDE_DATE_COLUMNS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
    ohlc_wide_columns,
)
from analyze.mov_ave_spread.ohlc_vector import (
    compute_group_anchors_all_windows,
)


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def _half_extreme_pos(
    arr: np.ndarray, lo: int, hi: int, sign: float,
) -> float:
    """WINDOW-RELATIVE position (0 = ``lo``) of the max (sign > 0) /
    min (sign < 0) valid CLOSE in ``arr[lo..hi]`` (inclusive), NaN when
    the slice holds no valid close.

    NaN closes are skipped; ties go to the earliest position (matching
    the vectorised first-occurrence range argmax).
    """
    seg: np.ndarray = arr[lo:hi + 1]
    valid: np.ndarray = ~np.isnan(seg)
    if not valid.any():
        return float("nan")
    if sign > 0:
        return float(int(np.argmax(np.where(valid, seg, -np.inf))))
    return float(int(np.argmin(np.where(valid, seg, np.inf))))


def _map_positions_to_dates(
    rel_positions: np.ndarray,
    window_starts: np.ndarray,
    dates_arr: np.ndarray,
) -> np.ndarray:
    """Map relative (within-window) positions to absolute dates.

    Args:
        rel_positions: Float array of window-relative anchor positions.
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
    from the reference sweep (NaN when the anchor is absent).  The two
    anchors live in DISJOINT window halves, so wherever both anchors
    exist the denominator is > 0.  NaN when either anchor or either
    value is absent.
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
) -> pd.DataFrame:
    """Compute the 4 anchor VALUE + 4 anchor DATE columns for one group.

    REFERENCE implementation (per-row window sweep) — NOT used by the
    production path (which calls
    ``ohlc_vector.compute_group_anchors_all_windows``); kept for A/B
    regression against the vectorised implementation.

    HALF-SPLIT ANCHOR selection (see the module docstring): the window
    [s, i] is cut in half — h = L // 2 with L = i - s + 1; the 1st
    anchor is the max (high side) / min (low side) valid CLOSE of the
    1st half [s, s+h-1] and the 2nd anchor the max/min valid CLOSE of
    the 2nd half [s+h, i].  No anchors when L < 2; a half with no
    valid close NULLs its anchor independently.

      - top high:  max close of the 1st half; value = that date's CLOSE.
      - 2nd high:  max close of the 2nd half; value = that date's
                   INTRADAY HIGH.
      - top low / 2nd low: the mirror rules on the min side.

    Uses an explicit per-row sweep over window slices that return float
    positions, then maps positions → (date, value) via vectorised numpy
    lookup.  This avoids the Timestamp-casting hazard of ``raw=False``
    and keeps the hot path in fast numeric code.

    Args:
        g: DataFrame with columns [date, price, high, low], sorted by
           date.
        w: Rolling window size in trading days.

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

    # Anchor dates are selected on the CLOSE (``price``); values are
    # looked up from close (top anchors) or intraday high/low (2nd
    # anchors).
    dates_arr: np.ndarray = host_array(g["date"].to_numpy())  # datetime64
    close_arr: np.ndarray = host_array(g["price"].to_numpy(dtype=np.float64))
    high_arr: np.ndarray = host_array(g["high"].to_numpy(dtype=np.float64))
    low_arr: np.ndarray = host_array(g["low"].to_numpy(dtype=np.float64))

    # Explicit per-row sweep: clearer diagnostics than a stateless
    # callback at zero cost here (reference-only A/B path).
    rel_max: np.ndarray = np.full(n, np.nan)
    rel_min: np.ndarray = np.full(n, np.nan)
    rel_2nd_max: np.ndarray = np.full(n, np.nan)
    rel_2nd_min: np.ndarray = np.full(n, np.nan)
    for i in range(n):
        s: int = int(window_starts[i])
        h: int = (i - s + 1) // 2
        if h < 1:
            continue
        mid: int = s + h
        # _half_extreme_pos returns the offset within [lo..hi]; the 2nd
        # half's offsets are re-based to the window start (mid - s) so
        # every rel_* position is window-relative (0 = s) — what the
        # _map_positions_to_* helpers expect.
        rel_max[i] = _half_extreme_pos(close_arr, s, mid - 1, 1.0)
        rel_2nd_max[i] = (
            _half_extreme_pos(close_arr, mid, i, 1.0) + (mid - s)
        )
        rel_min[i] = _half_extreme_pos(close_arr, s, mid - 1, -1.0)
        rel_2nd_min[i] = (
            _half_extreme_pos(close_arr, mid, i, -1.0) + (mid - s)
        )

    high_date_vals = _map_positions_to_dates(rel_max, window_starts, dates_arr)
    high_vals = _map_positions_to_values(rel_max, window_starts, close_arr)

    low_date_vals = _map_positions_to_dates(rel_min, window_starts, dates_arr)
    low_vals = _map_positions_to_values(rel_min, window_starts, close_arr)

    high_2nd_date_vals = _map_positions_to_dates(
        rel_2nd_max, window_starts, dates_arr
    )
    high_2nd_vals = _map_positions_to_values(
        rel_2nd_max, window_starts, high_arr
    )

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

    Anchor semantics (see module docstring): the window is cut in half
    and the 1st/2nd anchors are the max/min valid CLOSE of the 1st/2nd
    half (top anchors' value = that date's close; 2nd anchors' value =
    that date's intraday high/low).  open_Wd uses the ``open`` column.

    open_Wd uses the GPU-accelerated ``grouped_shift`` helper.  All anchor
    value+date columns are computed per (sec_type, code) group by the
    vectorised sparse-table implementation
    (``ohlc_vector.compute_group_anchors_all_windows``).

    Args:
        df: Source DataFrame sorted by [sec_type, code, date] with columns
            [sec_type, code, date, price, open, high, low].  Must be the
            FULL per-code history.

    Returns:
        The same df with OHLC columns added in place.
    """
    if df.empty:
        for col in OHLC_WIDE_COLUMNS:
            if col in OHLC_WIDE_DATE_COLUMNS:
                df[col] = pd.Series(dtype="datetime64[ns]")
            else:
                df[col] = pd.Series(dtype="float64")
        return df

    grp_keys: list[str] = ["sec_type", "code"]

    # Ensure numeric conversion of price / open / high / low.
    # Host-pure membership (``col in df.columns`` is a proxied
    # Index.__contains__ fallback under cudf.pandas).
    cols = set(safe_columns(df))
    for col in ("price", "open", "high", "low"):
        if col in cols:
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
    # doubling argmax table over the group's valid closes is shared
    # across windows inside compute_group_anchors_all_windows.
    #
    # cudf.pandas discipline (B-A3): the per-group compute loop stays
    # (host-numpy internals) but the writes are BATCHED — one concat of
    # the per-group anchor frames (each indexed by its group's original
    # row labels) + ONE .loc write per column, instead of
    # groups x columns proxied .loc writes (measured 140 fallbacks per
    # code; ~70k at stock-universe scale).
    anchor_frames: list[pd.DataFrame] = [
        compute_group_anchors_all_windows(
            g[["date", "price", "high", "low"]],
            OHLC_WINDOWS,
        )
        for (_st, _code), g in df.groupby(grp_keys, sort=False)
    ]
    if anchor_frames:
        # The anchor frames are REAL pandas frames (real ctor in
        # compute_group_anchors_all_windows) — proxy pd.concat would
        # attempt cuDF first and reject them (TypeError fallback with a
        # megabyte-long message). Use the REAL concat.
        real_concat = getattr(pd.concat, "_fsproxy_slow", pd.concat)
        all_anchors = real_concat(anchor_frames)
        anchor_cols = safe_columns(all_anchors)
        idx = all_anchors.index
        for oc in anchor_cols:
            df.loc[idx, oc] = host_array(all_anchors[oc].to_numpy())

    return df


def _init_date_columns(df: pd.DataFrame) -> None:
    """Initialize all (wide) OHLC date columns as datetime64[ns] with NaT.

    DATE columns are stored as ``datetime64[ns]`` — NaT is the native
    "missing" marker for this dtype. The long-format melt
    (``build_ohlc_long_frame``) keeps this dtype and the CSV COPY writer
    renders NaT as an empty field (SQL NULL).
    """
    n: int = len(df)
    for col in OHLC_WIDE_DATE_COLUMNS:
        df[col] = np.full(n, np.datetime64("NaT", "ns"))


def build_ohlc_long_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Melt the wide per-window OHLC frame to the long DB schema.

    The compute pipeline produces one (sec_type, code, date) frame with a
    column set per window (``OHLC_WIDE_COLUMNS``); the target table
    ``analysis.mov_ave_spreads_detail_ohlc`` is LONG — one row per
    (sec_type, code, date, period) with generic ``*_over_period`` columns.
    This function performs the boundary melt, fully vectorized: one
    slice + rename per window, a single ``concat`` (7 parts), and a
    whole-column overflow guard — no per-row Python loops and no row
    dicts (the CSV COPY writer consumes the frame directly).

    Overflow guard: today_close and open/high/low can be large for
    high-priced indices (e.g. SSE Composite ~3000). NUMERIC(18,6)
    comfortably holds |value| < 10^12 after 6dp rounding; guard as a
    safety net.

    Args:
        df: wide frame from ``compute_ohlc_columns`` (typically filtered
            to one date-chunk by the chunked insert path).

    Returns:
        Long frame with columns [sec_type, code, date, today_close,
        period] + OHLC_COLUMNS[1:], ready for ``csv_copy_from_frame_async``.
    """
    if df.empty:
        return pd.DataFrame(
            columns=["sec_type", "code", "date", "period"] + list(OHLC_COLUMNS)
        )

    base: list[str] = ["sec_type", "code", "date", "today_close"]
    long_names: list[str] = list(OHLC_COLUMNS[1:])  # after today_close
    parts: list[pd.DataFrame] = []
    for w in OHLC_WINDOWS:
        wide = list(ohlc_wide_columns(w))
        parts.append(
            df[base + wide]
            .rename(columns=dict(zip(wide, long_names)))
            .assign(period=w)
        )
    long_df = pd.concat(parts, ignore_index=True)
    long_df = long_df[
        ["sec_type", "code", "date", "today_close", "period"] + long_names
    ]

    # Overflow guard on the long numeric columns (whole-column ops).
    nulled: dict[str, int] = {}
    for c in ("today_close", "open_over_period", "high_over_period",
              "low_over_period", "high_2nd_over_period",
              "low_2nd_over_period", "high_line_slope_over_period",
              "low_line_slope_over_period"):
        clean, n = null_if_overflow_counted(long_df[c], max_abs=1e12, scale=6)
        long_df[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total: int = sum(nulled.values())
        per: str = ", ".join(f"{c}={n}" for c, n in nulled.items())
        print(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled)} column(s): {per}", flush=True)

    return long_df


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def find_ohlc_repair_dates(
    conn,
    sec_type: str,
) -> Set[datetime.date]:
    """Return the set of dates whose OHLC rows were written by an older
    pipeline version and must be recomputed (backfill repair).

    The table is LONG (one row per (sec_type, code, date, period)); a
    per-period row is "incomplete" when any of these hold:
      1. ``high_over_period`` is populated but
         ``high_date_over_period`` is NULL — the row predates the DATE /
         second-extrema columns.
      2. ``high_2nd_over_period`` is populated but
         ``high_line_slope_over_period`` is NULL — the row predates the
         roof/floor line-slope columns.
      3. Same as (2) on the low side.
      4. The 2nd anchor date lies BEFORE the top anchor date — the row
         predates the half-split rule (the 2nd half is strictly after
         the 1st, so the stored 2nd date can never precede the top
         date).  NOTE: a new-logic row CAN legitimately have a 2nd
         anchor without a top anchor (the 1st half holds no valid
         close), so that combination is NOT a legacy marker and is not
         flagged.

    Rows whose entire period window is NULL legitimately keep all
    columns NULL and are NOT flagged.

    Args:
      conn: asyncpg connection.
      sec_type: scope the detection to one sec_type.

    Returns:
      Set of dates needing (delete + recompute + reinsert).
    """
    rows = await conn.fetch(
        f"SELECT DISTINCT date FROM {OHLC_TABLE} "
        f"WHERE sec_type = $1 AND ("
        f"  (high_over_period IS NOT NULL AND high_date_over_period IS NULL)"
        f"  OR (high_2nd_over_period IS NOT NULL"
        f"      AND high_line_slope_over_period IS NULL)"
        f"  OR (low_2nd_over_period IS NOT NULL"
        f"      AND low_line_slope_over_period IS NULL)"
        f"  OR (high_2nd_date_over_period IS NOT NULL"
        f"      AND high_date_over_period IS NOT NULL"
        f"      AND high_2nd_date_over_period < high_date_over_period)"
        f"  OR (low_2nd_date_over_period IS NOT NULL"
        f"      AND low_date_over_period IS NOT NULL"
        f"      AND low_2nd_date_over_period < low_date_over_period)"
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
    no second DB fetch; anchor dates are the per-window-half max/min
    close while 2nd-anchor values use the intraday ``high``/``low``).
    The DataFrame must contain the FULL per-code history (not filtered
    to target_dates) so rolling computations have enough lookback rows
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
    # many extra columns (other MAs, slopes, stds, trading_amt_*) that
    # are irrelevant here. Anchor dates are selected on ``price``
    # (close); 2nd-anchor values use the intraday ``high``/``low``;
    # open_Wd uses ``open``.
    needed_cols = [
        "sec_type", "code", "date", "price", "open", "high", "low",
    ]
    available = column_subset(df, needed_cols)
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
        if sec_type is not None:
            # Per-sec_type scope: DELETE only this sec_type's rows — the
            # parent loop calls run_ohlc once per sec_type, so a whole-
            # table TRUNCATE here would wipe the previous sec_type's
            # freshly recomputed rows.
            print(f"\n[o0/3] Force mode: deleting {sec_type} rows from "
                  "mov_ave_spreads_detail_ohlc...", flush=True)
            status: str = await conn.execute(
                f"DELETE FROM {OHLC_TABLE} WHERE sec_type = $1", sec_type,
            )
            n_del: int = int(status.rsplit(" ", 1)[-1]) if status else 0
            print(f"    -> deleted {n_del:,} rows; will recompute all "
                  f"{sec_type} rows", flush=True)
        else:
            print("\n[o0/3] Force mode: truncating "
                  "mov_ave_spreads_detail_ohlc...", flush=True)
            await truncate_table_async(conn, OHLC_TABLE)
            print("    -> truncated; will recompute all rows", flush=True)
        target_dates_union: Optional[Set] = None
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
        # datetime64 ndarray comparison — isin with a python-date SET
        # against a datetime64 column matches nothing (same pitfall as
        # fetch.py's incremental filter); convert to a datetime64 ndarray
        # so the hash comparison hits.
        td64 = pd.to_datetime(sorted(target_dates_union)).values
        ohlc_df = ohlc_df[ohlc_df["date"].isin(td64)].reset_index(drop=True)
        print(f"    -> incremental filter: {len(ohlc_df):,} of {n_before:,} "
              f"rows are in target_dates_union", flush=True)

    if ohlc_df.empty:
        print("    -> no rows to upsert; skipping OHLC upsert.", flush=True)
        return

    # ---- Step 2: build + insert (chunked by date) -------------------
    # The compute output is WIDE (one column set per window); the table is
    # LONG (one row per (sec_type, code, date, period)). build_fn melts
    # each date-bounded chunk to the long frame and the CSV COPY writer
    # inserts it — no per-row dicts, C-level client encoding.
    print(f"\n[o2/3] Building + inserting {len(ohlc_df):,} wide rows "
          f"(x{len(OHLC_WINDOWS)} periods = "
          f"{len(ohlc_df) * len(OHLC_WINDOWS):,} long rows) in "
          f"date-bounded chunks via CSV COPY...", flush=True)
    n = await build_and_insert_chunked_df(
        conn, pool, ohlc_df,
        build_ohlc_long_frame,
        table_name=OHLC_TABLE,
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
