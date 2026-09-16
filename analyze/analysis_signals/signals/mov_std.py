"""mov_std signals (analysis_signals.signals) — Bollinger-band breach
days.

compute_std_signals — price beyond the PER-WINDOW-σ Bollinger band
ma_{W} ± k·std_{W}days (lower → buy only since the 2026-09 reduction;
the upper side realized negative); W/k restricted to STD_SIGNAL_K —
(ma_window=60, k=2.0) and (ma_window=20, k=3.0), the per-metric slice
of the forecasts' grid. Window / cooldown / gate machinery in _base.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.wide import (
    MonthWindow,
    apply_cooldown_rolling,
    round6,
)
from analyze.analysis_signals.config import (
    COOLDOWN_DAYS,
    SIDE_ACTION,
    STD_SIGNAL_K,
    STD_SIGNAL_SIDES,
    sub_type_std,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    _NO_ACCEPT,
    _in_month_rows,
    _ord_to_date,
    confirm_dicts,
    confirm_row_fields,
)


def compute_std_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
    *,
    std_signal_k: dict[int, tuple[float, ...]] = STD_SIGNAL_K,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — the Bollinger
    family, scanned ROLLINGLY (the PctSignalEngine month-ascending
    structure with the band levels standing in for the percentiles).

    Args:
        mats: wide matrices keyed "price", f"ma_{w}", f"std_{w}".
        windows: resolved MonthWindow list, ASCENDING (the cooldown
              chain carries across them).
        codes: sorted code list (matrix column order).
        sec_type: emitted into every row.
        first_ord: (C,) per-code first-data epoch-day ordinals — the
              full-window history gate (first data strictly before the
              window start).
        grid_ord: (T,) int64 day ordinals of the FULL grid.
        confirm: (stat_month, "ma_{w}", side) → the confirmed-code
              calibration entry — ONE gate decision per (ma_window,
              side) covering every k tier of the window (the gate SQL
              slice is the whole (ma_window, k) grid).
        std_signal_k: per-window σ tiers (STD_SIGNAL_K; the σ rides in
              the sub_type — a deep-breach day fires every shallower
              tier of its window).
    """
    codes_arr = np.asarray(codes)
    C = len(codes)
    # Per (ma_window, k, side) absolute-row cooldown chain, carried
    # across months (_NO_ACCEPT sentinel = nothing accepted yet).
    chains: dict[tuple[int, float, str], np.ndarray] = {}

    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue
        g = grid_ord[lo:hi]
        in_month = _in_month_rows(g, mw.stat_month)
        if not in_month.any():
            continue  # no grid day belongs to this snapshot month
        # in_month is a suffix mask (grid dates ascend) — its first
        # True row is the month's roll-in slice start.
        m0 = int(np.argmax(in_month))
        # Full-window history gate in DATE space.
        live = first_ord < mw.lo_ord
        Vin = mats["price"][lo:hi][m0:]

        rows: list[dict] = []
        for w, ks in std_signal_k.items():
            MA = mats[f"ma_{w}"][lo:hi]
            SD = mats[f"std_{w}"][lo:hi]
            for side in STD_SIGNAL_SIDES:
                # The confirm map is keyed per (ma_window, side) — one
                # gate decision covers every k tier of the window.
                conf = confirm.get((mw.stat_month, f"ma_{w}", side))
                if conf is None or conf[0].size == 0:
                    conf_info: dict[str, dict] | None = None
                    conf_mask: np.ndarray | None = None
                else:
                    conf_info = confirm_dicts(conf)
                    conf_mask = np.isin(
                        codes_arr,
                        np.asarray(conf[0], dtype=codes_arr.dtype),
                    )
                for k in ks:
                    # Band edge over the FULL window slice; NaN band
                    # members compare False (errstate silences the
                    # by-design NaN-compare warning).
                    band = (
                        (MA - k * SD) if side == "lower" else (MA + k * SD)
                    )
                    thr = band[m0:]
                    with np.errstate(invalid="ignore"):
                        mask_raw = (
                            (Vin < thr) if side == "lower" else (Vin > thr)
                        )
                    chain = chains.get((w, k, side))
                    if chain is None:
                        chain = np.full(C, _NO_ACCEPT, dtype=np.int64)
                    accepted, chain = apply_cooldown_rolling(
                        mask_raw, chain, lo + m0, COOLDOWN_DAYS,
                    )
                    chains[(w, k, side)] = chain

                    # Forecast-confirmation gate (after cooldown — the
                    # chains advanced regardless: detection is
                    # gating-independent, only emission is gated).
                    if conf_info is None or conf_mask is None:
                        continue
                    cells = accepted & live[None, :] & conf_mask[None, :]
                    ts, cs = np.nonzero(cells)
                    if ts.size == 0:
                        continue

                    op = "<" if side == "lower" else ">"
                    sub = sub_type_std(w, k)
                    band_name = (
                        f"ma{w}{'-' if side == 'lower' else '+'}{k:g}*std{w}"
                    )
                    for t, i in zip(ts.tolist(), cs.tolist()):
                        row_code = codes[i]
                        info = conf_info.get(row_code)
                        fields = confirm_row_fields(info)
                        rows.append({
                            "code": row_code,
                            "sec_type": sec_type,
                            "signal_type": "mov_std",
                            "signal_sub_type": sub,
                            "date": _ord_to_date(int(g[m0 + t])),
                            "action": SIDE_ACTION[side],
                            # The breached band edge (price space).
                            "signal_threshold": round6(float(thr[t, i])),
                            "confidence": fields["confidence"],
                            "tier": fields["tier"],
                            "code_baseline": fields["code_baseline"],
                            "code_rank": fields["code_rank"],
                            "reason": (
                                f"price {float(Vin[t, i]):.4f} {op} {side} "
                                f"band {band_name}="
                                f"{float(thr[t, i]):.4f} "
                                f"({k:g}-sigma breach)"
                            ),
                            "params": json.dumps({
                                "ma_window": w, "k": k, "side": side,
                                "cooldown_days": COOLDOWN_DAYS,
                                "conf_period":
                                    info["conf_period"] if info else None,
                                "confidence_factors":
                                    info["conf_factors"] if info else None,
                            }),
                        })
        if rows:
            yield mw.stat_month, rows