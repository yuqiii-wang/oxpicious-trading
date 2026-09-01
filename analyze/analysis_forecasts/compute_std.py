"""Bollinger-breach monthly aggregation (analysis_forecasts).

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

Per subset the aggregates are computed via masked einsum/max passes:
breach magnitude — mean_excess_close (MEAN fractional close excursion
beyond the band), mean_excess_max / max_excess_max (MEAN / MAX fractional
INTRADAY excursion, high for upper / low for lower breaches) — plus
per-horizon results — mean / high / low n-day forward change and
P(reverse > 1% against the breach side) over the subset's breach days
with a valid n-day change (n ∈ FORWARD_HORIZONS).

Yields (stat_month, rows) so __main__ can split each row into the
mov_std motivation dicts and the forecast_results result dicts and write
month-major.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    AVE_CHANGE_COLS,
    COOLDOWN_DAYS,
    FORWARD_HORIZONS,
    MA_WINDOWS,
    MAX_CHANGE_COLS,
    MAX_LOW_RATIO_COLS,
    MIN_CHANGE_COLS,
    MM_HORIZONS,
    OCCURRENCE_COUNT_COLS,
    REVERSE_PROB_COLS,
    STD_MULTIPLES,
    STD_SIDES,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizon,
    apply_cooldown,
    horizon_flags,
    round6,
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
              NC0_{n} / FIN_{n} for n in FORWARD_HORIZONS, DN, UP.
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

        hflags = horizon_flags(chg, lo, hi)
        FIN = {n: chg[f"FIN_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        NC0 = {n: chg[f"NC0_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        P = mats["price"][lo:hi]
        HIGH = mats["high"][lo:hi]
        LOW = mats["low"][lo:hi]
        HY = hype[lo:hi]

        rows: list[dict] = []
        for w in ma_windows:
            MA = mats[f"ma_{w}"][lo:hi]
            SD = mats[f"std_{w}"][lo:hi]
            for k in ks:
                up_thr = MA + k * SD
                dn_thr = MA - k * SD
                # Fractional excursions beyond the band (positive on a
                # breach): close-based mean + intraday mean/max. Band 0 →
                # inf → filtered by isfinite (band 0 with a breach is
                # degenerate data anyway).
                with np.errstate(divide="ignore", invalid="ignore"):
                    up_c = (P - up_thr) / up_thr
                    up_m = (HIGH - up_thr) / up_thr
                    dn_c = (dn_thr - P) / dn_thr
                    dn_m = (dn_thr - LOW) / dn_thr
                with np.errstate(invalid="ignore"):
                    masks = (
                        ("upper", P > up_thr, up_c, up_m, HIGH > 0),
                        ("lower", P < dn_thr, dn_c, dn_m, LOW > 0),
                    )
                for side, mask_raw, exc_c, exc_m, ext_ok in masks:
                    # Full-window gate: not-yet-live codes never emit
                    # (buckets over a partial window are meaningless).
                    # Raw pre-check first — skip cooldown work entirely
                    # for configs without a single breach.
                    if not ((mask_raw.sum(axis=0) * live) > 0).any():
                        continue

                    # Cooldown suppression (PK member cooldown_days):
                    # after an accepted breach day the next
                    # cooldown_days grid trading days cannot join the
                    # bucket (fixed skip — breaches inside the window
                    # do not restart it). cd == 0 is the identity.
                    for cd in COOLDOWN_DAYS:
                        mask = (
                            mask_raw if cd == 0
                            else apply_cooldown(mask_raw, cd)
                        )
                        count = mask.sum(axis=0) * live
                        if not (count > 0).any():
                            continue

                        # Hype split of the bucket (PK member).
                        # Aggregates are per subset — max/high/low are
                        # non-additive, so the non-hyped subset cannot
                        # be derived from the full bucket minus the
                        # hyped one.
                        for hyped in (False, True):
                            sub = (mask & HY) if hyped else (mask & ~HY)
                            subcount = sub.sum(axis=0)
                            emit = (count > 0) & (subcount > 0)
                            if not emit.any():
                                continue
                            idx = np.flatnonzero(emit)

                            # Breach magnitude: mean close excursion
                            # (over days with a finite one) + mean/max
                            # intraday excursion over days with a
                            # genuinely POSITIVE one (-inf → NULL when
                            # no breach day has one). ext_ok rejects
                            # high/low = 0 placeholders and exc_m > 0
                            # rejects sub-band extremes (some CSIndex
                            # files carry high = close − 0.01 /
                            # low = 0 when intraday data is absent) —
                            # "excursion beyond the band" must be
                            # positive to mean anything.
                            valid_c = sub & np.isfinite(exc_c)
                            cnt_c = valid_c.sum(axis=0)
                            xc0 = np.where(valid_c, exc_c, 0.0)
                            s_c = np.einsum("ij,ij->j", valid_c, xc0)
                            valid_m = sub & (exc_m > 0) & ext_ok
                            cnt_m = valid_m.sum(axis=0)
                            xm0 = np.where(valid_m, exc_m, 0.0)
                            s_m = np.einsum("ij,ij->j", valid_m, xm0)
                            mx = np.max(
                                np.where(valid_m, exc_m, -np.inf), axis=0
                            )

                            # Per-horizon aggregates over the subset.
                            agg = {
                                n: aggregate_horizon(
                                    sub, NC0[n], FIN[n],
                                    hflags[
                                        f"{'DN' if side == 'upper' else 'UP'}_{n}"
                                    ],
                                )
                                for n in FORWARD_HORIZONS
                            }

                            for i in idx:
                                row: dict = {
                                    "sec_type": sec_type,
                                    "code": codes[i],
                                    "stat_month": mw.stat_month,
                                    "ma_window": w,
                                    "k": k,
                                    "side": side,
                                    "cooldown_days": cd,
                                    "is_market_hyped": hyped,
                                    "mean_excess_close": (
                                        round6(s_c[i] / cnt_c[i])
                                        if cnt_c[i] > 0 else None
                                    ),
                                    "mean_excess_max": (
                                        round6(s_m[i] / cnt_m[i])
                                        if cnt_m[i] > 0 else None
                                    ),
                                    "max_excess_max": (
                                        round6(mx[i])
                                        if np.isfinite(mx[i]) else None
                                    ),
                                }
                                for n in FORWARD_HORIZONS:
                                    cn, s, hgh, low, rev = (
                                        int(agg[n][0][i]), agg[n][1][i],
                                        agg[n][2][i], agg[n][3][i],
                                        agg[n][4][i],
                                    )
                                    row[AVE_CHANGE_COLS[n]] = (
                                        round6(s / cn) if cn > 0 else None
                                    )
                                    if n in MM_HORIZONS:
                                        row[MAX_CHANGE_COLS[n]] = round6(hgh)
                                        row[MIN_CHANGE_COLS[n]] = round6(low)
                                        # within-window close swing amplitude:
                                        # (1 + max change pct) / (1 + min change pct)
                                        # = max(close[t+1..t+n]) / min(close[t+1..t+n])
                                        row[MAX_LOW_RATIO_COLS[n]] = (
                                            round6((1 + hgh) / (1 + low))
                                            if cn > 0 and low > -1 else None
                                        )
                                    row[REVERSE_PROB_COLS[n]] = (
                                        round6(rev / cn) if cn > 0 else None
                                    )
                                    row[OCCURRENCE_COUNT_COLS[n]] = cn
                                rows.append(row)

        if rows:
            yield mw.stat_month, rows
