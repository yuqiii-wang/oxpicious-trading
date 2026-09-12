"""MA / EMA-pair cross (golden / death cross) event-bucket monthly
aggregation (analysis_forecasts) — sparse tensor engine.

The mov_gap engine's streak-merge / hype-split / horizon-aggregation
machinery applied to the EXISTING relative-MA-spread columns of
analysis.mov_ave_spreads_detail — ma5_vs_ma{W} = (ma5 - ma_{W}) / ma_{W}
(fetched as ``pair_{W}``, W ∈ MOV_PAIRS_WINDOWS; the mov_pairs family)
— and to their EMA siblings, the EXISTING ema6_vs_ema{W} columns of
analysis.mov_ave_spreads_detail_ema (fetched as ``ema_pair_{W}``, W ∈
MOV_PAIRS_EMA_WINDOWS; the mov_pairs_ema family). Both are the parent
mov_ave_spread analysis's own spread definitions — no new MA / EMA
computation. A day triggers when the stored spread changes sign:

  side top    — CROSS UP   (golden cross): S[t] > 0 and S[t-1] <= 0
  side bottom — CROSS DOWN (death  cross): S[t] < 0 and S[t-1] >= 0

S[t-1] is the code's PREVIOUS union-grid row (wide-grid shift; a code
suspended across a sign flip misses that cross — the same union-grid
convention the streak-merge's run detection uses). NaN spreads compare
False, so warming up windows / missing rows never trigger.

The engine is source-agnostic: ``build_pairs_matrices`` scatters the
fetched spread columns under a ``prefix`` ("pair" for MA, "ema_pair"
for EMA) and ``compute_pairs_results`` reads the same prefix, so one
code path serves both families. Row payloads are identical
(pair_window, side, is_market_hyped); the caller writes
them to mov_pairs or mov_pairs_ema.

One-day EVENT signals (the 2026-09 streak migration's "one day"
branch of the unified pipeline — wide.iter_bucket_subsets with
merge=False): every cross day is its OWN forecast signal with a 1-day
run length. A cross is structurally a single day — a cross day's
predecessor sits on the OTHER side of zero, so consecutive cross days
are mutually exclusive and a streak-merge pass would be a no-op — so
the engines skip it; the recorded streak_signal_days is the 1 constant
and the result rows' streak spans are the signal day itself. Split by
PK member is_market_hyped, per-code ADAPTIVE reversal bar
(wide.reverse_thresholds: k_n·σ of the window's n-day forward
changes). No config JSONB payload (compute_gap precedent — the trigger
evidence is the stored spread itself, joinable via the bucket keys).

Yields (stat_month, rows) so __main__ can split each row into the
mov_pairs / mov_pairs_ema motivation dicts and the forecast_results
result dicts and write month-major.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    MM_HORIZONS,
    LOOKBACK_PERIOD,
    MOV_PAIRS_SIDES,
    MOV_PAIRS_WINDOWS,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizons_sparse,
    build_result_rows,
    iter_bucket_subsets,
    reverse_thresholds,
    scatter_column,
    window_sigmas,
)


def build_pairs_matrices(
    df,
    shape: tuple[int, int],
    didx: np.ndarray,
    cidx: np.ndarray,
    pair_windows: tuple = MOV_PAIRS_WINDOWS,
    prefix: str = "pair",
) -> dict[str, np.ndarray]:
    """Scatter the fetched spread columns into the (T, C) grid + build
    the 1-row-shifted previous-spread matrices.

    Args:
        prefix: the fetched column prefix AND the output key prefix —
              "pair" (ma5_vs_ma{W}, the mov_pairs family) or "ema_pair"
              (ema6_vs_ema{W}, the mov_pairs_ema family).

    Returns wide matrices keyed f"{prefix}_{w}" (the stored spread, NaN
    where missing) and f"{prefix}_{w}_prev" (the PREVIOUS grid row's
    spread, NaN on grid row 0). The shift is on the union trading-day
    grid (build_grid) — one vectorized pass, not per-code.
    """
    mats: dict[str, np.ndarray] = {}
    for w in pair_windows:
        S = scatter_column(df, f"{prefix}_{w}", shape, didx, cidx)
        Sp = np.full_like(S, np.nan)
        Sp[1:] = S[:-1]
        mats[f"{prefix}_{w}"] = S
        mats[f"{prefix}_{w}_prev"] = Sp
    return mats


def compute_pairs_results(
    mats: dict[str, np.ndarray],
    chg: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    hype: np.ndarray,
    first_ord: np.ndarray,
    pair_windows: tuple = MOV_PAIRS_WINDOWS,
    prefix: str = "pair",
    grid_ord: np.ndarray | None = None,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month.

    Args:
        mats: wide spread matrices (build_pairs_matrices) keyed
              f"{prefix}_{w}" + f"{prefix}_{w}_prev" ("pair" = the MA
              family's ma5_vs_ma{W}, "ema_pair" = the EMA family's
              ema6_vs_ema{W}).
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
            continue  # no grid rows in this window at all
        # Full-window gate: DATE-space comparison (first data month +
        # 60 months = first snapshot), same as the other engines.
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
        for w in pair_windows:
            S = mats[f"{prefix}_{w}"][lo:hi]
            Sp = mats[f"{prefix}_{w}_prev"][lo:hi]
            # Sign-flip triggers (NaN comparisons are False → warming-up
            # / missing rows never trigger), side-major (T, C, K) stack.
            # The qualifying bar is the ZERO line for both sides, so
            # the signed TRIGGER EXCESS (value − bar — the
            # forecast_results.trigger_excess source) is the day's
            # spread itself on either side (the live_signals
            # spread-cross precedent: excess reads as the spread).
            with np.errstate(invalid="ignore"):
                mask_raw = np.stack(
                    [(S > 0) & (Sp <= 0), (S < 0) & (Sp >= 0)], axis=2
                )
                excess3 = np.stack([S, S], axis=2)

            # ONE-DAY signals via the UNIFIED bucket pipeline
            # (wide.iter_bucket_subsets, merge=False): a cross day's
            # predecessor sits on the other side of zero, so
            # consecutive cross days are mutually exclusive — every
            # qualifying day is its own signal with a 1-day run length
            # (the streak-merge pass would be a no-op and is skipped).
            # The (side, hype) subsets come back as group-ascending
            # sparse cell lists.
            for (side, hyped, kk, ii, st, sc, fk, L_int, exc, mean_streak
                 ) in iter_bucket_subsets(
                    mask_raw, HY, live2, C, MOV_PAIRS_SIDES, merge=False,
                    excess3=excess3):
                agg = aggregate_horizons_sparse(
                    st, sc, fk, C, 1, side, NC0s, FINs, thr_n,
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
                        "pair_window": w,
                        "side": side,
                        "is_market_hyped": hyped,
                        "lookback_period": LOOKBACK_PERIOD,
                        # One-day signals: the bucket's mean run length
                        # is the 1 constant (the identity registry's
                        # streak column).
                        "streak_signal_days": round(
                            float(mean_streak[row_n]), 2),
                        # config JSONB: no extra motivation data
                        # for pair buckets (NULL = empty config,
                        # compute_gap precedent)
                        "config": None,
                    }
                    for row_n, i in enumerate(ii.tolist())
                ]
                rows.extend(build_result_rows(agg, kk, ii, base, thr_n))

        if rows:
            yield mw.stat_month, rows
