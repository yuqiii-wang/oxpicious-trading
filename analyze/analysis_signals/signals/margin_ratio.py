"""margin_ratio signals (analysis_signals.signals) — margin-buy
intensity z-score states.

The state-cell detection of analysis_forecasts.margin_ratio_state at
signal granularity: a day is a signal when its 融资买入额/成交额 ratio
(rz_buy / trading_amount) z-score vs the code's own trailing moments
(rolling 1220 rows, min 250, shifted 1 row) crosses one of the 4
SIDED z bars (mid is the neutral bulk and no_buy is an absence state
with no threshold — neither emits):

    vlow (z <= -2) / low (-2 < z <= -1)   → side bottom (buy)
    high (1 < z <= 2) / vhigh (z > 2)     → side top (sell)

The study behind the semantics (2026-09, docs/margin_ratio_study.md):
the ratio is a CROWDING (contrarian) indicator — high states carry
negative 5d/20d forward lift (monthly trend5 rank-IC -0.040) and
higher forward realized vol (vol5 IC +0.054), low states mild
positive drift at lower vol.

Differences from the mov_* engines (by design, mirroring px_vol):
  - Thresholds are the code's OWN trailing moments (adaptive z bars —
    see the forecasts margin_ratio config), NOT window percentiles;
    signal_threshold records the crossed |z|-bar per state.
  - No cooldown (state buckets admit every qualifying day, exactly
    like their forecast buckets).
  - confidence = the matching forecast bucket's cross-period
    MAX(reverse_prob) (ConfirmMap keyed (stat_month, ratio_state,
    side) — the gate population groups the states per side).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import (
    MARGIN_RATIO_HIGH_BAR,
    MARGIN_RATIO_LOW_BAR,
    MARGIN_RATIO_STATE_SIDE,
    MARGIN_RATIO_VHIGH_BAR,
    MARGIN_RATIO_VLOW_BAR,
    MARGIN_RATIO_Z_MIN_PERIODS,
    MARGIN_RATIO_Z_WINDOW,
)
from analyze.analysis_forecasts.wide import MonthWindow, round6
from analyze.analysis_signals.config import (
    MARGIN_RATIO_SIDE_ACTION,
    MARGIN_RATIO_SIGNAL_STATES,
    SIGNAL_TYPE_MARGIN_RATIO,
    TIER_NAMES,
    sub_type_margin_ratio,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    _cal_or_none,
    _in_month_rows,
    _ord_to_date,
)

# Per-state detection mask lambda + crossed bar + comparison label.
_STATE_MASKS = {
    "vlow": (
        lambda Z: Z <= MARGIN_RATIO_VLOW_BAR,
        abs(MARGIN_RATIO_VLOW_BAR), "<=",
    ),
    "low": (
        lambda Z: (Z > MARGIN_RATIO_VLOW_BAR) & (Z <= MARGIN_RATIO_LOW_BAR),
        abs(MARGIN_RATIO_LOW_BAR), "<=",
    ),
    "high": (
        lambda Z: (Z > MARGIN_RATIO_HIGH_BAR) & (Z <= MARGIN_RATIO_VHIGH_BAR),
        MARGIN_RATIO_HIGH_BAR, ">",
    ),
    "vhigh": (
        lambda Z: Z > MARGIN_RATIO_VHIGH_BAR,
        MARGIN_RATIO_VHIGH_BAR, ">",
    ),
}


def compute_margin_ratio_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — margin_ratio.

    Args:
        mats: wide state matrix keyed "z" (the day's margin-ratio
              z-score; NaN where undefined — those days never signal).
        confirm: (stat_month, ratio_state, side) → (codes,
              confidences, tier_pts, baselines, ranks) from
              gate.fetch_confirm on
              analysis_forecasts.margin_ratio_state.
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

        Z = mats["z"][lo:hi]
        rows: list[dict] = []
        for state in MARGIN_RATIO_SIGNAL_STATES:
            side = MARGIN_RATIO_STATE_SIDE[state]
            mask_fn, bar, op = _STATE_MASKS[state]
            # Adaptive confirmation gate (per code — the matching
            # margin_ratio_state bucket's calibrated rp threshold).
            conf = confirm.get((mw.stat_month, state, side))
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
                smask = mask_fn(Z)
            cells = (
                smask & in_month[:, None] & live[None, :]
                & conf_mask[None, :]
            )
            ts, cs = np.nonzero(cells)
            if ts.size == 0:
                continue

            end = mw.stat_month.isoformat()
            for t, i in zip(ts.tolist(), cs.tolist()):
                row_code = codes[i]
                zv = float(Z[t, i])
                rows.append({
                    "code": row_code,
                    "sec_type": sec_type,
                    "signal_type": SIGNAL_TYPE_MARGIN_RATIO,
                    "signal_sub_type": sub_type_margin_ratio(state),
                    "date": _ord_to_date(int(g[t])),
                    "action": MARGIN_RATIO_SIDE_ACTION[side],
                    "signal_threshold": round6(bar),
                    "confidence": round6(conf_dict.get(row_code, 0.0)),
                    "tier": TIER_NAMES.get(
                        tier_dict.get(row_code, 0), "standard"),
                    "code_baseline": _cal_or_none(
                        base_dict.get(row_code, np.nan)),
                    "code_rank": _cal_or_none(
                        rank_dict.get(row_code, np.nan)),
                    "reason": (
                        f"margin_ratio {state}: 融资买入额/成交额 z="
                        f"{zv:.2f} {op} {bar:g} (code's own rolling "
                        f"{MARGIN_RATIO_Z_WINDOW}-row moments), window "
                        f"ending {end}"
                    ),
                    "params": json.dumps({
                        "ratio_state": state,
                        "side": side,
                        "ratio_z": round6(zv),
                        "z_window": MARGIN_RATIO_Z_WINDOW,
                        "z_min_periods": MARGIN_RATIO_Z_MIN_PERIODS,
                        "vlow_bar": MARGIN_RATIO_VLOW_BAR,
                        "low_bar": MARGIN_RATIO_LOW_BAR,
                        "high_bar": MARGIN_RATIO_HIGH_BAR,
                        "vhigh_bar": MARGIN_RATIO_VHIGH_BAR,
                    }),
                })
        if rows:
            yield mw.stat_month, rows
