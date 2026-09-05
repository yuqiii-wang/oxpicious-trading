"""margin_ratio_state bucket monthly aggregation (analysis_forecasts) —
sparse tensor engine.

The margin-buy intensity STATE buckets (see
database/sql/analysis/analysis_forecasts/06_margin_ratio.sql and the
2026-09 study temp_scripts/study_margin_ratio_forecast.py /
docs/margin_ratio_study.md): per stat month's trailing 5-year window
[lo, hi) of the (T, C) wide grid, a (code, date) joins ONE of the 6
ratio states:

  z = (ratio - μ)/σ  — z-scored 融资买入额/成交额 ratio (scattered "z"
      matrix; the fetch layer computes ratio = rz_buy / trading_amount
      on buy days and the rolling-1220-row shifted moments, so NaN here
      means "no bucket")
  nb = no-margin-buy flag (scattered "nb" bool matrix: rz_buy == 0
      with trading_amount > 0)

  ratio_state: no_buy nb | vlow z<=-2 | low -2<z<=-1 | mid -1<z<=+1 |
               high +1<z<=+2 | vhigh z>+2

Like px_vol_state there is NO cooldown (a state cell admits every
qualifying day), and the bucket split is by PK member is_market_hyped
only. The bucket is etf/stock only — index rz_buy is NULL so every
mask is False and no rows emit.

Per (side, hype) subset the horizon aggregates reuse
wide.aggregate_horizons_sparse against the code's ADAPTIVE reversal
bar (reverse_thresholds: k_n·σ of the window's n-day forward changes)
— the crowding states high/vhigh carry side='top' (reverse on change
< -thr, the study's bearish reading), vlow/low/no_buy side='bottom'
(reverse on change > +thr), mid side='flat' with reverse_prob = NULL
(no directional claim). The config JSONB records the bucket's mean
ratio / mean z (motivation magnitude, like px_vol's mean_t / mean_z).

Yields (stat_month, rows) so __main__ can split each row into the
margin_ratio_state motivation dicts and the forecast_results result
dicts and write month-major.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    MARGIN_RATIO_HIGH_BAR,
    MARGIN_RATIO_LOW_BAR,
    MARGIN_RATIO_STATES,
    MARGIN_RATIO_STATE_SIDE,
    MARGIN_RATIO_VHIGH_BAR,
    MARGIN_RATIO_VLOW_BAR,
    MARGIN_RATIO_Z_MIN_PERIODS,
    MARGIN_RATIO_Z_WINDOW,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizons_sparse,
    build_result_rows,
    reverse_thresholds,
    round6,
    window_sigmas,
)

_K = len(MARGIN_RATIO_STATES)               # 6 states on the z axis

# Side-batch config ranges on the state axis (MARGIN_RATIO_STATES order:
# no_buy, vlow, low | mid | high, vhigh).
_SIDE_SLICES: dict[str, slice] = {
    "bottom": slice(0, 3),        # no_buy + vlow + low
    "flat": slice(3, 4),          # mid
    "top": slice(4, 6),           # high + vhigh
}


def compute_margin_ratio_results(
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
        mats: wide state matrices keyed "z" (z-scored margin ratio —
              NaN where undefined), "nb" (no-margin-buy bool) and
              "ratio" (raw rz_buy/trading_amount, NaN off buy days —
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

        Z = mats["z"][lo:hi]
        NB = mats["nb"][lo:hi]
        RA = mats["ratio"][lo:hi]
        with np.errstate(invalid="ignore"):
            # State masks (NaN compares False → non-bucket days never
            # join): (6, T, C) stacked in MARGIN_RATIO_STATES order.
            mask = np.stack([
                NB,
                Z <= MARGIN_RATIO_VLOW_BAR,
                (Z > MARGIN_RATIO_VLOW_BAR) & (Z <= MARGIN_RATIO_LOW_BAR),
                (Z > MARGIN_RATIO_LOW_BAR) & (Z <= MARGIN_RATIO_HIGH_BAR),
                (Z > MARGIN_RATIO_HIGH_BAR) & (Z <= MARGIN_RATIO_VHIGH_BAR),
                Z > MARGIN_RATIO_VHIGH_BAR,
            ])
        # (6, T, C) → (T, C, K) with k = state_idx.
        n_rows = hi - lo
        mask = mask.transpose(1, 2, 0).reshape(n_rows, C, _K)
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
                # motivation magnitude, like px_vol's mean_t/mean_z).
                # no_buy cells have NaN ratio/z by construction — their
                # bin sums stay NaN and round6 maps them to None.
                s_z = np.bincount(fk, weights=Z[st, sc], minlength=C * P)
                s_r = np.bincount(fk, weights=RA[st, sc], minlength=C * P)
                # The bins are CODE-major (flat = i*P + k), so gather
                # each emitted (code, config) pair's OWN bin — emit
                # cells have cell_cnt > 0 by construction.
                emit_flat = ii * P + kk
                mean_z_vals = np.divide(
                    s_z[emit_flat], cell_cnt[emit_flat],
                    out=np.full(emit_flat.size, np.nan),
                    where=cell_cnt[emit_flat] > 0,
                )
                mean_r_vals = np.divide(
                    s_r[emit_flat], cell_cnt[emit_flat],
                    out=np.full(emit_flat.size, np.nan),
                    where=cell_cnt[emit_flat] > 0,
                )
                base: list[dict] = []
                for row_n, (k, i) in enumerate(zip(kk.tolist(), ii.tolist())):
                    state = MARGIN_RATIO_STATES[sl.start + k]
                    base.append({
                        "sec_type": sec_type,
                        "code": codes[i],
                        "stat_month": mw.stat_month,
                        "ratio_state": state,
                        "side": MARGIN_RATIO_STATE_SIDE[state],
                        "is_market_hyped": hyped,
                        "z_window": MARGIN_RATIO_Z_WINDOW,
                        "z_min_periods": MARGIN_RATIO_Z_MIN_PERIODS,
                        "vlow_bar": MARGIN_RATIO_VLOW_BAR,
                        "low_bar": MARGIN_RATIO_LOW_BAR,
                        "high_bar": MARGIN_RATIO_HIGH_BAR,
                        "vhigh_bar": MARGIN_RATIO_VHIGH_BAR,
                        # config JSONB — asyncpg COPY needs a JSON text
                        # string (compute_px_vol precedent).
                        "config": json.dumps({
                            "mean_ratio": round6(mean_r_vals[row_n]),
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
