"""Build forecast SELL trade_decision dicts from algo signals on combined OHLC.

Runs the selected algo (``algo.apply_signals``) on a COMBINED DataFrame of
actual OHLC history + the scenario's 20 forecast OHLC days — as if the
forecast is a natural continuation of the actual data. The algo's SELL
signals (signal_confidence < 0) during the forecast region drive sell
decisions. BUY signals are skipped (no buying during forecast). The last
forecast day forces a final liquidation (sell all remaining position).

Falls back to the old precomputed sell_confidence path when ``algo`` is
None (backward compat for standalone forecast runs without an algo).
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from strategy._trading import formula as F
from strategy._1m_forcast.constants import HORIZON_DAYS, FORECAST_SELL_PREFIX
from .date_utils import compute_required_columns


def build_scenario_forecast_decisions(
    scenario_rows: List[Dict[str, Any]],
    state: Dict[str, Any],
    future_dates: List[datetime.date],
    scenario_name: str,
    start_decision_no: int,
    *,
    algo=None,
    algo_params: Optional[Dict[str, Any]] = None,
    actual_ohlc: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Convert one scenario's 20-day OHLC into algo-driven SELL trade_decision
    dicts.

    ``scenario_rows`` is the list of 20 forecast_1m rows for this scenario,
    ordered by forecast_day (1..20). ``state`` carries the run context.
    ``actual_ohlc`` is the trailing actual OHLC (255d, for indicator warmup).
    """
    anchor_close = state["anchor_close"]
    first_buy_fill_price = state.get("first_buy_fill_price")
    cost_basis_norm = state["cost_basis_norm"]

    total_qty = state["total_qty"]
    cash = state.get("cash", 0.0)
    realized_pnl_cum = state.get("realized_pnl_cum", 0.0)

    sell_confs: List[float] = []
    algo_ran = False
    if algo is not None and actual_ohlc and len(actual_ohlc) >= 2:
        try:
            rows_combined: List[Dict[str, Any]] = []
            for r in actual_ohlc:
                rows_combined.append({
                    "date": r["date"],
                    "open_price": r["open"],
                    "high_price": r["high"],
                    "low_price": r["low"],
                    "close_price": r["close"],
                })
            for t, row in enumerate(scenario_rows):
                rows_combined.append({
                    "date": future_dates[t],
                    "open_price": float(row["open_price"]) * anchor_close / 100.0,
                    "high_price": float(row["high_price"]) * anchor_close / 100.0,
                    "low_price": float(row["low_price"]) * anchor_close / 100.0,
                    "close_price": float(row["close_price"]) * anchor_close / 100.0,
                })
            combined = pd.DataFrame(rows_combined)
            combined = compute_required_columns(combined, algo.REQUIRED_COLUMNS)
            combined = algo.apply_signals(combined, algo_params or {})
            from strategy.factors_and_algos._algo.tuning import tune_signals
            combined = tune_signals(combined)
            fc_signals = combined["signal_confidence"].iloc[-HORIZON_DAYS:].tolist()
            sell_confs = [max(0.0, -float(sc)) for sc in fc_signals]
            algo_ran = True
        except Exception as e:
            print(f"       (algo '{getattr(algo, 'ALGO_NAME', '?')}' could not "
                  f"run on forecast OHLC: {e}; using precomputed sell schedule)",
                  flush=True)
            sell_confs = []
    if not algo_ran:
        sell_confs = [float(row["sell_confidence"]) for row in scenario_rows]

    decisions: List[Dict[str, Any]] = []
    decision_no = start_decision_no
    for t, row in enumerate(scenario_rows):
        if total_qty <= 0:
            break

        is_last_day = (t == HORIZON_DAYS - 1)
        confidence = sell_confs[t] if not is_last_day else 100.0

        fc_high = float(row["high_price"]) * anchor_close / 100.0
        fc_low = float(row["low_price"]) * anchor_close / 100.0
        fc_close = float(row["close_price"]) * anchor_close / 100.0

        fill_price = F.worst_case_sell_fill(fc_high, fc_low, fc_close)

        if first_buy_fill_price and first_buy_fill_price > 0:
            norm_price = F.normalized_price(fill_price, first_buy_fill_price)
        else:
            norm_price = 100.0

        if is_last_day:
            qty_sold = total_qty
        else:
            qty_sold = F.sell_qty(confidence, total_qty)
            qty_sold = min(qty_sold, total_qty)

        if qty_sold <= 0:
            continue

        position_before = F.position_value(total_qty, norm_price)
        position_after = F.position_value(total_qty - qty_sold, norm_price)
        cash_before = cash
        cash_after = cash + F.cash_delta_sell(qty_sold, norm_price)
        realized = F.realized_pnl(qty_sold, norm_price, cost_basis_norm)
        realized_pnl_cum += realized

        slippage = F.sell_slippage(fill_price, fc_close)

        exec_date = future_dates[t]
        reason_tag = "FINAL LIQUIDATION" if is_last_day else "algo SELL"
        signal_reason = (
            f"{FORECAST_SELL_PREFIX} F+{t+1}: {scenario_name} scenario, "
            f"{reason_tag}, conf={confidence:.1f}%, qty_sold={qty_sold:.2f}"
        )

        decisions.append({
            "decision_no": decision_no,
            "side": "SELL",
            "qty": round(qty_sold, 2),
            "exec_date": exec_date,
            "fill_price": round(fill_price, 6),
            "normalized_fill_price": round(norm_price, 6),
            "normalized_mean_buy_price": round(cost_basis_norm, 6),
            "position_before": round(position_before, 4),
            "position_after": round(position_after, 4),
            "cash_before": round(cash_before, 4),
            "cash_after": round(cash_after, 4),
            "total_qty_before": round(total_qty, 4),
            "total_qty_after": round(total_qty - qty_sold, 4),
            "realized_pnl": round(realized, 4),
            "slippage": round(slippage, 6),
            "fee": 0.0,
            "signal_value": None,
            "signal_reason": signal_reason,
        })

        total_qty -= qty_sold
        cash = cash_after
        decision_no += 1

    return decisions
