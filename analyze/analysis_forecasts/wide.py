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
  4. ``build_change_matrices`` — wide forward-change matrices (endpoint
     changes + the MM horizons' path-extreme swings) + validity
     + reverse flags shared by both engines.
  5. ``month_row_windows`` — per stat month, the [lo, hi) grid-row range
     of its trailing 5-year window.
  6. ``iter_bucket_subsets`` — the engines' UNIFIED bucket-signal
     pipeline: streak-merge (apply_streak_midpoints; or one-day
     signals) → sparsify → live-gated per-config counts → per-side →
     per-hype split, yielded as the group-ascending sparse cell lists
     of step 7 (shared by compute_rsi / compute_std / compute_gap /
     compute_pairs / compute_px_vol).
  7. ``aggregate_horizons_sparse`` — batched per-(code, config) mean/
     std/high/low/reverse-prob stats of ALL forward horizons over the SPARSE
     trigger-cell lists of a stacked (T, C, K) bucket mask (shared by
     both engines; bincount/reduceat — work scales with the trigger
     count, not the dense tensor). With ``win_ord`` it also gathers the
     per-horizon ragged TRIGGER-DATE lists (the calendar dates behind
     occurrence_count — the forecast_results.trigger_dates column).
  8. ``build_result_rows`` — expand one batch's gathered aggregates into
     the forecast_results fields of its emitted rows (vectorized — no
     per-row scalar rounding calls).
  9. ``split_forecast_rows`` — split computed bucket rows into the
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
    PX_VOL_SPEED_ORD,
    PX_VOL_VOL_ORD,
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


def build_px_vol_state_matrices(
    states_df: pd.DataFrame,
    grid_ord: np.ndarray,
    codes: list[str],
    shape: tuple[int, int],
) -> dict[str, np.ndarray]:
    """Scatter the price_vs_amt registry rows into the (T, C) grid.

    Args:
        states_df: fetch_price_vs_amt_states' frame (code, date,
            px_speed, vol_state, px_t, px_z — sorted by (code, date)).
        grid_ord: the MAIN grid's sorted day ordinals (build_grid).
        codes: the MAIN grid's sorted code list.
        shape: (T, C).

    Returns:
        Wide state matrices keyed:
          "speed" — (T, C) int8 speed ordinal 0..4 (PX_VOL_SPEEDS
                    order), -1 = no valid state that day
          "vol"   — (T, C) int8 vol ordinal 0..2 (PX_VOL_VOL_STATES
                    order), -1 = none
          "t" / "z" — (T, C) float64 of the recorded px_t / px_z
                    (NaN where no state — the bucket means' weights)

    The engines consume these INSTEAD of thresholding raw features: the
    registry (analysis.mov_ave_price_vs_amt) is the px_vol family's
    date-level source of truth, so the buckets audit against it 1:1.
    Registry dates outside the main grid (shouldn't happen — both
    derive from the same basic_stats close rows) are dropped.
    """
    T_n, C_n = shape
    speed = np.full(shape, -1, dtype=np.int8)
    vol = np.full(shape, -1, dtype=np.int8)
    t = np.full(shape, np.nan, dtype=np.float64)
    z = np.full(shape, np.nan, dtype=np.float64)
    if states_df.empty:
        return {"speed": speed, "vol": vol, "t": t, "z": z}

    dord = date_ordinals(states_df["date"])
    didx = np.searchsorted(grid_ord, dord)
    scodes = host_array(states_df["code"].to_numpy())
    codes_arr = np.asarray(codes)
    cidx = np.searchsorted(codes_arr, scodes)

    # Keep only (code, date) pairs that exist on the main grid.
    ok = didx < T_n
    ok[ok] &= cidx[ok] < C_n
    ok[ok] &= grid_ord[didx[ok]] == dord[ok]
    ok[ok] &= codes_arr[cidx[ok]] == scodes[ok]

    speed_idx = host_array(
        states_df["px_speed"].map(PX_VOL_SPEED_ORD).to_numpy(dtype="float64")
    )
    vol_idx = host_array(
        states_df["vol_state"].map(PX_VOL_VOL_ORD).to_numpy(dtype="float64")
    )
    speed[didx[ok], cidx[ok]] = speed_idx[ok].astype(np.int8)
    vol[didx[ok], cidx[ok]] = vol_idx[ok].astype(np.int8)
    t[didx[ok], cidx[ok]] = host_array(
        states_df["px_t"].to_numpy(dtype="float64")
    )[ok]
    z[didx[ok], cidx[ok]] = host_array(
        states_df["px_z"].to_numpy(dtype="float64")
    )[ok]
    return {"speed": speed, "vol": vol, "t": t, "z": z}


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
    """Wide matrices derived from the forward-change columns.

    Keys (n = forward horizon in trading days):
      NC0_{n} — next_change_{n}d with NaN→0 (einsum-safe sums)
      FIN_{n} — validity bool (day has a finite n-day forward change)
      FMAX0_{n} / FMIN0_{n} — MM horizons only: the n-day forward
              WINDOW's signed close extremes vs the signal close
              (fetch.add_path_extremes path_high_{n}d / path_low_{n}d)
              with NaN→0 (invalid days never fire a threshold compare),
              the swing the reversal event and max_low_change_ratio
              consume. At the next-day horizon the path IS the endpoint
              (NC0_1), so no separate matrices exist there.

    Note: max_low_change_ratio is derived at aggregation time from the
    bucket's PATH-extreme forward changes as (1 + max path high) /
    (1 + min path low): the widest realized within-window swing across
    the bucket's trigger days — highest close reached vs lowest close
    touched (signed, so a large ratio always signifies a large swing;
    the extrema of one trigger day's window never mix with another's
    endpoint).
    """
    mats: dict[str, np.ndarray] = {}
    for n in FORWARD_HORIZONS:
        nc = scatter_column(df, f"next_change_{n}d", shape, didx, cidx)
        fin = np.isfinite(nc)
        mats[f"NC0_{n}"] = np.where(fin, nc, 0.0)
        mats[f"FIN_{n}"] = fin
        if n in MM_HORIZONS:
            for key, col in (("FMAX0", "path_high_{n}d"),
                             ("FMIN0", "path_low_{n}d")):
                pc = scatter_column(df, col.format(n=n), shape, didx, cidx)
                mats[f"{key}_{n}"] = np.where(np.isfinite(pc), pc, 0.0)
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

    NOTE (2026-09 streak migration): the FORECAST event engines no
    longer use this — consecutive qualifying days now merge into ONE
    streak signal anchored at the run's mid row
    (``apply_streak_midpoints``). This stays for the SIGNALS layer's
    LIVE detection (analyze.analysis_signals — a live trigger cannot
    know an ongoing streak's mid ex-post, so day-level de-dup keeps the
    fixed-skip cooldown semantics).

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


def apply_streak_midpoints(
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Collapse each run of CONSECUTIVE qualifying rows into ONE signal
    at the run's MID row — the streak-merge that replaced the legacy
    cooldown suppression in the forecast engines (mov_rsi / mov_std /
    mov_gap / px_vol_state; consumed via ``iter_bucket_subsets``, the
    engines' unified bucket-signal pipeline).

    A run of length L starting at row s keeps only row
    s + (L - 1) // 2 (0-based; the ((L-1)//2 + 1)-th day — the MEAN-MID
    anchor of the high_low_streaks convention: a 1-day run anchors
    itself, a 7-day run its 4th day, an 8-day run its 4th day). Dates
    that keep satisfying the forecast condition CONTINUOUSLY are treated
    as ONE forecast signal, measured from the mid day.

    Operates per column (the flattened (T, C·K) config stack — columns
    are config-independent). Runs are clipped at the window-slice edges:
    a streak straddling the window start anchors from its visible part
    ("for now" — the month-window slices are independent worlds, the
    apply_cooldown precedent).

    Returns (mid_mask, run_len): ``mid_mask`` is True only at the kept
    mid rows; ``run_len`` (int32) carries the run's day count at those
    rows (0 elsewhere). The per-bucket MEAN of the kept cells'
    lengths is written to forecast_identities.streak_signal_days by the
    caller.
    """
    T, M = mask.shape
    # Zero-padded shifted views isolate run STARTS / ENDS in one
    # broadcast compare (a run touching the slice edge is still bounded
    # by the False pad).
    padded = np.zeros((T + 2, M), dtype=bool)
    padded[1:-1] = mask
    core = padded[1:-1]
    starts = core & ~padded[:-2]
    ends = core & ~padded[2:]
    rs, ms = np.nonzero(starts)
    re, me = np.nonzero(ends)
    # np.nonzero emits ROW-major order across the whole matrix, so the
    # k-th start is NOT the k-th end globally — pair them PER COLUMN
    # (within a column starts and ends strictly alternate, start first,
    # so the column-major-sorted k-th start pairs with the k-th end of
    # the same column).
    rs, ms = rs[np.lexsort((rs, ms))], ms[np.lexsort((rs, ms))]
    re = re[np.lexsort((re, me))]
    L = (re - rs + 1).astype(np.int32)
    mid_rows = rs + ((L - 1) // 2).astype(np.int64)
    mid = np.zeros((T, M), dtype=bool)
    lens = np.zeros((T, M), dtype=np.int32)
    mid[mid_rows, ms] = True
    lens[mid_rows, ms] = L
    return mid, lens


def iter_bucket_subsets(
    mask_raw: np.ndarray,
    HY: np.ndarray,
    live2: np.ndarray,
    C: int,
    sides: tuple[str, ...],
    side_slices: dict[str, slice] | None = None,
    *,
    merge: bool = True,
    excess3: np.ndarray | None = None,
):
    """The UNIFIED bucket-signal pipeline of the aggregation engines
    (compute_rsi / compute_std / compute_gap / compute_pairs /
    compute_px_vol): streak-merge → sparsify → live-gated per-config
    counts → per-side → per-hype split, yielded as the group-ascending
    sparse cell lists aggregate_horizons_sparse consumes.

    One implementation of the machinery every engine previously carried
    inline (the 2026-09 streak migration duplicated it per engine).

    Args:
        mask_raw: (Tw, C, K_total) bool bucket mask of ONE month window
              — the raw per-day qualifying tests (percentile compares,
              band breaches, sign flips, state equality). NaN compares
              are False upstream, so invalid days never enter.
        HY: (Tw, C) bool market-hype matrix of the same window slice.
        live2: (C, 1) bool live gate (full-window gate × ones) — codes
              without full-window history never count / never emit.
        C: number of codes.
        sides: side names in config-axis order.
        side_slices: optional {side: slice} per-side ranges of the
              config axis (the state engines' uneven layout — px_vol).
              None → K_total is split into len(sides) EQUAL contiguous
              ranges (the mov_* engines' side-major halves).
        merge: True (default) — STREAK-MERGE (apply_streak_midpoints):
              consecutive qualifying grid rows collapse into ONE signal
              at the run's MID row, run lengths ride along for the
              bucket's streak_signal_days mean and the result rows'
              streak spans. False — ONE-DAY signals: every qualifying
              day is its own signal with run length 1 (the pairs
              engines: a cross day's predecessor sits on the other side
              of zero, so consecutive cross days are mutually exclusive
              and the merge pass would be a no-op).
        excess3: optional (T, C, K_total) signed per-day per-config
              TRIGGER EXCESS (value − qualifying bar) of the same
              window slice — the scalar-bar engines' companion tensor
              (mov_rsi / mov_gap: the day's indicator minus its
              percentile bar; mov_std: the price minus the breached
              band edge; mov_pairs: the day's spread, the bar being the
              zero line). Gathered at the kept cells like ``lens`` and
              yielded per subset so aggregate_horizons_sparse can slice
              it into the rows' forecast_results.trigger_excess lists.
              None (state families — no scalar qualifying bar) yields
              None excess, and the result rows write NULL arrays.

    Yields per non-empty (side, is_market_hyped) subset — a tuple
        (side, hyped, kk, ii, st, sc, fk, lens, exc, mean_streak)
      side / hyped — the subset keys;
      kk, ii — (R,) config / code indices of the EMITTED buckets
              (np.nonzero(emit.T) convention of build_result_rows);
      st, sc, fk — (E,) group-ASCENDING sorted sparse cells of the
              subset (window-local row, code, flat group = code·P +
              config — the aggregate_horizons_sparse contract);
      lens — (E,) int32 run length per cell (the merged streak's day
              count; all 1s in one-day mode);
      exc  — (E,) float trigger excess per cell (the excess3 gather at
              the same cells, subset- and sort-aligned like ``lens``);
              None when excess3 was None;
      mean_streak — (R,) the bucket's mean run length at the emitted
              (code, config) positions — the source of
              forecast_identities.streak_signal_days.
    """
    K_total = mask_raw.shape[2]
    if side_slices is None:
        width = K_total // len(sides)
        slices = {
            s: slice(i * width, (i + 1) * width)
            for i, s in enumerate(sides)
        }
    else:
        slices = side_slices

    if merge:
        T = mask_raw.shape[0]
        mask3, lens3 = apply_streak_midpoints(mask_raw.reshape(T, -1))
        nz_t, nz_c, nz_k = np.nonzero(mask3.reshape(mask_raw.shape))
    else:
        nz_t, nz_c, nz_k = np.nonzero(mask_raw)
    if nz_t.size == 0:
        return
    nz_t = nz_t.astype(np.int32)
    nz_c = nz_c.astype(np.int32)
    nz_k = nz_k.astype(np.int32)
    # Run length per kept cell (the merged streak's day count; every
    # qualifying day carries its own 1-day run in one-day mode).
    if merge:
        cell_lens = lens3.reshape(mask_raw.shape)[nz_t, nz_c, nz_k]
    else:
        cell_lens = np.ones(nz_t.size, dtype=np.int32)
    # Trigger excess per kept cell (value − qualifying bar; None for
    # the state families — no excess3 tensor was passed).
    cell_exc = (
        excess3[nz_t, nz_c, nz_k] if excess3 is not None else None
    )
    # Live-gated per-config STREAK counts (one per qualifying run —
    # not-yet-live codes never emit).
    count = np.bincount(
        nz_c * K_total + nz_k, minlength=C * K_total
    ).reshape(C, K_total) * live2
    if not (count > 0).any():
        return
    hy_cells = HY[nz_t, nz_c]

    for side in sides:
        sl = slices[side]
        P = sl.stop - sl.start
        cnt_s = count[:, sl]
        if not (cnt_s > 0).any():
            continue
        in_side = (nz_k >= sl.start) & (nz_k < sl.stop)
        if not in_side.any():
            continue
        t_s = nz_t[in_side]
        c_s = nz_c[in_side]
        flat_s = c_s * P + (nz_k[in_side] - sl.start)
        hy_s = hy_cells[in_side]
        len_s = cell_lens[in_side]
        exc_s = cell_exc[in_side] if cell_exc is not None else None

        # Hype split of the bucket (PK member). Aggregates are per
        # subset — max/high/low are non-additive, so the non-hyped
        # subset cannot be derived from the full bucket minus the
        # hyped one. Each subset is a cell-list filter.
        for hyped in (False, True):
            sel = hy_s if hyped else ~hy_s
            if not sel.any():
                continue
            st = t_s[sel]
            sc = c_s[sel]
            fk = flat_s[sel]
            L_int = len_s[sel]
            exc = exc_s[sel] if exc_s is not None else None
            # Group-ascending cell order — one stable sort shared by
            # the subset count and every horizon's bincount / reduceat
            # reductions.
            order = np.argsort(fk, kind="stable")
            st = st[order]
            sc = sc[order]
            fk = fk[order]
            L_int = L_int[order]
            exc = exc[order] if exc is not None else None
            emit = (
                np.bincount(fk, minlength=C * P).reshape(C, P) > 0
            ) & (cnt_s > 0)
            if not emit.any():
                continue
            # streak_signal_days source: the bucket's MEAN run length
            # over its (hype-split) streak signals.
            L_sel = L_int.astype(np.float64)
            mean_streak = np.divide(
                np.bincount(fk, weights=L_sel, minlength=C * P),
                np.maximum(np.bincount(fk, minlength=C * P), 1),
            ).reshape(C, P)
            kk, ii = np.nonzero(emit.T)
            yield (side, hyped, kk, ii, st, sc, fk, L_int, exc,
                   mean_streak[ii, kk])


# Per-horizon aggregate bundle returned by ``aggregate_horizons_sparse``
# (each numeric member a (C, P) array): occurrence counts, sum of changes,
# sum of SQUARED changes, max / min ENDPOINT change (None at the next-day
# horizon — the max_change / min_change columns), max / min PATH-extreme
# change (None likewise — the highest/lowest close any trigger day's
# forward window reached, the max_low_change_ratio swing inputs),
# reversal count, and — when ``win_ord`` was passed — the ragged
# trigger-date lists (dict keyed by flat group id; None otherwise) plus,
# when ``lens`` was passed too, the parallel STREAK SPAN lists (the
# [start, end] calendar dates of each merged signal's qualifying run) and
# the parallel STREAK DAY counts (each run's trading-day length;
# None otherwise), plus — when ``vals`` was passed — the parallel TRIGGER
# EXCESS lists (each valid cell's value − qualifying bar; None otherwise).
HorizonAgg = tuple[np.ndarray, np.ndarray, np.ndarray,
                   np.ndarray | None, np.ndarray | None,
                   np.ndarray | None, np.ndarray | None,
                   np.ndarray,
                   "dict[int, list[date]] | None",
                   "dict[int, list[date]] | None",
                   "dict[int, list[date]] | None",
                   "dict[int, list[int]] | None",
                   "dict[int, list[float]] | None"]


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
    win_ord: np.ndarray | None = None,
    lens: np.ndarray | None = None,
    path0s: dict[int, tuple[np.ndarray, np.ndarray]] | None = None,
    vals: np.ndarray | None = None,
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
        path0s: per MM horizon n — the (FMAX0, FMIN0) path-extreme
              matrices (build_change_matrices), sliced to the window
              like NC0s. The SWING-AWARE reversal event and the
              max_low_change_ratio consume these: at any cell the
              ADVERSE PATH EXTREME (the window's lowest close for
              top/upper, highest for bottom/lower — signed) is what
              crosses the reversal bar, and the swing ratio's extrema
              are path extrema (never one day's high mixed with another
              day's low ENDPOINT). None falls back to the endpoint
              change everywhere (the 1-day path IS the endpoint).
        win_ord: optional (T,) int64 day ordinals of the window's grid
              rows (grid_ord[lo:hi]). When given, each horizon's bundle
              also carries the ragged trigger-date lists td — the
              calendar dates (datetime.date, ascending — cells are
              time-ascending within each group) of the group's bucket
              days with a VALID n-day forward change, keyed by flat
              group id, i.e. exactly the denominator of that horizon's
              cnt / mean / reverse_prob. Consumed by build_result_rows
              as the forecast_results.trigger_dates column; None member
              when win_ord is omitted.
        lens: optional (E,) int run length per subset cell (the merged
              streak's day count — apply_streak_midpoints' lens at the
              kept mid cells, subset- and sort-aligned like st/sc/flat).
              When given (event engines only) AND win_ord is given, the
              bundle also carries the parallel STREAK SPAN lists ss /
              se — per valid cell, the qualifying run's [start, end]
              calendar dates (mid row - (L-1)//2 .. mid row + L//2 on
              the grid; runs are clipped at the window slice so both
              ends always land inside) — and the parallel STREAK DAY
              lists sd — per valid cell, the run's trading-day count L.
              Consumed by build_result_rows as the
              forecast_results.streak_starts / streak_ends /
              streak_days columns (parallel to trigger_dates).
              None elsewhere.
        vals: optional (E,) float TRIGGER EXCESS per subset cell (the
              day's trigger value − the bucket's qualifying bar, signed;
              iter_bucket_subsets' exc, subset- and sort-aligned like
              st/sc/flat). When given, each horizon's bundle also
              carries the parallel TRIGGER EXCESS lists te — per valid
              cell (the SAME valid-cell subset as td), the cell's excess
              — consumed by build_result_rows as the
              forecast_results.trigger_excess column (parallel to
              trigger_dates). None (state engines / callers without a
              scalar qualifying bar) leaves the column NULL.

    Returns (per horizon n) — a HorizonAgg bundle of (C, P) numeric
    members (see HorizonAgg) plus the per-horizon td / ss / se / sd /
    te dicts:
        cnt — bucket days with a valid n-day forward change (int);
        s   — sum of the n-day changes over those days;
        s2  — sum of SQUARED n-day changes over those days (invalid days
              contribute 0.0 — NC0 semantics), the E[x²] half of the
              std_change numerator sqrt(E[x²] − E[x]²);
        hi_e — max ENDPOINT n-day change (-inf where cnt == 0; None for
              the next-day horizon — no MM columns);
        lo_e — min ENDPOINT n-day change (+inf where cnt == 0; None
              likewise);
        hi_p — max PATH-HIGH n-day change (-inf where cnt == 0; None
              likewise — the swing ratio's upper extreme);
        lo_p — min PATH-LOW n-day change (+inf where cnt == 0; None
              likewise — the swing ratio's lower extreme);
        rev — count of reversal days (the period's ADVERSE PATH EXTREME
              beyond the code's bar against the bucket side — the
              within-period swing, not merely the period-end close)
              among those days (int);
        td  — {flat group id: [date, ...]} of the cnt days (None when
              win_ord is None);
        ss / se — {flat group id: [date, ...]} of each signal's
              qualifying-run start / end calendar date, parallel to td
              (None when win_ord or lens is None);
        sd  — {flat group id: [int, ...]} of each signal's qualifying-
              run trading-day count, parallel to td (None when win_ord
              or lens is None);
        te  — {flat group id: [float, ...]} of each valid cell's
              trigger excess, parallel to td (None when vals is None).
    """
    CP = C * P
    # Group scaffolding over the group-ascending cells: one sort shared
    # by every horizon's extrema reduction.
    bounds = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.concatenate(([0], bounds))
    gid = flat[starts]
    rev_top = side in ("top", "upper")

    # Shared date objects of the window's grid rows (win_ord is int64
    # days-since-epoch — the same unit as build_grid's grid_ord). Every
    # group's date list slices THIS object array, so all lists of a
    # window share one small pool of date objects.
    wdate = (
        win_ord.astype("datetime64[D]").astype(object)
        if win_ord is not None else None
    )

    out: dict[int, HorizonAgg] = {}
    for n in FORWARD_HORIZONS:
        g = NC0s[n][st, sc]          # 0.0 on invalid days (NC0 semantics)
        v = FINs[n][st, sc]
        # Per-cell PATH extremes (the forward window's highest/lowest
        # close vs the signal close, signed; 0.0 on invalid days — the
        # FMAX0/FMIN0 NaN→0 semantics). At the next-day horizon the
        # path IS the endpoint (no separate matrices); a caller without
        # path matrices (compute_opp_pair — its forward quantity is a
        # relative-MA offset change, not a close path) falls back to
        # the endpoint everywhere (the legacy endpoint semantics).
        if n in MM_HORIZONS and path0s is not None:
            fh = path0s[n][0][st, sc]
            fl = path0s[n][1][st, sc]
        else:
            fh = fl = g
        cnt = np.bincount(flat[v], minlength=CP)
        s = np.bincount(flat, weights=g, minlength=CP)
        # g is 0.0 on invalid days, so the all-cell squared sum equals
        # the valid-day squared sum (the std E[x²] pass — same trick as
        # the mean sum above).
        s2 = np.bincount(flat, weights=g * g, minlength=CP)
        # Reversal at a cell — SWING-AWARE: the period's ADVERSE PATH
        # EXTREME (lowest close for top/upper, highest for bottom/
        # lower) beyond the code's bar, i.e. the forward window swung
        # ≥ thr against the bucket side AT ANY CLOSE of the period, not
        # merely at its end. Invalid cells hold 0.0 extremes, so the
        # threshold compare is False there — matches the legacy dense
        # 0/1 flag einsum exactly.
        thr_cell = thr_n[n][sc]
        adv = fl if rev_top else fh
        rv = (adv < -thr_cell) if rev_top else (adv > thr_cell)
        rev = np.bincount(flat[rv], minlength=CP)
        # Ragged trigger dates over the VALID cells (fk-ascending subset
        # of the fk-ascending cells): per group id, the contiguous
        # [lo, hi) slice of the valid cells' dates. Groups with no valid
        # day emit no entry (build_result_rows maps a miss to NULL).
        # The valid-cell group bounds are shared by the parallel
        # trigger-excess lists below.
        td = None
        ss = None
        se = None
        sd = None
        lo_g = hi_g = None
        if wdate is not None or vals is not None:
            fk_v = flat[v]
            lo_g = np.searchsorted(fk_v, gid, side="left")
            hi_g = np.searchsorted(fk_v, gid, side="right")
        if wdate is not None:
            d_v = wdate[st[v]]
            td = {
                g_id: d_v[a:b].tolist()
                for g_id, a, b in zip(gid.tolist(), lo_g.tolist(),
                                      hi_g.tolist())
                if b > a
            }
            if lens is not None:
                # Streak spans + day counts, parallel to td (same
                # valid-cell subset): per kept mid cell, the qualifying
                # run's start / end grid rows around it (mid - (L-1)//2
                # .. mid + L//2 — runs are clipped at the window slice,
                # so both land inside) mapped to the shared calendar
                # dates, and the run's trading-day count L.
                Lv = lens[v]
                starts_all = wdate[st[v] - ((Lv - 1) // 2)]
                ends_all = wdate[st[v] + (Lv // 2)]
                ss = {}
                se = {}
                sd = {}
                for g_id, a3, b3 in zip(gid.tolist(), lo_g.tolist(),
                                        hi_g.tolist()):
                    if b3 > a3:
                        ss[g_id] = starts_all[a3:b3].tolist()
                        se[g_id] = ends_all[a3:b3].tolist()
                        sd[g_id] = Lv[a3:b3].tolist()
        # Ragged trigger excesses over the SAME valid-cell subset (the
        # group bounds above are computed whenever either parallel list
        # is requested): per group id, the contiguous [lo, hi) slice of
        # the valid cells' excess values (value − qualifying bar).
        te = None
        if vals is not None:
            w_v = vals[v]
            te = {
                g_id: w_v[a:b].tolist()
                for g_id, a, b in zip(gid.tolist(), lo_g.tolist(),
                                      hi_g.tolist())
                if b > a
            }
        if n in MM_HORIZONS:
            hi_e = np.full(CP, -np.inf)
            lo_e = np.full(CP, np.inf)
            hi_p = np.full(CP, -np.inf)
            lo_p = np.full(CP, np.inf)
            # Groups with no valid cell keep the ±inf default (the
            # legacy where(..., ±inf).max semantics).
            hi_e[gid] = np.maximum.reduceat(np.where(v, g, -np.inf), starts)
            lo_e[gid] = np.minimum.reduceat(np.where(v, g, np.inf), starts)
            hi_p[gid] = np.maximum.reduceat(np.where(v, fh, -np.inf), starts)
            lo_p[gid] = np.minimum.reduceat(np.where(v, fl, np.inf), starts)
            out[n] = (cnt.reshape(C, P), s.reshape(C, P), s2.reshape(C, P),
                      hi_e.reshape(C, P), lo_e.reshape(C, P),
                      hi_p.reshape(C, P), lo_p.reshape(C, P),
                      rev.reshape(C, P), td, ss, se, sd, te)
        else:
            out[n] = (cnt.reshape(C, P), s.reshape(C, P), s2.reshape(C, P),
                      None, None, None, None,
                      rev.reshape(C, P), td, ss, se, sd, te)
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
    # Config-axis width P (flat group id = code·P + config — the td dict
    # key space of aggregate_horizons_sparse).
    P = agg[next(iter(agg))][0].shape[1]
    # First gather all horizon payloads (vectorized per horizon)...
    horizon_payloads: dict[int, dict[str, list | float | None]] = {}
    for n in FORWARD_HORIZONS:
        period = PERIOD_FOR_HORIZON[n]
        cnt, s, s2, hi_e, lo_e, hi_p, lo_p, rev, td, ss, se, sd, te = agg[n]
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
            hi_v = hi_e[ii, kk]   # (R,) max endpoint n-day change
            lo_v = lo_e[ii, kk]   # (R,) min endpoint n-day change
            max_vals = _round_none(hi_v)
            min_vals = _round_none(lo_v)
            # The SWING ratio: (1 + max path high) / (1 + min path low)
            # over the bucket's trigger days — the highest close any
            # trigger day's forward window reached vs the lowest any
            # window touched (signed, so the ratio is ≥ 1 and grows
            # with the widest realized within-period swing). NEVER
            # derivable from the endpoint max/min columns — one day's
            # window high is never paired with another day's endpoint.
            sw_hi = hi_p[ii, kk]
            sw_lo = lo_p[ii, kk]
            mlr_vals = _round_none(np.divide(
                1 + sw_hi, 1 + sw_lo,
                out=np.full(R, np.nan),
                where=pos & (sw_lo > -1),
            ))
        else:
            max_vals = [None] * R
            min_vals = [None] * R
            mlr_vals = [None] * R

        rev_vals = _round_none(np.divide(
            rev[ii, kk], cn, out=np.full(R, np.nan), where=pos))
        occ_vals = cn.tolist()
        # The row's ragged trigger-date list (NULL when the group has no
        # valid day at this horizon — occurrence_count 0).
        td_vals: list[list[date] | None] = [
            None if td is None else td.get(int(i) * P + int(k))
            for k, i in zip(kk.tolist(), ii.tolist())
        ]
        # The rows' parallel STREAK SPAN lists (NULL when the engine
        # passed no run lengths — state families): streak_starts[r] /
        # streak_ends[r] are element-wise parallel to trigger_dates[r].
        ss_vals: list[list[date] | None] = [
            None if ss is None else ss.get(int(i) * P + int(k))
            for k, i in zip(kk.tolist(), ii.tolist())
        ]
        se_vals: list[list[date] | None] = [
            None if se is None else se.get(int(i) * P + int(k))
            for k, i in zip(kk.tolist(), ii.tolist())
        ]
        # The rows' parallel STREAK DAY lists (same NULL semantics):
        # streak_days[r] holds each merged signal's trading-day count.
        sd_vals: list[list[int] | None] = [
            None if sd is None else sd.get(int(i) * P + int(k))
            for k, i in zip(kk.tolist(), ii.tolist())
        ]
        # The rows' parallel TRIGGER EXCESS lists (NULL when the engine
        # passed no per-cell values — the state families): each element
        # is the trigger day's value − the bucket's qualifying bar
        # (signed), rounded to the NUMERIC(10,6) scale; non-finite
        # members map to None (asyncpg cannot encode NaN here).
        te_vals: list[list[float | None] | None] = []
        for k, i in zip(kk.tolist(), ii.tolist()):
            t = None if te is None else te.get(int(i) * P + int(k))
            te_vals.append(None if t is None else [round6(x) for x in t])
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
            "td": td_vals,
            "ss": ss_vals,
            "se": se_vals,
            "sd": sd_vals,
            "te": te_vals,
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
                "trigger_dates": p["td"][r_idx],
                "streak_starts": p["ss"][r_idx],
                "streak_ends": p["se"][r_idx],
                "streak_days": p["sd"][r_idx],
                "trigger_excess": p["te"][r_idx],
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
