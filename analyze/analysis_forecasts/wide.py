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
  6. ``aggregate_horizons_sparse`` — batched per-(code, config) mean/
     std/high/low/reverse-prob stats of ALL forward horizons over the SPARSE
     trigger-cell lists of a stacked (T, C, K) bucket mask (shared by
     both engines; bincount/reduceat — work scales with the trigger
     count, not the dense tensor).
  7. ``build_result_rows`` — expand one batch's gathered aggregates into
     the forecast_results fields of its emitted rows (vectorized — no
     per-row scalar rounding calls).
  8. ``split_forecast_rows`` — split computed bucket rows into the
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
    MM_HORIZONS,
    N_MONTHS,
    PERIOD_FOR_HORIZON,
    REVERSE_THRESHOLD,
    REVERSE_THRESHOLD_MODE,
    REVERSE_THRESHOLD_STD_K,
    REVERSE_THRESHOLD_STD_MIN_DAYS,
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

    Note: max_low_change_ratio is NOT computed here — it is derived at
    write time from the row's own max/min forward changes as
    (1 + max) / (1 + min): the best-to-worst n-day ENDPOINT outcome
    ratio across the bucket's trigger days (the extrema generally come
    from DIFFERENT trigger days — NOT a within-window path swing).
    """
    mats: dict[str, np.ndarray] = {}
    for n in FORWARD_HORIZONS:
        nc = scatter_column(df, f"next_change_{n}d", shape, didx, cidx)
        fin = np.isfinite(nc)
        mats[f"NC0_{n}"] = np.where(fin, nc, 0.0)
        mats[f"FIN_{n}"] = fin
    return mats


# ---------------------------------------------------------------------------
#  Adaptive reverse threshold (per code, stat month, horizon)
# ---------------------------------------------------------------------------

def window_sigmas(
    NC0s: dict[int, np.ndarray],
    FINs: dict[int, np.ndarray],
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    """Per-horizon population σ and valid-day count of the window's
    forward changes, per code — (C,) arrays.

    σ is the dispersion of the n-day forward changes over ALL of the
    code's window days (the base_rates population — NOT the bucket
    days), the same quantity base_ave_change averages over. NaN σ where
    the code has no valid window day.
    """
    sigma: dict[int, np.ndarray] = {}
    cnts: dict[int, np.ndarray] = {}
    for n in FORWARD_HORIZONS:
        fin = FINs[n]
        cnt = fin.sum(axis=0)
        # NC0 is 0.0 on invalid days — masked sums equal valid-day sums
        # (same trick as aggregate_horizons_sparse).
        g = np.where(fin, NC0s[n], 0.0)
        s = g.sum(axis=0)
        s2 = (g * g).sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            var = s2 / cnt - (s / cnt) ** 2
        sig = np.sqrt(np.maximum(var, 0.0))
        sigma[n] = np.where(cnt > 0, sig, np.nan)
        cnts[n] = cnt
    return sigma, cnts


def reverse_thresholds(
    sigma: dict[int, np.ndarray],
    cnts: dict[int, np.ndarray],
) -> dict[int, np.ndarray]:
    """Per-horizon (C,) reversal bar for one stat month's window.

    "std" mode: thr[n] = REVERSE_THRESHOLD_STD_K[n] · σ_n — adaptive per
    code/horizon (no look-ahead: the window ends at the stat month).
    "fixed" mode or degenerate σ (non-finite / ≤ 0 / fewer than
    REVERSE_THRESHOLD_STD_MIN_DAYS valid days): the legacy constant
    REVERSE_THRESHOLD. Returned arrays are finite everywhere, so
    threshold comparisons never see NaN.
    """
    thr: dict[int, np.ndarray] = {}
    for n in FORWARD_HORIZONS:
        if REVERSE_THRESHOLD_MODE != "std":
            thr[n] = np.full(cnts[n].shape, REVERSE_THRESHOLD)
            continue
        k = REVERSE_THRESHOLD_STD_K[n]
        ok = (
            np.isfinite(sigma[n])
            & (sigma[n] > 0)
            & (cnts[n] >= REVERSE_THRESHOLD_STD_MIN_DAYS)
        )
        thr[n] = np.where(ok, k * sigma[n], REVERSE_THRESHOLD)
    return thr


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


# Per-horizon aggregate bundle returned by ``aggregate_horizons_sparse``
# (each member a (C, P) array): occurrence counts, sum of changes, sum of
# SQUARED changes, max / min change (None at the next-day horizon), and
# reversal count.
HorizonAgg = tuple[np.ndarray, np.ndarray, np.ndarray,
                   np.ndarray | None, np.ndarray | None, np.ndarray]


def aggregate_horizons_sparse(
    st: np.ndarray,
    sc: np.ndarray,
    flat: np.ndarray,
    C: int,
    P: int,
    side: str,
    NC0s: dict[int, np.ndarray],
    FINs: dict[int, np.ndarray],
    thr_n: dict[int, np.ndarray],
) -> dict[int, HorizonAgg]:
    """Per-(code, config) aggregates of ALL forward horizons over the
    SPARSE trigger cells of one (side, hyped) subset.

    Vectorized replacement of the legacy per-config 2-D
    ``aggregate_horizon``: the subset is a list of trigger cells (row,
    col, flat = col·P + config) instead of a dense (T, C, P) tensor, so
    every reduction scales with the ACTUAL trigger count E (bucket
    density ≈ 1–25%) instead of T·C·P. All groups are accumulated with
    np.bincount; the max/min forward-change extrema (MM_HORIZONS only —
    the next-day horizon has none) use one argsort of the flat group ids
    + np.maximum/minimum.reduceat over the group-contiguous sorted cells.

    Args:
        st:   (E,) grid-row index of each subset cell (ASCENDING order
              after the caller's stable argsort of ``flat``).
        sc:   (E,) code index of each subset cell (same order).
        flat: (E,) group id = code·P + config, ASCENDING (the sort key).
        C:    number of codes.
        P:    number of configs in this side batch.
        side: bucket side — "top"/"upper" reverse on change < −thr,
              "bottom"/"lower" on change > +thr, with thr = the code's
              adaptive reverse threshold for that horizon
              (reverse_thresholds output).
        NC0s: per horizon n — (T, C) n-day forward change, 0.0 on
              invalid days (build_change_matrices), so unweighted sums
              over all cells are the valid-day sums.
        FINs: per horizon n — (T, C) validity bool (finite n-day change).
        thr_n: per horizon n — (C,) reversal bar (reverse_thresholds).

    Returns (per horizon n, each (C, P)) — a HorizonAgg bundle:
        cnt — bucket days with a valid n-day forward change (int);
        s   — sum of the n-day changes over those days;
        s2  — sum of SQUARED n-day changes over those days (invalid days
              contribute 0.0 — NC0 semantics), the E[x²] half of the
              std_change numerator sqrt(E[x²] − E[x]²);
        hi  — max n-day change (-inf where cnt == 0; None for the
              next-day horizon — no MM columns);
        lo  — min n-day change (+inf where cnt == 0; None likewise);
        rev — count of reversal days (change beyond the code's bar
              against the bucket side) among those days (int).
    """
    CP = C * P
    # Group scaffolding over the group-ascending cells: one sort shared
    # by every horizon's extrema reduction.
    bounds = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.concatenate(([0], bounds))
    gid = flat[starts]
    rev_top = side in ("top", "upper")

    out: dict[int, HorizonAgg] = {}
    for n in FORWARD_HORIZONS:
        g = NC0s[n][st, sc]          # 0.0 on invalid days (NC0 semantics)
        v = FINs[n][st, sc]
        cnt = np.bincount(flat[v], minlength=CP)
        s = np.bincount(flat, weights=g, minlength=CP)
        # g is 0.0 on invalid days, so the all-cell squared sum equals
        # the valid-day squared sum (the std E[x²] pass — same trick as
        # the mean sum above).
        s2 = np.bincount(flat, weights=g * g, minlength=CP)
        # Reversal at a cell: NC0 is 0.0 on invalid days, so the
        # threshold compare is False there — matches the legacy dense
        # 0/1 flag einsum exactly.
        thr_cell = thr_n[n][sc]
        rv = (g < -thr_cell) if rev_top else (g > thr_cell)
        rev = np.bincount(flat[rv], minlength=CP)
        if n in MM_HORIZONS:
            hi = np.full(CP, -np.inf)
            lo = np.full(CP, np.inf)
            # Groups with no valid cell keep the ±inf default (the
            # legacy where(..., ±inf).max semantics).
            hi[gid] = np.maximum.reduceat(np.where(v, g, -np.inf), starts)
            lo[gid] = np.minimum.reduceat(np.where(v, g, np.inf), starts)
            out[n] = (cnt.reshape(C, P), s.reshape(C, P), s2.reshape(C, P),
                      hi.reshape(C, P), lo.reshape(C, P),
                      rev.reshape(C, P))
        else:
            out[n] = (cnt.reshape(C, P), s.reshape(C, P), s2.reshape(C, P),
                      None, None, rev.reshape(C, P))
    return out


def _round_none(arr: np.ndarray) -> list[float | None]:
    """float array → rounded 6dp list with non-finite → None (the
    legacy per-row round6 semantics, vectorized)."""
    r = np.round(np.where(np.isfinite(arr), arr, np.nan), 6)
    return [None if x != x else x for x in r.tolist()]


def build_result_rows(
    agg: dict[int, HorizonAgg],
    kk: np.ndarray,
    ii: np.ndarray,
    base: list[dict],
    thr_n: dict[int, np.ndarray],
) -> list[dict]:
    """Expand one emit batch into (4 × R) result payload dicts — one per
    (bucket × period) combination. Each dict carries the motivation
    fields + config + period + the CONSOLIDATED forecast_results
    columns (no period suffix; the ``period`` key carries that role).

    forecast_id is NOT assigned here — callers allocate one per bucket
    and share it across the 4 period rows (1:4 mov → forecast_results).

    Args:
        agg: aggregate_horizons_sparse output.
        kk:  (R,) config axis of the emit positions.
        ii:  (R,) code axis of the emit positions.
        base: (R,) motivation dicts (bucket keys + config JSONB).
        thr_n: per horizon n — (C,) reversal bar (reverse_thresholds);
              emitted as the row's ``reverse_threshold`` (the bar that
              row's reverse_prob was computed against).

    Returns:
        (4·R,) dicts — 4 period rows per bucket (next → 5d → 20d → 60d),
        period-major (all 4 periods of bucket 0, then all 4 of bucket 1,
        ...) so the caller can stride by 4 to group periods per bucket.
    """
    R = kk.size
    # First gather all horizon payloads (vectorized per horizon)...
    horizon_payloads: dict[int, dict[str, list | float | None]] = {}
    for n in FORWARD_HORIZONS:
        period = PERIOD_FOR_HORIZON[n]
        cnt, s, s2, hi, lo, rev = agg[n]
        cn = cnt[ii, kk]          # (R,) occurrence counts
        pos = cn > 0

        mean_raw = np.divide(
            s[ii, kk], cn, out=np.full(R, np.nan), where=pos)
        ave = _round_none(mean_raw)
        # Population std over the SAME valid days as ave_change:
        # sqrt(E[x²] − E[x]²), floored at 0 (rounding-guard). s2 has
        # invalid days contributing 0 and cn is the valid-day count, so
        # s2/cn is exactly E[x²] over valid days. pos == False keeps the
        # NaN → None chain (mean_raw NaN → var NaN → _round_none None).
        var = np.divide(
            s2[ii, kk], cn, out=np.full(R, np.nan), where=pos) \
            - mean_raw ** 2
        std = _round_none(np.sqrt(np.maximum(var, 0.0)))

        if n in MM_HORIZONS:
            hi_v = hi[ii, kk]    # (R,) max n-day forward change
            lo_v = lo[ii, kk]    # (R,) min n-day forward change
            max_vals = _round_none(hi_v)
            min_vals = _round_none(lo_v)
            mlr_vals = _round_none(np.divide(
                1 + hi_v, 1 + lo_v,
                out=np.full(R, np.nan),
                where=pos & (lo_v > -1),
            ))
        else:
            max_vals = [None] * R
            min_vals = [None] * R
            mlr_vals = [None] * R

        rev_vals = _round_none(np.divide(
            rev[ii, kk], cn, out=np.full(R, np.nan), where=pos))
        occ_vals = cn.tolist()
        # The row's reversal bar (per code, horizon — constant across a
        # window's configs).
        rt_vals = _round_none(thr_n[n][ii])

        horizon_payloads[n] = {
            "period": period,
            "ave": ave,
            "std": std,
            "max": max_vals,
            "min": min_vals,
            "mlr": mlr_vals,
            "rev": rev_vals,
            "occ": occ_vals,
            "rt": rt_vals,
        }

    # ...then emit bucket-major: [b0-next, b0-5d, b0-20d, b0-60d,
    # b1-next, ...] so forecast_id can stride by 4.
    out: list[dict] = []
    for r_idx, b in enumerate(base):
        for n in FORWARD_HORIZONS:
            p = horizon_payloads[n]
            out.append({
                **b,                          # motivation fields + config
                "period": p["period"],
                "ave_change": p["ave"][r_idx],
                "std_change": p["std"][r_idx],
                "max_change": p["max"][r_idx],
                "min_change": p["min"][r_idx],
                "occurrence_count": p["occ"][r_idx],
                "max_low_change_ratio": p["mlr"][r_idx],
                "reverse_prob": p["rev"][r_idx],
                "reverse_threshold": p["rt"][r_idx],
            })
    return out


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

    Input: (4·R,) dicts emitted by ``build_result_rows`` — bucket-major
    (4 consecutive period rows per bucket), each dict carries the full
    motivation fields + config + period + consolidated result columns.

    Returns:
        mov_rows    — (R,) dicts: UNIQUE rows per bucket (the 1st of
                      each 4-row group), filtered to ``mov_columns``
                      (mov_rsi / mov_std columns). The forecast_id was
                      already assigned by the caller (1 per bucket,
                      shared across all 4 period rows).
        result_rows — (4·R,) dicts: every input row filtered to
                      ``RESULT_COLUMNS`` (forecast_results columns).
    """
    mov_rows = [
        {k: rows[i][k] for k in mov_columns}
        for i in range(0, len(rows), 4)   # 1 per bucket
    ]
    result_rows = [
        {k: r[k] for k in RESULT_COLUMNS} for r in rows
    ]
    return mov_rows, result_rows
