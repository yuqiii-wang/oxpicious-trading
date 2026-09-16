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
from typing import Callable, Iterator

import numpy as np

from analyze.analysis_forecasts.wide import (
    MonthWindow,
    apply_cooldown_rolling,
    round6,
)
from analyze.analysis_signals.config import (
    COOLDOWN_DAYS,
    PAIRS_CROSS_THRESHOLD,
    PAIRS_SIGNAL_SIDES,
    PAIRS_SIGNAL_WINDOWS,
    SIDE_ACTION,
    SIGNAL_TYPE_MOV_PAIRS,
    sub_type_pair,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    _NO_ACCEPT,
    _in_month_rows,
    _ord_to_date,
    confirm_dicts,
    confirm_row_fields,
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
    pair_windows: tuple[int, ...] = PAIRS_SIGNAL_WINDOWS,
    prefix: str = "pair",
    signal_type: str = SIGNAL_TYPE_MOV_PAIRS,
    sub_type: Callable[[int], str] = sub_type_pair,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — the MA / EMA
    cross families (one rolling code path, ``prefix`` selects the
    source).

    Args:
        mats: wide spread matrices from build_pairs_matrices — keys
              f"{prefix}_{w}" (the stored spread, NaN where missing)
              and f"{prefix}_prev_{w}" (the PREVIOUS grid row's spread,
              NaN on grid row 0; the shift runs on the union
              trading-day grid, the forecast engine's convention).
        windows: resolved MonthWindow list, ASCENDING (the cooldown
              chain carries across them).
        codes: sorted code list (matrix column order).
        sec_type: emitted into every row.
        first_ord: (C,) per-code first-data epoch-day ordinals — the
              full-window history gate (first data strictly before the
              window start).
        grid_ord: (T,) int64 day ordinals of the FULL grid.
        confirm: (stat_month, f"{prefix}_{w}", side) → the confirmed-
              code calibration entry (per (window, side) — the cross
              has no k axis).
        pair_windows: the slow-leg windows (PAIRS_SIGNAL_WINDOWS).
        prefix: "pair" (MA family) | "ema_pair" (EMA family).
        signal_type: "mov_pairs" | "mov_pairs_ema".
        sub_type: window → sub_type string (sub_type_pair /
              sub_type_ema_pair).
    """
    codes_arr = np.asarray(codes)
    C = len(codes)
    # Per (matrix key, side) absolute-row cooldown chain, carried
    # across months (_NO_ACCEPT sentinel = nothing accepted yet).
    chains: dict[tuple[str, str], np.ndarray] = {}

    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue
        g = grid_ord[lo:hi]
        in_month = _in_month_rows(g, mw.stat_month)
        if not in_month.any():
            continue  # no grid day belongs to this snapshot month
        # in_month is a suffix mask (grid dates ascend) — its first
        # True row is the month's roll-in slice start. The prev matrix
        # is on the FULL grid, so the roll-in row's predecessor (last
        # row of the prior month) is already in Spin — the wide-grid
        # shift is respected at the month boundary too.
        m0 = int(np.argmax(in_month))
        # Full-window history gate in DATE space.
        live = first_ord < mw.lo_ord

        rows: list[dict] = []
        for w in pair_windows:
            key = f"{prefix}_{w}"
            Sin = mats[key][lo:hi][m0:]
            Spin = mats[f"{prefix}_prev_{w}"][lo:hi][m0:]
            for side in PAIRS_SIGNAL_SIDES:
                with np.errstate(invalid="ignore"):
                    # NaN spreads compare False — warming-up / missing
                    # rows (prev NaN on grid row 0) never trigger. A
                    # cross day's predecessor sits on the other side of
                    # zero, so crosses are one-day events (no streak
                    # merge) — the forecast engine's exact detection.
                    if side == "top":
                        mask_raw = (Sin > 0) & (Spin <= 0)
                    else:
                        mask_raw = (Sin < 0) & (Spin >= 0)
                chain = chains.get((key, side))
                if chain is None:
                    chain = np.full(C, _NO_ACCEPT, dtype=np.int64)
                accepted, chain = apply_cooldown_rolling(
                    mask_raw, chain, lo + m0, COOLDOWN_DAYS,
                )
                # The chain advances even for a month whose confirm
                # entry is missing — detection is gating-independent.
                chains[(key, side)] = chain

                # Forecast-confirmation gate (after cooldown).
                conf = confirm.get((mw.stat_month, key, side))
                if conf is None or conf[0].size == 0:
                    continue
                conf_info = confirm_dicts(conf)
                conf_mask = np.isin(
                    codes_arr,
                    np.asarray(conf[0], dtype=codes_arr.dtype),
                )
                cells = accepted & live[None, :] & conf_mask[None, :]
                ts, cs = np.nonzero(cells)
                if ts.size == 0:
                    continue

                sub = sub_type(w)
                col = _SPREAD_COL[prefix].format(w=w)
                end = mw.stat_month.isoformat()
                for t, i in zip(ts.tolist(), cs.tolist()):
                    row_code = codes[i]
                    v = float(Sin[t, i])
                    pv = float(Spin[t, i])
                    info = conf_info.get(row_code)
                    fields = confirm_row_fields(info)
                    rows.append({
                        "code": row_code,
                        "sec_type": sec_type,
                        "signal_type": signal_type,
                        "signal_sub_type": sub,
                        "date": _ord_to_date(int(g[m0 + t])),
                        "action": SIDE_ACTION[side],
                        # The crossed level: the ZERO line.
                        "signal_threshold": round6(PAIRS_CROSS_THRESHOLD),
                        "confidence": fields["confidence"],
                        "tier": fields["tier"],
                        "code_baseline": fields["code_baseline"],
                        "code_rank": fields["code_rank"],
                        "reason": (
                            f"{sub} cross {_CROSS_LABEL[side]}: {col} "
                            f"spread {v:+.4f} turned "
                            f"{'>' if side == 'top' else '<'} 0 "
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