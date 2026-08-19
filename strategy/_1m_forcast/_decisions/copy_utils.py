"""Copy parent's actual decisions and daily rows into a child seq.

- copy_actual_decisions: trade_decision rows, optionally excluding FINAL LIQUIDATION
- copy_actual_daily: strategy_daily rows, optionally excluding the final liquidation day
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional


async def copy_actual_decisions(
    conn, parent_seq_id: int, child_seq_id: int, *,
    exclude_final_liquidation: bool = True,
) -> int:
    """Copy trade_decision rows from parent to child seq.

    With ``exclude_final_liquidation=True`` (default), the FINAL LIQUIDATION
    SELL is skipped — the forecast sells REPLACE it in the child seq.

    Returns the number of rows copied.
    """
    extra_filter = " AND signal_reason NOT LIKE 'FORECAST SELL%'"
    if exclude_final_liquidation:
        extra_filter += " AND signal_reason NOT LIKE 'FINAL LIQUIDATION%'"
    rows = await conn.fetch(
        "SELECT decision_no, side, qty, exec_date, fill_price, "
        "       normalized_fill_price, normalized_mean_buy_price, "
        "       position_before, position_after, cash_before, cash_after, "
        "       total_qty_before, total_qty_after, realized_pnl, "
        "       slippage, fee, signal_value, signal_reason, "
        "       ft_stressed_conf_up, ft_stressed_conf_down "
        "FROM strategy.trade_decision "
        f"WHERE seq_id = $1{extra_filter} ORDER BY decision_no",
        parent_seq_id,
    )
    if not rows:
        return 0

    values = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        values.append((
            child_seq_id, d["decision_no"], d["side"], d["qty"],
            d["exec_date"], d["fill_price"], d["normalized_fill_price"],
            d["normalized_mean_buy_price"], d["position_before"],
            d["position_after"], d["cash_before"], d["cash_after"],
            d["total_qty_before"], d["total_qty_after"], d["realized_pnl"],
            d["slippage"], d["fee"], d["signal_value"], d["signal_reason"],
            d["ft_stressed_conf_up"], d["ft_stressed_conf_down"],
        ))

    await conn.executemany(
        "INSERT INTO strategy.trade_decision "
        "(seq_id, decision_no, side, qty, exec_date, fill_price, "
        " normalized_fill_price, normalized_mean_buy_price, "
        " position_before, position_after, cash_before, cash_after, "
        " total_qty_before, total_qty_after, realized_pnl, "
        " slippage, fee, signal_value, signal_reason, "
        " ft_stressed_conf_up, ft_stressed_conf_down) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)",
        values,
    )
    return len(rows)


async def copy_actual_daily(
    conn, parent_seq_id: int, child_seq_id: int, *,
    exclude_final_liquidation_day: Optional[datetime.date] = None,
    last_actual_date: Optional[datetime.date] = None,
) -> int:
    """Copy strategy_daily rows from parent to child seq.

    ``exclude_final_liquidation_day``: if provided, daily rows on this date
    and later are skipped (the forecast daily rows replace it).

    ``last_actual_date``: if provided (and ``exclude_final_liquidation_day``
    is None), daily rows AFTER this date are skipped (removes old forecast
    daily rows from the previous architecture).

    Returns the number of rows copied.
    """
    if exclude_final_liquidation_day:
        date_filter = " AND trade_date < $2"
        args = [parent_seq_id, exclude_final_liquidation_day]
    elif last_actual_date:
        date_filter = " AND trade_date <= $2"
        args = [parent_seq_id, last_actual_date]
    else:
        date_filter = ""
        args = [parent_seq_id]
    rows = await conn.fetch(
        "SELECT trade_date, close_price, normalized_close, "
        "       total_qty, cost_basis_norm, position_value, cash, "
        "       realized_pnl_cum, unrealized_pnl, total_pnl, return_rate, "
        "       sharpe_ratio, sharpe_ratio_255d, sharpe_ratio_500d, "
        "       normalized_mean_buy_period, "
        "       is_decision_day, decision_no "
        f"FROM strategy.strategy_daily "
        f"WHERE seq_id = $1{date_filter} ORDER BY trade_date",
        *args,
    )
    if not rows:
        return 0

    values = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        values.append((
            child_seq_id, d["trade_date"], d["close_price"],
            d["normalized_close"], d["total_qty"],
            d["cost_basis_norm"], d["position_value"], d["cash"],
            d["realized_pnl_cum"], d["unrealized_pnl"], d["total_pnl"],
            d["return_rate"], d["sharpe_ratio"], d["sharpe_ratio_255d"],
            d["sharpe_ratio_500d"], d["normalized_mean_buy_period"],
            d["is_decision_day"], d["decision_no"],
        ))

    await conn.executemany(
        "INSERT INTO strategy.strategy_daily "
        "(seq_id, trade_date, close_price, normalized_close, "
        " total_qty, cost_basis_norm, position_value, cash, "
        " realized_pnl_cum, unrealized_pnl, total_pnl, return_rate, "
        " sharpe_ratio, sharpe_ratio_255d, sharpe_ratio_500d, "
        " normalized_mean_buy_period, "
        " is_decision_day, decision_no) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)",
        values,
    )
    return len(rows)
