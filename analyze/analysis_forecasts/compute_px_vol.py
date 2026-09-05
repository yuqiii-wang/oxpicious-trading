"""px_vol_state bucket monthly aggregation (analysis_forecasts) —
sparse tensor engine.

The recent-day price-change × trading-amount STATE buckets (see
database/sql/analysis/analysis_forecasts/05_px_vol_state.sql and the
2026-09 temp_scripts studies): per stat month's trailing 5-year window
[lo, hi) of the (T, C) wide grid, a (code, date) joins ONE of the
15 speed × volume cells when BOTH legs hold:

  t = ret_1d / σ_ret(code, 255 rows ending t-1)   (scattered "t" matrix;
      NULL where σ is degenerate or below sigma_floor — the fetch layer
      applies the floor, so NaN here means "never a bucket")
  z = z-scored 量比                                (scattered "z" matrix)

  px_speed: sharp_up t>2.0 | slow_up 1.26<t<=2.0 | flat -1.29<=t<=1.26
            | slow_dn -2.0<=t<-1.29 | sharp_dn t<-2.0
  vol_state: heavy z>2.0 | normal | shrink z<-0.92

Unlike the mov_* EVENT buckets there is NO cooldown (a state cell
admits every qualifying day), and the bucket split is by PK member
is_market_hyped only. Config axis: k = speed_idx * 3 + state_idx with
PX_VOL_SPEEDS × PX_VOL_VOL_STATES ordering.

Per (side, hype) subset the horizon aggregates reuse
wide.aggregate_horizons_sparse (bincount/reduceat over the sparse
trigger cells) against the code's ADAPTIVE reversal bar
(reverse_thresholds: k_n·σ of the window's n-day forward changes) —
top speeds reverse on change < -thr, bottom speeds on change > +thr;
flat rows carry side='flat' and get reverse_prob = NULL (no
directional claim). The config JSONB records the bucket's mean t /
mean z (motivation magnitude, like mov_std's breach excesses).

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
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month.

    Args:
        mats: wide state matrices keyed "t" (σ-standardized price
              speed) and "z" (z-scored 量比) — NaN where the day has
              no valid state.
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
        # Per-(code, horizon) adaptive reversal bar for this window.
        thr_n = reverse_thresholds(*window_sigmas(NC0s, FINs))
        HY = hype[lo:hi]

        T = mats["t"][lo:hi]
        Z = mats["z"][lo:hi]
        with np.errstate(invalid="ignore"):
            # Speed masks (NaN compares False → invalid days never
            # join): (5, T, C) stacked in PX_VOL_SPEEDS order.
            speed = np.stack([
                T > PX_VOL_K_SHARP,
                (T > PX_VOL_K_SLOW_UP) & (T <= PX_VOL_K_SHARP),
                (T >= -PX_VOL_K_SLOW_DN) & (T <= PX_VOL_K_SLOW_UP),
                (T >= -PX_VOL_K_SHARP) & (T < -PX_VOL_K_SLOW_DN),
                T < -PX_VOL_K_SHARP,
            ])
            vol = np.stack([
                Z > PX_VOL_Z_HEAVY,
                (Z >= PX_VOL_Z_SHRINK) & (Z <= PX_VOL_Z_HEAVY),
                Z < PX_VOL_Z_SHRINK,
            ])
        # (5, 3, T, C) → (T, C, K) with k = speed_idx*3 + state_idx.
        n_rows = hi - lo
        mask = (speed[:, None] & vol[None, :]) \
            .transpose(2, 3, 0, 1).reshape(n_rows, C, _K)
        nz_t, nz_c, nz_k = np.nonzero(mask)
        if nz_t.size == 0:
            continue
        nz_t = nz_t.astype(np.int32)
        nz_c = nz_c.astype(np.int32)
        nz_k = nz_k.astype(np.int32)
        hy_cells = HY[nz_t, nz_c]

        rows: list[dict] = []
        for side, sl in _SIDE_SLICES.items():
            in_side = (nz_k >= sl.start) & (nz_k < sl.stop)
            if not in_side.any():
                continue
            t_s = nz_t[in_side]
            c_s = nz_c[in_side]
            k_s = nz_k[in_side] - sl.start
            P = sl.stop - sl.start
            flat_s = c_s * P + k_s
            hy_s = hy_cells[in_side]

            for hyped in (False, True):
                sel = hy_s if hyped else ~hy_s
                if not sel.any():
                    continue
                st = t_s[sel]
                sc = c_s[sel]
                fk = flat_s[sel]
                # Group-ascending cell order — one stable sort shared
                # by the emit count and every horizon's reductions.
                order = np.argsort(fk, kind="stable")
                st = st[order]
                sc = sc[order]
                fk = fk[order]
                cell_cnt = np.bincount(fk, minlength=C * P)
                # (C, P) emit grid — same shape convention as the
                # mov_* engines (build_result_rows gathers kk/ii from
                # its transpose).
                emit = cell_cnt.reshape(C, P) > 0
                if not emit.any():
                    continue

                agg = aggregate_horizons_sparse(
                    st, sc, fk, C, P, side, NC0s, FINs, thr_n
                )
                kk, ii = np.nonzero(emit.T)
                # Per-bucket mean state magnitudes (config JSONB — the
                # motivation magnitude, like mov_std's breach excess):
                # every sparse cell has valid t/z by construction, so
                # the all-cell sums are the per-cell mean numerators.
                s_t = np.bincount(fk, weights=T[st, sc], minlength=C * P)
                s_z = np.bincount(fk, weights=Z[st, sc], minlength=C * P)
                # The bins are CODE-major (flat = i*P + k), so gather
                # each emitted (code, config) pair's OWN bin — the
                # former mean_t[k] read code-0's bin for every row.
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
