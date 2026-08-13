"""Create 8 forecast child seqs — one per scenario.

Instead of inserting the mean scenario's sell schedule into the parent seq,
this module creates 8 child seqs (one per forecast scenario). Each child seq
carries a FULL COPY of the parent's actual decisions + that scenario's 20
forecast sells. This way, each scenario has its own trade_decision +
strategy_daily + strategy_results + risks, and the UI can switch between
scenarios via a dropdown.

Child seqs are tagged with parent_seq_id + scenario on strategy_identity.
Deleting a parent CASCADEs to its children (FK ON DELETE CASCADE).
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from strategy._trading import formula as F
from strategy._1m_forcast.constants import (
    HORIZON_DAYS,
    DISPLAY_SCENARIOS,
    FORECAST_SELL_PREFIX,
)


# ---------------------------------------------------------------------------
# Future trading dates — business days (Mon-Fri) from forecast_date
# ---------------------------------------------------------------------------
def future_trading_dates(
    forecast_date: datetime.date, n_days: int,
) -> List[datetime.date]:
    """Compute n future trading dates (skipping weekends)."""
    dates: List[datetime.date] = []
    d = forecast_date
    for _ in range(n_days):
        d += datetime.timedelta(days=1)
        while d.weekday() >= 5:  # Sat=5, Sun=6
            d += datetime.timedelta(days=1)
        dates.append(d)
    return dates


# ---------------------------------------------------------------------------
# Compute algo REQUIRED_COLUMNS from a close series (for combined actual+
# forecast df). Only BB needs this; MACD computes EMAs internally.
# ---------------------------------------------------------------------------
def _compute_required_columns(
    df: pd.DataFrame, required_columns: tuple,
) -> pd.DataFrame:
    """Compute the algo's REQUIRED_COLUMNS from close_price on the combined
    actual+forecast DataFrame.

    For Bollinger Bands: computes MA20/MA60, price_vs_ma20/price_vs_ma60,
    std_20days/std_60days from close_price. The 255d actual history provides
    ample warmup so all windows are fully populated for the 20 forecast days.

    For MACD: REQUIRED_COLUMNS is empty — nothing to compute (the algo
    computes EMAs internally from close_price).
    """
    if not required_columns:
        return df
    close = df["close_price"]
    df = df.copy()
    if "price_vs_ma20" in required_columns or "std_20days" in required_columns:
        ma20 = close.rolling(20, min_periods=1).mean()
        df["price_vs_ma20"] = (close - ma20) / ma20.where(ma20 != 0, np.nan)
        df["std_20days"] = close.rolling(20, min_periods=2).std()
    if "price_vs_ma60" in required_columns or "std_60days" in required_columns:
        ma60 = close.rolling(60, min_periods=1).mean()
        df["price_vs_ma60"] = (close - ma60) / ma60.where(ma60 != 0, np.nan)
        df["std_60days"] = close.rolling(60, min_periods=2).std()
    return df


# ---------------------------------------------------------------------------
# Build forecast SELL decision dicts from algo signals on combined OHLC
# ---------------------------------------------------------------------------
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

    Instead of using a precomputed sell schedule, this runs the selected algo
    (``algo.apply_signals``) on a COMBINED DataFrame of actual OHLC history +
    the scenario's 20 forecast OHLC days — as if the forecast is a natural
    continuation of the actual data. The algo's SELL signals
    (signal_confidence < 0) during the forecast region drive sell decisions.
    BUY signals are skipped (no buying during forecast). The last forecast
    day forces a final liquidation (sell all remaining position).

    Falls back to the old precomputed sell_confidence path when ``algo`` is
    None (backward compat for standalone forecast runs without an algo).

    ``scenario_rows`` is the list of 20 forecast_1m rows for this scenario,
    ordered by forecast_day (1..20). ``state`` carries the run context.
    ``actual_ohlc`` is the trailing actual OHLC (255d, for indicator warmup).
    """
    anchor_close = state["anchor_close"]
    first_buy_fill_price = state.get("first_buy_fill_price")
    cost_basis_norm = state["cost_basis_norm"]

    # Running portfolio state (carried from the last actual decision).
    total_qty = state["total_qty"]
    cash = state.get("cash", 0.0)
    realized_pnl_cum = state.get("realized_pnl_cum", 0.0)

    # ---- Compute algo-driven sell confidences for the 20 forecast days ----
    # Build a combined df: actual OHLC (for indicator warmup) + forecast
    # scenario OHLC (converted from forecast-norm to actual price space).
    # Algos whose REQUIRED_COLUMNS can't be derived from close alone (e.g.
    # ma_spread needs RSI + turnover slopes) raise here; we fall back to the
    # forecast's own precomputed sell_confidence so the run still completes.
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
            combined = _compute_required_columns(combined, algo.REQUIRED_COLUMNS)
            combined = algo.apply_signals(combined, algo_params or {})
            # Extract signal_confidence for the forecast days (last HORIZON_DAYS).
            fc_signals = combined["signal_confidence"].iloc[-HORIZON_DAYS:].tolist()
            # Derive sell confidence: |signal_confidence| when SELL (< 0), else 0.
            sell_confs = [max(0.0, -float(sc)) for sc in fc_signals]
            algo_ran = True
        except Exception as e:
            # The algo couldn't run on forecast-only OHLC (its required
            # columns aren't derivable from close — e.g. ma_spread needs RSI
            # + turnover slopes). Fall back to the precomputed sell schedule.
            print(f"       (algo '{getattr(algo, 'ALGO_NAME', '?')}' could not "
                  f"run on forecast OHLC: {e}; using precomputed sell schedule)",
                  flush=True)
            sell_confs = []
    if not algo_ran:
        # Fallback: old precomputed sell_confidence from forecast_1m rows.
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
            # Final liquidation: sell ALL remaining position.
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


# ---------------------------------------------------------------------------
# Build strategy_daily rows for the 20 forecast days
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Fetch the last actual portfolio state (for continuity)
# ---------------------------------------------------------------------------
FINAL_LIQ_PREFIX = "FINAL LIQUIDATION"


async def fetch_last_actual_state(
    conn, parent_seq_id: int,
) -> Optional[Dict[str, Any]]:
    """Fetch the portfolio state at the end of the actual backtest.

    The backtest engine always appends a FINAL LIQUIDATION SELL on the last
    day (total_qty_after = 0). The forecast sells REPLACE this liquidation,
    so we return the state BEFORE the final liquidation:
      - total_qty = total_qty_before of the final liquidation
      - cash = cash_before of the final liquidation
      - realized_pnl_cum = cumulative EXCLUDING the final liquidation
      - end_date = the final liquidation day (forecast starts next business day)

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

    # Detect the FINAL LIQUIDATION SELL (the backtest always appends one on
    # the last day when there's an open position). The forecast sells replace
    # it, so we need the PRE-liquidation state.
    is_final_liq = (
        last_dec["signal_reason"] is not None
        and last_dec["signal_reason"].startswith(FINAL_LIQ_PREFIX)
        and last_dec["side"] == "SELL"
    )

    if is_final_liq:
        # Use total_qty_before / cash_before (pre-liquidation state).
        total_qty = float(last_dec["total_qty_before"] or 0.0)
        cash = float(last_dec["cash_before"] or 0.0)
        cost_basis_norm = float(last_dec["normalized_mean_buy_price"] or 0.0)
        # realized_pnl_cum EXCLUDING the final liquidation: fetch the daily
        # row on the day BEFORE the final liquidation day.
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
        # Count actual decisions EXCLUDING the final liquidation.
        n_actual_decisions = await conn.fetchval(
            "SELECT count(*) FROM strategy.trade_decision "
            "WHERE seq_id = $1 AND signal_reason NOT LIKE 'FINAL LIQUIDATION%'",
            parent_seq_id,
        )
    else:
        # No final liquidation — use the post-decision state directly.
        last_daily = await conn.fetchrow(
            "SELECT total_qty, cash, cost_basis_norm, realized_pnl_cum, "
            "       normalized_mean_buy_period, trade_date "
            "FROM strategy.strategy_daily "
            "WHERE seq_id = $1 AND trade_date <= $2 "
            "ORDER BY trade_date DESC LIMIT 1",
            parent_seq_id, last_dec["exec_date"],
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

    info = await conn.fetchrow(
        "SELECT sec_type, code, start_date, end_date, "
        "       first_buy_date, first_buy_fill_price, total_buy_cost "
        "FROM strategy.strategy_results WHERE seq_id = $1",
        parent_seq_id,
    )
    if info is None:
        return None

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
        "end_date": last_dec["exec_date"],  # forecast_date = last actual decision date
        "start_date": info["start_date"],
        "n_actual_decisions": n_actual_decisions,
        "is_final_liquidation": is_final_liq,
    }


# ---------------------------------------------------------------------------
# Delete existing child seqs (for --force re-runs)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Copy parent's actual decisions into a child seq (excluding FINAL LIQUIDATION)
# ---------------------------------------------------------------------------
async def copy_actual_decisions(
    conn, parent_seq_id: int, child_seq_id: int, *,
    exclude_final_liquidation: bool = True,
) -> int:
    """Copy trade_decision rows from parent to child seq.

    With ``exclude_final_liquidation=True`` (default), the FINAL LIQUIDATION
    SELL is skipped — the forecast sells REPLACE it in the child seq.

    Returns the number of rows copied.
    """
    # Exclude FINAL LIQUIDATION (forecast sells replace it) AND any old
    # FORECAST SELL decisions (from the previous architecture that inserted
    # them into the parent seq — now they live in child seqs only).
    extra_filter = " AND signal_reason NOT LIKE 'FORECAST SELL%'"
    if exclude_final_liquidation:
        extra_filter += " AND signal_reason NOT LIKE 'FINAL LIQUIDATION%'"
    rows = await conn.fetch(
        "SELECT decision_no, side, qty, exec_date, fill_price, "
        "       normalized_fill_price, normalized_mean_buy_price, "
        "       position_before, position_after, cash_before, cash_after, "
        "       total_qty_before, total_qty_after, realized_pnl, "
        "       slippage, fee, signal_value, signal_reason "
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
        ))

    await conn.executemany(
        "INSERT INTO strategy.trade_decision "
        "(seq_id, decision_no, side, qty, exec_date, fill_price, "
        " normalized_fill_price, normalized_mean_buy_price, "
        " position_before, position_after, cash_before, cash_after, "
        " total_qty_before, total_qty_after, realized_pnl, "
        " slippage, fee, signal_value, signal_reason) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)",
        values,
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Copy parent's actual strategy_daily into a child seq
# (excluding the FINAL LIQUIDATION day — forecast replaces it)
# ---------------------------------------------------------------------------
async def copy_actual_daily(
    conn, parent_seq_id: int, child_seq_id: int, *,
    exclude_final_liquidation_day: Optional[datetime.date] = None,
    last_actual_date: Optional[datetime.date] = None,
) -> int:
    """Copy strategy_daily rows from parent to child seq.

    ``exclude_final_liquidation_day``: if provided, daily rows on this date
    and later are skipped (the final liquidation day reflects post-liquidation
    state; the forecast daily rows replace it).

    ``last_actual_date``: if provided (and ``exclude_final_liquidation_day``
    is None), daily rows AFTER this date are skipped (removes old forecast
    daily rows from the previous architecture that inserted them into the
    parent seq).

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


# ---------------------------------------------------------------------------
# Create one child seq for one scenario
# ---------------------------------------------------------------------------
async def create_scenario_child_seq(
    conn,
    parent_seq_id: int,
    scenario_name: str,
    scenario_rows: List[Dict[str, Any]],
    state: Dict[str, Any],
    parent_info: Dict[str, Any],
    *,
    algo=None,
    algo_params: Optional[Dict[str, Any]] = None,
    actual_ohlc: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[int, int, int]:
    """Create a child seq for one scenario with full actual + forecast data.

    Returns (child_seq_id, n_actual_copied, n_forecast_added).
    """
    from strategy._common.upsert import (
        insert_decisions, insert_daily_rows, insert_strategy_results,
    )

    sec_type = state["sec_type"]
    code = state["code"]
    strategy_name = parent_info["strategy_name"]
    seq_no = parent_info["seq_no"]

    # 1. Create the child seq row.
    # Child seq's "run over" period: same start as parent, end extended to
    # the last forecast sell date. start_date/end_date are part of the
    # natural business key (with scenario) so child seqs coexist with the
    # parent and with each other (one per scenario).
    forecast_date = state["end_date"]
    future_dates = future_trading_dates(forecast_date, HORIZON_DAYS)
    child_end_date = future_dates[-1] if future_dates else forecast_date

    child_seq_id = await conn.fetchval(
        "INSERT INTO strategy.strategy_identity "
        "(strategy_name, seq_no, sec_type, code, start_date, end_date, "
        " params, status, parent_seq_id, scenario) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, 'completed', $8, $9) "
        "RETURNING seq_id",
        strategy_name, seq_no, sec_type, code,
        parent_info["start_date"], child_end_date,
        parent_info.get("params", {}), parent_seq_id, scenario_name,
    )

    # 2. Copy parent's actual decisions into the child seq.
    # Exclude the FINAL LIQUIDATION SELL — the forecast sells REPLACE it.
    n_actual = await copy_actual_decisions(
        conn, parent_seq_id, child_seq_id,
        exclude_final_liquidation=state.get("is_final_liquidation", True),
    )

    # 3. Build + insert forecast decisions for this scenario.
    start_decision_no = n_actual + 1
    fc_decisions = build_scenario_forecast_decisions(
        scenario_rows, state, future_dates, scenario_name, start_decision_no,
        algo=algo, algo_params=algo_params, actual_ohlc=actual_ohlc,
    )
    if fc_decisions:
        # assign_no=False: forecast decision_no continues from n_actual + 1
        # (set in build_scenario_forecast_decisions). assign_decision_no would
        # re-number from 1 and collide with the actual decisions copied above.
        await insert_decisions(conn, child_seq_id, fc_decisions, assign_no=False)

    # 4. Copy parent's actual strategy_daily into the child seq.
    # Exclude the FINAL LIQUIDATION day's daily row (post-liquidation state;
    # the forecast daily rows replace it). When there's no final liquidation,
    # still exclude any old forecast daily rows (trade_date > last actual).
    if state.get("is_final_liquidation"):
        n_actual_daily = await copy_actual_daily(
            conn, parent_seq_id, child_seq_id,
            exclude_final_liquidation_day=forecast_date,
        )
    else:
        n_actual_daily = await copy_actual_daily(
            conn, parent_seq_id, child_seq_id,
            last_actual_date=forecast_date,
        )

    # 5. Build + insert forecast daily rows.
    fc_daily = build_scenario_forecast_daily(
        scenario_rows, fc_decisions, state, future_dates,
        state["first_buy_date"], state["first_buy_fill_price"],
    )
    if fc_daily:
        await insert_daily_rows(conn, child_seq_id, fc_daily)

    # 6. Insert strategy_results for the child seq.
    # Fetch ALL decisions (actual + forecast) to compute totals.
    all_sells = await conn.fetch(
        "SELECT realized_pnl FROM strategy.trade_decision "
        "WHERE seq_id = $1 AND side = 'SELL'",
        child_seq_id,
    )
    n_buys = await conn.fetchval(
        "SELECT count(*) FROM strategy.trade_decision "
        "WHERE seq_id = $1 AND side = 'BUY'",
        child_seq_id,
    )
    total_realized = sum(float(r["realized_pnl"] or 0) for r in all_sells)
    total_abs = sum(abs(float(r["realized_pnl"] or 0)) for r in all_sells)
    new_end_date = await conn.fetchval(
        "SELECT max(exec_date) FROM strategy.trade_decision WHERE seq_id = $1",
        child_seq_id,
    )

    await insert_strategy_results(
        conn, child_seq_id, sec_type, code,
        start_date=state["start_date"],
        end_date=new_end_date,
        total_buy_cost=state.get("total_buy_cost"),
        first_buy_date=state["first_buy_date"],
        first_buy_fill_price=state["first_buy_fill_price"],
        total_realized_pnl=round(total_realized, 4),
        total_abs_pnl=round(total_abs, 4),
        n_sells=len(all_sells),
        n_buys=n_buys,
    )

    return child_seq_id, n_actual, len(fc_decisions)


# ---------------------------------------------------------------------------
# Orchestrate: create all 8 child seqs
# ---------------------------------------------------------------------------
async def insert_forecast_child_seqs(
    conn,
    parent_seq_id: int,
    all_rows: List[Dict[str, Any]],
    state: Dict[str, Any],
    *,
    algo=None,
    algo_params: Optional[Dict[str, Any]] = None,
    actual_ohlc: Optional[List[Dict[str, Any]]] = None,
) -> List[Tuple[str, int]]:
    """Create 8 child seqs (one per DISPLAY_SCENARIO).

    ``all_rows`` is the list of forecast_1m row dicts for ALL scenarios.
    This function filters by scenario name and creates one child seq per
    scenario. ``algo`` / ``algo_params`` / ``actual_ohlc`` are threaded to
    ``build_scenario_forecast_decisions`` so forecast sells are algo-driven.

    Returns [(scenario_name, child_seq_id), ...].
    """
    # Fetch parent info for the child seq rows. start_date/end_date come
    # from strategy_identity (the OHLC input period) — NOT strategy_results
    # (which carries the OUTPUT min/max exec_date). The child's identity
    # start_date mirrors the parent's OHLC start; end_date is extended to
    # the last forecast sell date (set in create_scenario_child_seq).
    parent_info = await conn.fetchrow(
        "SELECT strategy_name, seq_no, sec_type, code, params, "
        "       start_date, end_date "
        "FROM strategy.strategy_identity WHERE seq_id = $1",
        parent_seq_id,
    )
    if parent_info is None:
        return []
    parent_info_dict = {
        "strategy_name": parent_info["strategy_name"],
        "seq_no": parent_info["seq_no"],
        "params": parent_info["params"],
        "start_date": parent_info["start_date"],
        "end_date": parent_info["end_date"],
    }

    # Group rows by scenario.
    rows_by_scenario: Dict[str, List[Dict[str, Any]]] = {}
    for r in all_rows:
        sc = r["scenario"]
        if sc not in rows_by_scenario:
            rows_by_scenario[sc] = []
        rows_by_scenario[sc].append(r)

    created: List[Tuple[str, int]] = []
    for scenario_name in DISPLAY_SCENARIOS:
        scenario_rows = rows_by_scenario.get(scenario_name, [])
        if not scenario_rows:
            continue
        # Sort by forecast_day.
        scenario_rows.sort(key=lambda r: r["forecast_day"])

        child_seq_id, n_actual, n_fc = await create_scenario_child_seq(
            conn, parent_seq_id, scenario_name, scenario_rows, state, parent_info_dict,
            algo=algo, algo_params=algo_params, actual_ohlc=actual_ohlc,
        )
        created.append((scenario_name, child_seq_id))
        print(f"       [{scenario_name}] child seq={child_seq_id}: "
              f"{n_actual} actual + {n_fc} forecast decisions",
              flush=True)

    return created
