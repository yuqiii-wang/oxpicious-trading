"""Fetch last actual portfolio state and delete existing child seqs.

- fetch_last_actual_state: read the portfolio state at the end of the actual
  backtest (pre-liquidation if the backtest appended a FINAL LIQUIDATION SELL)
- delete_existing_child_seqs: CASCADE-delete all child seqs for a parent seq
"""
from __future__ import annotations

from typing import Any, Dict, Optional

FINAL_LIQ_PREFIX = "FINAL LIQUIDATION"


async def fetch_last_actual_state(
    conn, parent_seq_id: int,
) -> Optional[Dict[str, Any]]:
    """Fetch the portfolio state at the end of the actual backtest.

    The backtest engine may append a FINAL LIQUIDATION SELL on the last
    day (total_qty_after = 0). The forecast sells REPLACE this liquidation,
    so in that case we return the state BEFORE the final liquidation:
      - total_qty = total_qty_before of the final liquidation
      - cash = cash_before of the final liquidation
      - realized_pnl_cum = cumulative EXCLUDING the final liquidation
    Otherwise (position held to the end), the state is read from the last
    strategy_daily row at the run's LAST DATA date (end_date) — the
    position is unchanged since the last decision, but MTM/holding-period
    fields reflect the latest data.

    end_date = the run's last DATA date (forecast starts the next business
    day after it), NOT the last decision's exec_date.

    Returns None if the seq has no decisions.
    """
    last_dec = await conn.fetchrow(
        "SELECT side, total_qty_before, total_qty_after, cash_before, cash_after, "
        "       normalized_mean_buy_price, realized_pnl, exec_date, signal_reason "
        "FROM strategy.trade_decision "
        "WHERE seq_id = $1 AND signal_reason NOT LIKE 'FORECAST SELL%' "
        "ORDER BY decision_no DESC LIMIT 1",
        parent_seq_id,
    )
    if last_dec is None:
        return None

    is_final_liq = (
        last_dec["signal_reason"] is not None
        and last_dec["signal_reason"].startswith(FINAL_LIQ_PREFIX)
        and last_dec["side"] == "SELL"
    )

    if is_final_liq:
        total_qty = float(last_dec["total_qty_before"] or 0.0)
        cash = float(last_dec["cash_before"] or 0.0)
        cost_basis_norm = float(last_dec["normalized_mean_buy_price"] or 0.0)
        last_daily = await conn.fetchrow(
            "SELECT total_qty, cash, cost_basis_norm, realized_pnl_cum, "
            "       normalized_mean_buy_period, trade_date "
            "FROM strategy.strategy_daily "
            "WHERE seq_id = $1 AND trade_date < $2 "
            "ORDER BY trade_date DESC LIMIT 1",
            parent_seq_id, last_dec["exec_date"],
        )
        realized_pnl_cum = float(last_daily["realized_pnl_cum"]) if last_daily else 0.0
        mean_buy_period = float(last_daily["normalized_mean_buy_period"]) if last_daily else 0.0
        n_actual_decisions = await conn.fetchval(
            "SELECT count(*) FROM strategy.trade_decision "
            "WHERE seq_id = $1 AND signal_reason NOT LIKE 'FINAL LIQUIDATION%'",
            parent_seq_id,
        )

    info = await conn.fetchrow(
        "SELECT r.sec_type, r.code, r.start_date, "
        "       i.end_date, "
        "       r.first_buy_date, r.first_buy_fill_price, r.total_buy_cost "
        "FROM strategy.strategy_results r "
        "JOIN strategy.strategy_identity i ON i.seq_id = r.seq_id "
        "WHERE r.seq_id = $1",
        parent_seq_id,
    )
    if info is None:
        return None

    # Anchor at the run's LAST DATA date (strategy_identity.end_date = the
    # last OHLC day the backtest consumed, e.g. yesterday), NOT the last
    # decision's exec_date — the position is typically held after the last
    # trade, and the child seq must carry the actual daily rows through the
    # latest data (matching the non-forecast view). Falls back to the last
    # decision date when end_date is NULL.
    anchor_date = info["end_date"] or last_dec["exec_date"]

    if not is_final_liq:
        # Position held at the last DATA date (no trades between the last
        # decision and end_date — total_qty/cash/cost_basis are unchanged,
        # but realized_pnl_cum/mean_buy_period come from the latest row).
        last_daily = await conn.fetchrow(
            "SELECT total_qty, cash, cost_basis_norm, realized_pnl_cum, "
            "       normalized_mean_buy_period, trade_date "
            "FROM strategy.strategy_daily "
            "WHERE seq_id = $1 AND trade_date <= $2 "
            "ORDER BY trade_date DESC LIMIT 1",
            parent_seq_id, anchor_date,
        )
        total_qty = float(last_daily["total_qty"]) if last_daily else float(last_dec["total_qty_after"])
        cash = float(last_daily["cash"]) if last_daily else float(last_dec["cash_after"])
        cost_basis_norm = float(last_daily["cost_basis_norm"]) if last_daily else float(last_dec["normalized_mean_buy_price"])
        realized_pnl_cum = float(last_daily["realized_pnl_cum"]) if last_daily else 0.0
        mean_buy_period = float(last_daily["normalized_mean_buy_period"]) if last_daily else 0.0
        n_actual_decisions = await conn.fetchval(
            "SELECT count(*) FROM strategy.trade_decision WHERE seq_id = $1",
            parent_seq_id,
        )

    return {
        "seq_id": parent_seq_id,
        "sec_type": info["sec_type"],
        "code": info["code"],
        "total_qty": total_qty,
        "cash": cash,
        "cost_basis_norm": cost_basis_norm,
        "realized_pnl_cum": realized_pnl_cum,
        "mean_buy_period": mean_buy_period,
        "first_buy_date": info["first_buy_date"],
        "first_buy_fill_price": (
            float(info["first_buy_fill_price"])
            if info["first_buy_fill_price"] is not None else None
        ),
        "total_buy_cost": (
            float(info["total_buy_cost"])
            if info["total_buy_cost"] is not None else None
        ),
        "end_date": anchor_date,
        "start_date": info["start_date"],
        "n_actual_decisions": n_actual_decisions,
        "is_final_liquidation": is_final_liq,
    }


async def delete_existing_child_seqs(conn, parent_seq_id: int) -> int:
    """Delete existing forecast child seqs for a parent.

    CASCADE on the parent_seq_id FK handles trade_decision, strategy_daily,
    strategy_results, strategy_risks, strategy_risk_period cleanup.

    Returns the number of child seqs deleted.
    """
    n = await conn.execute(
        "DELETE FROM strategy.strategy_identity WHERE parent_seq_id = $1",
        parent_seq_id,
    )
    return int(n.split()[-1]) if n else 0
