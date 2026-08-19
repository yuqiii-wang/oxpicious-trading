"""Strategy-agnostic DB fetch helpers.

Two concerns, both reusable across strategies:

  1. discover_available_codes() — list all codes in analysis.mov_ave_spreads_detail
     for a sec_type. Used by the ``--all`` flag so any strategy can backtest
     every available security without manual --codes entry.

  2. fetch_strategy_seqs() / fetch_decisions() — read strategy_seq rows for
     a sec_type (optionally filtered by code list) and the trade_decision
     rows for a given seq_id. Since strategy_seq is per-code (one row per
     strategy execution on ONE code), fetch_decisions no longer needs a
     code parameter — the seq_id alone identifies the (strategy, code) run.
     Used by the risk pipeline (strategy._risks) and by any future
     analytics that need to inspect a strategy's executed trades.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import datetime

from strategy._common.constants import SEC_TYPE_BASIC_STATS_TABLE


# ---------------------------------------------------------------------------
# Discovery — list all codes present in analysis.mov_ave_spreads_detail for
# a sec_type. Used by --all so the strategy backtests every available sec.
# ---------------------------------------------------------------------------
_DISCOVER_SQL = """
    SELECT DISTINCT code
    FROM analysis.mov_ave_spreads_detail
    WHERE sec_type = $1
    ORDER BY code ASC
"""


async def discover_available_codes(conn, sec_type: str) -> List[str]:
    """Return all distinct codes available in the analysis detail table for
    the given sec_type, sorted ascending.

    Used by the ``--all`` flag so any strategy can backtest every security
    that has analysis data without manual --codes entry.
    """
    rows = await conn.fetch(_DISCOVER_SQL, sec_type)
    return [r["code"] for r in rows]


# ---------------------------------------------------------------------------
# strategy_seq reads — list (seq_id, code) pairs for the risk pipeline
# ---------------------------------------------------------------------------
async def fetch_strategy_seqs(
    conn,
    sec_type: str,
    codes: list = None,
    strategy_name: str = None,
) -> List[Tuple[int, str]]:
    """Return [(seq_id, code), ...] for the given sec_type, optionally
    filtered by a code list and/or a strategy_name.

    strategy_identity is per-code (one row per (strategy, code) run), so this
    reads directly from strategy_identity — no JOIN to trade_decision needed.

    If ``codes`` is empty/None, returns ALL (seq_id, code) pairs for the
    sec_type (subject to the strategy_name filter). Used by the risk pipeline
    to know which seqs to compute risk metrics for. ``strategy_name`` scopes
    the result to one algo (e.g. 'macd') so a run for one
    algo doesn't recompute risks for another algo's seqs.
    """
    name_clause = " AND strategy_name = $3" if strategy_name else ""
    if codes:
        if strategy_name:
            rows = await conn.fetch(
                f"SELECT seq_id, code "
                f"FROM strategy.strategy_identity "
                f"WHERE sec_type = $1 AND code = ANY($2::text[]){name_clause} "
                f"ORDER BY seq_id, code",
                sec_type, sorted(codes), strategy_name,
            )
        else:
            rows = await conn.fetch(
                "SELECT seq_id, code "
                "FROM strategy.strategy_identity "
                "WHERE sec_type = $1 AND code = ANY($2::text[]) "
                "ORDER BY seq_id, code",
                sec_type, sorted(codes),
            )
    else:
        if strategy_name:
            rows = await conn.fetch(
                f"SELECT seq_id, code "
                f"FROM strategy.strategy_identity "
                f"WHERE sec_type = $1{name_clause} "
                f"ORDER BY seq_id, code",
                sec_type, strategy_name,
            )
        else:
            rows = await conn.fetch(
                "SELECT seq_id, code "
                "FROM strategy.strategy_identity "
                "WHERE sec_type = $1 "
                "ORDER BY seq_id, code",
                sec_type,
            )
    return [(r["seq_id"], r["code"]) for r in rows]


# ---------------------------------------------------------------------------
# trade_decision reads — used by the risk pipeline and future analytics
# ---------------------------------------------------------------------------
async def fetch_decisions(
    conn,
    seq_id: int,
    columns: str = "decision_no, side, exec_date, qty, fill_price, "
                   "realized_pnl, signal_reason",
) -> List[Dict[str, Any]]:
    """Fetch trade_decision rows for a seq_id, ordered chronologically.

    No ``code`` parameter is needed — strategy_seq is per-code, so the
    seq_id alone identifies the (strategy, code) run. (The code itself
    lives on strategy_seq if the caller needs it.)

    The default column list covers what the risk pipeline needs. Callers
    needing more columns can pass a custom ``columns`` string.
    """
    rows = await conn.fetch(
        f"SELECT {columns} "
        "FROM strategy.trade_decision "
        "WHERE seq_id = $1 "
        "ORDER BY decision_no",
        seq_id,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Daily close-price reads — used by the risk pipeline to compute price-based
# drawdowns (deepest drop since unzero position / since last buy). Reads from
# the same per-sec_type basic_stats table the backtest used for fill prices so
# the drawdown reference matches the strategy's execution price source.
# ---------------------------------------------------------------------------
async def fetch_close_prices(
    conn,
    sec_type: str,
    code: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> List[Dict[str, Any]]:
    """Fetch daily close prices for one code over [start_date, end_date].

    Returns rows as dicts ``{"date": date, "close_price": float}`` sorted
    ascending by date. Reads from ``stats.<sec_type>_basic_stats`` (the same
    source the backtest uses for open/close fill prices).
    """
    table = SEC_TYPE_BASIC_STATS_TABLE[sec_type]
    rows = await conn.fetch(
        f"SELECT date, close AS close_price "
        f"FROM {table} "
        "WHERE code = $1 AND date >= $2 AND date <= $3 "
        "ORDER BY date ASC",
        code, start_date, end_date,
    )
    return [{"date": r["date"], "close_price": float(r["close_price"])}
            for r in rows]


# ---------------------------------------------------------------------------
# strategy_daily reads — used by the risk pipeline to compute the per-period
# mark-to-market change in unrealized_pnl (unrealized_pnl(end of period) -
# unrealized_pnl(end of previous period)). strategy_daily is written by the
# backtest runner BEFORE risks are computed, so it is available here.
# ---------------------------------------------------------------------------
async def fetch_daily_unrealized(
    conn,
    seq_id: int,
) -> List[Dict[str, Any]]:
    """Fetch the daily unrealized_pnl series for a seq_id, sorted by date.

    Returns rows as dicts ``{"trade_date": date, "unrealized_pnl": float}``.
    Used by the risk pipeline's per-period MTM-change computation.
    """
    rows = await conn.fetch(
        "SELECT trade_date, unrealized_pnl "
        "FROM strategy.strategy_daily "
        "WHERE seq_id = $1 "
        "ORDER BY trade_date ASC",
        seq_id,
    )
    return [{"trade_date": r["trade_date"],
             "unrealized_pnl": float(r["unrealized_pnl"])}
            for r in rows]


# ---------------------------------------------------------------------------
# strategy_results reads — total_buy_cost (peak capital deployed) is the
# stable denominator for the risk score's loss_fraction. Reads from the 1:1
# strategy_results row written by the backtest runner.
# ---------------------------------------------------------------------------
async def fetch_total_buy_cost(conn, seq_id: int) -> float:
    """Return total_buy_cost (peak normalized capital deployed) for a seq_id.

    Used by the risk pipeline as the denominator for loss_fraction
    (|window loss| / total_buy_cost) — the standard "% of capital" basis.
    Returns 0.0 when the row is missing.
    """
    row = await conn.fetchrow(
        "SELECT total_buy_cost FROM strategy.strategy_results "
        "WHERE seq_id = $1",
        seq_id,
    )
    if row is None or row["total_buy_cost"] is None:
        return 0.0
    return float(row["total_buy_cost"])
