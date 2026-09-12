"""MA-Spread High/Low streak MEAN-MID anchor monthly aggregation
(analysis_forecasts) — sparse tensor engine.

The mov_pairs engine's event-bucket machinery applied to the EXISTING
band-break excursion streaks of analysis.mov_ave_high_low_pct_streaks
(fetched by fetch.fetch_high_low_streaks with each streak's SIDE
derived in SQL off the unrounded end-date close vs the end month's
band). Every streak period is audited at its MEAN-MID anchor day:

    anchor = the ((n - 1) // 2 + 1)-th own trading day of the span
             [start_date, end_date], n = the stored day_count — the
             floor of the MEAN elapsed day (n-1)/2 ("mean mid elapsed
             day once entered a streak"; an 8-day streak anchors its
             4th day, a 7-day streak its 4th, a 1-day streak the day
             itself).

The anchor is EX-POST: the streak length — hence its mid — is known
only after the streak closes, so the buckets audit streak-period
behaviour (the 2026-09 study temp_scripts/study_high_low_streaks_
forecast.py shows a strong mean-reversion reading from the mid anchor:
below-band streaks drift UP, above-band streaks drift DOWN); they are
NOT a live trigger.

``build_streak_anchor_cells`` resolves every streak to its anchor
(t, c) grid cell ONCE (per-code cumulative own-row machinery on the
(T, C) grid — no python row loops):

  own    — bool (T, C): the code's OWN trading rows on the grid
           (frame rows ∩ the project CN trading calendar — the same
           row space the streaks step counted its day_count on);
  cum    — int32 (T, C): per-column cumulative own-row count;
  inv    — int32 (T, C): ordinal -> grid row (the inverse of cum);
  anchor — lo = searchsorted(grid, start), hi = searchsorted(grid,
           end, right); the (k+1)-th own row in the span is
           inv[cum[lo-1, c] + k, c] with k = min((n-1)//2, m-1), m =
           own rows in the span. The stored mid is CLAMPED to the
           frame's own rows: where the forecast frame drops rows the
           streaks step saw (estimated-close rows on index/etf), m <
           n and the anchor shifts to the nearest available mid row —
           the anchor must be a date the forward-change matrices know.

``compute_high_low_streaks_results`` then runs the mov_pairs per-month
loop over the anchor cells: window filter (anchor date in (stat_month
- 5y, stat_month]), full-window gate, per-(period, pct_type) combo and
side, hype split of the ANCHOR cells, per-code adaptive reversal bar
(wide.reverse_thresholds), sparse horizon aggregation
(aggregate_horizons_sparse) and result-row expansion. No cooldown —
each streak contributes exactly ONE trigger and streaks are inherently
separated (a streak ends only after a 6+-day in-band gap or a side
switch), the state-family shape (px_vol / margin_ratio precedent).

The config JSONB records the bucket's streak-length context:
{"mean_day_count": float, "min_day_count": int, "max_day_count": int}
(asyncpg COPY needs a JSON text string — compute_opp_pair precedent).

Yields (stat_month, rows) so __main__ can write month-major batches to
analysis_forecasts.high_low_streaks + forecast_results.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np
import pandas as pd

from _common._holidays_and_weekdays import is_trading_day
from _common.df_utils import host_array

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    MM_HORIZONS,
    HIGH_LOW_STREAKS_PERIODS,
    HIGH_LOW_STREAKS_SIDES,
    HIGH_LOW_STREAKS_TYPES,
    LOOKBACK_PERIOD,
)
from analyze.analysis_forecasts.wide import (
    aggregate_horizons_sparse,
    build_result_rows,
    date_ordinals,
    reverse_thresholds,
    round6,
    window_sigmas,
)

# (period, pct_type) combos in canonical order — the engine's config
# axis; the combo key packs the pair into one sortable int.
COMBOS: tuple[tuple[int, int], ...] = tuple(
    (p, t) for p in HIGH_LOW_STREAKS_PERIODS for t in HIGH_LOW_STREAKS_TYPES
)
_COMBO_KEYS = np.array([p * 100 + t for p, t in COMBOS], dtype=np.int64)


def build_streak_anchor_cells(
    streaks: pd.DataFrame,
    df: pd.DataFrame,
    grid_ord: np.ndarray,
    codes: list[str],
    shape: tuple[int, int],
    didx: np.ndarray,
    cidx: np.ndarray,
    extra_cols: dict[str, np.ndarray] | None = None,
) -> dict[str, np.ndarray] | None:
    """Resolve every fetched streak to its mean-mid anchor (t, c) grid
    cell (absolute grid rows).

    Args:
      streaks: fetch_high_low_streaks' frame (code, period, pct_type,
          start_date, end_date, day_count, side), any order.
      df: the forecast input frame (build_grid's source — provides the
          (code, date) rows the anchor is placed on).
      grid_ord / codes / shape / didx / cidx: build_grid outputs.
      extra_cols: optional {name: host ndarray} over the streaks frame's
          ROWS, carried through the same validity mask (aligned with the
          returned arrays) — consumers that need per-streak payloads
          (e.g. the signals engine's band edge / span dates).

    Returns a dict of aligned host arrays over the VALID streaks —
      t          — (E,) int64 grid row of the anchor day,
      c          — (E,) int64 code column,
      combo      — (E,) int64 index into COMBOS,
      side_ord   — (E,) int8 +1 top / -1 bottom,
      day_count  — (E,) int64 the stored streak length (config context),
      end_t      — (E,) int64 grid row of the streak's end_date (the
                   provisional-streak guard: an end within
                   HIGH_LOW_PCT_GAP_TOLERANCE rows of the grid edge may
                   still extend — the signals family drops those),
      anchor_pos — (E,) int64 1-based position of the anchor day within
                   the span (floor of the mean elapsed day, after the
                   clamp to available frame rows),
      plus one entry per extra_cols key,
    or None when no streak anchors (all codes off the grid / no rows).
    """
    if streaks.empty:
        return None
    T, C = shape

    # ---- own VALID trading rows on the grid --------------------------
    # The streak space is the code's real CN trading rows (the streaks
    # step filtered the vendor weekday-holiday ffills out); the frame
    # may carry such rows for codes the calendar excludes, so the own
    # mask repeats that filter (one python loop over the frame's UNIQUE
    # dates — a few thousand — then a vectorized isin).
    dord = date_ordinals(df["date"])
    uniq = np.unique(dord).astype("datetime64[D]")
    ok = np.array([is_trading_day(pd.Timestamp(u).date()) for u in uniq])
    row_ok = np.isin(dord, uniq[ok].astype(np.int64))
    own = np.zeros(shape, dtype=bool)
    own[didx[row_ok], cidx[row_ok]] = True

    cum = own.cumsum(axis=0, dtype=np.int32)
    # ordinal -> grid row inverse (inv[o, c] = the grid row of column
    # c's (o+1)-th own trading row; -1 sentinel unused by construction).
    inv = np.full(shape, -1, dtype=np.int32)
    r_idx, c_idx2 = np.nonzero(own)
    inv[cum[r_idx, c_idx2] - 1, c_idx2] = r_idx.astype(np.int32)
    del own

    # ---- streak rows -> anchor cells (vectorized) ---------------------
    codes_arr = np.asarray(codes)
    s_code = host_array(streaks["code"].to_numpy())
    sc = np.searchsorted(codes_arr, s_code).astype(np.int64)
    # Off-grid codes (sc == C, or an in-range slot holding a different
    # code) drop out via the equality mask; sc is clamped for the fancy
    # indexing below so the mask is the only correctness gate.
    ok_code = (sc < C) & (codes_arr[np.minimum(sc, C - 1)] == s_code)
    sc = np.minimum(sc, C - 1)

    s_ord = date_ordinals(streaks["start_date"])
    e_ord = date_ordinals(streaks["end_date"])
    n_dc = host_array(streaks["day_count"].to_numpy()).astype(np.int64)
    period = host_array(streaks["period"].to_numpy()).astype(np.int64)
    pct = host_array(streaks["pct_type"].to_numpy()).astype(np.int64)
    side_str = host_array(streaks["side"].to_numpy())
    side_ord = np.where(side_str == "top", 1, -1).astype(np.int8)
    combo = np.searchsorted(
        _COMBO_KEYS, period * 100 + pct
    ).astype(np.int64)

    lo = np.searchsorted(grid_ord, s_ord)
    hi = np.searchsorted(grid_ord, e_ord, side="right")
    base = np.where(lo > 0, cum[np.maximum(lo - 1, 0), sc], 0).astype(
        np.int64
    )
    m_span = (
        cum[np.maximum(hi - 1, 0), sc].astype(np.int64) - base
    )
    ok_code &= (hi > lo) & (m_span >= 1)
    if not ok_code.any():
        return None
    # The stored mid, clamped to the frame's available own rows (see
    # the module docstring — the anchor must be a date the change
    # matrices know).
    k = np.minimum((n_dc - 1) // 2, m_span - 1)
    apos = inv[base + k, sc].astype(np.int64)
    ok_code &= apos >= 0
    if not ok_code.any():
        return None
    cells: dict[str, np.ndarray] = {
        "t": apos[ok_code],
        "c": sc[ok_code],
        "combo": combo[ok_code],
        "side_ord": side_ord[ok_code],
        "day_count": n_dc[ok_code],
        # grid row of end_date (hi points one past it)
        "end_t": (hi - 1).astype(np.int64)[ok_code],
        # 1-based anchor position within the span (post-clamp)
        "anchor_pos": (k + 1).astype(np.int64)[ok_code],
    }
    for name, arr in (extra_cols or {}).items():
        cells[name] = np.asarray(arr)[ok_code]
    return cells


def compute_high_low_streaks_results(
    cells: dict[str, np.ndarray],
    chg: dict[str, np.ndarray],
    windows: list,
    codes: list[str],
    sec_type: str,
    hype: np.ndarray,
    first_ord: np.ndarray,
    grid_ord: np.ndarray | None = None,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month — the mov_pairs
    loop over the anchor cells.

    Args:
      cells: build_streak_anchor_cells output (ABSOLUTE grid rows).
      chg:  shared change matrices (build_change_matrices):
            NC0_{n} / FIN_{n} for n in FORWARD_HORIZONS.
      windows: resolved MonthWindow list for the target months.
      codes: sorted code list (matrix column order).
      sec_type: emitted into every row.
      hype: (T, C) bool matrix of market-hyped (date, code) cells
            (build_hype_matrix) — split on the ANCHOR cells.
      first_ord: (C,) per-code first data date as ABSOLUTE epoch-day
            ordinals — a code is live for a window only when
            first_ord < mw.lo_ord (DATE-space full-window gate).
      grid_ord: optional (T,) int64 day ordinals of the FULL grid
            (build_grid) — sliced per window into aggregate_horizons_
            sparse's win_ord so each emitted row carries its
            trigger_dates (the ANCHOR dates behind occurrence_count).
    """
    C = len(codes)
    t_all = cells["t"]
    c_all = cells["c"]

    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue  # no grid rows in this window at all
        # Full-window gate (DATE space) + anchor-in-window filter.
        live = first_ord < mw.lo_ord
        win = (t_all >= lo) & (t_all < hi) & live[c_all]
        if not win.any():
            continue

        FINs = {n: chg[f"FIN_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        NC0s = {n: chg[f"NC0_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        # Window-sliced PATH-extreme matrices (FMAX0/FMIN0) — the
        # swing-aware reversal event + max_low_change_ratio inputs.
        PATH0s = {n: (chg[f"FMAX0_{n}"][lo:hi], chg[f"FMIN0_{n}"][lo:hi])
                  for n in MM_HORIZONS}
        # Per-(code, horizon) reversal bar for this window (adaptive
        # k·σ of the code's window forward changes; fixed fallback).
        thr_n = reverse_thresholds(*window_sigmas(NC0s, FINs))
        HY = hype[lo:hi]

        t_w = t_all[win] - lo           # window-local anchor rows
        c_w = c_all[win]
        combo_w = cells["combo"][win]
        side_w = cells["side_ord"][win]
        dc_w = cells["day_count"][win].astype(np.float64)

        rows: list[dict] = []
        for ci, (period, pct_t) in enumerate(COMBOS):
            sel = combo_w == ci
            if not sel.any():
                continue
            for side in HIGH_LOW_STREAKS_SIDES:
                s_sel = sel & (
                    side_w == (1 if side == "top" else -1)
                )
                if not s_sel.any():
                    continue
                t_s = t_w[s_sel]
                c_s = c_w[s_sel]
                dc_s = dc_w[s_sel]
                hy_s = HY[t_s, c_s]

                # Hype split of the bucket (PK member) — each subset is
                # a cell-list filter (max/min are non-additive).
                for hyped in (False, True):
                    m2 = hy_s if hyped else ~hy_s
                    if not m2.any():
                        continue
                    st = t_s[m2]
                    sc = c_s[m2]
                    dcv = dc_s[m2]
                    # Group id = code index (P = 1 config per combo×side
                    # batch) — group-ascending cell order shared by the
                    # count / horizon reductions and the config stats.
                    fk = sc
                    order = np.argsort(fk, kind="stable")
                    st = st[order]
                    sc = sc[order]
                    fk = fk[order]
                    dcv = dcv[order]
                    cnt = np.bincount(fk, minlength=C)
                    emit = cnt > 0
                    if not emit.any():
                        continue

                    # Streak-length context (config JSONB): mean/min/max
                    # day_count over the bucket's anchor cells.
                    wmean = np.divide(
                        np.bincount(fk, weights=dcv, minlength=C), cnt,
                        out=np.full(C, np.nan), where=cnt > 0,
                    )
                    bounds = np.flatnonzero(fk[1:] != fk[:-1]) + 1
                    starts = np.concatenate(([0], bounds))
                    dmin = np.full(C, np.nan)
                    dmax = np.full(C, np.nan)
                    dmin[fk[starts]] = np.minimum.reduceat(dcv, starts)
                    dmax[fk[starts]] = np.maximum.reduceat(dcv, starts)

                    agg = aggregate_horizons_sparse(
                        st, sc, fk, C, 1, side, NC0s, FINs, thr_n,
                        path0s=PATH0s,
                        win_ord=None if grid_ord is None
                        else grid_ord[lo:hi],
                    )
                    kk = np.zeros(int(emit.sum()), dtype=np.int64)
                    ii = np.nonzero(emit)[0]
                    base: list[dict] = [
                        {
                            "sec_type": sec_type,
                            "code": codes[i],
                            "stat_month": mw.stat_month,
                            # band_period (NOT "period" — reserved by the
                            # result-row pipeline, which stamps its own
                            # period 'next'/'5d'/'20d'/'60d' key here).
                            "band_period": period,
                            "pct_type": pct_t,
                            "side": side,
                            "is_market_hyped": hyped,
                            "lookback_period": LOOKBACK_PERIOD,
                            # streak_signal_days — the identity registry's
                            # streak column: this family IS the streak
                            # convention (mean-mid anchors), so the bucket's
                            # mean streak day_count (config mean_day_count).
                            "streak_signal_days": round(
                                float(wmean[i]), 2),
                            # config JSONB — asyncpg COPY needs a JSON
                            # text string (compute_opp_pair precedent).
                            "config": json.dumps({
                                "mean_day_count": round6(
                                    float(wmean[i])),
                                "min_day_count": int(dmin[i]),
                                "max_day_count": int(dmax[i]),
                            }),
                        }
                        for i in ii.tolist()
                    ]
                    rows.extend(build_result_rows(agg, kk, ii, base, thr_n))

        if rows:
            yield mw.stat_month, rows
