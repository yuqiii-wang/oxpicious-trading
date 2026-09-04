"""Signal computation engines (analysis_signals).

For each target stat month's trailing 5-year window [lo, hi) of the
(T, C) wide grid — the SAME window, thresholds, cooldown and
full-window history gate the analysis_forecasts bucket engines use —
detect the extreme days and emit signal rows:

  compute_rsi_signals — rsi_{W}days in the top pct% (RSI_PCT = 1 →
      action=sell) or bottom pct% (action=buy) of the window's
      non-NULL values (linear-interpolated percentile threshold,
      gathered from the column-sorted window matrix — the same
      `_thresholds` helper the forecast RSI engine uses).

  compute_std_signals — price beyond the 2σ Bollinger band
      ma_{W} ± k·std_{W}days (upper → sell, lower → buy).

  compute_gap_signals — gap_{W}days (the W-day fractional price return,
      from analysis.mov_ave_rsi) in the top pct% (GAP_PCT = 1 →
      action=sell, sharp W-day rally) or bottom pct% (action=buy, sharp
      W-day selloff) of the window's non-NULL values — the exact
      compute_rsi_signals machinery applied to unbounded fractional
      returns (the percentile thresholding is rank-based, so identical).

Differences from the forecast engines (by design):
  - No forward-change aggregation, no market-hype split — signals are
    pure detection rows (threshold / reason / params / action /
    confidence).
  - Only days INSIDE the snapshot month M are emitted: each date is
    owned by exactly one monthly snapshot, so the date-level PK never
    conflicts across months (the cooldown still runs over the whole
    window, so a trigger late in month M-1 suppresses early-M days —
    identical to the forecast buckets).
  - signal_threshold is the crossed threshold itself: the window
    percentile of rsi_{W}days (constant per code/month/sub_type) or
    the day's band level ma_{W} ± k·std_{W}days (varies daily).
  - confidence = MAX(reverse_prob) across all forecast_results periods
    (next / 5d / 20d / 60d) for the code's matching forecast bucket
    (read from ConfirmMap at write time). reverse_prob = P(n-day
    forward change is a REVERSAL > 1% against the bucket side).

Adaptive forecast-confirmation gate (QRp_P90): a detected day is
RECORDED only when the matching analysis_forecasts bucket (same
code/sec_type/stat_month/window/side/pct|k/cooldown config) has a
cross-period reversal confidence (MAX reverse_prob across the
forecast_results periods next/5d/20d/60d) at or above the P90 quantile
of its population (same sec_type/family/side, all buckets of all PRIOR
stat_months — M-1 calibration, no look-ahead; legacy confidence > 0
fallback below GATE_MIN_POP population buckets). __main__ builds the
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
from analyze.analysis_forecasts.config import GAP_WINDOWS, MA_WINDOWS, RSI_WINDOWS
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    apply_cooldown,
    round6,
)
from analyze.analysis_signals.config import (
    COOLDOWN_DAYS,
    GAP_PCT,
    RSI_PCT,
    SIDE_ACTION,
    STD_K,
    sub_type_gap,
    sub_type_rsi,
    sub_type_std,
)

_EPOCH = date(1970, 1, 1)

# Confirmed-code+confidence map passed by __main__: (stat_month,
# matrix_key, side) → tuple of (1-D array of confirmed codes, 1-D array
# of their confidence values) — matrix_key is the engine's matrix name
# ("rsi_{w}" / "ma_{w}" / "gap_{w}"). Confidence = MAX(reverse_prob)
# across all forecast periods (P of >1% reversal). Missing / empty
# entry means "nothing confirmed".
ConfirmMap = dict[tuple[date, str, str], tuple[np.ndarray, np.ndarray]]


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
                # clears the QRp_P90 population-quantile threshold.
                conf = confirm.get((mw.stat_month, key, side))
                if conf is None or conf[0].size == 0:
                    continue
                conf_codes, conf_vals = conf
                # Build per-code confidence array aligned to codes_arr
                conf_dict: dict[str, float] = {
                    str(c): float(v) for c, v in zip(conf_codes, conf_vals)
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


def compute_rsi_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
    rsi_windows: tuple = RSI_WINDOWS,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — RSI family.

    Args:
        mats: wide rsi matrices keyed f"rsi_{w}".
        confirm: keyed (stat_month, "rsi_{w}", side) — see
              _compute_pct_signals (the window key is the matrix key).
        rsi_windows: RSI windows to emit (default: forecasts config).
    """
    return _compute_pct_signals(
        mats, windows, codes, sec_type, first_ord, grid_ord, confirm,
        keys=[f"rsi_{w}" for w in rsi_windows],
        pct=RSI_PCT,
        signal_type="mov_rsi",
        sub_type={f"rsi_{w}": sub_type_rsi(w) for w in rsi_windows},
        param_key="rsi_window",
        fmt=".2f",
    )


def compute_gap_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
    gap_windows: tuple = GAP_WINDOWS,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — gap family.

    Identical machinery to compute_rsi_signals applied to the
    gap_{W}days N-day price-return matrices (analysis.mov_ave_rsi):
    top 1% = sharp W-day rally → sell, bottom 1% = sharp W-day selloff
    → buy.

    Args:
        mats: wide gap matrices keyed f"gap_{w}".
        confirm: keyed (stat_month, "gap_{w}", side) — see
              _compute_pct_signals.
        gap_windows: gap windows to emit (default: forecasts config).
    """
    return _compute_pct_signals(
        mats, windows, codes, sec_type, first_ord, grid_ord, confirm,
        keys=[f"gap_{w}" for w in gap_windows],
        pct=GAP_PCT,
        signal_type="mov_gap",
        sub_type={f"gap_{w}": sub_type_gap(w) for w in gap_windows},
        param_key="gap_window",
        fmt=".4f",
    )


def compute_std_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
    ma_windows: tuple = MA_WINDOWS,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — Bollinger family.

    Args:
        mats: wide matrices keyed "price", f"ma_{w}", f"std_{w}".
        confirm: (stat_month, "ma_{w}", side) → (codes, confidences)
              tuple (forecast-confirmation gate + per-code confidence;
              missing/empty = none).
        (rest as compute_rsi_signals)
    """
    C = len(codes)
    codes_arr = np.asarray(codes)
    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue
        live = first_ord < mw.lo_ord
        if not live.any():
            continue
        g = grid_ord[lo:hi]
        in_month = _in_month_rows(g, mw.stat_month)

        P = mats["price"][lo:hi]
        rows: list[dict] = []
        for w in ma_windows:
            MA = mats[f"ma_{w}"][lo:hi]
            SD = mats[f"std_{w}"][lo:hi]
            up_thr = MA + STD_K * SD
            dn_thr = MA - STD_K * SD

            for side, thr in (("upper", up_thr), ("lower", dn_thr)):
                # Forecast-confirmation gate (after cooldown — see the
                # module docstring): only codes whose matching bucket
                # has reverse_prob > 0 in any period (next/5d/20d/60d).
                conf = confirm.get((mw.stat_month, f"ma_{w}", side))
                if conf is None or conf[0].size == 0:
                    continue
                conf_codes, conf_vals = conf
                conf_dict: dict[str, float] = {
                    str(c): float(v) for c, v in zip(conf_codes, conf_vals)
                }
                conf_mask = np.isin(
                    codes_arr, np.asarray(conf_codes, dtype=codes_arr.dtype),
                )

                with np.errstate(invalid="ignore"):
                    mask_raw = (P > thr) if side == "upper" else (P < thr)
                mask = apply_cooldown(mask_raw, COOLDOWN_DAYS)
                cells = (
                    mask & in_month[:, None] & live[None, :]
                    & conf_mask[None, :]
                )
                ts, cs = np.nonzero(cells)
                if ts.size == 0:
                    continue

                op = ">" if side == "upper" else "<"
                band_name = f"ma{w}{'+' if side == 'upper' else '-'}{STD_K:g}*std{w}"
                for t, i in zip(ts.tolist(), cs.tolist()):
                    row_code = codes[i]
                    rows.append({
                        "code": row_code,
                        "sec_type": sec_type,
                        "signal_type": "mov_std",
                        "signal_sub_type": sub_type_std(w),
                        "date": _ord_to_date(int(g[t])),
                        "action": SIDE_ACTION[side],
                        "signal_threshold": round6(thr[t, i]),
                        "confidence": round6(conf_dict.get(row_code, 0.0)),
                        "reason": (
                            f"price {float(P[t, i]):.4f} {op} {side} band "
                            f"{band_name}={float(thr[t, i]):.4f} "
                            f"({STD_K:g}-sigma breach)"
                        ),
                        "params": json.dumps({
                            "ma_window": w, "k": STD_K, "side": side,
                            "cooldown_days": COOLDOWN_DAYS,
                        }),
                    })
        if rows:
            yield mw.stat_month, rows
