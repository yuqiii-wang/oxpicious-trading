"""Internal OHLC step for analysis.mov_ave_spread.

Rolling roof/floor anchor summary (today_close + open/high/low/second-
peak-high/second-trough-low over 7 windows: 20/60/120/255/500/750/1275
trading days, plus the change/days since the last high anchor and the
last low anchor, one pair per side) for
ETF + Index + Stock. One row per (sec_type, code, date, period) in
analysis.mov_ave_spreads_roof_and_floor_lines (renamed 2026-10 from
mov_ave_spreads_detail_ohlc).

For each window W ending on `date` (= "today" / the clicked date):
  - open_Wd:      open price on the W-th trading day before `date`
  - high_Wd:      top-high anchor: the MAXIMUM valid CLOSE in the 1st
                  HALF of the window — value = that anchor date's CLOSE
  - low_Wd:       top-low anchor: the MINIMUM valid CLOSE in the 1st
                  half — value = that anchor date's CLOSE
  - high_2nd_Wd:  second-high anchor: the MAXIMUM valid CLOSE in the
                  dynamic range [top_high_pos + gap, window_end] —
                  value = that anchor date's INTRADAY HIGH (wick)
  - low_2nd_Wd:   second-low anchor: the MINIMUM valid CLOSE in the
                  dynamic range [top_low_pos + gap, window_end] —
                  value = that anchor date's INTRADAY LOW (wick)
  - high_line_slope_Wd:  slope of the roof line through the two high
                  anchors, in price units per trading day:
                  (high_2nd_Wd - high_Wd) / (trading days between the two
                  anchor dates); negative = descending roof
  - low_line_slope_Wd:   slope of the floor line through the two low
                  anchors, same formula on the low side; positive =
                  ascending floor
  - change_since_last_high_Wd: today_close MINUS the value of the LAST
                  HIGH anchor — the later-dated of the two high anchors
                  (the 2nd whenever present, else the 1st; the
                  constructive gap guarantees the 2nd anchor is strictly
                  after the 1st, so there is never a tie); sign encodes
                  direction (negative = below the roof anchor, positive
                  = above it)
  - days_since_last_high_Wd: trading days between `date` and that last
                  high anchor date (INTEGER in the DB)
  - change_since_last_low_Wd / days_since_last_low_Wd: the mirror pair
                  on the low side (positive = above the floor anchor)

HALF-SPLIT ANCHORS + DYNAMIC 2nd RANGE: the window [date-W+1, date]
is cut in half — h = L // 2 with L the window length in trading-day
positions (for odd L the 2nd half gets the extra day).  The 1st
extreme is the max/min valid CLOSE of the 1st half.  Ties go to the
earliest date; NaN closes are skipped.

The 2nd extreme is NOT confined to the 2nd half — instead its search
range starts at [1st_anchor_position + gap, window_end] where
gap = ohlc_second_gap_td(W) = ceil(0.20*W) with a 20-trading-day floor
for W >= 60 (config.py), so the time-distance gap is enforced
CONSTRUCTIVELY.  This means the 2nd anchor can be anywhere from the
gap mark after the 1st anchor to the window end (not just the 2nd
half).  When the 1st anchor is too close to the window end
(1st_pos + gap > window_end), only the 2nd
anchor is NULLed while the 1st anchor stays valid.  Both values
(1st = close, 2nd = intraday high/low) and slopes are recorded only
when BOTH anchors exist.

MIGRATION NOTE (2026-09): the gap was ceil(0.10*W) before.  SEMANTICS
CHANGE — rows written by the 10% rule are not detectable by
find_ohlc_repair_dates and must be recomputed via a --force rebuild
of this OHLC step.

MIGRATION NOTE (2026-10): the table was RENAMED to
analysis.mov_ave_spreads_roof_and_floor_lines, and the former
change/days_since_last_extreme pair (move since the latest-dated of
the FOUR anchors) was SPLIT into the per-side
change/days_since_last_high + change/days_since_last_low pairs; the
rename DROPped the old table, so a full (incremental) rebuild of every
sec_type repopulates it.

today_close is the close price on `date` (COALESCE(adj_close, close) for
ETFs; close for index/stock). NOT NULL.

Source: the same source DataFrame already loaded by the parent
mov_ave_spread.fetch_source_data — reuses the same DataFrame, no second
DB round-trip. Anchor DATES are selected on the CLOSE price (``price``
column); top-anchor VALUES are the close on those dates while 2nd-anchor
VALUES are the intraday high/low on those dates (``high``/``low`` columns).
open_Wd uses the open column (fetched from stats.*_basic_stats +
stats.*_adjustment).

This module is an INTERNAL step of analysis.mov_ave_spread — it is invoked
from __main__.py after the detail table has been
repopulated, reusing the same DB connection + source DataFrame. It is NOT
a standalone runnable.

Incremental mode (``force=False``):
  Two kinds of target dates are (re)computed and upserted:
    1. Missing dates — present in source identity tables but NOT yet in
       analysis.mov_ave_spreads_roof_and_floor_lines (per-sec_type check).
    2. Repair dates — dates whose existing rows predate the DATE /
       second-extrema / last-high/low columns (detected via
       ``high_over_period IS NOT NULL AND high_date_over_period IS
       NULL`` etc. — see find_ohlc_repair_dates). Those rows are deleted
       up-front so the chunked COPY path can re-insert the recomputed
       rows (COPY cannot upsert).

  The OHLC columns require up to 1275 prior rows per code, so the FULL
  per-code history (already in the parent DataFrame) is used and the
  result is filtered to target_dates before upsert.

Force mode (``force=True``):
  Truncate analysis.mov_ave_spreads_roof_and_floor_lines, then recompute
  and insert all rows for the active universe.

GPU acceleration: the open_Wd shift computation uses the shared
``grouped_shift`` helper.  All anchor value+date computations run
through the vectorised implementation in ``_common.ohlc_vector``
(pandas/cuDF Series ops per group: positions, valid counts, gathers
and metrics; the doubling-argmax sparse table is the single
sanctioned host-numpy core — no ``rolling().apply`` UDFs anywhere).
The former per-window reference sweep is kept below as the reference
for A/B regression (``_compute_group_ohlc_columns``).
"""
from __future__ import annotations

import datetime
from typing import Set

import numpy as np
import pandas as pd

from _common.db_commons import chunked_purge_async
from _common.df_utils import (
    grouped_shift,
    host_array,
    safe_columns,
)
from analysis.mov_ave_spread._common import (
    null_if_overflow_counted,
    run_table_step,
    sec_type_code,
)
from analysis.mov_ave_spread.config import (
    OHLC_ANALYSIS_NAME,
    OHLC_COLUMNS,
    OHLC_DESCRIPTION,
    OHLC_TABLE,
    OHLC_WINDOWS,
    OHLC_WIDE_COLUMNS,
    OHLC_WIDE_DATE_COLUMNS,
    ohlc_second_gap_td,
    ohlc_wide_columns,
)
from analysis.mov_ave_spread._common.ohlc_vector import (
    compute_group_anchors_all_windows,
)

import logging
logger = logging.getLogger(__name__)


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
    anchors are separated by at least the constructive gap
    (ohlc_second_gap_td), so wherever both anchors exist the
    denominator is > 0.  NaN when either anchor or either value is
    absent.
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
    the DYNAMIC range [1st_pos + gap, i] (gap = ohlc_second_gap_td(w);
    not confined to the 2nd half).  No anchors when L < 2; a half with
    no valid close NULLs its anchor independently.

      - top high:  max close of the 1st half; value = that date's CLOSE.
      - 2nd high:  max close of the dynamic range; value = that date's
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
        low_2nd_date_Wd, low_line_slope_Wd,
        change_since_last_high_Wd, days_since_last_high_Wd,
        change_since_last_low_Wd, days_since_last_low_Wd.
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
    # Two-phase computation: 1st anchor in the 1st half, then 2nd
    # anchor from the DYNAMIC range [top + gap, b] — the gap
    # (ohlc_second_gap_td(w)) is enforced constructively (no
    # post-filter needed).
    rel_max: np.ndarray = np.full(n, np.nan)
    rel_min: np.ndarray = np.full(n, np.nan)
    rel_2nd_max: np.ndarray = np.full(n, np.nan)
    rel_2nd_min: np.ndarray = np.full(n, np.nan)
    gap: int = ohlc_second_gap_td(w)
    for i in range(n):
        s: int = int(window_starts[i])
        h: int = (i - s + 1) // 2
        if h < 1:
            continue
        mid: int = s + h
        # Phase 1 — 1st anchor in the 1st half [s, mid-1]
        rel_max[i] = _half_extreme_pos(close_arr, s, mid - 1, 1.0)
        rel_min[i] = _half_extreme_pos(close_arr, s, mid - 1, -1.0)
        # Convert to GLOBAL positions for Phase 2 search range
        if not np.isnan(rel_max[i]):
            top_max_abs: int = s + int(rel_max[i])
            lo2_max: int = top_max_abs + gap
            if lo2_max <= i:
                offset = _half_extreme_pos(close_arr, lo2_max, i, 1.0)
                # offset = rel. position within [lo2_max, i]; convert to
                # window-relative (offset from s): offset + (lo2_max - s)
                rel_2nd_max[i] = offset + (lo2_max - s)
        if not np.isnan(rel_min[i]):
            top_min_abs: int = s + int(rel_min[i])
            lo2_min: int = top_min_abs + gap
            if lo2_min <= i:
                offset = _half_extreme_pos(close_arr, lo2_min, i, -1.0)
                rel_2nd_min[i] = offset + (lo2_min - s)

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
    # window start.  The gap (ohlc_second_gap_td(w)) is enforced
    # constructively (2nd anchor searched from top + gap to b), so
    # sec - top >= gap wherever both exist.
    high_slope_vals: np.ndarray = _anchor_line_slope(
        high_vals, high_2nd_vals, rel_max, rel_2nd_max
    )
    low_slope_vals: np.ndarray = _anchor_line_slope(
        low_vals, low_2nd_vals, rel_min, rel_2nd_min
    )

    # -- change / days since the LAST anchor of EACH side (reference
    # form) -- Same rule as ohlc_vector._since_last_anchor_metrics: the
    # later-dated of the side's two anchors — the 2nd whenever present
    # (its value is the anchor date's INTRADAY high/low), else the 1st
    # (value = the anchor date's close).  rel_* are window-RELATIVE
    # positions; convert to global row positions via window_starts so
    # the trading-day distance is a plain subtraction.  The value arrays
    # are already row-aligned per-row anchor values, so the winner's
    # value is a per-side np.where over the pair (no gather needed).
    def _since_last_side(
        rel_top: np.ndarray,
        rel_sec: np.ndarray,
        top_vals: np.ndarray,
        sec_vals: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        def glob(rel: np.ndarray) -> np.ndarray:
            # NaN rel (absent anchor) must stay NEGATIVE after the
            # global mapping — window_starts + (-1) would be a
            # valid-looking phantom position one row before the window.
            return np.where(
                np.isnan(rel),
                -1,
                window_starts + np.nan_to_num(rel).astype(np.int64),
            )
        g_top: np.ndarray = glob(rel_top)
        g_sec: np.ndarray = glob(rel_sec)
        last: np.ndarray = np.where(g_sec >= 0, g_sec, g_top)
        ok: np.ndarray = last >= 0
        use_sec: np.ndarray = g_sec >= 0
        # top_vals / sec_vals are ROW-ALIGNED per-row anchor values, so
        # the winner's value is a per-row selection (NO positional
        # gather — indexing them by `last` would read another row's
        # anchor value; the vectorised path gathers instead because it
        # holds the underlying position-indexed arrays).
        value: np.ndarray = np.where(use_sec, sec_vals, top_vals)
        change: np.ndarray = np.where(ok, close_arr - value, np.nan)
        days: np.ndarray = np.where(
            ok,
            (positions_in_group - last).astype(np.float64),
            np.nan,
        )
        return change, days

    chg_h, days_h = _since_last_side(rel_max, rel_2nd_max, high_vals, high_2nd_vals)
    chg_l, days_l = _since_last_side(rel_min, rel_2nd_min, low_vals, low_2nd_vals)

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
            f"change_since_last_high_{w}d": chg_h,
            f"days_since_last_high_{w}d": days_h,
            f"change_since_last_low_{w}d": chg_l,
            f"days_since_last_low_{w}d": days_l,
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
    renders NaT as an empty field (SQL NULL). The host datetime64[ns]
    buffer init is deliberate: cudf's scalar-NaT setitem infers a STR
    column (probe: 2026-10-02 single-code A/B run), which then rejects
    the anchor-date .loc writes.
    """
    n: int = len(df)
    for col in OHLC_WIDE_DATE_COLUMNS:
        df[col] = np.full(n, np.datetime64("NaT", "ns"))


def build_ohlc_long_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Melt the wide per-window OHLC frame to the long DB schema.

    The compute pipeline produces one (sec_type, code, date) frame with a
    column set per window (``OHLC_WIDE_COLUMNS``); the target table
    ``analysis.mov_ave_spreads_roof_and_floor_lines`` is LONG — one row per
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
              "low_line_slope_over_period", "change_since_last_high",
              "change_since_last_low"):
        clean, n = null_if_overflow_counted(long_df[c], max_abs=1e12, scale=6)
        long_df[c] = clean
        if n > 0:
            nulled[c] = n
    if nulled:
        total: int = sum(nulled.values())
        per: str = ", ".join(f"{c}={n}" for c, n in nulled.items())
        logger.info(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled)} column(s): {per}")

    # days_since_last_high/_low are INTEGER in the DB.  They are float64
    # (NaN-capable) through the compute; render to integer STRINGS at
    # this emit boundary — under cudf.pandas both Int64 and object-int
    # columns come back from to_numpy() as float64 ("3.0"), which
    # PostgreSQL's INTEGER COPY input rejects, while the string column
    # renders "3" / "" (NULL) (probe: temp_scripts/probe_csv_int64_days.py).
    for c in ("days_since_last_high", "days_since_last_low"):
        days_vals: np.ndarray = host_array(long_df[c].to_numpy())
        days_ok: np.ndarray = ~np.isnan(days_vals)
        days_str: np.ndarray = np.full(len(days_vals), None, dtype=object)
        days_str[days_ok] = days_vals[days_ok].astype("int64").astype(str)
        long_df[c] = days_str

    return long_df


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__; the
#  skeleton lives in _common.steps.run_table_step)
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
      5. Any anchor DATE is populated but either ``days_since_last_high``
         or ``days_since_last_low`` is NULL — the row predates the
         change/days-since-last-high/low columns (2026-10 split of the
         former change/days_since_last_extreme pair).  days depends only
         on the anchor POSITIONS, so a new-logic row has it populated
         whenever the side's anchor exists (the change columns may
         legitimately be NULL when the winning anchor's intraday value
         is missing, so THEY are not the marker).

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
        f"  OR ((high_date_over_period IS NOT NULL"
        f"      OR low_date_over_period IS NOT NULL"
        f"      OR high_2nd_date_over_period IS NOT NULL"
        f"      OR low_2nd_date_over_period IS NOT NULL)"
        f"      AND (days_since_last_high IS NULL"
        f"           OR days_since_last_low IS NULL))"
        f")",
        sec_type_code(sec_type),
    )
    return {r["date"] for r in rows}


async def _detect_ohlc_repairs(conn, st: str) -> Set[datetime.date]:
    """detect_fn hook — backfill repair dates whose rows predate the
    DATE / 2nd-extrema / last-high-low columns. The stale rows are
    deleted up-front so the chunked COPY path (which cannot upsert) can
    re-insert the recomputed rows without PK conflicts."""
    repair_st = await find_ohlc_repair_dates(conn, st)
    if repair_st:
        n_deleted: int = await chunked_purge_async(
            conn, OHLC_TABLE,
            where_sql="sec_type = $1 AND date = ANY($2)",
            params=(sec_type_code(st), sorted(repair_st)),
        )
        logger.info(f"    -> {st}: backfill repair — deleted "
              f"{n_deleted:,} incomplete rows across "
              f"{len(repair_st)} dates")
    return repair_st


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

    Pipeline (see _common.steps.run_table_step)
      0. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_spreads_roof_and_floor_lines against source
         identity tables, PLUS repair dates whose rows predate the DATE /
         second-extrema columns (purged up-front via the detect_fn hook).
      1. Compute today_close + rolling OHLC columns over the FULL
         per-code history, then filter to target_dates.
      2. Melt each chunk to the long schema (build_ohlc_long_frame) and
         CSV-COPY it into the table.
      3. Upsert analysis.analysis_identity registry.
    """
    await run_table_step(
        conn, df,
        title="ROOF_AND_FLOOR_LINES (internal step of mov_ave_spread)",
        tag="o",
        table=OHLC_TABLE,
        analysis_name=OHLC_ANALYSIS_NAME,
        detail_name="mov_ave_spreads_roof_and_floor_lines",
        description=OHLC_DESCRIPTION,
        needed_cols=[
            "sec_type", "code", "date", "price", "open", "high", "low",
        ],
        compute_fn=compute_ohlc_columns,
        build_df_fn=build_ohlc_long_frame,
        detect_fn=_detect_ohlc_repairs,
        force=force,
        pool=pool,
        max_concurrent=max_concurrent,
        sec_type=sec_type,
        code_filter=code_filter,
        label="mov_ave_spreads_roof_and_floor_lines",
    )
