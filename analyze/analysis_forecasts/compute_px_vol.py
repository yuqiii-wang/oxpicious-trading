"""px_vol_state bucket monthly aggregation (analysis_forecasts) —
sparse tensor engine.

The recent-day price-change × trading-amount STATE buckets (see
database/sql/analysis/analysis_forecasts/05_px_vol_state.sql and the
2026-09 temp_scripts studies): per stat month's trailing 5-year window
[lo, hi) of the (T, C) wide grid, a (code, date) joins ONE of the
15 speed × volume cells when BOTH legs hold. The categories come from
the analysis.mov_ave_price_vs_amt REGISTRY (the px_vol family's
DATE-LEVEL source of truth, built by analyze.mov_ave_spread) —
scattered via wide.build_px_vol_state_matrices into:

  speed  — (T, C) int8 speed ordinal (PX_VOL_SPEEDS order; -1 = no
           valid state that day)
  vol    — (T, C) int8 vol ordinal (PX_VOL_VOL_STATES order; -1 = none)
  t / z  — (T, C) float64 of the recorded px_t / px_z (the bucket
           mean_t / mean_z config magnitudes)

  px_speed: sharp_up t>2.0 | slow_up 1.26<t<=2.0 | flat -1.29<=t<=1.26
            | slow_dn -2.0<=t<-1.29 | sharp_dn t<-2.0
  vol_state: heavy z>2.0 | normal | shrink z<-0.92

(Thresholding the raw t/z features was superseded by the registry
read — the engines AUDIT against the recorded categories; the
PX_VOL_* constants remain the recorded row parameters AND the audit
bar fetch.assert_price_vs_amt_params enforces before consuming.)

Like the mov_* EVENT buckets the state signals are STREAK-MERGED
(2026-09, the unified bucket-signal pipeline wide.iter_bucket_subsets):
consecutive grid rows holding the SAME (speed, vol) cell collapse into
ONE forecast signal at the run's MID row — the high_low_streaks
mean-mid anchor — the bucket's MEAN run length recorded on
forecast_identities.streak_signal_days, and the bucket split is by PK
member is_market_hyped only. Config axis: k = speed_idx * 3 + state_idx
with PX_VOL_SPEEDS × PX_VOL_VOL_STATES ordering.

Per (side, hype) subset the horizon aggregates reuse
wide.aggregate_horizons_sparse (bincount/reduceat over the sparse
trigger cells) against the code's ADAPTIVE reversal bar
(reverse_thresholds: k_n·σ of the window's n-day forward changes) —
top speeds reverse on change < -thr, bottom speeds on change > +thr;
flat rows carry side='flat' and get reverse_prob = NULL (no
directional claim). The config JSONB records the bucket's mean t /
mean z (motivation magnitude, like margin_ratio's mean ratio / mean z).

Yields (stat_month, rows) so __main__ can split each row into the
px_vol_state motivation dicts and the forecast_results result dicts
and write month-major.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    MM_HORIZONS,
    LOOKBACK_PERIOD,
    PX_VOL_K_SHARP,
    PX_VOL_K_SLOW_DN,
    PX_VOL_K_SLOW_UP,
    PX_VOL_LB_WINDOW,
    PX_VOL_SIGMA_FLOOR,
    PX_VOL_SIGMA_WINDOW,
    PX_VOL_SPEEDS,
    PX_VOL_SPEED_SIDE,
    PX_VOL_VOL_STATES,
    PX_VOL_Z_HEAVY,
    PX_VOL_Z_SHRINK,
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

_N_STATES = len(PX_VOL_VOL_STATES)          # 3 vol states per speed
_K = len(PX_VOL_SPEEDS) * _N_STATES         # 15 configs

# Side-batch config ranges on the k axis (speed-major layout).
_SIDE_SLICES: dict[str, slice] = {
    "top": slice(0, 2 * _N_STATES),                    # sharp_up+slow_up
    "flat": slice(2 * _N_STATES, 3 * _N_STATES),
    "bottom": slice(3 * _N_STATES, 5 * _N_STATES),     # slow_dn+sharp_dn
}


def compute_px_vol_results(
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
        mats: wide state matrices from the price_vs_amt registry
              (wide.build_px_vol_state_matrices) keyed "speed"/"vol"
              (int8 category ordinals, -1 = none) and "t"/"z" (the
              recorded px_t / px_z — NaN where no state).
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
    n_speeds = len(PX_VOL_SPEEDS)

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

        S = mats["speed"][lo:hi]
        V = mats["vol"][lo:hi]
        T = mats["t"][lo:hi]
        Z = mats["z"][lo:hi]
        # Speed masks (5, T, C) stacked in PX_VOL_SPEEDS order —
        # equality on the registry's int8 ordinals (-1 never matches).
        speed = np.stack([(S == i) for i in range(n_speeds)])
        vol = np.stack([(V == i) for i in range(_N_STATES)])
        # (5, 3, T, C) → (T, C, K) with k = speed_idx*3 + state_idx.
        n_rows = hi - lo
        mask = (speed[:, None] & vol[None, :]) \
            .transpose(2, 3, 0, 1).reshape(n_rows, C, _K)

        rows: list[dict] = []
        # Streak-merged state signals via the UNIFIED bucket pipeline
        # (wide.iter_bucket_subsets, merge=True — the 2026-09 migration
        # of the state family onto the mov_* event-family convention):
        # consecutive grid rows holding the SAME (speed, vol) cell
        # collapse into ONE signal at the run's MID row, run lengths
        # ride along per kept cell for the bucket's streak_signal_days
        # mean, and the (side, hype) subsets come back as
        # group-ascending sparse cell lists (the side slices are the
        # speed-major layout's uneven ranges).
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
            # motivation magnitude, like margin_ratio's):
            # every sparse cell has valid t/z by construction, so
            # the all-cell sums are the per-cell mean numerators.
            cell_cnt = np.bincount(fk, minlength=C * P)
            s_t = np.bincount(fk, weights=T[st, sc], minlength=C * P)
            s_z = np.bincount(fk, weights=Z[st, sc], minlength=C * P)
            # The bins are CODE-major (flat = i*P + k), so gather
            # each emitted (code, config) pair's OWN bin — emit
            # cells have cell_cnt > 0 by construction.
            emit_flat = ii * P + kk
            mean_t_vals = np.divide(
                s_t[emit_flat], cell_cnt[emit_flat],
                out=np.full(emit_flat.size, np.nan),
                where=cell_cnt[emit_flat] > 0,
            )
            mean_z_vals = np.divide(
                s_z[emit_flat], cell_cnt[emit_flat],
                out=np.full(emit_flat.size, np.nan),
                where=cell_cnt[emit_flat] > 0,
            )
            base: list[dict] = []
            for row_n, (k, i) in enumerate(zip(kk.tolist(), ii.tolist())):
                speed_name = PX_VOL_SPEEDS[(sl.start + k) // _N_STATES]
                base.append({
                    "sec_type": sec_type,
                    "code": codes[i],
                    "stat_month": mw.stat_month,
                    "px_speed": speed_name,
                    "vol_state": PX_VOL_VOL_STATES[k % _N_STATES],
                    "side": PX_VOL_SPEED_SIDE[speed_name],
                    "is_market_hyped": hyped,
                    "sigma_window": PX_VOL_SIGMA_WINDOW,
                    "lb_window": PX_VOL_LB_WINDOW,
                    "k_slow_up": PX_VOL_K_SLOW_UP,
                    "k_slow_dn": PX_VOL_K_SLOW_DN,
                    "k_sharp": PX_VOL_K_SHARP,
                    "z_heavy": PX_VOL_Z_HEAVY,
                    "z_shrink": PX_VOL_Z_SHRINK,
                    "sigma_floor": PX_VOL_SIGMA_FLOOR,
                    "lookback_period": LOOKBACK_PERIOD,
                    # the bucket's MEAN merged-state-run length (the
                    # identity registry's streak column).
                    "streak_signal_days": round(
                        float(mean_streak[row_n]), 2),
                    # config JSONB — asyncpg COPY needs a JSON text
                    # string (compute_std precedent).
                    "config": json.dumps({
                        "mean_t": round6(mean_t_vals[row_n]),
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
