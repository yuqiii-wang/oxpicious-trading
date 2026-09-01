"""Wide (date × code) matrix helpers for analyze.analysis_forecasts.

The monthly aggregation engines (compute_rsi / compute_std) work on
2-D numpy matrices of shape (T, C) — T = union trading-day grid rows,
C = codes — so a whole sec_type is aggregated with vectorized passes
instead of per-code Python loops.

Pipeline per sec_type:
  1. ``build_month_specs`` — the target stat months (completed month-ends)
     and each month's inclusive trailing-window start (month-end
     minus WINDOW_YEARS + 1 day).
  2. ``build_grid`` — factorize the long frame into the (T, C) grid.
  3. ``scatter_column`` — long column → wide matrix (one fancy-index
     assignment; NaN where the code has no row on a grid date).
  4. ``build_change_matrices`` — wide forward-change matrices + validity
     + reverse flags shared by both engines.
  5. ``month_row_windows`` — per stat month, the [lo, hi) grid-row range
     of its trailing 5-year window.
  6. ``aggregate_horizon`` — per-code mean/high/low/reverse-prob stats of
     one forward horizon over a bucket mask (shared by both engines).
  7. ``split_forecast_rows`` — split computed bucket rows into the
     motivation (mov_rsi / mov_std) and result (forecast_results) dicts.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    N_MONTHS,
    REVERSE_THRESHOLD,
    RESULT_COLUMNS,
    WINDOW_YEARS,
)


# ---------------------------------------------------------------------------
#  Month specs / windows
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MonthSpec:
    """One target stat month: the completed month-end plus the inclusive
    start of its trailing window (= month-end - WINDOW_YEARS + 1 day,
    i.e. the window covers exactly WINDOW_YEARS of calendar dates)."""
    stat_month: date
    lower: date  # inclusive window start
    upper: date  # inclusive window end (== stat_month)


@dataclass(frozen=True)
class MonthWindow:
    """Resolved grid-row range [lo, hi) of one stat month's window.

    lo_ord is the window start's ABSOLUTE epoch-day ordinal (independent
    of the grid extent) — the full-window gate compares per-code first
    data ordinals against it, because the grid itself may begin after
    the nominal window start (lo clamped to 0) and row-space comparison
    would wrongly pass codes first listed at the grid start."""
    stat_month: date
    lo: int
    hi: int
    lo_ord: int


def _shift_years(d: date, years: int) -> date:
    """Calendar-year shift with Feb-29 clamping (pandas DateOffset
    semantics). Pure stdlib — no cudf.pandas proxy dispatch."""
    y = d.year + years
    try:
        return d.replace(year=y)
    except ValueError:  # Feb 29 in a non-leap target year
        return d.replace(year=y, day=28)


def build_month_specs(
    n_months: int = N_MONTHS,
    window_years: int = WINDOW_YEARS,
) -> list[MonthSpec]:
    """The last ``n_months`` COMPLETED month-ends as MonthSpec list
    (ascending, oldest first).

    The current (partial) month is excluded: its stats would change every
    day and break the month-granular incremental contract. Window lower =
    month-end - window_years + 1 day (inclusive), so the window spans
    exactly ``window_years`` of calendar dates: (M - 5y, M].

    Pure stdlib datetime — the previous pd.Timestamp/MonthBegin/
    date_range/DateOffset version triggered a dozen cudf.pandas fallbacks
    per run (Timestamp.today/normalize/date, MonthBegin, DateOffset,
    Timedelta, date_range, IndexOpsMixin.__iter__) for what is plain
    calendar arithmetic.
    """
    today = date.today()
    # Last COMPLETED month-end: first-of-current-month - 1 day (even on
    # the month's last day the current month is not yet complete).
    last_me = date(today.year, today.month, 1) - timedelta(days=1)

    specs: list[MonthSpec] = []
    y, m = last_me.year, last_me.month
    for _ in range(n_months):
        me = date(y, m, calendar.monthrange(y, m)[1])
        lower = _shift_years(me, -window_years) + timedelta(days=1)
        specs.append(MonthSpec(me, lower, me))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    specs.reverse()
    return specs


def month_row_windows(
    grid_ord: np.ndarray,
    specs: list[MonthSpec],
) -> list[MonthWindow]:
    """Resolve each spec's window to a [lo, hi) range of grid rows via
    binary search on the sorted day-ordinal grid (calendar-accurate —
    no fixed row-count approximation of the 5-year window), plus the
    window start's ABSOLUTE epoch ordinal (lo_ord) for the date-space
    full-window gate."""
    lo_ord = np.array([s.lower for s in specs], dtype="datetime64[D]")
    hi_ord = np.array([s.upper for s in specs], dtype="datetime64[D]")
    lo = np.searchsorted(grid_ord, lo_ord.astype(np.int64), side="left")
    hi = np.searchsorted(grid_ord, hi_ord.astype(np.int64), side="right")
    return [
        MonthWindow(s.stat_month, int(a), int(b), int(o))
        for s, a, b, o in zip(specs, lo, hi, lo_ord.astype(np.int64))
    ]


# ---------------------------------------------------------------------------
#  Long → wide grid
# ---------------------------------------------------------------------------

def date_ordinals(s: pd.Series) -> np.ndarray:
    """datetime64 Series → REAL int64 host ndarray of day ordinals.

    Unwraps the cudf.pandas proxy ONCE at the pandas→numpy boundary
    (``.to_numpy()`` on a proxy Series returns a proxy-subclass ndarray
    whose every downstream op dispatches into cudf).
    """
    return host_array(s.to_numpy()).astype("datetime64[D]").astype(np.int64)


def build_grid(
    df: pd.DataFrame,
) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray]:
    """Factorize the long frame into the (T, C) grid coordinates.

    Args:
        df: long frame sorted by (code, date) with columns date + code.

    Returns:
        (grid_ord, codes, didx, cidx):
        grid_ord — (T,) sorted int64 day ordinals (union date grid);
        codes    — (C,) sorted code strings;
        didx     — (N,) per-row date index into the grid;
        cidx     — (N,) per-row code index.
    """
    dord = date_ordinals(df["date"])
    grid_ord = np.unique(dord)  # sorted union grid
    # np.unique(return_inverse) gives (sorted uniques, inverse) in one
    # deterministic call on a REAL host array (unwrapped above pattern),
    # so the outputs are real ndarrays — safe for numpy fancy indexing.
    code_arr = host_array(df["code"].to_numpy())
    codes_arr, cidx = np.unique(code_arr, return_inverse=True)
    cidx = cidx.astype(np.int64, copy=False)
    didx = np.searchsorted(grid_ord, dord).astype(np.int64, copy=False)
    return grid_ord, [str(c) for c in codes_arr], didx, cidx


def first_ords_from_dates(
    first_dates: dict[str, date],
    codes: list[str],
) -> np.ndarray:
    """Per-code first data date as ABSOLUTE epoch-day ordinals (days
    since 1970-01-01 — the same unit as grid_ord / MonthWindow.lo_ord).

    The dates are the codes' TRUE first data dates (min(date) from the
    source table — NOT derivable from the fetched frame, which is
    bounded to the earliest needed window start and would clip
    long-history codes to the fetch boundary).

    Codes without a date map to the int64 sentinel (never live). Used
    to gate the monthly buckets in DATE space (NOT grid-row space —
    the grid may begin after the nominal window start, so row-space
    lo would be clamped to 0 and wrongly pass codes first listed at
    the grid start): a code enters a stat_month only once its own
    history strictly PRECEDES the window start (first data month +
    60 months = first snapshot) — a code first listed 2020-01 first
    appears in the 2025-01 snapshot, NOT 2024-12 whose window merely
    STARTS at the first data date.
    """
    sentinel = np.iinfo(np.int64).max
    first = np.full(len(codes), sentinel, dtype=np.int64)
    code_pos = {c: i for i, c in enumerate(codes)}
    epoch = date(1970, 1, 1)
    for c, d in first_dates.items():
        i = code_pos.get(c)
        if i is not None:
            first[i] = (d - epoch).days
    return first


def scatter_column(
    df: pd.DataFrame,
    col: str,
    shape: tuple[int, int],
    didx: np.ndarray,
    cidx: np.ndarray,
    dtype: np.dtype = np.float64,
) -> np.ndarray:
    """Long column → (T, C) wide matrix (one fancy-index scatter).

    float matrices are NaN-initialized (missing cell = no row that date);
    bool matrices are False-initialized. Source (code, date) pairs are
    unique (DB PKs), so no scatter collisions.
    """
    fill = np.nan if dtype == np.float64 else False
    mat = np.full(shape, fill, dtype=dtype)
    vals = host_array(df[col].to_numpy())
    mat[didx, cidx] = vals.astype(dtype, copy=False)
    return mat


def build_hype_matrix(
    episodes: pd.DataFrame,
    grid_ord: np.ndarray,
    codes: list[str],
    shape: tuple[int, int],
) -> np.ndarray:
    """(T, C) bool matrix of market-hyped (grid date, code) cells.

    An episode (code, start_date..end_date inclusive, any
    min_checkin_period) marks every grid date it spans. Episodes of codes
    outside the active universe (delisted) are ignored; interval marking
    is a small per-episode slice loop on real host ndarrays (irregular
    intervals — not vectorizable without a blow-up to calendar rows).
    """
    H = np.zeros(shape, dtype=bool)
    if episodes.empty:
        return H
    codes_arr = np.asarray(codes)
    ep_codes = host_array(episodes["code"].to_numpy())
    cidx = np.searchsorted(codes_arr, ep_codes)
    ok = cidx < len(codes_arr)
    ok[ok] = codes_arr[cidx[ok]] == ep_codes[ok]
    s = date_ordinals(episodes["start_date"])
    e = date_ordinals(episodes["end_date"])
    lo = np.searchsorted(grid_ord, s)
    hi = np.searchsorted(grid_ord, e, side="right")
    for l, h, c in zip(lo[ok].tolist(), hi[ok].tolist(), cidx[ok].tolist()):
        if h > l:
            H[l:h, c] = True
    return H


# ---------------------------------------------------------------------------
#  Forward-change wide matrices (shared by both engines)
# ---------------------------------------------------------------------------

def build_change_matrices(
    df: pd.DataFrame,
    shape: tuple[int, int],
    didx: np.ndarray,
    cidx: np.ndarray,
) -> dict[str, np.ndarray]:
    """Wide matrices derived from the next_change_{n}d columns.

    Keys (n = forward horizon in trading days):
      NC0_{n} — next_change_{n}d with NaN→0 (einsum-safe sums)
      FIN_{n} — validity bool (day has a finite n-day forward change)

    Plus the next-day reversal flags:
      DN      — 1.0 where next-day change < -REVERSE_THRESHOLD else 0.0
                (reversal after an OVERBOUGHT / upper-breach day)
      UP      — 1.0 where next-day change > +REVERSE_THRESHOLD else 0.0
                (reversal after an OVERSOLD / lower-breach day)

    NaN comparisons are False, so DN/UP are naturally 0.0 on invalid days.

    Note: max_low_change_ratio is NOT computed here — it is derived at
    write time from the row's own max/min forward changes as
    (1 + max) / (1 + min), which equals max(close[t+1..t+n]) /
    min(close[t+1..t+n]) without needing forward price windows (the
    per-month grid cannot see past the month end anyway).
    """
    mats: dict[str, np.ndarray] = {}
    for n in FORWARD_HORIZONS:
        nc = scatter_column(df, f"next_change_{n}d", shape, didx, cidx)
        fin = np.isfinite(nc)
        mats[f"NC0_{n}"] = np.where(fin, nc, 0.0)
        mats[f"FIN_{n}"] = fin

    nc1 = scatter_column(df, "next_change_1d", shape, didx, cidx)
    with np.errstate(invalid="ignore"):
        mats["DN"] = np.where(nc1 < -REVERSE_THRESHOLD, 1.0, 0.0)
        mats["UP"] = np.where(nc1 > REVERSE_THRESHOLD, 1.0, 0.0)
    return mats


def horizon_flags(
    chg: dict[str, np.ndarray],
    lo: int,
    hi: int,
) -> dict[str, np.ndarray]:
    """Per-horizon reversal flag matrices for one window slice.

    Returns (per horizon n) DN_{n} / UP_{n}: 1.0 where the n-day forward
    change < -REVERSE_THRESHOLD (reversal after an overbought / upper
    day) or > +REVERSE_THRESHOLD (reversal after an oversold / lower
    day), else 0.0. NC0_{n} is 0.0 on invalid days, so flags are
    naturally 0.0 there.
    """
    flags: dict[str, np.ndarray] = {}
    for n in FORWARD_HORIZONS:
        NC0 = chg[f"NC0_{n}"][lo:hi]
        with np.errstate(invalid="ignore"):
            flags[f"DN_{n}"] = np.where(NC0 < -REVERSE_THRESHOLD, 1.0, 0.0)
            flags[f"UP_{n}"] = np.where(NC0 > REVERSE_THRESHOLD, 1.0, 0.0)
    return flags


def apply_cooldown(mask: np.ndarray, cooldown_days: int) -> np.ndarray:
    """Suppress re-triggers within ``cooldown_days`` grid rows of the
    last ACCEPTED trigger, per column (code).

    Greedy sequential over the time axis (an accepted day depends on the
    previous accepted day), vectorized across codes: row ``t`` accepts a
    trigger day whose previous accepted trigger is more than
    ``cooldown_days`` rows earlier. Triggers inside the skip window do
    NOT restart the cooldown — the first trigger after it is accepted
    (fixed-skip semantics). Spacing is counted on the union trading-day
    grid (a code suspended during the skip window has slightly fewer of
    its own days skipped — negligible at 5 days).

    ``cooldown_days == 0`` accepts every trigger (callers may skip the
    call as an identity fast path). The cooldown restarts at each
    month-window slice — windows are independent worlds.
    """
    T, C = mask.shape
    out = np.zeros_like(mask)
    last = np.full(C, -(2**62), dtype=np.int64)
    for t in range(T):
        cand = mask[t] & (t - last > cooldown_days)
        out[t] = cand
        # np.where on the (C,) bool — cheap; the loop is T iterations of
        # a few vector ops (T ≈ 1,220 rows per 5y window).
        last = np.where(cand, t, last)
    return out


def aggregate_horizon(
    mask: np.ndarray,
    NC0: np.ndarray,
    FIN: np.ndarray,
    flag: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-code aggregates of one forward horizon over a bucket mask.

    Args:
        mask: (T, C) bucket bool matrix.
        NC0:  (T, C) n-day forward change with NaN→0 (einsum-safe).
        FIN:  (T, C) validity bool (day has a finite n-day change).
        flag: (T, C) 0/1 reversal flag for the bucket side.

    Returns (per code, arrays of len C):
        cnt — bucket days with a valid n-day forward change;
        s   — sum of the n-day changes over those days;
        hi  — max n-day change over those days (-inf when cnt == 0);
        lo  — min n-day change over those days (+inf when cnt == 0);
        rev — count of >1% reversal days among those days.
    """
    valid = mask & FIN
    cnt = valid.sum(axis=0)
    s = np.einsum("ij,ij->j", mask, NC0)  # NC0 is 0.0 on invalid days
    with np.errstate(invalid="ignore"):
        hi = np.max(np.where(valid, NC0, -np.inf), axis=0)
        lo = np.min(np.where(valid, NC0, np.inf), axis=0)
    rev = np.einsum("ij,ij->j", mask, flag)
    return cnt, s, hi, lo, rev


# ---------------------------------------------------------------------------
#  Row emission helpers
# ---------------------------------------------------------------------------

def round6(x: float) -> float | None:
    """float → rounded 6dp (NUMERIC(10,6) / NUMERIC(6,6) scale) with
    NaN/inf → None (asyncpg cannot encode NaN into these columns)."""
    x = float(x)
    return round(x, 6) if np.isfinite(x) else None


def split_forecast_rows(
    rows: list[dict],
    mov_columns: list[str],
) -> tuple[list[dict], list[dict]]:
    """Split computed bucket rows into the two write targets.

    Each computed bucket row carries the bucket keys, motivation cols,
    result cols and the allocated forecast_id. Returns:
        mov_rows    — dicts with ``mov_columns`` (mov_rsi / mov_std)
        result_rows — dicts with RESULT_COLUMNS (forecast_results)
    """
    mov_rows = [{k: r[k] for k in mov_columns} for r in rows]
    result_rows = [{k: r[k] for k in RESULT_COLUMNS} for r in rows]
    return mov_rows, result_rows
