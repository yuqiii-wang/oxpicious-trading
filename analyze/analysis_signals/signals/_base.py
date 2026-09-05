"""Shared signal-engine machinery (analysis_signals.signals).

For each target stat month's trailing 5-year window [lo, hi) of the
(T, C) wide grid — the SAME window, thresholds, cooldown and
full-window history gate the analysis_forecasts bucket engines use —
the per-family engines (mov_rsi / mov_gap / mov_std / px_vol) detect
the extreme days and emit signal rows. This module holds the pieces
every family shares:

  - ConfirmMap — the confirmed-code calibration map passed by
    __main__ (built by gate.fetch_confirm).
  - The full-window live gate, the snapshot-month row mask and the
    calibration-value helpers.
  - _compute_pct_signals — the percentile-family engine behind
    compute_rsi_signals / compute_gap_signals.

Differences from the forecast engines (by design):
  - No forward-change aggregation, no market-hype split — signals are
    pure detection rows (threshold / reason / params / action /
    confidence).
  - Only days INSIDE the snapshot month M are emitted: each date is
    owned by exactly one monthly snapshot, so the date-level PK never
    conflicts across months (the cooldown still runs over the whole
    window, so a trigger late in month M-1 suppresses early-M days —
    identical to the forecast buckets).
  - confidence = MAX(reverse_prob) across all forecast_results periods
    (next / 5d / 20d / 60d) for the code's matching forecast bucket
    (read from ConfirmMap at write time). reverse_prob = P(n-day
    forward change is a REVERSAL beyond the bucket's adaptive
    reverse_threshold (k·σ of the code's window forward changes) against
    the bucket side).

Adaptive forecast-confirmation gate (QRp_P90 + mean-reversal rule): a
detected day is RECORDED only when the matching analysis_forecasts
bucket (same code/sec_type/stat_month/window/side/pct|k/cooldown
config) has a cross-period reversal confidence (MAX reverse_prob
across the forecast_results periods next/5d/20d/60d) at or above the
P90 quantile of its population (same sec_type/family/side, all
buckets of all PRIOR stat_months — M-1 calibration, no look-ahead;
legacy confidence > 0 fallback below GATE_MIN_POP population buckets)
AND the qualifying period's code prior mean reverse_prob is positive
where known — the mean sees reverse too, not just the single
bucket-period (unknown mean — no prior bucket-periods for that
side/period — does not block). __main__ builds the
confirmed-code sets per (stat_month, window, side) via
analysis_signals.gate.fetch_confirm and passes them as `confirm`; the
engines AND them into the cell mask AFTER cooldown, so detection stays
identical to the forecast buckets and the gate only filters which days
get recorded (NULL / missing forecast = not confirmed). Confidence for
each emitted row is looked up per code from the confirm map.

Yields (stat_month, rows) so __main__ can write month-major (one
atomic transaction per month, keeping the month-granular incremental
detection crash-safe).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.compute_rsi import _thresholds
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    apply_cooldown,
    round6,
)
from analyze.analysis_signals.config import (
    COOLDOWN_DAYS,
    SIDE_ACTION,
    TIER_NAMES,
)

_EPOCH = date(1970, 1, 1)

# Confirmed-code calibration map passed by __main__: (stat_month,
# matrix_key, side) → tuple of five aligned 1-D arrays over the
# confirmed codes — (codes, confidences, tier_pts, baselines, ranks).
# matrix_key is the engine's matrix name ("rsi_{w}" / "ma_{w}" /
# "gap_{w}"). confidence = MAX(reverse_prob) across all forecast
# periods (reversal probability at the bucket's adaptive
# reverse_threshold); tier_pts 2/1/0 = proven / proven_dir /
# standard (MAX over qualifying periods, see gate.py); baseline = the
# code's prior mean rp for the confidence's argmax period; rank = the
# within-code percentile floor of the confidence. NaN = unknown (code
# history too short). Missing / empty entry means "nothing confirmed".
ConfirmMap = dict[
    tuple[date, str, str],
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
]


def _cal_or_none(v: float) -> float | None:
    """Calibration value → DB NULL when unknown (NaN); round6 otherwise."""
    v = float(v)
    return round6(v) if np.isfinite(v) else None


def _ord_to_date(o: int) -> date:
    """Epoch-day ordinal (days since 1970-01-01) → python date."""
    return date.fromordinal(o + _EPOCH.toordinal())


def _in_month_rows(grid_slice: np.ndarray, stat_month: date) -> np.ndarray:
    """Bool row mask of the window slice falling inside the snapshot
    month M (grid dates >= M's first day; the upper bound is implied —
    the window ends at the month-end)."""
    return grid_slice >= (stat_month.replace(day=1) - _EPOCH).days


def _compute_pct_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
    *,
    keys: list[str],
    pct: int,
    signal_type: str,
    sub_type: dict[str, str],
    param_key: str,
    fmt: str,
) -> Iterator[tuple[date, list[dict]]]:
    """Shared percentile-family engine behind compute_rsi_signals /
    compute_gap_signals — the top/bottom-pct% extreme-day detection over
    each stat month's trailing 5-year window.

    Args:
        mats: wide indicator matrices (one per ``keys`` entry).
        keys: matrix keys to emit (e.g. ["rsi_6", ...] / ["gap_2", "gap_3"]).
        pct: percentile width (1 = top/bottom 1%).
        signal_type: emitted signal_type ("mov_rsi" / "mov_gap").
        sub_type: matrix key → sub_type string (e.g. "rsi_6" → "rsi6").
        param_key: params JSON key for the window ("rsi_window" / "gap_window").
        fmt: format spec for the day's indicator value in ``reason``
              ("0-100 RSI" uses .2f, fractional gap returns .4f).
        (rest as compute_rsi_signals)
    """
    C = len(codes)
    codes_arr = np.asarray(codes)
    col = np.arange(C)
    pct_label = f"{pct}%"

    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue
        # Full-window gate (DATE space) — same as the forecast buckets.
        live = first_ord < mw.lo_ord
        if not live.any():
            continue
        g = grid_ord[lo:hi]
        in_month = _in_month_rows(g, mw.stat_month)

        rows: list[dict] = []
        for key in keys:
            V = mats[key][lo:hi]
            valid_n = np.count_nonzero(~np.isnan(V), axis=0).astype(np.int64)
            if not ((valid_n > 0) & live).any():
                continue
            S = np.sort(V, axis=0)  # NaN last — quantile gathers
            thr_top = _thresholds(S, valid_n, col, 1.0 - pct / 100.0)
            thr_bot = _thresholds(S, valid_n, col, pct / 100.0)

            for side, thr in (
                ("top", thr_top), ("bottom", thr_bot),
            ):
                # Adaptive confirmation gate (after cooldown — see the
                # module docstring): only codes whose matching bucket
                # clears its calibrated threshold, with per-code
                # tier / baseline / rank calibration.
                conf = confirm.get((mw.stat_month, key, side))
                if conf is None or conf[0].size == 0:
                    continue
                conf_codes, conf_vals, tier_vals, base_vals, rank_vals = conf
                # Per-code calibration arrays aligned to codes_arr
                conf_dict: dict[str, float] = {
                    str(c): float(v) for c, v in zip(conf_codes, conf_vals)
                }
                tier_dict: dict[str, int] = {
                    str(c): int(v) for c, v in zip(conf_codes, tier_vals)
                }
                base_dict: dict[str, float] = {
                    str(c): float(v) for c, v in zip(conf_codes, base_vals)
                }
                rank_dict: dict[str, float] = {
                    str(c): float(v) for c, v in zip(conf_codes, rank_vals)
                }
                conf_mask = np.isin(
                    codes_arr, np.asarray(conf_codes, dtype=codes_arr.dtype),
                )

                with np.errstate(invalid="ignore"):
                    mask_raw = (
                        (V >= thr[None, :]) if side == "top"
                        else (V <= thr[None, :])
                    )
                # Cooldown over the whole window (identical to the
                # forecast buckets), then restrict to the snapshot
                # month + live + confirmed codes.
                mask = apply_cooldown(mask_raw, COOLDOWN_DAYS)
                cells = (
                    mask & in_month[:, None] & live[None, :]
                    & conf_mask[None, :]
                )
                ts, cs = np.nonzero(cells)
                if ts.size == 0:
                    continue

                op = ">=" if side == "top" else "<="
                end = mw.stat_month.isoformat()
                w = int(key.rsplit("_", 1)[1])
                sub = sub_type[key]
                for t, i in zip(ts.tolist(), cs.tolist()):
                    v = float(V[t, i])
                    row_code = codes[i]
                    rows.append({
                        "code": row_code,
                        "sec_type": sec_type,
                        "signal_type": signal_type,
                        "signal_sub_type": sub,
                        "date": _ord_to_date(int(g[t])),
                        "action": SIDE_ACTION[side],
                        "signal_threshold": round6(thr[i]),
                        "confidence": round6(conf_dict.get(row_code, 0.0)),
                        "tier": TIER_NAMES.get(
                            tier_dict.get(row_code, 0), "standard"),
                        "code_baseline": _cal_or_none(
                            base_dict.get(row_code, np.nan)),
                        "code_rank": _cal_or_none(
                            rank_dict.get(row_code, np.nan)),
                        "reason": (
                            f"{sub}={v:{fmt}} {op} {side} {pct_label} "
                            f"threshold {float(thr[i]):.4f} of trailing "
                            f"5y window ending {end}"
                        ),
                        "params": json.dumps({
                            param_key: w, "side": side,
                            "pct": pct, "cooldown_days": COOLDOWN_DAYS,
                        }),
                    })
        if rows:
            yield mw.stat_month, rows
