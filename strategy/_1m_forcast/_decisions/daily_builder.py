"""Build strategy_daily rows for the 20 forecast days of one scenario."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from strategy._trading import formula as F


def build_scenario_forecast_daily(
    scenario_rows: List[Dict[str, Any]],
    forecast_decisions: List[Dict[str, Any]],
    state: Dict[str, Any],
    future_dates: List[datetime.date],
    first_buy_date: datetime.date,
    first_buy_fill_price: Optional[float],
) -> List[Dict[str, Any]]:
    """Build strategy_daily rows for the 20 forecast days of one scenario."""
    anchor_close = state["anchor_close"]
    cost_basis_norm = state["cost_basis_norm"]
    first_buy_fill_price_actual = state.get("first_buy_fill_price") or first_buy_fill_price

    total_qty = state["total_qty"]
    cash = state.get("cash", 0.0)
    realized_pnl_cum = state.get("realized_pnl_cum", 0.0)
    mean_buy_period = state.get("mean_buy_period", 0.0)

    decision_by_date = {d["exec_date"]: d for d in forecast_decisions}

    daily_rows: List[Dict[str, Any]] = []
    for t, row in enumerate(scenario_rows):
        trade_date = future_dates[t]
        fc_close = float(row["close_price"]) * anchor_close / 100.0

        if first_buy_fill_price_actual and first_buy_fill_price_actual > 0:
            normalized_close = F.normalized_price(fc_close, first_buy_fill_price_actual)
        else:
            normalized_close = 100.0

        decision = decision_by_date.get(trade_date)
        is_decision_day = decision is not None
        decision_no = decision.get("decision_no") if is_decision_day else None

        if is_decision_day:
            total_qty = float(decision.get("total_qty_after") or 0.0)
            cash = float(decision.get("cash_after") or 0.0)
            realized_pnl_cum += float(decision.get("realized_pnl") or 0.0)

        position_value = F.position_value(total_qty, normalized_close)
        unrealized_pnl = (total_qty / 100.0) * (normalized_close - cost_basis_norm) if total_qty > 0 else 0.0
        total_pnl = realized_pnl_cum + unrealized_pnl

        capital_deployed = (total_qty / 100.0) * cost_basis_norm if total_qty > 0 else 0.0
        mean_holding_days = (trade_date - first_buy_date).days - mean_buy_period
        return_rate = F.annualized_return(total_pnl, capital_deployed, mean_holding_days)

        daily_rows.append({
            "trade_date": trade_date,
            "close_price": round(fc_close, 6),
            "normalized_close": round(normalized_close, 6),
            "total_qty": round(total_qty, 4),
            "cost_basis_norm": round(cost_basis_norm, 6),
            "position_value": round(position_value, 4),
            "cash": round(cash, 4),
            "realized_pnl_cum": round(realized_pnl_cum, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "total_pnl": round(total_pnl, 4),
            "return_rate": round(return_rate, 6),
            "sharpe_ratio": 0.0,
            "sharpe_ratio_255d": 0.0,
            "sharpe_ratio_500d": 0.0,
            "normalized_mean_buy_period": round(mean_buy_period, 6),
            "is_decision_day": is_decision_day,
            "decision_no": decision_no,
        })

    return daily_rows
