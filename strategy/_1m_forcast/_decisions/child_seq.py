"""Create one child seq per forecast scenario and orchestrate all child seqs.

- create_scenario_child_seq: create a child seq for one scenario with full
  actual + forecast data (decisions, daily rows, results)
- insert_forecast_child_seqs: orchestrate creating child seqs for all
  DISPLAY_SCENARIOS
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional, Tuple

from strategy._1m_forcast.constants import HORIZON_DAYS, DISPLAY_SCENARIOS
from .date_utils import future_trading_dates
from .decisions_builder import build_scenario_forecast_decisions
from .daily_builder import build_scenario_forecast_daily
from .copy_utils import copy_actual_decisions, copy_actual_daily


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
        upsert_strategy_seq,
    )

    sec_type = state["sec_type"]
    code = state["code"]
    strategy_name = parent_info["strategy_name"]
    seq_no = parent_info["seq_no"]

    forecast_date = state["end_date"]
    future_dates = future_trading_dates(forecast_date, HORIZON_DAYS)
    child_end_date = future_dates[-1] if future_dates else forecast_date

    raw_params = parent_info["params"]
    if isinstance(raw_params, str):
        raw_params = json.loads(raw_params)
    elif raw_params is None:
        raw_params = {}

    child_seq_id = await upsert_strategy_seq(
        conn,
        strategy_name=strategy_name,
        sec_type=sec_type,
        code=code,
        start_date=parent_info["start_date"],
        end_date=child_end_date,
        params=raw_params,
        scenario=scenario_name,
        parent_seq_id=parent_seq_id,
        force=True,
        seq_no=seq_no,
        is_active=parent_info.get("is_active", True),
    )
    if child_seq_id is None:
        raise RuntimeError(
            f"upsert_strategy_seq returned None for scenario {scenario_name} "
            f"(should not happen with force=True)"
        )

    # Copy parent's actual decisions into the child seq (exclude FINAL LIQUIDATION)
    n_actual = await copy_actual_decisions(
        conn, parent_seq_id, child_seq_id,
        exclude_final_liquidation=state.get("is_final_liquidation", True),
    )

    # Build + insert forecast decisions for this scenario
    start_decision_no = n_actual + 1
    fc_decisions = build_scenario_forecast_decisions(
        scenario_rows, state, future_dates, scenario_name, start_decision_no,
        algo=algo, algo_params=algo_params, actual_ohlc=actual_ohlc,
    )
    if fc_decisions:
        await insert_decisions(conn, child_seq_id, fc_decisions, assign_no=False)

    # Copy parent's actual strategy_daily (exclude FINAL LIQUIDATION day)
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

    # Build + insert forecast daily rows
    fc_daily = build_scenario_forecast_daily(
        scenario_rows, fc_decisions, state, future_dates,
        state["first_buy_date"], state["first_buy_fill_price"],
    )
    if fc_daily:
        await insert_daily_rows(conn, child_seq_id, fc_daily)

    # Insert strategy_results for the child seq
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

    await insert_strategy_results(
        conn, child_seq_id, sec_type, code,
        # Dates mirror the child identity row (data period incl. forecast
        # horizon), NOT the last decision's exec_date — forecast sells may
        # stop before day 20, but the run extends to child_end_date.
        start_date=parent_info["start_date"],
        end_date=child_end_date,
        total_buy_cost=state.get("total_buy_cost"),
        first_buy_date=state["first_buy_date"],
        first_buy_fill_price=state["first_buy_fill_price"],
        total_realized_pnl=round(total_realized, 4),
        total_abs_pnl=round(total_abs, 4),
        n_sells=len(all_sells),
        n_buys=n_buys,
    )

    return child_seq_id, n_actual, len(fc_decisions)


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
    """Create child seqs (one per DISPLAY_SCENARIO).

    ``all_rows`` is the list of forecast_1m row dicts for ALL scenarios.
    This function filters by scenario name and creates one child seq per
    scenario. ``algo`` / ``algo_params`` / ``actual_ohlc`` are threaded to
    ``build_scenario_forecast_decisions`` so forecast sells are algo-driven.

    Returns [(scenario_name, child_seq_id), ...].
    """
    parent_info = await conn.fetchrow(
        "SELECT strategy_name, seq_no, sec_type, code, params, "
        "       start_date, end_date, is_active "
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
        "is_active": parent_info["is_active"],
    }

    # Group rows by scenario
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
