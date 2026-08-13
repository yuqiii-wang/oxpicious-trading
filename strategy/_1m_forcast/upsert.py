"""DB write for the 1-month forecast tables.

Bulk-upserts:
  - strategy.forecast_1m        — 8 scenarios × 20 days = 160 rows per run
  - strategy.forecast_1m_stats  — 1:1 per run (the 20d historical stats)

With ``force=True``, existing rows for the (seq_id, forecast_date) are
deleted first so re-runs are idempotent.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from strategy._common.db import bulk_upsert_async
from strategy._1m_forcast.constants import FORECAST_TABLE

STATS_TABLE = "strategy.forecast_1m_stats"

# Columns written to strategy.forecast_1m (excludes seq_id which is added
# per-row below; excludes code/forecast_date which the caller passes).
FORECAST_COLUMNS = [
    "scenario", "forecast_day",
    "open_price", "high_price", "low_price", "close_price", "daily_return",
    "trading_amt", "rsi",
    "sell_fraction", "sell_confidence", "realized_pnl_forecast",
    "scenario_weight", "total_qty", "cost_basis_norm",
]

# Columns written to strategy.forecast_1m_stats (excludes seq_id/forecast_date).
STATS_COLUMNS = [
    "sigma_daily",
    "sigma_255d",
    "sigma_255d_max",
    "oc_gap_mean", "oc_gap_std",
    "hl_gap_mean", "hl_gap_std",
    "amt_mean", "amt_std", "amt_hl_corr",
    "rsi_6", "rsi_10", "rsi_14", "rsi_20",
    "anchor_close", "first_buy_fill_price",
    "last_total_pnl",
]


async def upsert_forecast(
    conn,
    seq_id: int,
    code: str,
    forecast_date,
    rows: List[Dict[str, Any]],
    stats_row: Dict[str, Any],
    force: bool = False,
) -> int:
    """Bulk-upsert forecast rows + the 1:1 stats row for (seq_id, forecast_date).

    ``rows`` is the output of ``compute.compute_forecast`` (160 rows).
    ``stats_row`` is the output of ``compute.compute_history_stats`` + the
    caller-attached anchor_close / first_buy_fill_price / RSI values.
    Returns the number of forecast rows upserted (stats row not counted).
    """
    if not rows:
        return 0

    if force:
        await conn.execute(
            f"DELETE FROM {FORECAST_TABLE} "
            "WHERE seq_id = $1 AND forecast_date = $2",
            seq_id, forecast_date,
        )
        await conn.execute(
            f"DELETE FROM {STATS_TABLE} "
            "WHERE seq_id = $1 AND forecast_date = $2",
            seq_id, forecast_date,
        )

    # 1. Main forecast rows.
    out: List[Dict[str, Any]] = []
    for d in rows:
        r: Dict[str, Any] = {
            "seq_id": seq_id,
            "code": code,
            "forecast_date": forecast_date,
        }
        for c in FORECAST_COLUMNS:
            r[c] = d.get(c)
        out.append(r)
    n = await bulk_upsert_async(
        conn, FORECAST_TABLE, out,
        ["seq_id", "forecast_date", "scenario", "forecast_day"],
    )

    # 2. Stats row (1:1).
    s: Dict[str, Any] = {
        "seq_id": seq_id,
        "forecast_date": forecast_date,
    }
    for c in STATS_COLUMNS:
        s[c] = stats_row.get(c)
    await bulk_upsert_async(
        conn, STATS_TABLE, [s],
        ["seq_id", "forecast_date"],
    )

    return n
