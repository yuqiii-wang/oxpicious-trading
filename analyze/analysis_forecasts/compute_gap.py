"""Gap (N-day return) extreme-bucket monthly aggregation
(analysis_forecasts) — sparse tensor engine.

The mov_rsi engine (compute_rsi.py) applied to the gap_{W}days columns
(W-day fractional price return from analysis.mov_ave_rsi, W ∈ {2, 3}):

For each stat month's trailing 5-year window [lo, hi) of the (T, C) wide
grid and each gap window W:

  1. Sort the window slice of gap_{W} column-wise ONCE (np.sort puts NaN
     last) — every percentile threshold (top + bottom × 1/5/10/25) is then
     a linear-interpolated gather from the same sorted matrix.
  2. Bucket mask: top → V ≥ τ(q=1−pct/100) (sharp W-day rally);
     bottom → V ≤ τ(q=pct/100) (sharp W-day selloff). NaN comparisons
     are False, so invalid days never enter a bucket. Codes whose own
     history does not span the full window are gated out.
  3. The (side, pct) configs are stacked into ONE (T, C, K) bucket mask
     tensor (side-major), and the UNIFIED bucket-signal pipeline
     (wide.iter_bucket_subsets) runs the whole shared span ONCE on the
     flattened (T, C·K) stack (columns are config-independent):
     streak-merge, sparsification, live-gated per-config streak counts
     and the (side, hype) subset splits as group-ascending trigger-cell
     lists — every downstream reduction (hype split, per-horizon mean /
     high / low n-day forward change and P(reverse beyond the code's
     adaptive reverse_threshold) via wide.aggregate_horizons_sparse)
     works on those lists. The row payload is expanded by
     wide.build_result_rows.

Gap values are unbounded fractional returns (unlike 0–100 RSI) but the
percentile machinery is rank-based — identical code path.

Yields (stat_month, rows) so __main__ can split each row into the
mov_gap motivation dicts and the forecast_results result dicts and write
month-major.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    MM_HORIZONS,
    GAP_PCTS,
    GAP_SIDES,
    GAP_WINDOWS,
    LOOKBACK_PERIOD,
)
from analyze.analysis_forecasts.compute_rsi import _thresholds
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizons_sparse,
    build_result_rows,
    iter_bucket_subsets,
    reverse_thresholds,
    window_sigmas,
)


def compute_gap_results(
    mats: dict[str, np.ndarray],
    chg: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    hype: np.ndarray,
    first_ord: np.ndarray,
    gap_windows: tuple = GAP_WINDOWS,
    pcts: tuple = GAP_PCTS,
    grid_ord: np.ndarray | None = None,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month.

    Args:
        mats: wide gap matrices keyed f"gap_{w}".
        chg:  shared change matrices (build_change_matrices):
              NC0_{n} / FIN_{n} for n in FORWARD_HORIZONS.
        windows: resolved MonthWindow list for the target months.
        codes: sorted code list (matrix column order).
        sec_type: emitted into every row.
        hype: (T, C) bool matrix of market-hyped (date, code) cells
              (build_hype_matrix).
        first_ord: (C,) per-code first data date as ABSOLUTE epoch-day
              ordinals (first_ords_from_dates) — a code is live for a
              window only when first_ord < mw.lo_ord (DATE-space
              comparison), i.e. its own history strictly precedes the
              window start.
        grid_ord: optional (T,) int64 day ordinals of the FULL grid
              (build_grid) — sliced per window into aggregate_horizons_
              sparse's win_ord so each emitted row carries its
              trigger_dates (the calendar dates behind occurrence_count).
    """
    C = len(codes)
    col = np.arange(C)
    P = len(pcts)
    K = 2 * P
    # Config axis is side-major: quantiles for the top-side pcts first,
    # then the bottom-side pcts.
    qs = [(1.0 - p / 100.0) for p in pcts] + [p / 100.0 for p in pcts]

    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue  # no grid rows in this window at all
        # Full-window gate: same DATE-space comparison as the other
        # engines (first data month + 60 months = first snapshot).
        live = first_ord < mw.lo_ord
        if not live.any():
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
        live2 = live[:, None]

        rows: list[dict] = []
        for w in gap_windows:
            V = mats[f"gap_{w}"][lo:hi]
            valid_n = np.count_nonzero(~np.isnan(V), axis=0).astype(np.int64)
            if not ((valid_n > 0) & live).any():
                continue
            S = np.sort(V, axis=0)      # NaN last — quantile gathers
            thr = np.stack(
                [_thresholds(S, valid_n, col, q) for q in qs], axis=1
            )  # (C, K), NaN where the column has no valid values

            # Bucket masks for ALL (side, pct) configs in one broadcast
            # compare (NaN V / NaN τ compare False → invalid days never
            # enter a bucket), plus the per-config signed TRIGGER
            # EXCESS tensor (value − qualifying bar: V − τ_top for the
            # top-side pcts, V − τ_bottom for the bottom-side — the
            # forecast_results.trigger_excess source; NaN cells compare
            # False in the mask, so excess is only gathered where the
            # bar was actually breached).
            with np.errstate(invalid="ignore"):
                V3 = V[:, :, None]
                mask_raw = np.concatenate(
                    [V3 >= thr[:, :P][None], V3 <= thr[:, P:][None]],
                    axis=2,
                )  # (T, C, K)
                excess3 = np.concatenate(
                    [V3 - thr[:, :P][None], V3 - thr[:, P:][None]],
                    axis=2,
                )  # (T, C, K)

            # Streak-merge + side/hype subsets via the UNIFIED bucket
            # pipeline (wide.iter_bucket_subsets): consecutive
            # qualifying grid rows collapse into ONE signal at the run's
            # MID row (the high_low_streaks mean-mid anchor; the legacy
            # fixed-5-day cooldown was removed 2026-09), run lengths
            # ride along per kept cell for the bucket's
            # streak_signal_days mean, and the (side, hype) subsets
            # come back as group-ascending sparse cell lists.
            for (side, hyped, kk, ii, st, sc, fk, L_int, exc, mean_streak
                 ) in iter_bucket_subsets(mask_raw, HY, live2, C, GAP_SIDES,
                                          excess3=excess3):
                agg = aggregate_horizons_sparse(
                    st, sc, fk, C, P, side, NC0s, FINs, thr_n,
                    path0s=PATH0s,
                    win_ord=None if grid_ord is None
                    else grid_ord[lo:hi],
                    lens=L_int,
                    vals=exc,
                )
                base: list[dict] = [
                    {
                        "sec_type": sec_type,
                        "code": codes[i],
                        "stat_month": mw.stat_month,
                        "gap_window": w,
                        "side": side,
                        "pct": pcts[k],
                        "is_market_hyped": hyped,
                        "lookback_period": LOOKBACK_PERIOD,
                        "streak_signal_days": round(
                            float(mean_streak[row_n]), 2),
                        # config JSONB: no extra motivation data
                        # for gap buckets (NULL = empty config)
                        "config": None,
                    }
                    for row_n, (k, i) in enumerate(
                        zip(kk.tolist(), ii.tolist()))
                ]
                rows.extend(build_result_rows(agg, kk, ii, base, thr_n))

        if rows:
            yield mw.stat_month, rows
