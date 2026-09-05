"""opp_pair signals (analysis_signals.signals) — industry opposite-pair
trend forecasts at signal granularity.

The pair detection of analysis_forecasts.opp_pair_state: by industry
pair, when ONE side's benchmark-offset MA trend is DROPPING the forecast
result is the OTHER side industry's future trend. A signal day is a
(forecast target B, date) where its paired trigger industry A's trend
crossed the 0 bar that day:

    rel_A(t) = MA_A[t]/MA_A[t-W] - MA_M[t]/MA_M[t-W] < 0

(A's W-day MA-trend return below the benchmark's — "an industry whose
trend grows while the benchmark grows more is DROPPING after the
offset"; the matrices come from compute_opp_pair.build_opp_pair_matrices,
shared verbatim with the forecast buckets).

Differences from the mov_* engines (by design):
  - The row's ``code`` is the TARGET industry B (the side the forecast
    is about), NOT the trigger — action = buy (side 'bottom' → B
    expected up); params JSON carries the trigger industry_id and the
    day's trend value.
  - No cooldown (state buckets, like px_vol) and signal_threshold is
    the constant 0 trend bar.
  - confidence = the matching forecast bucket's cross-period
    MAX(reverse_prob) (ConfirmMap keyed
    (stat_month, "pair_{W}", 'bottom') — gate.fetch_confirm on
    analysis_forecasts.opp_pair_state with code_col =
    'pair_industry_id', so the per-security calibration tracks the
    TARGET industry's prior confirmation history).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.compute_opp_pair import _pair_axis
from analyze.analysis_forecasts.config import (
    OPP_PAIR_SEC_TYPE,
    OPP_PAIR_SIDE,
)
from analyze.analysis_forecasts.wide import MonthWindow, round6
from analyze.analysis_signals.config import (
    OPP_PAIR_TREND_BAR,
    SIDE_ACTION,
    SIGNAL_TYPE_OPP_PAIR,
    TIER_NAMES,
    sub_type_opp_pair,
)
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    _cal_or_none,
    _in_month_rows,
    _ord_to_date,
)


def compute_opp_pair_signals(
    mats: dict[int, dict],
    windows: list[MonthWindow],
    industries: list[str],
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    pairs,
    confirm: ConfirmMap,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — opp_pair family.

    Args:
        mats: build_opp_pair_matrices output (trend-window keyed).
        windows: resolved MonthWindow list for the target months.
        industries: sorted industry_id list (matrix column order).
        first_ord: (C,) per-industry first composite-close date as
              ABSOLUTE epoch-day ordinals (both endpoints must be live).
        grid_ord: (T,) the grid's day ordinals.
        pairs: fetch_opp_pair_pairs output (the unordered pair set).
        confirm: (stat_month, "pair_{W}", 'bottom') → (target codes,
              confidences, tier_pts, baselines, ranks) from
              gate.fetch_confirm on analysis_forecasts.opp_pair_state.
    """
    a_idx, b_idx, a_ids, b_ids, scores, _corrs, _dates = _pair_axis(
        pairs, industries)
    if a_idx.size == 0:
        return

    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue
        live = first_ord < mw.lo_ord
        pair_live = live[a_idx] & live[b_idx]
        if not pair_live.any():
            continue
        g = grid_ord[lo:hi]
        in_month = _in_month_rows(g, mw.stat_month)

        rows: list[dict] = []
        for w in sorted(mats):
            # ConfirmMap keyed by the gate's matrix key ("pair_{W}" —
            # the same convention as rsi_{W} / ma_{W}); the emitted
            # row's sub_type stays "pair{W}".
            conf = confirm.get((mw.stat_month, f"pair_{w}", OPP_PAIR_SIDE))
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
            # Per-pair confirmation: the TARGET industry (b) is gated.
            conf_set = set(conf_dict)
            confirmed = np.fromiter(
                (b in conf_set for b in b_ids), dtype=bool, count=len(b_ids),
            )

            TR = mats[w]["trig"][lo:hi]
            with np.errstate(invalid="ignore"):
                cells = (
                    (TR[:, a_idx] < OPP_PAIR_TREND_BAR)
                    & pair_live[None, :] & confirmed[None, :]
                    & in_month[:, None]
                )
            ts, pc = np.nonzero(cells)
            if ts.size == 0:
                continue

            end = mw.stat_month.isoformat()
            sub = sub_type_opp_pair(w)
            for t, i in zip(ts.tolist(), pc.tolist()):
                a = a_ids[i]
                b = b_ids[i]
                v = float(TR[t, a_idx[i]])
                score = float(scores[i]) if np.isfinite(scores[i]) else None
                rows.append({
                    "code": b,
                    "sec_type": OPP_PAIR_SEC_TYPE,
                    "signal_type": SIGNAL_TYPE_OPP_PAIR,
                    "signal_sub_type": sub,
                    "date": _ord_to_date(int(g[t])),
                    "action": SIDE_ACTION[OPP_PAIR_SIDE],
                    "signal_threshold": round6(OPP_PAIR_TREND_BAR),
                    "confidence": round6(conf_dict.get(b, 0.0)),
                    "tier": TIER_NAMES.get(
                        tier_dict.get(b, 0), "standard"),
                    "code_baseline": _cal_or_none(
                        base_dict.get(b, np.nan)),
                    "code_rank": _cal_or_none(
                        rank_dict.get(b, np.nan)),
                    "reason": (
                        f"opp_pair {sub}: {a} ex-benchmark trend "
                        f"{v:.4f} < {OPP_PAIR_TREND_BAR:g} → {b} "
                        f"forecast up (pair score "
                        f"{'—' if score is None else f'{score:.3f}'}), "
                        f"window ending {end}"
                    ),
                    "params": json.dumps({
                        "industry_id": a,
                        "pair_industry_id": b,
                        "trend_window": w,
                        "trend_value": round6(v),
                        "trend_bar": OPP_PAIR_TREND_BAR,
                        "side": OPP_PAIR_SIDE,
                        "pair_score": round6(score)
                        if score is not None else None,
                    }),
                })
        if rows:
            yield mw.stat_month, rows
