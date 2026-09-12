"""Bollinger-breach monthly aggregation (analysis_forecasts) — sparse
tensor engine.

For each stat month's trailing 5-year window [lo, hi) of the (T, C) wide
grid, each MA window W and each sigma multiple k:

  upper breach: price > ma_{W} + k·std_{W}days
  lower breach: price < ma_{W} - k·std_{W}days

(NaN bounds / NaN price compare False, so rows without a fully-populated
band never enter a bucket.) Codes whose own history does not span the
full window (first data date > window start) are gated out — no
partial-window stats. Each (code, w, k, side) bucket is SPLIT into
two rows by the PK member is_market_hyped — whether the bucket's breach
dates fall inside the code's stats.mov_ave_market_hypes episodes:
one row for the hyped breach days and one for the non-hyped breach days
(each subset emitted only where non-empty — no breach, no record).

The (k, side) configs are stacked into ONE (T, C, K) bucket mask tensor
per MA window (K = len(STD_MULTIPLES), side-major: the first half of
the config axis is the upper-side ks, the second the lower-side ks).
The UNIFIED bucket-signal pipeline (wide.iter_bucket_subsets) then runs
the whole shared span ONCE on the flattened (T, C·K) stack (columns are
config-independent): streak-merge, sparsification with a single
np.nonzero, live-gated per-config streak counts and the (side, hype)
subset splits as trigger-cell lists — every downstream reduction works
on those lists: the hype split is a cell filter, the per-horizon mean /
high / low n-day forward change and P(reverse beyond the code's
adaptive reverse_threshold) come from wide.aggregate_horizons_sparse
(bincount/reduceat passes scaling with the trigger count). The row
payload (forecast_results fields) is expanded by wide.build_result_rows
(vectorized rounding). No per-config / per-code Python loops.

Yields (stat_month, rows) so __main__ can split each row into the
mov_std motivation dicts and the forecast_results result dicts and write
month-major.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    LOOKBACK_PERIOD,
    MA_WINDOWS,
    MM_HORIZONS,
    STD_MULTIPLES,
    STD_SIDES,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizons_sparse,
    build_result_rows,
    iter_bucket_subsets,
    reverse_thresholds,
    window_sigmas,
)


def compute_std_results(
    mats: dict[str, np.ndarray],
    chg: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    hype: np.ndarray,
    first_ord: np.ndarray,
    ma_windows: tuple = MA_WINDOWS,
    ks: tuple = STD_MULTIPLES,
    grid_ord: np.ndarray | None = None,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month.

    Args:
        mats: wide matrices keyed "price", f"ma_{w}", f"std_{w}".
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
              comparison; row-space lo clamps to 0 when the grid starts
              after the nominal window start), i.e. its own history
              strictly precedes the window start — first data month +
              60 months = first snapshot (first listed 2020-01 →
              first snapshot 2025-01).
        grid_ord: optional (T,) int64 day ordinals of the FULL grid
              (build_grid) — sliced per window into aggregate_horizons_
              sparse's win_ord so each emitted row carries its
              trigger_dates (the calendar dates behind occurrence_count).
    """
    C = len(codes)
    K = len(ks)
    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue  # no grid rows in this window at all
        # Full-window gate: codes whose own history starts ON OR AFTER
        # the window start are excluded — their 5y window would be
        # partial (first data month + 60 months = first snapshot:
        # earliest data 2020-01 → first snapshot 2025-01, NOT 2024-12
        # whose window merely STARTS at the first data date).
        # DATE-space comparison (absolute ordinals): grid-row space
        # would wrongly pass codes first listed at the grid start when
        # the grid begins after the nominal window start.
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
        P = mats["price"][lo:hi]
        HY = hype[lo:hi]
        live2 = live[:, None]

        rows: list[dict] = []
        for w in ma_windows:
            MA = mats[f"ma_{w}"][lo:hi]
            SD = mats[f"std_{w}"][lo:hi]

            # Per-(k, side) breach masks stacked side-major into ONE
            # (T, C, 2K) tensor, plus the matching BAND-EDGE tensor
            # (MA ± k·SD per config) — the trigger-excess bar each
            # breach day's price is measured against. Raw pre-check
            # first — skip the streak-merge work entirely for windows
            # without a single breach.
            mask_sides = []
            bar_sides = []
            with np.errstate(invalid="ignore"):
                for side in STD_SIDES:
                    ms = [
                        P > MA + k * SD if side == "upper"
                        else P < MA - k * SD
                        for k in ks
                    ]
                    mask_sides.append(np.stack(ms, axis=2))
                    bars = [
                        MA + k * SD if side == "upper"
                        else MA - k * SD
                        for k in ks
                    ]
                    bar_sides.append(np.stack(bars, axis=2))
                mask_raw = np.concatenate(mask_sides, axis=2)  # (T, C, 2K)
                # Signed TRIGGER EXCESS (value − qualifying bar): the
                # breached day's price minus the band edge it crossed —
                # the forecast_results.trigger_excess source (NaN cells
                # compare False in the mask, so excess is only gathered
                # where a band was actually breached).
                excess3 = P[:, :, None] - np.concatenate(
                    bar_sides, axis=2)  # (T, C, 2K)
            if not ((mask_raw.sum(axis=0) * live2) > 0).any():
                continue

            # Streak-merge + side/hype subsets via the UNIFIED bucket
            # pipeline (wide.iter_bucket_subsets): consecutive breach
            # grid rows collapse into ONE signal at the run's MID row
            # (the high_low_streaks mean-mid anchor; the legacy
            # fixed-5-day cooldown was removed 2026-09), run lengths
            # ride along per kept cell for the bucket's
            # streak_signal_days mean, and the (side, hype) subsets
            # come back as group-ascending sparse cell lists.
            for (side, hyped, kk, ii, st, sc, fk, L_int, exc, mean_streak
                 ) in iter_bucket_subsets(mask_raw, HY, live2, C, STD_SIDES,
                                          excess3=excess3):
                agg = aggregate_horizons_sparse(
                    st, sc, fk, C, K, side, NC0s, FINs, thr_n,
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
                        "ma_window": w,
                        "k": ks[k],
                        "side": side,
                        "is_market_hyped": hyped,
                        "lookback_period": LOOKBACK_PERIOD,
                        "streak_signal_days": round(
                            float(mean_streak[row_n]), 2),
                        # config JSONB: no extra motivation data
                        # for std buckets (NULL = empty config)
                        "config": None,
                    }
                    for row_n, (k, i) in enumerate(
                        zip(kk.tolist(), ii.tolist()))
                ]

                rows.extend(build_result_rows(agg, kk, ii, base, thr_n))

        if rows:
            yield mw.stat_month, rows
