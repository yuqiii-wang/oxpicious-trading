"""RSI extreme-bucket monthly aggregation (analysis_forecasts).

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
  4. Each (code, w, side, pct) bucket is SPLIT into two rows by the PK
     member is_market_hyped — hyped bucket days (inside a
     mov_ave_market_hypes episode) vs non-hyped ones (each subset
     emitted only where non-empty). Aggregates per subset via masked
     einsum passes (no per-code Python loops): per-horizon results —
     mean / high / low n-day forward change and P(reverse > 1%) over
     the subset's bucket days with a valid n-day change (n ∈
     FORWARD_HORIZONS).

Yields (stat_month, rows) so __main__ can split each row into the
mov_rsi motivation dicts and the forecast_results result dicts and write
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
    MAX_CHANGE_COLS,
    MAX_LOW_RATIO_COLS,
    MIN_CHANGE_COLS,
    MM_HORIZONS,
    OCCURRENCE_COUNT_COLS,
    REVERSE_PROB_COLS,
    RSI_PCTS,
    RSI_SIDES,
    RSI_WINDOWS,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizon,
    apply_cooldown,
    horizon_flags,
    round6,
)


def _thresholds(
    S: np.ndarray,
    valid_n: np.ndarray,
    col: np.ndarray,
    q: float,
) -> np.ndarray:
    """Column-wise linear-interpolated quantile gather from the sorted
    window matrix S (NaN-last). Columns with valid_n == 0 → NaN."""
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
    C = len(codes)
    col = np.arange(C)

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
        # the grid begins after the nominal window start. Applied by
        # masking the RSI values to NaN for not-yet-live codes, which
        # zeroes their bucket counts / valid_n everywhere.
        live = first_ord < mw.lo_ord
        if not live.any():
            continue

        hflags = horizon_flags(chg, lo, hi)
        FIN = {n: chg[f"FIN_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        NC0 = {n: chg[f"NC0_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        HY = hype[lo:hi]

        rows: list[dict] = []
        for w in rsi_windows:
            V = mats[f"rsi_{w}"][lo:hi]
            valid_n = np.count_nonzero(~np.isnan(V), axis=0).astype(np.int64)
            if not ((valid_n > 0) & live).any():
                continue
            S = np.sort(V, axis=0)      # NaN last — quantile gathers

            for side in RSI_SIDES:
                for p in pcts:
                    q = (1.0 - p / 100.0) if side == "top" else (p / 100.0)
                    thr = _thresholds(S, valid_n, col, q)
                    with np.errstate(invalid="ignore"):
                        mask_raw = (
                            (V >= thr[None, :]) if side == "top"
                            else (V <= thr[None, :])
                        )
                    # Full-window gate: not-yet-live codes never emit
                    # (their percentile thresholds / buckets over a
                    # partial window are meaningless and unused). Raw
                    # pre-check first — skip cooldown work entirely for
                    # configs without a single trigger.
                    if not ((mask_raw.sum(axis=0) * live) > 0).any():
                        continue

                    # Cooldown suppression (PK member cooldown_days):
                    # after an accepted trigger day the next
                    # cooldown_days grid trading days cannot join the
                    # bucket (fixed skip — triggers inside the window
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
                            if not (sub.sum(axis=0) > 0).any():
                                continue

                            # Per-horizon aggregates over the subset.
                            agg = {
                                n: aggregate_horizon(
                                    sub, NC0[n], FIN[n],
                                    hflags[
                                        f"{'DN' if side == 'top' else 'UP'}_{n}"
                                    ],
                                )
                                for n in FORWARD_HORIZONS
                            }

                            for i in np.flatnonzero(
                                (sub.sum(axis=0) > 0) & (count > 0)
                            ):
                                row: dict = {
                                    "sec_type": sec_type,
                                    "code": codes[i],
                                    "stat_month": mw.stat_month,
                                    "rsi_window": w,
                                    "side": side,
                                    "pct": p,
                                    "cooldown_days": cd,
                                    "is_market_hyped": hyped,
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
