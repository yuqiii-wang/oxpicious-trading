"""Result-row expansion (analyze.analysis_forecasts.wide.results).

``build_result_rows`` expands one emit batch's gathered aggregates into
the forecast_results payload dicts — 5 period rows per bucket (the four
horizons next/5d/20d/60d plus the FIXED-weight blended 'mixed' row the
analysis_signals confirmation gate reads), bucket-major so the writer
can stride by len(ALL_PERIODS). The mixed row mirrors the idempotent
SQL backfill (database/sql/analysis/analysis_forecasts/01_forecast_
results.sql): ave / reverse_prob / max_low_change_ratio are weight means
renormalized over the horizons whose stats exist, std_change is the
mixture dispersion sqrt(Σw·E[x²] − (Σw·mean)²) — NOT the mean of the
stds — occurrence_count the MIN positive leg count, threshold the
full-weight mean of the four bars, and the extrema + per-period date /
excess arrays are NULL (they do not blend). Blends run over the same
6dp-rounded leg values the rows carry (the NUMERIC(10,6) scale — the
compute_base._leg_matrix precedent), so python and SQL backfill agree
digit-for-digit.

``split_forecast_rows`` splits computed rows into the motivation
(mov_*) and result (forecast_results) write dicts.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from analyze.analysis_forecasts.config import (
    ALL_PERIODS,
    FORWARD_HORIZONS,
    MM_HORIZONS,
    MIXED_HORIZON_WEIGHTS,
    PERIOD_FOR_HORIZON,
    PERIOD_MIXED,
    RESULT_COLUMNS,
)


def _round_none(arr: np.ndarray) -> list[float | None]:
    """float array → rounded 6dp list with non-finite → None (the
    legacy per-row round6 semantics, vectorized)."""
    r = np.round(np.where(np.isfinite(arr), arr, np.nan), 6)
    return [None if x != x else x for x in r.tolist()]


def build_result_rows(
    agg: dict[int, HorizonAgg],
    kk: np.ndarray,
    ii: np.ndarray,
    base: dict[str, list],
    thr_n: dict[int, np.ndarray],
) -> list[dict]:
    """Expand one emit batch into (len(ALL_PERIODS) × R) result payload
    dicts — one per (bucket × period) combination. Each dict carries the
    motivation fields + config + period + the CONSOLIDATED
    forecast_results columns (no period suffix; the ``period`` key
    carries that role).

    forecast_id is NOT assigned here — callers allocate one per bucket
    and share it across the period rows (1:len(ALL_PERIODS) mov →
    forecast_results).

    Args:
        agg: aggregate_horizons_sparse output.
        kk:  (R,) config axis of the emit positions.
        ii:  (R,) code axis of the emit positions.
        base: COLUMNAR motivation payload (base_rows) — a dict of (R,)
              lists (identity + family bucket keys + lookback +
              streak_signal_days + config), one entry per bucket aligned
              with kk/ii. Fanned out across the period rows here — the
              one place the per-bucket row plumbing exists (the metric
              engines stay loop-free).
        thr_n: per horizon n — (C,) reversal bar (reverse_thresholds);
              emitted as the row's ``threshold`` (the bar that row's
              reverse_prob was computed against).

    Returns:
        (5·R,) dicts — 5 period rows per bucket (next → 5d → 20d → 60d
        → mixed), period-major (all 5 periods of bucket 0, then all 5
        of bucket 1, ...) so the caller can stride by len(ALL_PERIODS)
        to group periods per bucket.
    """
    R = kk.size
    # Config-axis width P (flat group id = code·P + config — the td dict
    # key space of aggregate_horizons_sparse).
    P = agg[next(iter(agg))][0].shape[1]
    ii_l = ii.tolist()
    kk_l = kk.tolist()

    # First gather all horizon payloads (vectorized per horizon)...
    horizon_payloads: dict[int, dict] = {}
    for n in FORWARD_HORIZONS:
        period = PERIOD_FOR_HORIZON[n]
        cnt, s, s2, hi_e, lo_e, hi_p, lo_p, rev, td, ss, se, sd, te = agg[n]
        cn = cnt[ii, kk]          # (R,) occurrence counts
        pos = cn > 0

        mean_raw = np.divide(
            s[ii, kk], cn, out=np.full(R, np.nan), where=pos)
        ave = _round_none(mean_raw)
        # Population std over the SAME valid days as ave_change:
        # sqrt(E[x²] − E[x]²), floored at 0 (rounding-guard). s2 has
        # invalid days contributing 0 and cn is the valid-day count, so
        # s2/cn is exactly E[x²] over valid days. pos == False keeps the
        # NaN → None chain (mean_raw NaN → var NaN → _round_none None).
        var = np.divide(
            s2[ii, kk], cn, out=np.full(R, np.nan), where=pos) \
            - mean_raw ** 2
        std = _round_none(np.sqrt(np.maximum(var, 0.0)))

        if n in MM_HORIZONS:
            hi_v = hi_e[ii, kk]   # (R,) max endpoint n-day change
            lo_v = lo_e[ii, kk]   # (R,) min endpoint n-day change
            max_vals = _round_none(hi_v)
            min_vals = _round_none(lo_v)
            # The SWING ratio: (1 + max path high) / (1 + min path low)
            # over the bucket's trigger days — the highest close any
            # trigger day's forward window reached vs the lowest any
            # window touched (signed, so the ratio is ≥ 1 and grows
            # with the widest realized within-period swing). NEVER
            # derivable from the endpoint max/min columns — one day's
            # window high is never paired with another day's endpoint.
            sw_hi = hi_p[ii, kk]
            sw_lo = lo_p[ii, kk]
            mlr_vals = _round_none(np.divide(
                1 + sw_hi, 1 + sw_lo,
                out=np.full(R, np.nan),
                where=pos & (sw_lo > -1),
            ))
        else:
            max_vals = [None] * R
            min_vals = [None] * R
            mlr_vals = [None] * R

        rev_vals = _round_none(np.divide(
            rev[ii, kk], cn, out=np.full(R, np.nan), where=pos))
        occ_vals = cn.tolist()
        # The row's ragged trigger-date list (NULL when the group has no
        # valid day at this horizon — occurrence_count 0).
        td_vals: list[list[date] | None] = [
            None if td is None else td.get(i * P + k)
            for k, i in zip(kk_l, ii_l)
        ]
        # The rows' parallel STREAK SPAN lists (NULL when the engine
        # passed no run lengths — state families): streak_starts[r] /
        # streak_ends[r] are element-wise parallel to trigger_dates[r].
        ss_vals: list[list[date] | None] = [
            None if ss is None else ss.get(i * P + k)
            for k, i in zip(kk_l, ii_l)
        ]
        se_vals: list[list[date] | None] = [
            None if se is None else se.get(i * P + k)
            for k, i in zip(kk_l, ii_l)
        ]
        # The rows' parallel STREAK DAY lists (same NULL semantics):
        # streak_days[r] holds each merged signal's trading-day count.
        sd_vals: list[list[int] | None] = [
            None if sd is None else sd.get(i * P + k)
            for k, i in zip(kk_l, ii_l)
        ]
        # The rows' parallel TRIGGER EXCESS lists (NULL when the engine
        # passed no per-cell values — the state families): each element
        # is the trigger day's value − the bucket's qualifying bar
        # (signed), rounded to the NUMERIC(10,6) scale; non-finite
        # members map to None (asyncpg cannot encode NaN here).
        te_vals: list[list[float | None] | None] = []
        for k, i in zip(kk_l, ii_l):
            t = None if te is None else te.get(i * P + k)
            te_vals.append(None if t is None else [round6(x) for x in t])
        # The row's reversal bar (per code, horizon — constant across a
        # window's configs).
        rt_vals = _round_none(thr_n[n][ii])

        horizon_payloads[n] = {
            "period": period,
            "ave": ave,
            "std": std,
            "max": max_vals,
            "min": min_vals,
            "mlr": mlr_vals,
            "rev": rev_vals,
            "occ": occ_vals,
            "td": td_vals,
            "ss": ss_vals,
            "se": se_vals,
            "sd": sd_vals,
            "te": te_vals,
            "rt": rt_vals,
        }

    # ---- The weight-blended 'mixed' row (the SQL 01 backfill's blend,
    # over the SAME 6dp-rounded leg values the horizon rows carry) —
    # the row the analysis_signals confirmation gate reads.
    w = np.array([MIXED_HORIZON_WEIGHTS[n] for n in FORWARD_HORIZONS])
    w_col = w[:, None]                                    # (4, 1)
    ave_M = np.array(
        [horizon_payloads[n]["ave"] for n in FORWARD_HORIZONS],
        dtype=np.float64)                                 # (4, R) NaN=leg NULL
    std_M = np.array(
        [horizon_payloads[n]["std"] for n in FORWARD_HORIZONS],
        dtype=np.float64)
    rev_M = np.array(
        [horizon_payloads[n]["rev"] for n in FORWARD_HORIZONS],
        dtype=np.float64)
    mlr_M = np.array(
        [horizon_payloads[n]["mlr"] for n in FORWARD_HORIZONS],
        dtype=np.float64)
    thr_M = np.array(
        [horizon_payloads[n]["rt"] for n in FORWARD_HORIZONS],
        dtype=np.float64)
    occ_M = np.array(
        [horizon_payloads[n]["occ"] for n in FORWARD_HORIZONS],
        dtype=np.int64)

    has_stat = np.isfinite(ave_M)                         # (4, R)
    w_stat = (w_col * has_stat).sum(axis=0)               # (R,)
    safe_stat = np.where(w_stat > 0, w_stat, np.nan)
    ave_mix = (w_col * np.where(has_stat, ave_M, 0.0)).sum(axis=0) / safe_stat
    # Mixture dispersion sqrt(Σw·E[x²] / Σw − mean²) over the SAME
    # valid legs (E[x²] = std² + ave²) — floored at 0 (rounding guard);
    # NULL when NO leg has stats (safe_stat NaN → NaN → None).
    ex2_mix = (w_col * np.where(
        has_stat, std_M ** 2 + ave_M ** 2, 0.0)).sum(axis=0) / safe_stat
    std_mix = np.sqrt(np.maximum(ex2_mix - ave_mix ** 2, 0.0))
    rev_mix = (w_col * np.where(has_stat, rev_M, 0.0)).sum(axis=0) / safe_stat
    # max_low_change_ratio renormalizes over the horizons with an mlr
    # stat (the MM legs — 'next' carries none), the SQL's separate
    # w_mlr denominator.
    has_mlr = np.isfinite(mlr_M)
    w_mlr = (w_col * has_mlr).sum(axis=0)
    mlr_mix = (w_col * np.where(has_mlr, mlr_M, 0.0)).sum(axis=0) / np.where(
        w_mlr > 0, w_mlr, np.nan)
    # occurrence_count = the MIN positive leg count (the blend is only
    # as well-observed as its weakest leg), 0 when none.
    pos_occ = occ_M > 0
    occ_mix = np.where(
        pos_occ.any(axis=0),
        np.where(pos_occ, occ_M, np.iinfo(np.int64).max).min(axis=0),
        0,
    )
    # threshold = the FULL-weight mean of the four bars (the bars are
    # finite everywhere — no renormalization).
    thr_mix = (w_col * thr_M).sum(axis=0)

    mixed_payload = {
        "period": PERIOD_MIXED,
        "ave": _round_none(ave_mix),
        "std": _round_none(std_mix),
        "max": [None] * R,
        "min": [None] * R,
        "mlr": _round_none(mlr_mix),
        "rev": _round_none(rev_mix),
        "occ": occ_mix.tolist(),
        "td": [None] * R,
        "ss": [None] * R,
        "se": [None] * R,
        "sd": [None] * R,
        "te": [None] * R,
        "rt": _round_none(thr_mix),
    }

    # ...then emit bucket-major: [b0-next, b0-5d, b0-20d, b0-60d,
    # b0-mixed, b1-next, ...] so forecast_id can stride by
    # len(ALL_PERIODS). The motivation columns fan out per bucket once
    # (shared scalars across the 5 period rows — no per-field re-merge).
    base_items = list(base.items())
    out: list[dict] = []
    for r_idx in range(R):
        motivation = {k: col[r_idx] for k, col in base_items}
        for payload in (
            *(horizon_payloads[n] for n in FORWARD_HORIZONS),
            mixed_payload,
        ):
            out.append({
                **motivation,                 # identity + bucket keys + config
                "period": payload["period"],
                "ave_change": payload["ave"][r_idx],
                "std_change": payload["std"][r_idx],
                "max_change": payload["max"][r_idx],
                "min_change": payload["min"][r_idx],
                "occurrence_count": payload["occ"][r_idx],
                "trigger_dates": payload["td"][r_idx],
                "streak_starts": payload["ss"][r_idx],
                "streak_ends": payload["se"][r_idx],
                "streak_days": payload["sd"][r_idx],
                "trigger_excess": payload["te"][r_idx],
                "max_low_change_ratio": payload["mlr"][r_idx],
                "reverse_prob": payload["rev"][r_idx],
                "threshold": payload["rt"][r_idx],
            })
    return out


# ---------------------------------------------------------------------------
#  Row emission helpers
# ---------------------------------------------------------------------------

def round6(x: float) -> float | None:
    """float → rounded 6dp (NUMERIC(10,6) / NUMERIC(6,6) scale) with
    NaN/inf → None (asyncpg cannot encode NaN into these columns)."""
    x = float(x)
    return round(x, 6) if np.isfinite(x) else None


def split_forecast_rows(
    rows: list[dict],
    mov_columns: list[str],
) -> tuple[list[dict], list[dict]]:
    """Split computed bucket rows into the two write targets.

    Input: (len(ALL_PERIODS)·R,) dicts emitted by ``build_result_rows``
    — bucket-major (len(ALL_PERIODS) consecutive period rows per
    bucket), each dict carries the full motivation fields + config +
    period + consolidated result columns.

    Returns:
        mov_rows    — (R,) dicts: UNIQUE rows per bucket (the 1st of
                      each period group), filtered to ``mov_columns``
                      (mov_rsi / mov_std columns). The forecast_id was
                      already assigned by the caller (1 per bucket,
                      shared across all period rows).
        result_rows — (len(ALL_PERIODS)·R,) dicts: every input row
                      filtered to ``RESULT_COLUMNS`` (forecast_results
                      columns).
    """
    stride = len(ALL_PERIODS)
    mov_rows = [
        {k: rows[i][k] for k in mov_columns}
        for i in range(0, len(rows), stride)   # 1 per bucket
    ]
    result_rows = [
        {k: r[k] for k in RESULT_COLUMNS} for r in rows
    ]
    return mov_rows, result_rows
