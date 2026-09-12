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
  - confidence = the DRIVING-FACTOR COMPOSITE at the gate's argmax
    period (see gate.py): a weighted blend of evidence (t-stat),
    efficiency (sharpe), consistency (probability lift over the base
    rate) and the code's prior mean composite — all computed in the
    SIGNAL'S direction (dir_ave is sign-flipped for top/upper, so a
    buy row's confidence speaks about the upward reversal and a sell
    row's about the downward), all horizon-free and comparable across
    periods / families / sec_types. The argmax period (the horizon the
    confidence speaks about) and the full factor breakdown are
    recorded in the row's params JSON (conf_period /
    confidence_factors).

Forecast-confirmation gate (forecast-result rule): a detected day is
RECORDED only when the matching analysis_forecasts bucket (same
code/sec_type/stat_month/window/side/pct|k/cooldown config) qualifies —
at least ONE forecast_results period (next/5d/20d/60d) has reverse_prob
> GATE_RP_MIN (reverse P > 1% — a material reversal probability) AND a
MEAN REVERSAL (dir_ave > 0 — the bucket's mean forward change reverses,
so the signal holds, not just a fat reversal tail) AND PROBABILITY LIFT
(rp above the unconditional base_rates probability for the same side —
the conjunct falls back to TRUE without a base_rates row) AND MAGNITUDE
LIFT (dir_ave above the sign-aligned base_ave_change — the mean
reversal must beat the window's own drift; same fallback) AND a REAL
SAMPLE (occurrence_count >= GATE_MIN_OCCURRENCE) AND a SIGNIFICANT
MEAN (dir_ave · sqrt(occurrence_count) >= GATE_T_STAT_MIN ·
std_change); see gate.py.
__main__ builds the
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
# matrix_key, side) → tuple of seven aligned 1-D arrays over the
# confirmed codes — (codes, confidences, tier_pts, baselines, ranks,
# periods, factors). matrix_key is the engine's matrix name
# ("rsi_{w}" / "ma_{w}" / "gap_{w}"). confidence = the driving-factor
# composite at the gate's argmax-composite qualifying period (see
# gate.py); tier_pts 2/1/0 = proven / proven_dir / standard (MAX over
# qualifying periods); baseline = the code's prior mean composite for
# the argmax period; rank = the within-code percentile floor of the
# confidence; period = the argmax period string; factors = the
# confidence_factors JSON object text for the params column.
# NaN = unknown (code history too short). Missing / empty entry means
# "nothing confirmed".
ConfirmMap = dict[
    tuple[date, str, str],
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray,
          np.ndarray, np.ndarray],
]


def confirm_dicts(conf: tuple) -> dict[str, dict]:
    """One ConfirmMap entry → {code: row fields}, the per-code lookup
    every engine builds once per (window, side): confidence / tier_pts
    / code_baseline / code_rank / conf_period / conf_factors (the
    factor JSON parsed). NaN calibrations stay NaN — _cal_or_none maps
    them to DB NULL at write time."""
    codes, conf_vals, tier_vals, base_vals, rank_vals, periods, factors = \
        conf
    out: dict[str, dict] = {}
    for c, cv, tv, bv, rv, p, fj in zip(
        codes, conf_vals, tier_vals, base_vals, rank_vals, periods, factors,
    ):
        try:
            fac = json.loads(fj) if fj is not None else None
        except (TypeError, ValueError):
            fac = None
        out[str(c)] = {
            "confidence": float(cv),
            "tier_pts": int(tv),
            "code_baseline": float(bv),
            "code_rank": float(rv),
            "conf_period": str(p),
            "conf_factors": fac,
        }
    return out


def confirm_row_fields(info: dict | None) -> dict:
    """The per-code confirm entry → the row's calibration fields
    (defaults mirror the pre-gate behavior: confidence 0, standard
    tier, NULL calibrations — only reachable for a code missing from
    its own confirm entry, which the isin-mask makes impossible)."""
    if info is None:
        return {
            "confidence": 0.0,
            "tier": TIER_NAMES[0],
            "code_baseline": None,
            "code_rank": None,
            "conf_period": None,
            "conf_factors": None,
        }
    return {
        "confidence": round6(info["confidence"]),
        "tier": TIER_NAMES.get(info["tier_pts"], TIER_NAMES[0]),
        "code_baseline": _cal_or_none(info["code_baseline"]),
        "code_rank": _cal_or_none(info["code_rank"]),
    }


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
                # clears its calibrated gate, with per-code driving
                # -factor confidence / tier / baseline / rank.
                conf = confirm.get((mw.stat_month, key, side))
                if conf is None or conf[0].size == 0:
                    continue
                conf_info = confirm_dicts(conf)
                conf_mask = np.isin(
                    codes_arr, np.asarray(conf[0], dtype=codes_arr.dtype),
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
                    info = conf_info.get(row_code)
                    fields = confirm_row_fields(info)
                    rows.append({
                        "code": row_code,
                        "sec_type": sec_type,
                        "signal_type": signal_type,
                        "signal_sub_type": sub,
                        "date": _ord_to_date(int(g[t])),
                        "action": SIDE_ACTION[side],
                        "signal_threshold": round6(thr[i]),
                        "confidence": fields["confidence"],
                        "tier": fields["tier"],
                        "code_baseline": fields["code_baseline"],
                        "code_rank": fields["code_rank"],
                        "reason": (
                            f"{sub}={v:{fmt}} {op} {side} {pct_label} "
                            f"threshold {float(thr[i]):.4f} of trailing "
                            f"5y window ending {end}"
                        ),
                        "params": json.dumps({
                            param_key: w, "side": side,
                            "pct": pct, "cooldown_days": COOLDOWN_DAYS,
                            "conf_period":
                                info["conf_period"] if info else None,
                            "confidence_factors":
                                info["conf_factors"] if info else None,
                        }),
                    })
        if rows:
            yield mw.stat_month, rows
