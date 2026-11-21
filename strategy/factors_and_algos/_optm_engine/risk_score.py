"""Official risk score inside Set-B evaluation (skill Part 9).

The repo's own risk pipeline (`strategy._risks.compute.compute_risk_seq`
— the function the RiskRunner upserts) run IN-MEMORY per code: the study
measured the official score separating naive from adaptive combs 8-12x
while Calmar alone barely separates them, so it joins Set B as a
tiebreaker (lower wins) beside the DD cap.

Pure module: consumes the objective's per-code backtest outputs + the
evaluation frames; no DB, no engine imports beyond compute_risk_seq.
"""
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from _common.df_utils import host_array

from strategy._risks.compute import compute_risk_seq


def _close_price_records(df: pd.DataFrame) -> list:
    """``[{"date", "close_price"}, ...]`` records for compute_risk_seq —
    the same shape the risk study fed it (python dates, float closes)."""
    dates = host_array(df["date"])
    closes = host_array(df["close_price"])
    out = []
    for d, c in zip(dates, closes):
        day = d.item() if hasattr(d, "item") else d
        out.append({"date": day, "close_price": float(c)})
    return out


def official_risk_score(
    per_code: Dict[str, dict],
    dfs: Dict[str, pd.DataFrame],
) -> Optional[float]:
    """Mean official ``risk_score`` across codes (lower is better).

    ``per_code`` is the objective's ``_run_backtests`` output
    (``decisions`` / ``daily_rows`` / ``capital`` per code); ``dfs`` the
    matching evaluation frames (``ctx.dfs`` or ``ctx.oos_dfs``).
    ``None`` when no code yields a risk row (no trades / no sells).
    """
    scores = []
    for code, res in per_code.items():
        decisions = res.get("decisions") or []
        if not decisions:
            continue
        df = dfs.get(code)
        if df is None or df.empty:
            continue
        row = compute_risk_seq(
            seq_id=0,
            code=code,
            decisions=decisions,
            close_prices=_close_price_records(df),
            daily_rows=res.get("daily_rows") or [],
            total_buy_cost=res.get("capital") or 0.0,
        )
        if row is not None:
            scores.append(float(row["risk_score"]))
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


__all__ = ["official_risk_score"]
