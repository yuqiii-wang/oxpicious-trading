"""dividend_state bucket monthly aggregation (analysis_forecasts) —
sparse tensor engine.

The valuation STATE buckets over the dividend-yield series of
analysis.dividends (see
database/sql/analysis/analysis_forecasts/12_dividend_state.sql): per
stat month's trailing 5-year window [lo, hi) of the (T, C) wide grid, a
(code, date) joins ONE of the 5 z states — the trailing-12m D/P
(fractional) standardized by the code's OWN trailing moments (the fetch
layer computes z = (dividend_yield - μ)/σ on the rolling-1220-row
shifted moments, min 250 non-NULL observations, so NaN here means "no
bucket" — non-payer days):

  vlow z <= -2 | low (-2,-1] | mid (-1,+1] | high (+1,+2] | vhigh z > +2

The family's defining semantics: the yield is HIGHER-the-better — a
high-yield day is a cheap, well-supported valuation → the extreme high
states are bullish (side 'bottom'), the low-yield states bearish (side
'top'); mid is 'flat' (reverse_prob NULL — no directional claim). The
mapping REVERSES the pe sibling's (compute_pe).

Signals are STREAK-MERGED via the UNIFIED bucket pipeline
(wide.iter_bucket_subsets, merge=True — the 2026-09 px_vol convention):
consecutive grid rows holding the same state collapse into ONE forecast
signal at the run's MID row, the bucket's MEAN run length recorded on
forecast_identities.streak_signal_days, the bucket split by PK member
is_market_hyped only.

Per (side, hype) subset the horizon aggregates reuse
wide.aggregate_horizons_sparse against the code's ADAPTIVE reversal bar
(reverse_thresholds: k_n·σ of the window's n-day forward changes).
The config JSONB records the bucket's mean yield level (mean_metric —
fractional D/P) and mean z (motivation magnitude, like margin_ratio's
mean_ratio / mean_z).

Yields (stat_month, rows) so __main__ can split each row into the
dividend_state motivation dicts and the forecast_results result dicts
and write month-major.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    DIVIDEND_STATE_SIDE,
    FORWARD_HORIZONS,
    LOOKBACK_PERIOD,
    MM_HORIZONS,
    VAL_HIGH_BAR,
    VAL_LOW_BAR,
    VAL_STATES,
    VAL_VHIGH_BAR,
    VAL_VLOW_BAR,
    VAL_Z_MIN_PERIODS,
    VAL_Z_WINDOW,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizons_sparse,
    build_result_rows,
    iter_bucket_subsets,
    reverse_thresholds,
    round6,
    window_sigmas,
)

_K = len(VAL_STATES)                        # 5 states on the z axis

# Contiguous per-side ranges of the state axis (VAL_STATES order —
# the dividend mapping REVERSES the pe ranges: vlow, low are the
# bearish 'top' states, high, vhigh the bullish 'bottom' ones).
_SIDE_SLICES: dict[str, slice] = {
    "top": slice(0, 2),
    "flat": slice(2, 3),
    "bottom": slice(3, 5),
}


def compute_dividend_results(
    mats: dict[str, np.ndarray],
    chg: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    hype: np.ndarray,
    first_ord: np.ndarray,
    grid_ord: np.ndarray | None = None,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month.

    Args:
        mats: wide state matrices keyed "z" (the dividend_yield's
              z-score vs the code's own trailing moments — NaN where
              undefined) and "metric" (the raw dividend_yield level —
              config JSONB magnitude only).
        chg:  shared change matrices (build_change_matrices):
              NC0_{n} / FIN_{n} for n in FORWARD_HORIZONS.
        windows: resolved MonthWindow list for the target months.
        codes: sorted code list (matrix column order).
        sec_type: emitted into every row.
        hype: (T, C) bool matrix of market-hyped (date, code) cells
              (build_hype_matrix).
        first_ord: (C,) per-code first data date as ABSOLUTE epoch-day
              ordinals — a code is live for a window only when
              first_ord < mw.lo_ord (DATE-space full-window gate).
        grid_ord: optional (T,) int64 day ordinals of the FULL grid
              (build_grid) — sliced per window into aggregate_horizons_
              sparse's win_ord so each emitted row carries its
              trigger_dates (the calendar dates behind occurrence_count).
    """
    C = len(codes)

    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue
        live = first_ord < mw.lo_ord
        if not live.any():
            continue

        FINs = {n: chg[f"FIN_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        NC0s = {n: chg[f"NC0_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        # Window-sliced PATH-extreme matrices (FMAX0/FMIN0) — the
        # swing-aware reversal event + max_low_change_ratio inputs.
        PATH0s = {n: (chg[f"FMAX0_{n}"][lo:hi], chg[f"FMIN0_{n}"][lo:hi])
                  for n in MM_HORIZONS}
        # Per-(code, horizon) adaptive reversal bar for this window.
        thr_n = reverse_thresholds(*window_sigmas(NC0s, FINs))
        HY = hype[lo:hi]
        live2 = live[:, None]

        Z = mats["z"][lo:hi]
        M = mats["metric"][lo:hi]
        # (K, T, C) state masks stacked in VAL_STATES order — NaN z
        # compares False, so undefined-z days never join a bucket.
        with np.errstate(invalid="ignore"):
            mask = np.stack([
                Z <= VAL_VLOW_BAR,
                (Z > VAL_VLOW_BAR) & (Z <= VAL_LOW_BAR),
                (Z > VAL_LOW_BAR) & (Z <= VAL_HIGH_BAR),
                (Z > VAL_HIGH_BAR) & (Z <= VAL_VHIGH_BAR),
                Z > VAL_VHIGH_BAR,
            ])
        # (K, T, C) → (T, C, K) with k = the state position.
        n_rows = hi - lo
        mask = mask.transpose(1, 2, 0).reshape(n_rows, C, _K)

        rows: list[dict] = []
        # Streak-merged state signals via the UNIFIED bucket pipeline
        # (wide.iter_bucket_subsets, merge=True): consecutive grid rows
        # holding the SAME state collapse into ONE signal at the run's
        # MID row, run lengths ride along per kept cell for the bucket's
        # streak_signal_days mean, and the (side, hype) subsets come
        # back as group-ascending sparse cell lists (the side slices
        # are the dividend mapping's reversed ranges).
        for (side, hyped, kk, ii, st, sc, fk, L_int, exc, mean_streak
             ) in iter_bucket_subsets(
                mask, HY, live2, C,
                tuple(_SIDE_SLICES), side_slices=_SIDE_SLICES):
            sl = _SIDE_SLICES[side]
            P = sl.stop - sl.start
            agg = aggregate_horizons_sparse(
                st, sc, fk, C, P, side, NC0s, FINs, thr_n,
                path0s=PATH0s,
                win_ord=None if grid_ord is None else grid_ord[lo:hi],
                lens=L_int,
            )
            # Per-bucket mean state magnitudes (config JSONB — the
            # motivation magnitude, like margin_ratio's mean_ratio /
            # mean_z). Every sparse cell has a valid z by construction
            # (NaN compares False upstream), so the all-cell sums are
            # the per-cell mean numerators; the yield level is gathered
            # per cell from the raw-dividend_yield matrix.
            cell_cnt = np.bincount(fk, minlength=C * P)
            cell_z = Z[st, sc]
            cell_m = M[st, sc]
            # The bins are CODE-major (flat = i*P + k), so gather each
            # emitted (code, state) pair's OWN bin — emit cells have
            # cell_cnt > 0 by construction.
            emit_flat = ii * P + kk
            mean_z_vals = np.divide(
                np.bincount(fk, weights=cell_z, minlength=C * P)[emit_flat],
                cell_cnt[emit_flat],
                out=np.full(emit_flat.size, np.nan),
                where=cell_cnt[emit_flat] > 0,
            )
            mean_m_vals = np.divide(
                np.bincount(fk, weights=cell_m, minlength=C * P)[emit_flat],
                cell_cnt[emit_flat],
                out=np.full(emit_flat.size, np.nan),
                where=cell_cnt[emit_flat] > 0,
            )
            base: list[dict] = []
            for row_n, (k, i) in enumerate(zip(kk.tolist(), ii.tolist())):
                state = VAL_STATES[sl.start + k]
                base.append({
                    "sec_type": sec_type,
                    "code": codes[i],
                    "stat_month": mw.stat_month,
                    "val_state": state,
                    "side": DIVIDEND_STATE_SIDE[state],
                    "is_market_hyped": hyped,
                    "z_window": VAL_Z_WINDOW,
                    "z_min_periods": VAL_Z_MIN_PERIODS,
                    "vlow_bar": VAL_VLOW_BAR,
                    "low_bar": VAL_LOW_BAR,
                    "high_bar": VAL_HIGH_BAR,
                    "vhigh_bar": VAL_VHIGH_BAR,
                    "lookback_period": LOOKBACK_PERIOD,
                    # the bucket's MEAN merged-state-run length (the
                    # identity registry's streak column).
                    "streak_signal_days": round(
                        float(mean_streak[row_n]), 2),
                    # config JSONB — asyncpg COPY needs a JSON text
                    # string (compute_px_vol precedent).
                    "config": json.dumps({
                        "mean_metric": round6(mean_m_vals[row_n]),
                        "mean_z": round6(mean_z_vals[row_n]),
                    }),
                })
            batch = build_result_rows(agg, kk, ii, base, thr_n)
            if side == "flat":
                # No directional claim → the reversal probability is
                # meaningless (its "against the bucket side" is
                # undefined); NULL it on all 4 period rows.
                batch = [{**r, "reverse_prob": None} for r in batch]
            rows.extend(batch)

        if rows:
            yield mw.stat_month, rows
