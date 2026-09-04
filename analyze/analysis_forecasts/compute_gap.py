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
     tensor (side-major), cooldown-suppressed ONCE on the flattened
     (T, C·K) stack, sparsified with a single np.nonzero, and every
     downstream reduction (hype split, per-horizon mean / high / low
     n-day forward change and P(reverse > 1%) via
     wide.aggregate_horizons_sparse) works on the trigger-cell lists.
     The row payload is expanded by wide.build_result_rows.

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
    COOLDOWN_DAYS,
    FORWARD_HORIZONS,
    GAP_PCTS,
    GAP_SIDES,
    GAP_WINDOWS,
)
from analyze.analysis_forecasts.compute_rsi import _thresholds
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizons_sparse,
    apply_cooldown,
    build_result_rows,
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
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month.

    Args:
        mats: wide gap matrices keyed f"gap_{w}".
        chg:  shared change matrices (build_change_matrices):
              NC0_{n} / FIN_{n} for n in FORWARD_HORIZONS, DN, UP.
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
            # enter a bucket).
            with np.errstate(invalid="ignore"):
                V3 = V[:, :, None]
                mask_raw = np.concatenate(
                    [V3 >= thr[:, :P][None], V3 <= thr[:, P:][None]],
                    axis=2,
                )  # (T, C, K)

            # Cooldown suppression (PK member cooldown_days) — one call
            # for the whole (T, C·K) stack (columns are config-independent).
            T = mask_raw.shape[0]
            for cd in COOLDOWN_DAYS:
                mask3 = (
                    mask_raw if cd == 0
                    else apply_cooldown(
                        mask_raw.reshape(T, -1), cd
                    ).reshape(mask_raw.shape)
                )
                # Sparsify ONCE per cooldown value: every downstream
                # reduction works on the trigger-cell lists.
                nz_t, nz_c, nz_k = np.nonzero(mask3)
                if nz_t.size == 0:
                    continue
                nz_t = nz_t.astype(np.int32)
                nz_c = nz_c.astype(np.int32)
                nz_k = nz_k.astype(np.int32)
                # Post-cooldown per-config trigger counts, live-gated.
                count = np.bincount(
                    nz_c * K + nz_k, minlength=C * K
                ).reshape(C, K) * live2
                if not (count > 0).any():
                    continue
                hy_cells = HY[nz_t, nz_c]

                for si, side in enumerate(GAP_SIDES):
                    sl = slice(si * P, (si + 1) * P)
                    cnt_s = count[:, sl]
                    if not (cnt_s > 0).any():
                        continue
                    in_side = (nz_k >= si * P) & (nz_k < (si + 1) * P)
                    if not in_side.any():
                        continue
                    t_s = nz_t[in_side]
                    c_s = nz_c[in_side]
                    k_s = nz_k[in_side] - si * P
                    flat_s = c_s * P + k_s
                    hy_s = hy_cells[in_side]

                    # Hype split of the bucket (PK member) — each subset
                    # is a cell-list filter (max/min are non-additive).
                    for hyped in (False, True):
                        sel = hy_s if hyped else ~hy_s
                        if not sel.any():
                            continue
                        st = t_s[sel]
                        sc = c_s[sel]
                        fk = flat_s[sel]
                        # Group-ascending cell order — one stable sort
                        # shared by the subset count and every horizon's
                        # bincount / reduceat reductions.
                        order = np.argsort(fk, kind="stable")
                        st = st[order]
                        sc = sc[order]
                        fk = fk[order]
                        emit = (
                            np.bincount(fk, minlength=C * P).reshape(C, P)
                            > 0
                        ) & (cnt_s > 0)
                        if not emit.any():
                            continue

                        agg = aggregate_horizons_sparse(
                            st, sc, fk, C, P, side, NC0s, FINs
                        )
                        kk, ii = np.nonzero(emit.T)
                        base: list[dict] = [
                            {
                                "sec_type": sec_type,
                                "code": codes[i],
                                "stat_month": mw.stat_month,
                                "gap_window": w,
                                "side": side,
                                "pct": pcts[k],
                                "cooldown_days": cd,
                                "is_market_hyped": hyped,
                                # config JSONB: no extra motivation data
                                # for gap buckets (NULL = empty config)
                                "config": None,
                            }
                            for k, i in zip(kk.tolist(), ii.tolist())
                        ]
                        rows.extend(build_result_rows(agg, kk, ii, base))

        if rows:
            yield mw.stat_month, rows
