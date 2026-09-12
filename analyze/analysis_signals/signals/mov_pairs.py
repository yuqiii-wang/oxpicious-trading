"""mov_pairs / mov_pairs_ema signals (analysis_signals.signals) — MA /
EMA-pair cross (golden / death cross) days.

The cross-event detection of analysis_forecasts.mov_pairs /
mov_pairs_ema at signal granularity: a day is a signal when the stored
relative spread CHANGES SIGN — side top a CROSS UP / golden cross
(spread[t] > 0 and spread[t-1] <= 0, ma5 rises through the slow MA →
action=sell, the extreme-day convention), side bottom a CROSS DOWN /
death cross (spread[t] < 0 and spread[t-1] >= 0 → action=buy). The
detection reuses the FORECAST ENGINE'S OWN matrix machinery verbatim —
analysis_forecasts.compute_pairs.build_pairs_matrices scatters the
fetched spread columns (fetch_analysis_inputs' pair_{W} / ema_pair_{W}
— the parent mov_ave_spread analysis's own spread definitions, no new
MA computation) plus the 1-row-shifted previous-spread matrices the
sign flip needs — so the signal days are the buckets' trigger days 1:1.

Event buckets like mov_rsi / mov_std / mov_gap: the SAME cooldown_days
suppression as the forecast buckets (PK member there, params JSON
here) — after an accepted cross the next COOLDOWN_DAYS grid trading
days cannot re-join — and the full-window history gate.

signal_threshold = PAIRS_CROSS_THRESHOLD (0.0 — the spread crosses the
ZERO line; the recorded value documents the crossed level for the
consumers that read it as a threshold). The live day-close mirror
records the day's spread against 0, so signal_excess reads as the
spread's signed distance from the zero line on the cross day.

confidence = the driving-factor composite at the matching forecast
bucket's argmax period (ConfirmMap keyed (stat_month, "{prefix}_{W}",
side)); the factor breakdown rides in params JSON (see _base /
gate.py).

The engine is source-agnostic: ``prefix`` "pair" (the MA family's
ma5_vs_ma{W}) or "ema_pair" (the EMA family's ema6_vs_ema{W}) selects
the matrices, the emitted signal_type and the sub_type naming — one
code path serves both families, the compute_pairs precedent.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.wide import MonthWindow, apply_cooldown, round6
from analyze.analysis_signals.config import (
    COOLDOWN_DAYS,
    PAIRS_CROSS_THRESHOLD,
    SIDE_ACTION,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    confirm_dicts,
    confirm_row_fields,
    _in_month_rows,
    _ord_to_date,
)

# prefix → the source spread column name (for the reason string).
_SPREAD_COL = {"pair": "ma5_vs_ma{w}", "ema_pair": "ema6_vs_ema{w}"}

# prefix → cross side labels for the reason string.
_CROSS_LABEL = {"top": "UP (golden)", "bottom": "DOWN (death)"}


def compute_pairs_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
    *,
    pair_windows: tuple,
    prefix: str,
    signal_type: str,
    sub_type,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — the MA / EMA
    cross families (one code path, ``prefix`` selects the source).

    Args:
      mats: wide spread matrices from build_pairs_matrices — keys
            f"{prefix}_{w}" (the stored spread, NaN where missing) and
            f"{prefix}_{w}_prev" (the PREVIOUS grid row's spread, NaN on
            grid row 0); the shift is on the union trading-day grid
            (build_grid), the forecast engine's convention.
      windows: resolved MonthWindow list for the target months.
      codes: sorted code list (matrix column order).
      sec_type: emitted into every row.
      first_ord: (C,) per-code first data date as ABSOLUTE epoch-day
            ordinals — full-window history gate.
      grid_ord: (T,) int64 day ordinals of the FULL grid (build_grid).
      confirm: (stat_month, f"{prefix}_{w}", side) → the confirmed-code
            calibration entry from gate.fetch_confirm on
            analysis_forecasts.mov_pairs / mov_pairs_ema.
      pair_windows: the slow-leg windows (MOV_PAIRS_WINDOWS /
            MOV_PAIRS_EMA_WINDOWS from the forecasts config).
      prefix: "pair" (MA family) | "ema_pair" (EMA family).
      signal_type: "mov_pairs" | "mov_pairs_ema".
      sub_type: window → sub_type string (sub_type_pair /
            sub_type_ema_pair).
    """
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

        rows: list[dict] = []
        for w in pair_windows:
            S = mats[f"{prefix}_{w}"][lo:hi]
            Sp = mats[f"{prefix}_{w}_prev"][lo:hi]
            # Sign-flip mask (NaN comparisons are False → warming-up /
            # missing rows never trigger), side-major (T, C, 2) stack —
            # the forecast engine's exact detection.
            with np.errstate(invalid="ignore"):
                mask3 = np.stack(
                    [(S > 0) & (Sp <= 0), (S < 0) & (Sp >= 0)], axis=2
                )
            # Cooldown over the whole window (identical to the forecast
            # buckets), then restrict to the snapshot month + live +
            # gate-confirmed codes.
            T = mask3.shape[0]
            mask3 = apply_cooldown(
                mask3.reshape(T, -1), COOLDOWN_DAYS
            ).reshape(mask3.shape)

            for si, side in enumerate(("top", "bottom")):
                conf = confirm.get((mw.stat_month, f"{prefix}_{w}", side))
                if conf is None or conf[0].size == 0:
                    continue
                conf_info = confirm_dicts(conf)
                conf_mask = np.isin(
                    codes_arr, np.asarray(conf[0], dtype=codes_arr.dtype),
                )
                cells = (
                    mask3[:, :, si] & in_month[:, None] & live[None, :]
                    & conf_mask[None, :]
                )
                ts, cs = np.nonzero(cells)
                if ts.size == 0:
                    continue

                sub = sub_type(w)
                col = _SPREAD_COL[prefix].format(w=w)
                end = mw.stat_month.isoformat()
                for t, i in zip(ts.tolist(), cs.tolist()):
                    row_code = codes[i]
                    v = float(S[t, i])
                    pv = float(Sp[t, i])
                    info = conf_info.get(row_code)
                    fields = confirm_row_fields(info)
                    rows.append({
                        "code": row_code,
                        "sec_type": sec_type,
                        "signal_type": signal_type,
                        "signal_sub_type": sub,
                        "date": _ord_to_date(int(g[t])),
                        "action": SIDE_ACTION[side],
                        "signal_threshold": round6(PAIRS_CROSS_THRESHOLD),
                        "confidence": fields["confidence"],
                        "tier": fields["tier"],
                        "code_baseline": fields["code_baseline"],
                        "code_rank": fields["code_rank"],
                        "reason": (
                            f"{sub} cross {_CROSS_LABEL[side]}: {col} "
                            f"spread {v:+.4f} turned {'>' if side == 'top' else '<'} 0 "
                            f"from {pv:+.4f}, window ending {end}"
                        ),
                        "params": json.dumps({
                            "pair_window": w,
                            "side": side,
                            "cooldown_days": COOLDOWN_DAYS,
                            "spread": round6(v),
                            "prev_spread": round6(pv),
                            "conf_period":
                                info["conf_period"] if info else None,
                            "confidence_factors":
                                info["conf_factors"] if info else None,
                        }),
                    })
        if rows:
            yield mw.stat_month, rows
