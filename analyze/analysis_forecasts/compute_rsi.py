"""RSI extreme-bucket monthly aggregation (analysis_forecasts) — sparse
tensor engine.

For each stat month's trailing 5-year window [lo, hi) of the (T, C) wide
grid and each RSI window W:

  1. Sort the window slice of rsi_{W} column-wise ONCE (np.sort puts NaN
     last) — every percentile threshold (top + bottom × 1/5/10/25) is then
     a linear-interpolated gather from the same sorted matrix.
  2. Threshold τ (per code) = quantile at q over the column's valid_n
     non-NULL values: pos = q·(valid_n−1), τ = S[⌊pos⌋] + frac·(S[⌈pos⌉−S[⌊pos⌋]).
  3. Bucket mask: top → V ≥ τ(q=1−pct/100); bottom → V ≤ τ(q=pct/100)
     (NaN comparisons are False, so invalid days never enter a bucket).
     Codes whose own history does not span the full window (first data
     date > window start) are gated out — no partial-window stats.
  4. The (side, pct) configs are stacked into ONE (T, C, K) bucket mask
     tensor (K = len(RSI_SIDES)·len(pcts), side-major). Cooldown
     suppression runs ONCE per cooldown value on the flattened (T, C·K)
     stack (columns are config-independent), then the mask is SPARSIFIED
     with a single np.nonzero: every downstream reduction (cooldown
     counts, market-hype split, per-horizon mean / high / low n-day
     forward change and P(reverse beyond the code's adaptive
     reverse_threshold) via wide.aggregate_horizons_sparse)
     works on the trigger-cell lists — bincount/reduceat passes scaling
     with the trigger count instead of dense T·C·K tensors, and the row
     payload is expanded by wide.build_result_rows (vectorized rounding).
     No per-config / per-code Python loops. Per (code, horizon) the
     reversal probability is computed against the code's ADAPTIVE
     reverse threshold (wide.reverse_thresholds: k·σ of the code's
     window forward changes in "std" mode, fixed-bar fallback).

Yields (stat_month, rows) so __main__ can split each row into the
mov_rsi motivation dicts and the forecast_results result dicts and write
month-major.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    COOLDOWN_DAYS,
    FORWARD_HORIZONS,
    RSI_PCTS,
    RSI_SIDES,
    RSI_WINDOWS,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizons_sparse,
    apply_cooldown,
    build_result_rows,
    reverse_thresholds,
    window_sigmas,
)


def _thresholds(
    S: np.ndarray,
    valid_n: np.ndarray,
    col: np.ndarray,
    q: float,
) -> np.ndarray:
    """Column-wise linear-interpolated quantile gather from the sorted
    window matrix S (NaN-last). Columns with valid_n == 0 → NaN.

    Also imported by analyze.analysis_signals.signals._base
    (single-q form) — keep the signature.
    """
    n_safe = np.maximum(valid_n, 1)
    pos = q * (n_safe - 1)
    i0 = np.floor(pos).astype(np.int64)
    i1 = np.minimum(i0 + 1, n_safe - 1)
    frac = pos - i0
    thr = S[i0, col] + frac * (S[i1, col] - S[i0, col])
    return np.where(valid_n > 0, thr, np.nan)


def compute_rsi_results(
    mats: dict[str, np.ndarray],
    chg: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    hype: np.ndarray,
    first_ord: np.ndarray,
    rsi_windows: tuple = RSI_WINDOWS,
    pcts: tuple = RSI_PCTS,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month.

    Args:
        mats: wide rsi matrices keyed f"rsi_{w}".
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
        # Per-(code, horizon) reversal bar for this window (adaptive
        # k·σ of the code's window forward changes; fixed fallback).
        thr_n = reverse_thresholds(*window_sigmas(NC0s, FINs))
        HY = hype[lo:hi]
        live2 = live[:, None]

        rows: list[dict] = []
        for w in rsi_windows:
            V = mats[f"rsi_{w}"][lo:hi]
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

            # Cooldown suppression (PK member cooldown_days):
            # after an accepted trigger day the next cooldown_days grid
            # trading days cannot join the bucket (fixed skip — triggers
            # inside the window do not restart it). cd == 0 is the
            # identity. One call for the whole (T, C·K) stack — columns
            # are config-independent.
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
                # Post-cooldown per-config trigger counts, live-gated
                # (not-yet-live codes never emit).
                count = np.bincount(
                    nz_c * K + nz_k, minlength=C * K
                ).reshape(C, K) * live2
                if not (count > 0).any():
                    continue
                hy_cells = HY[nz_t, nz_c]

                for si, side in enumerate(RSI_SIDES):
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

                    # Hype split of the bucket (PK member).
                    # Aggregates are per subset — max/high/low are
                    # non-additive, so the non-hyped subset cannot
                    # be derived from the full bucket minus the
                    # hyped one. Each subset is a cell-list filter.
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
                            st, sc, fk, C, P, side, NC0s, FINs, thr_n
                        )
                        kk, ii = np.nonzero(emit.T)
                        base: list[dict] = [
                            {
                                "sec_type": sec_type,
                                "code": codes[i],
                                "stat_month": mw.stat_month,
                                "rsi_window": w,
                                "side": side,
                                "pct": pcts[k],
                                "cooldown_days": cd,
                                "is_market_hyped": hyped,
                                # config JSONB: no extra motivation data
                                # for RSI buckets (NULL = empty config)
                                "config": None,
                            }
                            for k, i in zip(kk.tolist(), ii.tolist())
                        ]
                        rows.extend(build_result_rows(agg, kk, ii, base, thr_n))

        if rows:
            yield mw.stat_month, rows
