"""mov_std signals (analysis_signals.signals) — Bollinger-band breach
days.

compute_std_signals — price beyond the 2σ Bollinger band
ma_{W} ± k·std_{W}days (upper → sell, lower → buy). Window / cooldown /
gate machinery in _base.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import MA_WINDOWS
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    apply_cooldown,
    round6,
)
from analyze.analysis_signals.config import (
    COOLDOWN_DAYS,
    SIDE_ACTION,
    STD_K,
    TIER_NAMES,
    sub_type_std,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    _cal_or_none,
    _in_month_rows,
    _ord_to_date,
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
                # _base docstring): only codes whose matching bucket
                # clears its calibrated HYB QRp_P90 threshold, with
                # per-code tier / baseline / rank calibration.
                conf = confirm.get((mw.stat_month, f"ma_{w}", side))
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
                        "tier": TIER_NAMES.get(
                            tier_dict.get(row_code, 0), "standard"),
                        "code_baseline": _cal_or_none(
                            base_dict.get(row_code, np.nan)),
                        "code_rank": _cal_or_none(
                            rank_dict.get(row_code, np.nan)),
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
