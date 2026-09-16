"""px_vol signals (analysis_signals.signals) — price-speed × amount-level
state cells.

The state-cell detection of analysis_forecasts.px_vol_state at signal
granularity: a day is a signal when BOTH its legs fall in one of the
signal cells of PX_VOL_SIGNAL_CELLS (the 2026-09 reduction study's
four edge-backed (speed, vol) cells — sharp_up_heavy / slow_up_heavy
sell, sharp_dn_normal / slow_dn_shrink buy; the full 10-sided state
lattice keeps computing in the forecasts layer, and flat is not
emitted — no directional claim). The day
categories come from the analysis.mov_ave_price_vs_amt REGISTRY (the
px_vol family's date-level source of truth, scattered via
wide.build_px_vol_state_matrices into int8 ordinals):

    sharp_up (t > 2.0)  / slow_up (1.26 < t <= 2.0)   → side top
    slow_dn (-2.0 <= t < -1.29) / sharp_dn (t < -2.0) → side bottom
    heavy (z > 2.0) / normal / shrink (z < -0.92)

Differences from the mov_* engines (by design):
  - Thresholds are the code's OWN trailing moments (adaptive by std —
    see the forecasts px_vol config), NOT window percentiles, so the
    recorded signal_threshold is the CONSTANT t-bar per speed (the
    registry read superseded thresholding raw t/z — the engines audit
    against the recorded categories).
  - No cooldown (state buckets admit every qualifying day, exactly
    like their forecast buckets).
  - signal_threshold = the speed's t-bar (k_sharp for sharp_*,
    k_slow_up for slow_up, k_slow_dn for slow_dn); params JSON carries
    the full adaptive threshold set + the day's recorded t / z.
  - confidence = the driving-factor composite at the matching forecast
    bucket's argmax period (ConfirmMap keyed (stat_month, px_speed,
    side) — the gate population groups the speed's vol states); the
    factor breakdown rides in params JSON (see _base / gate.py).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    PX_VOL_K_SHARP,
    PX_VOL_K_SLOW_DN,
    PX_VOL_K_SLOW_UP,
    PX_VOL_LB_WINDOW,
    PX_VOL_SIGMA_FLOOR,
    PX_VOL_SIGMA_WINDOW,
    PX_VOL_SPEED_ORD,
    PX_VOL_SPEED_SIDE,
    PX_VOL_VOL_ORD,
    PX_VOL_Z_HEAVY,
    PX_VOL_Z_SHRINK,
)
from analyze.analysis_forecasts.wide import MonthWindow, round6
from analyze.analysis_signals.config import (
    PX_VOL_SIDE_ACTION,
    PX_VOL_SIGNAL_CELLS,
    SIGNAL_TYPE_PX_VOL,
    sub_type_px_vol,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    confirm_dicts,
    confirm_row_fields,
    _in_month_rows,
    _ord_to_date,
)


def compute_px_vol_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — px_vol family.

    Args:
        mats: wide state matrices from the price_vs_amt registry
              (wide.build_px_vol_state_matrices) keyed "speed"/"vol"
              (int8 category ordinals, -1 = none) and "t"/"z" (the
              recorded px_t / px_z — NaN where no state).
        confirm: (stat_month, px_speed, side) → the confirmed-code
              calibration entry (codes, confidences, tier_pts,
              baselines, ranks, periods, factors) from
              gate.fetch_confirm on analysis_forecasts.px_vol_state.
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

        S = mats["speed"][lo:hi]
        V = mats["vol"][lo:hi]
        T = mats["t"][lo:hi]
        Z = mats["z"][lo:hi]
        rows: list[dict] = []
        for speed, vol_state in PX_VOL_SIGNAL_CELLS:
            side = PX_VOL_SPEED_SIDE[speed]
            # Adaptive confirmation gate (per code — the matching
            # px_vol_state bucket's calibrated gate; the gate groups a
            # speed's vol states, so the key carries the speed only).
            conf = confirm.get((mw.stat_month, speed, side))
            if conf is None or conf[0].size == 0:
                continue
            conf_info = confirm_dicts(conf)
            conf_mask = np.isin(
                codes_arr, np.asarray(conf[0], dtype=codes_arr.dtype),
            )

            if speed == "sharp_up":
                bar = PX_VOL_K_SHARP
            elif speed == "slow_up":
                bar = PX_VOL_K_SLOW_UP
            elif speed == "slow_dn":
                bar = PX_VOL_K_SLOW_DN
            else:  # sharp_dn
                bar = PX_VOL_K_SHARP
            # Registry-equality cell masks (the ordinals are >= 0, so
            # the -1 "no valid state" sentinel never matches). The
            # recorded t-bar stays the signal_threshold.
            cells = (
                (S == PX_VOL_SPEED_ORD[speed])
                & (V == PX_VOL_VOL_ORD[vol_state])
                & in_month[:, None] & live[None, :]
                & conf_mask[None, :]
            )
            ts, cs = np.nonzero(cells)
            if ts.size == 0:
                continue

            op = ">" if speed.endswith("up") else "<"
            end = mw.stat_month.isoformat()
            for t, i in zip(ts.tolist(), cs.tolist()):
                row_code = codes[i]
                tv = float(T[t, i])
                zv = float(Z[t, i])
                info = conf_info.get(row_code)
                fields = confirm_row_fields(info)
                rows.append({
                    "code": row_code,
                    "sec_type": sec_type,
                    "signal_type": SIGNAL_TYPE_PX_VOL,
                    "signal_sub_type": sub_type_px_vol(speed, vol_state),
                    "date": _ord_to_date(int(g[t])),
                    "action": PX_VOL_SIDE_ACTION[side],
                    "signal_threshold": round6(bar),
                    "confidence": fields["confidence"],
                    "tier": fields["tier"],
                    "code_baseline": fields["code_baseline"],
                    "code_rank": fields["code_rank"],
                    "reason": (
                        f"px_vol {speed}: t={tv:.2f} {op} {bar:g} "
                        f"(σ-scaled ret_1d), AmtLevel z={zv:.2f} "
                        f"[bars z>{PX_VOL_Z_HEAVY:g} heavy / "
                        f"z<{PX_VOL_Z_SHRINK:g} shrink], window "
                        f"ending {end}"
                    ),
                    "params": json.dumps({
                        "px_speed": speed,
                        "vol_state": vol_state,
                        "side": side,
                        "t": round6(tv),
                        "z_amt_level": round6(zv),
                        "sigma_window": PX_VOL_SIGMA_WINDOW,
                        "lb_window": PX_VOL_LB_WINDOW,
                        "k_slow_up": PX_VOL_K_SLOW_UP,
                        "k_slow_dn": PX_VOL_K_SLOW_DN,
                        "k_sharp": PX_VOL_K_SHARP,
                        "z_heavy": PX_VOL_Z_HEAVY,
                        "z_shrink": PX_VOL_Z_SHRINK,
                        "sigma_floor": PX_VOL_SIGMA_FLOOR,
                        "conf_period":
                            info["conf_period"] if info else None,
                        "confidence_factors":
                            info["conf_factors"] if info else None,
                    }),
                })
        if rows:
            yield mw.stat_month, rows
