"""px_vol signals (analysis_signals.signals) — σ-standardized price
speed × 量比 z-score state cells.

The state-cell detection of analysis_forecasts.px_vol_state at signal
granularity: a day is a signal when BOTH its σ-standardized price speed
(t = ret_1d / rolling-255 σ_ret, shifted 1 row) and its z-scored 量比
fall in one of the 10 SIDED cells (flat is not emitted — no directional
claim):

    sharp_up (t > 2.0)  / slow_up (1.26 < t <= 2.0)   → side top
    slow_dn (-2.0 <= t < -1.29) / sharp_dn (t < -2.0) → side bottom
    heavy (z > 2.0) / normal / shrink (z < -0.92)

Differences from the mov_* engines (by design):
  - Thresholds are the code's OWN trailing moments (adaptive by std —
    see the forecasts px_vol config), NOT window percentiles, so the
    day's observed t / z are compared against CONSTANT bars;
    signal_threshold records the crossed t-bar per speed.
  - No cooldown (state buckets admit every qualifying day, exactly
    like their forecast buckets).
  - signal_threshold = the speed's t-bar (k_sharp for sharp_*,
    k_slow_up for slow_up, k_slow_dn for slow_dn); params JSON carries
    the full adaptive threshold set + the day's z.
  - confidence = the matching forecast bucket's cross-period
    MAX(reverse_prob) (ConfirmMap keyed (stat_month, px_speed, side) —
    the gate population groups the speed's vol states).
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
    PX_VOL_SPEED_SIDE,
    PX_VOL_SPEEDS,
    PX_VOL_VOL_STATES,
    PX_VOL_Z_HEAVY,
    PX_VOL_Z_SHRINK,
)
from analyze.analysis_forecasts.wide import MonthWindow, round6
from analyze.analysis_signals.config import (
    PX_VOL_SIDE_ACTION,
    SIGNAL_TYPE_PX_VOL,
    TIER_NAMES,
    sub_type_px_vol,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    _cal_or_none,
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
        mats: wide state matrices keyed "t" and "z".
        confirm: (stat_month, px_speed, side) → (codes, confidences,
              tier_pts, baselines, ranks) from gate.fetch_confirm on
              analysis_forecasts.px_vol_state.
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

        T = mats["t"][lo:hi]
        Z = mats["z"][lo:hi]
        rows: list[dict] = []
        for si, speed in enumerate(PX_VOL_SPEEDS):
            if speed == "flat":
                continue
            side = PX_VOL_SPEED_SIDE[speed]
            # Adaptive confirmation gate (per code — the matching
            # px_vol_state bucket's calibrated rp threshold).
            conf = confirm.get((mw.stat_month, speed, side))
            if conf is None or conf[0].size == 0:
                continue
            conf_codes, conf_vals, tier_vals, base_vals, rank_vals = conf
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
                if speed == "sharp_up":
                    smask = T > PX_VOL_K_SHARP
                    bar = PX_VOL_K_SHARP
                elif speed == "slow_up":
                    smask = (T > PX_VOL_K_SLOW_UP) & (T <= PX_VOL_K_SHARP)
                    bar = PX_VOL_K_SLOW_UP
                elif speed == "slow_dn":
                    smask = (T >= -PX_VOL_K_SHARP) & (T < -PX_VOL_K_SLOW_DN)
                    bar = PX_VOL_K_SLOW_DN
                else:  # sharp_dn
                    smask = T < -PX_VOL_K_SHARP
                    bar = PX_VOL_K_SHARP
            cells = (
                smask & in_month[:, None] & live[None, :]
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
                rows.append({
                    "code": row_code,
                    "sec_type": sec_type,
                    "signal_type": SIGNAL_TYPE_PX_VOL,
                    "signal_sub_type": sub_type_px_vol(
                        speed, PX_VOL_VOL_STATES[
                            0 if zv > PX_VOL_Z_HEAVY
                            else (2 if zv < PX_VOL_Z_SHRINK else 1)
                        ],
                    ),
                    "date": _ord_to_date(int(g[t])),
                    "action": PX_VOL_SIDE_ACTION[side],
                    "signal_threshold": round6(bar),
                    "confidence": round6(conf_dict.get(row_code, 0.0)),
                    "tier": TIER_NAMES.get(
                        tier_dict.get(row_code, 0), "standard"),
                    "code_baseline": _cal_or_none(
                        base_dict.get(row_code, np.nan)),
                    "code_rank": _cal_or_none(
                        rank_dict.get(row_code, np.nan)),
                    "reason": (
                        f"px_vol {speed}: t={tv:.2f} {op} {bar:g} "
                        f"(σ-scaled ret_1d), 量比z={zv:.2f} "
                        f"[bars z>{PX_VOL_Z_HEAVY:g} heavy / "
                        f"z<{PX_VOL_Z_SHRINK:g} shrink], window "
                        f"ending {end}"
                    ),
                    "params": json.dumps({
                        "px_speed": speed,
                        "vol_state": PX_VOL_VOL_STATES[
                            0 if zv > PX_VOL_Z_HEAVY
                            else (2 if zv < PX_VOL_Z_SHRINK else 1)
                        ],
                        "side": side,
                        "t": round6(tv),
                        "z_liangbi": round6(zv),
                        "sigma_window": PX_VOL_SIGMA_WINDOW,
                        "lb_window": PX_VOL_LB_WINDOW,
                        "k_slow_up": PX_VOL_K_SLOW_UP,
                        "k_slow_dn": PX_VOL_K_SLOW_DN,
                        "k_sharp": PX_VOL_K_SHARP,
                        "z_heavy": PX_VOL_Z_HEAVY,
                        "z_shrink": PX_VOL_Z_SHRINK,
                        "sigma_floor": PX_VOL_SIGMA_FLOOR,
                    }),
                })
        if rows:
            yield mw.stat_month, rows
