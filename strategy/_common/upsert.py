"""DB upsert for the strategy schema (shared across all strategies).

Writes three targets:
  1. strategy.strategy_identity — one IDENTITY row per (strategy, code) run.
     PURE IDENTITY table (run results live on strategy_results). Resolves the
     next seq_no for the strategy_name unless --seq-no is given. With --force,
     an existing (strategy_name, seq_no, sec_type, code) is deleted (CASCADE
     removes its strategy_results + decisions + risk rows) before re-inserting.
  2. strategy.strategy_results  — 1:1 with strategy_identity (seq_id is PK + FK).
     Holds run RESULTS: start/end_date, total_buy_cost, the first-buy
     normalization anchor (first_buy_date / first_buy_fill_price), and the
     P&L summary (total_realized_pnl / total_abs_pnl / n_sells / n_buys).
  3. strategy.trade_decision — ordered decisions within the seq. decision_no
     is assigned 1..N after sorting by (exec_date, side). Each
     row carries normalized_fill_price (base = 100 at the first BUY fill).

All functions are strategy-agnostic: they operate purely on the strategy_identity
/ strategy_results / trade_decision tables and don't know anything about MA
crosses, RSI, or any other strategy-specific signal logic. A future strategy
(mean-reversion, momentum, etc.) would call the same insert_strategy_seq() +
insert_strategy_results() + insert_decisions() triple with its own decisions list.
"""
from __future__ import annotations

import json
import datetime
from typing import Any, Dict, List, Optional

from strategy._common.db import bulk_upsert_async
from strategy._common.constants import SEQ_TABLE, INFO_TABLE, DECISION_TABLE, DAILY_TABLE


# Columns written to trade_decision (order matches the table DDL; excludes
# seq_id which is set via the FK). Shared across all strategies — the
# trade_decision schema is generic. NOTE: trade_decision no longer carries
# sec_type / code (those live on strategy_seq/strategy_results, which is
# per-code) nor commission (folded into fees). normalized_fill_price is
# attached by the backtest (base = 100 at the first BUY fill).
# normalized_mean_buy_price is the weighted-avg BUY norm price (cost basis):
# post-BUY value on BUY rows, pre-SELL value (the one realized_pnl uses) on
# SELL rows. total_qty_before/after track the cumulative quantity in
# confidence/qty units (NOT /100).
DECISION_COLUMNS = [
    "decision_no", "side", "qty",
    "exec_date",
    "fill_price", "normalized_fill_price", "normalized_mean_buy_price",
    "position_before", "position_after",
    "cash_before", "cash_after",
    "total_qty_before", "total_qty_after",
    "realized_pnl",
    "slippage", "fee",
    "signal_value", "signal_reason",
]


async def resolve_seq_no(
    conn,
    strategy_name: str,
    sec_type: str,
    code: str,
    force: bool,
    seq_no: Optional[int],
) -> int:
    """Determine the seq_no to use for this (strategy, sec_type, code) run.

    - If ``seq_no`` is given: with --force, delete the existing
      (strategy_name, seq_no, sec_type, code) row (CASCADE drops its
      strategy_results + decisions + risk rows); without --force, raise if it
      already exists.
    - If ``seq_no`` is None: use max(existing seq_no)+1 for the strategy_name
      (or 1 if none). Multiple codes in one --all run share the same seq_no;
      they get distinct seq_ids but the same seq_no.
    """
    if seq_no is not None:
        existing_id = await conn.fetchval(
            f"SELECT seq_id FROM {SEQ_TABLE} "
            "WHERE strategy_name=$1 AND seq_no=$2 "
            "  AND sec_type=$3 AND code=$4",
            strategy_name, seq_no, sec_type, code,
        )
        if existing_id is not None:
            if not force:
                raise RuntimeError(
                    f"strategy_seq({strategy_name},{seq_no},"
                    f"{sec_type},{code}) already exists "
                    f"(seq_id={existing_id}). Use --force to overwrite."
                )
            # CASCADE removes the seq's strategy_results + trade_decision +
            # strategy_risk_seq + strategy_risk_period rows.
            await conn.execute(
                f"DELETE FROM {SEQ_TABLE} WHERE seq_id=$1",
                existing_id,
            )
        return seq_no

    max_no = await conn.fetchval(
        f"SELECT COALESCE(MAX(seq_no), 0) FROM {SEQ_TABLE} "
        "WHERE strategy_name=$1",
        strategy_name,
    )
    return int(max_no) + 1


async def insert_strategy_seq(
    conn,
    strategy_name: str,
    seq_no: int,
    sec_type: str,
    code: str,
    params: dict,
    status: str = "completed",
) -> int:
    """Insert a strategy_seq row (one code per seq) and return its seq_id.

    strategy_seq is now a PURE IDENTITY table — run results (dates,
    total_buy_cost, first-buy anchor, P&L summary) are written separately to
    strategy_results via insert_strategy_results(). Call that right after this.
    """
    params_json = json.dumps(params, default=str)
    # Use RETURNING to get the IDENTITY-generated seq_id.
    seq_id = await conn.fetchval(
        f"""
        INSERT INTO {SEQ_TABLE}
            (strategy_name, seq_no, sec_type, code, params, status)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        RETURNING seq_id
        """,
        strategy_name, seq_no, sec_type, code,
        params_json, status,
    )
    return int(seq_id)


async def insert_strategy_results(
    conn,
    seq_id: int,
    sec_type: str,
    code: str,
    *,
    start_date: datetime.date,
    end_date: Optional[datetime.date],
    total_buy_cost: Optional[float],
    first_buy_date: Optional[datetime.date],
    first_buy_fill_price: Optional[float],
    total_realized_pnl: float,
    total_abs_pnl: float,
    n_sells: int,
    n_buys: int,
    currency: str = "CNY",
) -> int:
    """Insert the 1:1 strategy_results row (run RESULTS) for seq_id.

    All fields are derived from the backtest decisions list by the runner:
      - start_date / end_date = min / max(decisions.exec_date)
      - total_buy_cost = sum(gross_value + fees) over BUYs
      - first_buy_date / first_buy_fill_price = the first BUY (normalization
        anchor; trade_decision.normalized_fill_price = fill_price / this * 100)
      - total_realized_pnl / total_abs_pnl / n_sells / n_buys = SELL-side
        P&L summary (moved here from strategy_risk_seq)

    ON CONFLICT (seq_id) DO UPDATE so re-running with --force on the same
    seq_id (after the CASCADE delete re-inserts strategy_seq with a new
    IDENTITY value) just upserts cleanly.
    """
    await conn.execute(
        f"""
        INSERT INTO {INFO_TABLE}
            (seq_id, sec_type, code,
             start_date, end_date, total_buy_cost, currency,
             first_buy_date, first_buy_fill_price,
             total_realized_pnl, total_abs_pnl, n_sells, n_buys)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (seq_id) DO UPDATE SET
             sec_type             = EXCLUDED.sec_type,
             code                 = EXCLUDED.code,
             start_date           = EXCLUDED.start_date,
             end_date             = EXCLUDED.end_date,
             total_buy_cost       = EXCLUDED.total_buy_cost,
             currency             = EXCLUDED.currency,
             first_buy_date       = EXCLUDED.first_buy_date,
             first_buy_fill_price = EXCLUDED.first_buy_fill_price,
             total_realized_pnl   = EXCLUDED.total_realized_pnl,
             total_abs_pnl        = EXCLUDED.total_abs_pnl,
             n_sells              = EXCLUDED.n_sells,
             n_buys               = EXCLUDED.n_buys
        """,
        seq_id, sec_type, code,
        start_date, end_date, total_buy_cost, currency,
        first_buy_date, first_buy_fill_price,
        total_realized_pnl, total_abs_pnl, n_sells, n_buys,
    )
    return 1


def assign_decision_no(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort decisions by (exec_date, side) and assign 1..N.

    BUY before SELL on the same exec_date (a SELL can't fill before the BUY
    that opened it on the same day in this single-position model, but the
    sort keeps the order deterministic).
    """
    ordered = sorted(
        decisions,
        key=lambda d: (d["exec_date"], 0 if d["side"] == "BUY" else 1),
    )
    for i, d in enumerate(ordered, start=1):
        d["decision_no"] = i
    return ordered


async def insert_decisions(
    conn,
    seq_id: int,
    decisions: List[Dict[str, Any]],
) -> int:
    """Bulk-insert trade_decision rows for the given seq_id.

    ``decisions`` is a list of dicts whose keys include the columns in
    DECISION_COLUMNS. Missing keys default to None. ``decision_no`` is
    assigned here via assign_decision_no() after sorting. Each row must
    carry ``normalized_fill_price`` (attached by the backtest).
    """
    if not decisions:
        return 0
    decisions = assign_decision_no(decisions)
    rows = []
    for d in decisions:
        r = {"seq_id": seq_id}
        for c in DECISION_COLUMNS:
            r[c] = d.get(c)
        rows.append(r)
    inserted = await bulk_upsert_async(
        conn, DECISION_TABLE, rows,
        ["seq_id", "decision_no"],
    )
    return inserted


# Columns written to strategy_daily (excludes seq_id which is set via the FK).
# One row per (seq_id, trade_date) from the first BUY date to the end of the
# backtest. unrealized_pnl = (total_qty/100) * (normalized_close - cost_basis_norm)
# — as if all remaining position were sold at the day's close.
DAILY_COLUMNS = [
    "trade_date",
    "close_price", "normalized_close",
    "total_qty", "cost_basis_norm", "position_value", "cash",
    "realized_pnl_cum", "unrealized_pnl", "total_pnl",
    "return_rate",
    "sharpe_ratio", "sharpe_ratio_255d", "sharpe_ratio_500d",
    "normalized_mean_buy_period",
    "is_decision_day", "decision_no",
]


async def insert_daily_rows(
    conn,
    seq_id: int,
    daily_rows: List[Dict[str, Any]],
) -> int:
    """Bulk-insert strategy_daily rows for the given seq_id.

    ``daily_rows`` is a list of dicts whose keys include the columns in
    DAILY_COLUMNS. Missing keys default to None. The caller (runner) computes
    these via ``backtest.compute_daily_rows`` AFTER decisions are numbered
    (so ``decision_no`` can be linked).
    """
    if not daily_rows:
        return 0
    rows = []
    for d in daily_rows:
        r = {"seq_id": seq_id}
        for c in DAILY_COLUMNS:
            r[c] = d.get(c)
        rows.append(r)
    inserted = await bulk_upsert_async(
        conn, DAILY_TABLE, rows,
        ["seq_id", "trade_date"],
    )
    return inserted
