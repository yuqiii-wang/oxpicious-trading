"""DB upsert for the risk-specific tables.

Both ``strategy.strategy_risks`` and ``strategy.strategy_risk_period``
are written only by this package — they are not shared with the backtest
pipeline (which writes ``strategy_identity`` + ``trade_decision`` via
``strategy._common.upsert``). The functions here are thin wrappers over
``bulk_upsert_async`` that pin the correct conflict-key columns.
"""
from __future__ import annotations

from typing import Any, Dict, List

from strategy._common.db import bulk_upsert_async
from strategy._risks.constants import (
    RISK_SEQ_TABLE, RISK_PERIOD_TABLE, RISK_FACTORS_TABLE,
)


async def upsert_risk_seq(conn, rows: List[Dict[str, Any]]) -> int:
    """Upsert strategy_risks rows (key: seq_id + code)."""
    if not rows:
        return 0
    return await bulk_upsert_async(
        conn, RISK_SEQ_TABLE, rows, ["seq_id", "code"],
    )


async def upsert_risk_periods(conn, rows: List[Dict[str, Any]]) -> int:
    """Upsert strategy_risk_period rows (key: seq_id + code + period_type + period_value)."""
    if not rows:
        return 0
    return await bulk_upsert_async(
        conn, RISK_PERIOD_TABLE, rows,
        ["seq_id", "code", "period_type", "period_value"],
    )


async def upsert_risk_factors(conn, rows: List[Dict[str, Any]]) -> int:
    """Upsert strategy_risk_factors rows (key: seq_id + code + component + sub_key)."""
    if not rows:
        return 0
    return await bulk_upsert_async(
        conn, RISK_FACTORS_TABLE, rows,
        ["seq_id", "code", "component", "sub_key"],
    )
