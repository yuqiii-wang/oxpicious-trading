"""Create forecast child seqs — one per scenario.

This module re-exports all functions from the ``_decisions`` subpackage
for backward compatibility. The implementation lives in:

- ``_decisions.date_utils``: future_trading_dates, compute_required_columns
- ``_decisions.decisions_builder``: build_scenario_forecast_decisions
- ``_decisions.daily_builder``: build_scenario_forecast_daily
- ``_decisions.state_utils``: fetch_last_actual_state, delete_existing_child_seqs
- ``_decisions.copy_utils``: copy_actual_decisions, copy_actual_daily
- ``_decisions.child_seq``: create_scenario_child_seq, insert_forecast_child_seqs
"""
from strategy._1m_forcast._decisions import (
    future_trading_dates,
    compute_required_columns as _compute_required_columns,
    build_scenario_forecast_decisions,
    build_scenario_forecast_daily,
    fetch_last_actual_state,
    delete_existing_child_seqs,
    copy_actual_decisions,
    copy_actual_daily,
    create_scenario_child_seq,
    insert_forecast_child_seqs,
)

__all__ = [
    "future_trading_dates",
    "build_scenario_forecast_decisions",
    "build_scenario_forecast_daily",
    "fetch_last_actual_state",
    "delete_existing_child_seqs",
    "copy_actual_decisions",
    "copy_actual_daily",
    "create_scenario_child_seq",
    "insert_forecast_child_seqs",
]
