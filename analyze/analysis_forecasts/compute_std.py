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
dates fall inside the code's analysis.mov_ave_market_hypes episodes:
one row for the hyped breach days and one for the non-hyped breach days
(each subset emitted only where non-empty — no breach, no record).

The (k, side) configs are stacked into ONE (T, C, K) bucket mask tensor
per MA window (K = len(STD_MULTIPLES), side-major: the first half of
the config axis is the upper-side ks, the second the lower-side ks).
Cooldown suppression runs ONCE per cooldown value on the flattened
(T, C·K) stack (columns are config-independent), then the mask is
SPARSIFIED with a single np.nonzero: every downstream reduction works
on the trigger-cell lists — the hype split is a cell filter, the
per-horizon mean / high / low n-day forward change and
P(reverse beyond the code's adaptive reverse_threshold)
come from wide.aggregate_horizons_sparse (bincount/reduceat passes
scaling with the trigger count), and the breach magnitude aggregates
(mean_excess_close, mean_excess_max / max_excess_max — MEAN / MAX
fractional close / intraday excursion beyond the band, high for upper /
low for lower breaches) are gathered at the cells themselves: the
per-cell band level is a vectorized
``MA[t,c] ± ks_arr[k]·SD[t,c]`` gather (no dense (T, C, K) excursion
tensors), counts/sums accumulate with np.bincount and the per-group
max with one np.maximum.reduceat over the group-contiguous sorted
cells. The row payload (forecast_results fields) is expanded by
wide.build_result_rows (vectorized rounding). No per-config / per-code
Python loops.

Yields (stat_month, rows) so __main__ can split each row into the
mov_std motivation dicts and the forecast_results result dicts and write
month-major.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    COOLDOWN_DAYS,
    FORWARD_HORIZONS,
    MA_WINDOWS,
    STD_MULTIPLES,
    STD_SIDES,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizons_sparse,
    apply_cooldown,
    build_result_rows,
    reverse_thresholds,
    round6,
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
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month.

    Args:
        mats: wide matrices keyed "price", "high", "low", f"ma_{w}",
              f"std_{w}".
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
    K = len(ks)
    ks_arr = np.asarray(ks, dtype=np.float64)
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
        P = mats["price"][lo:hi]
        HIGH = mats["high"][lo:hi]
        LOW = mats["low"][lo:hi]
        HY = hype[lo:hi]
        live2 = live[:, None]
        # ext_ok rejects high/low = 0 placeholders (some CSIndex files
        # carry high = close − 0.01 / low = 0 when intraday data is
        # absent) — "excursion beyond the band" must be positive to
        # mean anything.
        ext_ok = {"upper": HIGH > 0, "lower": LOW > 0}

        rows: list[dict] = []
        for w in ma_windows:
            MA = mats[f"ma_{w}"][lo:hi]
            SD = mats[f"std_{w}"][lo:hi]

            # Per-(k, side) breach masks stacked side-major into ONE
            # (T, C, 2K) tensor. Raw pre-check first — skip cooldown
            # work entirely for windows without a single breach.
            mask_sides = []
            with np.errstate(invalid="ignore"):
                for side in STD_SIDES:
                    ms = [
                        P > MA + k * SD if side == "upper"
                        else P < MA - k * SD
                        for k in ks
                    ]
                    mask_sides.append(np.stack(ms, axis=2))
                mask_raw = np.concatenate(mask_sides, axis=2)  # (T, C, 2K)
            if not ((mask_raw.sum(axis=0) * live2) > 0).any():
                continue

            # Cooldown suppression (PK member cooldown_days):
            # after an accepted breach day the next cooldown_days grid
            # trading days cannot join the bucket (fixed skip — breaches
            # inside the window do not restart it). cd == 0 is the
            # identity. One call for the whole (T, C·2K) stack —
            # columns are config-independent.
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
                # Post-cooldown per-config breach counts, live-gated
                # (not-yet-live codes never emit).
                count = np.bincount(
                    nz_c * (2 * K) + nz_k, minlength=C * 2 * K
                ).reshape(C, 2 * K) * live2
                if not (count > 0).any():
                    continue
                hy_cells = HY[nz_t, nz_c]

                for si, side in enumerate(STD_SIDES):
                    sl = slice(si * K, (si + 1) * K)
                    cnt_s = count[:, sl]
                    if not (cnt_s > 0).any():
                        continue
                    in_side = (nz_k >= si * K) & (nz_k < (si + 1) * K)
                    if not in_side.any():
                        continue
                    t_s = nz_t[in_side]
                    c_s = nz_c[in_side]
                    k_s = nz_k[in_side] - si * K
                    flat_s = c_s * K + k_s
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
                        # shared by the subset count, every horizon's
                        # bincount / reduceat reductions and the breach
                        # magnitude reduceat.
                        order = np.argsort(fk, kind="stable")
                        st = st[order]
                        sc = sc[order]
                        fk = fk[order]
                        emit = (
                            np.bincount(fk, minlength=C * K).reshape(C, K)
                            > 0
                        ) & (cnt_s > 0)
                        if not emit.any():
                            continue

                        agg = aggregate_horizons_sparse(
                            st, sc, fk, C, K, side, NC0s, FINs, thr_n
                        )
                        kk, ii = np.nonzero(emit.T)
                        base: list[dict] = [
                            {
                                "sec_type": sec_type,
                                "code": codes[i],
                                "stat_month": mw.stat_month,
                                "ma_window": w,
                                "k": ks[j],
                                "side": side,
                                "cooldown_days": cd,
                                "is_market_hyped": hyped,
                            }
                            for j, i in zip(kk.tolist(), ii.tolist())
                        ]

                        # Breach magnitude aggregates over the subset —
                        # gathered at the cells (band level per cell is
                        # a vectorized MA ± ks_arr[fk]·SD gather; no
                        # dense excursion tensors). Counts/sums via
                        # bincount, per-group max via reduceat.
                        with np.errstate(divide="ignore", invalid="ignore"):
                            kf = fk % K  # config axis of the group id
                            thr = (
                                MA[st, sc] + ks_arr[kf] * SD[st, sc]
                                if side == "upper"
                                else MA[st, sc] - ks_arr[kf] * SD[st, sc]
                            )
                            ec = (
                                (P[st, sc] - thr) / thr if side == "upper"
                                else (thr - P[st, sc]) / thr
                            )
                            em = (
                                (HIGH[st, sc] - thr) / thr if side == "upper"
                                else (thr - LOW[st, sc]) / thr
                            )
                        # close-based excursion: any finite one counts
                        # (band 0 → inf / NaN bands → NaN filtered by
                        # isfinite); intraday excursion only genuinely
                        # POSITIVE ones (+ ext_ok placeholder guard) —
                        # sub-band extremes are rejected (exc_m > 0).
                        vc = np.isfinite(ec)
                        vm = (em > 0) & ext_ok[side][st, sc]
                        CP = C * K
                        cc = np.bincount(fk[vc], minlength=CP).reshape(C, K)
                        scc = np.bincount(
                            fk[vc], weights=ec[vc], minlength=CP
                        ).reshape(C, K)
                        cm = np.bincount(fk[vm], minlength=CP).reshape(C, K)
                        smc = np.bincount(
                            fk[vm], weights=em[vm], minlength=CP
                        ).reshape(C, K)
                        bounds = np.flatnonzero(fk[1:] != fk[:-1]) + 1
                        starts = np.concatenate(([0], bounds))
                        gid = fk[starts]
                        mx = np.full(CP, -np.inf)
                        # Groups with no valid cell keep the -inf default
                        # (the legacy where(..., -inf).max semantics).
                        mx[gid] = np.maximum.reduceat(
                            np.where(vm, em, -np.inf), starts
                        )
                        mx = mx.reshape(C, K)

                        R = kk.size
                        cc_e = cc[ii, kk]
                        cm_e = cm[ii, kk]
                        mean_close = np.divide(
                            scc[ii, kk], cc_e,
                            out=np.full(R, np.nan), where=cc_e > 0,
                        )
                        mean_max = np.divide(
                            smc[ii, kk], cm_e,
                            out=np.full(R, np.nan), where=cm_e > 0,
                        )
                        # config JSONB: breach magnitude metrics
                        # (migrated from mov_std scalar columns)
                        # asyncpg COPY needs a JSON text string
                        for row, mc, mm, mxv in zip(
                            base, mean_close, mean_max, mx[ii, kk]
                        ):
                            row["config"] = json.dumps({
                                "mean_excess_close": round6(mc),
                                "mean_excess_max": round6(mm),
                                "max_excess_max": round6(mxv),
                            })

                        rows.extend(build_result_rows(agg, kk, ii, base, thr_n))

        if rows:
            yield mw.stat_month, rows
